# SPDX-License-Identifier: Apache-2.0
"""Arm L: the runner, not the agent, holds the loop (PLAN.md, 2026-10-31)."""
from __future__ import annotations

import json
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


def test_only_the_loops_arms_get_a_stop_hook():
    assert "Stop" not in json.loads(we.hook_settings())["hooks"]
    got = json.loads(we.hook_settings(stop=True))["hooks"]
    assert "PreToolUse" in got and got["Stop"][0]["hooks"][0]["command"] == f"{we.WE}/stop_hook.sh"
