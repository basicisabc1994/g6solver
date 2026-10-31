# SPDX-License-Identifier: Apache-2.0
"""Arm L: the runner, not the agent, holds the loop (PLAN.md, 2026-10-31)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import distill  # noqa: E402
import run as we  # noqa: E402

TREE = {"goal": "fix it", "branches": [
    {"goal": "the symptom goes", "shapes": [{"shape": "the issue's input", "probe": "test_shapes.py::test_a"},
                                            {"shape": "one level deeper", "probe": "test_shapes.py::test_b"}]},
    {"goal": "others keep working", "shapes": [{"shape": "ordinary input", "probe": ""}]}]}


def test_l_runs_on_training_bugs_and_m_on_held_out_ones():
    sonnet, train, held = we.R3_MODELS[1], "black-5634", we.R3_HELD[0]
    assert we.r3_loop_key((sonnet, train, "L1", 1))
    assert we.r3_loop_key((sonnet, held, "M1", 5))
    assert not we.r3_loop_key((sonnet, held, "L1", 1))
    assert not we.r3_loop_key((sonnet, train, "M1", 1))
    assert not we.r3_loop_key((sonnet, train, "L1", 3))


def test_the_loops_prompt_points_at_the_tree_and_names_no_bug():
    task = we.load_tasks()[0]
    text = we.prompt(task, "a title", "a body", "L1")
    assert f"{we.WE}/tree.json" in text and "a body" in text
    assert we.prompt(task, "a title", "a body", "M1") == text
    assert distill.named(we.LOOP_ADDENDUM + we.SCOUT_PROMPT, [], []) == []


def test_the_scout_never_writes_to_the_repository_and_is_told_the_run_command():
    assert we.PROBE_RUN in we.SCOUT_PROMPT and f"{we.WE}/probes" in we.SCOUT_PROMPT
    assert "maintainers" not in we.SCOUT_PROMPT.lower()


def test_the_scouts_tree_becomes_a_goal_tree_and_reports_shapes_with_no_probe():
    got = we.scout_tree(json.dumps(TREE))
    assert got["shapes"] == 3 and got["open"] == ["ordinary input"]
    assert "- [root.0.1] one level deeper" in got["text"]


def test_an_unreadable_tree_is_reported_not_raised():
    assert we.scout_tree("not json")["error"]
    assert we.scout_tree(json.dumps({"goal": "x", "branches": []}))["error"]


def test_the_stop_hook_blocks_only_on_a_failing_probe_and_lets_go():
    hook = we.stop_hook("/repo")
    assert we.PROBE_RUN in hook and '"decision": "block"' in hook
    assert f'-ge {we.MAX_STOPS}' in hook
    assert "--deselect" in hook and f"{we.WE}/waived.txt" in hook


def test_the_thread_stops_where_the_fix_begins_and_skips_bots():
    def note(at, body, kind="User"):
        return {"created_at": at, "body": body, "user": {"type": kind}, "author_association": "MEMBER"}

    got = we.thread_text([note("2026-01-01T00:00:00Z", "a plan"), note("2026-01-02T00:00:00Z", "ci noise", "Bot"),
                          note("2026-03-01T00:00:00Z", "the fix is here")], "2026-02-01T00:00:00Z")
    assert "a plan" in got and "ci noise" not in got and "the fix is here" not in got
    assert we.thread_text([], "2026-02-01T00:00:00Z") == ""
    long = we.thread_text([note("2026-01-01T00:00:00Z", "x" * 99999)], "2026-02-01T00:00:00Z")
    assert len(long) <= we.MAX_THREAD_CHARS + 200


def test_arm_t_is_plain_claude_code_on_any_bug_and_the_thread_goes_to_t_and_the_loop():
    sonnet = we.R3_MODELS[1]
    assert we.r3_loop_key((sonnet, "black-5634", "T", 2))
    assert we.r3_loop_key((sonnet, we.R3_HELD[0], "T", 5))
    assert not we.r3_loop_key((sonnet, "black-5634", "T", 3))
    task = we.load_tasks()[0]
    assert we.prompt(task, "t", "b", "T") == we.prompt(task, "t", "b", "A")
    assert [a for a in ("A", "E", "G1", "H1", "T", "L1", "M1") if we.gets_thread(a)] == ["T", "L1", "M1"]


def test_scouts_from_l2_combine_dimensions_and_leave_no_shape_unclear():
    assert we.scout_prompt("L1") == we.SCOUT_PROMPT
    for arm in ("L2", "M2", "L3"):
        text = we.scout_prompt(arm)
        assert we.SCOUT_V2 in text and text.index(we.SCOUT_V2) < text.index("5. Critique")
    assert distill.named(we.SCOUT_V2, [], []) == []


CROSS = json.dumps({"goal": "g", "branches": [], "dimensions": {"scope": ["top", "def", "if"], "tail": ["", "#c"]}})


def test_the_gate_passes_probes_that_fail_at_base_and_cover_the_product():
    assert we.scout_gate(CROSS, exit_code=1, collected=6) == []


def test_the_gate_refuses_probes_that_cannot_run_or_reproduce_nothing():
    assert "could not run" in we.scout_gate(CROSS, exit_code=2, collected=6)[0]
    assert "none fails" in we.scout_gate(CROSS, exit_code=0, collected=6)[0]


def test_the_gate_counts_the_product_itself_rather_than_trusting_the_scout():
    assert "4 of 6" in we.scout_gate(CROSS, exit_code=1, collected=4)[0]


def test_the_gate_needs_two_dimensions_and_caps_the_product():
    one = json.dumps({"dimensions": {"scope": ["a", "b"]}})
    assert "two dimensions" in we.scout_gate(one, exit_code=1, collected=2)[0]
    assert "two dimensions" in we.scout_gate("no tree", exit_code=1, collected=0)[0]
    big = json.dumps({"dimensions": {"a": list("abcdefgh"), "b": list("abcdefgh"), "c": list("abcdefgh")}})
    assert we.scout_gate(big, exit_code=1, collected=we.MAX_CROSS) == []


def test_scouts_from_l3_declare_dimensions_and_the_second_try_continues_the_session():
    assert we.SCOUT_V3 not in we.scout_prompt("L2")
    assert we.SCOUT_V3 in we.scout_prompt("L3") and we.SCOUT_V2 in we.scout_prompt("M3")
    assert distill.named(we.SCOUT_V3, [], []) == []
    assert we.scout_again(["claude", "-p", "--disallowedTools", "WebFetch"]) == [
        "claude", "-p", "--continue", "--disallowedTools", "WebFetch"]


def test_a_probe_that_stops_collecting_on_the_patch_blocks_too():
    hook = we.stop_hook("/repo")
    assert '"$code" -ne 1 ] && [ "$code" -ne 2' in hook


def test_counting_the_cross_keeps_each_test_on_its_own_line():
    assert we.PROBE_RUN.count(" -q") == 1 and we.PROBE_COLLECT.count(" -q") == 1   # two make pytest print file totals
    assert "--collect-only" in we.PROBE_COLLECT


def test_a_critic_reads_the_tree_from_l4_and_names_no_bug():
    assert we.has_critic("L4") and we.has_critic("M4")
    assert not we.has_critic("L3") and not we.has_critic("M1") and not we.has_critic("L5")
    text = we.critic_prompt(1, "a title", "a body")
    assert f"{we.WE}/tree.json" in text and "a body" in text and we.PROBE_RUN in text
    assert distill.named(we.CRITIC_PROMPT, [], []) == []


def test_the_audit_finds_a_drive_but_not_a_word_before_an_escaped_newline():
    assert distill.named(r"read file:\n then stop: e.g. line:\r\n", [], []) == []
    assert distill.named(r"see C:\Users and also (D:\temp)", [], []) == [r"C:\ ".strip(), r"D:\ ".strip()]


def _repairs(results, asked):
    """G6's ``cegis`` over a tree whose checks fail as ``results`` says, one entry a round."""
    from g6.cegis import InputDomain, cegis

    answers = iter(results)
    loop = we.Repairs(lambda: next(answers), lambda text, n: asked.append((n, text)))
    got = cegis(we.WorkingTree(), lambda **_: True, domain=InputDomain.of({"probes": ["all"]}), runner=loop,
                max_iterations=we.MAX_REPAIRS + 1, timeout_s=60.0)
    return got, loop


def test_clean_probes_end_the_loop_without_asking_the_agent_anything():
    asked = []
    got, loop = _repairs([(set(), "")], asked)
    assert got.status == "no_counterexample_in_domain" and asked == [] and loop.round == 1


def test_a_failure_goes_back_to_the_agent_and_a_repair_ends_the_loop():
    asked = []
    got, loop = _repairs([({"h:a"}, "a fails"), (set(), "")], asked)
    assert got.status == "no_counterexample_in_domain" and [n for n, _ in asked] == [1]
    assert "a fails" in asked[0][1]


def test_a_failure_the_agent_has_already_seen_is_not_sent_again():
    asked = []
    got, _ = _repairs([({"h:a", "h:b"}, "both"), ({"h:b"}, "b still")], asked)
    assert len(asked) == 1 and got.status == "inconclusive"


def test_a_new_failure_after_a_repair_goes_back():
    asked = []
    got, _ = _repairs([({"h:a"}, "a"), ({"h:b"}, "b"), (set(), "")], asked)
    assert len(asked) == 2 and got.status == "no_counterexample_in_domain"


def test_the_repairs_are_capped():
    asked = []
    got, _ = _repairs([({f"h:{i}"}, str(i)) for i in range(9)], asked)
    assert len(asked) == we.MAX_REPAIRS and got.status == "counterexample_budget_exhausted"


def test_the_gate_refuses_a_failure_that_is_a_crash_not_an_assertion():
    assert "not an assertion" in we.scout_gate(CROSS, exit_code=1, collected=6, broken=3)[0]


def test_the_gate_reads_pytests_own_words_for_a_crash(tmp_path):
    """Real pytest output: an assertion and a ``pytest.fail`` are probes doing their job, a TypeError is a broken probe."""
    (tmp_path / "test_g.py").write_text(
        "import pytest\n\n\ndef test_a():\n    assert 1 == 2\n\n\ndef test_b():\n    raise TypeError('x')\n\n\n"
        "def test_c():\n    pytest.fail('no')\n\n\ndef test_d():\n    raise AssertionError('y')\n", "utf-8")
    command = we.gate_flags().split()
    done = subprocess.run([sys.executable, "-m", "pytest", str(tmp_path), "-q", "--tb=short", "-p", "no:cacheprovider",
                           "--rootdir", str(tmp_path), *command], capture_output=True, text=True,
                          env={**os.environ, "COLUMNS": "300"}, cwd=tmp_path)
    assert we.crashes(done.stdout) == 1


def test_the_gate_reads_the_containers_three_numbers():
    assert we.gate_numbers("FAILED a.py::t - assert 1\nexit=1\ncross=6\n") == (1, 6, 0)
    assert we.gate_numbers("FAILED a.py::t - TypeError: x\nexit=1\ncross=0\n") == (1, 0, 1)
    assert we.gate_numbers("nothing useful") == (2, 0, 0)


def test_a_plugin_cannot_reformat_the_summary_the_runner_reads():
    assert "-p no:pretty" in we.PROBE_RUN and "-p no:pretty" in we.PROBE_COLLECT


def test_pytests_exit_decides_whether_probes_failed_when_the_names_cannot_be_read():
    assert we.probe_failures("FAILED a/test_x.py::t - assert 1\nexit=1\n", "held0") == {"held0:test_x.py::t"}
    assert we.probe_failures("Results (0.17s):\n  10 failed\n  24 passed\nexit=1\n", "held0") == {"held0:exit 1"}
    assert we.probe_failures("garbage, no exit line", "held0") == {"held0:no exit"}


def test_clean_or_empty_probes_are_not_failures():
    assert we.probe_failures("5 passed\nexit=0\n", "scout") == set()
    assert we.probe_failures("no tests ran\nexit=5\n", "scout") == set()


def test_fresh_bugs_run_on_a_and_t_and_the_held_out_form_of_the_loop_only():
    sonnet, fresh = we.R3_MODELS[1], we.load_tasks(we.TASKS_FRESH)[0].id
    assert fresh not in {t.id for t in we.load_tasks()}
    for arm in ("A", "T", "M7"):
        assert we.r3_loop_key((sonnet, fresh, arm, 2)), arm
    for arm in ("E", "L7", "G1", "H1"):
        assert not we.r3_loop_key((sonnet, fresh, arm, 1)), arm
    assert not we.r3_loop_key((sonnet, fresh, "M7", 3))
    assert we.task_by_id(fresh).id == fresh


def test_probes_run_in_the_projects_environment():
    assert we.PROBE_RUN.startswith(".venv/bin/python -m pytest")
    assert we.PROBE_COLLECT.startswith(".venv/bin/python -m pytest")


def test_a_hidden_scout_works_elsewhere_and_is_told_nothing_of_the_first():
    text = we.scout_prompt("L5", hidden=True)
    assert we.HIDDEN in text and f"{we.WE}/tree.json" not in text and f"{we.WE}/probes" not in text
    assert we.scout_prompt("L5").count(we.HIDDEN) == 0


def test_from_l5_the_scout_may_leave_a_silent_shape_out_and_must_assert_exact_values():
    text = we.scout_prompt("L5")
    assert we.SCOUT_V2 not in text and "Never leave a shape as unclear" not in text
    assert "silent" in text and "exact" in text
    assert distill.named(we.SCOUT_V5, [], []) == []


def test_from_l5_the_prompt_carries_g1_and_no_way_to_waive():
    task = we.load_tasks()[0]
    text = we.prompt(task, "a title", "a body", "L5")
    assert "waived" not in text and "further checks you cannot see" in text
    assert (we.HARNESS / "G1.txt").read_text("utf-8").strip()[:60] in text
    assert we.prompt(task, "a title", "a body", "M5") == text
    assert distill.named(we.LOOP_ADDENDUM_5, [], []) == []


def test_l6_scouts_read_the_code_and_compose_at_random():
    text = we.scout_prompt("L6")
    assert we.SCOUT_V6 in text and we.SCOUT_V5 not in text and "test_random" in text and "in the code" in text
    assert "test_random" not in we.scout_prompt("L5")
    assert distill.named(we.SCOUT_V6, [], []) == []


def test_two_hidden_scouts_from_l6_each_in_its_own_place():
    assert we.hidden_scouts("L5") == 1 and we.hidden_scouts("M6") == 2 and we.hidden_scouts("L7") == 2
    assert we.hidden_at(0) == (we.HIDDEN, we.HIDDEN_TREE)
    assert we.hidden_at(1) == (f"{we.WE}/hidden2", f"{we.WE}/hidden2_tree.json")
    text = we.scout_prompt("L6", hidden=True, at=1)
    assert f"{we.WE}/hidden2/" in text or f"{we.WE}/hidden2 " in text
    assert f"{we.WE}/hidden2_tree.json" in text and f"{we.WE}/probes" not in text


def test_the_gate_counts_generated_inputs_when_it_is_asked_to():
    assert we.scout_gate(CROSS, exit_code=1, collected=6, randoms=we.MIN_RANDOM) == []
    assert f"12 of {we.MIN_RANDOM}" in we.scout_gate(CROSS, exit_code=1, collected=6, randoms=12)[0]
    assert we.scout_gate(CROSS, exit_code=1, collected=6) == []


def test_the_container_reports_the_generated_cases():
    assert we.random_cases("exit=1\ncross=6\nrandom=300\n") == 300
    assert we.random_cases("exit=1\n") == 0


def test_arms_before_l5_keep_the_stop_hook_and_l5_has_the_outer_loop():
    assert we.has_hook("L1") and we.has_hook("M4") and not we.has_hook("L5")
    assert we.holds_loop("L5") and we.holds_loop("M7") and not we.holds_loop("L4")


def test_failures_are_read_from_pytests_summary():
    text = "FAILED ../../opt/we/hidden/test_eof.py::test_cross[a-b] - assert 1\nERROR ../x/test_y.py\n1 failed, 1 error\n"
    assert we.failed_ids(text) == {"test_eof.py::test_cross[a-b]", "test_y.py"}


JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="test_x" name="test_cross[fmt: off-tail]"/>
<testcase classname="test_x" name="test_cross[fmt: off-head]"><failure message="assert 1 == 2">the traceback</failure></testcase>
<testcase classname="test_x" name="test_cross[other]"><skipped message="cannot occur"/></testcase>
<testcase classname="test_x" name="test_random[1]"/>
<testcase classname="test_x" name="test_boom"><error message="fixture broke">its traceback</error></testcase>
</testsuite></testsuites>"""


def _said(code, xml=JUNIT):
    return f"console tail\nexit={code}\n{we.JUNIT_MARK}\n{xml}"


def _run(**cases):
    return we.ProbeRun(1, {f"t::{k}": v for k, v in cases.items()}, {f"t::{k}": f"why {k}" for k in cases})


def test_l8_on_reads_pytests_own_report():
    assert not we.qualified("L7") and not we.qualified("M7") and we.qualified("L8") and we.qualified("M9")


def test_the_report_keeps_a_parametrized_id_whole_so_two_cases_stay_two():
    got = we.read_probes(_said(1))
    assert got.exit == 1 and {"test_x::test_cross[fmt: off-tail]", "test_x::test_cross[fmt: off-head]"} <= set(got.results)
    assert got.results["test_x::test_cross[fmt: off-tail]"] == "passed"
    assert got.results["test_x::test_cross[fmt: off-head]"] == "failed"


def test_the_report_gives_each_failure_its_own_words():
    got = we.read_probes(_said(1))
    assert "assert 1 == 2" in got.texts["test_x::test_cross[fmt: off-head]"]
    assert "the traceback" in got.texts["test_x::test_cross[fmt: off-head]"]
    assert "fixture broke" in got.texts["test_x::test_boom"]


def test_only_a_run_that_exercised_something_is_usable():
    assert we.read_probes(_said(1)).usable
    assert we.read_probes(_said(0, JUNIT.replace(' name="test_boom"', ' name="b"'))).usable
    assert not we.read_probes("garbage, no exit line").usable                     # nothing readable
    assert not we.read_probes(_said(1, "not xml")).usable                         # an unreadable report
    assert not we.read_probes(_said(5, "<testsuites/>")).usable                   # no tests collected
    assert not we.read_probes(_said(2)).usable                                    # could not run
    skipped = '<testsuites><testsuite><testcase classname="t" name="a"><skipped/></testcase></testsuite></testsuites>'
    assert not we.read_probes(_said(0, skipped)).usable                           # exit 0, and nothing ran


def test_a_case_that_passed_before_the_change_is_a_regression_and_others_are_owed():
    now = _run(a="failed", b="failed", c="error", d="passed")
    base = _run(a="passed", b="failed", d="failed")
    assert we.split_failures(now, base) == (["t::a"], ["t::b", "t::c"])
    assert we.split_failures(now, None) == ([], ["t::a", "t::b", "t::c"])


def test_the_agent_sees_regressions_first_with_each_cases_own_words():
    failing, text = we.failures_of([("held0", _run(a="failed", b="failed"), _run(a="failed", b="passed"))])
    assert failing == {"held0:t::a", "held0:t::b"}
    assert text.index("why b") < text.index("why a") and "Passed before your change" in text


def test_a_long_list_of_failures_is_cut_and_says_how_many_are_left():
    cases = {f"c{i:03}": "failed" for i in range(we.MAX_SHOWN + 5)}
    failing, text = we.failures_of([("held0", _run(**cases), None)])
    assert len(failing) == we.MAX_SHOWN + 5 and "and 5 more" in text and "c999" not in text


def test_a_set_that_stops_running_on_the_patch_is_reported_never_clean():
    gone = we.ProbeRun(2, {}, {}, "ImportError: cannot import name")
    failing, text = we.failures_of([("held1", gone, _run(a="passed"))])
    assert failing == {"held1:exit 2"} and "ImportError" in text


def test_a_clean_usable_set_reports_nothing():
    assert we.failures_of([("scout", _run(a="passed"), None)]) == (set(), "")


def _repairs_with_state(results, states, asked):
    from g6.cegis import InputDomain, cegis

    answers, trees = iter(results), iter(states)
    loop = we.Repairs(lambda: next(answers), lambda text, n: asked.append((n, text)), state=lambda: next(trees))
    got = cegis(we.WorkingTree(), lambda **_: True, domain=InputDomain.of({"probes": ["all"]}), runner=loop,
                max_iterations=we.MAX_REPAIRS + 1, timeout_s=60.0)
    return got, loop


def test_a_failure_goes_back_while_the_agent_is_still_changing_the_code():
    asked = []
    got, _ = _repairs_with_state([({"h:a"}, "a"), ({"h:a"}, "a again"), (set(), "")], ["t1", "t2"], asked)
    assert len(asked) == 2 and "a again" in asked[1][1] and got.status == "no_counterexample_in_domain"


def test_the_loop_ends_when_the_agent_leaves_the_code_as_it_was():
    asked = []
    got, loop = _repairs_with_state([({"h:a"}, "a"), ({"h:a"}, "a")], ["t1", "t1"], asked)
    assert len(asked) == 1 and got.status == "inconclusive" and loop.round == 2


def test_repairs_stay_capped_when_the_agent_keeps_changing_the_code():
    asked = []
    got, _ = _repairs_with_state([({"h:a"}, "a")] * 9, [f"t{i}" for i in range(9)], asked)
    assert len(asked) == we.MAX_REPAIRS and got.status == "counterexample_budget_exhausted"


def test_the_gate_refuses_a_set_in_which_no_case_ran():
    assert any("ran" in w for w in we.scout_gate(CROSS, exit_code=1, collected=6, executed=0))
    assert we.scout_gate(CROSS, exit_code=1, collected=6, executed=1) == []


class _Reply:
    def __init__(self, text):
        self.stdout, self.stderr, self.returncode = text.encode("utf-8"), b"", 0


class _Docker:
    """Answers the probes' script with ``said`` and anything else with the tree."""

    def __init__(self, said):
        self.said, self.scripts = said, []

    def sh(self, ctr, script, *args, **kw):
        self.scripts.append(script)
        return _Reply(self.said if "junitxml" in script else CROSS)


def _gate(said, arm="L8"):
    class Ctx:
        pass

    ctx = Ctx()
    ctx.arm, ctx.docker = arm, _Docker(said)
    return we.probe_gate(ctx, we.load_tasks()[0], "agent", f"{we.WE}/probes", f"{we.WE}/tree.json")


def _xml(cross=6, random=we.MIN_RANDOM, outcome="", extra=""):
    cases = [f'<testcase classname="t" name="test_cross[{i}]">{outcome}</testcase>' for i in range(cross)]
    cases += [f'<testcase classname="t" name="test_random[{i}]"/>' for i in range(random)]
    return f"<testsuites><testsuite>{''.join(cases)}{extra}</testsuite></testsuites>"


FAILS = '<testcase classname="t" name="test_symptom"><failure message="assert 1 == 2">t</failure></testcase>'


def test_the_l8_gate_counts_cases_from_the_report_not_from_collection():
    assert _gate(_said(1, _xml(extra=FAILS))) == []


def test_the_l8_gate_counts_only_generated_inputs_that_ran():
    skipped = _xml(random=0, extra=FAILS).replace(
        "</testsuite>", "".join(f'<testcase classname="t" name="test_random[{i}]"><skipped/></testcase>'
                                for i in range(we.MIN_RANDOM)) + "</testsuite>")
    assert any("test_random has 0" in w for w in _gate(_said(1, skipped)))


def test_the_l8_gate_refuses_a_cross_test_that_only_skipped():
    assert any("ran" in w for w in _gate(_said(1, _xml(outcome="<skipped/>", extra=FAILS))))


def test_the_l8_gate_still_refuses_a_crash():
    crash = '<testcase classname="t" name="test_c"><failure message="TypeError: nope">t</failure></testcase>'
    assert any("not an assertion" in w for w in _gate(_said(1, _xml(extra=FAILS + crash))))


REPLY = """[codex] Starting Codex task thread.
[codex] Assistant message captured: I think VERDICT: none, for now...
[codex] Turn completed.
RULE: Every name the option can take must be derived, not assumed.
VERDICT: defect
WHY: With `--my-help` the candidate compares `my-help` to `my_help` and misses it (core.py:3155).
```python
def test_it():
    assert 1 == 2
```
"""


def test_the_critics_reply_is_read_after_its_progress_lines():
    got = we.critic_report(REPLY)
    assert got["verdict"] == "defect" and "derived" in got["rule"] and "my_help" in got["why"]
    assert got["test"].startswith("def test_it") and "assert 1 == 2" in got["test"]


def test_a_critic_that_finds_nothing_gives_no_test():
    got = we.critic_report("[codex] Turn completed.\nRULE: r\nVERDICT: none\nWHY: nothing found\n```python\n```\n")
    assert got["verdict"] == "none" and got["test"] == ""


def test_a_critic_saying_none_cannot_send_a_test():
    got = we.critic_report("RULE: r\nVERDICT: none\nWHY: w\n```python\ndef test_a():\n    assert 0\n```\n")
    assert got["test"] == ""


def test_a_reply_that_cannot_be_read_is_no_verdict():
    assert we.critic_report("the model said something else")["verdict"] == "unreadable"
    assert we.critic_report("")["test"] == ""


def test_the_critics_prompt_names_no_bug_and_keeps_the_fix_out():
    assert we.PATCH_CRITIC.count("candidate.patch") and "VERDICT" in we.PATCH_CRITIC
    assert "maintainers" not in we.PATCH_CRITIC and "/oracle" not in we.PATCH_CRITIC


def test_the_critic_needs_the_companions_place_set_and_says_so(monkeypatch, tmp_path):
    monkeypatch.delenv("G6_CODEX_COMPANION", raising=False)
    with pytest.raises(RuntimeError, match="G6_CODEX_COMPANION"):
        we.run_codex(tmp_path)


class _Archive:
    @staticmethod
    def redact(text):
        return text


class _Run:
    def __init__(self):
        self.written = {}

    def write(self, name, text):
        self.written[name] = text


def _tar():
    import io
    import tarfile

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        body = b"print('parent')\n"
        info = tarfile.TarInfo("src/app.py")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return raw.getvalue()


class _CriticDocker:
    def __init__(self):
        self.calls, self.scripts = [], []

    def __call__(self, *args, input=None, timeout=None, env=None):
        self.calls.append((args, input))
        out = _tar() if "archive" in args else b"a patch" if "cat" in args else b""
        return type("Done", (), {"stdout": out, "stderr": b"", "returncode": 0})()

    def sh(self, ctr, script, *args, **kw):
        self.scripts.append(script)
        return _Reply("")


def _critic_ctx(docker, reply):
    class Ctx:
        archive = _Archive()

    ctx = Ctx()
    ctx.docker, ctx.seen = docker, {}

    def critic(folder):
        ctx.seen = {p.relative_to(folder).as_posix(): p.read_text("utf-8") for p in folder.rglob("*") if p.is_file()}
        if isinstance(reply, Exception):
            raise reply
        return reply

    ctx.critic = critic
    return ctx


def _critic(ctx):
    task = we.load_tasks()[0]
    run = _Run()
    return we.run_critic(ctx, task, run, "agent", "b" * 40, "a title", "the issue"), run


def test_the_critic_sees_the_task_the_parent_and_the_patch_and_nothing_else(monkeypatch):
    monkeypatch.setattr(we, "copy_out", lambda docker, ctr, src, dest: Path(dest).write_bytes(b"a patch") or True)
    ctx = _critic_ctx(_CriticDocker(), REPLY)
    got, run = _critic(ctx)
    assert set(ctx.seen) == {"issue.txt", "candidate.patch", "critic_prompt.md", "parent/src/app.py"}
    assert "the issue" in ctx.seen["issue.txt"] and ctx.seen["candidate.patch"] == "a patch"
    assert got["verdict"] == "defect" and run.written["critic_reply.txt"] == REPLY
    assert not any("/oracle" in " ".join(map(str, args)) for args, _ in ctx.docker.calls)


def test_the_critics_test_goes_where_the_runner_runs_it_and_only_when_there_is_one(monkeypatch):
    monkeypatch.setattr(we, "copy_out", lambda docker, ctr, src, dest: Path(dest).write_bytes(b"a patch") or True)
    ctx = _critic_ctx(_CriticDocker(), REPLY)
    _critic(ctx)
    wrote = [(args, body) for args, body in ctx.docker.calls if body]
    assert len(wrote) == 1 and f"{we.CRITIC_AT}/test_critic.py" in " ".join(wrote[0][0]) and b"assert 1 == 2" in wrote[0][1]
    quiet = _critic_ctx(_CriticDocker(), "RULE: r\nVERDICT: none\nWHY: fine\n")
    _critic(quiet)
    assert not [body for _, body in quiet.docker.calls if body]


def test_a_critic_that_fails_is_recorded_and_the_run_goes_on(monkeypatch):
    monkeypatch.setattr(we, "copy_out", lambda docker, ctr, src, dest: Path(dest).write_bytes(b"a patch") or True)
    got, _ = _critic(_critic_ctx(_CriticDocker(), RuntimeError("quota")))
    assert got["verdict"] == "failed" and "quota" in got["error"] and got["test"] == ""


def test_the_agent_reads_the_critics_rule_reasons_and_test_only_if_its_test_fails():
    found = {"rule": "the rule", "why": "the reasons", "test": "def test_it(): ..."}
    failing, text = we.with_critic(({"critic:t::a"}, "cases"), found)
    assert failing == {"critic:t::a"} and "the rule" in text and "the reasons" in text and "def test_it" in text and "cases" in text
    assert we.with_critic(({"held0:t::a"}, "cases"), found) == ({"held0:t::a"}, "cases")
    assert we.with_critic((set(), ""), found) == (set(), "")


def test_the_probe_sets_are_named_for_the_arm():
    assert we.probe_sets("L8") == [("scout", f"{we.WE}/probes"), ("held0", f"{we.WE}/hidden"), ("held1", f"{we.WE}/hidden2")]


def test_only_the_loops_arms_get_a_stop_hook():
    assert "Stop" not in json.loads(we.hook_settings())["hooks"]
    got = json.loads(we.hook_settings(stop=True))["hooks"]
    assert "PreToolUse" in got and got["Stop"][0]["hooks"][0]["command"] == f"{we.WE}/stop_hook.sh"
