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
    return {"call": call, "pytest_exit": exit, "report": report, "results": results, **kw}


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
    ({"pytest_exit": "1", "results": {"t1": "failed", "t2": "failed"}}, U, "never recorded"),   # no call recorded
    ({"patch_applied": False}, U, "did not apply"),
    (None, U, "never recorded"),
    (["not", "a", "body"], U, "never recorded"),
])
def test_one_reader_turns_a_test_report_into_three_states(got, state, why):
    seen = box.tests_seen(got, ["t1", "t2"])
    assert seen["state"] == state and why in seen["why"], seen
    assert seen["passed"] + seen["failed"] + seen["unknown"] == seen["tests"] == 2


# --- finding 5: the patch-size probe ----------------------------------------------------------------------------------------------
def test_finding_5_a_failed_size_probe_whose_output_holds_a_size_is_unknown_so_arm_r_is_inconclusive_never_clean():
    class FailedDocker:
        def sh(self, *args, **kw):
            return done(1, f"{we.PATCH_BYTES}=123\n".encode(), b"failed\n")

    result = we.review_once(SimpleNamespace(docker=FailedDocker(), arm="R"), SimpleNamespace(repo="/w/toy"),
                            SimpleNamespace(write_json=lambda *args: None), "ctr", [], "", None, 0, 0,
                            lambda: {"verdict": "none", "test": ""}, base="base")
    assert result["status"] == "inconclusive"


@pytest.mark.parametrize(("got", "size"), [(done(0, f"{we.PATCH_BYTES}=123\n".encode()), 123), (done(0, b"0 lines\n"), None),
                                           (done(1, f"{we.PATCH_BYTES}=0\n".encode()), None), (done(None), None),
                                           (done(-15, f"{we.PATCH_BYTES}=5\n".encode()), None)])
def test_the_size_is_read_only_from_a_call_seen_to_pass_with_its_own_line(got, size):
    docker = SimpleNamespace(sh=lambda *a, **kw: got)
    assert we.patch_bytes(SimpleNamespace(docker=docker), SimpleNamespace(repo="/w/r"), "agent", "base") == size
    assert we.loop_status(we.LOOP_CLEAN, we.patch_bytes(SimpleNamespace(docker=docker), SimpleNamespace(repo="/w/r"), "a", "b")) \
        == (we.LOOP_CLEAN if size else "inconclusive")


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
    assert got["validate"]["base"]["results"] == {"t::bug": "failed"}                   # the outcome as observed, kept
    assert cohorts.admit(got, {"task_image_id": ORACLE, "oracle_image_id": ORACLE}) == ""


# --- the oracle run keeps what it observed -------------------------------------------------------------------------------------
JUNIT = (b'<testsuites><testsuite><testcase classname="t" name="bug"><failure/></testcase>'
         b'<testcase classname="t" name="ok"/></testsuite></testsuites>')


class OracleDocker:
    """A check container: the pytest call answers ``said`` with ``code``, and the report's copy writes ``report`` (None: it fails)."""

    def __init__(self, said: bytes, code, report: bytes | None) -> None:
        self.said, self.code, self.report = said, code, report

    def __call__(self, *args, input=None, timeout=None, env=None):
        if args[0] == "cp" and args[1].endswith(we.JUNIT_IN):
            if self.report is None:
                return done(1, b"", b"Error: no such file")
            Path(args[2]).write_bytes(self.report)
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
    ctx = SimpleNamespace(docker=OracleDocker(said, code, report), g6={"commit": "0" * 40}, variant=we.ROUND3)
    got = we._oracle_run(ctx, task, label="base")
    assert (got["call"], got["pytest_exit"], got["report"]) == (call, exit, report is not None and b"broken" not in report), got
    assert box.tests_seen(got, ["t::bug", "t::ok"])["state"] == seen
    assert we.observed(got, ["t::bug"]) == {"call": call, "call_why": got["call_why"], "pytest_exit": exit,
                                            "report": got["report"], "results": {"t::bug": "failed"} if got["report"] else {}}


def test_the_round_trip_compares_bytes_it_read_never_a_capture_that_failed(tmp_path):
    class CaptureDocker:
        def __init__(self, capture_code, copies):
            self.capture_code, self.copies = capture_code, copies

        def sh(self, *a, **kw):
            return done(self.capture_code)

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
#: id), and a gold patch's run, on the same image ids, whose own report gives that test's node id below that module and saw it pass.
NEW, OLD = "tests.test_new::test_bug", "tests.test_old::test_ok"
ERRED = body({"::tests.test_new": "error", OLD: "passed"}, exit="2", nodes={OLD: "tests/test_old.py::test_ok"},
             collect_errors=["tests/test_new.py"])
GOLD_RUN = body({NEW: "passed", OLD: "passed"}, exit="0",
                nodes={NEW: "tests/test_new.py::test_bug", OLD: "tests/test_old.py::test_ok"}, collect_errors=[])


def test_a_base_collection_error_of_the_fail_to_pass_tests_module_is_seen_failing_when_the_gold_run_saw_it_pass():
    got = box.tests_seen(ERRED, [NEW], seen_passing_in=GOLD_RUN, fail_to_pass=[NEW])
    assert got["state"] == F and got["failed"] == 1, got
    assert box.tests_seen(ERRED, [NEW])["state"] == U, "without the gold run's report, a missing result stays unknown"


@pytest.mark.parametrize(("base", "gold", "why"), [
    ({**ERRED, "collect_errors": ["tests/test_other.py"]}, GOLD_RUN, "a module that holds no fail-to-pass test"),
    (ERRED, {**GOLD_RUN, "results": {NEW: "error", OLD: "passed"}}, "the gold run errors too"),
    (ERRED, {**GOLD_RUN, "results": {OLD: "passed"}}, "the gold run has no result for it"),
    (ERRED, {**GOLD_RUN, "nodes": {OLD: "tests/test_old.py::test_ok"}}, "the gold run's report gives no node id for it"),
    ({k: v for k, v in ERRED.items() if k != "collect_errors"}, GOLD_RUN, "the base's own report names no collection error: "
                                                                          "only its junit rendering does"),
    ({**ERRED, "collect_errors": ["tests/test_new"]}, GOLD_RUN, "an error whose node id is not the module the test is below"),
    ({**ERRED, "collect_errors": "tests/test_new.py"}, GOLD_RUN, "a report that is not a list of node ids"),
    ({**ERRED, "results": {OLD: "passed"}, "collect_errors": ["tests/test_old.py"]}, GOLD_RUN,
     "an error in the module of a pass-to-pass test only"),
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
         "the gold run errors too": (ERRED, {**GOLD_RUN, "results": {NEW: "error", OLD: "passed"}}),
         "the gold run has no result for it": (ERRED, {**GOLD_RUN, "results": {OLD: "passed"}}),
         "the gold run's report gives no node id for it": (ERRED, {**GOLD_RUN, "nodes": {OLD: "tests/test_old.py::test_ok"}}),
         "only junit's rendering names the error": ({k: v for k, v in ERRED.items() if k != "collect_errors"}, GOLD_RUN),
         "an error node id that is not the test's module": ({**ERRED, "collect_errors": ["tests/test_new"]}, GOLD_RUN),
         "a report that is not a list of node ids": ({**ERRED, "collect_errors": "tests/test_new.py"}, GOLD_RUN),
         "an error in the module of a pass-to-pass test only": ({**ERRED, "results": {OLD: "passed"},
                                                                 "collect_errors": ["tests/test_old.py"]}, GOLD_RUN)}


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


def test_an_oracle_run_asked_for_pytests_own_report_keeps_each_tests_node_id_and_each_failed_collection(tmp_path):
    lines = b"".join(json.dumps(x).encode() + b"\n" for x in (
        {"collect_error": "tests/test_new.py"}, {"node": "tests/test_old.py::test_ok", "junit": OLD},
        {"node": "tests/test_old.py::test_ok", "junit": OLD}))
    task = SimpleNamespace(id="toy-1", repo="/w/toy", test_modules=["t.py"])
    docker = ReportDocker(lines)
    got = we._oracle_run(SimpleNamespace(docker=docker, g6={"commit": "0" * 40}, variant=we.ROUND3), task, label="base", report=True)
    assert got["collect_errors"] == ["tests/test_new.py"] and got["nodes"] == {OLD: "tests/test_old.py::test_ok"}
    assert docker.written[we.PLUGIN_AT] == we.REPORT_PLUGIN.encode() and "-p we_report" in docker.scripts[-1]
    assert we.observed(got, [NEW, OLD])["nodes"] == {OLD: "tests/test_old.py::test_ok"}
    lost = we._oracle_run(SimpleNamespace(docker=ReportDocker(None), g6={"commit": "0" * 40}, variant=we.ROUND3), task,
                          label="base", report=True)
    assert lost["collect_errors"] is None and lost["nodes"] is None          # not recorded: the rule cannot apply
    plain = we._oracle_run(SimpleNamespace(docker=OracleDocker(b"exit=1\n", 0, JUNIT), g6={"commit": "0" * 40},
                                           variant=we.ROUND3), task, label="base")
    assert "nodes" not in plain and "collect_errors" not in plain            # the study's own runs are as they were


def test_the_report_plugin_gives_pytests_own_node_ids_and_the_junit_key_pytest_itself_computes(tmp_path):
    project, plugins = tmp_path / "project", tmp_path / "plugins"
    (project / "tests").mkdir(parents=True)
    plugins.mkdir()
    (project / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (project / "tests" / "test_new.py").write_text("from nowhere_at_all import thing\n\ndef test_bug():\n    pass\n",
                                                    encoding="utf-8")
    (project / "tests" / "test_old.py").write_text("class TestOld:\n    def test_ok(self):\n        pass\n\n"
                                                    "import pytest\n\n@pytest.mark.parametrize('x', [1, 2])\n"
                                                    "def test_p(x):\n    pass\n", encoding="utf-8")
    (plugins / "we_report.py").write_text(we.REPORT_PLUGIN, encoding="utf-8")
    report, junit = tmp_path / "report.jsonl", tmp_path / "junit.xml"
    import os
    done_ = __import__("subprocess").run(                  # the other module's tests run too, so one report holds both kinds
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "we_report", "-q", f"--junitxml={junit}",
         "-o", "junit_family=xunit2", "--continue-on-collection-errors", "tests"], cwd=project, capture_output=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(plugins), "WE_REPORT": str(report)})
    assert done_.returncode == 1, done_.stdout
    got = we.read_report(report.read_bytes())
    assert got["collect_errors"] == ["tests/test_new.py"], got
    keys = we.parse_junit(junit.read_text(encoding="utf-8"))
    assert {k for k in keys if keys[k] == "passed"} == set(got["nodes"]), (keys, got)     # the same tests, keyed as junit keys them
    assert got["nodes"]["tests.test_old.TestOld::test_ok"] == "tests/test_old.py::TestOld::test_ok"
    assert got["nodes"]["tests.test_old::test_p[2]"] == "tests/test_old.py::test_p[2]"


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
#: ``read_probes``), or a test report's results or exit taken from a body (``"results"``, ``"pytest_exit"``).
ATTRS, RAW, KEYS = {"returncode", "stdout", "stderr"}, {"out", "err", "parse_junit", "read_probes"}, {"results", "pytest_exit"}
#: The one reader, and the ``read`` each consumer hands it to take its answer from a call's output.
READERS = {("box_mcp.py", "seen"), ("box_mcp.py", "tests_seen"), ("box_mcp.py", "_run_seen"), ("box_mcp.py", "_collect_failed"),
           ("box_mcp.py", "seen_failing")}
#: Where the controls, the probe and the call phases start, and the host run, which the round trip reads only by its record's
#: status, its ``final_patch_read`` and the kept patch ``keep_final`` read: a boundary the walk below does not cross.
ROOTS = {("cohorts.py", n) for n in ("main", "controls", "manifest", "admit", "round_trip", "known_patch", "image_ids")} | \
        {("run.py", n) for n in ("patch_bytes", "keep_final", "workspace_from")} | {("box_mcp.py", n) for n in ("call", "settle", "serve")}
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
    assert {("run.py", "patch_bytes"), ("run.py", "_oracle_run"), ("run.py", "validate"), ("run.py", "selfcheck"),
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
