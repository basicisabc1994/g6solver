# SPDX-License-Identifier: Apache-2.0
"""Interpret candidate data on training observations without conferring authority.

Predicates require strict isolation and use only the existing confined batch runner.
No global environment changes occur here. Current inactive identities are read
under the shared status transaction before execution, including training reuse.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

from g6.core.ledger import canonical
from g6.core import stop
from g6.distillation import sandbox

from .detectors import run_template
from .facts import _extract_facts
from .inactive_identity import method_identity, recorded_cases
from .ledger import Stores, failure_writer
from .library import Library
from .records import MethodContract, VerifierCandidate


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


def _recorded_verdicts(candidate, cases, resolve):
    observations = [Observation({'tests/test_case.py': case['before_tests']},
                                {'tests/test_case.py': case['after_tests']},
                                _extract_facts({'tests/test_case.py': case['before_tests']},
                                               {'tests/test_case.py': case['after_tests']}))
                    for case in cases]
    values, costs = [], []
    for observation in observations:
        repeats = []
        for _ in range(3):
            if stop.engaged() is not None:
                raise ValueError('stop engaged during recorded comparison')
            start = time.monotonic()
            # Match the evaluator's abstention for unregistered feature/probe semantics.
            repeats.append('abstain' if candidate.contract.applies_when or candidate.contract.probes else
                           _execute_candidate(candidate, [observation], resolve)[0])
            costs.append(time.monotonic() - start)
        if len(set(repeats)) != 1:
            raise ValueError('flaky recorded verdict')
        values.append(repeats[0])
    if costs and sum(cost > candidate.contract.cost_bound.wall_s for cost in costs) / len(costs) > .1:
        raise ValueError('recorded cost bound exceeded')
    return values


def _check_runtime_inactive(candidate, resolve):
    entries = [entry for entry in Library().list() if entry.family == candidate.contract.family
               and entry.status in {'held', 'withdrawn'}]
    try:
        identity = None
        for entry in sorted(entries, key=lambda entry: entry.status != 'held'):
            if entry.candidate_id == candidate.candidate_id:
                raise ExecutionRefused(f'G6_E_DISTILL_METHOD_{entry.status.upper()}',
                                       f'G6_E_DISTILL_METHOD_{entry.status.upper()}: inactive executable method')
            historical = Stores().for_model(VerifierCandidate).get(entry.candidate_id)
            if identity is None:
                identity = method_identity(candidate)
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


@failure_writer()
def run_candidate(candidate: VerifierCandidate, observations: list[Observation], resolve, *, stack=()) -> list[str]:
    """Resolve parents read-only; reject stale parents and dependency cycles."""
    candidate = VerifierCandidate.model_validate_json(canonical(candidate.model_dump(mode="json")))
    _check_runtime_inactive(candidate, resolve)
    return _execute_candidate(candidate, observations, resolve, stack=stack)


def _execute_candidate(candidate, observations, resolve, *, stack=()):
    """Internal recorded replay skips only the top-level guard; parents remain guarded."""
    if candidate.candidate_id in stack or len(stack) >= 16:
        raise ExecutionRefused("G6_E_SYNTH_DEPENDENCY", "cyclic or over-deep method dependency")
    stack = (*stack, candidate.candidate_id)
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
        values = run_candidate(resolve(candidate.body), selected, resolve, stack=stack)
    else:
        body = candidate.body
        if body.op == "sequence":
            values = ["abstain"] * len(selected)
            for ident in body.entries:
                pending = [i for i, value in enumerate(values) if value == "abstain"]
                if not pending:
                    break
                outcomes = run_candidate(resolve(ident), [selected[i] for i in pending], resolve, stack=stack)
                for i, value in zip(pending, outcomes, strict=True):
                    values[i] = value
        else:
            columns = [run_candidate(resolve(ident), selected, resolve, stack=stack) for ident in body.entries]
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
