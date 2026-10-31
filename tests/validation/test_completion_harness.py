# SPDX-License-Identifier: Apache-2.0
"""The product's completion loop against the worked examples' arm L9: the text the package ships, resolved from
``run.py``'s own functions, and the parts of the loop that had to be carried across, on the same inputs, so that the two
cannot drift. No model, no container, nothing written under ``validation/``."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402

from g6.harness import completion_loop as loop  # noqa: E402

ARM = "L9"
#: Text with braces, as a trained harness may hold: each part must take them as run.py does.
ADDED = {"fixer": "Name {the} rule.", "scout": "List {every} branch.", "repair": "Keep {the} shape.",
         "critic": "Name {the} line."}
TASK = we.Task(id="t", project="o/n", full_name="o/n", licence="MIT", pr=1, issue=7, merge_commit="m", parent="p",
               merged_at="2026-01-01", test_modules=("t",), install=("i",))


def fixer_of(arm: str) -> str:
    """What ``prompt`` adds to round 1's prompt for ``arm``, which it does not keep apart."""
    bare = we.PROMPT.format(owner="o", name="n", number=7, title="T", text="X")
    head, _, tail = bare.partition("\nIssue #7")
    said = we.prompt(TASK, "T", "X", arm)
    assert said.startswith(head) and said.endswith("\n\nIssue #7" + tail)
    return said[len(head) + 1:-len("\n\nIssue #7" + tail)]


def test_the_shipped_text_is_l9s():
    shipped = loop.harness()
    assert shipped.scout == we.scout_prompt(ARM)
    assert shipped.hidden_scouts == tuple(we.scout_prompt(ARM, hidden=True, at=i) for i in range(we.hidden_scouts(ARM)))
    assert shipped.repair == we.repair_text(ARM)
    assert shipped.critic == we.critic_text(ARM)
    assert shipped.critic_note == we.CRITIC_NOTE
    assert shipped.fixer == fixer_of(ARM)


def test_the_stop_rule_and_the_limits_are_l9s():
    assert (loop.MAX_REPAIRS, loop.MAX_CROSS, loop.MIN_RANDOM) == (we.MAX_REPAIRS, we.MAX_CROSS, we.MIN_RANDOM)
    assert (loop.MAX_SHOWN, loop.MAX_CASE_CHARS) == (we.MAX_SHOWN, we.MAX_CASE_CHARS)
    assert (loop.SCOUT_CAP_USD, loop.CRITIC_SECONDS) == (we.SCOUT_CAP_USD, we.CRITIC_SECONDS)
    assert loop.TRAINED == we.TRAINED


def dropped_in(tmp_path: Path, added: dict[str, str]) -> Path:
    data = json.loads(loop.HARNESS.read_text(encoding="utf-8"))
    path = tmp_path / "harness.json"
    path.write_text(json.dumps({**data, "trained": added}), encoding="utf-8")
    return path


def test_a_trained_harness_needs_no_code_and_composes_as_run_py_does(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "trained", lambda arm: dict(ADDED))
    shipped = loop.harness(dropped_in(tmp_path, ADDED))
    assert shipped.scout == we.scout_prompt(ARM)
    assert shipped.hidden_scouts[1] == we.scout_prompt(ARM, hidden=True, at=1)
    assert shipped.repair == we.repair_text(ARM)
    assert shipped.critic == we.critic_text(ARM)
    assert shipped.fixer == fixer_of(ARM)


def test_training_can_only_add_to_the_parts_it_may(tmp_path):
    with pytest.raises(ValueError, match="training cannot change"):
        loop.harness(dropped_in(tmp_path, {"stop_rule": "never"}))


def run_of(kind, *, exit_code, results, texts=None, tail="the tail"):
    return kind(exit_code, dict(results), dict(texts or {}), tail)


CASES = {"lost": ("passed", "failed"), "owed": ("failed", "failed"), "fixed": ("failed", "passed"),
         "kept": ("passed", "passed")}


@pytest.mark.parametrize("sets", [
    [("scout", 1, 1), ("held0", 1, 1)],
    [("scout", 2, 1)],           # a set that no longer runs is reported, never clean
    [("scout", None, 1)],
])
def test_the_failures_read_as_l9_reads_them(sets):
    def seen(kind):
        for label, now_exit, base_exit in sets:
            now = run_of(kind, exit_code=now_exit, results={k: v[1] for k, v in CASES.items()},
                         texts={"lost": "assert 1 == 2", "owed": "assert 3 == 4"})
            base = run_of(kind, exit_code=base_exit, results={k: v[0] for k, v in CASES.items()})
            yield label, now, base

    assert loop.failures_of(seen(loop.ProbeRun)) == we.failures_of(seen(we.ProbeRun))


def test_the_failures_past_the_cap_are_counted_as_l9_counts_them():
    results = {f"c{i}": "failed" for i in range(we.MAX_SHOWN + 3)}

    def seen(kind):
        return [("scout", run_of(kind, exit_code=1, results=results), None)]

    assert loop.failures_of(seen(loop.ProbeRun)) == we.failures_of(seen(we.ProbeRun))


CRITIC = {"rule": "the rule", "why": "the why", "test": "def test_x():\n    assert False"}


@pytest.mark.parametrize("found", [({"critic:test_critic.py::test_x"}, "fails"), ({"scout:a"}, "fails"), (set(), "")])
def test_the_critic_is_added_as_l9_adds_it(found):
    assert loop.with_critic(found, CRITIC, loop.harness().critic_note) == we.with_critic(found, CRITIC)


TREE = json.dumps({"goal": "g", "dimensions": {"a": ["1", "2"], "b": ["1", "2", "3"]}, "branches": []})


@pytest.mark.parametrize("raw, numbers", [
    (TREE, {"exit_code": 1, "collected": 6, "broken": 0, "randoms": 300, "executed": 6}),   # nothing wrong
    (TREE, {"exit_code": 0, "collected": 6, "broken": 0, "randoms": 300, "executed": 6}),   # reproduces nothing
    (TREE, {"exit_code": 1, "collected": 5, "broken": 2, "randoms": 10, "executed": 0}),
    (TREE, {"exit_code": 2, "collected": 0, "broken": 0, "randoms": 0, "executed": 0}),
    ("not json", {"exit_code": 1, "collected": 6}),
    (json.dumps({"dimensions": {"a": ["1"]}}), {"exit_code": 1, "collected": 1}),
])
def test_the_scout_gate_counts_as_l9_counts(raw, numbers):
    assert loop.scout_gate(raw, **numbers) == we.scout_gate(raw, **numbers)


@pytest.mark.parametrize("said, proof", [
    ("READ: review abc\nRULE: a rule\nVERDICT: defect\nWHY: why\n```python\ndef test_x():\n    assert 0\n```\n",
     "review abc"),
    ("READ: review abc\nRULE: a rule\nVERDICT: none\nWHY: nothing\n", "review abc"),
    ("RULE: a rule\nVERDICT: none\n", "review abc"),      # did not read the pack
    ("no form at all", ""),
])
def test_the_critics_reply_is_read_as_l9_reads_it(said, proof):
    assert loop.critic_report(said, proof=proof) == we.critic_report(said, proof=proof)
