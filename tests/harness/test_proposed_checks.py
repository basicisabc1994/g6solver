# SPDX-License-Identifier: Apache-2.0
"""Verification search, part 2: checks the caller proposes, run on the base commit's code and on the patch, each in a
fresh confined copy, classified by the intent they declare, and reported in ``assessment.completion``.

A proposed check never passes a requirement and never gives ``defect_detected`` or ``test_integrity_compromised``: a
counterexample or a failure of the change is at most ``insufficient_evidence``, with a reproduction. Development
fixtures only: none is V9 or V10 evaluation data, and no model is called. Tests that take ``bwrap_backend`` run real
confined pytest runs (Linux bubblewrap) and skip elsewhere; the others replace G6's runs with recorded results, which
say nothing about confinement, and the refusal tests switch the boundary off."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.harness import patch_review, verification_search
from g6.harness.patch_review import review_patch
from g6.invariants import check_envelope
from g6.workforce import repo_tests

BASE = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
PLANTED = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b + 1\n"
INCOMPLETE = "def add(a, b):\n    return a + b if a > 0 else a - b\n\n\ndef mul(a, b):\n    return a * b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
ADD = "test_calc.py::test_add"
R1 = [{"id": "R1", "text": "add returns the sum of two integers", "checks": [ADD]}]
COMPLETION = {"demonstrated", "not_established", "defects_reproduced", "preservation", "test_adequacy",
              "open_questions", "activities"}


def check(ident, intent, file, source, requirement="R1", test=""):
    found = {"id": ident, "requirement_id": requirement, "file": file, "source": source}
    if intent is not None:
        found["intent"] = intent
    if test:
        found["test"] = test
    return found


MUL_KEPT = check("c-mul", "preserve", "tests/test_mul_kept.py",
                 "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n")
NEGATIVE = check("c-neg", "change", "test_negative_sum.py",
                 "from calc import add\n\n\ndef test_negative():\n    assert add(-2, -3) == -5\n")
WRONG_KEEP = check("c-wrong", "preserve", "test_wrong_keep.py",
                   "from calc import add\n\n\ndef test_add_six():\n    assert add(2, 3) == 6\n")
VACUOUS = check("c-vac", "change", "test_vacuous.py", "def test_nothing():\n    assert True\n")
PROBE = check("c-probe", "change", "test_probe_add.py",
              "import json\n\nfrom calc import add\n\n\ndef test_observe(capsys):\n    with capsys.disabled():\n"
              "        for a, b in [(2, 3), (-2, -3)]:\n"
              "            print('G6-OBSERVATION ' + json.dumps({'input': [a, b], 'output': add(a, b)}), flush=True)\n")
NO_HISTORY = check("c-history", "preserve", "test_no_history.py",
                   "import shutil\nimport subprocess\nfrom pathlib import Path\n\n\ndef test_no_history():\n"
                   "    here = Path.cwd()\n"
                   "    assert not any((folder / '.git').exists() for folder in (here, *here.parents))\n"
                   "    git = shutil.which('git')\n"
                   "    if git:\n"
                   "        assert subprocess.run([git, 'rev-parse', '--git-dir'], capture_output=True).returncode != 0\n")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text(BASE)
    (root / "test_calc.py").write_text(TESTS)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    return root


def review(repo, requirements=R1, **kwargs):
    return review_patch(repo, task="Make add return the sum of two integers.", requirements=requirements, **kwargs)


def result(passed=(), failed=(), *, verdict=None, output="", seconds=0.25, digest=""):
    """One recorded ``repo_tests`` run result: these node ids passed or failed in their call; ``digest``, the copy's
    digest as the run began (a run in a copy only: an in-place run's is the artifact digest)."""
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    verdict = verdict or ("failed" if failed else "passed")
    return {"verdict": verdict, "said": "", "passed": len(passed), "failed": len(failed), "not_passed": 0,
            "total": len(passed) + len(failed), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": output,
            "seconds": seconds, "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": verdict not in ("passed", "failed"), "receipt": "", "rootdir": "",
            **({"source_before": digest} if digest else {})}


class Tiers:
    """Stands in for the review's own runs (``patch_review._execute`` and ``_execute_at_base``): every in-place run
    passes what it was asked; the stated checks at the base commit did not run."""

    def __init__(self, monkeypatch):
        self.calls: list[tuple[str, ...]] = []

        def execute(root, ids, *, timeout_s, change=None):
            self.calls.append(tuple(ids))
            nodes = [ADD if i == "test_calc.py" else i for i in ids]
            return result(nodes)

        monkeypatch.setattr(patch_review, "_execute", execute)
        monkeypatch.setattr(patch_review, "_execute_at_base",
                            lambda change, ids, *, timeout_s: {"verdict": "not_run", "said": "not recorded"})


class Stage:
    """Stands in for each proposed check's run (``verification_search._run_check``): ``outcomes`` maps a check's text
    to what its run on the patch and at the base commit gives ("passed", "failed", "error", or a recorded result).
    Records each run asked for; ``advance`` seconds pass on G6's clock with each run."""

    def __init__(self, monkeypatch, outcomes, *, advance=0.0):
        self.calls: list[tuple[str, bool, int]] = []
        clock = [verification_search._clock()]
        monkeypatch.setattr(verification_search, "_clock", lambda: clock[0])

        def run_check(change, placed, source, node, *, at_base, timeout_s):
            self.calls.append((node, at_base, timeout_s))
            clock[0] += advance
            spec = outcomes[source]["base" if at_base else "patch"]
            if isinstance(spec, dict):
                return spec
            if spec == "passed":
                return result([node], digest=("b" if at_base else "p") * 64)
            if spec == "failed":
                return result(failed=[node], output=f"FAILED {node} - AssertionError: assert 7 == 6",
                              digest=("b" if at_base else "p") * 64)
            return {"verdict": "collection_error", "said": "the tests could not be collected", "output": "",
                    "seconds": 0.1, "isolation": {"backend": "synthetic-unit-fixture"}}

        monkeypatch.setattr(verification_search, "_run_check", run_check)


def _codes(got) -> set[str]:
    return {reason["code"] for reason in got["assessment"]["reasons"]}


def _requirement(got, ident) -> dict:
    return next(r for r in got["assessment"]["requirements"] if r["id"] == ident)


def _activity(got, ident) -> dict:
    return next(a for a in got["assessment"]["completion"]["activities"] if a.get("check") == ident)


def _refused(got, code) -> None:
    assert got["ok"] is False and got["completion_state"] == "blocked-escalated", got
    assert got["warning_card"]["code"] == code and check_envelope(got) == []
    assert "assessment" not in got and "merge" not in got


# --- search off ---------------------------------------------------------------------------------------------------------

def test_with_no_proposed_checks_every_decided_field_is_unchanged_and_completion_is_added(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    stage = Stage(monkeypatch, {})
    plain = review(repo)
    for extra in ({"proposed_checks": None}, {"proposed_checks": []}):
        got = review(repo, **extra)
        assert got["assessment"]["outcome"] == plain["assessment"]["outcome"] == "supported_within_scope"
        assert got["assessment"]["reasons"] == plain["assessment"]["reasons"]
        assert got["assessment"]["requirements"] == plain["assessment"]["requirements"]
        assert got["assessment"]["unknowns"] == plain["assessment"]["unknowns"]
        assert got["assessment"]["next_actions"] == plain["assessment"]["next_actions"]
        assert set(got) == set(plain) and got["evidence"] == plain["evidence"]
    assert stage.calls == []
    completion = plain["assessment"]["completion"]
    assert set(completion) == COMPLETION
    assert [d["requirement"] for d in completion["demonstrated"]] == ["R1"]
    assert completion["demonstrated"][0]["checks"] == [ADD]
    assert completion["not_established"] == [] and completion["open_questions"] == []
    assert completion["test_adequacy"] == []
    assert completion["defects_reproduced"] == {"stated_and_repository_checks": [], "proposed_checks": []}
    assert not any(a["activity"] == "proposed check" for a in completion["activities"])
    assert all(a["model_calls"] == 0 for a in completion["activities"])


# --- refusals before anything runs --------------------------------------------------------------------------------------

@pytest.mark.parametrize("checks, said", [
    ([check("c1", None, "test_a.py", "def test_a():\n    pass\n")], "intent"),
    ([check("c1", "keep", "test_a.py", "def test_a():\n    pass\n")], "intent"),
    ([check("c1", "preserve", "conftest.py", "import pytest\n")], "test_"),
    ([check("c1", "preserve", "tests/conftest.py", "import pytest\n")], "test_"),
    ([check("c1", "preserve", "pytest.ini", "[pytest]\n")], "test_"),
    ([check("c1", "preserve", "tox.ini", "[pytest]\n")], "test_"),
    ([check("c1", "preserve", "setup.cfg", "[tool:pytest]\n")], "test_"),
    ([check("c1", "preserve", "pyproject.toml", "[tool.pytest.ini_options]\n")], "test_"),
    ([check("c1", "preserve", "pytest.toml", "[pytest]\n")], "test_"),
    ([check("c1", "preserve", "hook.pth", "import os\n")], "test_"),
    ([check("c1", "preserve", "calc/__init__.py", "x = 1\n")], "test_"),
    ([check("c1", "preserve", "sitecustomize.py", "x = 1\n")], "test_"),
    ([check("c1", "preserve", "../test_a.py", "def test_a():\n    pass\n")], ".."),
    ([check("c1", "preserve", "/tmp/test_a.py", "def test_a():\n    pass\n")], "relative"),
    ([check("c1", "preserve", "C:/test_a.py", "def test_a():\n    pass\n")], "relative"),
    ([check("c1", "preserve", "tests\\test_a.py", "def test_a():\n    pass\n")], "relative"),
    ([check("c1", "preserve", ".git/test_a.py", "def test_a():\n    pass\n")], "folder"),
    ([check("c1", "preserve", "__pycache__/test_a.py", "def test_a():\n    pass\n")], "folder"),
    ([check("c1", "preserve", "venv/test_a.py", "def test_a():\n    pass\n")], "folder"),
    ([check("c1", "preserve", "x.dist-info/test_a.py", "def test_a():\n    pass\n")], "folder"),
    ([check("c1", "preserve", "test_a.py", "def test_a():\n    pass\n", test="test_a\n")], "test"),
    ([check("c1", "preserve", "test_a.py", "def test_a():\n    pass\n", test="-p no:x")], "test"),
    ([check("c1", "preserve", "test_a.py", "def test_a():\n    pass\n", requirement="R9")], "R9"),
    ([check("c1", "preserve", "test_a.py", "x = 1\n"), check("c1", "change", "test_b.py", "x = 1\n")], "unique"),
    ([{**check("c1", "preserve", "test_a.py", "x = 1\n"), "weight": 2}], "weight"),
    ([check(f"c{i}", "preserve", "test_a.py", "x = 1\n") for i in range(21)], "20"),
    ([check("c1", "preserve", "test_a.py", "#" * (64 * 1024 + 1))], "64"),
    ("test_a.py", "list"),
])
def test_an_invalid_proposed_check_refuses_the_stage_before_anything_runs(repo, monkeypatch, checks, said):
    (repo / "calc.py").write_text(FIXED)
    tiers, stage = Tiers(monkeypatch), Stage(monkeypatch, {})
    got = review(repo, proposed_checks=checks)
    _refused(got, "G6_E_INPUT_VALIDATION")
    assert said in got["warning_card"]["message"], got["warning_card"]["message"]
    assert tiers.calls == [] and stage.calls == []


def test_twenty_checks_and_sixty_four_kilobytes_each_are_allowed(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    big = "#" * (64 * 1024 - 40) + "\ndef test_a():\n    pass\n"
    checks = [check(f"c{i}", "preserve", f"test_a{i}.py", big) for i in range(20)]
    stage = Stage(monkeypatch, {big: {"patch": "passed", "base": "passed"}})
    got = review(repo, proposed_checks=checks)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert len(stage.calls) == 40


def test_a_check_g6_would_place_where_a_path_exists_or_in_a_linked_folder_is_refused(repo, monkeypatch, tmp_path):
    (repo / "calc.py").write_text(FIXED)
    tiers, stage = Tiers(monkeypatch), Stage(monkeypatch, {})
    taken = check("c1", "preserve", "test_a.py", "def test_a():\n    pass\n")
    folder = repo / verification_search.placement(verification_search.ProposedCheck.model_validate(taken))
    folder.parent.mkdir()
    got = review(repo, proposed_checks=[taken])
    _refused(got, "G6_E_INPUT_VALIDATION")
    assert "exists" in got["warning_card"]["message"]
    folder.parent.rmdir()
    (repo / "src").write_text("not a folder\n")
    got = review(repo, proposed_checks=[check("c1", "preserve", "src/test_a.py", "def test_a():\n    pass\n")])
    _refused(got, "G6_E_INPUT_VALIDATION")
    (repo / "src").unlink()
    assert tiers.calls == [] and stage.calls == []
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        os.symlink(elsewhere, repo / "linked", target_is_directory=True)
    except OSError:
        pytest.skip("this system cannot make a symbolic link here")
    got = review(repo, proposed_checks=[check("c1", "preserve", "linked/test_a.py", "def test_a():\n    pass\n")])
    _refused(got, "G6_E_INPUT_VALIDATION")
    assert "link" in got["warning_card"]["message"]
    assert tiers.calls == [] and stage.calls == []


def test_g6_places_each_check_in_a_folder_of_its_own_named_from_its_text():
    one = verification_search.ProposedCheck.model_validate(MUL_KEPT)
    folder = "g6_check_" + hashlib.sha256(MUL_KEPT["source"].encode("utf-8")).hexdigest()[:12]
    assert verification_search.placement(one) == f"tests/{folder}/test_mul_kept.py"
    top = verification_search.ProposedCheck.model_validate(NEGATIVE)
    assert verification_search.placement(top).startswith("g6_check_")
    assert verification_search.placement(top).endswith("/test_negative_sum.py")


# --- the classifier (plan rule 3) ---------------------------------------------------------------------------------------

NODE = "g6_check_x/test_a.py::test_a"


def _read(spec, observations=()):
    lines = "".join(f"G6-OBSERVATION {json.dumps(item)}\n" for item in observations)
    if spec == "passed":
        ran = result([NODE], output=lines + ".\n1 passed in 0.01s\n")
    elif spec == "failed":
        ran = result(failed=[NODE], output=f"FAILED {NODE} - AssertionError: assert 7 == 6\n")
    elif spec == "contained":
        ran = result(failed=[NODE], output=f"E       PermissionError: [Errno 13] Permission denied: '/x'\n"
                                           f"FAILED {NODE} - PermissionError: [Errno 13] Permission denied\n")
    elif spec == "unreadable":   # options such as --tb=no -rN hide how it failed, so G6 cannot rule out a refusal
        ran = result(failed=[NODE], output="F\n1 failed in 0.01s\n")
    else:
        ran = {"verdict": spec, "said": "", "output": "", "seconds": 0.1}
    return verification_search.reading(ran, NODE)


@pytest.mark.parametrize("intent, base, patch, expected", [
    ("preserve", "passed", "failed", "counterexample"),
    ("preserve", "failed", "failed", "fails_on_base_too"),
    ("preserve", "passed", "passed", "not_refuted"),
    ("preserve", "failed", "passed", "not_refuted"),
    ("change", "passed", "failed", "unconfirmed_failure"),
    ("change", "failed", "failed", "unconfirmed_failure"),
    ("change", "passed", "passed", "not_refuted"),
    ("change", "failed", "passed", "not_refuted"),
    ("preserve", "passed", "timed_out", "check_error"),
    ("change", "collection_error", "passed", "check_error"),
    ("preserve", "passed", "changed_tracked_files", "check_error"),
    ("change", "passed", "incomplete", "check_error"),
    ("preserve", "passed", "contained", "check_error"),
    ("change", "contained", "passed", "check_error"),
    ("preserve", "passed", "unreadable", "check_error"),
    ("change", "passed", "unreadable", "check_error"),
])
def test_each_check_is_classified_by_its_intent_and_its_two_runs(intent, base, patch, expected):
    found = verification_search.classify(intent, _read(patch), _read(base))
    assert found["result"] == expected, found


def test_a_change_check_that_passes_on_both_does_not_exercise_the_change():
    found = verification_search.classify("change", _read("passed"), _read("passed"))
    assert found["result"] == "not_refuted" and "does not exercise the change" in found["note"]
    assert "does not exercise" not in verification_search.classify("preserve", _read("passed"),
                                                                   _read("passed")).get("note", "")


def test_a_probe_whose_observations_differ_has_changed_behaviour_and_one_whose_agree_is_not_refuted():
    base = _read("passed", [{"input": [2, 3], "output": -1}, {"input": [0, 0], "output": 0}])
    patch = _read("passed", [{"input": [2, 3], "output": 5}, {"input": [0, 0], "output": 0}])
    found = verification_search.classify("change", patch, base)
    assert found["result"] == "behaviour_changed"
    assert found["differences"] == [{"input": [2, 3], "base": -1, "patch": 5}]
    same = verification_search.classify("preserve", base, base)
    assert same["result"] == "not_refuted"
    # observations G6 cannot read in full are an error, never a pass or a finding
    malformed = verification_search.reading(result([NODE], output="G6-OBSERVATION {not json}\n"), NODE)
    assert verification_search.classify("change", malformed, base)["result"] == "check_error"
    many = _read("passed", [{"input": i, "output": i} for i in range(verification_search.MAX_OBSERVATIONS + 1)])
    assert verification_search.classify("change", many, base)["result"] == "check_error"
    cut = verification_search.reading(result([NODE], output="G6-OBSERVATION {\"input\": 1, \"output\": 2}\n"
                                             "... [9000 bytes of output omitted] ...\n"), NODE)
    assert verification_search.classify("change", cut, base)["result"] == "check_error"


# --- authority and mapping (plan rules 2 and 4), with recorded runs -----------------------------------------------------

def test_a_counterexample_is_unconfirmed_evidence_never_a_defect_and_the_requirement_keeps_its_status(repo,
                                                                                                    monkeypatch):
    (repo / "calc.py").write_text(PLANTED)
    Tiers(monkeypatch)
    Stage(monkeypatch, {MUL_KEPT["source"]: {"patch": "failed", "base": "passed"}})
    got = review(repo, proposed_checks=[MUL_KEPT])
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [("evidence", "unconfirmed_counterexample")]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    assert not {"check_failed", "derived_test_regressed"} & _codes(got)
    assert got["completion_state"] == "qualified-draft" and check_envelope(got) == []
    found = assessment["completion"]["defects_reproduced"]
    assert found["stated_and_repository_checks"] == []
    [defect] = found["proposed_checks"]
    assert (defect["check"], defect["requirement"], defect["result"], defect["provenance"]) == (
        "c-mul", "R1", "counterexample", "proposed")
    reproduction = defect["reproduction"]
    assert reproduction["text"] == MUL_KEPT["source"]
    assert reproduction["sha256"] == hashlib.sha256(MUL_KEPT["source"].encode("utf-8")).hexdigest()
    assert reproduction["node"].startswith(reproduction["file"]) and reproduction["file"].startswith("tests/g6_check_")
    assert reproduction["command"] == f"python -m pytest -p no:cacheprovider {reproduction['node']}"
    assert (reproduction["patched_tree_digest"], reproduction["base_tree_digest"]) == ("p" * 64, "b" * 64)
    assert reproduction["artifact_digest"] == got["artifact_digest"]
    assert any(reproduction["file"] in action and reproduction["sha256"] in action and reproduction["command"] in action
               for action in assessment["next_actions"])


def test_a_proposed_failure_never_outranks_a_stated_defect_and_the_two_are_kept_apart(repo, monkeypatch):
    (repo / "calc.py").write_text(PLANTED)
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None: result(
        failed=[ADD], output=f"FAILED {ADD} - AssertionError"))
    Stage(monkeypatch, {MUL_KEPT["source"]: {"patch": "failed", "base": "passed"}})
    got = review(repo, proposed_checks=[MUL_KEPT])
    assert got["assessment"]["outcome"] == "defect_detected"
    found = got["assessment"]["completion"]["defects_reproduced"]
    assert [(d["check"], d["provenance"]) for d in found["stated_and_repository_checks"]] == [(ADD, "stated")]
    assert found["stated_and_repository_checks"][0]["reproduction"]["command"] == (
        f"python -m pytest -p no:cacheprovider {ADD}")
    assert [(d["check"], d["provenance"]) for d in found["proposed_checks"]] == [("c-mul", "proposed")]


def test_an_incomplete_fix_is_an_unconfirmed_failure(repo, monkeypatch):
    (repo / "calc.py").write_text(INCOMPLETE)
    Tiers(monkeypatch)
    Stage(monkeypatch, {NEGATIVE["source"]: {"patch": "failed", "base": "failed"}})
    got = review(repo, proposed_checks=[NEGATIVE])
    assert got["assessment"]["outcome"] == "insufficient_evidence"
    assert [(r["stage"], r["code"]) for r in got["assessment"]["reasons"]] == [("evidence", "unconfirmed_failure")]
    [defect] = got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    assert defect["result"] == "unconfirmed_failure" and defect["reproduction"]["text"] == NEGATIVE["source"]


def test_a_wrong_preserve_check_fails_on_the_base_too_and_accuses_nobody(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    Stage(monkeypatch, {WRONG_KEEP["source"]: {"patch": "failed", "base": "failed"}})
    got = review(repo, proposed_checks=[WRONG_KEEP])
    assessment = got["assessment"]
    assert assessment["outcome"] == "supported_within_scope", assessment["reasons"]
    assert assessment["reasons"] == []
    assert assessment["completion"]["defects_reproduced"]["proposed_checks"] == []
    [question] = assessment["completion"]["open_questions"]
    assert question["check"] == "c-wrong" and question["result"] == "fails_on_base_too"
    assert "base commit" in question["question"] and question["behaviours"]["base"]
    assert _activity(got, "c-wrong")["result"] == "fails_on_base_too"


def test_a_vacuous_check_that_passes_leaves_the_requirement_not_established(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    Stage(monkeypatch, {VACUOUS["source"]: {"patch": "passed", "base": "passed"}})
    unchecked = [*R1, {"id": "R2", "text": "add returns the sum of two negative integers", "checks": []}]
    got = review(repo, unchecked, proposed_checks=[{**VACUOUS, "requirement_id": "R2"}])
    second = _requirement(got, "R2")
    assert second["status"] == "not_established"
    assert second["proposed_checks"] == [{"id": "c-vac", "intent": "change", "result": "not_refuted"}]
    assert "not refuted by 1 proposed check" in second["proposed_note"]
    assert got["assessment"]["outcome"] == "insufficient_evidence"
    assert _codes(got) == {"requirement_without_check"}
    assert "does not exercise the change" in _activity(got, "c-vac")["note"]
    [missing] = got["assessment"]["completion"]["not_established"]
    assert missing["requirement"] == "R2" and missing["settle_by"]
    # a requirement no proposed check names is as it was
    assert "proposed_checks" not in _requirement(got, "R1")


def test_preservation_lists_each_obligation_and_what_was_checked_for_it(repo, monkeypatch):
    """An intent's non-goal is an obligation to keep behaviour; a proposed check for it is listed there and does not
    establish it. The tests that touch the changed modules are an obligation the repository states."""
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    Stage(monkeypatch, {MUL_KEPT["source"]: {"patch": "passed", "base": "passed"},
                        NEGATIVE["source"]: {"patch": "passed", "base": "failed"}})
    got = review(repo, intent={"goal": "Make add sum.", "non_goals": ["change what mul returns"]},
                 proposed_checks=[{**MUL_KEPT, "requirement_id": "intent.non_goals.0"}, NEGATIVE])
    preservation = {entry["obligation"]: entry for entry in got["assessment"]["completion"]["preservation"]}
    kept = preservation["intent.non_goals.0"]
    assert (kept["status"], kept["provenance"], kept["stated_checks"]) == ("not_established", "stated", [])
    assert kept["proposed_checks"] == [{"id": "c-mul", "intent": "preserve", "result": "not_refuted"}]
    derived = preservation["the repository's tests that touch the changed modules"]
    assert (derived["provenance"], derived["result"], derived["regressions"]) == ("repository_stated", "passed", [])
    assert not any(entry.get("check") == "c-neg" for entry in preservation.values())   # a change check keeps nothing
    assert _requirement(got, "intent.non_goals.0")["status"] == "not_established"


def test_a_probe_on_intended_behaviour_asks_a_question_and_changes_no_outcome(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    observed = {"base": [{"input": [2, 3], "output": -1}], "patch": [{"input": [2, 3], "output": 5}]}
    node = "g6_check_x/test_probe_add.py"
    Stage(monkeypatch, {PROBE["source"]: {
        side: result([node], output="".join(f"G6-OBSERVATION {json.dumps(o)}\n" for o in items))
        for side, items in observed.items()}})
    monkeypatch.setattr(verification_search, "placement", lambda one: node)
    got = review(repo, proposed_checks=[PROBE])
    assessment = got["assessment"]
    assert assessment["outcome"] == "supported_within_scope" and assessment["reasons"] == []
    [question] = assessment["completion"]["open_questions"]
    assert question["result"] == "behaviour_changed"
    assert question["behaviours"]["differences"] == [{"input": [2, 3], "base": -1, "patch": 5}]
    assert "-1" in question["question"] and "5" in question["question"] and "intended" in question["question"]


def test_at_most_three_questions_are_asked(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    sources = [f"def test_{i}():\n    assert False\n" for i in range(5)]
    Stage(monkeypatch, {text: {"patch": "failed", "base": "failed"} for text in sources})
    got = review(repo, proposed_checks=[check(f"c{i}", "preserve", f"test_q{i}.py", text)
                                        for i, text in enumerate(sources)])
    assert len(got["assessment"]["completion"]["open_questions"]) == verification_search.MAX_QUESTIONS == 3
    assert [a["result"] for a in got["assessment"]["completion"]["activities"]
            if a["activity"] == "proposed check"] == ["fails_on_base_too"] * 5


def test_a_check_error_is_counted_never_a_finding(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    stage = Stage(monkeypatch, {VACUOUS["source"]: {"patch": "error", "base": "passed"}})
    got = review(repo, proposed_checks=[VACUOUS])
    assert got["assessment"]["outcome"] == "supported_within_scope" and got["assessment"]["reasons"] == []
    found = _activity(got, "c-vac")
    assert found["result"] == "check_error"
    assert [run["on"] for run in found["runs"]] == ["patch", "base commit"]
    assert found["runs"][1]["result"] == "skipped" and found["runs"][1]["reason"]
    assert len(stage.calls) == 1   # its run at the base commit would decide nothing


def test_completion_activities_list_every_run_and_every_skip_and_the_stage_stops_at_the_deadline(repo,
                                                                                                monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    sources = [f"def test_{i}():\n    pass\n" for i in range(3)]
    stage = Stage(monkeypatch, {text: {"patch": "passed", "base": "passed"} for text in sources}, advance=40.0)
    got = review(repo, timeout_s=100, proposed_checks=[check(f"c{i}", "preserve", f"test_t{i}.py", text)
                                                       for i, text in enumerate(sources)])
    activities = got["assessment"]["completion"]["activities"]
    assert all(isinstance(a["seconds"], float) and a["model_calls"] == 0 for a in activities)
    tiers = [(a["tier"], a["result"]) for a in activities if a["activity"] == "check run"]
    assert (1, "passed") in tiers and (2, "passed") in tiers and (3, "skipped") in tiers
    assert all(a["reason"] for a in activities if a["result"] == "skipped")
    first, second, third = (_activity(got, f"c{i}") for i in range(3))
    assert first["result"] == "not_refuted" and [r["on"] for r in first["runs"]] == ["patch", "base commit"]
    assert second["result"] == "check_error"
    assert second["runs"][0]["result"] == "passed" and second["runs"][1]["result"] == "skipped"
    assert "time" in second["runs"][1]["reason"]
    assert third["result"] == "skipped" and third["runs"] == [] and "time" in third["reason"]
    assert [(node.startswith("g6_check_"), at_base) for node, at_base, _ in stage.calls] == [
        (True, False), (True, True), (True, False)]
    assert all(bound <= verification_search.RUN_S for _, _, bound in stage.calls)
    # nothing it skipped is counted for or against the patch
    assert got["assessment"]["outcome"] == "supported_within_scope"


def test_the_stage_is_skipped_when_test_integrity_is_compromised(repo, monkeypatch):
    (repo / "calc.py").write_text(FIXED)
    (repo / "conftest.py").write_text("import pytest\n")
    Tiers(monkeypatch)
    stage = Stage(monkeypatch, {})
    got = review(repo, proposed_checks=[MUL_KEPT])
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert stage.calls == []
    found = _activity(got, "c-mul")
    assert found["result"] == "skipped" and "integrity" in found["reason"]


# --- no isolation: the stage is refused, never run unconfined -----------------------------------------------------------

def _no_boundary(got) -> None:
    _refused(got, ISOLATION_UNAVAILABLE)
    said = got["warning_card"]["message"]
    assert "bubblewrap" in said and "g6 doctor" in said and "choose it once with g6 setup" not in said


def test_without_isolation_the_stage_is_refused_and_no_check_runs_unconfined(repo, monkeypatch, tmp_path):
    (repo / "calc.py").write_text(FIXED)
    Tiers(monkeypatch)
    marker = tmp_path / "ran-unconfined.txt"
    writes = check("c-write", "preserve", "test_marker.py",
                   f"from pathlib import Path\n\n\ndef test_marker():\n    Path({str(marker)!r}).write_text('x')\n")
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # the person allowed unconfined runs: a proposed check still never has one
    monkeypatch.delenv(confine.REQUIRE, raising=False)
    _no_boundary(review(repo, proposed_checks=[writes]))
    monkeypatch.delenv(confine.ALLOW)
    _no_boundary(review(repo, proposed_checks=[writes]))
    assert not marker.exists()


def test_a_copy_run_that_requires_isolation_is_refused_before_it_starts(repo, monkeypatch, tmp_path):
    """The additive ``require_isolation`` of ``repo_tests``: a run with no backend is refused even where the person
    allowed unconfined runs, at the base commit and on a copy of the patch alike; without it nothing changes."""
    (repo / "calc.py").write_text(FIXED)
    marker = tmp_path / "ran.txt"
    text = f"from pathlib import Path\n\n\ndef test_marker():\n    Path({str(marker)!r}).write_text('x')\n"
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")
    change = repo_tests.change(repo)
    with pytest.raises(repo_tests.RepoRefused) as refused:
        repo_tests.run_at_base(change, ["g6_check_t/test_m.py"], overlay={"g6_check_t/test_m.py": text},
                               require_isolation=True)
    assert refused.value.code == ISOLATION_UNAVAILABLE and not marker.exists()
    with pytest.raises(repo_tests.RepoRefused):
        verification_search._run_check(change, "g6_check_t/test_m.py", text, "g6_check_t/test_m.py", at_base=False,
                                       timeout_s=30)
    assert not marker.exists()
    # without it, as before: the person allowed the run, which goes ahead with the check overlaid in the copy
    ran = repo_tests.run_at_base(change, ["g6_check_t/test_m.py"], overlay={"g6_check_t/test_m.py": text})
    assert ran["verdict"] == "passed" and ran["isolation"]["backend"] is None and marker.exists()
    assert not (repo / "g6_check_t").exists()


# --- the MCP surface ----------------------------------------------------------------------------------------------------

def test_the_mcp_tool_takes_proposed_checks_and_keeps_its_envelope_and_codes(repo, monkeypatch):
    pytest.importorskip("fastmcp")
    import asyncio

    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    (repo / "calc.py").write_text(PLANTED)
    Tiers(monkeypatch)
    Stage(monkeypatch, {MUL_KEPT["source"]: {"patch": "failed", "base": "passed"}})
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def finished(client, arguments) -> dict:
        job = data(await client.call_tool("g6_review_patch", {"repo": str(repo), "task": "Make add sum.",
                                                              "requirements": R1, **arguments}))
        for _ in range(400):
            got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
            if got.get("status") in ("completed", "failed"):
                assert got["status"] == "completed", got
                return got["result"]
            await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    async def flow():
        tool = (await server.get_tools())["g6_review_patch"]
        async with Client(server) as client:
            plain = await finished(client, {})
            searched = await finished(client, {"proposed_checks": [MUL_KEPT]})
            refused = await finished(client, {"proposed_checks": [check("c1", None, "test_a.py", "x = 1\n")]})
        return tool, plain, searched, refused

    tool, plain, searched, refused = asyncio.run(flow())
    assert "proposed_checks" in tool.parameters["properties"] and "proposed_checks" in tool.description
    assert plain["assessment"]["outcome"] == "supported_within_scope"
    assert searched["assessment"]["outcome"] == "insufficient_evidence"
    assert "unconfirmed_counterexample" in _codes(searched)
    for got in (plain, searched):
        assert got["completion_state"] == "qualified-draft" and got["warning_card"]["code"] == "G6_E_PATCH_REVIEW"
        assert check_envelope(got) == [] and got["request_id"] and "run_id" in got
        assert set(got["assessment"]["completion"]) == COMPLETION
    assert set(searched) == set(plain)
    assert [set(item) for item in searched["evidence"]] == [set(item) for item in plain["evidence"]]
    assert refused["completion_state"] == "blocked-escalated"
    assert refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and "assessment" not in refused


# --- real confined runs (Linux bubblewrap; run in Docker on Windows) ----------------------------------------------------

def test_confined_a_planted_defect_gives_a_counterexample_with_its_reproduction(repo, bwrap_backend):
    (repo / "calc.py").write_text(PLANTED)
    got = review(repo, proposed_checks=[MUL_KEPT, NO_HISTORY])
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [("evidence", "unconfirmed_counterexample")]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    mul = _activity(got, "c-mul")
    assert mul["result"] == "counterexample", mul
    assert [(run["on"], run["result"]) for run in mul["runs"]] == [("patch", "failed"), ("base commit", "passed")]
    # the scratch copies hold no repository history: no .git in the copy or above it, and git finds no repository
    assert _activity(got, "c-history")["result"] == "not_refuted", _activity(got, "c-history")
    [defect] = assessment["completion"]["defects_reproduced"]["proposed_checks"]
    reproduction = defect["reproduction"]
    assert reproduction["patched_tree_digest"] != reproduction["base_tree_digest"]
    assert all(len(reproduction[key]) == 64 for key in ("sha256", "patched_tree_digest", "base_tree_digest"))
    assert got["artifact_digest"] == patch_review.digest(repo_tests.snapshot(repo))


def _copy_without_history(repo: Path, into: Path) -> Path:
    shutil.copytree(repo, into, ignore=shutil.ignore_patterns(".git"))
    return into


def _plain_pytest(where: Path, node: str) -> subprocess.CompletedProcess:
    """``python -m pytest -p no:cacheprovider <node>`` from ``where``, with G6 kept out: no PYTHONPATH, and an import
    of g6 fails."""
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME", "SYSTEMROOT", "TMP", "TEMP")}
    boot = ("import sys; sys.modules['g6'] = None; import pytest; "
            f"raise SystemExit(pytest.main(['-p', 'no:cacheprovider', {node!r}]))")
    return subprocess.run([sys.executable, "-c", boot], cwd=where, env=env, capture_output=True, text=True,
                          timeout=120)


def test_confined_a_reproduction_runs_with_plain_pytest_in_a_fresh_copy_without_g6(repo, bwrap_backend, tmp_path):
    (repo / "calc.py").write_text(PLANTED)
    got = review(repo, proposed_checks=[MUL_KEPT])
    [defect] = got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    reproduction = defect["reproduction"]
    assert hashlib.sha256(reproduction["text"].encode("utf-8")).hexdigest() == reproduction["sha256"]
    patched = _copy_without_history(repo, tmp_path / "patched")
    (patched / reproduction["file"]).parent.mkdir(parents=True)
    (patched / reproduction["file"]).write_text(reproduction["text"], encoding="utf-8")
    base = _copy_without_history(patched, tmp_path / "base")
    (base / "calc.py").write_text(BASE)
    on_patch, at_base = _plain_pytest(patched, reproduction["node"]), _plain_pytest(base, reproduction["node"])
    assert on_patch.returncode == 1 and "1 failed" in on_patch.stdout, on_patch.stdout + on_patch.stderr
    assert at_base.returncode == 0 and "1 passed" in at_base.stdout, at_base.stdout + at_base.stderr


def test_confined_a_wrong_preserve_check_and_an_incomplete_fix_and_a_probe(repo, bwrap_backend):
    (repo / "calc.py").write_text(INCOMPLETE)
    got = review(repo, proposed_checks=[WRONG_KEEP, NEGATIVE, PROBE, VACUOUS])
    assert got["assessment"]["outcome"] == "insufficient_evidence", got["assessment"]["reasons"]
    assert [(r["stage"], r["code"]) for r in got["assessment"]["reasons"]] == [("evidence", "unconfirmed_failure")]
    assert {ident: _activity(got, ident)["result"] for ident in ("c-wrong", "c-neg", "c-probe", "c-vac")} == {
        "c-wrong": "fails_on_base_too", "c-neg": "unconfirmed_failure", "c-probe": "behaviour_changed",
        "c-vac": "not_refuted"}
    [defect] = got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"]
    assert defect["check"] == "c-neg"
    questions = {q["check"]: q for q in got["assessment"]["completion"]["open_questions"]}
    assert set(questions) == {"c-wrong", "c-probe"}
    assert questions["c-probe"]["behaviours"]["differences"] == [{"input": [2, 3], "base": -1, "patch": 5}]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    # a probe on intended behaviour alone changes no outcome
    (repo / "calc.py").write_text(FIXED)
    probed = review(repo, proposed_checks=[PROBE])
    assert probed["assessment"]["outcome"] == "supported_within_scope", probed["assessment"]["reasons"]
    assert _activity(probed, "c-probe")["result"] == "behaviour_changed"


def test_confined_a_check_that_writes_reaches_the_network_or_overruns_is_contained(repo, bwrap_backend, monkeypatch,
                                                                                    tmp_path):
    (repo / "calc.py").write_text(FIXED)
    monkeypatch.setattr(verification_search, "RUN_S", 8)
    planted, outside = repo / "planted.txt", tmp_path / "outside.txt"
    writes = check("c-write", "preserve", "test_write.py",
                   f"from pathlib import Path\n\n\ndef test_write():\n    Path({str(planted)!r}).write_text('x')\n"
                   f"    Path({str(outside)!r}).write_text('x')\n")
    network = check("c-net", "preserve", "test_net.py",
                    "import _socket\n\n\ndef test_connect():\n"
                    "    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)\n    s.settimeout(3)\n"
                    "    s.connect(('192.0.2.1', 9))\n")
    slow = check("c-slow", "change", "test_slow.py", "import time\n\n\ndef test_slow():\n    time.sleep(120)\n")
    edits = check("c-edit", "change", "test_edit.py",
                  "from pathlib import Path\n\n\ndef test_edit():\n"
                  "    Path('calc.py').write_text('def add(a, b):\\n    return 0\\n')\n")
    before = patch_review.digest(repo_tests.snapshot(repo))
    got = review(repo, proposed_checks=[writes, network, slow, edits])
    assert got["artifact_digest"] == before == patch_review.digest(repo_tests.snapshot(repo))
    assert not planted.exists() and not outside.exists()
    assert (repo / "calc.py").read_text() == FIXED
    for ident in ("c-write", "c-net", "c-slow", "c-edit"):
        assert _activity(got, ident)["result"] == "check_error", _activity(got, ident)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert got["assessment"]["completion"]["defects_reproduced"]["proposed_checks"] == []
