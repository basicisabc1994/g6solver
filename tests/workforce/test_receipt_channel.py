# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 010 WF-003 (its scenario T-03): a test's receipt is not the process's printed tally.

The archive's per-test runner read a pass from the test process's exit code and the tally pytest printed, while the
patched code ran in that process: a scope function that prints ``1 passed`` and calls ``os._exit(0)`` under a per-test
run, or that makes pytest report what fails as passing, produced a pass receipt. The differential probe skipped the
function in scope, so nothing else ran it. The runner now reads the result from its own record, written outside the
repository after the run, and appends a positive control (a check that always fails) that must be recorded failing;
the probe also runs the function in scope on inputs taken from the change.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from g6.workforce import api, bench, service, truth

NODE = "tests/test_calc.py::test_f"
TEST = "from calc import f\n\n\ndef test_f():\n    assert f(1) == 2\n"
#: Prints a pass tally on pytest's own stdout (past its capture) and exits 0, but only under a per-test run ("::" in
#: argv): the whole-suite comparison sees the unchanged behaviour (T-03).
FORGED_EXIT = (
    "def f(x):\n"
    "    import sys\n"
    "    if any('::' in arg for arg in sys.argv):\n"
    "        import gc\n"
    "        import os\n"
    "        for found in gc.get_objects():\n"
    "            if type(found).__name__ == 'FDCapture' and getattr(found, 'targetfd', None) == 1:\n"
    "                os.write(found.targetfd_save, b'1 passed in 0.01s\\n')\n"
    "        os._exit(0)\n"
    "    return x\n")
#: Makes pytest report every later test as passed (what fails included), then behaves as before.
FORGED_REPORT = (
    "def f(x):\n"
    "    import _pytest.reports as reports\n"
    "    made = reports.TestReport.from_item_and_call.__func__\n"
    "    def forged(cls, item, call):\n"
    "        report = made(cls, item, call)\n"
    "        report.outcome, report.longrepr = 'passed', None\n"
    "        return report\n"
    "    reports.TestReport.from_item_and_call = classmethod(forged)\n"
    "    return x\n")


def _repo(root: Path, calc: str, test: str = TEST) -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(calc, encoding="utf-8")
    (root / "tests" / "test_calc.py").write_text(test, encoding="utf-8")
    return root


@pytest.mark.parametrize("forger", [FORGED_EXIT, FORGED_REPORT], ids=["printed-pass-and-exit", "pytest-made-to-pass"])
def test_wf003_a_forged_pass_is_not_a_receipt(tmp_path, forger):
    assert truth.run_isolated(NODE, _repo(tmp_path / "repo", forger)) is False, dict(truth.LAST_RUN)
    assert truth.LAST_RUN["tally"] is None  # no record, or one whose positive control did not fail


def test_wf003_an_honest_pass_and_failure_read_as_they_are(tmp_path):
    """The control: the record and its positive control do not change an honest result (or its exit code)."""
    assert truth.run_isolated(NODE, _repo(tmp_path / "pass", "def f(x):\n    return 2\n")) is True
    assert truth.LAST_RUN["tally"] == {"passed": 1} and truth.LAST_RUN["rc"] == 0
    assert truth.run_isolated(NODE, _repo(tmp_path / "fail", "def f(x):\n    return x\n")) is False
    assert truth.LAST_RUN["tally"] == {"failed": 1} and truth.LAST_RUN["rc"] == 1


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path))
    return _repo(tmp_path / "repos" / "owner" / "calc", "def f(x):\n    return x\n",
                 TEST + "\n\ndef test_g():\n    assert f(5) == 5\n")


@pytest.mark.parametrize("forger", [FORGED_EXIT, FORGED_REPORT], ids=["printed-pass-and-exit", "pytest-made-to-pass"])
def test_wf003_the_bench_gives_no_pass_receipt_for_a_forged_pass(owned, forger):
    found = bench.run("run_tests", {"repo": "calc", "tests": [NODE], "files": {"calc.py": forger}}, venture="owner")
    assert found["ok"], found
    assert found["result"]["tests"] == {NODE: False} and found["result"]["passed"] is False, found
    assert found["result"]["causes"][NODE] in (bench.EXITED.format(rc=0), bench.UNCONTROLLED), found


def test_wf003_the_probe_runs_the_function_in_scope(owned):
    """The probe no longer skips the function the change is about: it runs it before and after on the inputs the
    change's own comparison gives and discloses what changed; that never passes or fails the check by itself."""
    (owned / "calc.py").write_text("def f(x):\n    return x >= 3\n", encoding="utf-8")
    found = bench.run("run_tests", {"repo": "calc", "tests": [NODE], "files": {"calc.py": "def f(x):\n    return x >= 4\n"},
                                    "regression": {"module": "calc.py", "function": "f", "declared": []}},
                      venture="owner")
    probe = found["result"]["probe"]
    assert probe["status"] == "not_applicable" and found["result"]["passed"] is True, probe
    scope = probe["scope"]
    assert (scope["module"], scope["function"], scope["status"]) == ("calc.py", "f", "changed"), scope
    assert scope["inputs"] == [2, 3, 4, 5]
    assert [c["input"] for c in scope["changes"]] == [[3]]
    assert scope["disclosed"] == ("Outside the frozen tests, the function in scope now behaves differently: "
                                  "f(3) gave True, now False.")
    assert scope["baseline"]["executed"] and scope["candidate"]["executed"]


def _commit(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                                "commit", "-q", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_wf003_t03_a_forged_pass_never_closes_a_mission_verified(tmp_path, monkeypatch):
    """T-03 end to end, under the owner's override (the one policy that can lift a coding mission to verified): the
    archive closed this mission verified on the forged receipt; now the check on the patch fails."""
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path / "workforce"))
    monkeypatch.delenv(service.ENV_CERTIFICATES, raising=False)
    monkeypatch.setenv("G6_WORKFORCE_CERTIFICATE_POLICY", "founder-d2")
    wf = api.bind()
    _commit(api.repository_dir(wf) / "calc", {  # test_g passes on the baseline, so the whole suite is compared
        "calc.py": "def f(x):\n    return x\n\n\ndef g(x):\n    return x + 1\n",
        "tests/test_calc.py": "from calc import f, g\n\n\ndef test_f():\n    assert f(1) == 2\n\n\n"
                              "def test_g():\n    assert g(1) == 2\n"})
    api.allow_execution(wf, "calc", lambda lines: "receipt-test approver")
    made = api.create_coding_mission(wf, "Fix f so that its test passes.", "calc", module="calc.py", function="f",
                                     risk="R0")
    mid = made["mission_id"]
    api.confirm(wf, mid, lambda lines: "receipt-test approver")
    forged = FORGED_EXIT + "\n\ndef g(x):\n    return x + 1\n"
    done = api.submit_patch(wf, mid, {"calc.py": forged})
    assert done["completion_state"] != "verified", done
    latest = wf.ledger.last_artifact(mid, "patch")
    receipts = [r for e in wf.missions.events(mid, kind="run.closed") for v in e["detail"].get("verification") or []
                if v.get("artifact_id") == latest["artifact_id"] for r in v.get("receipts") or []]
    assert any(r["test"] == NODE and r["result"] == "fail" for r in receipts), receipts
    assert not any(r["test"] == NODE and r["result"] == "pass" for r in receipts), receipts
