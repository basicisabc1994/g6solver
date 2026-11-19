# SPDX-License-Identifier: Apache-2.0
"""Local bug-injection admission over S1 tasks and the existing mutation runner."""
from __future__ import annotations

import ast
import json
import math
import random
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from g6.recursion.tasks import TaskSpec
from g6.rsi.records import CheckSpec, TaskError, content_digest
from g6.workforce import mutation_check

Runner = Callable[[str, CheckSpec, int], dict]
_ORIGINS = {"human", "dataset", "solver"}
_LICENCES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC0-1.0"}


@dataclass(frozen=True)
class ReferenceOrigin:
    kind: str
    identity: str
    derived_from: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReferenceSolution:
    code: str
    origin: ReferenceOrigin


@dataclass(frozen=True)
class IssueTemplate:
    behaviour: str
    message: str
    provenance: str = "template"

    def render(self) -> str:
        return f"Repair {self.behaviour}.\nObserved failure: {self.message}"


@dataclass(frozen=True)
class FamilySpec:
    task: TaskSpec
    repository_id: str
    bug_class: str
    repository_licence: str
    hidden_tests: CheckSpec
    primary_origin: ReferenceOrigin
    second_reference: ReferenceSolution
    issue: IssueTemplate
    suite_origin: str = "repository"
    operator: str = "bug_injection"
    actor_outputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedFamily:
    """Evaluator metadata; only ``tasks`` use the S1 task record format."""

    tasks: tuple[TaskSpec, ...]
    hidden_tests: CheckSpec
    second_reference: ReferenceSolution
    primary_origin: ReferenceOrigin
    family_digest: str
    seed: int
    repository_id: str
    repository_licence: str
    bug_class: str
    issue_provenance: str
    strength: dict

    def to_record(self) -> dict:
        record = {"tasks": [task.to_record() for task in self.tasks],
                "hidden_tests": self.hidden_tests.to_record(), "second_reference": asdict(self.second_reference),
                "primary_origin": asdict(self.primary_origin), "family_digest": self.family_digest,
                "seed": self.seed, "repository_id": self.repository_id,
                "repository_licence": self.repository_licence, "bug_class": self.bug_class,
                "issue_provenance": self.issue_provenance, "strength": dict(self.strength)}
        return json.loads(json.dumps(record))


def _syntax(code: str) -> str:
    try:
        tree = ast.parse(code)
        compile(tree, "<family-source>", "exec")
        if not tree.body:
            raise ValueError
        return ast.dump(tree, include_attributes=False)
    except (SyntaxError, ValueError, TypeError):
        raise TaskError("G6_E_RSI_GENERATOR_REFERENCE") from None


def family_digest(task: TaskSpec) -> str:
    """Keep source clones together even when names, suites, splits or seeds change."""
    return content_digest({"reference": _syntax(task.hidden_ref)})


def _normal(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _screen(spec: FamilySpec, texts: object) -> None:
    if type(spec.actor_outputs) is not tuple or any(type(t) is not str for t in spec.actor_outputs):
        raise TaskError("G6_E_RSI_GENERATOR_ORIGIN")
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from strings(item)

    target = _normal("\n".join(strings(texts)))
    for output in spec.actor_outputs:
        normalized = _normal(output)
        if not normalized:
            continue
        words = normalized.split()
        fragments = (" ".join(words[i:i + 13]) for i in range(len(words) - 12))
        if normalized in target or any(fragment in target for fragment in fragments):
            raise TaskError("G6_E_RSI_GENERATOR_ORIGIN")


def _origin(origin: ReferenceOrigin) -> set[str]:
    if not isinstance(origin, ReferenceOrigin) or origin.kind not in _ORIGINS:
        raise TaskError("G6_E_RSI_GENERATOR_ORIGIN")
    if (type(origin.identity) is not str or not origin.identity.strip() or type(origin.derived_from) is not tuple
            or any(type(s) is not str or not s.strip() for s in origin.derived_from)):
        raise TaskError("G6_E_RSI_GENERATOR_REFERENCE")
    return {origin.identity, *origin.derived_from}


def _validate(spec: FamilySpec) -> TaskSpec:
    if not isinstance(spec, FamilySpec):
        raise TaskError("G6_E_RSI_GENERATOR_INPUT")
    task = TaskSpec.from_record(spec.task.to_record())
    if (task.source != "generated" or task.split not in ("train", "dev", "held_out")
            or spec.operator != "bug_injection" or spec.repository_licence not in _LICENCES
            or any(type(s) is not str or not s.strip() for s in (spec.repository_id, spec.bug_class))):
        raise TaskError("G6_E_RSI_GENERATOR_INPUT")
    if (not isinstance(spec.issue, IssueTemplate) or spec.issue.provenance != "template"
            or spec.suite_origin != "repository"
            or any(type(s) is not str or not s.strip() for s in (spec.issue.behaviour, spec.issue.message))):
        raise TaskError("G6_E_RSI_GENERATOR_ORIGIN")
    if not isinstance(spec.hidden_tests, CheckSpec) or not isinstance(spec.second_reference, ReferenceSolution):
        raise TaskError("G6_E_RSI_GENERATOR_REFERENCE")
    first = _origin(spec.primary_origin)
    second = _origin(spec.second_reference.origin)
    if first & second or _syntax(task.hidden_ref) == _syntax(spec.second_reference.code):
        raise TaskError("G6_E_RSI_GENERATOR_REFERENCE")
    _screen(spec, {"task": task.to_record(), "issue": asdict(spec.issue),
                   "hidden_tests": spec.hidden_tests.to_record(),
                   "second_reference": asdict(spec.second_reference),
                   "primary_origin": asdict(spec.primary_origin),
                   "repository_id": spec.repository_id, "bug_class": spec.bug_class})
    return task


def _outcome(raw: dict) -> SimpleNamespace:
    """Adapt completed runner tallies; infrastructure failures never count as kills."""
    if (type(raw) is not dict or raw.get("verdict") not in ("passed", "failed")
            or raw.get("degraded", True) is not False or raw.get("not_passed", 1) != 0
            or raw.get("deselected", 1) != 0 or raw.get("setup_errors", {"unknown": ""})
            or any(raw.get(key) for key in ("missing_modules", "os_errors", "collection_stopped", "omitted_ids"))
            or not isinstance(raw.get("isolation"), dict) or not raw["isolation"].get("backend")):
        raise TaskError("G6_E_RSI_GENERATOR_RUN")
    passed, failed = raw.get("passed"), raw.get("failed")
    if (type(passed) is not int or type(failed) is not int or passed < 0 or failed < 0 or passed + failed == 0
            or (raw["verdict"] == "passed") != (failed == 0)):
        raise TaskError("G6_E_RSI_GENERATOR_RUN")
    return SimpleNamespace(passed=passed, failed=failed, not_passed=0, reason="",
                           execution_id=content_digest({"passed": passed, "failed": failed}))


def generate_family(spec: FamilySpec, *, seed: int, repo: str | Path | None = None,
                    run: Runner | None = None, budget_s: float = 120,
                    timeout_s: int = 30) -> GeneratedFamily:
    """Refuse a family unless both references pass and every sampled mutation fails."""
    if (type(seed) is not int or seed < 0 or type(timeout_s) is not int or not 1 <= timeout_s <= 300
            or type(budget_s) not in (int, float) or not math.isfinite(budget_s) or budget_s <= 0):
        raise TaskError("G6_E_RSI_GENERATOR_INPUT")
    task = _validate(spec)
    if run is None:
        if repo is None:
            raise TaskError("G6_E_RSI_GENERATOR_INPUT")
        from g6.recursion.tasks import run_checks

        def run(code, checks, timeout_s):
            check_task = replace(task, public_tests=checks, provenance=None)
            return run_checks(check_task, code, repo=repo, timeout_s=timeout_s)

    totals = {}
    for code in (task.hidden_ref, spec.second_reference.code):
        for label, checks in (("hidden", spec.hidden_tests), ("public", task.public_tests)):
            for _ in range(3):
                result = _outcome(run(code, checks, timeout_s))
                if result.failed:
                    raise TaskError("G6_E_RSI_GENERATOR_REFERENCE")
                if label in totals and result.passed != totals[label]:
                    raise TaskError("G6_E_RSI_GENERATOR_RUN")
                totals[label] = result.passed
    candidates = mutation_check.mutants(task.hidden_ref)
    random.Random(seed).shuffle(candidates)
    if not candidates:
        raise TaskError("G6_E_RSI_GENERATOR_WEAK_SUITE")

    def mutated(code, seconds):
        result = _outcome(run(code, spec.hidden_tests, min(seconds, timeout_s)))
        if result.passed + result.failed != totals["hidden"]:
            raise TaskError("G6_E_RSI_GENERATOR_RUN")
        # This binds a local result to its candidate, not to a stored runner receipt.
        result.execution_id = content_digest({"code": code, "checks": spec.hidden_tests.to_record(),
                                              "passed": result.passed, "failed": result.failed})
        return result

    strength = mutation_check.check(task.hidden_ref, mutated, budget_s=budget_s,
                                    base_duration_s=0, candidates=candidates)
    if (strength.get("stopped") or strength.get("tried") != len(candidates)
            or strength.get("rejected") != len(candidates) or strength.get("survived")):
        raise TaskError("G6_E_RSI_GENERATOR_WEAK_SUITE")
    digest = family_digest(task)
    tasks = tuple(replace(task, task_id=f"generated:{content_digest([digest, seed, mutant.source])}",
                          prompt=f"{spec.issue.render()}\n\n```python\n{mutant.source}\n```", provenance=None)
                  for mutant in candidates)
    summary = {key: strength[key] for key in ("ran", "tried", "rejected")}
    generated = GeneratedFamily(tasks, spec.hidden_tests, spec.second_reference, spec.primary_origin,
                                digest, seed, spec.repository_id, spec.repository_licence, spec.bug_class,
                                spec.issue.provenance, summary)
    _screen(spec, generated.to_record())
    return generated
