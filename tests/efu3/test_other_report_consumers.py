"""The report decision also governs adapters outside the phase readers."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.algorithm_design.autonomous import RepoTestsRunner
from g6.bundles import agent_change
from g6.metaharness import coding
from g6.workforce import bench, repo_tests as rt
from tests.harness.efu2_cases import FAILED, NODE, PASSED, report


@pytest.mark.parametrize("raw", [
    report(FAILED, verdict="passed"), report(PASSED, verdict="failed"),
    report(PASSED, loaded_complete=True),
])
def test_oracle_cannot_support_a_report_the_reader_did_not_accept(raw):
    oracle = coding.coding_oracle(run=lambda *a, **kw: raw)
    assert oracle(str(Path.cwd()), {"tests": ["test_calc.py"]}) is False


def test_mutation_adapter_cannot_turn_an_unknown_report_into_a_kill(monkeypatch, tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    monkeypatch.setattr(rt, "change", lambda *a: SimpleNamespace(files=("calc.py",)))
    assert coding._mutants_killed(tmp_path, ["test_calc.py"], timeout_s=30,
                                 run=lambda *a, **kw: report(PASSED, verdict="failed")) is False


@pytest.mark.parametrize("raw", [report(PASSED, verdict="failed"), report(FAILED, verdict="passed"),
                                 report({"setup": "failed", "teardown": "passed"})])
def test_bundle_mutation_adapter_requires_a_positive_body_result(raw):
    assert agent_change._as_run(raw).execution_id == ""


def test_algorithm_adapter_does_not_mark_an_unknown_report_completed(monkeypatch):
    runner = object.__new__(RepoTestsRunner)
    runner._checker, runner._sources = "checker", {}
    runner.mission = SimpleNamespace(tests=(NODE,))
    monkeypatch.setattr(runner, "_checker_digest", lambda: runner._checker)
    monkeypatch.setattr(runner, "_source_digests", lambda: runner._sources)
    got = runner._observed(report(FAILED, verdict="passed", degraded=False), "source")
    assert got.completed is False


def test_bench_cannot_mark_a_rejected_report_passed(monkeypatch):
    from contextlib import nullcontext
    raw = report(FAILED, verdict="passed", passed=1, failed=0, total=1,
                 not_passed=0, seconds=0.0, interpreter_source="recorded")
    monkeypatch.setattr(bench, "_repo", lambda *a: Path.cwd())
    monkeypatch.setattr(bench.confine, "run_folder", lambda **kw: nullcontext(str(Path.cwd())))
    monkeypatch.setattr(rt, "run", lambda *a, **kw: raw)
    assert bench._in_place({"repo": "recorded", "tests": ["test_calc.py"]}, None)["result"]["passed"] is False


def test_an_oracle_needs_a_body_match_for_the_requested_scope():
    oracle = coding.coding_oracle(run=lambda *a, **kw: report(PASSED))
    assert oracle(str(Path.cwd()), {"tests": ["test_other.py"]}) is False


def test_every_scope_result_is_decided_from_the_accepted_phase_records():
    for phases, expected in ((PASSED, "passed"), (FAILED, "failed")):
        decided = rt.read_report(report(phases))
        for scope in (NODE, "test_calc.py"):
            assert decided.body_result(scope) == expected
        assert decided.body_result("test_other.py") == "unknown"
    assert rt.read_report(report(FAILED, verdict="passed")).body_result(NODE) == "unknown"
