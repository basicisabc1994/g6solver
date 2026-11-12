# SPDX-License-Identifier: Apache-2.0
"""The product's completion loop against the worked examples' arms L9 and K: the text the package ships, resolved from
``run.py``'s own functions, and the parts of the loop that had to be carried across, on the same inputs, so that the two
cannot drift. No model, no container, nothing written under ``validation/``."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402

from g6.core.llm import FakeLLM, LLMError  # noqa: E402
from g6.harness import completion_loop as loop  # noqa: E402
from g6.harness import verification_proposer as proposer  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

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


# --- arm K, the review-until-clean loop: ``reviews_until_clean`` and ``advance`` on the same reviews -------------------------

FOUND = {"rule": "a {rule}", "why": "a why", "test": "def test_x():\n    assert {}"}
FAILS = we.ProbeRun(exit=1, results={"t": "failed"}, texts={"t": "AssertionError"})
PASSES = we.ProbeRun(exit=0, results={"t": "passed"}, texts={})
BROKEN = we.ProbeRun(exit=2, results={}, texts={})
QUIET = {"verdict": "none", "rule": "no rule", "why": "nothing", "test": "", "error": ""}
FAILED = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": "timeout"}


def defect(n: int) -> dict:
    return {"verdict": "defect", "rule": f"rule {n}", "why": f"reason {n}", "test": f"def test_{n}():\n    assert 0", "error": ""}


def test_the_shipped_text_is_ks():
    shipped = loop.harness()
    assert shipped.critic_note == we.CRITIC_NOTE and shipped.review_ask == we.REVIEW_ASK
    assert loop.review_note(FOUND, shipped) == we.CRITIC_NOTE.format(**FOUND) + we.REVIEW_ASK
    assert we.convert_note("X1", FOUND) == we.CONVERT_ON_PATCH + loop.review_note(FOUND, shipped)


def test_each_reviews_test_has_ks_file_name():
    assert [loop.critic_file(n) for n in range(1, 6)] == [we.critic_files(n)[1] for n in range(1, 6)]


def frozen(monkeypatch, reviews: list[dict], sets: list, moves: bool) -> dict:
    """``reviews_until_clean`` itself, with a tree that moves on with each repair unless ``moves`` is false."""
    tree, left, seen, sent = [0], list(sets), [], []

    def sh(agent, script, *args, **kw):
        return type("Done", (), {"stdout": f"tree {tree[0]}".encode(), "stderr": b"", "returncode": 0})()

    def critique(n: int) -> dict:
        seen.append(n)
        return reviews[n - 1]

    def session(ctx, run, agent, args, key, tally, text, name, **kw):
        sent.append(text)
        tree[0] += int(moves)

    monkeypatch.setattr(we, "run_set", lambda *args: left.pop(0))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    ctx = type("Ctx", (), {"docker": type("Docker", (), {"sh": staticmethod(sh)})()})()
    run = type("Run", (), {"write_json": lambda self, name, body: None})()
    got = we.reviews_until_clean(ctx, type("T", (), {"repo": "/w/r"})(), run, "agent", "b", [], "key", None, 1.0, 0.0, critique)
    return {"reviews": len(seen), "sent": sent, "reason": got["reason"], "clean": got["status"] == "no_counterexample_in_domain"}


def product(tmp_path: Path, monkeypatch, reviews: list[dict], sets: list, moves: bool, again: int = 0) -> dict:
    """``advance``, one hook at a time, on the same reviews. Each new test takes what ``sets`` says, as ``run.py``'s whole folder
    does, and an earlier one passes, which is all the frozen loop's cases ask of one. ``again``: the reviews that rewrote their
    round's test rather than read a new patch, as the frozen loop has none."""
    left, seen = list(sets), []
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    monkeypatch.setattr(proposer, "boundary", lambda root: "")
    monkeypatch.setattr(loop, "review_patch", lambda repo, **asked: {"assessment": {"report": "R"}})

    def reply(system: str, user: str) -> str:
        found = reviews[len(seen)]
        seen.append(found)
        if found["error"]:
            raise LLMError(found["error"])
        proof = re.search(r"review [0-9a-f]{32}", user)[0]
        return (f"READ: {proof}\nRULE: {found['rule']}\nVERDICT: {found['verdict']}\nWHY: {found['why']}\n"
                + (f"```python\n{found['test']}\n```\n" if found["test"] else ""))

    def run_check(change, placed, source, node, *, at_base, timeout_s, roots=()):
        got = left.pop(0) if Path(placed).name == loop.critic_file(max(len(seen) - again, 1)) else PASSES
        if got.exit not in (0, 1):
            return {"verdict": "collection_error", "said": "will not run"}
        observed = f"{placed}::t"
        return {"verdict": "failed" if got.exit else "passed", "said": "", "output": "", "rootdir": "", "loaded_outside": {},
                "collected_ids": [observed], "omitted_ids": [], "setup_errors": {},
                "failed_ids": [observed] if got.results["t"] == "failed" else [],
                "loaded_complete": True, "os_errors": [], "failure_messages": {},
                "test_outcomes": {f"{placed}::t": {"setup": "passed", "call": got.results["t"], "teardown": "passed"}}}

    monkeypatch.setattr(vs, "_run_check", run_check)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mod.py").write_text("x = 1\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "."), ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "b")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "mod.py").write_text("x = 2\n", encoding="utf-8")
    critic, session, sent = FakeLLM(reply), tmp_path / "g6" / "completion_loop" / "s1", []
    for repair in range(we.MAX_REPAIRS + 2):
        step = loop.advance(root, "the task", "HEAD", session, critic=critic)
        if step.action == loop.STOP:
            break
        sent.append(step.text)
        if moves:
            (root / "mod.py").write_text(f"x = {repair + 3}\n", encoding="utf-8")
    state = json.loads((session / "state.json").read_text(encoding="utf-8"))
    return {"reviews": len(seen), "sent": sent, "reason": state["stop"], "clean": state["stop"] == loop.CLEAN}


@pytest.mark.parametrize("reviews, sets, moves", [
    ([QUIET], [], True),                                                    # nothing at once
    ([defect(1), defect(2), QUIET], [FAILS, FAILS], True),                  # the point of the arm
    ([defect(1), QUIET], [FAILS], True),
    ([defect(n) for n in range(1, we.MAX_REPAIRS + 2)], [FAILS] * (we.MAX_REPAIRS + 1), True),     # the cap
    ([defect(1), defect(2)], [FAILS], False),                               # the agent rejected it
    ([defect(1)], [PASSES], True),                                          # a test that passes is no finding
    ([{**defect(1), "test": ""}], [], True),                                # a defect with no test sends nothing
    ([FAILED], [], True),                                                   # no clean review
    ([defect(1), FAILED], [FAILS], True),
    ([defect(1), {"verdict": "maybe", "rule": "", "why": "", "test": "", "error": ""}], [FAILS], True),
])
def test_the_loop_runs_as_k_runs(tmp_path, monkeypatch, reviews, sets, moves):
    assert product(tmp_path, monkeypatch, reviews, sets, moves) == frozen(monkeypatch, reviews, sets, moves)


def test_where_the_product_departs_from_k_a_test_that_will_not_run_ends_no_review_clean(tmp_path, monkeypatch):
    """The one case the loop no longer shares with K, by the maintainers' ruling. K says nothing of a test that will not run, so
    its review stands as clean, and a defect a reviewer named is gone from the result. The product tells the reviewer once, and
    with a test that still will not run it ends with no clean review. The frozen side stays held to what it did."""
    assert frozen(monkeypatch, [defect(1)], [BROKEN], True) == {
        "reviews": 1, "sent": [], "reason": "a review reproduced nothing", "clean": True}
    assert product(tmp_path, monkeypatch, [defect(1), defect(1)], [BROKEN, BROKEN], True, again=1) == {
        "reviews": 2, "sent": [], "reason": loop.UNREPRODUCED, "clean": False}
