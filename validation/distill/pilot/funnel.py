"""Bounded acquisition feasibility counts; no case bodies are published."""
from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType, SimpleNamespace

from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.distill import acceptance_gate as production, operators
from g6.distill.acceptance_gate import _headline
from g6.distill.held_out import oracle
from g6.distill.held_out.case_gate import CaseRecord, Provenance
from g6.workforce import mutation_check, repo_tests
from validation.worked_examples.selection import is_test

from . import _material, access, allocation, convert, preflight

MAX_RUNS = 600
MAX_SECONDS = 3 * 60 * 60
ARMS = ("as_built", "fixed_tests", "multi_target")
VARIANTS = (*operators.TRAINING, *operators.LEGITIMATE, "reversed_maintainer")
STAGES = ("extraction", "shape", "isolation", "fixed_suites", "control", "mutant_count",
          "settle", "label", "headline")
REPOSITORY = Path(__file__).resolve().parents[3]
SHAPE_REASONS = frozenset(("no_python_fix", "multiple_python_targets", "empty_changed_lines", "missing_target_pair",
                           "single_test_module_required", "missing_test_pair", "multi_target_required"))
EXTRACT_REASONS = frozenset(("image_metadata_invalid", "image_missing", "image_id_mismatch", "extraction_failed"))
EXTRACT_MESSAGES = {
    "ValueError: missing or large support source": "support_missing_or_large",
    "ValueError: missing or large source": "fix_source_missing_or_large",
    "ValueError: linked support path": "support_linked_path",
    "ValueError: unsupported fix path": "unsupported_fix_path",
    "ValueError: support source too large": "support_source_too_large",
}
SUITE_COUNTERS = ("suite_requests", "requests_refused", "runs_not_started",
                  "returned_passed", "returned_failed", "returned_unknown")


@dataclass(frozen=True)
class SuiteObservation:
    outcome: str
    reason: str = ""
    verdict: str | None = None
    not_started: bool = False


def _verdict(value):
    return value if isinstance(value, str) and value in repo_tests.REPORT_VERDICTS else "unknown"


class _Stop(Exception):
    def __init__(self, reason):
        self.reason = reason


class _BudgetStop(_Stop):
    def __init__(self):
        super().__init__("unrun_budget")


def _bind(function, **bindings):
    """Use the existing function body with local dependencies, leaving modules untouched."""
    bound = FunctionType(function.__code__, {**function.__globals__, **bindings}, function.__name__,
                         function.__defaults__, function.__closure__)
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


def suite_observation(case, implementation, suite, *, workspace, timeout_s, python=None):
    """The oracle's staging route, with only structured diagnostics retained."""
    reason = ""
    observation = None

    def request(*args, **kwargs):
        nonlocal observation
        raw = repo_tests._run(*args, **kwargs)
        verdict = _verdict(raw.get("verdict"))
        observation = SuiteObservation("unknown", verdict=verdict,
                                       not_started=verdict in {"could_not_start", "not_run"})
        return raw

    def established(raw, **kwargs):
        nonlocal reason
        result = repo_tests.established(raw, **kwargs)
        if result.of(oracle.TEST_PATH) not in {"passed", "failed"}:
            if raw.get("missing_modules"):
                reason = "missing_module"
            elif result.withheld in {"loading_unknown", "foreign_code"}:
                reason = "source_loading_failure"
            elif raw.get("verdict") == "no_tests_collected":
                reason = "no_tests_collected"
        return result

    adapter = SimpleNamespace(_run=request, established=established, RepoRefused=repo_tests.RepoRefused)
    run = _bind(oracle._run, repo_tests=adapter)
    try:
        outcome = run(case, implementation, suite, workspace=workspace, timeout_s=timeout_s, python=python)
    except repo_tests.RepoRefused as exc:
        exc._funnel_observation = observation
        raise
    return SuiteObservation(outcome, reason, observation.verdict, observation.not_started)


class _Budget:
    def __init__(self, clock):
        self.clock, self.started, self.runs = clock, clock(), 0
        self.row = None

    def remaining(self):
        return MAX_SECONDS - (self.clock() - self.started)

    def check(self):
        if self.runs >= MAX_RUNS or self.remaining() < 1:
            raise _BudgetStop()

    def suite(self, runner, case, implementation, tests, *, workspace, timeout_s, python=None):
        self.check()
        self.runs += 1
        self.row["runs"] += 1
        self.row["suite_requests"] += 1
        self.row["verdict"] = None
        self.row["stages"][self.row["stage"]]["runs"] += 1
        try:
            result = runner(case, implementation, tests, workspace=workspace,
                            timeout_s=min(timeout_s, int(self.remaining())), python=python)
        except repo_tests.RepoRefused as exc:
            observation = getattr(exc, "_funnel_observation", None)
            if observation is None:
                self.row["requests_refused"] += 1
                self.row["runs_not_started"] += 1
            else:
                self.row["verdict"] = observation.verdict
                self.row["runs_not_started"] += int(observation.not_started)
                self.row["returned_unknown"] += 1
            raise
        outcome = result.outcome if isinstance(result, SuiteObservation) else result
        self.row["returned_" + (outcome if outcome in {"passed", "failed"} else "unknown")] += 1
        if isinstance(result, SuiteObservation):
            self.row["verdict"] = _verdict(result.verdict) if result.verdict is not None else None
            self.row["runs_not_started"] += int(result.not_started)
        if self.remaining() < 1:
            raise _BudgetStop()
        if isinstance(result, SuiteObservation):
            if result.reason:
                raise _Stop(result.reason)
            return result.outcome
        return result if result in {"passed", "failed"} else "unknown"


def _stage(budget, row, stage, operation):
    budget.check()
    row["stage"] = stage
    entry = row["stages"].setdefault(stage, {"seconds": 0.0, "runs": 0})
    start = budget.clock()
    try:
        result = operation()
        if budget.remaining() < 1:
            raise _BudgetStop()
        return result
    finally:
        entry["seconds"] += max(0.0, budget.clock() - start)


def _new_row(bug, arm, variant=None):
    return {"bug_id": bug, "repository": allocation.repository_of(bug), "arm": arm, "variant": variant,
            "stage": "unrun", "reason": "ok", "label": None, "headline_stratum": None,
            "mutant_count": None, "seconds": 0.0, "runs": 0, "verdict": None, "stages": {},
            **dict.fromkeys(SUITE_COUNTERS, 0)}


def _extract(manifest, cohort, bug_id, runner, budget):
    bug = access.load_bug(manifest, cohort, bug_id)
    cache = {}

    def read(command):
        budget.check()
        key = tuple(command)
        if key not in cache:
            if runner is preflight.docker_runner:
                try:
                    cache[key] = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                                timeout=min(120, budget.remaining()), check=False)
                except subprocess.TimeoutExpired:
                    if budget.remaining() < 1:
                        raise _BudgetStop() from None
                    raise _Stop("extraction_failed") from None
            else:
                cache[key] = runner(command)
        result = cache[key]
        if result.returncode and _material.EXTRACT in command:
            lines = (result.stderr or "").splitlines()
            last = lines[-1].strip() if lines else ""
            reason = EXTRACT_MESSAGES.get(last, "extraction_failed")
            if last.startswith("UnicodeDecodeError: 'utf-8' codec can't decode"):
                reason = "source_not_utf8"
            if reason != "support_source_too_large":
                budget.check()
            raise _Stop(reason)
        budget.check()
        return result

    try:
        material = _material.extract(bug, read, include_support=True)
    except _material.MaterialError as exc:
        raise _Stop(str(exc) if str(exc) in EXTRACT_REASONS else "extraction_failed") from None
    return material, read


def _shape(manifest, cohort, bug_id, arm, material, runner):
    if arm == "as_built":
        try:
            return convert.inspect_bug(manifest, cohort, bug_id, runner=runner)
        except ValueError as exc:
            raise _Stop(str(exc) if str(exc) in SHAPE_REASONS else "case_record_invalid") from None
    targets = _material.targets(material)
    if not targets:
        raise _Stop("no_python_fix")
    if arm == "fixed_tests" and len(targets) != 1:
        raise _Stop("multiple_python_targets")
    if arm == "multi_target" and len(targets) < 2:
        raise _Stop("multi_target_required")
    # Rank by additions plus deletions; the mutation domain remains fixed-side changed lines.
    target = min(targets, key=lambda p: (-_changed_size(material, p), p))
    lines = _material.changed_lines(material, target)
    if not lines:
        raise _Stop("empty_changed_lines")
    if target not in material["base"] or target not in material["fixed"]:
        raise _Stop("missing_target_pair")
    tests = [p for p in _material.changed_files(material) if is_test(p)]
    if len(tests) != 1:
        raise _Stop("single_test_module_required")
    test = tests[0]
    if test not in material["base"] or test not in material["fixed"]:
        raise _Stop("missing_test_pair")
    try:
        return CaseRecord(unit_id=bug_id,
                          provenance=Provenance(source="maintainer", cohort="development",
                                                source_ref="pilot-acquisition", source_sha256=digest(material)),
                          files={p: s for p, s in material["fixed"].items() if not is_test(p)}, target=target,
                          changed_lines=lines, before_tests=material["base"][test],
                          after_tests=material["fixed"][test], known_wrong=(material["base"][target],))
    except ValueError:
        raise _Stop("case_record_invalid") from None


def _changed_size(material, target):
    _material.changed_lines(material, target)
    active, left, right, count = False, 0, 0, 0
    for line in material["patch"].splitlines():
        if left or right:
            if line.startswith("+"):
                count += int(active)
                right -= 1
            elif line.startswith("-"):
                count += int(active)
                left -= 1
            elif line.startswith(" "):
                left -= 1
                right -= 1
        elif line.startswith("+++ "):
            active = line[4:] == f"b/{target}"
        elif line.startswith("@@ "):
            match = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
            left = int(match[1]) if match[1] is not None else 1
            right = int(match[2]) if match[2] is not None else 1
    return count


def _variant(case, arm, variant):
    if arm == "as_built":
        return case
    try:
        after = case.before_tests if variant == "reversed_maintainer" else (
            operators.apply_operator(case.after_tests, variant))
    except ValueError:
        raise _Stop("operator_not_applicable") from None
    return CaseRecord.model_validate({**case.model_dump(), "before_tests": case.after_tests, "after_tests": after})


def _classify(case, result, policy, run, workspace):
    """Bind production's headline rule and its local oracle import to the same budget."""
    native_import = builtins.__import__
    proxy = SimpleNamespace(_run=run, OraclePolicy=oracle.OraclePolicy)

    def imports(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "held_out" and level == 1 and fromlist == ("oracle",):
            return SimpleNamespace(oracle=proxy)
        return native_import(name, globals, locals, fromlist, level)

    negative = _bind(production._negative_justification,
                     __builtins__={**vars(builtins), "__import__": imports},
                     state_path=lambda *parts: workspace)
    headline = _bind(_headline, _negative_justification=negative)
    observation = SimpleNamespace(case=case, label=result.label, oracle_result=result.model_dump(mode="json"))
    _, strata = headline((observation,), policy)
    return next(key for key, count in strata.items() if count)


def _sample(case, policy, budget):
    try:
        sample = mutation_check.mutants(case.files[case.target], limit=policy.max_mutants,
                                        lines=case.changed_lines,
                                        deadline=budget.clock() + min(policy.wall_s, budget.remaining()))
        return len({digest(m.source) for m in sample if m.source != case.files[case.target]})
    except mutation_check.Unfinished:
        if budget.remaining() < 1:
            raise _BudgetStop() from None
        raise _Stop("mutation_sample_unfinished") from None


def _label(case, policy, run, workspace, budget):
    def mutants(*args, deadline, **kwargs):
        budget.check()
        return mutation_check.mutants(*args, deadline=min(deadline, budget.clock() + budget.remaining()), **kwargs)

    mutation = SimpleNamespace(mutants=mutants, Unfinished=mutation_check.Unfinished)
    label = _bind(oracle.label, _run=run, mutation_check=mutation)
    return label(case, policy=policy, workspace=workspace)


def _summarize(rows):
    return {"rows": len(rows), "bugs": len({r["bug_id"] for r in rows}),
            "stages": dict(Counter(stage for r in rows for stage in r["stages"])),
            "bugs_per_stage": {stage: len({r["bug_id"] for r in rows if stage in r["stages"]})
                               for stage in STAGES},
            "reasons": dict(Counter(r["reason"] for r in rows)),
            "strata": dict(Counter(r["headline_stratum"] for r in rows if r["headline_stratum"])),
            "labels": dict(Counter(r["label"] for r in rows if r["label"])),
            "seconds_per_label": {label: sum(r["seconds"] for r in rows if r["label"] == label)
                                  for label in ("positive", "negative")},
            "runs": sum(r["runs"] for r in rows), "seconds": sum(r["seconds"] for r in rows),
            **{key: sum(r[key] for r in rows) for key in SUITE_COUNTERS}}


def _output_path(output):
    output = Path(output).absolute()
    if output.resolve().is_relative_to(REPOSITORY.resolve()):
        raise ValueError("output_inside_repository")
    if any(p.is_symlink() or p.is_junction() for p in (output, *output.parents)):
        raise ValueError("linked_output_refused")
    return output


def _write(output, report):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output.parent,
                                         prefix=".funnel-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_funnel(manifest, cohort, output, *, arms=ARMS, bugs=None, runner=preflight.docker_runner,
               suite_runner=suite_observation, clock=time.monotonic):
    """Run only allocation-selected acquisition rows and publish scalar observations atomically."""
    budget = _Budget(clock)
    ids = access.selected(manifest, bugs)
    cohort = Path(cohort)
    if any(p.name.lower().startswith("final-") for p in (cohort, *cohort.parents)):
        raise ValueError("the final cohort is sealed; only the owner opens it")
    output = _output_path(output)
    if not output.parent.is_dir():
        raise ValueError("output_parent_required")
    arms = tuple(arms)
    if not arms or len(set(arms)) != len(arms) or set(arms) - set(ARMS):
        raise ValueError("unknown_or_duplicate_arm")
    arms = tuple(a for a in ARMS if a in arms)
    rows = []
    workspace = output.parent

    for bug_id in ids:
        for arm in arms:
            row = _new_row(bug_id, arm)
            budget.row = row
            try:
                material, extraction_runner = _stage(budget, row, "extraction", lambda: _extract(
                    manifest, cohort, bug_id, runner, budget))
                case = _stage(budget, row, "shape", lambda: _shape(
                    manifest, cohort, bug_id, arm, material, extraction_runner))

                def isolation():
                    if not confine.backend(sys.executable)[0]:
                        raise _Stop(ISOLATION_UNAVAILABLE)

                _stage(budget, row, "isolation", isolation)
                policy = oracle.OraclePolicy() if arm == "as_built" else oracle.OraclePolicy(min_mutants=1)

                def run(case, implementation, tests, *, workspace=workspace, timeout_s=policy.per_run_s,
                        python=None):
                    return budget.suite(suite_runner, case, implementation, tests, workspace=workspace,
                                        timeout_s=timeout_s, python=python)

                def fixed_suites():
                    for tests in (case.before_tests, case.after_tests):
                        if run(case, case.files[case.target], tests) != "passed":
                            raise _Stop("fixed_suite_did_not_pass")

                _stage(budget, row, "fixed_suites", fixed_suites)

                def control():
                    outcome = run(case, case.known_wrong[0], case.after_tests)
                    if outcome != "failed":
                        raise _Stop("control_base_passes" if outcome == "passed" else "control_unsettled")

                _stage(budget, row, "control", control)

                def mutants():
                    row["mutant_count"] = _sample(case, policy, budget)
                    if row["mutant_count"] < policy.min_mutants:
                        raise _Stop("insufficient_mutant_sample")

                _stage(budget, row, "mutant_count", mutants)
            except (_Stop, repo_tests.RepoRefused, OSError, ValueError, KeyError, TypeError) as exc:
                row["reason"] = _reason(exc)
                row["seconds"] = sum(v["seconds"] for v in row["stages"].values())
                rows.append(row)
                continue

            # Common prechecks are charged once, to the first variant row of this bug/arm.
            for number, variant in enumerate(("maintainer",) if arm == "as_built" else VARIANTS):
                if number:
                    row = _new_row(bug_id, arm, variant)
                    row["mutant_count"] = rows[-1]["mutant_count"]
                row["variant"] = variant
                budget.row = row
                try:
                    budget.check()
                    variant_case = _stage(budget, row, "shape", lambda: _variant(case, arm, variant))
                    result = _stage(budget, row, "settle", lambda: _label(
                        variant_case, policy, run, workspace, budget))
                    if result.label == "excluded":
                        raise _Stop(result.reason)
                    _stage(budget, row, "label", lambda: None)
                    stratum = _stage(budget, row, "headline", lambda: _classify(
                        variant_case, result, policy, run, workspace))
                    row.update(label=result.label, reason=result.reason, headline_stratum=stratum)
                except (_Stop, repo_tests.RepoRefused, OSError, ValueError, KeyError, TypeError) as exc:
                    row["reason"] = _reason(exc)
                row["seconds"] = sum(v["seconds"] for v in row["stages"].values())
                rows.append(row)
    seconds = max(0.0, clock() - budget.started)
    if not math.isfinite(seconds):
        raise ValueError("invalid_clock")
    report = {"arms": {a: _summarize([r for r in rows if r["arm"] == a]) for a in arms}, "bugs": rows,
              "repositories": {repo: {a: _summarize([r for r in rows if r["repository"] == repo and r["arm"] == a])
                                       for a in arms} for repo in sorted({r["repository"] for r in rows})},
              "total_runs": budget.runs, "total_seconds": seconds,
              **{"total_" + key: sum(r[key] for r in rows) for key in SUITE_COUNTERS},
              "run_count_scope": "confined-route requests; refusals charged conservatively",
              "caps": {"runs": MAX_RUNS, "seconds": MAX_SECONDS},
              "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    _write(output, report)
    return report


def _reason(exc):
    if isinstance(exc, _Stop):
        return exc.reason
    if isinstance(exc, repo_tests.RepoRefused):
        return ISOLATION_UNAVAILABLE if exc.code == ISOLATION_UNAVAILABLE else "suite_runner_refused"
    if isinstance(exc, OSError):
        return "io_unavailable"
    return "invalid_material"


def main(argv=None, *, runner=preflight.docker_runner, suite_runner=suite_observation):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", choices=(*ARMS, "all"), action="append")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        arms = ARMS if not args.arm or args.arm == ["all"] else args.arm
        run_funnel(manifest, args.cohort, args.output, arms=arms, runner=runner, suite_runner=suite_runner)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "funnel_input_refused\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
