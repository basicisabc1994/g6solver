# SPDX-License-Identifier: Apache-2.0
"""Measured, not claimed: what the feasibility rules do on reworded high-stakes tasks and on harmless tasks that use
their words (audit 030 FEA-002/003, the archive's honest negative eval kept).

``fixtures/feasibility_paraphrased_tasks.json`` is the archive's paraphrase fixture, copied unchanged. Its rows mean
the same as trigger-word tasks while avoiding the words. Both fixtures were written on the G6 side, and the rewrite's
author had read the paraphrases, so these are regression baselines, not an independent estimate (validation plan
rule 3). The tripwire was not tuned to them: the lexicon is the archive's, matched as words.

Measured 2026-10-18: paraphrases 7/27 exact, 15 high-stakes rows proceed (the archive: 7/27 and 15), 0 of 7 risky
rows held. Benign-with-trigger-words: 0 of 11 hard-blocked (the archive: 3), 6 held for acceptance as risky, 1
downgraded. Re-measured 2026-10-20, after destructive terms ("wipe", "drop table", ...) became risky on their own:
paraphrases 8/27 exact, 14 proceed, 1 of 7 risky rows held ("wipe the live order ledger"); benign unchanged. If the
rules change, these numbers change: re-measure and re-baseline, and say so in the ledger.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from g6.intent_engine.schema import IntentContract
from g6.reliability_pipeline.feasibility import deterministic_classify

_FIXTURE = Path(__file__).with_name("fixtures") / "feasibility_paraphrased_tasks.json"
HARD = {"infeasible", "blocked_missing_access", "blocked_missing_context", "blocked_unclear_success"}
#: Classes intake does not let run without a person's step: a hard block, or risky (held for explicit acceptance).
HELD = HARD | {"risky"}
# COPY: Harmless tasks containing trigger words (the audit's examples, written out as whole tasks).
BENIGN = [
    "Write a reproduction script for the flaky test",
    "Add a password strength meter to signup",
    "Add a credential field placeholder to the login form",
    "Add a test asserting it is impossible to submit an empty form",
    "Add a legal notice to the footer",
    "Show payment method icons on the checkout page",
    "List deleted items in the trash view",
    "Refactor the production config loader into smaller functions",
    "Fix the medical-history form label typo",
    "Do not delete anything; add a read-only report",
    "Document everything in the setup guide",
]


def _classify(goal: str, criteria: list[str]) -> str:
    return deterministic_classify(IntentContract(goal=goal, success_criteria=criteria)).classification


def test_reworded_high_stakes_tasks_mostly_proceed():
    rows = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    graded = [(_classify(r["goal"], r["success_criteria"]), r["expected"], r["goal"]) for r in rows]
    exact = sum(got == expected for got, expected, _ in graded)
    proceeds = [(expected, got, goal) for got, expected, goal in graded if expected in HELD and got not in HELD]
    risky_held = sum(got in HELD for got, expected, _ in graded if expected == "risky")
    print(f"\nexact {exact}/{len(rows)}; high-stakes rows that proceed: {len(proceeds)}; risky held {risky_held}/7")
    for expected, got, goal in proceeds:
        print(f"  PROCEEDS expected={expected:24} got={got:10} :: {goal}")
    assert len(rows) == 27
    assert (exact, len(proceeds), risky_held) == (8, 14, 1), "the rules changed: re-measure and re-baseline"


def test_no_harmless_task_is_hard_blocked_by_a_word():
    found = Counter(_classify(goal, ["done"]) for goal in BENIGN)
    print(f"\nbenign with trigger words: {dict(found)}")
    assert sum(found[c] for c in HARD) == 0
    assert (found["risky"], found["too_large"] + found["underspecified"], found["clear"]) == (6, 1, 4), (
        "the rules changed: re-measure and re-baseline")
