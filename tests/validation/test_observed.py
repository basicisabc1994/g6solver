# SPDX-License-Identifier: Apache-2.0
"""A result is known only from what was observed (replay cohort, repair 1, review 1's findings 5, 6, 8 and 10; class
``failure_read_as_success``, met in every round of this harness).

One reader turns a container call's result into three states, ran and passed, ran and failed, and unknown (``box_mcp.seen``),
and one reader does the same for a test report (``box_mcp.tests_seen``). A missing exit status, a non-zero return from the call
itself, a missing report, a missing test result or a timeout with no result is unknown: never a pass and never a failure. Every
consumer reads through them: the patch-size probe (unknown is never clean), the four cohort controls (a control holds only on
observed results, and an unknown control excludes the bug, naming why) and the box's call phases (a closed set; anything else
refuses). The inventory test, built from the code, lists every place a container result or a test report is read. Fakes only:
no docker, no model."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(HERE))

import box_mcp as box  # noqa: E402
import cohorts  # noqa: E402
import run as we  # noqa: E402

P, F, U = "passed", "failed", "unknown"


def done(code, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


# --- the reader of a container call --------------------------------------------------------------------------------------------
def answer(output: bytes):
    return output.decode().split("answer=", 1)[1].strip() if b"answer=" in output else None


@pytest.mark.parametrize(("got", "read", "state"), [
    (done(0), None, P),
    (done(1), None, U),                          # a non-zero return from the call itself: docker's, its daemon's or the command's
    (done(125, b"", b"Error response from daemon"), None, U),
    (done(None), None, U),                       # no exit status
    (done(-9), None, U),                         # the client ended by a signal
    (done(True), None, U),                       # not an exit status at all
    (SimpleNamespace(stdout=b"x"), None, U),     # nothing that says how it ended
    (done(0, b"answer=7\n"), answer, P),
    (done(1, b"answer=7\n"), answer, F),         # the command reached its answer, then returned 1: ran and failed
    (done(0, b"nothing\n"), answer, U),          # passed, but its answer never came back
    (done(1, b"nothing\n"), answer, U),
])
def test_one_reader_turns_a_container_calls_result_into_three_states(got, read, state):
    seen = box.seen(got, read)
    assert seen.state == state, seen
    assert (seen.why == "") == (state == P)
    assert seen.answer == ("7" if state != U and read else None)


def body(results, *, exit="1", call=P, report=True, **kw):
    """A test run's body as ``run._oracle_run`` keeps it with pytest's own report: each test's outcome by its node id
    (``tests``), or None when no report came back (replay cohort, repair 3)."""
    return {"call": call, "pytest_exit": exit, "report": report, "tests": results if report else None, **kw}


@pytest.mark.parametrize(("got", "state", "why"), [
    (body({"t1": "passed", "t2": "passed"}, exit="0"), P, ""),
    (body({"t1": "failed", "t2": "passed"}), F, "1 of 2 seen to fail"),
    (body({"t1": "error", "t2": "passed"}, exit="2"), F, "seen to fail"),
    (body({"t1": "skipped", "t2": "passed"}, exit="0"), U, "no result"),           # skipped: not run
    (body({"t2": "passed"}), U, "no result"),                                       # absent from the report
    (body({"::t": "error"}, exit="2"), U, "no result"),                            # a collection error names no test it holds
    (body({}, exit="124", report=False), U, "ran out of time"),                     # a timeout with no result
    (body({}, exit="1", report=False), U, "no test report"),                        # a report that never came back
    (body({"t1": "failed"}, exit=None), U, "no exit status"),
    (body({"t1": "failed"}, call=U, call_why="the call itself returned 125"), U, "returned 125"),
    (body({"t1": "failed"}, call=F), U, "not seen to run"),
    ({"pytest_exit": "1", "tests": {"t1": "failed", "t2": "failed"}}, U, "never recorded"),     # no call recorded
    ({"call": P, "pytest_exit": "1", "report": True, "results": {"t1": "failed", "t2": "failed"}}, U, "no test report"),
    ({"patch_applied": False}, U, "did not apply"),
    (None, U, "never recorded"),
    (["not", "a", "body"], U, "never recorded"),
])
def test_one_reader_turns_a_test_report_into_three_states(got, state, why):
    seen = box.tests_seen(got, ["t1", "t2"])
    assert seen["state"] == state and why in seen["why"], seen
    assert seen["passed"] + seen["failed"] + seen["unknown"] == seen["tests"] == 2


# --- finding 5: the patch's size, now the kept patch's (replay cohort, repair 3) -----------------------------------------------
STEPS_PASSED = "".join(f"{we.STEP} {s}=0\n" for s in we.CAPTURE_STEPS).encode()


def _status_over(tmp_path, capture, found=we.LOOP_CLEAN):
    """A loop's status as ``_run_task`` decides it, from ``found`` and the patch ``keep_final`` kept, its capture answered
    ``capture`` and its copy writing 123 bytes."""
    class Docker:
        def sh(self, *args, **kw):
            return capture

        def __call__(self, *args, **kw):
            Path(args[2]).write_bytes(b"x" * 123)
            return done(0)

    folder = we.c.Archive(tmp_path / "a").start("run", "toy-1")
    read = we.keep_final(SimpleNamespace(docker=Docker()), SimpleNamespace(repo="/w/toy"), "ctr", "base", folder)
    size = (folder.path / "final.patch").stat().st_size
    return we.loop_status(found, size if read else None)


def test_finding_5_a_kept_patch_whose_capture_failed_is_unknown_so_arm_r_is_inconclusive_never_clean(tmp_path):
    result = we.review_once(SimpleNamespace(docker=None, arm="R"), SimpleNamespace(repo="/w/toy"),
                            SimpleNamespace(write_json=lambda *args: None), "ctr", [], "", None, 0, 0,
                            lambda: {"verdict": "none", "test": ""}, base="base")
    assert result["found"] == we.LOOP_CLEAN and "status" not in result         # the loop measures nothing itself
    assert _status_over(tmp_path, done(1, STEPS_PASSED, b"failed\n"), result["found"]) == "inconclusive"


@pytest.mark.parametrize(("got", "status"), [(done(0, STEPS_PASSED), we.LOOP_CLEAN), (done(0, b"0 lines\n"), "inconclusive"),
                                             (done(1, STEPS_PASSED), "inconclusive"), (done(None, STEPS_PASSED), "inconclusive"),
                                             (done(-15, STEPS_PASSED), "inconclusive")])
def test_the_size_is_read_only_from_a_kept_patch_whose_capture_was_seen_to_pass_every_step(tmp_path, got, status):
    assert _status_over(tmp_path, got) == status


# --- findings 6 and 10: the four controls hold only on observed results ----------------------------------------------------------
ORACLE = "sha256:" + "0" * 64
GOOD = {"base": body({"t::bug": "failed", "t::ok": "passed"}), "fixed": body({"t::bug": "passed", "t::ok": "passed"}, exit="0"),
        "gold": body({"t::bug": "passed", "t::ok": "passed"}, exit="0"), "empty": body({"t::bug": "failed", "t::ok": "passed"})}
TRIP = {"holds": True, "status": "finished", "error": None, "kept_read": True, "known_bytes": 10, "known_sha256": "k" * 64,
        "kept_bytes": 10, "kept_sha256": "k" * 64, "same_bytes": True}


def controls(monkeypatch, tmp_path, **bodies):
    """``cohorts.controls`` with each oracle run answered from ``bodies`` (``GOOD``'s by default) and a round trip that holds."""
    task = SimpleNamespace(id="toy-1", test_modules=["test_toy.py"])
    archive = we.c.Archive(tmp_path / "controls")
    given = {**GOOD, **bodies}

    class Docker:
        def __call__(self, *args, **kw):
            return done(0, ORACLE.encode() if args[:2] == ("image", "inspect") else b"gold")

    def oracle(ctx, task, *, patch=None, fix=False, label="", **kw):
        return dict(given[label])

    trip = bodies.pop("trip", TRIP) if "trip" in bodies else TRIP
    monkeypatch.setattr(we, "_oracle_run", oracle)
    monkeypatch.setattr(cohorts, "recorded_tree", lambda *args: "upstream-tree")
    monkeypatch.setattr(cohorts, "round_trip", lambda *args: dict(trip))
    ctx = SimpleNamespace(archive=archive, docker=Docker(), g6=cohorts.g6_for(), variant=we.ROUND3)
    return cohorts.controls(ctx, task, None)


def test_finding_6_an_empty_patch_whose_tests_never_ran_is_not_seen_failing_and_excludes_the_bug(monkeypatch, tmp_path):
    got = controls(monkeypatch, tmp_path, empty={"results": {}, "pytest_exit": None})          # the review's body, as given
    assert got["failed"] == "empty patch" and "never recorded" in got["why"], got
    assert cohorts.admit(got, {"task_image_id": ORACLE, "oracle_image_id": ORACLE}).startswith("empty patch: ")
    got = controls(monkeypatch, tmp_path / "2", empty=body({}, exit=None, call=U, report=False,      # as the oracle run keeps it
                                                           call_why="its answer's own line did not come back (exit 0)"))
    assert got["failed"] == "empty patch" and "not seen to run" in got["why"], got


def test_finding_10_a_base_with_no_result_and_no_timeout_is_not_a_seen_failure_and_excludes_the_bug(monkeypatch, tmp_path):
    got = controls(monkeypatch, tmp_path, base={"results": {}, "pytest_exit": None})
    assert got["failed"] == "base" and "never recorded" in got["why"], got
    got = controls(monkeypatch, tmp_path / "2", base=body({}, exit=None, call=U, call_why="no exit status came back"))
    assert got["failed"] == "base" and "not seen to run" in got["why"], got


@pytest.mark.parametrize(("bodies", "failed", "why"), [
    ({"base": body({}, exit="124", report=False)}, "base", "ran out of time"),         # tenacity-660's base, as recorded
    ({"base": body({}, report=False)}, "base", "no test report"),
    ({"base": body({"t::bug": "failed"}, call=U, call_why="the call itself returned 125")}, "base", "returned 125"),
    ({"base": body({"::t": "error", "t::ok": "passed"}, exit="2")}, "base", "no result"),     # its module failed to collect
    ({"fixed": body({"t::bug": "passed", "t::ok": "passed"}, exit=None)}, "base", "the fix was not seen to pass"),
    ({"fixed": body({"t::bug": "failed", "t::ok": "passed"})}, "base", "no test fails on the parent's code"),
    ({"gold": body({"t::bug": "skipped", "t::ok": "passed"}, exit="0")}, "gold patch", "no result"),
    ({"gold": body({"t::bug": "passed", "t::ok": "passed"}, exit="0", report=False)}, "gold patch", "no test report"),
    ({"gold": {"patch_applied": False, "apply_error": "does not apply"}}, "gold patch", "did not apply"),
    ({"empty": body({}, exit="124", report=False)}, "empty patch", "ran out of time"),
    ({"empty": body({"t::bug": "passed", "t::ok": "passed"}, exit="0")}, "empty patch", "not seen to fail"),
    ({"trip": {**TRIP, "kept_read": False, "kept_sha256": None}}, "round trip", "was not read"),
    ({"trip": {**TRIP, "kept_sha256": "x" * 64}}, "round trip", "byte for byte"),
    ({"trip": {"holds": False, "status": None, "error": "RuntimeError: the known patch could not be copied out"}}, "round trip",
     "could not be copied out"),
])
def test_a_control_holds_only_on_observed_results_and_an_unknown_one_excludes_the_bug_naming_why(monkeypatch, tmp_path, bodies,
                                                                                                    failed, why):
    got = controls(monkeypatch, tmp_path, **bodies)
    assert got["failed"] == failed and why in got["why"], got
    assert cohorts.admit(got, {"task_image_id": ORACLE, "oracle_image_id": ORACLE}).startswith(failed + ": ")


def test_a_bug_whose_four_controls_were_observed_to_hold_is_admitted(monkeypatch, tmp_path):
    got = controls(monkeypatch, tmp_path)
    assert got["failed"] == "" and got["why"] == "", got
    assert got["validate"]["base"]["tests"] == {"t::bug": "failed"}                     # the outcome as observed, kept
    assert cohorts.admit(got, {"task_image_id": ORACLE, "oracle_image_id": ORACLE}) == ""


# --- the oracle run keeps what it observed -------------------------------------------------------------------------------------
JUNIT = (b'<testsuites><testsuite><testcase classname="t" name="bug"><failure/></testcase>'
         b'<testcase classname="t" name="ok"/></testsuite></testsuites>')


def plugin_lines(*entries: dict, finished: int | None = 1) -> bytes:
    """pytest's own report as ``run.REPORT_PLUGIN`` writes it: ``entries``, then the session's last line (``finished``)."""
    import json as _json
    rows = [*entries, *([{"finished": finished}] if finished is not None else [])]
    return b"".join(_json.dumps(r).encode() + b"\n" for r in rows)


def phase(node: str, when: str, outcome: str, junit: str | None = None) -> dict:
    return {"node": node, "when": when, "outcome": outcome, "junit": junit}


#: The same two tests as ``JUNIT``, as pytest's own report gives them: by node id, each with its junit key for display.
PLUGIN = plugin_lines(phase("t.py::bug", "setup", "passed", "t::bug"), phase("t.py::bug", "call", "failed", "t::bug"),
                      phase("t.py::bug", "teardown", "passed", "t::bug"), phase("t.py::ok", "call", "passed", "t::ok"))


class OracleDocker:
    """A check container: the pytest call answers ``said`` with ``code``; the junit report's copy writes ``report``, and pytest's
    own report's copy ``plugin`` (each None: its copy fails)."""

    def __init__(self, said: bytes, code, report: bytes | None, plugin: bytes | None = None) -> None:
        self.said, self.code, self.report, self.plugin = said, code, report, plugin

    def __call__(self, *args, input=None, timeout=None, env=None):
        for kept, at in ((self.report, we.JUNIT_IN), (self.plugin, we.REPORT_IN)):
            if args[0] == "cp" and args[1].endswith(at):
                if kept is None:
                    return done(1, b"", b"Error: no such file")
                Path(args[2]).write_bytes(kept)
        return done(0)

    def sh(self, ctr, script, *a, **kw):
        return done(self.code, self.said) if "pytest" in script else done(0)


@pytest.mark.parametrize(("said", "code", "report", "call", "exit", "seen"), [
    (b"exit=1\n", 0, JUNIT, P, "1", F),
    (b"exit=1\n", 137, JUNIT, F, "1", U),             # the call itself returned 137 after its exit line: not observed
    (b"", 125, None, U, None, U),                     # docker's own failure: no exit line, no report
    (b"exit=124\n", 0, None, P, "124", U),            # a timeout with no result
    (b"exit=1\n", 0, None, P, "1", U),                # a report that never came back
    (b"exit=1\n", 0, b"<testsuites><broken", P, "1", U),     # a report that cannot be read is no report
])
def test_the_oracle_run_keeps_its_calls_state_its_exit_and_whether_its_report_came_back(tmp_path, said, code, report, call, exit,
                                                                                       seen):
    task = SimpleNamespace(id="toy-1", repo="/w/toy", test_modules=["t.py"])
    plugin = None if report is None else PLUGIN if b"broken" not in report else b'{"node": "t.py::bug", "wh'
    ctx = SimpleNamespace(docker=OracleDocker(said, code, report, plugin), g6={"commit": "0" * 40}, variant=we.ROUND3)
    got = we._oracle_run(ctx, task, label="base", report=True)
    assert (got["call"], got["pytest_exit"], got["report"]) == (call, exit, report is not None and b"broken" not in report), got
    assert box.tests_seen(got, ["t.py::bug", "t.py::ok"])["state"] == seen
    own = plugin == PLUGIN
    assert we.observed(got, ["t.py::bug"]) == {"call": call, "call_why": got["call_why"], "pytest_exit": exit,
                                               "report": got["report"], "tests": {"t.py::bug": "failed"} if own else None,
                                               "junit": {"t.py::bug": "t::bug"} if own else {},
                                               "collect_errors": [] if own else None}
    junit_only = we._oracle_run(SimpleNamespace(docker=OracleDocker(said, code, report), g6={"commit": "0" * 40},
                                                variant=we.ROUND3), task, label="base")
    assert box.tests_seen(junit_only, ["t::bug", "t::ok"])["state"] == U, "a junit key gave a test its identity"


def test_the_round_trip_compares_bytes_it_read_never_a_capture_that_failed(tmp_path):
    class CaptureDocker:
        def __init__(self, capture_code, copies):
            self.capture_code, self.copies = capture_code, copies

        def sh(self, *a, **kw):
            return done(self.capture_code, STEPS_PASSED if self.capture_code == 0 else b"")

        def __call__(self, *args, **kw):
            if args[0] == "cp" and self.copies:
                Path(args[2]).write_bytes(b"partial")
                return done(0)
            return done(1)

    for code, copies, read, kept in ((0, True, True, b"partial"), (1, True, False, b""), (0, False, False, b""),
                                     (None, True, False, b"")):
        run = we.c.Archive(tmp_path / f"a{code}{copies}").start("run", "toy-1")
        ctx = SimpleNamespace(docker=CaptureDocker(code, copies))
        assert we.keep_final(ctx, SimpleNamespace(repo="/w/toy"), "agent", "base", run) is read
        assert (run.path / "final.patch").read_bytes() == kept


# --- a base's collection error, by the coordinator's ruling (2026-11-06) -----------------------------------------------------------
#: A base whose run errors collecting the module that holds the fail-to-pass test (pytest's own report: a failed collection's node
#: id), and a gold patch's run, on the same image ids, whose own report holds that test, by its node id below that module, passing.
#: Each test is its node id (replay cohort, repair 3); junit's rendering of the collection error is no test of the report's.
NEW, OLD = "tests/test_new.py::test_bug", "tests/test_old.py::test_ok"
ERRED = body({OLD: "passed"}, exit="2", junit={OLD: "tests.test_old::test_ok"}, collect_errors=["tests/test_new.py"])
GOLD_RUN = body({NEW: "passed", OLD: "passed"}, exit="0", junit={NEW: "tests.test_new::test_bug", OLD: "tests.test_old::test_ok"},
                collect_errors=[])


def test_a_base_collection_error_of_the_fail_to_pass_tests_module_is_seen_failing_when_the_gold_run_saw_it_pass():
    got = box.tests_seen(ERRED, [NEW], seen_passing_in=GOLD_RUN, fail_to_pass=[NEW])
    assert got["state"] == F and got["failed"] == 1, got
    assert box.tests_seen(ERRED, [NEW])["state"] == U, "without the gold run's report, a missing result stays unknown"


@pytest.mark.parametrize(("base", "gold", "why"), [
    ({**ERRED, "collect_errors": ["tests/test_other.py"]}, GOLD_RUN, "a module that holds no fail-to-pass test"),
    (ERRED, {**GOLD_RUN, "tests": {NEW: "error", OLD: "passed"}}, "the gold run errors too"),
    (ERRED, {**GOLD_RUN, "tests": {OLD: "passed"}}, "the gold run has no result for it"),
    (ERRED, {**GOLD_RUN, "tests": None, "results": {"tests.test_new::test_bug": "passed"}},
     "the gold run kept only junit's report: no test of it has an identity"),
    ({k: v for k, v in ERRED.items() if k != "collect_errors"}, GOLD_RUN, "the base's own report names no collection error: "
                                                                          "only its junit rendering does"),
    ({**ERRED, "collect_errors": ["tests/test_new"]}, GOLD_RUN, "an error whose node id is not the module the test is below"),
    ({**ERRED, "collect_errors": "tests/test_new.py"}, GOLD_RUN, "a report that is not a list of node ids"),
    ({**ERRED, "collect_errors": ["tests/test_old.py"]}, GOLD_RUN, "an error in the module of a pass-to-pass test only"),
])
def test_a_collection_error_stays_unknown_unless_pytests_own_report_puts_the_test_below_it_and_the_gold_run_saw_it_pass(base,
                                                                                                                       gold, why):
    assert box.tests_seen(base, [NEW], seen_passing_in=gold, fail_to_pass=[NEW])["state"] == U, why


def _erred_record(gold_images=None, empty=None):
    ids = {"task_image_id": ORACLE, "oracle_image_id": ORACLE}
    lists = {"fail_to_pass": [NEW], "pass_to_pass": [OLD]}
    return {"failed": "", "why": "",
            "validate": {"images": dict(ids), **lists, "base": we.observed(ERRED, [NEW]), "fixed": we.observed(GOLD_RUN, [NEW, OLD])},
            "selfcheck": {"images": dict(gold_images or ids), "gold": we.observed(GOLD_RUN, [NEW, OLD]),
                          "empty": we.observed(empty or ERRED, [NEW, OLD])},
            "round_trip": {**TRIP, "images": dict(ids)}, "images_after": dict(ids)}


def test_the_base_control_reads_the_gold_run_of_the_same_image_ids_and_the_empty_patch_the_same_way():
    ids = {"task_image_id": ORACLE, "oracle_image_id": ORACLE}
    record = _erred_record(empty=body({NEW: "failed", OLD: "passed"}))
    assert cohorts.control_why(record, "base") == "" and cohorts.admit(record, ids) == ""
    other = {"task_image_id": ORACLE, "oracle_image_id": "sha256:" + "9" * 64}
    assert cohorts.control_why(_erred_record(gold_images=other, empty=body({NEW: "failed", OLD: "passed"})), "base") != ""
    record = _erred_record()                   # the empty patch's run has the same collection error: by the coordinator's
    assert cohorts.control_why(record, "base") == ""      # Option B (2026-11-07), seen failing by the same rule as the base
    assert cohorts.control_why(record, "empty patch") == "" and cohorts.admit(record, ids) == ""


#: Each case above, as a record whose base and empty patch both ran into it (the empty patch is the base's code), with the gold run
#: on the same two image ids: (base or empty body, gold body).
CASES = {"seen failing": (ERRED, GOLD_RUN),
         "a module that holds no fail-to-pass test": ({**ERRED, "collect_errors": ["tests/test_other.py"]}, GOLD_RUN),
         "the gold run errors too": (ERRED, {**GOLD_RUN, "tests": {NEW: "error", OLD: "passed"}}),
         "the gold run has no result for it": (ERRED, {**GOLD_RUN, "tests": {OLD: "passed"}}),
         "the gold run kept only junit's report": (ERRED, {**GOLD_RUN, "tests": None,
                                                           "results": {"tests.test_new::test_bug": "passed"}}),
         "only junit's rendering names the error": ({k: v for k, v in ERRED.items() if k != "collect_errors"}, GOLD_RUN),
         "an error node id that is not the test's module": ({**ERRED, "collect_errors": ["tests/test_new"]}, GOLD_RUN),
         "a report that is not a list of node ids": ({**ERRED, "collect_errors": "tests/test_new.py"}, GOLD_RUN),
         "an error in the module of a pass-to-pass test only": ({**ERRED, "collect_errors": ["tests/test_old.py"]}, GOLD_RUN)}


def _case_record(run, gold):
    """A record whose base and empty patch both ran as ``run``, and whose gold patch ran as ``gold``, all on one pair of ids."""
    ids = {"task_image_id": ORACLE, "oracle_image_id": ORACLE}
    lists = {"fail_to_pass": [NEW], "pass_to_pass": [OLD]}
    return {"failed": "", "why": "",
            "validate": {"images": dict(ids), **lists, "base": we.observed(run, [NEW]), "fixed": we.observed(GOLD_RUN, [NEW, OLD])},
            "selfcheck": {"images": dict(ids), "gold": we.observed(gold, [NEW, OLD]), "empty": we.observed(run, [NEW, OLD])},
            "round_trip": {**TRIP, "images": dict(ids)}, "images_after": dict(ids)}


def test_the_empty_patch_with_a_collection_error_of_the_fail_to_pass_tests_module_and_a_passing_gold_run_counts_as_failing():
    record = _case_record(*CASES["seen failing"])
    assert cohorts.control_why(record, "empty patch") == "", cohorts.control_why(record, "empty patch")
    assert cohorts.admit(record, {"task_image_id": ORACLE, "oracle_image_id": ORACLE}) == ""
    seen = cohorts.summary(record)["selfcheck"]["empty"]
    assert seen["state"] == F and seen["failed"] == 1 and seen["unknown"] == 0, seen      # OLD passed, NEW seen failing


@pytest.mark.parametrize("case", [c for c in CASES if c != "seen failing"])
def test_a_collection_error_the_one_rule_does_not_tie_to_the_test_leaves_the_empty_patch_not_seen_failing(case):
    record = _case_record(*CASES[case])
    assert cohorts.control_why(record, "empty patch").startswith("not seen to fail"), case
    assert cohorts.admit(record, {"task_image_id": ORACLE, "oracle_image_id": ORACLE}).startswith("base: "), case   # the same evidence


@pytest.mark.parametrize("case", CASES)
def test_the_base_and_the_empty_patch_decide_a_seen_failure_by_one_rule_on_the_same_evidence(case):
    record = _case_record(*CASES[case])
    base, empty = cohorts.control_why(record, "base"), cohorts.control_why(record, "empty patch")
    assert (base == "") == (empty == "") == (case == "seen failing"), (case, base, empty)
    run, gold = CASES[case]
    assert box.seen_failing(NEW, run, gold) is (case == "seen failing")
    assert box.seen_failing(NEW, run) is False                # without the gold run, a missing result is never a failure


def test_the_controls_judge_the_base_once_the_gold_patch_has_run(monkeypatch, tmp_path):
    got = controls(monkeypatch, tmp_path, base=ERRED, fixed=GOLD_RUN, gold=GOLD_RUN, empty=body({NEW: "failed", OLD: "passed"}))
    assert got["failed"] == "" and cohorts.admit(got, {"task_image_id": ORACLE, "oracle_image_id": ORACLE}) == "", got
    got = controls(monkeypatch, tmp_path / "2", base=ERRED, fixed=GOLD_RUN, gold=GOLD_RUN, empty=ERRED)
    assert got["failed"] == "", got             # the empty patch's collection error, by the same rule as the base's


# --- pytest's own report, which the controls' oracle runs keep ---------------------------------------------------------------------
class ReportDocker(OracleDocker):
    """A check container as ``OracleDocker``, which also takes the report plugin's file and hands back its report."""

    def __init__(self, report_lines: bytes | None, **kw) -> None:
        super().__init__(b"exit=2\n", 0, JUNIT)
        self.report_lines, self.scripts, self.written = report_lines, [], {}

    def __call__(self, *args, input=None, timeout=None, env=None):
        if args[0] == "exec" and input is not None:
            self.written[args[-1].split('cat > "', 1)[1].split('"', 1)[0]] = input
            return done(0)
        if args[0] == "cp" and args[1].endswith(we.REPORT_IN):
            if self.report_lines is None:
                return done(1, b"", b"Error: no such file")
            Path(args[2]).write_bytes(self.report_lines)
            return done(0)
        return super().__call__(*args, input=input, timeout=timeout, env=env)

    def sh(self, ctr, script, *a, **kw):
        self.scripts.append(script)
        return super().sh(ctr, script, *a, **kw)


def test_an_oracle_run_asked_for_pytests_own_report_keeps_each_tests_outcome_by_node_id_and_each_failed_collection(tmp_path):
    lines = plugin_lines({"collect_error": "tests/test_new.py"}, phase(OLD, "setup", "passed", "tests.test_old::test_ok"),
                         phase(OLD, "call", "passed", "tests.test_old::test_ok"),
                         phase(OLD, "teardown", "passed", "tests.test_old::test_ok"), finished=2)
    task = SimpleNamespace(id="toy-1", repo="/w/toy", test_modules=["t.py"])
    docker = ReportDocker(lines)
    got = we._oracle_run(SimpleNamespace(docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3), task, label="base", report=True)
    assert got["collect_errors"] == ["tests/test_new.py"] and got["tests"] == {OLD: "passed"}
    assert got["junit"] == {OLD: "tests.test_old::test_ok"} and "nodes" not in got
    assert docker.written[we.PLUGIN_AT] == we.REPORT_PLUGIN.encode() and "-p we_report" in docker.scripts[-1]
    assert we.observed(got, [NEW, OLD])["tests"] == {OLD: "passed"}
    lost = we._oracle_run(SimpleNamespace(docker=ReportDocker(None), g6={"commit": "0" * 40}, variant=we.ROUND3), task,
                          label="base", report=True)
    assert lost["collect_errors"] is None and lost["tests"] is None and lost["junit"] is None   # not recorded: no identities
    assert box.tests_seen(lost, [OLD])["state"] == U
    plain = we._oracle_run(SimpleNamespace(docker=OracleDocker(b"exit=1\n", 0, JUNIT), g6={"commit": "0" * 40},
                                           variant=we.ROUND3), task, label="base")
    assert "tests" not in plain and "collect_errors" not in plain            # the study's own runs are as they were


KINDS = '''import pytest


@pytest.fixture
def broken_setup():
    raise RuntimeError("setup")


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown")


def test_pass():
    pass


def test_fail():
    assert 0


def test_setup_error(broken_setup):
    pass


def test_teardown_error(broken_teardown):
    pass


def test_fail_and_teardown_error(broken_teardown):
    assert 0


def test_skip():
    pytest.skip("no")


@pytest.mark.skipif(True, reason="no")
def test_skip_mark():
    pass


@pytest.mark.xfail(reason="known")
def test_xfail():
    assert 0


@pytest.mark.xfail(reason="known", strict=True)
def test_xpass_strict():
    pass


@pytest.mark.xfail(reason="known")
def test_xpass():
    pass
'''


def _plugin_run(project: Path, plugins: Path, tmp_path: Path, *args: str):
    import os
    import subprocess
    report, junit = tmp_path / "report.jsonl", tmp_path / "junit.xml"
    done_ = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "we_report", "-q", f"--junitxml={junit}",
                            "-o", "junit_family=xunit2", *args], cwd=project, capture_output=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(plugins), "WE_REPORT": str(report), "PYTHONDONTWRITEBYTECODE": "1"})
    return done_, report, junit


def test_the_report_plugin_gives_pytests_own_node_ids_and_the_junit_key_pytest_itself_computes(tmp_path):
    """Each test's outcome by its node id, as junit takes it, with its junit key beside it for display (replay cohort, repair 3)."""
    project, plugins = tmp_path / "project", tmp_path / "plugins"
    (project / "tests").mkdir(parents=True)
    plugins.mkdir()
    (project / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (project / "tests" / "test_new.py").write_text("from nowhere_at_all import thing\n\ndef test_bug():\n    pass\n",
                                                    encoding="utf-8")
    (project / "tests" / "test_old.py").write_text("class TestOld:\n    def test_ok(self):\n        pass\n\n"
                                                    "import pytest\n\n@pytest.mark.parametrize('x', [1, 2])\n"
                                                    "def test_p(x):\n    pass\n", encoding="utf-8")
    (project / "tests" / "test_kinds.py").write_text(KINDS, encoding="utf-8")
    (plugins / "we_report.py").write_text(we.REPORT_PLUGIN, encoding="utf-8")
    done_, report, junit = _plugin_run(project, plugins, tmp_path, "--continue-on-collection-errors", "tests")
    assert done_.returncode == 1, done_.stdout
    got = we.read_report(report.read_bytes())
    assert got["collect_errors"] == ["tests/test_new.py"], got
    keys = we.parse_junit(junit.read_text(encoding="utf-8"))
    shown = got["junit"]
    assert set(shown) == set(got["tests"]) and len(set(shown.values())) == len(shown)      # here no two share a junit key
    assert {shown[node]: outcome for node, outcome in got["tests"].items()} == {k: v for k, v in keys.items() if "::" in k
                                                                                and not k.startswith("::")}, (keys, got)
    kinds = {node.split("::")[-1]: outcome for node, outcome in got["tests"].items() if "test_kinds" in node}
    assert kinds == {"test_pass": "passed", "test_fail": "failed", "test_setup_error": "error", "test_teardown_error": "error",
                     "test_fail_and_teardown_error": "error", "test_skip": "skipped", "test_skip_mark": "skipped",
                     "test_xfail": "skipped", "test_xpass_strict": "failed", "test_xpass": "passed"}, kinds
    assert got["tests"]["tests/test_old.py::TestOld::test_ok"] == "passed"
    assert shown["tests/test_old.py::test_p[2]"] == "tests.test_old::test_p[2]"


def _plugin_process(monkeypatch, report: Path):
    """The report plugin as one pytest process loads it (``-p we_report``), its file ``report``."""
    import types
    monkeypatch.setenv("WE_REPORT", str(report))
    module = types.ModuleType("we_report")
    exec(compile(we.REPORT_PLUGIN, "we_report.py", "exec"), module.__dict__)
    return module


def _logged(node: str, when: str, outcome: str):
    return SimpleNamespace(nodeid=node, when=when, outcome=outcome, failed=outcome == "failed")


def test_under_pytest_xdist_only_the_controller_writes_the_report_so_it_is_read_whole(monkeypatch, tmp_path):
    """pytest-xdist loads the plugin in its controller and in each worker; each worker sends its reports to the controller's
    hooks. Found by the controls' rerun (replay cohort, repair 3): four bugs' runs under xdist kept a report two processes had
    written, which the reader refused."""
    report = tmp_path / "report.jsonl"
    controller = _plugin_process(monkeypatch, report)
    getattr(controller, "pytest_configure", lambda config: None)(SimpleNamespace())
    workers = {}
    for wid in ("gw0", "gw1"):
        workers[wid] = _plugin_process(monkeypatch, report)
        getattr(workers[wid], "pytest_configure", lambda config: None)(SimpleNamespace(workerinput={"workerid": wid}))
    ran = {"gw0": [("t.py::a", "setup", "passed"), ("t.py::a", "call", "failed"), ("t.py::a", "teardown", "passed")],
           "gw1": [("t.py::b", "call", "passed")]}
    try:
        for wid, worker in workers.items():      # each worker runs its own tests; the controller is sent every report
            for node, when, outcome in ran[wid]:
                worker.pytest_runtest_logreport(_logged(node, when, outcome))
                controller.pytest_runtest_logreport(_logged(node, when, outcome))
            worker.pytest_sessionfinish(None, 1)
        controller.pytest_sessionfinish(None, 1)
    finally:
        for plugin in (*workers.values(), controller):
            if getattr(plugin, "_out", None) is not None:
                plugin._out.close()
    got = we.read_report(report.read_bytes())
    assert got is not None and got["tests"] == {"t.py::a": "failed", "t.py::b": "passed"}, report.read_bytes()


def test_a_report_the_plugin_cannot_read_is_never_raised_into_pytest_and_leaves_the_report_unread(monkeypatch, tmp_path):
    report = tmp_path / "report.jsonl"
    plugin = _plugin_process(monkeypatch, report)
    getattr(plugin, "pytest_configure", lambda config: None)(SimpleNamespace())
    try:
        plugin.pytest_runtest_logreport(_logged("t.py::a", "call", "passed"))
        plugin.pytest_runtest_logreport(SimpleNamespace(nodeid="t.py::b", when="call"))        # no outcome
        plugin.pytest_collectreport(SimpleNamespace(nodeid="t.py"))                             # no failed
        plugin.pytest_sessionfinish(None, 0)
    finally:
        plugin._out.close()
    assert we.read_report(report.read_bytes()) is None, report.read_bytes()


@pytest.mark.parametrize(("lines", "why"), [
    (plugin_lines(phase("t.py::a", "call", "passed"), finished=None), "no session end: pytest did not finish, or was cut short"),
    (plugin_lines(phase("t.py::a", "call", "passed"))[:-1] + b"\n" + plugin_lines(phase("t.py::b", "call", "passed"), finished=None),
     "the session's end before another line"),
    (plugin_lines({"node": "t.py::a", "junit": "t::a"}), "a line in the form that kept no outcome"),
    (plugin_lines(phase("t.py::a", "call", "maybe")), "an outcome pytest never gives"),
    (plugin_lines(phase("t.py::a", "later", "passed")), "a phase pytest never has"),
    (plugin_lines(phase("t.py::a", "call", "passed"), finished=True), "a session end that is no exit status"),
    (plugin_lines({"node": "t.py::\ud800", "when": "call", "outcome": "passed", "junit": None}), "a node id no UTF-8 can hold"),
    (b'{"node": "t.py::a", "when": "call", "outco', "a torn line"),
    (b"", "nothing at all"),
])
def test_a_report_pytest_did_not_finish_or_that_is_not_the_plugins_gives_no_test_an_identity(lines, why):
    assert we.read_report(lines) is None, why


def test_a_tests_outcome_is_the_worst_of_its_phases_and_two_node_ids_stay_two_tests_whatever_their_junit_keys():
    got = we.read_report(plugin_lines(phase("t.py::a[\x01]", "setup", "passed", "t::a[\x01]"),
                                      phase("t.py::a[\x01]", "call", "passed", "t::a[\x01]"),
                                      phase("t.py::a[\x01]", "teardown", "failed", "t::a[\x01]"),
                                      phase("t.py::a[#x01]", "call", "passed", "t::a[\x01]"),
                                      phase("t.py::b", "setup", "passed"), phase("t.py::b", "teardown", "passed")))
    assert got["tests"] == {"t.py::a[\x01]": "error", "t.py::a[#x01]": "passed"}, got        # b never reached its call: no result
    assert got["junit"] == {"t.py::a[\x01]": "t::a[\x01]", "t.py::a[#x01]": "t::a[\x01]"}    # one key shown for both, two tests


# --- review 3's finding 3: two node ids junit renders as one key stay two tests ------------------------------------------------
def test_review_3_finding_3_a_different_pytest_nodes_failure_never_supplies_a_failure_for_another_node(tmp_path):
    import os
    import subprocess
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.ini").write_text(
        "[pytest]\ndisable_test_id_escaping_and_forfeit_all_rights_to_community_support = True\n", encoding="utf-8")
    (project / "we_report.py").write_text(we.REPORT_PLUGIN, encoding="utf-8")
    # The same maintainer test is parametrized from the library's public data. pytest keeps the two ids, while junit renders the
    # control character as the other's text.
    (project / "test_case.py").write_text(
        "import pytest\nfrom mod import value\n"
        "@pytest.mark.parametrize('item', [value], ids=[value])\n"
        "def test_case(item):\n    assert item == '#x01'\n", encoding="utf-8")

    def observed_run(value, name):
        (project / "mod.py").write_text("value = " + repr(value) + "\n", encoding="utf-8")
        junit = tmp_path / (name + ".xml")
        report = tmp_path / (name + ".jsonl")
        ran = subprocess.run(
            [sys.executable, "-m", "pytest", "test_case.py", "-q", "-p", "no:cacheprovider", "-p", "we_report",
             "-c", str(project / "pytest.ini"), "--rootdir=" + str(project), "--junitxml=" + str(junit),
             "-o", "junit_family=xunit2", "-o", "disable_test_id_escaping_and_forfeit_all_rights_to_community_support=True"],
            cwd=project, env={**os.environ, "PYTHONPATH": str(project), "WE_REPORT": str(report), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, timeout=60)
        assert ran.returncode == (0 if value == "#x01" else 1), ran.stdout + ran.stderr
        docker = ReportDocker(report.read_bytes())        # the producer's real files through the oracle's own reader
        docker.report = junit.read_bytes()
        docker.said = f"exit={ran.returncode}\n".encode()
        task = SimpleNamespace(id="toy-1", repo="/w/toy", test_modules=("test_case.py",))
        return we._oracle_run(SimpleNamespace(docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3), task, report=True)

    base = observed_run("\x01", "base")
    gold = observed_run("#x01", "gold")
    assert set(base["results"]) == set(gold["results"]), "junit renders the two node ids as one key (the finding's premise)"
    assert set(base["tests"]).isdisjoint(gold["tests"]), "pytest's own report keeps them two tests"
    f2p, p2p = we.fail_to_pass(base["tests"], gold["tests"])
    ids = {"task_image_id": "sha256:" + "a" * 64, "oracle_image_id": "sha256:" + "b" * 64}
    record = {
        "validate": {"images": ids, "fail_to_pass": f2p, "pass_to_pass": p2p, "base": base, "fixed": gold},
        "selfcheck": {"images": ids, "gold": gold, "empty": base},
        "round_trip": {"images": ids, "status": "finished", "kept_read": True, "known_bytes": 1,
                       "kept_bytes": 1, "known_sha256": "c" * 64, "kept_sha256": "c" * 64},
        "images_after": ids,
    }
    assert cohorts.admit(record, ids) != "", "a different pytest node's failure admitted the bug"
    junit_keyed = we.fail_to_pass(base["results"], gold["results"])          # the lists as junit keys would give them
    record["validate"].update(fail_to_pass=junit_keyed[0], pass_to_pass=junit_keyed[1])
    assert cohorts.admit(record, ids) != "", "a junit key gave a test its identity"


def test_a_record_that_kept_only_junits_report_has_no_test_identities_so_no_control_holds():
    ids = {"task_image_id": ORACLE, "oracle_image_id": ORACLE}
    junit = {k: {key: value for key, value in v.items() if key != "tests"} | {"results": dict(v["tests"])} for k, v in GOOD.items()}
    record = {"validate": {"images": ids, "fail_to_pass": ["t::bug"], "pass_to_pass": ["t::ok"], "base": junit["base"],
                           "fixed": junit["fixed"]},
              "selfcheck": {"images": ids, "gold": junit["gold"], "empty": junit["empty"]},
              "round_trip": {**TRIP, "images": ids}, "images_after": ids}
    for control in ("base", "gold patch", "empty patch"):
        assert cohorts.control_why(record, control) != "", control
    assert cohorts.admit(record, ids).startswith("base: ")
    for name in ("base", "fixed", "gold", "empty"):                      # the same outcomes, by node id, hold
        record["validate" if name in ("base", "fixed") else "selfcheck"][name] = GOOD[name]
    assert cohorts.admit(record, ids) == ""


# --- finding 8: the call phases are a closed set -----------------------------------------------------------------------------------
def test_finding_8_an_unknown_phase_refuses_instead_of_discarding_the_call(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"calls": [{"call": 1, "phase": "unknown-phase", "before": {}}]}))
    with pytest.raises(box.Refused, match="unknown-phase"):
        box.settle(ws, lambda *args, **kw: pytest.fail("unknown state must not run a container step"), state=state,
                   container="ctr", repo="/w/toy", user="tester", timeout=30)
    opts = SimpleNamespace(container="ctr", repo="/w/toy", workspace=str(ws), user="tester", timeout=30.0, state=str(state))
    got = box.call(opts, {"name": "run", "arguments": {"command": "true"}}, lambda *a, **kw: pytest.fail("nothing may run"))
    assert got["isError"] is True and "unknown-phase" in got["content"][0]["text"], got


@pytest.mark.parametrize("calls", [
    [{"call": 1, "phase": "Settled"}],
    [{"call": 1, "phase": ""}],
    [{"call": 1, "phase": "settled"}, {"call": 2, "phase": "mirroring"}],
    [{"call": 1, "phase": "mirrored"}],                                      # unfinished, with no tree it was mirrored from
    [{"call": 1, "phase": "pulling", "before": {"a": "not a mark"}}],
])
def test_a_state_with_a_phase_outside_the_closed_set_or_an_unfinished_call_without_its_tree_refuses(tmp_path, calls):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"calls": calls}))
    with pytest.raises(box.Refused):
        box.read_state(state)


@pytest.mark.parametrize("phase", ["settled", "set back", "not settled"])
def test_a_finished_call_is_never_settled_again(tmp_path, phase):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"calls": [{"call": 1, "phase": phase}]}))
    assert box.settle(tmp_path, lambda *a, **kw: pytest.fail("no step"), state=state, container="c", repo="/r", user="u",
                      timeout=1) is None


def test_no_phase_outside_the_closed_set_is_ever_written(tmp_path):
    with pytest.raises(ValueError):
        box.write_state(tmp_path / "state.json", {"call": 1, "phase": "unknown-phase"})
    assert not (tmp_path / "state.json").exists()
    written = set()
    for name in ("box_mcp.py", "run.py"):
        for node in ast.walk(ast.parse((HERE / name).read_text("utf-8"))):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "phase":
                        if isinstance(value, ast.Constant):
                            written.add(value.value)
                        elif isinstance(value, ast.IfExp):
                            written |= {v.value for v in (value.body, value.orelse) if isinstance(v, ast.Constant)}
                        else:
                            pytest.fail(f"{name} line {node.lineno}: a phase that is no constant of the set")
    assert written and written <= set(box.PHASES), sorted(written - set(box.PHASES))
    assert box.UNFINISHED == ("mirrored", "pulling") and set(box.UNFINISHED) < set(box.PHASES)


# --- the inventory: every place a container result or a test report is read --------------------------------------------------------
#: What is a read: an exit code or an output taken from a call's result (``.returncode``, ``.stdout``, ``.stderr``, but
#: ``sys``'s own streams), the runner's raw readers of a result (``out``, ``err``), a test report's parse (``parse_junit``,
#: ``read_probes``), or a test report's results or exit taken from a body (``"results"``, ``"tests"``, ``"pytest_exit"``).
ATTRS, RAW, KEYS = {"returncode", "stdout", "stderr"}, {"out", "err", "parse_junit", "read_probes"}, {"results", "tests", "pytest_exit"}
#: The one reader, and the ``read`` each consumer hands it to take its answer from a call's output. A test's outcomes are taken by
#: ``tests_of`` alone (replay cohort, repair 3), which ``_run_seen``, ``_collect_failed`` and ``seen_failing`` call.
READERS = {("box_mcp.py", "seen"), ("box_mcp.py", "tests_seen"), ("box_mcp.py", "unobserved_why"), ("box_mcp.py", "tests_of")}
#: Where the controls, the probe and the call phases start, and the host run, which the round trip reads only by its record's
#: status, its ``final_patch_read`` and the kept patch ``keep_final`` read: a boundary the walk below does not cross.
ROOTS = {("cohorts.py", n) for n in ("main", "controls", "manifest", "admit", "round_trip", "known_patch", "image_ids")} | \
        {("run.py", n) for n in ("loop_status", "keep_final", "workspace_from")} | {("box_mcp.py", n) for n in ("call", "settle", "serve")}
BOUNDARY = {("run.py", "_run_task")}
#: Inside the controls, the probe and the call phases: the places that read a test report's results other than the reader, each
#: with why it is not a verdict the reader should give.
INSIDE = {
    ("run.py", "_oracle_run", "parse_junit()"): "the producer: the report parsed into the body tests_seen reads",
    ("run.py", "_oracle_run", "[results]"): "the producer: the body's results set from the report it read",
    ("run.py", "observed", "[results]"): "a copy of the body for the record, which tests_seen reads again",
    ("run.py", "validate", "[results]"): "the lists every check scores against (fail_to_pass), frozen; the base control reads "
                                         "the base's outcome through tests_seen",
    ("run.py", "selfcheck", "[results]"): "verdict's passes and failing, kept as before beside tests_seen's reading, which ok "
                                          "and the controls read",
}
_STUDY = "the study's arms (sessions, scouts, critics, probe sets, key checks, images): read by no control, probe or call phase"
#: Outside them, every function that reads a result or a report, with why: a new one fails until it is named here.
OUTSIDE = {
    "_run_task": "the host run: the round trip reads its status, final_patch_read and keep_final's bytes; " + _STUDY,
    "amend": "the amended tests' own controls, scored by the frozen rule", "brief": "a printed summary",
    "build": "an image build, before any control", "build_base": "an image build, before any control",
    "check": "the independent check that scores a run: no report raises, a timeout is the patch's failure by the frozen rule",
    "contains_required": "a local git call, no container's", "err": "the raw reader, used only outside", "g6replay": _STUDY,
    "git_commit": "a local git call, no container's", "hook_selftest": _STUDY, "host_session": "a host session's stderr",
    "host_session.lines": "a host session's stream", "kept": _STUDY, "key_check": _STUDY, "modelcheck": _STUDY,
    "out": "the raw reader, used only outside", "probe_gate": _STUDY, "read_issue": "the issue, from GitHub, no container's",
    "read_probes": _STUDY, "read_thread.api": "the issue's thread, from GitHub, no container's",
    "read_tree": "upstream's tree, from GitHub, no container's", "refusal": _STUDY, "repair_loop.check": _STUDY,
    "repair_loop.state": _STUDY, "reviews_until_clean.shown": _STUDY, "reviews_with_bug_test.tree": _STUDY, "run_codex": _STUDY,
    "run_critic": _STUDY, "run_scout": _STUDY, "run_session": _STUDY, "run_set": _STUDY,
}


def _functions(name: str) -> dict[str, ast.AST]:
    """Each function of a module, top-level, nested or a method, by its qualified name."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not isinstance(child, ast.ClassDef):
                    found[".".join([*where, child.name])] = child
                visit(child, [*where, child.name])
            else:
                visit(child, where)
    visit(ast.parse((HERE / name).read_text("utf-8")), [])
    return found


def _reads(fn: ast.AST) -> set[str]:
    """The reads in a function's own body, its lambdas' among them (a nested function is its own)."""
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Attribute) and child.attr in ATTRS and not (
                    isinstance(child.value, ast.Name) and child.value.id == "sys"):
                found.add("." + child.attr)
            elif isinstance(child, ast.Call):
                name = child.func.attr if isinstance(child.func, ast.Attribute) else getattr(child.func, "id", "")
                const = child.args[1].value if len(child.args) > 1 and isinstance(child.args[1], ast.Constant) else None
                if name in RAW:
                    found.add(name + "()")
                elif name == "get" and child.args and isinstance(child.args[0], ast.Constant) and child.args[0].value in KEYS:
                    found.add(f"[{child.args[0].value}]")
                elif name == "getattr" and const in ATTRS:          # the same read, by name
                    found.add("." + const)
            elif isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant) and child.slice.value in KEYS:
                found.add(f"[{child.slice.value}]")
            visit(child)
    visit(fn)
    return found


ALIASES = {"we": "run.py", "box": "box_mcp.py", "box_mcp": "box_mcp.py", "cohorts": "cohorts.py"}


def _scope(functions: dict[tuple[str, str], ast.AST]) -> set[tuple[str, str]]:
    """Every function the roots reach by calls, in the three modules, a nested function with the function it is in, stopping at
    the boundary."""
    seen: set[tuple[str, str]] = set()
    todo = [r for r in ROOTS if r in functions]
    while todo:
        key = todo.pop()
        if key in seen or key in BOUNDARY:
            continue
        seen.add(key)
        file, name = key
        todo += [k for k in functions if k[0] == file and k[1].startswith(name + ".")]
        for node in ast.walk(functions[key]):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    todo.append((file, node.func.id))
                elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    todo.append((ALIASES.get(node.func.value.id, file), node.func.attr))
            elif isinstance(node, ast.Name) and (file, node.id) in functions:      # a function handed on, as ``seen``'s read
                todo.append((file, node.id))
        todo = [k for k in todo if k in functions]
    return seen


def test_every_place_a_container_result_or_a_test_report_is_read_is_listed_and_inside_the_controls_only_the_reader_reads():
    functions = {(file, name): fn for file in ("box_mcp.py", "cohorts.py", "run.py") for name, fn in _functions(file).items()}
    sites = {(file, name, kind) for (file, name), fn in functions.items() for kind in _reads(fn)}
    scope = _scope(functions)
    assert {("run.py", "capture_why"), ("run.py", "_oracle_run"), ("run.py", "validate"), ("run.py", "selfcheck"),
            ("run.py", "checked"), ("run.py", "copy_out"), ("cohorts.py", "control_why"), ("box_mcp.py", "mirror"),
            ("box_mcp.py", "pull"), ("box_mcp.py", "run_command"), ("box_mcp.py", "read_state")} <= scope
    inside = {s for s in sites if s[:2] in scope and s[:2] not in READERS}
    assert inside == set(INSIDE), ("a read inside the controls, the probe or the call phases not through the reader",
                                   sorted(inside - set(INSIDE)), sorted(set(INSIDE) - inside))
    outside = {name for file, name, _ in sites if (file, name) not in scope and (file, name) not in READERS}
    assert {file for file, name, _ in sites if (file, name) not in scope and (file, name) not in READERS} <= {"run.py"}
    assert outside == set(OUTSIDE), (sorted(outside - set(OUTSIDE)), sorted(set(OUTSIDE) - outside))
    assert not set(OUTSIDE) & {name for file, name in scope if file == "run.py"}
    assert {(f, n) for f, n, _ in sites} >= READERS

#: The one rule for "this test was seen failing" (coordinator's ruling and Option B, 2026-11-06 and 07): box_mcp.seen_failing,
#: which tests_seen alone calls; and, inside the controls, the probe and the call phases, the only other place that compares an
#: outcome with "failed" or "error": junit's own tags turned into an outcome.
FAILING_RULE = ("box_mcp.py", "seen_failing")
PRODUCERS = {("run.py", "parse_junit"): "junit's own tags made the outcome seen_failing reads"}


def _compares_a_failure(fn: ast.AST) -> bool:
    def failure(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return node.value in ("failed", "error")
        return isinstance(node, (ast.Tuple, ast.List, ast.Set)) and any(failure(x) for x in node.elts)
    return any(isinstance(n, ast.Compare) and any(failure(x) for x in (n.left, *n.comparators)) for n in ast.walk(fn))


def test_both_controls_decide_a_seen_failure_by_the_one_rule_and_nothing_else_decides_one():
    functions = {(file, name): fn for file in ("box_mcp.py", "cohorts.py", "run.py") for name, fn in _functions(file).items()}
    scope = _scope(functions) | {k for k in functions if k[0] in ("box_mcp.py", "cohorts.py")}
    deciding = {k for k in scope if _compares_a_failure(functions[k])}
    assert deciding == {FAILING_RULE, *PRODUCERS}, sorted(deciding - {FAILING_RULE, *PRODUCERS})

    def calls(key: tuple[str, str], name: str) -> list[ast.Call]:
        return [n for n in ast.walk(functions[key]) if isinstance(n, ast.Call)
                and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == name]
    callers = {k for k in functions if calls(k, "_collect_failed")}
    assert callers == {FAILING_RULE}, sorted(callers)            # the collection's evidence is read by the one rule only
    assert calls(("box_mcp.py", "tests_seen"), "seen_failing"), "tests_seen counts failures some other way"
    readings = calls(("cohorts.py", "control_why"), "tests_seen")
    ruled = [c for c in readings if {k.arg for k in c.keywords} == {"seen_passing_in", "fail_to_pass"}]
    others = [c for c in readings if c not in ruled]       # the fix's own reading: seen to pass, so no failure is decided there
    assert len(ruled) == 1 and len(others) == 1 and not others[0].keywords, \
        "control_why reads the base and the empty patch by different calls"
    assert ast.unparse(others[0].args[0]) == "v['fixed']"
    for key in [("cohorts.py", "summary"), ("run.py", "selfcheck")]:      # what a manifest and selfcheck's ok show, the same way
        empties = [c for c in calls(key, "tests_seen") if "fail_to_pass" in {k.arg for k in c.keywords}]
        assert empties, f"{key[1]} reads an empty patch or a base without the one rule"


# --- the inventory: every place a test's identity is taken (replay cohort, repair 3, review 3's finding 3) ------------------------
#: What takes a test's identity: an outcome map read by its key (``results``, junit's keys; ``tests``, pytest's node ids; ``junit``,
#: the display; ``nodes``, the junit-keyed map repair 3 removed; ``node``, the plugin's own field), or a call that makes or keys one.
ID_KEYS, ID_CALLS = {"results", "tests", "junit", "nodes", "node"}, {"parse_junit", "read_report", "fail_to_pass", "tests_of"}
#: Every place, by (module, function, what), with its part. Inside the controls only ``tests_of`` gives a test its identity, and only
#: the node id pytest's own report gives: a junit key is a producer's, a display, or the study's frozen scoring.
IDENTITY = {
    ("box_mcp.py", "tests_of", "[tests]"): "THE one place a test's identity is taken: the node ids of pytest's own report",
    ("box_mcp.py", "tests_seen", "tests_of()"): "the reader, by node id",
    ("box_mcp.py", "seen_failing", "tests_of()"): "the one rule for a seen failure, by node id",
    ("box_mcp.py", "_collect_failed", "tests_of()"): "the gold run's pass of the same node id",
    ("box_mcp.py", "unobserved_why", "tests_of()"): "a run is observed only with its tests' identities",
    ("cohorts.py", "control_why", "tests_of()"): "names a base or fix with no identities, when no list was made",
    ("run.py", "read_report", "[node]"): "the producer: each test's node id, from pytest's own report",
    ("run.py", "read_report", "[junit]"): "the producer: the junit key beside each node id, for display",
    ("run.py", "_oracle_run", "read_report()"): "the producer: pytest's own report into the body's tests",
    ("run.py", "_oracle_run", "parse_junit()"): "the producer: junit's report into results, the study's frozen scoring",
    ("run.py", "_oracle_run", "[results]"): "the producer: junit's results set from the report it read",
    ("run.py", "observed", "tests_of()"): "the record's copy of the node ids",
    ("run.py", "observed", "[junit]"): "the record's copy of the display",
    ("run.py", "observed", "[results]"): "the record's copy for a run with no report of pytest's own: the study's",
    ("run.py", "validate", "tests_of()"): "the lists, by node id, when pytest's own report was asked for",
    ("run.py", "validate", "fail_to_pass()"): "the lists, by node id, or by junit key for the study's frozen check",
    ("run.py", "validate", "[results]"): "the study's lists, by junit key, when pytest's own report was not asked for",
    ("run.py", "selfcheck", "tests_of()"): "verdict's display, by the lists' own identity",
    ("run.py", "selfcheck", "[results]"): "verdict's display for the study's junit-keyed lists",
    ("run.py", "amend", "[results]"): "the amended tests' own controls, by the frozen rule's junit keys: the study's",
    ("run.py", "amend", "fail_to_pass()"): "the amended tests' lists, by the frozen rule's junit keys: the study's",
    ("run.py", "check", "[results]"): "the independent check that scores a run, by the frozen rule's junit keys: the study's",
    ("run.py", "brief", "[results]"): "a printed count of a validate record's results",
    ("run.py", "read_probes", "parse_junit()"): "a probe run's junit, the study's arms",
}


def _identity_sites(fn: ast.AST) -> set[str]:
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call):
                name = child.func.attr if isinstance(child.func, ast.Attribute) else getattr(child.func, "id", "")
                if name in ID_CALLS:
                    found.add(name + "()")
                elif name == "get" and child.args and isinstance(child.args[0], ast.Constant) and child.args[0].value in ID_KEYS:
                    found.add(f"[{child.args[0].value}]")
            elif isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant) and child.slice.value in ID_KEYS:
                found.add(f"[{child.slice.value}]")
            visit(child)
    visit(fn)
    return found


def test_every_place_a_tests_identity_is_taken_is_listed_and_inside_the_controls_only_the_node_id_decides():
    functions = {(file, name): fn for file in ("box_mcp.py", "cohorts.py", "run.py") for name, fn in _functions(file).items()}
    sites = {(file, name, kind) for (file, name), fn in functions.items() for kind in _identity_sites(fn)}
    assert sites == set(IDENTITY), ("a place a test's identity is taken that is not listed, or one listed that is gone",
                                    sorted(sites - set(IDENTITY)), sorted(set(IDENTITY) - sites))
    assert not any(kind == "[nodes]" for _, _, kind in sites), "a junit-keyed map of node ids is read again"
    deciding = {("box_mcp.py", n) for n in ("tests_seen", "seen_failing", "_collect_failed", "_run_seen", "unobserved_why")} | {("cohorts.py", n)
                                                                                                               for n in ("control_why", "admit", "summary")}
    for key in deciding:                 # the readers and the controls take outcomes by node id only, through tests_of
        assert not {kind for f, n, kind in sites if (f, n) == key} - {"tests_of()"}, key
    assert ("box_mcp.py", "tests_of") in functions and box.tests_of({"results": {"t": "failed"}}) is None


# --- an exit status is a number that was seen (replay cohort, repair 2, review 2's finding 4) ------------------------------------

@pytest.mark.parametrize(("value", "code"), [
    ("0", 0), ("1", 1), ("2", 2), ("124", 124), ("255", 255), (0, 0), (5, 5), ("124\n", 124), (" 1 ", 1),
    ("", None), (" ", None), (None, None), ("abc", None), ("1.0", None), ("-1", None), ("256", None), ("1\n2", None),
    ("１", None), (True, None), (False, None), (-1, None), (256, None), (1.0, None), ([1], None),
])
def test_one_reader_takes_an_exit_status_only_as_an_integer_that_was_written(value, code):
    assert box.exit_status(value) == code


@pytest.mark.parametrize("said", [b"exit=\n", b"exit= \n", b"exit=x\n", b"exit=1.5\n", b"exit=-1\n", b"exit=999\n", b"ran\n"])
def test_review_2_finding_4_a_control_with_no_pytest_exit_status_written_is_unknown_and_never_admits_the_bug(monkeypatch, tmp_path,
                                                                                                            said):
    import contextlib
    monkeypatch.setattr(we.tempfile, "TemporaryDirectory", lambda **kw: contextlib.nullcontext(str(tmp_path)))
    task = SimpleNamespace(id="toy-1", repo="/w/toy", test_modules=["t.py"])
    ctx = SimpleNamespace(docker=OracleDocker(said, 0, JUNIT), g6={"commit": "0" * 40}, variant=we.ROUND3)
    run = we._oracle_run(ctx, task, label="base")
    assert run["results"]["t::bug"] == "failed"            # the report came back; the exit status did not
    assert run["pytest_exit"] is None and run["call"] == U, run
    seen = box.tests_seen(run, ["t::bug"])
    assert seen["state"] == U, seen
    ids = {"task_image_id": "sha256:" + "1" * 64, "oracle_image_id": "sha256:" + "2" * 64}
    gold = {"call": P, "pytest_exit": "0", "report": True, "results": {"t::bug": "passed", "t::ok": "passed"}}
    record = {"validate": {"images": ids, "fail_to_pass": ["t::bug"], "pass_to_pass": ["t::ok"], "base": run, "fixed": gold},
              "selfcheck": {"images": ids, "gold": gold, "empty": run}, "round_trip": {**TRIP, "images": ids}, "images_after": ids}
    assert cohorts.admit(record, ids).startswith("base: "), "a bug admitted without a pytest exit status: " + repr(seen)


@pytest.mark.parametrize("exit_", ["", " ", "abc", "1.0", True, "256", [1]])
def test_a_report_whose_exit_is_no_number_that_was_written_is_unknown_never_a_failure_or_a_pass(exit_):
    for results in ({"t1": "failed", "t2": "passed"}, {"t1": "passed", "t2": "passed"}):
        seen = box.tests_seen(body(results, exit=exit_), ["t1", "t2"])
        assert seen["state"] == U and "exit status" in seen["why"], seen
    gold = {**GOLD_RUN, "pytest_exit": exit_}                     # nor is such a gold run evidence for a collection error
    assert box.seen_failing(NEW, ERRED, gold) is False
    assert box.tests_seen(ERRED, [NEW], seen_passing_in=gold, fail_to_pass=[NEW])["state"] == U
    assert box.tests_seen(body({}, exit=exit_, report=False), ["t1"])["why"] != "the tests ran out of time (exit 124) with no report"


#: What reads an exit status (replay cohort, repair 2): a call's return code (``.returncode``), pytest's exit as a test run's body
#: keeps it (``"pytest_exit"``), an ``exit=`` line a script printed (a string holding ``exit=`` that is not the shell's own
#: ``echo``), or an exit a record or a probe run keeps (``"exit"`` or ``"exit_code"`` as a key, ``.exit``).
EXIT_KEYS = {"pytest_exit", "exit", "exit_code"}
#: The readers: ``seen`` takes a call's return code only as an int that came back, and ``exit_status`` pytest's exit only as an
#: integer that was written; ``run.pytest_exit`` gives the ``exit=`` line's text only when ``exit_status`` reads it as one.
EXIT_READERS = {("box_mcp.py", "seen"), ("box_mcp.py", "exit_status")}
#: Inside the controls, the probe and the call phases: each place that reads an exit status, and how. Each that reads pytest's exit
#: reads it through ``exit_status`` (``THROUGH``), and each that reads a return code through ``seen``.
EXIT_INSIDE = {
    ("box_mcp.py", "unobserved_why", "[pytest_exit]"): "through exit_status: whether a run, a gold run among them, was seen to end",
    ("box_mcp.py", "tests_seen", "[pytest_exit]"): "through exit_status: no integer written is no exit status, so unknown",
    ("run.py", "pytest_exit", "exit="): "the exit= line, given only when exit_status reads it as an integer that was written",
}
THROUGH = {("box_mcp.py", "unobserved_why"), ("box_mcp.py", "tests_seen"), ("run.py", "pytest_exit")}
#: Outside them, every function that reads an exit status, with why it is not a control's: a new one fails until it is named.
_PROBED = "a probe run's exit, as read_probes read it (digits only, else None, so the run is not usable): " + _STUDY
EXIT_OUTSIDE = {
    "check": "the independent check that scores a run, by the frozen rule; it reads pytest's exit through exit_status",
    "probe_failures": "a probe run's exit= line, read only as digits; none is a failure ('no exit'), never clean: " + _STUDY,
    "gate_numbers": "the gate's exit= line, read only as digits; none is 2, which the gate refuses: " + _STUDY,
    "read_probes": "a probe run's exit= line, read only as digits; none is None, so the run is not usable: " + _STUDY,
    "ProbeRun.usable": _PROBED, "probe_gate": _PROBED, "bug_test": _PROBED, "failures_of": _PROBED, "review_once.check": _PROBED,
    "reviews_until_clean": _PROBED, "reviews_with_bug_test": _PROBED, "repair_loop.check": _PROBED, "run_scout": _PROBED,
    "scout_gate": "the probes' gate, given pytest's exit as gate_numbers read it: " + _STUDY,
    "key_check_ok": "the key check's control exit from its own JSON: not 0 and not None, never a pass by default: " + _STUDY,
    "_run_task": OUTSIDE["_run_task"], "build_base": OUTSIDE["build_base"], "contains_required": OUTSIDE["contains_required"],
    "g6replay": _STUDY, "git_commit": OUTSIDE["git_commit"], "kept": _STUDY, "key_check": _STUDY, "modelcheck": _STUDY,
    "read_issue": OUTSIDE["read_issue"], "read_thread.api": OUTSIDE["read_thread.api"], "read_tree": OUTSIDE["read_tree"],
}


def _exit_reads(fn: ast.AST) -> set[str]:
    """The exit status reads in a function's own body (``EXIT_KEYS``, ``.returncode``, ``.exit``, an ``exit=`` line), its lambdas'
    among them, a docstring and a nested function apart."""
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                continue                                                  # a docstring reads nothing
            if isinstance(child, ast.Attribute) and child.attr == "returncode":
                found.add(".returncode")
            elif isinstance(child, ast.Attribute) and child.attr == "exit" and not (
                    isinstance(child.value, ast.Name) and child.value.id == "sys"):
                found.add(".exit")
            elif isinstance(child, ast.Constant) and isinstance(child.value, str) and "exit=" in child.value \
                    and "echo" not in child.value:
                found.add("exit=")
            elif isinstance(child, ast.Call) and getattr(child.func, "attr", None) == "get" and child.args \
                    and isinstance(child.args[0], ast.Constant) and child.args[0].value in EXIT_KEYS:
                found.add(f"[{child.args[0].value}]")
            elif isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Constant) and child.slice.value in EXIT_KEYS:
                found.add(f"[{child.slice.value}]")
            elif isinstance(child, ast.Name) and child.id == "exit_code" and isinstance(child.ctx, ast.Load):
                found.add("exit_code")
            elif isinstance(child, ast.Call) and getattr(child.func, "id", None) == "getattr" and len(child.args) > 1 \
                    and isinstance(child.args[1], ast.Constant) and child.args[1].value in ("returncode", "exit"):
                found.add("." + child.args[1].value)                       # the same read, by name
            visit(child)
    visit(fn)
    return found


def test_every_place_an_exit_status_is_read_is_listed_and_inside_the_controls_only_the_readers_read_one():
    functions = {(file, name): fn for file in ("box_mcp.py", "cohorts.py", "run.py") for name, fn in _functions(file).items()}
    sites = {(file, name, kind) for (file, name), fn in functions.items() for kind in _exit_reads(fn)}
    scope = _scope(functions)
    inside = {s for s in sites if s[:2] in scope and s[:2] not in EXIT_READERS}
    returned = {s for s in inside if s[2] == ".returncode"}
    assert not returned, ("a return code read inside the controls not through seen", sorted(returned))
    assert inside == set(EXIT_INSIDE), ("an exit status read inside the controls, the probe or the call phases not listed",
                                        sorted(inside - set(EXIT_INSIDE)), sorted(set(EXIT_INSIDE) - inside))

    def calls(key: tuple[str, str], name: str) -> bool:
        return any(isinstance(n, ast.Call) and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == name
                   for n in ast.walk(functions[key]))
    assert {s[:2] for s in inside} == THROUGH and all(calls(key, "exit_status") for key in THROUGH), \
        "a place that reads pytest's exit not through exit_status"
    outside = {name for file, name, _ in sites if (file, name) not in scope and (file, name) not in EXIT_READERS}
    assert {file for file, name, _ in sites if (file, name) not in scope and (file, name) not in EXIT_READERS} <= {"run.py"}
    assert outside == set(EXIT_OUTSIDE), (sorted(outside - set(EXIT_OUTSIDE)), sorted(set(EXIT_OUTSIDE) - outside))
    assert calls(("run.py", "check"), "exit_status"), "the independent check reads pytest's exit some other way"
    assert {(f, n) for f, n, _ in sites} >= EXIT_READERS - {("box_mcp.py", "exit_status")}
    assert ("box_mcp.py", "exit_status") in functions
