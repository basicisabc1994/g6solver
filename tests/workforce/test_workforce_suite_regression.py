# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The bench compares a patch with the whole existing suite (ECHA Stage A, 2026-10-09, founder decision (a)).

Found by the Stage A baseline: G6's patch check ran only the top-level ``def test_*`` functions, so on a repository whose
tests are mostly class-based (mistune: 3 of 1,122 visible) a patch that broke 10 class-based tests closed as
qualified-draft. Pinned here: when a patch is applied the bench runs the repository's whole ``tests/`` once, compares
every test that passed on the unpatched copy with the patched one, and the patch passes only if none of them stopped
passing. A test that already failed, was skipped, expected to fail or errored on the unpatched copy is not a regression.
When the baseline suite cannot run the comparison is unmeasured and said so, and the named-test result stands as before.
"""
from __future__ import annotations

import pytest
from g6.workforce import truth
from g6.workforce import bench

CALC = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
#: Fixes add and leaves ``sub`` right for the named test (5 - 3) while breaking it for a negative result.
FIXED_BREAKS_NEGATIVE = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return abs(a - b)\n"
TESTS = (
    "import pytest\nfrom calc import add, sub\n\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n\n\n"
    "def test_sub():\n    assert sub(5, 3) == 2\n\n\n"
    "class TestNegatives:\n"
    "    def test_negative(self):\n        assert sub(1, 3) == -2\n\n"
    "    def test_known_bad(self):\n        assert sub(1, 1) == 99\n\n"
    "    @pytest.mark.skip(reason='not yet')\n    def test_skipped(self):\n        assert sub(1, 3) == 0\n\n\n"
    "@pytest.mark.parametrize('a,b', [(3, 1), (1, 3)])\ndef test_param(a, b):\n    assert sub(a, b) == a - b\n"
)
NAMED = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_sub"]


def _make(root, name="calc", venture="v1", tests=TESTS):
    repo = root / bench.REPOS / venture / name
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(tests, encoding="utf-8")
    return repo


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path))
    bench.SUITE_BASELINES.clear()
    _make(tmp_path)
    return tmp_path


def _run(**params):
    return bench.run("run_tests", {"repo": "calc", "tests": NAMED, **params}, venture="v1")


def test_a_patch_that_breaks_only_a_class_based_test_does_not_pass(root):
    """The Stage A false accept: the named top-level tests pass, a class-based test the visible list never held breaks."""
    found = _run(files={"calc.py": FIXED_BREAKS_NEGATIVE})["result"]
    assert found["tests"] == {NAMED[0]: True, NAMED[1]: True}                 # every named test passes ...
    assert found["passed"] is False                                           # ... and the patch still does not
    assert found["suite"]["status"] == "fail"
    assert "tests/test_calc.py::TestNegatives::test_negative" in found["suite"]["tests"]
    assert "tests/test_calc.py::test_param[1-3]" in found["suite"]["tests"]     # a parametrized case is a test too


def test_a_patch_that_breaks_nothing_passes_and_says_how_much_it_compared(root):
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["passed"] is True
    assert found["suite"] == {"status": "pass", "compared": 4, "broken": 0, "tests": []}
    # test_add fails on the unpatched copy and is fixed; test_sub, test_negative and both parametrized cases passed
    # before: those four are what the patch is compared on. The known-bad and the skipped test are not.


def test_a_test_that_already_failed_or_was_skipped_is_not_a_regression(root):
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["passed"] is True
    assert not [t for t in found["suite"]["tests"] if "known_bad" in t or "skipped" in t]


def test_the_unpatched_run_does_no_suite_comparison(root):
    found = _run()["result"]
    assert "suite" not in found and found["tests"] == {NAMED[0]: False, NAMED[1]: True}


def test_the_baseline_suite_runs_once_per_unchanged_repository(root, monkeypatch):
    # (a patch that breaks something is confirmed by one more run of the patched copy: review I-3)
    calls = []
    real = truth.run_suite

    def counted(repo, timeout=None):
        calls.append(str(repo))
        return real(repo, timeout)

    monkeypatch.setattr(truth, "run_suite", counted)
    _run(files={"calc.py": FIXED})
    _run(files={"calc.py": FIXED_BREAKS_NEGATIVE})
    originals = [c for c in calls if "g6_baseline_" in c]
    assert len(originals) == 1, calls          # the unpatched repository once, in a scratch copy; each patched copy is its own run
    assert len(calls) == 4                     # baseline, one clean patch, and one broken patch run twice


def test_a_changed_repository_gets_a_new_baseline(root, monkeypatch):
    first = _run(files={"calc.py": FIXED})["result"]["suite"]["compared"]
    (root / bench.REPOS / "v1" / "calc" / "tests" / "test_more.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8")
    assert _run(files={"calc.py": FIXED})["result"]["suite"]["compared"] == first + 1


def test_a_baseline_that_cannot_run_is_unmeasured_and_changes_nothing_else(root, monkeypatch):
    real = truth.run_suite

    def baseline_cannot_start(repo, timeout=None):
        if "g6_baseline_" in str(repo):
            return {"ran": False, "started": False, "why": "the test runner could not start", "outcomes": {},
                    "collect_errors": [], "seconds": 0.0}
        return real(repo, timeout)          # the patched copy would run, if it were asked

    monkeypatch.setattr(truth, "run_suite", baseline_cannot_start)
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["passed"] is True                                            # the named tests decide, as before
    assert found["suite"] == {"status": "not_run", "reason": "the test runner could not start"}


def test_two_threads_asking_for_the_same_baseline_run_it_once(root, monkeypatch):
    """The workforce's checks and its learning drain run at once in one process; the cache is shared."""
    import threading
    import time

    calls, real = [], truth.run_suite

    def slow(repo, timeout=None):
        calls.append(str(repo))
        time.sleep(0.5)
        return real(repo, timeout)

    monkeypatch.setattr(truth, "run_suite", slow)
    source, got = root / bench.REPOS / "v1" / "calc", []
    threads = [threading.Thread(target=lambda: got.append(bench._suite_baseline(source))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1 and got[0] is got[1], calls

def test_a_patch_whose_suite_does_not_finish_is_an_executed_failure(root, monkeypatch):
    real = truth.run_suite

    def slow_after_patch(repo, timeout=None):
        if "g6_bench_" in str(repo):
            return {"ran": False, "started": True, "why": "the tests did not finish: timed out after 240 s",
                    "outcomes": {}, "collect_errors": [], "seconds": 240.0}
        return real(repo, timeout)

    monkeypatch.setattr(truth, "run_suite", slow_after_patch)
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["passed"] is False
    assert found["suite"]["status"] == "fail" and "did not finish" in found["suite"]["reason"]


def test_a_patch_that_stops_the_suite_from_collecting_breaks_what_it_could_not_load(root):
    broken = FIXED + "\nraise RuntimeError('import time failure')\n"
    found = _run(files={"calc.py": broken})["result"]
    assert found["passed"] is False and found["suite"]["status"] == "fail"


def test_a_patch_that_breaks_the_import_of_one_test_file_breaks_the_tests_that_file_held(root):
    """The file is not collected, so its tests are missing from the patched run: missing is not passing."""
    repo = root / bench.REPOS / "v1" / "calc"
    (repo / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_helper.py").write_text("from helper import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n",
                                                   encoding="utf-8")
    found = _run(files={"calc.py": FIXED, "helper.py": "raise RuntimeError('broken at import')\n"})["result"]
    assert found["tests"] == {NAMED[0]: True, NAMED[1]: True}              # the named tests are untouched ...
    assert found["passed"] is False and found["suite"]["status"] == "fail"  # ... the other file's test is gone
    assert found["suite"]["tests"] == ["tests/test_helper.py::test_value"]


def test_many_broken_tests_are_counted_in_full_and_listed_in_part(root):
    many = (root / bench.REPOS / "v1" / "calc" / "tests" / "test_many.py")
    many.write_text("import pytest\nfrom calc import sub\n\n\n@pytest.mark.parametrize('n', range(40))\n"
                    "def test_many(n):\n    assert sub(n, 0) == n\n", encoding="utf-8")
    found = _run(files={"calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return -1\n"})["result"]
    assert found["suite"]["broken"] >= 40
    assert len(found["suite"]["tests"]) == bench.MAX_BROKEN_SHOWN


def test_a_repository_without_a_tests_directory_has_no_suite_comparison(tmp_path, monkeypatch):
    monkeypatch.setenv(bench.ENV_DB_DIR, str(tmp_path))
    repo = tmp_path / bench.REPOS / "v1" / "flat"
    repo.mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8")
    (repo / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                                       encoding="utf-8")
    found = bench.run("run_tests", {"repo": "flat", "tests": ["test_calc.py::test_add"],
                                    "files": {"calc.py": FIXED}}, venture="v1")["result"]
    assert found["passed"] is True and found["suite"]["status"] == "not_run"


def _ran(outcomes, seconds=1.0, collect_errors=()):
    return {"ran": True, "started": True, "why": "", "outcomes": dict(outcomes), "collect_errors": list(collect_errors),
            "collect_skipped": [], "seconds": seconds}


def _not_ran(started, why="the test runner could not start"):
    return {"ran": False, "started": started, "why": why, "outcomes": {}, "collect_errors": [], "collect_skipped": [],
            "seconds": 0.0}


def test_a_correct_patch_is_not_failed_by_test_ids_that_carry_the_path_of_the_copy(root):
    """Review I-3: a parametrize over files beside the test names the file by absolute path, and the baseline and the
    patched suite run in different folders."""
    repo = root / bench.REPOS / "v1" / "calc"
    (repo / "tests" / "data").mkdir()
    (repo / "tests" / "data" / "one.txt").write_text("1", encoding="utf-8")
    (repo / "tests" / "test_paths.py").write_text(
        "import glob\nimport os\n\nimport pytest\n\nHERE = os.path.dirname(os.path.abspath(__file__))\n"
        "FILES = sorted(glob.glob(os.path.join(HERE, 'data', '*.txt')))\n\n\n@pytest.mark.parametrize('path', FILES)\n"
        "def test_file(path):\n    assert os.path.exists(path)\n", encoding="utf-8")
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["passed"] is True and found["suite"]["status"] == "pass", found["suite"]


def test_a_test_that_fails_once_and_passes_again_is_not_held_against_the_patch(root, monkeypatch):
    """Review I-3 (flaky): a broken test is confirmed by running the patched copy once more."""
    calls = []
    base = {"tests/t.py::test_a": "passed", "tests/t.py::test_b": "passed"}

    def run(repo, timeout=None):
        calls.append(str(repo))
        if "g6_baseline_" in str(repo):
            return _ran(base)
        return _ran({**base, "tests/t.py::test_b": "failed"} if sum("g6_bench_" in c for c in calls) == 1 else base)

    monkeypatch.setattr(truth, "run_suite", run)
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["suite"]["status"] == "pass" and sum("g6_bench_" in c for c in calls) == 2


def test_a_test_that_fails_both_times_stays_broken(root, monkeypatch):
    base = {"tests/t.py::test_a": "passed", "tests/t.py::test_b": "passed"}
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: _ran(
        base if "g6_baseline_" in str(repo) else {**base, "tests/t.py::test_b": "failed"}))
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["suite"] == {"status": "fail", "compared": 2, "broken": 1, "tests": ["tests/t.py::test_b"]}


def test_the_patched_suite_gets_the_time_the_unpatched_one_needed_twice_over(root, monkeypatch):
    """Review I-3: a baseline that took 200 s of the 240 must not make a slightly slower patched run a failure."""
    limits = {}

    def run(repo, timeout=None):
        limits["baseline" if "g6_baseline_" in str(repo) else "patched"] = timeout
        return _ran({"tests/t.py::test_a": "passed"}, seconds=200.0)

    monkeypatch.setattr(truth, "run_suite", run)
    _run(files={"calc.py": FIXED})
    assert limits["patched"] >= 400, limits


def test_a_baseline_with_nothing_that_passed_has_nothing_to_compare(root, monkeypatch):
    """Review I-5: compared 0 is not a pass."""
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: _ran({"tests/t.py::test_a": "failed",
                                                                            "tests/t.py::test_b": "skipped"}))
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["passed"] is True and found["suite"]["status"] == "not_run"
    assert "passed" in found["suite"]["reason"]


def test_test_files_the_baseline_could_not_import_are_counted_in_the_record(root, monkeypatch):
    """Review I-5: most files failing to import (the interpreter lacks the repository's dependencies) must be visible."""
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: _ran(
        {"tests/t.py::test_a": "passed"}, collect_errors=["tests/u.py", "tests/v.py"]))
    suite = _run(files={"calc.py": FIXED})["result"]["suite"]
    assert suite["status"] == "pass" and suite["not_collected"] == 2


def test_a_patched_runner_that_never_started_is_unmeasured_not_a_failure(root, monkeypatch):
    base = {"tests/t.py::test_a": "passed"}
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: _ran(base) if "g6_baseline_" in str(repo)
                        else _not_ran(started=False))
    found = _run(files={"calc.py": FIXED})["result"]
    assert found["passed"] is True and found["suite"]["status"] == "not_run"


def test_a_baseline_whose_runner_never_started_is_not_remembered(root, monkeypatch):
    calls = []
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: calls.append(1) or _not_ran(started=False))
    source = root / bench.REPOS / "v1" / "calc"
    bench._suite_baseline(source)
    bench._suite_baseline(source)
    assert len(calls) == 2


def test_a_baseline_that_timed_out_is_remembered_for_a_while_and_then_tried_again(root, monkeypatch):
    calls = []
    monkeypatch.setattr(truth, "run_suite", lambda repo, timeout=None: calls.append(1) or _not_ran(
        started=True, why="the tests did not finish: timed out after 240 s"))
    source = root / bench.REPOS / "v1" / "calc"
    bench._suite_baseline(source)
    bench._suite_baseline(source)
    assert len(calls) == 1                       # one transient timeout does not cost a run per check
    monkeypatch.setattr(bench, "NOT_RAN_TTL_S", 0)
    bench._suite_baseline(source)
    assert len(calls) == 2                       # ... and is not held against the repository for ever


def test_two_repositories_do_not_wait_for_each_others_baseline(root, monkeypatch):
    """Review I-7: one global lock held for a whole suite run blocked every other venture's check. Both runs must be
    inside the suite at once (a barrier, not a stopwatch, so a loaded machine cannot fail it)."""
    import threading

    (root / bench.REPOS / "v1" / "other").mkdir()
    (root / bench.REPOS / "v1" / "other" / "x.py").write_text("x = 1\n", encoding="utf-8")
    real = truth.run_suite
    meet = threading.Barrier(2, timeout=30)
    met = []

    def slow(repo, timeout=None):
        try:
            meet.wait()  # a lock shared by both repositories keeps the second out until this one gives up
            met.append(repo)
        except threading.BrokenBarrierError:
            pass
        return real(repo, timeout)

    monkeypatch.setattr(truth, "run_suite", slow)
    sources = [root / bench.REPOS / "v1" / "calc", root / bench.REPOS / "v1" / "other"]
    threads = [threading.Thread(target=bench._suite_baseline, args=(s,)) for s in sources]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(met) == 2


def test_the_answer_shown_to_the_worker_keeps_the_named_tests_whole_however_many_suite_ids_there_are():
    """Review I-4: the shown answer is cut at 600 characters and parsed again for the focused brief; an answer that no
    longer parses loses the named failing tests along with the suite's."""
    import json

    from g6.workforce import rungs

    ids = [f"tests/test_markdown_roundtrip.py::TestMarkdownRendererRoundTrip::test_case_number_{n:03d}" for n in range(12)]
    answer = {"passed": False, "tests": {"tests/test_calc.py::test_add": True},
              "suite": {"status": "fail", "compared": 900, "broken": 12, "tests": ids}}
    shown = rungs.shown_answer(answer)
    assert len(shown) <= rungs._ANSWER_CHARS
    parsed = json.loads(shown)                    # it still parses ...
    assert parsed["tests"] == {"tests/test_calc.py::test_add": True} and parsed["suite"]["tests"]
    assert parsed["suite"]["more"] == 12 - len(parsed["suite"]["tests"])   # ... and says how many it left out
