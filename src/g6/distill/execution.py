# SPDX-License-Identifier: Apache-2.0
"""Interpret candidate data on training observations without conferring authority.

Predicates require strict isolation and use only the existing confined batch runner.
No global environment changes occur here. Status is snapshotted before execution
and checked again afterwards; detector work never holds the status transaction.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

from g6.core.ledger import canonical, digest
from g6.distillation import sandbox

from .detectors import run_template
from .facts import _extract_facts, source_mapping
from .inactive_identity import method_identity, recorded_cases
from .ledger import Stores
from .records import MethodContract, VerifierCandidate
from .runtime_state import StatusChanged, measure_executions, require_current, run_memo, run_scope


@dataclass(frozen=True)
class Observation:
    before: Mapping[str, str]
    after: Mapping[str, str]
    facts: dict
    expected: str = "concern"
    language: str = "python"
    framework: str = "pytest"


class ExecutionRefused(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


@run_scope()
def _recorded_verdicts(candidate, cases, resolve):
    if type(candidate) is not VerifierCandidate:
        raise ValueError('typed candidate required')
    candidate = VerifierCandidate.model_validate_json(canonical(candidate.model_dump(mode='json')))
    cases = list(cases)
    observations = [Observation({'tests/test_case.py': case['before_tests']},
                                {'tests/test_case.py': case['after_tests']},
                                _extract_facts({'tests/test_case.py': case['before_tests']},
                                               {'tests/test_case.py': case['after_tests']}))
                    for case in cases]
    context = tuple(entry.entry_id for entry in run_memo().snapshot())
    values, _ = measure_executions(candidate, list(zip(cases, observations, strict=True)),
        key_for=lambda pair: ('recorded', candidate.candidate_id, digest(pair[0]), context),
        # Match evaluator abstention for unregistered feature/probe semantics.
        execute=lambda pair: ('abstain' if candidate.contract.applies_when or candidate.contract.probes else
                              _execute_candidate(candidate, [pair[1]], resolve)[0]),
        clock=time.monotonic)
    return values


def _check_runtime_inactive(candidate, resolve):
    memo = run_memo()
    try:
        identity = method_identity(candidate, resolve=resolve)
        key = ('training', identity)
        if key in memo.checked:
            return
        entries = [entry for entry in memo.snapshot() if entry.family == candidate.contract.family
                   and entry.status in {'held', 'withdrawn'}]
        for entry in sorted(entries, key=lambda entry: entry.status != 'held'):
            if entry.candidate_id == candidate.candidate_id:
                raise ExecutionRefused(f'G6_E_DISTILL_METHOD_{entry.status.upper()}',
                                       f'G6_E_DISTILL_METHOD_{entry.status.upper()}: inactive executable method')
            historical = Stores().for_model(VerifierCandidate).get(entry.candidate_id)
            if identity == method_identity(historical):
                raise ExecutionRefused(f'G6_E_DISTILL_METHOD_{entry.status.upper()}',
                                       f'G6_E_DISTILL_METHOD_{entry.status.upper()}: inactive executable method')
        for entry in entries:
            cases, expected = recorded_cases(entry)
            missing = {ident: case for ident, case in cases.items() if ident not in expected}
            if missing:
                historical = Stores().for_model(VerifierCandidate).get(entry.candidate_id)
                expected.update(zip(missing, _recorded_verdicts(historical, missing.values(), resolve), strict=True))
            actual = _recorded_verdicts(candidate, cases.values(), resolve)
            if all(value == expected[ident] for ident, value in zip(cases, actual, strict=True)):
                raise ExecutionRefused(f'G6_E_DISTILL_METHOD_{entry.status.upper()}',
                                       f'G6_E_DISTILL_METHOD_{entry.status.upper()}: inactive executable method')
    except ExecutionRefused as error:
        if error.code in {'G6_E_DISTILL_METHOD_HELD', 'G6_E_DISTILL_METHOD_WITHDRAWN'}:
            raise
        code = error.code if re.fullmatch(r'G6_E_[A-Z0-9_]+', error.code) else 'G6_E_DISTILL_RECORDED_CASES'
        raise ExecutionRefused('G6_E_DISTILL_IDENTITY_UNKNOWN',
                               f'G6_E_DISTILL_IDENTITY_UNKNOWN: {code}: inactive comparison unavailable') from None
    except Exception:
        raise ExecutionRefused('G6_E_DISTILL_IDENTITY_UNKNOWN',
                               'G6_E_DISTILL_IDENTITY_UNKNOWN: G6_E_DISTILL_RECORDED_CASES: '
                               'inactive comparison unavailable') from None
    memo.checked.add(key)


def in_scope(contract: MethodContract, observation: Observation) -> bool:
    if observation.language not in contract.languages or observation.framework not in contract.frameworks:
        return False
    for predicate in contract.applies_when:
        value = observation.facts.get(predicate.feature)
        if value is None:
            return False
        try:
            other = predicate.value
            if predicate.op == "eq":
                matches = value == other
            elif predicate.op == "ne":
                matches = value != other
            elif predicate.op == "gt":
                matches = value > other
            elif predicate.op == "ge":
                matches = value >= other
            elif predicate.op == "lt":
                matches = value < other
            elif predicate.op == "le":
                matches = value <= other
            else:
                matches = other in value
            if not matches:
                return False
        except (TypeError, ValueError):
            return False
    return True


@run_scope()
def run_candidate(candidate: VerifierCandidate, observations: list[Observation], resolve, *, stack=()) -> list[str]:
    """Resolve parents read-only; reject stale parents and dependency cycles."""
    if type(candidate) is not VerifierCandidate:
        raise ValueError('typed candidate required')
    candidate = VerifierCandidate.model_validate_json(canonical(candidate.model_dump(mode="json")))
    memo = run_memo()
    resolved = {}

    def frozen_resolve(ref):
        if ref not in resolved:
            child = resolve(ref)
            resolved[ref] = VerifierCandidate.model_validate_json(canonical(child.model_dump(mode='json')))
        return resolved[ref]
    if not isinstance(observations, list) or not callable(resolve):
        raise ValueError('typed observations and dependency resolver required')
    for observation in observations:
        if (not isinstance(observation, Observation) or not isinstance(observation.facts, dict)
                or any(not isinstance(value, str) for value in
                       (observation.expected, observation.language, observation.framework))):
            raise ValueError('invalid training observation')
        source_mapping(observation.before)
        source_mapping(observation.after)
        canonical(observation.facts)
    snapshot = memo.snapshot()
    _guard_dependencies(candidate, frozen_resolve, stack=stack)
    try:
        require_current(snapshot)
        result = _execute_candidate(candidate, observations, frozen_resolve, stack=stack)
        require_current(snapshot)
    except StatusChanged as error:
        raise ExecutionRefused(error.code, str(error)) from None
    return result


def _guard_dependencies(candidate, resolve, *, stack=()):
    if candidate.candidate_id in stack or len(stack) >= 16:
        raise ExecutionRefused('G6_E_SYNTH_DEPENDENCY', 'cyclic or over-deep method dependency')
    memo = run_memo()
    key = ('training', candidate.candidate_id)
    if key in memo.walked:
        return
    refs = ([candidate.body] if candidate.form == 'reuse' else
            candidate.body.entries if candidate.form == 'composition' else [])
    for ref in refs:
        child = resolve(ref)
        child = VerifierCandidate.model_validate_json(canonical(child.model_dump(mode='json')))
        _guard_dependencies(child, resolve, stack=(*stack, candidate.candidate_id))
    _check_runtime_inactive(candidate, resolve)
    memo.walked.add(key)


def _execute_candidate(candidate, observations, resolve, *, stack=()):
    """Raw replay never starts another inactive comparison, even for dependencies."""
    if candidate.candidate_id in stack or len(stack) >= 16:
        raise ExecutionRefused("G6_E_SYNTH_DEPENDENCY", "cyclic or over-deep method dependency")
    stack = (*stack, candidate.candidate_id)
    memo = run_memo()
    if memo.measuring:
        return _execute_uncached(candidate, observations, resolve, stack=stack)
    identity = method_identity(candidate, resolve=resolve)
    keys = [('training', identity, digest([obs.before, obs.after, obs.facts, obs.language, obs.framework]),
             memo.repeat) for obs in observations]
    missing = list(dict.fromkeys(key for key in keys if key not in memo.values))
    if not missing:
        return [memo.values[key] for key in keys]
    selected_indices = [keys.index(key) for key in missing]
    pending = [observations[index] for index in selected_indices]
    values = _execute_uncached(candidate, pending, resolve, stack=stack)
    memo.values.update(zip(missing, values, strict=True))
    return [memo.values[key] for key in keys]


def _execute_uncached(candidate, observations, resolve, *, stack):
    active = [i for i, obs in enumerate(observations) if in_scope(candidate.contract, obs)]
    results = ["abstain"] * len(observations)
    if not active:
        return results
    selected = [observations[i] for i in active]
    if candidate.form == "template_instance":
        if candidate.body.bindings:
            raise ExecutionRefused("G6_E_SYNTH_BINDINGS", "seed bindings must be empty data")
        values = [run_template(candidate.body.template, obs.before, obs.after, split="training").verdict
                  for obs in selected]
    elif candidate.form == "predicate":
        problems = sandbox.check_source(candidate.body, max_chars=8192)
        if problems:
            raise ExecutionRefused("G6_E_SYNTH_SOURCE", problems[0])
        if os.environ.get("G6_REQUIRE_ISOLATION", "").strip().lower() not in {"1", "true", "yes", "on"}:
            raise ExecutionRefused("G6_E_ISOLATION_UNAVAILABLE", "predicate execution requires strict isolation")
        runs = sandbox.run_batch(candidate.body, [obs.facts for obs in selected],
                                 timeout_s=candidate.contract.cost_bound.wall_s, memory_mb=256)
        if len(runs) != len(selected):
            raise ExecutionRefused("G6_E_SYNTH_EXECUTION", "runner output count differs from input count")
        values = []
        for run in runs:
            if not run.ok:
                code = "G6_E_ISOLATION_UNAVAILABLE" if "G6_E_ISOLATION_UNAVAILABLE" in run.reason else (
                    "G6_E_SYNTH_EXECUTION")
                raise ExecutionRefused(code, run.reason)
            out = run.value
            if (not isinstance(out, dict) or set(out) != {"verdict", "reason"}
                    or out.get("verdict") not in candidate.contract.verdict_kinds
                    or not isinstance(out.get("reason"), str)):
                raise ExecutionRefused("G6_E_SYNTH_EXECUTION", "predicate must return verdict and reason")
            values.append(out["verdict"])
    elif candidate.form == "reuse":
        values = _execute_candidate(resolve(candidate.body), selected, resolve, stack=stack)
    else:
        body = candidate.body
        if body.op == "sequence":
            values = ["abstain"] * len(selected)
            for ident in body.entries:
                pending = [i for i, value in enumerate(values) if value == "abstain"]
                if not pending:
                    break
                outcomes = _execute_candidate(resolve(ident), [selected[i] for i in pending], resolve, stack=stack)
                for i, value in zip(pending, outcomes, strict=True):
                    values[i] = value
        else:
            columns = [_execute_candidate(resolve(ident), selected, resolve, stack=stack) for ident in body.entries]
            values = []
            for row in zip(*columns, strict=True):
                concerns, abstains = row.count("concern"), row.count("abstain")
                needed = 1 if body.op == "any" else len(row) if body.op == "all" else body.k
                verdict = "concern" if concerns >= needed else (
                    "abstain" if concerns + abstains >= needed else "no_concern")
                values.append(verdict)
    for i, value in zip(active, values, strict=True):
        results[i] = value
    return results
