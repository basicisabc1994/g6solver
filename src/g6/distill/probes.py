# SPDX-License-Identifier: Apache-2.0
"""Bounded v2 templates on explicit inputs, through the confined repository runner.

These observations cover a sampled operator family only. They grant no acceptance
authority. The shared mutation generator is named in every outcome's provenance.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import Field, model_validator

from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.workforce import mutation_check, repo_tests

from .records import Model, Record, Sha256

PROBE_NAMES_V2 = frozenset({"kill_set.v2", "discriminates_at_base.v2"})
Count = Annotated[int, Field(strict=True, ge=1)]


class KillSetBindings(Model):
    limit: Annotated[Count, Field(le=24)] = 24
    lines: Literal["changed_by_patch"] = "changed_by_patch"
    min_settled: Count = 12
    reruns: Annotated[Count, Field(le=3)] = 2

    @model_validator(mode="after")
    def sample(self):
        if self.min_settled > self.limit:
            raise ValueError("minimum settled sample exceeds limit")
        return self


class BaseBindings(Model):
    reruns: Annotated[Count, Field(le=3)] = 2


class ProbeTemplateInstance(Model):
    """Data-only integration schema; pinned v1 candidate records stay unchanged."""
    template: Literal["kill_set.v2", "discriminates_at_base.v2"]
    bindings: KillSetBindings | BaseBindings

    @model_validator(mode="before")
    @classmethod
    def bind(cls, value):
        if isinstance(value, dict) and value.get("template") in PROBE_NAMES_V2:
            value = dict(value)
            schema = KillSetBindings if value["template"] == "kill_set.v2" else BaseBindings
            bindings = value.get("bindings", {})
            if isinstance(bindings, Model):
                bindings = bindings.model_dump(mode="json")
            value["bindings"] = schema.model_validate(bindings)
        return value


class ProbeBudget(Model):
    wall_s: Annotated[float, Field(gt=0, le=180)]
    probe_runs: Annotated[int, Field(strict=True, ge=0, le=512)]
    model_calls: Literal[0] = 0


def _path(value):
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute() or path.as_posix() != value
            or ".." in path.parts or path.suffix != ".py" or any(p.startswith(".") for p in path.parts)
            or PureWindowsPath(value).is_reserved()
            or path.name in {"conftest.py", "sitecustomize.py", "usercustomize.py", "pytest.py"}):
        raise ValueError("expected an ordinary relative Python source path")
    return value


def _sources(value):
    if not isinstance(value, dict) or len(value) > 128:
        raise ValueError("expected at most 128 explicit source files")
    folded = {path.casefold() for path in value}
    if len(folded) != len(value):
        raise ValueError("source paths alias on Windows")
    for path, source in value.items():
        _path(path)
        if not isinstance(source, str) or len(source.encode("utf-8")) > 131072:
            raise ValueError("source text exceeds bound")
        if any(parent.as_posix().casefold() in folded for parent in PurePosixPath(path).parents):
            raise ValueError("file and directory paths conflict")
    if sum(len(s.encode("utf-8")) for s in value.values()) > 1048576:
        raise ValueError("source mapping exceeds byte bound")
    return dict(value)


class ProbeInput(Model):
    files: dict[str, str]
    target: str
    changed_lines: tuple[Count, ...] = Field(min_length=1, max_length=4096)
    base_implementation: str | None = Field(default=None, max_length=131072)
    new_tests: dict[str, str] | None = None

    @model_validator(mode="after")
    def paths(self):
        _sources(self.files)
        _path(self.target)
        if self.target not in self.files or len(set(self.changed_lines)) != len(self.changed_lines):
            raise ValueError("target and unique changed lines are required")
        if any(line > len(self.files[self.target].splitlines()) for line in self.changed_lines):
            raise ValueError("changed line outside target")
        if self.new_tests is not None:
            _sources(self.new_tests)
        return self


class RunObservation(Model):
    implementation_sha256: Sha256
    suite_sha256: Sha256
    report_sha256: Sha256
    isolation_backend: str = Field(min_length=1)
    status: Literal["passed", "failed", "unknown"]


class ProbeProvenance(Model):
    runner: Literal["g6.workforce.repo_tests._run"] = "g6.workforce.repo_tests._run"
    generator: Literal["g6.workforce.mutation_check.mutants", "explicit_base"]
    shared_mechanism: bool
    input_sha256: Sha256
    before_sha256: Sha256
    after_sha256: Sha256
    bindings_sha256: Sha256
    budget_sha256: Sha256
    split: Literal["training", "dev", "monitor", "field", "acceptance"]
    observations: tuple[RunObservation, ...]


class ProbeOutcome(Record):
    identity_field = "outcome_id"
    outcome_id: Sha256 | None = None
    template: Literal["kill_set.v2", "discriminates_at_base.v2"]
    verdict: Literal["concern", "no_concern", "abstain"]
    reason: str
    before_kills: tuple[Sha256, ...] = ()
    after_kills: tuple[Sha256, ...] = ()
    lost_kills: tuple[Sha256, ...] = ()
    settled: Annotated[int, Field(strict=True, ge=0)] = 0
    runs: Annotated[int, Field(strict=True, ge=0)]
    wall_s: Annotated[float, Field(ge=0)]
    provenance: ProbeProvenance

    @model_validator(mode="after")
    def consistent(self):
        if self.runs != len(self.provenance.observations):
            raise ValueError("run count differs from observations")
        if self.verdict != "abstain" and not self.runs:
            raise ValueError("a verdict requires executed observations")
        if set(self.lost_kills) != set(self.before_kills) - set(self.after_kills):
            raise ValueError("lost kills differ from observed sets")
        return self


def _stage_run(inputs, implementation, suite, *, workspace, timeout_s, python, deadline):
    with confine.run_folder(prefix="distill-probe-", dir=workspace) as folder:
        staging = Path(folder)
        repo = staging / "repo"
        repo.mkdir()
        for rel, source in {**inputs.files, **suite}.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(implementation if rel == inputs.target else source, encoding="utf-8", newline="\n")
        return repo_tests._run(repo, sorted(suite), roots=(), timeout_s=timeout_s, network=False,
                               copied=True, python=python or sys.executable, scratch_dir=staging,
                               deadline=deadline, require_isolation=True)


def run_probe(instance, before, after, *, split, probe_input, workspace, cost_bound, python=None):
    """Refuse resource/isolation failures; abstain on incomplete behavioural evidence."""
    from .facts import check_split

    check_split(split)
    return _run_probe(instance, before, after, split=split, probe_input=probe_input,
                      workspace=workspace, cost_bound=cost_bound, python=python)


def _run_probe(instance, before, after, *, split, probe_input, workspace, cost_bound, python=None):
    """Execution seam also used by the protected evaluator on its own inputs."""
    instance = ProbeTemplateInstance.model_validate(instance.model_dump(mode="json"))
    budget = ProbeBudget.model_validate(cost_bound)
    started = time.monotonic()
    deadline = started + budget.wall_s
    inputs = ProbeInput.model_validate(probe_input)
    before, after = _sources(before), _sources(after)
    for suite in (before, after, inputs.new_tests or {}):
        # Test overlays never replace any implementation or ancestor directory.
        _sources({**inputs.files, **suite})
        if set(suite) & set(inputs.files):
            raise ValueError("test paths overlap implementation files")
        if any(not (PurePosixPath(p).name.startswith("test_") or PurePosixPath(p).name.endswith("_test.py"))
               for p in suite):
            raise ValueError("suite must name pytest test files")
    workspace = Path(workspace)
    if any(p.is_symlink() or p.is_junction() for p in (workspace, *workspace.parents)):
        raise ValueError("linked staging workspace refused")
    if not workspace.is_dir():
        raise ValueError("an existing staging workspace is required")
    home = os.environ.get("G6_HOME")
    if home and workspace.resolve().is_relative_to((Path(home) / "distill" / "held_out").resolve()):
        raise ValueError("sealed custody cannot be an execution workspace")
    observations, old_kills, new_kills = [], set(), set()
    settled = 0
    bindings = instance.bindings
    fixed = inputs.files[inputs.target]

    def refused():
        raise repo_tests.RepoRefused("G6_E_PROBE_BUDGET", "probe cost bound exhausted or insufficient")

    def check_time():
        if time.monotonic() >= deadline:
            refused()

    def answer(verdict, reason):
        check_time()
        return ProbeOutcome(template=instance.template, verdict=verdict, reason=reason,
                            before_kills=tuple(sorted(old_kills)), after_kills=tuple(sorted(new_kills)),
                            lost_kills=tuple(sorted(old_kills - new_kills)), settled=settled,
                            runs=len(observations), wall_s=time.monotonic() - started,
                            provenance=ProbeProvenance(
                                generator="g6.workforce.mutation_check.mutants" if instance.template == "kill_set.v2"
                                else "explicit_base", shared_mechanism=instance.template == "kill_set.v2",
                                input_sha256=digest(inputs.model_dump(mode="json")), before_sha256=digest(before),
                                after_sha256=digest(after), bindings_sha256=digest(bindings.model_dump(mode="json")),
                                budget_sha256=digest(budget.model_dump(mode="json")), split=split,
                                observations=tuple(observations)))

    def run(implementation, suite):
        remaining = int(deadline - time.monotonic())
        if len(observations) >= budget.probe_runs or remaining < 1:
            refused()
        raw = _stage_run(inputs, implementation, suite, workspace=workspace, timeout_s=min(20, remaining),
                         python=python, deadline=deadline)
        check_time()
        isolation = raw.get("isolation")
        if not isinstance(isolation, dict) or not isolation.get("backend") or isolation.get("network") is not False:
            raise repo_tests.RepoRefused(ISOLATION_UNAVAILABLE, "probe requires offline operating-system isolation")
        if raw.get("verdict") == "timed_out":
            refused()
        status = repo_tests.established(raw, sources=tuple({**inputs.files, **suite})).of()
        status = status if status in {"passed", "failed"} else "unknown"
        observations.append(RunObservation(implementation_sha256=digest(implementation), suite_sha256=digest(suite),
                                           report_sha256=digest(raw), isolation_backend=isolation["backend"], status=status))
        return status

    if instance.template == "kill_set.v2":
        if budget.probe_runs < 2 + 2 * bindings.min_settled * bindings.reruns:
            refused()
        try:
            sample = mutation_check.mutants(fixed, limit=bindings.limit, lines=inputs.changed_lines, deadline=deadline)
        except mutation_check.Unfinished:
            refused()
        check_time()
        sample = {digest(m.source): m.source for m in sample if m.source != fixed}
        if 2 + 2 * len(sample) * bindings.reruns > budget.probe_runs:
            refused()
    else:
        if not inputs.new_tests:
            return answer("abstain", "no_new_tests")
        if inputs.base_implementation is None:
            raise ValueError("base implementation is required")
        if any(after.get(p) != text or before.get(p) == text for p, text in inputs.new_tests.items()):
            raise ValueError("new tests must be supplied changed or added after-tests")
        if budget.probe_runs < 3 + bindings.reruns:
            refused()
    if not before or not after:
        return answer("abstain", "empty_suite")
    if run(fixed, before) != "passed" or run(fixed, after) != "passed":
        return answer("abstain", "fixed_suite_did_not_pass")
    if instance.template == "discriminates_at_base.v2":
        if run(fixed, inputs.new_tests) != "passed":
            return answer("abstain", "fixed_suite_did_not_pass")
        outcomes = [run(inputs.base_implementation, inputs.new_tests) for _ in range(bindings.reruns)]
        if "unknown" in outcomes or len(set(outcomes)) != 1:
            return answer("abstain", "base_unsettled")
        return answer("concern" if outcomes[0] == "passed" else "no_concern", "base_" + outcomes[0])
    if len(sample) < bindings.min_settled:
        return answer("abstain", "too_few_settled_mutants")
    for ident, source in sample.items():
        old, new = run(source, before), run(source, after)
        if "unknown" in (old, new):
            return answer("abstain", "mutant_unsettled")
        settled += 1
        if old == "failed":
            old_kills.add(ident)
        if new == "failed":
            new_kills.add(ident)
    for ident in sorted(old_kills - new_kills):
        for _ in range(bindings.reruns - 1):
            if (run(sample[ident], before), run(sample[ident], after)) != ("failed", "passed"):
                return answer("abstain", "lost_kill_unsettled")
    if old_kills - new_kills and new_kills - old_kills:
        return answer("abstain", "kills_lost_and_gained")
    return answer("concern" if new_kills < old_kills else "no_concern",
                  "lost_kill" if new_kills < old_kills else "no_lost_kill_on_sample")


def outcome_facts(outcomes, before, after):
    """Check content identity and source binding before recording v2 facts."""
    result = {}
    for name, outcome in outcomes.items():
        if name not in PROBE_NAMES_V2:
            result[name] = outcome
            continue
        if isinstance(outcome, ProbeOutcome):
            outcome = outcome.model_dump(mode="json")
        if not isinstance(outcome, dict) or not outcome.get("outcome_id"):
            raise ValueError("recorded probe outcome requires its content identity")
        row = ProbeOutcome.model_validate(outcome)
        if row.template != name or row.provenance.before_sha256 != digest(before) or row.provenance.after_sha256 != digest(after):
            raise ValueError("probe source binding differs from fact sources")
        result[name] = row.model_dump(mode="json")
    return result
