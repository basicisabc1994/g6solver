# SPDX-License-Identifier: Apache-2.0
"""Arm L: the runner, not the agent, holds the loop (PLAN.md, 2026-10-31)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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


def test_arms_before_l5_keep_the_stop_hook_and_l5_has_the_outer_loop():
    assert we.has_hook("L1") and we.has_hook("M4") and not we.has_hook("L5")
    assert we.holds_loop("L5") and we.holds_loop("M7") and not we.holds_loop("L4")


def test_failures_are_read_from_pytests_summary():
    text = "FAILED ../../opt/we/hidden/test_eof.py::test_cross[a-b] - assert 1\nERROR ../x/test_y.py\n1 failed, 1 error\n"
    assert we.failed_ids(text) == {"test_eof.py::test_cross[a-b]", "test_y.py"}


def test_only_the_loops_arms_get_a_stop_hook():
    assert "Stop" not in json.loads(we.hook_settings())["hooks"]
    got = json.loads(we.hook_settings(stop=True))["hooks"]
    assert "PreToolUse" in got and got["Stop"][0]["hooks"][0]["command"] == f"{we.WE}/stop_hook.sh"
