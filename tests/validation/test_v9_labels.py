# SPDX-License-Identifier: Apache-2.0
"""V9's trees, test executor and label contract runner (PROTOCOL.md, "Two contracts", "Ground truth and its error").

Development fixtures only: every repository, patch and hidden test here is a tiny one made in a temporary folder for
these tests; none is V9 evaluation data. The executor runs pytest on these fixtures in a child process."""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from patch_review_utility import executor, labels, trees

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
HIDDEN = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
          "def test_zero():\n    assert add(0, 0) == 0\n")
SPEC = "from calc import add\n\n\ndef test_negative():\n    assert add(-2, -3) == -5\n"
_DIFFS = itertools.count()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def make_repo(root: Path, files: dict[str, str]) -> str:
    """A git repository at ``root`` holding ``files`` in one commit; its commit hash."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    git(root, "init", "-q")
    git(root, "-c", "core.autocrlf=false", "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "development fixture")
    return git(root, "rev-parse", "HEAD").strip()


def diff_of(source: Path, base: str, changes: dict[str, str]) -> str:
    """The patch that turns ``base`` into ``base`` plus ``changes``, made in a throwaway clone."""
    tree = trees.checkout(source, base, source.parent / f"diff-{next(_DIFFS)}")
    for rel, text in changes.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text, encoding="utf-8", newline="\n")
    git(tree, "add", "-A")
    return git(tree, "diff", "--cached", "--no-color", "--no-ext-diff")


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    commit = make_repo(root, {"calc.py": BAD, "tests/test_existing.py": "def test_true():\n    assert True\n"})
    return root, commit


# --- trees -------------------------------------------------------------------------------------------------------------

def test_a_tree_is_the_base_commit_with_the_patch_applied_and_says_what_it_changed(source, tmp_path):
    root, base = source
    patch = diff_of(root, base, {"calc.py": GOOD, "tests/test_calc.py": HIDDEN})
    tree = trees.checkout(root, base, tmp_path / "patched")
    assert trees.apply(tree, patch) == "non_empty"
    assert (tree / "calc.py").read_text(encoding="utf-8") == GOOD
    assert git(tree, "rev-parse", "HEAD").strip() == base
    assert trees.changed_files(tree) == ["calc.py", "tests/test_calc.py"]
    assert trees.patch_paths(patch) == ["calc.py", "tests/test_calc.py"]


def test_an_empty_patch_and_one_that_does_not_apply_leave_no_review_decision(source, tmp_path):
    root, base = source
    assert trees.apply(trees.checkout(root, base, tmp_path / "a"), "  \n") == "empty"
    stale = "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n-def mul(a, b):\n+def mul(x, y):\n     return a * b\n"
    tree = trees.checkout(root, base, tmp_path / "b")
    assert trees.apply(tree, stale) == "not_applying"
    assert trees.changed_files(tree) == []


# --- the executor -------------------------------------------------------------------------------------------------------

def test_the_executor_reports_each_test_and_never_touches_the_tree(source, tmp_path):
    root, base = source
    tree = trees.checkout(root, base, tmp_path / "t")
    run = executor.Executor().run(tree, ["test_hidden.py::test_add", "test_hidden.py::test_zero",
                                         "test_hidden.py::test_gone"], timeout_s=120,
                                  overlay={"files": {"test_hidden.py": HIDDEN}})
    assert run["outcomes"]["test_hidden.py::test_add"] == "failed"
    assert run["outcomes"]["test_hidden.py::test_zero"] == "passed"
    assert executor.outcome(run, "test_hidden.py::test_gone") == "missing"
    assert not (tree / "test_hidden.py").exists() and run["timed_out"] is False and run["error"] == ""


def test_the_executor_also_reports_each_tests_phases(source, tmp_path):
    root, base = source
    tree = trees.checkout(root, base, tmp_path / "t")
    text = ("import pytest\n\n\n@pytest.fixture\ndef broken():\n    raise RuntimeError('set-up')\n\n\n"
            "def test_setup(broken):\n    pass\n\n\ndef test_body():\n    assert False\n")
    run = executor.Executor().run(tree, ["test_phases.py"], timeout_s=120, overlay={"files": {"test_phases.py": text}})
    assert run["phases"]["test_phases.py::test_setup"]["setup"] == "failed"
    assert run["phases"]["test_phases.py::test_body"] == {"setup": "passed", "call": "failed", "teardown": "passed"}
    assert run["outcomes"]["test_phases.py::test_setup"] == run["outcomes"]["test_phases.py::test_body"] == "failed"
    assert executor.Executor().run(tree, ["x.py"], timeout_s=5)["phases"] == {}


def test_the_executor_applies_a_patch_overlay_records_collection_errors_and_stops_at_its_timeout(source, tmp_path):
    root, base = source
    tree = trees.checkout(root, base, tmp_path / "t")
    overlay = {"patch": diff_of(root, base, {"test_hidden.py": HIDDEN})}
    patched = executor.Executor().run(tree, ["test_hidden.py::test_zero"], timeout_s=120, overlay=overlay)
    assert executor.outcome(patched, "test_hidden.py::test_zero") == "passed"
    broken = executor.Executor().run(tree, ["test_broken.py"], timeout_s=120,
                                     overlay={"files": {"test_broken.py": "import nowhere_to_be_found\n"}})
    assert broken["collection_errors"] and executor.unrunnable(broken)
    slow = executor.Executor().run(tree, ["test_slow.py"], timeout_s=2,
                                   overlay={"files": {"test_slow.py": "import time\n\n\ndef test_slow():\n"
                                                                      "    time.sleep(30)\n"}})
    assert slow["timed_out"] is True and executor.unrunnable(slow)
    refused = executor.Executor().run(tree, ["x.py"], timeout_s=5, overlay={"patch": "not a patch\n"})
    assert refused["error"].startswith("the overlay did not apply")


FAKE_DOCKER = """\
import sys, time
with open(sys.argv[1], "a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[2:]) + "\\n")
if sys.argv[2] == "run":
    time.sleep(60)
"""


def test_a_container_run_past_its_timeout_is_killed_by_its_own_unique_name(source, tmp_path):
    root, base = source
    tree = trees.checkout(root, base, tmp_path / "t")
    fake, log = tmp_path / "fake_docker.py", tmp_path / "docker.log"
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    prefix = [sys.executable, str(fake), str(log)]
    command = [*prefix, "run", "--rm", "--name", "{container}", "--network", "none", "image", "pytest", "{tests}"]
    run = executor.Executor(command, confined=True, network=False).run(tree, ["tests"], timeout_s=2)
    assert run["timed_out"] is True and run["container_stopped"] is True
    started, killed, removed = log.read_text(encoding="utf-8").splitlines()
    name = started.split("--name ")[1].split()[0]
    assert name.startswith("v9-") and killed == f"kill {name}" and removed == f"rm -f {name}"
    again = executor.Executor(command, confined=True, network=False).run(tree, ["tests"], timeout_s=2)
    assert log.read_text(encoding="utf-8").count(name) == 3 and again["timed_out"]   # every run its own name


# --- the vault and the label contract ------------------------------------------------------------------------------------

def write_contract(folder: Path, base_root: Path, base: str, *, specification=True) -> Path:
    folder.mkdir(parents=True)
    for name, text in (("reference.diff", diff_of(base_root, base, {"calc.py": GOOD})), ("test_hidden.py", HIDDEN),
                       ("test_spec.py", SPEC)):
        (folder / name).write_text(text, encoding="utf-8", newline="\n")
    groups = [{"id": "hidden_tests", "origin": "hidden_tests", "files": {"test_hidden.py": "test_hidden.py"},
               "fail_to_pass": ["test_hidden.py::test_add"], "pass_to_pass": ["test_hidden.py::test_zero"]}]
    if specification:
        groups.append({"id": "specification", "origin": "withheld_specification", "author": "a named author",
                       "files": {"test_spec.py": "test_spec.py"}, "tests": ["test_spec.py::test_negative"]})
    path = folder / "contract.json"
    path.write_text(json.dumps({"task_id": "dev-1", "reference_patch": "reference.diff", "groups": groups}),
                    encoding="utf-8")
    return path


@pytest.fixture
def sealed(source, tmp_path, monkeypatch):
    monkeypatch.setenv("G6_ECHA_RESEARCH", "1")
    root, base = source
    path = write_contract(tmp_path / "vault" / "dev-1", root, base)
    ledger = labels.Ledger(tmp_path / "ledger.jsonl")
    vault = labels.seal([tmp_path / "vault"], ledger=ledger, vault_id="v9-development")
    return vault, ledger, path


def test_the_contract_is_read_only_through_the_vault_and_arm_inputs_inside_it_are_named(sealed, tmp_path):
    vault, ledger, path = sealed
    contract = labels.load(vault, path, ledger=ledger)
    assert contract["groups"][0]["tests"] == ["test_hidden.py::test_add", "test_hidden.py::test_zero"]
    assert contract["groups"][0]["overlay"] == {"files": {"test_hidden.py": HIDDEN}, "patch": ""}
    assert labels.exposed(vault, [path, tmp_path / "source" / "calc.py"]) == [str(path)]
    with pytest.raises(PermissionError):
        labels.guarded_read(vault, "builder", path, ledger=ledger)
    assert [e["kind"] for e in ledger.events() if e["event"] == "incident"] == ["access_denied"]
    with pytest.raises(ValueError):
        labels.load(vault, tmp_path / "source" / "calc.py", ledger=ledger)   # a contract outside the vault


def test_sealing_needs_the_research_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("G6_ECHA_RESEARCH", raising=False)
    (tmp_path / "v").mkdir()
    with pytest.raises(RuntimeError):
        labels.seal([tmp_path / "v"], ledger=labels.Ledger(tmp_path / "l.jsonl"), vault_id="v")


class Scripted:
    """An executor whose outcomes are set per tree name and group: a list, one entry per call, each a mapping of test
    id to outcome, or "timeout"; ``seconds`` per call."""

    def __init__(self, script, seconds=1.0):
        self.script, self.seconds, self.calls = {k: list(v) for k, v in script.items()}, seconds, []

    def run(self, tree, tests, *, timeout_s, overlay=None):
        key = (Path(tree).name, "spec" if "test_spec.py" in (overlay or {}).get("files", {}) else "hidden")
        self.calls.append((key, tuple(tests), timeout_s))
        step = self.script[key].pop(0)
        if step == "error":
            return {"outcomes": {}, "collection_errors": [], "exit": None, "seconds": 0.1, "timed_out": False,
                    "error": "the command could not start (OSError)"}
        if step == "timeout":
            return {"outcomes": {}, "collection_errors": [], "exit": None, "seconds": timeout_s, "timed_out": True,
                    "error": ""}
        return {"outcomes": dict(step), "collection_errors": [], "exit": 0, "seconds": self.seconds,
                "timed_out": False, "error": ""}


ADD, ZERO, NEG = "test_hidden.py::test_add", "test_hidden.py::test_zero", "test_spec.py::test_negative"
ALL_PASS = {ADD: "passed", ZERO: "passed"}


def test_calibration_drops_unstable_checks_sets_timeouts_and_applies_both_oracle_controls(sealed):
    vault, ledger, path = sealed
    contract = labels.load(vault, path, ledger=ledger)
    flaky = [ALL_PASS, {ADD: "passed", ZERO: "failed"}, ALL_PASS]
    run = Scripted({("reference", "hidden"): flaky, ("reference", "spec"): [{NEG: "passed"}] * 3,
                    ("base", "hidden"): [{ADD: "failed", ZERO: "passed"}] * 3, ("base", "spec"): [{NEG: "failed"}] * 3},
                   seconds=12.0)
    got = labels.calibrate(contract, reference_tree=Path("reference"), base_tree=Path("base"), executor=run,
                           timeout_s=600)
    hidden, spec = got["groups"]
    assert hidden["kept"] == [ADD] and hidden["dropped"] == [ZERO] and got["dropped"] == 1
    assert hidden["timeout_s"] == 120.0 and spec["timeout_s"] == 120.0      # ten times the median, at least 60
    assert got["controls"] == {"reference_passes_every_kept_check": True,
                               "base_fails_a_check_the_reference_passes": True, "base_failing": [ADD, NEG]}
    assert got["passed"] is True
    quick = labels.calibrate(contract, reference_tree=Path("reference"), base_tree=Path("base"), executor=Scripted({
        ("reference", "hidden"): [ALL_PASS] * 3, ("reference", "spec"): [{NEG: "passed"}] * 3,
        ("base", "hidden"): [ALL_PASS] * 3, ("base", "spec"): [{NEG: "passed"}] * 3}, seconds=0.5), timeout_s=600)
    assert quick["groups"][0]["timeout_s"] == 60 and quick["passed"] is False     # the base fails nothing


def test_the_reference_must_pass_every_kept_check():
    contract = {"task_id": "x", "groups": [{"id": "hidden_tests", "origin": "hidden_tests", "tests": [ADD],
                                            "fail_to_pass": [ADD], "overlay": {"files": {}, "patch": ""}}]}
    got = labels.calibrate(contract, reference_tree=Path("reference"), base_tree=Path("base"),
                           executor=Scripted({("reference", "hidden"): [{ADD: "skipped"}] * 3,
                                              ("base", "hidden"): [{ADD: "failed"}] * 3}), timeout_s=600)
    assert got["controls"]["reference_passes_every_kept_check"] is False and got["passed"] is False


def calibrated():
    return {"task_id": "dev-1", "passed": True, "groups": [
        {"id": "hidden_tests", "origin": "hidden_tests", "kept": [ADD, ZERO], "fail_to_pass": [ADD],
         "timeout_s": 60.0},
        {"id": "specification", "origin": "withheld_specification", "kept": [NEG], "fail_to_pass": [],
         "timeout_s": 60.0}]}


def test_a_check_fails_on_two_of_three_runs_or_a_timeout_and_a_patch_is_correct_only_when_none_fails(sealed):
    vault, ledger, path = sealed
    contract = labels.load(vault, path, ledger=ledger)
    one_bad = [ALL_PASS, {ADD: "failed", ZERO: "passed"}, ALL_PASS]
    got = labels.label(contract, calibrated(), Path("patched"), executor=Scripted({
        ("patched", "hidden"): one_bad, ("patched", "spec"): [{NEG: "passed"}] * 3}))
    assert got["label"] == "correct" and got["checks"][ADD]["runs"] == ["passed", "failed", "passed"]
    two_bad = [{ADD: "failed", ZERO: "passed"}, {ADD: "failed", ZERO: "passed"}, ALL_PASS]
    got = labels.label(contract, calibrated(), Path("patched"), executor=Scripted({
        ("patched", "hidden"): two_bad, ("patched", "spec"): [{NEG: "passed"}] * 3}))
    assert got["label"] == "defective" and got["failed"] == [ADD] and got["defect_categories"] == ["fail_to_pass"]
    timed = labels.label(contract, calibrated(), Path("patched"), executor=Scripted({
        ("patched", "hidden"): [ALL_PASS, "timeout", ALL_PASS], ("patched", "spec"): [{NEG: "passed"}] * 3}))
    assert timed["label"] == "defective" and timed["failed"] == [ADD, ZERO]
    assert timed["defect_categories"] == ["fail_to_pass", "pass_to_pass"]


def test_an_infrastructure_error_is_retried_once_and_a_persistent_one_leaves_the_patch_unlabelled(sealed):
    vault, ledger, path = sealed
    contract = labels.load(vault, path, ledger=ledger)
    once = Scripted({("patched", "hidden"): ["error", ALL_PASS, ALL_PASS, ALL_PASS],
                     ("patched", "spec"): [{NEG: "passed"}] * 3})
    got = labels.label(contract, calibrated(), Path("patched"), executor=once)
    assert got["label"] == "correct" and got["retried"] == 1 and got["infrastructure_errors"] == []
    assert len(once.calls) == 7
    twice = Scripted({("patched", "hidden"): [ALL_PASS, "error", "error", ALL_PASS],
                      ("patched", "spec"): [{NEG: "failed"}] * 3})
    got = labels.label(contract, calibrated(), Path("patched"), executor=twice)
    assert got["label"] is None and got["retried"] == 1 and got["defect_categories"] == []
    assert got["infrastructure_errors"] == ["hidden_tests: the command could not start (OSError)"]
    assert "infrastructure" in got["unlabelled_because"]


def test_calibration_retries_an_infrastructure_error_once_too(sealed):
    vault, ledger, path = sealed
    contract = labels.load(vault, path, ledger=ledger)
    got = labels.calibrate(contract, reference_tree=Path("reference"), base_tree=Path("base"), executor=Scripted({
        ("reference", "hidden"): ["error", ALL_PASS, ALL_PASS, ALL_PASS], ("reference", "spec"): [{NEG: "passed"}] * 3,
        ("base", "hidden"): [{ADD: "failed", ZERO: "passed"}] * 3, ("base", "spec"): [{NEG: "failed"}] * 3}),
        timeout_s=600)
    assert got["passed"] is True and got["groups"][0]["dropped"] == []


def test_a_disagreement_between_the_hidden_tests_and_the_specification_is_recorded(sealed):
    vault, ledger, path = sealed
    contract = labels.load(vault, path, ledger=ledger)
    got = labels.label(contract, calibrated(), Path("patched"), executor=Scripted({
        ("patched", "hidden"): [ALL_PASS] * 3, ("patched", "spec"): [{NEG: "failed"}] * 3}))
    assert got["label"] == "defective" and got["by_origin"] == {"hidden_tests": True, "withheld_specification": False}
    assert got["disagreement"] is True and got["defect_categories"] == ["specification"]


def test_end_to_end_on_a_real_fixture_the_reference_is_correct_and_a_partial_fix_is_defective(source, sealed,
                                                                                                tmp_path):
    root, base = source
    vault, ledger, path = sealed
    contract = labels.load(vault, path, ledger=ledger)
    reference = trees.checkout(root, base, tmp_path / "reference")
    assert trees.apply(reference, contract["reference_patch"]) == "non_empty"
    run = executor.Executor()
    got = labels.calibrate(contract, reference_tree=reference, base_tree=trees.checkout(root, base, tmp_path / "base"),
                           executor=run, timeout_s=300)
    assert got["passed"] is True and got["dropped"] == 0
    partial = trees.checkout(root, base, tmp_path / "partial")
    trees.apply(partial, diff_of(root, base, {"calc.py": "def add(a, b):\n    return abs(a + b)\n"}))
    found = labels.label(contract, got, partial, executor=run)
    assert found["label"] == "defective" and found["disagreement"] is True
    assert labels.label(contract, got, reference, executor=run)["label"] == "correct"
    assert vault.verify_unchanged(ledger=ledger) == ()
