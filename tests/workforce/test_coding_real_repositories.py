# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Repository mode on real-shaped repositories (ECHA Stage A, 2026-10-09, founder decision (a)).

The Stage A baseline ran G6 on 15 public bug fixes. As shipped it refused 9 of 12: a ``src/`` layout its runner could not
import, one skipped, xfailed or xpassed top-level test (which it could not count), a pytest fixture named ``test_url``
(which it counted as a test it could never run), and it ran only top-level ``def test_*`` functions, so a patch that broke
class-based tests closed as qualified-draft. Pinned here, through the real MCP entry: such a repository is accepted; what
G6 could not count is recorded in the frozen standard under its reason, never silently dropped; and a patch that passes
every named test but breaks a class-based one is not accepted.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from g6.workforce import truth
from g6.workforce import bench, coding, service

_spec = importlib.util.spec_from_file_location("objective_rig", Path(__file__).with_name("_objective_rig.py"))
_rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rig)
objective_rig, plain = _rig.objective_rig, _rig.plain

# COPY: Synthetic owner's objective.
OBJECTIVE = "Fix the failing area test and verify that the underlying bug is solved."
BUGGY = "def area(w, h):\n    return w + h\n"
FIXED = "def area(w, h):\n    return w * h\n"
#: Fixes the named failing test by special-casing it, and breaks a class-based test the visible list never held.
CHEAT = "def area(w, h):\n    return 6 if (w, h) == (2, 3) else 0\n"
TESTS = (
    "import pytest\nfrom shapes import area\n\n\n"
    "def test_area():\n    assert area(2, 3) == 6\n\n\n"
    "def test_zero():\n    assert area(0, 0) == 0\n\n\n"
    "@pytest.mark.skip(reason='later')\ndef test_skipped():\n    assert area(1, 1) == 99\n\n\n"
    "@pytest.mark.xfail\ndef test_expected_to_fail():\n    assert area(1, 1) == 99\n\n\n"
    "@pytest.mark.xfail\ndef test_unexpectedly_passes():\n    assert area(0, 0) == 0\n\n\n"
    "@pytest.fixture\ndef test_url():\n    return 'http://example.test'\n\n\n"
    "class TestSquares:\n    def test_square(self):\n        assert area(2, 2) == 4\n"
)
FAILING = "tests/test_shapes.py::test_area"
MODULE = "src/shapes/__init__.py"
FILES = {MODULE: BUGGY, "tests/test_shapes.py": TESTS}


def create(rig, files=None, **request):
    rig.repository(files or FILES)
    found = rig.call("mission_create", objective=plain(OBJECTIVE), risk_tier="R0", budget_cents=0,
                     repo_json=json.dumps({"repo": "auth_service", **request}))
    return found


def standard(mid):
    return service.bind().missions.acceptance(mid).metadata["coding"]


def test_a_src_layout_repository_with_tests_g6_cannot_count_is_accepted(objective_rig):
    found = create(objective_rig)
    assert found["ok"], found
    frozen = standard(found["mission_id"])
    # the top-level tests in file order, then the class-based one pytest collects (audit F01, 2026-10-11)
    assert frozen["required_tests"] == [FAILING, "tests/test_shapes.py::test_zero", "tests/test_shapes.py::TestSquares::test_square"]
    assert frozen["scope"]["selected"] == {"module": MODULE, "function": "area"}    # found under src/ without being named
    assert frozen["scope"]["failing"] == [FAILING]


def test_the_question_to_the_person_and_the_agreed_requirements_name_only_the_tests_that_count(objective_rig):
    """Before Stage A the person was asked to confirm a list that read "test_skipped: G6 cannot check this"."""
    rig = objective_rig
    mid = create(rig)["mission_id"]
    asked = rig.call("mission_status", mission_id=mid, detail="full")["awaiting_human"][0]["question"]
    agreed = json.dumps(service.bind().missions.acceptance(mid).as_dict()["requirements"])
    for text in (asked, agreed):
        assert not any(name in text for name in ("test_skipped", "test_expected_to_fail", "test_unexpectedly_passes", "test_url"))
    assert "test_area" in agreed and "test_zero" in agreed


def test_what_g6_could_not_count_is_recorded_under_its_reason(objective_rig):
    frozen = standard(create(objective_rig)["mission_id"])
    assert frozen["suite"]["status"] == "ran"
    assert frozen["suite"]["not_counted"] == [
        {"test": "tests/test_shapes.py::test_expected_to_fail", "why": "xfailed"},
        {"test": "tests/test_shapes.py::test_skipped", "why": "skipped"},
        {"test": "tests/test_shapes.py::test_unexpectedly_passes", "why": "xpassed"},
        {"test": "tests/test_shapes.py::test_url", "why": "not a test"}]
    # the baseline suite passes test_zero and the class-based test; test_area fails: that is the whole count
    assert frozen["suite"]["passed"] == 2


def test_a_patch_that_fixes_the_named_test_and_breaks_a_class_based_one_is_not_accepted(objective_rig):
    rig = objective_rig
    mid = create(rig)["mission_id"]
    rig.human(mid)
    unit = rig.call("mission_status", mission_id=mid, detail="full")["awaiting_chat"][0]["unit"]
    sent_back = rig.call("mission_submit", mission_id=mid, unit_key=unit, artifact_json=json.dumps({"files": {MODULE: CHEAT}}))
    assert sent_back["ok"], sent_back
    assert sent_back.get("status") != "done" and sent_back.get("awaiting_chat"), sent_back
    assert "TestSquares::test_square" in json.dumps(sent_back["awaiting_chat"])         # the worker is told which test broke


def test_the_same_patch_without_the_class_based_break_is_accepted(objective_rig):
    rig = objective_rig
    mid = create(rig)["mission_id"]
    rig.human(mid)
    unit = rig.call("mission_status", mission_id=mid, detail="full")["awaiting_chat"][0]["unit"]
    done = rig.call("mission_submit", mission_id=mid, unit_key=unit, artifact_json=json.dumps({"files": {MODULE: FIXED}}))
    assert done["ok"] and done["status"] == "done" and done["completion_state"] == "qualified-draft", done


def test_a_repeated_cheat_is_sent_back_with_the_broken_class_based_test_and_does_not_crash(objective_rig):
    rig = objective_rig
    mid = create(rig)["mission_id"]
    rig.human(mid)
    unit = rig.call("mission_status", mission_id=mid, detail="full")["awaiting_chat"][0]["unit"]
    body = json.dumps({"files": {MODULE: CHEAT}})
    rig.call("mission_submit", mission_id=mid, unit_key=unit, artifact_json=body)
    again = rig.call("mission_submit", mission_id=mid, unit_key=unit, artifact_json=body)
    assert again["ok"], again                       # a repeat goes to the focused brief, which reads the broken tests' source
    shown = json.dumps(again.get("awaiting_chat") or again)
    assert "TestSquares::test_square" in shown
    assert "area(2, 2) == 4" in shown        # the focused brief reads the broken class-based test's own assertion


CLASS_ONLY = ("from shapes import area\n\n\nclass TestArea:\n    def test_area(self):\n        assert area(2, 3) == 6\n\n"
              "    def test_zero(self):\n        assert area(0, 0) == 0\n")
CLASS_NODES = ["tests/test_shapes.py::TestArea::test_area", "tests/test_shapes.py::TestArea::test_zero"]


def test_a_repository_whose_tests_are_all_in_classes_is_accepted_and_its_failing_class_test_scopes_the_change(objective_rig):
    """Integrated audit F01 (2026-10-11): the frozen list came from top-level ``def test_*`` only, so a repository that
    keeps its tests in classes (the common pytest shape) was refused as having no tests, and a failing class-based test
    could not name the function to change."""
    found = create(objective_rig, {MODULE: BUGGY, "tests/test_shapes.py": CLASS_ONLY})
    assert found["ok"], found
    frozen = standard(found["mission_id"])
    assert sorted(frozen["required_tests"]) == CLASS_NODES
    assert frozen["scope"]["failing"] == ["tests/test_shapes.py::TestArea::test_area"]
    assert frozen["scope"]["selected"] == {"module": MODULE, "function": "area"}


def test_a_class_based_test_in_a_mixed_repository_is_a_required_test_too(objective_rig):
    frozen = standard(create(objective_rig)["mission_id"])
    assert "tests/test_shapes.py::TestSquares::test_square" in frozen["required_tests"]
    assert frozen["required_tests"][:2] == [FAILING, "tests/test_shapes.py::test_zero"]  # the top-level order is unchanged


def test_a_fixture_named_like_a_test_does_not_make_g6_refuse_the_repository(objective_rig):
    files = {"src/shapes/__init__.py": BUGGY,
             "tests/test_shapes.py": ("import pytest\nfrom shapes import area\n\n\n"
                                      "@pytest.fixture\ndef test_url():\n    return 1\n\n\n"
                                      "def test_area():\n    assert area(2, 3) == 6\n\n\n"
                                      "def test_zero():\n    assert area(0, 0) == 0\n")}
    found = create(objective_rig, files)
    assert found["ok"], found
    assert standard(found["mission_id"])["required_tests"] == [FAILING, "tests/test_shapes.py::test_zero"]


def test_when_the_suite_cannot_run_at_the_start_the_frozen_list_is_the_visible_one_and_says_so(objective_rig, monkeypatch):
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: {
        "ran": False, "started": False, "why": "the test runner could not start", "outcomes": {},
        "collect_errors": [], "seconds": 0.0})
    # without a runner that can name each test, a repository whose visible tests all run is frozen as before
    files = {"shapes.py": BUGGY, "tests/test_shapes.py": TESTS.replace("import pytest\n", "import pytest\n")
             .replace("@pytest.mark.skip(reason='later')\ndef test_skipped():\n    assert area(1, 1) == 99\n\n\n", "")
             .replace("@pytest.mark.xfail\ndef test_expected_to_fail():\n    assert area(1, 1) == 99\n\n\n", "")
             .replace("@pytest.mark.xfail\ndef test_unexpectedly_passes():\n    assert area(0, 0) == 0\n\n\n", "")}
    found = create(objective_rig, files, module="shapes.py", function="area")
    assert found["ok"], found
    frozen = standard(found["mission_id"])
    assert frozen["suite"] == {"status": "not_run", "reason": "the test runner could not start", "passed": 0, "not_counted": []}
    assert FAILING in frozen["required_tests"]


def test_a_test_in_a_file_that_cannot_be_imported_says_so_instead_of_not_a_test():
    """python-dotenv's ipython tests (Stage A): the file does not import here, so pytest never collects its tests."""
    suite = {"ran": True, "started": True, "why": "", "collect_errors": ["tests/test_ipython.py"], "seconds": 0.1,
             "outcomes": {"tests/test_a.py::test_ok": "passed", "tests/test_a.py::test_slow[1]": "skipped",
                          "tests/test_a.py::test_slow[2]": "skipped", "tests/test_a.py::test_mixed[a]": "passed",
                          "tests/test_a.py::test_mixed[b]": "skipped"}}
    kept, record = coding._counted(
        ["tests/test_a.py::test_ok", "tests/test_a.py::test_slow", "tests/test_a.py::test_mixed",
         "tests/test_ipython.py::test_new_variable", "tests/test_a.py::test_url"], suite)
    assert kept == ["tests/test_a.py::test_ok", "tests/test_a.py::test_mixed"]     # a mixed function still runs and counts
    assert record["not_counted"] == [{"test": "tests/test_a.py::test_slow", "why": "skipped"},
                                     {"test": "tests/test_a.py::test_url", "why": "not a test"},
                                     {"test": "tests/test_ipython.py::test_new_variable", "why": "could not be collected"}]
    assert record["passed"] == 2


def _finish(rig, mid, body):
    rig.human(mid)
    unit = rig.call("mission_status", mission_id=mid, detail="full")["awaiting_chat"][0]["unit"]
    return rig.call("mission_submit", mission_id=mid, unit_key=unit, artifact_json=json.dumps({"files": {MODULE: body}}))


def test_a_finished_result_says_how_many_tests_g6_could_not_count(objective_rig):
    done = _finish(objective_rig, create(objective_rig)["mission_id"], FIXED)
    assert done["status"] == "done", done
    assert "could not count 4 of this repository's tests" in json.dumps(done["result"])


def test_a_result_says_when_the_whole_suite_could_not_be_run_at_the_start(objective_rig, monkeypatch):
    real = truth.run_suite
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: {
        "ran": False, "started": False, "why": "the test runner could not start", "outcomes": {},
        "collect_errors": [], "seconds": 0.0})
    mid = create(objective_rig, {"shapes.py": BUGGY, "tests/test_shapes.py": (
        "from shapes import area\n\n\ndef test_area():\n    assert area(2, 3) == 6\n")}, module="shapes.py",
        function="area")["mission_id"]
    monkeypatch.setattr(truth, "run_suite", real)
    rig = objective_rig
    rig.human(mid)
    unit = rig.call("mission_status", mission_id=mid, detail="full")["awaiting_chat"][0]["unit"]
    done = rig.call("mission_submit", mission_id=mid, unit_key=unit, artifact_json=json.dumps({"files": {"shapes.py": FIXED}}))
    assert done["status"] == "done", done
    assert "could not run this repository's whole test suite" in json.dumps(done["result"])


def test_a_result_says_when_the_change_could_not_be_compared_with_the_rest_of_the_suite(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)["mission_id"]
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: {
        "ran": False, "started": False, "why": "the test runner could not start", "outcomes": {},
        "collect_errors": [], "seconds": 0.0})
    bench.SUITE_BASELINES.clear()
    done = _finish(rig, mid, FIXED)
    assert done["status"] == "done", done
    assert "could not compare this change with the rest of the repository's tests" in json.dumps(done["result"])


def test_a_result_that_compared_everything_does_not_claim_otherwise(objective_rig):
    done = _finish(objective_rig, create(objective_rig, {"src/shapes/__init__.py": BUGGY, "tests/test_shapes.py": (
        "from shapes import area\n\n\ndef test_area():\n    assert area(2, 3) == 6\n\n\n"
        "def test_zero():\n    assert area(0, 0) == 0\n")})["mission_id"], FIXED)
    assert done["status"] == "done", done
    text = json.dumps(done["result"])
    assert "could not count" not in text and "could not compare this change" not in text and "whole test suite" not in text


@pytest.mark.parametrize(("node", "expected"), [
    ("tests/test_x.py::test_plain", ["assert a == 1"]),
    ("tests/test_x.py::TestGroup::test_method", ["assert b == 2"]),
    ("tests/test_x.py::test_param[1-3]", ["assert c == 3"]),
    ("tests/test_x.py::TestGroup::test_param[x]", ["assert b == 2"]),
    ("tests/test_x.py::TestNested::TestInner::test_deep", ["assert d == 4"]),
    ("tests/test_x.py::test_missing", []),
])
def test_a_failing_tests_assertions_are_read_whatever_its_node_id_looks_like(tmp_path, node, expected):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test_plain():\n    assert a == 1\n\n\n"
        "class TestGroup:\n    def test_method(self):\n        assert b == 2\n\n"
        "    def test_param(self, x):\n        assert b == 2\n\n\n"
        "def test_param(c):\n    assert c == 3\n\n\n"
        "class TestNested:\n    class TestInner:\n        def test_deep(self):\n            assert d == 4\n",
        encoding="utf-8")
    assert coding._assertions(tmp_path, node) == expected


def test_the_bench_comparison_is_what_catches_it(objective_rig):
    """The control for the class-based break: the top-level tests alone (what G6 ran before Stage A, and what the frozen list
    held before audit F01) pass the cheat; the suite comparison is what catches it whatever the frozen list holds."""
    rig = objective_rig
    mid = create(rig)["mission_id"]
    wf = service.bind()
    named = [test for test in standard(mid)["required_tests"] if test.count("::") == 1]
    found = bench.run("run_tests", {"repo": standard(mid)["snapshot"], "tests": named, "files": {MODULE: CHEAT}},
                      venture=wf.venture_id)["result"]
    assert found["tests"] == dict.fromkeys(named, True) and found["suite"]["status"] == "fail" and not found["passed"]


def test_a_suite_that_writes_files_does_not_change_the_frozen_snapshot(objective_rig):
    """Review I-2: the suite ran inside the snapshot that is then frozen, so a file a test wrote became part of the
    baseline G6 holds every patch to."""
    files = {**FILES, "tests/test_writes.py": ("from pathlib import Path\n\n\ndef test_writes():\n"
                                               "    Path(__file__).with_name('written_by_a_test.txt').write_text('x')\n")}
    found = create(objective_rig, files)
    assert found["ok"], found
    frozen = standard(found["mission_id"])
    snapshot = bench._repo(frozen["snapshot"], service.bind().venture_id)
    assert not list(snapshot.rglob("written_by_a_test.txt"))
    assert frozen["baseline_digest"] == bench._digest(snapshot)


def test_a_finished_result_says_when_test_files_could_not_be_imported(objective_rig):
    """Review I-5: a repository most of whose test files cannot be imported here compares almost nothing; say so."""
    files = {**FILES, "tests/test_extra.py": "import no_such_module_for_g6\n\n\ndef test_extra():\n    assert True\n"}
    done = _finish(objective_rig, create(objective_rig, files)["mission_id"], FIXED)
    assert done["status"] == "done", done
    assert "1 of this repository's test files could not be imported here" in json.dumps(done["result"])


def test_a_test_in_a_module_that_skipped_itself_is_not_called_not_a_test():
    """Review M-3."""
    suite = {"ran": True, "started": True, "why": "", "collect_errors": [], "collect_skipped": ["tests/test_opt.py"],
             "seconds": 0.1, "outcomes": {"tests/test_a.py::test_ok": "passed"}}
    _, record = coding._counted(["tests/test_a.py::test_ok", "tests/test_opt.py::test_needs_lib"], suite)
    assert record["not_counted"] == [{"test": "tests/test_opt.py::test_needs_lib", "why": "skipped"}]
