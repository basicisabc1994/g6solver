"""Fake-only checks for the acquisition funnel."""
from __future__ import annotations

import difflib
import importlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from g6.distill.held_out import oracle
from g6.workforce import mutation_check, repo_tests
from validation.distill.pilot import allocation

FIXED = "def answer(x):\n    return x + 1  # SOURCE_SENTINEL\n"
BASE = "def answer(x):\n    return x  # BASE_SENTINEL\n"
OLD = "from subject import answer\ndef test_answer():\n    assert answer(1) >= 1  # OLD_SENTINEL\n"
NEW = "from subject import answer\ndef test_answer():\n    assert answer(1) == 2  # TEST_SENTINEL\n    assert answer(2) == 3\n"
MUTANT = "def answer(x):\n    return 9  # MUTANT_SENTINEL\n"


def patch(path, old, new):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                     fromfile=f"a/{path}", tofile=f"b/{path}"))


class DockerFake:
    def __init__(self, material):
        self.material, self.calls, self.failure = material, [], None

    def __call__(self, command):
        self.calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            key = "b" if "task" in command[3] else "c"
            return subprocess.CompletedProcess(command, 0, "sha256:" + key * 64, "")
        assert command[1] == "run" and "support" in command
        assert command[command.index("--network") + 1] == "none"
        if self.failure:
            return subprocess.CompletedProcess(command, 1, "", self.failure)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.material), "")


@pytest.fixture
def rig(monkeypatch):
    f = importlib.import_module("validation.distill.pilot.funnel")
    folder = Path.cwd() / ".build" / ("funnel-test-" + uuid4().hex)
    folder.mkdir()
    ids = [f"repo{i}-1" for i in range(18)]
    roles = allocation.allocate("a" * 64, [allocation.repository_of(i) for i in ids])
    manifest = {"cohort": "development", "cohort_digest": "a" * 64, "label": allocation.LABEL,
                "roles": {r: {"repositories": repos, "bugs": [b for b in ids if allocation.repository_of(b) in repos]}
                          for r, repos in roles.items()}}
    body = {"cohort": "development", "digest": "a" * 64,
            "bugs": [{"id": i, "task_image": "g6we-task:" + i, "task_image_id": "sha256:" + "b" * 64,
                      "oracle_image": "g6we-oracle:" + i, "oracle_image_id": "sha256:" + "c" * 64} for i in ids]}
    cohort = folder / "development.json"
    cohort.write_text(json.dumps(body), encoding="utf-8")
    manifest_path = folder / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    material = {"base": {"subject.py": BASE, "tests/test_answer.py": OLD},
                "fixed": {"subject.py": FIXED, "tests/test_answer.py": NEW},
                "name_status": "M\tsubject.py\nM\ttests/test_answer.py\n",
                "patch": patch("subject.py", BASE, FIXED) + patch("tests/test_answer.py", OLD, NEW)}
    docker = DockerFake(material)
    calls = []
    outcomes = {}

    def suite(case, implementation, tests, **kwargs):
        calls.append((implementation, tests, kwargs))
        return outcomes.get((implementation, tests), "passed" if implementation == FIXED else "failed")

    monkeypatch.setattr(f, "REPOSITORY", folder / "repository")
    monkeypatch.setattr(f.confine, "backend", lambda *a, **kw: ("fake-backend", ""))
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: [
        mutation_check.Mutant(2, "x", "9", MUTANT)])
    bugs = manifest["roles"]["acquisition"]["bugs"]

    def run(arms=("fixed_tests",), selected=None, **kw):
        return f.run_funnel(manifest, cohort, folder / "funnel.json", arms=arms,
                            bugs=bugs[:1] if selected is None else selected,
                            runner=docker, suite_runner=kw.pop("suite_runner_override", suite), **kw)

    return SimpleNamespace(f=f, folder=folder, manifest=manifest, manifest_path=manifest_path,
                           cohort=cohort, material=material, docker=docker, suite=suite,
                           calls=calls, outcomes=outcomes, bugs=bugs, run=run)


@pytest.mark.parametrize("role", ["admission", "monitor", "reuse"])
def test_role_refused_before_content(rig, role):
    with pytest.raises(ValueError, match="role=" + role):
        rig.run(selected=rig.manifest["roles"][role]["bugs"])
    assert not rig.docker.calls and not rig.calls


def test_allocation_and_final_refused_before_content(rig):
    rig.manifest["label"] = "changed"
    with pytest.raises(ValueError, match="invalid allocation"):
        rig.run()
    rig.manifest["label"] = allocation.LABEL
    with pytest.raises(ValueError, match="sealed"):
        rig.f.run_funnel(rig.manifest, rig.folder / "final-unopened.json", rig.folder / "out.json",
                         runner=rig.docker, suite_runner=rig.suite)
    assert not rig.docker.calls and not rig.calls


def test_inside_repository_output_refused_before_content(rig, monkeypatch):
    monkeypatch.setattr(rig.f, "REPOSITORY", Path.cwd())
    with pytest.raises(ValueError, match="output_inside_repository"):
        rig.run()
    assert not rig.docker.calls and not rig.calls


@pytest.mark.parametrize("diagnostic,reason", [("ValueError: support source too large", "support_source_too_large"),
                                                ("source text must stay hidden", "extraction_failed")])
def test_extraction_first_stop(rig, diagnostic, reason):
    rig.docker.failure = diagnostic
    row = rig.run()["bugs"][0]
    assert (row["stage"], row["reason"], row["label"]) == ("extraction", reason, None)
    assert not rig.calls


@pytest.mark.parametrize("change,reason", [("multi", "multiple_python_targets"),
                                          ("no_target", "no_python_fix"),
                                          ("tests", "single_test_module_required"),
                                          ("pair", "missing_test_pair"),
                                          ("lines", "empty_changed_lines")])
def test_shape_stops_before_suites(rig, change, reason):
    if change == "multi":
        rig.material["name_status"] += "M\tsecond.py\n"
    elif change == "no_target":
        rig.material["name_status"] = "M\ttests/test_answer.py\n"
    elif change == "tests":
        rig.material["name_status"] += "M\ttests/test_second.py\n"
    elif change == "pair":
        del rig.material["base"]["tests/test_answer.py"]
    else:
        rig.material["patch"] = ""
    row = rig.run(arms=("as_built",))["bugs"][0]
    assert (row["stage"], row["reason"]) == ("shape", reason)
    assert row["label"] is None and not rig.calls


def test_backend_unavailable_stops(rig, monkeypatch):
    monkeypatch.setattr(rig.f.confine, "backend", lambda *a, **kw: (None, "private diagnostic"))
    row = rig.run()["bugs"][0]
    assert (row["stage"], row["reason"]) == ("isolation", "G6_E_ISOLATION_UNAVAILABLE")
    assert not rig.calls


@pytest.mark.parametrize("suite", [OLD, NEW])
def test_fixed_must_pass_both_maintainer_suites(rig, suite):
    rig.outcomes[FIXED, suite] = "failed"
    row = rig.run()["bugs"][0]
    assert (row["stage"], row["reason"]) == ("fixed_suites", "fixed_suite_did_not_pass")
    assert row["label"] is None


def test_base_passing_fixed_tests_invalidates_control(rig):
    rig.outcomes[BASE, NEW] = "passed"
    row = rig.run()["bugs"][0]
    assert (row["stage"], row["reason"]) == ("control", "control_base_passes")
    assert len(rig.calls) == 3 and row["label"] is None


def test_mutant_minimum_is_after_control_and_defaults_stay_intact(rig):
    row = rig.run(arms=("as_built",))["bugs"][0]
    assert (row["stage"], row["reason"], row["mutant_count"]) == (
        "mutant_count", "insufficient_mutant_sample", 1)
    assert len(rig.calls) == 3 and row["label"] is None


def test_unsettled_mutant_never_gets_label(rig):
    rig.outcomes[MUTANT, NEW] = "unknown"
    rows = rig.run()["bugs"]
    assert rows[0]["stage"] == "settle" and rows[0]["reason"] == "mutant_unsettled"
    assert rows[0]["label"] is None


def test_variants_and_production_headline_and_no_content(rig, capsys):
    snapshot = oracle.OraclePolicy().model_dump()
    original_run = oracle._run
    report = rig.run()
    rows = report["bugs"]
    assert len(rows) == 8
    assert {r["variant"] for r in rows} == {"erase_assertion", "assert_true", "loosen_equality", "rename_test",
                                             "extract_helper", "named_constant", "split_test", "reversed_maintainer"}
    assert all(r["label"] == "negative" and r["headline_stratum"] in {"n1", "n2"} for r in rows)
    assert report["total_runs"] == len(rig.calls)
    assert sum(r["runs"] for r in rows) == report["total_runs"]
    assert all(sum(v["runs"] for v in r["stages"].values()) == r["runs"] for r in rows)
    assert oracle.OraclePolicy().model_dump() == snapshot and oracle._run is original_run
    assert report["code_sha256"] and report["repositories"] and report["arms"]["fixed_tests"]["strata"]
    serialized = (rig.folder / "funnel.json").read_text(encoding="utf-8") + capsys.readouterr().out
    for secret in (BASE, FIXED, OLD, NEW, MUTANT, "SOURCE_SENTINEL", "TEST_SENTINEL"):
        assert secret not in serialized
    protected = ["src/g6/distill/held_out", "src/g6/distill/operators.py", "src/g6/distill/records.py",
                 "src/g6/distill/acceptance_gate.py", "src/g6/distill/rectify_gate.py",
                 "src/g6/harness/library_review_gate.py", "src/g6/mcp/distill.py",
                 "validation/distill/held_out", "validation/distill/pilot/allocation.json"]
    diff = subprocess.run(["git", "diff", "HEAD", "--", *protected], capture_output=True, check=True)
    assert diff.stdout == b""


def test_multi_target_arm_uses_largest_changed_file_ties_by_path(rig):
    rig.material["base"]["aaa.py"] = BASE
    rig.material["fixed"]["aaa.py"] = FIXED
    rig.material["name_status"] += "M\taaa.py\n"
    rig.material["patch"] += patch("aaa.py", BASE, FIXED)
    targets = []

    def suite(case, implementation, tests, **kwargs):
        targets.append(case.target)
        return rig.suite(case, implementation, tests, **kwargs)

    report = rig.run(arms=("multi_target",), suite_runner_override=suite)
    assert set(targets) == {"aaa.py"}
    assert len(report["bugs"]) == 8
    assert oracle.OraclePolicy().min_mutants == 30


def test_multi_target_arm_only_applies_to_multi_target(rig):
    row = rig.run(arms=("multi_target",))["bugs"][0]
    assert row["reason"] == "multi_target_required" and not rig.calls


@pytest.mark.parametrize("cap", [0, 3, 10])
def test_global_run_cap_stops_every_arm_and_remaining_bug(rig, monkeypatch, cap):
    monkeypatch.setattr(rig.f, "MAX_RUNS", cap)
    report = rig.run(arms=("as_built", "fixed_tests", "multi_target"), selected=rig.bugs[:2])
    assert len(rig.calls) == report["total_runs"] == cap
    unrun = [r for r in report["bugs"] if r["reason"] == "unrun_budget"]
    assert unrun and unrun[-1]["bug_id"] == rig.bugs[1]
    assert all(r["label"] is None for r in unrun)


def test_wall_cap_stops_before_extraction(rig, monkeypatch):
    monkeypatch.setattr(rig.f, "MAX_SECONDS", 0)
    report = rig.run(selected=rig.bugs)
    assert all(r["reason"] == "unrun_budget" for r in report["bugs"])
    assert not rig.calls and not rig.docker.calls


def test_cli_writes_only_requested_output(rig):
    status = rig.f.main(["--manifest", str(rig.manifest_path), "--cohort", str(rig.cohort),
                         "--output", str(rig.folder / "cli.json"), "--arm", "as_built"],
                        runner=rig.docker, suite_runner=rig.suite)
    assert status == 0
    body = json.loads((rig.folder / "cli.json").read_text(encoding="utf-8"))
    assert len(body["bugs"]) == 6 and body["total_runs"] == 18


@pytest.mark.parametrize("outcome,diagnostic,want", [("passed", "", "passed"),
    ("unknown", "missing_module", "missing_module"), ("unknown", "source_loading_failure", "source_loading_failure"),
    ("unknown", "no_tests_collected", "no_tests_collected")])
def test_oracle_route_uses_established_not_exit_codes(rig, monkeypatch, outcome, diagnostic, want):
    seen = []

    @contextmanager
    def folder(**kwargs):
        stage = rig.folder / uuid4().hex
        stage.mkdir()
        yield str(stage)

    def run(repo, tests, **kwargs):
        assert kwargs["require_isolation"] is True and kwargs["network"] is False
        seen.append((repo / "subject.py").read_text(encoding="utf-8"))
        return {"returncode": 99 if outcome == "passed" else 0, "isolation": {"backend": "fake"},
                "missing_modules": ["hidden_name"] if diagnostic == "missing_module" else [],
                "verdict": "no_tests_collected" if diagnostic == "no_tests_collected" else "passed"}

    def established(raw, **kwargs):
        assert kwargs["sources"] == ("subject.py",)
        return SimpleNamespace(of=lambda test: outcome,
                               withheld="loading_unknown" if diagnostic == "source_loading_failure" else "")

    monkeypatch.setattr(rig.f.confine, "run_folder", folder)
    monkeypatch.setattr(repo_tests, "_run", run)
    monkeypatch.setattr(repo_tests, "established", established)
    case = rig.f.convert.inspect_bug(rig.manifest, rig.cohort, rig.bugs[0], runner=rig.docker)
    result = rig.f.suite_observation(case, FIXED, NEW, workspace=rig.folder, timeout_s=1)
    assert (result.reason or result.outcome) == want
    assert seen == [FIXED]

def test_funnel_module_exists():
    assert importlib.util.find_spec("validation.distill.pilot.funnel") is not None


def test_multi_target_rank_counts_deletions_as_changed_lines(rig):
    old = "def other():\n    x = 1\n    y = 2\n    z = 3\n    return x + y + z\n"
    new = "def other():\n    return 6\n"
    rig.material["base"]["zzz.py"] = old
    rig.material["fixed"]["zzz.py"] = new
    rig.material["name_status"] += "M\tzzz.py\n"
    rig.material["patch"] += patch("zzz.py", old, new)
    targets = []

    def suite(case, implementation, tests, **kwargs):
        targets.append(case.target)
        return "passed" if implementation == new else "failed"

    rig.run(arms=("multi_target",), suite_runner_override=suite)
    assert set(targets) == {"zzz.py"}


def test_positive_witness_and_production_stratum(rig):
    def suite(case, implementation, tests, **kwargs):
        if implementation == FIXED:
            return "passed"
        if tests in (OLD, NEW):
            return "failed"
        if "assert True" in tests or "pass" in tests:
            return "passed"
        return "failed"

    report = rig.run(suite_runner_override=suite)
    row = next(r for r in report["bugs"] if r["variant"] == "erase_assertion")
    assert (row["label"], row["headline_stratum"], row["reason"]) == (
        "positive", "witness_positive", "known_wrong_witness")


def test_unknown_control_stops_before_mutation(rig, monkeypatch):
    rig.outcomes[BASE, NEW] = "unknown"
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: pytest.fail("mutation after failed control"))
    row = rig.run()["bugs"][0]
    assert (row["stage"], row["reason"]) == ("control", "control_unsettled")


def test_mutation_timeout_has_own_reason(rig, monkeypatch):
    def unfinished(*a, **kwargs):
        raise mutation_check.Unfinished()

    monkeypatch.setattr(mutation_check, "mutants", unfinished)
    row = rig.run()["bugs"][0]
    assert (row["stage"], row["reason"]) == ("mutant_count", "mutation_sample_unfinished")
    assert row["mutant_count"] is None and row["label"] is None


def test_budget_covers_headline_runs(rig, monkeypatch):
    monkeypatch.setattr(rig.f, "MAX_RUNS", 10)
    report = rig.run()
    first = report["bugs"][0]
    assert first["stage"] == "headline" and first["reason"] == "unrun_budget"
    assert first["stages"]["headline"]["runs"] == 1
    assert first["label"] is None and len(rig.calls) == 10
    assert all(r["reason"] == "unrun_budget" for r in report["bugs"])


def test_wall_cap_clamps_run_timeout_and_latches(rig, monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(rig.f, "MAX_SECONDS", 5)

    def suite(case, implementation, tests, **kwargs):
        assert kwargs["timeout_s"] <= 5
        elapsed[0] = 6.0
        return "passed"

    report = rig.run(clock=lambda: elapsed[0], suite_runner_override=suite, selected=rig.bugs)
    assert report["total_runs"] == 1
    assert report["total_seconds"] == 6.0
    assert all(r["reason"] == "unrun_budget" for r in report["bugs"])


def test_failed_atomic_replace_preserves_previous_report(rig, monkeypatch):
    target = rig.folder / "funnel.json"
    target.write_text("prior report", encoding="utf-8")

    def fail(*args):
        raise OSError("replacement failure")

    monkeypatch.setattr(rig.f.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failure"):
        rig.run(arms=("as_built",))
    assert target.read_text(encoding="utf-8") == "prior report"
    assert not list(rig.folder.glob(".funnel-*.tmp"))


def test_unsupported_operator_is_counted_without_suites_for_that_variant(rig):
    rig.material["fixed"]["tests/test_answer.py"] = NEW.replace("def test_answer():", "def test_answer(fixture):")
    report = rig.run()
    row = next(r for r in report["bugs"] if r["variant"] == "rename_test")
    assert row["reason"] == "operator_not_applicable" and row["runs"] == 0
    assert row["stage"] == "shape"


def test_cli_refusal_does_not_echo_payload(rig, monkeypatch, capsys):
    monkeypatch.setattr(rig.f.access, "selected", lambda *a, **kw: (_ for _ in ()).throw(ValueError(NEW)))
    with pytest.raises(SystemExit) as caught:
        rig.f.main(["--manifest", str(rig.manifest_path), "--cohort", str(rig.cohort),
                    "--output", str(rig.folder / "out.json")], runner=rig.docker, suite_runner=rig.suite)
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err == "funnel_input_refused\n"

def test_as_built_uses_default_policy_through_label_and_headline(rig, monkeypatch):
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: [
        mutation_check.Mutant(2, "x", str(n), MUTANT + f"# {n}\n") for n in range(30)])
    report = rig.run(arms=("as_built",))
    row = report["bugs"][0]
    assert row["mutant_count"] == 30 and row["label"] == "negative"
    assert row["headline_stratum"] == "n2" and row["variant"] == "maintainer"
    assert oracle.OraclePolicy().model_dump() == {"min_mutants": 30, "max_mutants": 128,
                                                 "per_run_s": 20, "wall_s": 600}


def test_zero_mutants_stop_pilot_policy(rig, monkeypatch):
    monkeypatch.setattr(mutation_check, "mutants", lambda *a, **kw: [])
    row = rig.run()["bugs"][0]
    assert (row["reason"], row["mutant_count"], row["label"]) == ("insufficient_mutant_sample", 0, None)


def test_oracle_route_rejects_missing_boundary(rig, monkeypatch):
    @contextmanager
    def folder(**kwargs):
        stage = rig.folder / uuid4().hex
        stage.mkdir()
        yield str(stage)

    monkeypatch.setattr(rig.f.confine, "run_folder", folder)
    monkeypatch.setattr(repo_tests, "_run", lambda *a, **kw: {"isolation": {}})
    monkeypatch.setattr(repo_tests, "established", lambda *a, **kw: pytest.fail("unconfined evidence used"))
    case = rig.f.convert.inspect_bug(rig.manifest, rig.cohort, rig.bugs[0], runner=rig.docker)
    with pytest.raises(repo_tests.RepoRefused) as caught:
        rig.f.suite_observation(case, FIXED, NEW, workspace=rig.folder, timeout_s=1)
    assert caught.value.code == "G6_E_ISOLATION_UNAVAILABLE"


def test_suite_refusal_is_sanitized_and_not_retried(rig):
    attempts = []

    def refused(*a, **kw):
        attempts.append(1)
        raise repo_tests.RepoRefused("G6_E_ISOLATION_UNAVAILABLE", NEW)

    row = rig.run(suite_runner_override=refused)["bugs"][0]
    assert row["reason"] == "G6_E_ISOLATION_UNAVAILABLE"
    assert len(attempts) == 1 and row["label"] is None


def test_linked_output_is_refused_before_content(rig, monkeypatch):
    target = rig.folder / "funnel.json"
    native = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == target or native(p))
    with pytest.raises(ValueError, match="linked_output_refused"):
        rig.run()
    assert not rig.docker.calls and not rig.calls

def test_oracle_resampling_obeys_remaining_wall_budget(rig, monkeypatch):
    elapsed = [0.0]
    deadlines = []
    monkeypatch.setattr(rig.f, "MAX_SECONDS", 5)

    def mutants(*a, **kwargs):
        deadlines.append(kwargs["deadline"])
        if len(deadlines) == 2:
            elapsed[0] = 6.0
            raise mutation_check.Unfinished()
        return [mutation_check.Mutant(2, "x", "9", MUTANT)]

    monkeypatch.setattr(mutation_check, "mutants", mutants)
    report = rig.run(clock=lambda: elapsed[0])
    assert len(deadlines) == 2 and all(d <= 5.0 for d in deadlines)
    assert report["bugs"][0]["reason"] == "unrun_budget"
    assert all(r["label"] is None for r in report["bugs"])

@pytest.mark.parametrize("reason", ["missing_module", "source_loading_failure", "no_tests_collected"])
def test_runner_diagnostic_stops_fixed_stage_without_content(rig, reason):
    def suite(*a, **kw):
        return rig.f.SuiteObservation("unknown", reason)

    row = rig.run(suite_runner_override=suite)["bugs"][0]
    assert (row["stage"], row["reason"], row["label"], row["runs"]) == ("fixed_suites", reason, None, 1)
