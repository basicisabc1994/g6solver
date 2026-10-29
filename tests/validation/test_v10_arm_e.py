# SPDX-License-Identifier: Apache-2.0
"""V10's arm E, model-written tests without G6 (docs/validation/patch_review_search/PROTOCOL.md on claude/vs, "Arm E
in detail"): its run mechanics only.

Development fixtures only: every repository, patch and model reply here is a tiny one made for these tests; none is
V9 or V10 evaluation data, and no model is called (the model is a fake that returns written replies). Most tests use a
recorded executor; the end-to-end ones run pytest on a fixture through V9's executor."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from patch_review_search import arm_e
from patch_review_utility import dispositions, executor

ACCEPTED = dispositions.make(dispositions.ACCEPT)
DEFECT = dispositions.make(dispositions.DEFECT, "arm 1 found a regression")
FROZEN = arm_e.Frozen(runs=1, feedback=False, seed=20261029, send_seed=False, run_s=60, limit_s=1800, call_cap=8)
BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
PASSES = "from calc import add\n\n\ndef test_sum():\n    assert add(2, 3) == 5\n"
FAILS = "from calc import add\n\n\ndef test_wrong():\n    assert add(2, 2) == 5\n"


def reply(*files: tuple[str, str]) -> str:
    return json.dumps({"files": [{"path": path, "text": text} for path, text in files]})


def placed(path: str, text: str) -> str:
    folder, _, name = path.rpartition("/")
    return "/".join(filter(None, [folder, "g6_check_" + hashlib.sha256(text.encode()).hexdigest()[:12], name]))


class Model:
    """A fake model: the replies it is given, in order, and every request it was sent."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request: dict) -> str:
        self.requests.append(request)
        found = self.replies.pop(0)
        if isinstance(found, BaseException):
            raise found
        return found


def phases(call: str) -> dict:
    if call == "setup_failed":
        return {"setup": "failed", "teardown": "passed"}
    return {"setup": "passed", "call": call, "teardown": "passed"}


class Recorded:
    """A recorded executor: for each placed file and tree ("patched" or "base"), one result per run, each a node to
    its call outcome, or ``"collection"`` / ``"timeout"`` for a run that never reached the tests."""

    def __init__(self, script: dict):
        self.script, self.calls = {key: list(value) for key, value in script.items()}, []

    def run(self, tree, tests, *, timeout_s, overlay=None):
        (file,) = {test.partition("::")[0] for test in tests}
        assert set(overlay["files"]) == {file}
        side = Path(tree).name
        self.calls.append((side, file))
        found = self.script[(side, file)].pop(0)
        if found == "timeout":
            return {"outcomes": {}, "phases": {}, "collection_errors": [], "exit": None, "seconds": 1.0,
                    "timed_out": True, "error": ""}
        if found == "collection":
            return {"outcomes": {}, "phases": {}, "collection_errors": [file], "exit": 2, "seconds": 1.0,
                    "timed_out": False, "error": ""}
        return {"outcomes": {}, "phases": {f"{file}::{node}": phases(call) for node, call in found.items()},
                "collection_errors": [], "exit": 0, "seconds": 1.0, "timed_out": False, "error": ""}


@pytest.fixture
def trees(tmp_path):
    """The base commit's tree and the patched tree of a tiny repository (folders named as ``Recorded`` reads them)."""
    found = {}
    for side, code in (("base", BAD), ("patched", GOOD)):
        root = tmp_path / side
        (root / "tests").mkdir(parents=True)
        (root / "calc.py").write_text(code, encoding="utf-8", newline="\n")
        (root / "tests" / "test_calc.py").write_text(PASSES, encoding="utf-8", newline="\n")
        found[side] = root
    return found


def run(trees, model, runner, *, arm_1=ACCEPTED, arm_a=ACCEPTED, c_calls=1, t_calls=0, frozen=FROZEN, **kwargs):
    return arm_e.arm_e(ask=model, patched_tree=trees["patched"], base_tree=trees["base"], patch="the patch",
                       contract="R1. Addition returns the sum.", context={"files": ["calc.py"]}, arm_1=arm_1,
                       arm_a=arm_a, c_calls=c_calls, t_calls=t_calls, frozen=frozen, executor=runner, **kwargs)


# --- the call count, the reply and the kept tests ------------------------------------------------------------------------

@pytest.mark.parametrize("c, t, cap, calls", [(0, 0, 8, 1), (3, 2, 8, 3), (1, 4, 8, 4), (9, 1, 5, 5)])
def test_e_makes_as_many_calls_as_the_larger_of_c_and_t_and_at_least_one(c, t, cap, calls):
    assert arm_e.calls(c, t, cap=cap) == calls


def test_a_reply_is_a_json_object_of_files_and_anything_else_cannot_be_parsed():
    assert arm_e.parse(reply(("tests/test_a.py", PASSES))) == [{"path": "tests/test_a.py", "text": PASSES}]
    assert arm_e.parse(json.dumps({"files": []})) == []
    for bad in ("not json", "[]", json.dumps({"files": [{"path": 1, "text": "x"}]}), json.dumps({"tests": []}),
                json.dumps({"files": [{"path": "tests/test_a.py"}]})):
        with pytest.raises(arm_e.Unparseable):
            arm_e.parse(bad)


def test_a_reply_holding_exactly_one_fenced_block_that_parses_is_read_whatever_prose_surrounds_it():
    # the paid rehearsal's third fault, and the coordinator's ruling on it (2026-10-29): exactly one fenced block whose
    # content parses as the registered object is read, prose before or after it ignored; the reading is counted
    body = reply(("tests/test_a.py", PASSES))
    files = [{"path": "tests/test_a.py", "text": PASSES}]
    assert arm_e.read(body) == (files, "json")
    for fenced in (f"```json\n{body}\n```", f"```\n{body}\n```\n", f"  ```json\n{body}\n```  "):
        assert arm_e.read(fenced) == (files, "fenced") and arm_e.parse(fenced) == files
    for prose in (f"Here are the tests:\n```json\n{body}\n```", f"```json\n{body}\n```\nAnd more.",
                  f"Looking at this:\n\n1. **R1** first\n\n```json\n{body}\n```\nThat is all."):
        assert arm_e.read(prose) == (files, "fenced_with_prose")
    for bad in (f"```json\n{body}\n```\n```json\n{body}\n```",                 # two blocks: refused
                f"Two:\n```json\n{body}\n```\nand\n```\n{{}}\n```",
                "Here:\n```json\n{not json\n```", "```json\n[]\n```",          # one block that does not parse
                f"Here are the tests: {body[:20]}", f"```json\n{body}"):       # no block, or one never closed
        with pytest.raises(arm_e.Unparseable):
            arm_e.read(bad)


def test_arm_e_counts_each_replys_reading(trees):
    body = reply(("tests/test_sum.py", PASSES))
    file = placed("tests/test_sum.py", PASSES)
    runner = Recorded({("patched", file): [{"test_sum": "passed"}], ("base", file): [{"test_sum": "passed"}]})
    got = run(trees, Model(body, f"Tests:\n```json\n{body}\n```"), runner, c_calls=2)
    assert [x["reading"] for x in got["exchanges"]] == ["json", "fenced_with_prose"]
    assert got["counts"]["readings"] == {"json": 1, "fenced": 0, "fenced_with_prose": 1}


@pytest.mark.parametrize("path", ["conftest.py", "tests/a_test.py", "tests/test-a.py", "../test_a.py", "/test_a.py",
                                  "tests\\test_a.py", "venv/test_a.py", ".hidden/test_a.py", "x.egg-info/test_a.py",
                                  "tests//test_a.py", "./test_a.py", "tests/__init__.py", "test_a.txt"])
def test_only_a_test_file_g6_would_place_is_kept(trees, path):
    kept, discarded = arm_e.keep(trees["patched"], [{"path": path, "text": PASSES, "call": 1}])
    assert kept == [] and discarded[0]["file"] == path and discarded[0]["why"]


def test_a_kept_file_is_placed_where_g6_places_a_free_form_check(trees):
    kept, discarded = arm_e.keep(trees["patched"], [{"path": "tests/test_sum.py", "text": PASSES, "call": 1},
                                                    {"path": "test_top.py", "text": PASSES, "call": 2}])
    assert discarded == []
    assert [k["placed"] for k in kept] == [placed("tests/test_sum.py", PASSES), placed("test_top.py", PASSES)]
    assert kept[0]["tests"] == [placed("tests/test_sum.py", PASSES) + "::test_sum"] and kept[1]["call"] == 2


def test_a_file_g6_could_not_place_or_already_kept_is_discarded_and_counted(trees):
    (trees["patched"] / "lib").write_text("a file, not a folder\n", encoding="utf-8")
    taken = placed("tests/test_taken.py", FAILS).rpartition("/")[0]
    (trees["patched"] / taken).mkdir()
    pool = [{"path": "lib/test_a.py", "text": PASSES, "call": 1},
            {"path": "tests/test_taken.py", "text": FAILS, "call": 1},
            {"path": "tests/test_sum.py", "text": PASSES, "call": 1},
            {"path": "tests/test_sum.py", "text": PASSES, "call": 2},
            {"path": "tests/test_empty.py", "text": "VALUE = 1\n", "call": 2}]
    kept, discarded = arm_e.keep(trees["patched"], pool)
    assert [k["file"] for k in kept] == ["tests/test_sum.py"]
    assert [d["file"] for d in discarded] == ["lib/test_a.py", "tests/test_taken.py", "tests/test_sum.py",
                                              "tests/test_empty.py"]


def test_at_most_twenty_test_functions_are_kept_in_file_order(trees):
    def many(prefix: str, n: int) -> str:
        return "".join(f"def test_{prefix}{i}():\n    pass\n\n\n" for i in range(n))

    text = "class TestGroup:\n    def test_one(self):\n        pass\n\n    def helper(self):\n        pass\n\n\n"
    pool = [{"path": "tests/test_a.py", "text": many("a", 8), "call": 1},
            {"path": "tests/test_b.py", "text": text + many("b", 10), "call": 1},
            {"path": "tests/test_c.py", "text": many("c", 5), "call": 2},
            {"path": "tests/test_d.py", "text": many("d", 1), "call": 2}]
    kept, discarded = arm_e.keep(trees["patched"], pool)
    assert sum(len(k["tests"]) for k in kept) == arm_e.MAX_TESTS == 20
    assert kept[1]["tests"][0].endswith("::TestGroup::test_one") and len(kept[1]["tests"]) == 11
    assert [t.rpartition("::")[2] for t in kept[2]["tests"]] == ["test_c0"]
    assert [(d["file"], d["test"]) for d in discarded] == [
        ("tests/test_c.py", f"test_c{i}") for i in range(1, 5)] + [("tests/test_d.py", "test_d0")]


def test_a_file_that_cannot_be_parsed_is_kept_as_one_test_that_will_not_pass(trees):
    kept, _ = arm_e.keep(trees["patched"], [{"path": "tests/test_broken.py", "text": "def test_(:\n", "call": 1}])
    assert kept[0]["tests"] == [placed("tests/test_broken.py", "def test_(:\n")]


# --- runs, readings and the disposition ----------------------------------------------------------------------------------

def test_e_accepts_when_arm_1_accepts_and_every_kept_test_passes_and_runs_them_at_the_base_too(trees):
    file = placed("tests/test_sum.py", PASSES)
    runner = Recorded({("patched", file): [{"test_sum": "passed"}], ("base", file): [{"test_sum": "failed"}]})
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES))), runner)
    assert got["disposition"]["accepted"] is True and got["disposition"]["category"] == dispositions.ACCEPT
    assert runner.calls == [("patched", file), ("base", file)]
    assert got["tests"][file + "::test_sum"]["base"] == ["failed"] and got["findings"] == []
    assert got["e_filtered"]["accepted"] is True and got["a_and_e"]["accepted"] is True


def test_a_kept_test_that_fails_in_its_body_is_a_finding_and_e_does_not_accept(trees):
    file = placed("tests/test_wrong.py", FAILS)
    runner = Recorded({("patched", file): [{"test_wrong": "failed"}], ("base", file): [{"test_wrong": "failed"}]})
    got = run(trees, Model(reply(("tests/test_wrong.py", FAILS))), runner)
    assert got["disposition"]["accepted"] is False and got["disposition"]["category"] == dispositions.DEFECT
    assert [f["node"] for f in got["findings"]] == [file + "::test_wrong"] and got["findings"][0]["file"] == file
    assert got["findings"][0]["text"] == FAILS                     # a reproduction the reruns can run on its own
    assert got["e_filtered"]["accepted"] is True               # it fails at the base too, so it is set aside
    assert got["a_and_e"]["accepted"] is False


@pytest.mark.parametrize("patched", [["collection"], ["timeout"], [{"test_sum": "setup_failed"}]])
def test_a_kept_test_that_cannot_be_collected_set_up_or_finished_does_not_pass_and_is_no_finding(trees, patched):
    file = placed("tests/test_sum.py", PASSES)
    runner = Recorded({("patched", file): patched, ("base", file): [{"test_sum": "passed"}]})
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES))), runner)
    assert got["disposition"]["accepted"] is False and got["disposition"]["category"] == dispositions.ABSTAIN
    assert got["findings"] == [] and got["counts"]["errors"] == 1
    assert got["e_filtered"]["accepted"] is True               # an error is set aside in E-filtered


def test_a_test_with_no_recorded_outcome_does_not_pass(trees):
    file = placed("tests/test_sum.py", PASSES)
    runner = Recorded({("patched", file): [{"test_other": "passed"}], ("base", file): [{}]})
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES))), runner)
    assert got["disposition"]["accepted"] is False and got["tests"][file + "::test_sum"]["patched"] == ["missing"]
    assert got["e_filtered"]["accepted"] is False


def test_each_kept_test_runs_r_times_and_passes_only_in_more_than_half_of_them(trees):
    frozen = arm_e.Frozen(runs=3, feedback=False, seed=1, send_seed=False, run_s=60, limit_s=1800, call_cap=8)
    file = placed("tests/test_sum.py", PASSES)
    two = "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n"
    other = placed("tests/test_two.py", two)
    runner = Recorded({("patched", file): [{"test_sum": "passed"}, {"test_sum": "failed"}, {"test_sum": "passed"}],
                       ("patched", other): [{"test_a": "passed", "test_b": "failed"},
                                            {"test_a": "failed", "test_b": "failed"},
                                            {"test_a": "skipped", "test_b": "passed"}],
                       ("base", file): [{}] * 3, ("base", other): [{}] * 3})
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES), ("tests/test_two.py", two))), runner, frozen=frozen)
    assert got["tests"][file + "::test_sum"]["passes"] is True
    assert got["tests"][other + "::test_a"]["passes"] is False and got["tests"][other + "::test_b"]["finding"] is True
    assert len(runner.calls) == 12 and got["disposition"]["category"] == dispositions.DEFECT


def test_a_parametrised_test_passes_only_when_every_case_passes(trees):
    file = placed("tests/test_sum.py", PASSES)
    runner = Recorded({("patched", file): [{"test_sum[1]": "passed", "test_sum[2]": "failed"}],
                       ("base", file): [{}]})
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES))), runner)
    assert got["tests"][file + "::test_sum"]["patched"] == ["failed"]


def test_when_arm_1_does_not_accept_e_does_not_either_and_keeps_arm_1s_category(trees):
    file = placed("tests/test_sum.py", PASSES)
    runner = Recorded({("patched", file): [{"test_sum": "passed"}], ("base", file): [{"test_sum": "passed"}]})
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES))), runner, arm_1=DEFECT)
    assert got["disposition"]["accepted"] is False and got["disposition"]["category"] == dispositions.DEFECT
    assert got["e_filtered"]["accepted"] is False and got["a_and_e"]["accepted"] is True


def test_a_parsed_reply_with_no_test_file_gives_arm_1s_disposition_and_is_counted(trees):
    for arm_1 in (ACCEPTED, DEFECT):
        got = run(trees, Model(reply(), reply()), Recorded({}), arm_1=arm_1, c_calls=2)
        assert got["disposition"]["accepted"] is arm_1["accepted"] and got["counts"]["no_test_file"] is True
        assert got["disposition"]["category"] == arm_1["category"] and got["calls"] == {"due": 2, "made": 2}
    got = run(trees, Model(reply(("conftest.py", PASSES))), Recorded({}))
    assert got["disposition"]["accepted"] is True and got["counts"] == {**got["counts"], "no_test_file": False,
                                                                       "none_kept": True, "discarded": 1}


# --- tool errors ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("failure, kind", [(RuntimeError("the provider said 529"), arm_e.PROVIDER_ERROR),
                                           (TimeoutError("no reply"), arm_e.TIME_OUT), ("{not json", arm_e.UNPARSEABLE)])
def test_a_provider_error_a_time_out_or_an_unparseable_reply_is_a_counted_tool_error(trees, failure, kind):
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES)), failure), Recorded({}), c_calls=2)
    assert got["disposition"]["category"] == dispositions.ERROR and got["disposition"]["error_kind"] == kind
    assert got["e_filtered"] == got["disposition"] and got["a_and_e"] == got["disposition"]
    assert got["calls"]["made"] == 2 and len(got["exchanges"]) == 2


def test_a_patch_reached_after_es_share_of_spending_is_a_tool_error_and_calls_stop_at_the_share(trees):
    model = Model(reply(("tests/test_sum.py", PASSES)))
    got = run(trees, model, Recorded({}), share_left=lambda: False)
    assert got["disposition"]["error_kind"] == arm_e.SPEND_SHARE and model.requests == []
    left = iter([True, False])
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES))), Recorded({}), c_calls=2,
              share_left=lambda: next(left))
    assert got["disposition"]["error_kind"] == arm_e.SPEND_SHARE and got["calls"]["made"] == 1


def test_the_arms_wall_clock_limit_ends_it_as_a_time_out(trees):
    now = [0.0]

    def slow(request):
        assert request["timeout_s"] == FROZEN.limit_s
        now[0] = FROZEN.limit_s + 1.0
        return reply(("tests/test_sum.py", PASSES))

    got = run(trees, slow, Recorded({}), clock=lambda: now[0])            # no run is started past the limit
    assert got["disposition"]["error_kind"] == arm_e.TIME_OUT and got["disposition"]["time_or_cap_reached"] is True


def test_an_arm_1_without_a_disposition_leaves_e_none_of_its_own(trees):
    broken = dispositions.make(dispositions.ERROR, "the suite cannot run at the base commit", error_kind="tool_error")
    file = placed("tests/test_sum.py", PASSES)
    runner = Recorded({("patched", file): [{"test_sum": "passed"}], ("base", file): [{"test_sum": "passed"}]})
    got = run(trees, Model(reply(("tests/test_sum.py", PASSES))), runner, arm_1=broken)
    assert got["disposition"]["category"] == dispositions.ERROR and got["disposition"]["error_kind"] == arm_e.ARM_1


# --- the frozen parameters: feedback and the seed --------------------------------------------------------------------------

def test_without_feedback_every_call_comes_first_and_no_request_carries_results(trees):
    files = [placed("tests/test_a.py", PASSES), placed("tests/test_b.py", FAILS)]
    runner = Recorded({("patched", files[0]): [{"test_sum": "passed"}], ("patched", files[1]): [{"test_wrong": "failed"}],
                       ("base", files[0]): [{}], ("base", files[1]): [{}]})
    model = Model(reply(("tests/test_a.py", PASSES)), reply(("tests/test_b.py", FAILS)))
    got = run(trees, model, runner, c_calls=2)
    assert [r["feedback"] for r in model.requests] == [None, None]
    assert [r["call"] for r in model.requests] == [1, 2] and model.requests[0]["context"] == {"files": ["calc.py"]}
    assert runner.calls[:2] == [("patched", files[0]), ("patched", files[1])] and got["calls"]["made"] == 2


def test_the_seed_is_recorded_and_sent_to_the_model_only_when_the_freeze_says_so(trees):
    model = Model(reply())
    got = run(trees, model, Recorded({}))
    assert "seed" not in model.requests[0] and got["frozen"]["seed"] == FROZEN.seed
    assert got["frozen"]["send_seed"] is False
    sending = arm_e.Frozen(runs=1, feedback=False, seed=99, send_seed=True, run_s=60, limit_s=1800, call_cap=8)
    model = Model(reply())
    run(trees, model, Recorded({}), frozen=sending)
    assert model.requests[0]["seed"] == 99


SEARCH = '''\
RUN_S = 45


def _run_check(change, *, at_base, timeout_s):
    return {}


def search(change, checks):
    def read(at_base):
        return _run_check(change, at_base=at_base, timeout_s=RUN_S)

    for one in checks:
        on_patch = read(at_base=False)
        {loop}
        at_base = read(at_base=True)
'''


def g6_search(tmp_path, loop: str = "pass", name: str = "search_fixture"):
    """A stand-in for G6's verification search module (only what ``g6_parameters`` reads), loaded from a file."""
    import importlib.util
    path = tmp_path / f"{name}.py"
    path.write_text(SEARCH.replace("{loop}", loop), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r_and_each_runs_limit_are_read_from_g6s_verification_search(tmp_path):
    module = g6_search(tmp_path)
    assert arm_e.g6_parameters(module) == {"runs": 1, "run_s": 45}
    frozen = arm_e.frozen_from_g6(module, feedback=True, seed=3, send_seed=False, limit_s=1800, call_cap=8)
    assert (frozen.runs, frozen.run_s, frozen.feedback) == (1, 45, True)
    twice = g6_search(tmp_path, "on_patch = read(at_base=False)", "search_twice")
    assert arm_e.g6_parameters(twice)["runs"] == 2


def test_a_change_in_how_g6_runs_a_check_is_refused_not_guessed(tmp_path):
    looped = g6_search(tmp_path, "for _ in range(3):\n            read(at_base=False)", "search_looped")
    with pytest.raises(ValueError, match="shape"):
        arm_e.g6_parameters(looped)
    without = g6_search(tmp_path, name="search_without")
    del without.RUN_S
    with pytest.raises(ValueError, match="RUN_S"):
        arm_e.g6_parameters(without)


def test_a_file_over_g6s_64_kilobytes_is_discarded_as_g6_refuses_it(trees):
    head = "def test_big():\n    pass\n"
    at_limit = head + "#" * (arm_e.MAX_SOURCE_BYTES - len(head) - 1) + "\n"
    over = at_limit + "#\n"
    assert len(at_limit.encode()) == arm_e.MAX_SOURCE_BYTES == 64 * 1024
    kept, discarded = arm_e.keep(trees["patched"], [{"path": "tests/test_limit.py", "text": at_limit, "call": 1},
                                                    {"path": "tests/test_over.py", "text": over, "call": 1},
                                                    {"path": "tests/test_odd.py", "text": "\ud800", "call": 1}])
    assert [k["file"] for k in kept] == ["tests/test_limit.py"]
    assert [d["file"] for d in discarded] == ["tests/test_over.py", "tests/test_odd.py"]
    assert "65536" in discarded[0]["why"] or "64 kilobytes" in discarded[0]["why"]


def test_with_feedback_each_call_after_the_first_gets_the_results_of_the_tests_kept_so_far(trees):
    frozen = arm_e.Frozen(runs=1, feedback=True, seed=7, send_seed=False, run_s=60, limit_s=1800, call_cap=8)
    files = [placed("tests/test_a.py", PASSES), placed("tests/test_b.py", FAILS)]
    runner = Recorded({("patched", files[0]): [{"test_sum": "passed"}], ("patched", files[1]): [{"test_wrong": "failed"}],
                       ("base", files[0]): [{}], ("base", files[1]): [{}]})
    model = Model(reply(("tests/test_a.py", PASSES)), reply(("tests/test_b.py", FAILS)))
    got = run(trees, model, runner, c_calls=2, frozen=frozen)
    assert model.requests[0]["feedback"] is None
    assert model.requests[1]["feedback"] == [{"file": "tests/test_a.py", "test": files[0] + "::test_sum",
                                              "outcomes": ["passed"]}]
    assert runner.calls == [("patched", files[0]), ("patched", files[1]), ("base", files[0]), ("base", files[1])]
    assert got["disposition"]["category"] == dispositions.DEFECT


def test_frozen_parameters_are_checked():
    for bad in ({"runs": 0}, {"run_s": 0}, {"limit_s": 0}, {"call_cap": 0}):
        with pytest.raises(ValueError):
            arm_e.Frozen(**{**dict(runs=1, feedback=False, seed=1, send_seed=False, run_s=60, limit_s=1800, call_cap=8), **bad})


# --- end to end, with V9's executor ---------------------------------------------------------------------------------------

def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_end_to_end_e_runs_each_kept_file_on_its_own_with_v9s_executor(trees):
    broken = "import nowhere_to_be_found\n\n\ndef test_never():\n    pass\n"
    model = Model(reply(("tests/test_sum.py", PASSES), ("tests/test_wrong.py", FAILS),
                        ("tests/test_broken.py", broken)))
    got = run(trees, model, executor.Executor())
    sums, wrong = placed("tests/test_sum.py", PASSES), placed("tests/test_wrong.py", FAILS)
    assert got["tests"][sums + "::test_sum"] == {**got["tests"][sums + "::test_sum"], "patched": ["passed"],
                                                 "base": ["failed"], "passes": True}
    assert got["tests"][wrong + "::test_wrong"]["finding"] is True
    assert got["tests"][placed("tests/test_broken.py", broken) + "::test_never"]["patched"] == ["error"]
    assert got["disposition"]["category"] == dispositions.DEFECT and got["counts"]["errors"] == 1
    assert not any((trees["patched"] / "tests").glob("g6_check_*")) and os.listdir(trees["base"] / "tests") == [
        "test_calc.py"]
