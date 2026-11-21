# SPDX-License-Identifier: Apache-2.0
"""S(c, L): ordered, training-only proposals. Admission is a separate process.

Retain one Synthesizer across retries of a concept. Its budget includes the
concept's prior proposal (one call), leaving three synthesis sends, errors and
inner retries included. No provider/configuration is discovered implicitly.
"""
from __future__ import annotations

import ast
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from threading import Lock, RLock
from typing import NamedTuple

from g6.core.ledger import canonical, digest
from g6.core.llm import EmptyRetry, FakeLLM, LLMCaller, LLMError
from g6.core.model_calls import counting
from g6.reasoning.budget import Budget, BudgetExhausted, MeteredCaller

from .detectors import SEED_TEMPLATES
from .execution import ExecutionRefused, Observation, run_candidate
from .facts import extract_facts, source_mapping
from .ledger import Stores
from .library import Library
from .records import (Composition, Concept, FailureRecord, LibraryEntry, MethodContract, Model,
                      SynthesisedBy, TemplateInstance, VerifierCandidate)


@dataclass(frozen=True)
class SynthesisCase:
    failure: FailureRecord
    before: Mapping[str, str]
    after: Mapping[str, str]
    expected: str = "concern"
    language: str = "python"
    framework: str = "pytest"


class Refusal(Model):
    code: str
    reason: str


class SynthesisResult(Model):
    candidates: list[VerifierCandidate]
    model_calls: int
    synthesis_calls: int
    refusals: list[Refusal]


class _Limit(BudgetExhausted):
    pass


def _offline(caller) -> bool:
    return isinstance(caller, FakeLLM) or isinstance(caller, EmptyRetry) and _offline(caller._inner)


class TrainingView(NamedTuple):
    concept: Concept
    observations: list
    unique: set
    duplicates: list  # record ids dropped because another training failure already holds their case


def _training_view(concept, cases):
    # Check every header/reference BEFORE touching any case's source content.
    concept = Concept.model_validate_json(canonical(concept.model_dump(mode="json")))
    failures = []
    for case in cases:
        if case.failure.split != "training" or case.failure.observed_by != "independent":
            raise ValueError("only independent training failures are visible to S")
        failure = FailureRecord.model_validate_json(canonical(case.failure.model_dump(mode="json")))
        if failure.family != concept.family or case.expected not in {"concern", "no_concern"}:
            raise ValueError("case family or expected verdict does not match the concept")
        failures.append(failure)
    # Every header is checked first. Then one observation per case: an already-present duplicate record of a
    # case is dropped and reported, keeping the concept's own record first.
    chosen, duplicates = {}, []
    for case in sorted(cases, key=lambda c: c.failure.record_id not in concept.from_failures):
        if case.failure.case_ref in chosen:
            duplicates.append(case.failure.record_id)
        else:
            chosen[case.failure.case_ref] = case
    supplied = {case.failure.record_id for case in cases}
    cases = [case for case in cases if chosen.get(case.failure.case_ref) is case]
    failures = [case.failure for case in cases]
    if (not failures or not {f.record_id for f in failures} <= set(concept.from_failures)
            or not set(concept.from_failures) <= supplied
            or not any(c.expected == "concern" for c in cases)):
        raise ValueError("exactly the concept's nonempty training failures are required")
    observations, literals = [], Counter()
    for case in cases:
        before, after = source_mapping(case.before), source_mapping(case.after)
        unique = set(before) | set(after)
        for source in (*before.values(), *after.values()):
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    unique.add(node.name)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 8:
                    unique.add(node.value)
        literals.update(unique)
        observations.append(Observation(before, after, extract_facts(before, after, split="training"),
                                        case.expected, case.language, case.framework))
    return TrainingView(concept, observations, {value for value, count in literals.items() if count == 1}, duplicates)


def _scrub(value, literals):
    if isinstance(value, str):
        for literal in sorted(literals, key=lambda v: (-len(v), v)):
            value = value.replace(literal, "<training-detail>")
        return value
    if isinstance(value, dict):
        # Preserve separate records even when names redact to the same string.
        return {_scrub(key, literals) + (f":{i}" if _scrub(key, literals) != key else ""):
                _scrub(child, literals) for i, (key, child) in enumerate(value.items())}
    if isinstance(value, list):
        return [_scrub(child, literals) for child in value]
    return value


class Synthesizer:
    """Read-only S session; emits records and diagnostics, writes no store.

    Non-FakeLLM callers require the host's weekly pacing meter, provider and
    positive worst_case account amount per send. Reservations are charged in
    full (no unmeasured savings). The injected caller retains its own send gate.
    """
    def __init__(self, *, caller: LLMCaller | None = None, pacing=None, provider: str = "",
                 worst_case: float | None = None, now=None, policy=None):
        from .meta.records import SynthesisPolicy
        self.policy = SynthesisPolicy() if policy is None else SynthesisPolicy.model_validate_json(policy.model_dump_json())
        self.caller, self.pacing, self.provider = caller, pacing, provider
        self.worst_case = worst_case
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._meters: dict[str, MeteredCaller] = {}
        self._concept_attempts: set[str] = set()
        self._lock = RLock()

    def synthesise(self, concept: Concept, cases: Sequence[SynthesisCase], *, library=None) -> SynthesisResult:
        with self._lock:
            return self._synthesise(concept, tuple(cases), Library() if library is None else library)

    def _synthesise(self, concept, cases, library):
        refusals = []
        meter = self._meters.get(concept.concept_id)

        def refuse(code, reason):
            refusals.append(Refusal(code=code, reason=reason))

        def result(candidate=None):
            calls = meter.calls if meter else 0
            return SynthesisResult(candidates=[candidate] if candidate else [], synthesis_calls=calls,
                                   model_calls=1 + calls, refusals=refusals)

        try:
            concept, observations, unique, duplicates = _training_view(concept, cases)
        except ValueError as error:
            refuse("G6_E_SYNTH_TRAINING_ONLY", str(error))
            return result()
        if duplicates:
            refuse("G6_E_SYNTH_DUPLICATE_CASE", f"{len(duplicates)} duplicate training record(s) of an already-held "
                                                "case dropped: " + ", ".join(duplicates))
            cases = tuple(case for case in cases if case.failure.record_id not in duplicates)
        if meter is None and self.caller is not None and self.policy.max_synthesis_calls:
            meter = MeteredCaller(self.caller, Budget(max_model_calls=self.policy.max_synthesis_calls))
            self._meters[concept.concept_id] = meter
        ids = [case.failure.case_ref for case in cases]
        entries = {entry.entry_id: entry for entry in sorted(library.list(), key=lambda e: e.entry_id)}

        def resolve(ident):
            entry = entries.get(ident)
            if entry is None or entry.status not in {"probation", "admitted", "demoted"}:
                raise ExecutionRefused("G6_E_SYNTH_DEPENDENCY", "parent is missing or inactive")
            entry = LibraryEntry.model_validate_json(canonical(entry.model_dump(mode="json")))
            method = (library.candidate(entry) if hasattr(library, "candidate") else
                      Stores().for_model(VerifierCandidate).get(entry.candidate_id))
            method = VerifierCandidate.model_validate_json(canonical(method.model_dump(mode="json")))
            if (method.candidate_id != entry.candidate_id or method.contract.content_id != entry.contract_sha256
                    or method.contract.family != entry.family):
                raise ExecutionRefused("G6_E_SYNTH_DEPENDENCY", "parent candidate/contract mismatch")
            return method

        default_contract = MethodContract(family=concept.family, languages=["python"], frameworks=["pytest"],
            applies_when=[], assumptions=["Static observations only; training coverage is not acceptance."],
            inputs=concept.observable_features, probes=[], verdict_kinds=["concern", "abstain", "no_concern"],
            cost_bound={"wall_s": 2.0, "probe_runs": 0, "model_calls": 0},
            escape_family=concept.falsifier, withdrawal_conditions=["Independent regression or refuted fire"])

        def candidate(form, body, *, parents=(), contract=None, exchange=None):
            return VerifierCandidate(concept_id=concept.concept_id, form=form, body=body,
                contract=contract or default_contract, lineage=list(parents),
                synthesised_by=SynthesisedBy(kind="model" if exchange else (
                    "composition" if form in {"reuse", "composition"} else "template"), exchange_sha256=exchange),
                training_case_ids=ids, authority="proposed", model_calls=1 + (meter.calls if meter else 0))

        last_observed = []

        def covers(method):
            nonlocal last_observed
            last_observed = []
            try:
                last_observed = run_candidate(method, observations, resolve)
                return last_observed == [obs.expected for obs in observations]
            except ExecutionRefused as error:
                refuse(error.code, str(error))
                return False

        # Reuse keeps scope intact. A new concept never inherits the parent's authority.
        usable = []
        for ident, entry in entries.items():
            if entry.family != concept.family or entry.status not in {"probation", "admitted", "demoted"}:
                continue
            try:
                method = resolve(ident)
            except ExecutionRefused as error:
                refuse(error.code, str(error))
                continue
            reusable = candidate("reuse", ident, parents=[ident], contract=method.contract)
            if covers(reusable):
                return result(reusable)
            usable.append((ident, method))

        # Bounded deterministic search; parents still enforce their individual scope.
        pool = usable[:8]
        groups = list(combinations(pool, 2))
        if len(pool) > 2:
            groups.append(tuple(pool))
        for group in groups:
            parents = [ident for ident, _ in group]
            contracts = [method.contract for _, method in group]
            contract = MethodContract(family=concept.family,
                languages=sorted({s for c in contracts for s in c.languages}),
                frameworks=sorted({s for c in contracts for s in c.frameworks}), applies_when=[],
                assumptions=sorted({s for c in contracts for s in c.assumptions}),
                inputs=sorted({s for c in contracts for s in c.inputs}), probes=[p for c in contracts for p in c.probes],
                verdict_kinds=["concern", "abstain", "no_concern"],
                cost_bound={"wall_s": sum(c.cost_bound.wall_s for c in contracts),
                            "probe_runs": sum(c.cost_bound.probe_runs for c in contracts), "model_calls": 0},
                escape_family=concept.falsifier, withdrawal_conditions=sorted(
                    {s for c in contracts for s in c.withdrawal_conditions}))
            for op, k in [("any", None), ("all", None), ("sequence", None),
                          *[("k_of_n", k) for k in range(2, len(group))]]:
                composed = candidate("composition", Composition(op=op, entries=parents, k=k),
                                     parents=parents, contract=contract)
                if covers(composed):
                    return result(composed)

        for name in self.policy.template_order:
            if SEED_TEMPLATES[name].supported:
                method = candidate("template_instance", TemplateInstance(template=name, bindings={}))
                if covers(method):
                    return result(method)

        if self.policy.max_synthesis_calls == 0:
            refuse("G6_E_SYNTH_CALL_LIMIT", "synthesis policy permits no model calls")
            return result()
        if self.caller is None:
            refuse("G6_E_SYNTH_NO_CALLER", "no injected model caller; no provider was discovered")
            return result()
        prompt = canonical(_scrub({"statement": concept.statement, "falsifier": concept.falsifier,
            "features": concept.observable_features, "cases": [
                {"facts": obs.facts, "expected": obs.expected} for obs in observations]}, unique))

        terminal = False

        def ask(system, feedback=None):
            nonlocal terminal
            if meter.check():
                refuse("G6_E_SYNTH_CALL_LIMIT", "concept budget exhausted; next call refused")
                return None
            online = not _offline(self.caller)
            if online and (self.pacing is None or not self.provider or self.worst_case is None
                           or not math.isfinite(self.worst_case) or self.worst_case <= 0):
                refuse("G6_E_SYNTH_PACING", "weekly meter, provider and per-send worst case required")
                terminal = True
                return None
            # Reserve enough for every possible inner retry before entering the caller.
            reservation = None
            if online:
                reservation = self.pacing.reserve(self.provider, self.now(),
                    self.worst_case * (self.policy.max_synthesis_calls - meter.calls))
                if not reservation.admitted:
                    refuse("G6_E_SYNTH_PACING", reservation.reason)
                    terminal = True
                    return None
            noted = 0
            send_lock = Lock()
            def before_send():
                nonlocal noted
                # MeteredCaller already charged the logical attempt. Extra sends
                # are charged in its finally, so include them in this preflight.
                with send_lock:
                    if meter.calls + noted > self.policy.max_synthesis_calls:
                        raise _Limit("next model send would exceed the synthesis policy call cap")
                    if reservation is not None and (reservation.ends is None or self.now() >= reservation.ends):
                        raise ExecutionRefused("G6_E_SYNTH_PACING", "weekly reservation is unbound or expired")
                    noted += 1
            try:
                user = (canonical({"view": json.loads(prompt), "repair": _scrub(feedback, unique)})
                        if feedback is not None else prompt)
                with counting(before_call=before_send):
                    reply = meter.complete(system, user, max_tokens=2048)
                exchange = digest({"system": system, "user": user, "reply": reply})
                data = json.loads(reply)
                if not isinstance(data, dict):
                    raise ValueError("proposal must be a JSON object")
                return data, exchange
            except _Limit as error:
                refuse("G6_E_SYNTH_CALL_LIMIT", str(error))
            except ExecutionRefused as error:
                terminal = True
                refuse(error.code, str(error))
            except (LLMError, ValueError, TypeError) as error:
                refuse("G6_E_SYNTH_MODEL", str(error))
            finally:
                if reservation is not None:
                    self.pacing.settle(reservation, reservation.amount)
            return None

        # Exactly one bindings attempt, then predicate and one repair. No free-form tests.
        stages = ["template bindings"]
        if self.policy.try_predicate:
            stages.append("predicate")
            if self.policy.repair_budget:
                stages.append("predicate repair")
        feedback = None
        for stage in stages:
            if meter.calls >= self.policy.max_synthesis_calls:
                break
            response = ask("Propose " + stage + ". Return JSON only: " + (
                '{"template": "seed id", "bindings": {}}. Bindings are data, never source.' if stage == "template bindings"
                else '{"source": "def solve(facts): ..."}. Return verdict and reason; no imports or case literals.'),
                feedback if stage == "predicate repair" else None)
            if response is None:
                if terminal:
                    break
                continue
            data, exchange = response
            try:
                if stage == "template bindings":
                    if set(data) != {"template", "bindings"} or data["bindings"] != {}:
                        raise ExecutionRefused("G6_E_SYNTH_BINDINGS", "trusted seeds accept only empty bindings")
                    body = TemplateInstance.model_validate(data)
                    method = candidate("template_instance", body, exchange=exchange)
                else:
                    if set(data) != {"source"} or not isinstance(data["source"], str):
                        raise ValueError("predicate proposal must contain only source text")
                    if any(literal in data["source"] for literal in unique):
                        raise ExecutionRefused("G6_E_SYNTH_CASE_LITERAL", "candidate contains a unique training literal")
                    method = candidate("predicate", data["source"], exchange=exchange)
                if covers(method):
                    return result(method)
                if method.form == "predicate":
                    feedback = {"source": method.body, "observed": last_observed,
                                "expected": [obs.expected for obs in observations]}
            except ExecutionRefused as error:
                refuse(error.code, str(error))
            except (ValueError, TypeError) as error:
                refuse("G6_E_SYNTH_MODEL", str(error))
        if meter.calls >= self.policy.max_synthesis_calls:
            refuse("G6_E_SYNTH_CALL_LIMIT", "synthesis policy call budget spent; next call refused")
        return result()


def propose_concept(cases: Sequence[SynthesisCase], *, synthesizer: Synthesizer):
    """One bounded concept proposal from independent training inputs only.

    Host caller retains disclosure admission; this adapter supplies no provider,
    credentials or consent. One send plus S's three sends holds the four-call cap.
    """
    from g6.core.door import blocked_reply
    def refused(code, reason):
        return blocked_reply(f'[{code}] {reason}')
    cases = tuple(cases)
    if (not cases or any(c.failure.split != 'training' or c.failure.observed_by != 'independent' for c in cases)
        or len({c.failure.family for c in cases}) != 1):
        return refused('G6_E_SYNTH_TRAINING_ONLY', 'Concepts require independent training failures.')
    caller = synthesizer.caller
    if caller is None:
        return refused('G6_E_SYNTH_NO_CALLER', 'No host model caller configured.')
    online = not _offline(caller)
    if online and (synthesizer.pacing is None or synthesizer.provider not in ('claude', 'codex')
                   or type(synthesizer.worst_case) not in (int, float)
                   or not math.isfinite(synthesizer.worst_case) or synthesizer.worst_case <= 0):
        return refused('G6_E_SYNTH_PACING', 'Bound weekly meter and positive worst-case reservation required.')
    with synthesizer._lock:
        key = digest(sorted(c.failure.record_id for c in cases))
        if key in synthesizer._concept_attempts:
            return refused('G6_E_SYNTH_CALL_LIMIT', 'Concept proposal send already attempted for these failures.')
        reservation = None
        if online:
            reservation = synthesizer.pacing.reserve(synthesizer.provider, synthesizer.now(), synthesizer.worst_case)
            if not reservation.admitted:
                return refused('G6_E_SYNTH_PACING', reservation.reason)
        synthesizer._concept_attempts.add(key)
        sends = 0
        def before_send():
            nonlocal sends
            if sends >= 1:
                raise _Limit('concept allows one send, inner retries included')
            if reservation is not None and (reservation.ends is None or synthesizer.now() >= reservation.ends):
                raise _Limit('concept reservation unbound or expired')
            sends += 1
        try:
            user = canonical({'family': cases[0].failure.family, 'training': [
                {'record_id': c.failure.record_id, 'facts': extract_facts(source_mapping(c.before), source_mapping(c.after),
                    split='training')} for c in cases]})
            system = ('Propose a general checking concept, JSON only: statement (at most 600 characters), '
                      'observable_features (trusted facts names), falsifier, prediction '
                      '({prediction_id, measure, before, expected}, with a direction), worth (0..1).')
            with counting(before_call=before_send):
                reply = caller.complete(system, user, max_tokens=2048)
            data = json.loads(reply)
            fields = {'statement', 'observable_features', 'falsifier', 'prediction', 'worth'}
            if not isinstance(data, dict) or set(data) != fields:
                raise ValueError('concept must contain exactly the requested data fields')
            return Concept(**data, family=cases[0].failure.family,
                from_failures=[c.failure.record_id for c in cases],
                proposer={'provider': synthesizer.provider or 'offline',
                          'model': type(caller).__name__, 'prompt_sha256': digest((system, user))},
                origin='model_proposed', status='proposed')
        except (ValueError, TypeError, LLMError, BudgetExhausted):
            return refused('G6_E_SYNTH_MODEL', 'Concept proposal refused; no independent evidence created.')
        finally:
            if reservation is not None:
                synthesizer.pacing.settle(reservation, reservation.amount)


__all__ = ["SynthesisCase", "SynthesisResult", "Synthesizer", "propose_concept"]
