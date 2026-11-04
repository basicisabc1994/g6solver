# SPDX-License-Identifier: Apache-2.0
"""The parent gate by case (PLAN.md, 2026-11-04, evening): replay 5's registration flags a patch only when a case that fails on
the parent still fails on it. Replay 3's verdict, which replays 3 and 4 were scored with, flagged a patch when any case failed on
the parent and any case failed on the patch; it is kept as it was for them, and replay 5 takes the rule its registration states."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import replay3 as r3  # noqa: E402
import replay4 as r4  # noqa: E402
import replay5 as r5  # noqa: E402
import run as we  # noqa: E402

FOUND = {"verdict": "defect", "rule": "r", "why": "w", "test": "def test_x():\n    assert 0\n", "error": ""}
PARENT = we.ProbeRun(1, {"t::test_a": "failed", "t::test_b": "passed"}, {}, "")
OTHER_ONLY = we.ProbeRun(1, {"t::test_a": "passed", "t::test_b": "failed"}, {}, "")
SAME = we.ProbeRun(1, {"t::test_a": "failed", "t::test_b": "passed"}, {}, "")


def test_replay_3s_verdict_is_kept_as_replays_3_and_4_were_scored_with_it():
    assert r3.verdict(FOUND, PARENT, OTHER_ONLY)["said"] == "reproduced"
    assert r3.rule().same_case is False and r4.SYMPTOM.same_case is False


def test_by_case_a_patch_whose_failing_cases_all_passed_on_the_parent_is_not_flagged():
    got = r3.verdict(FOUND, PARENT, OTHER_ONLY, same_case=True)
    assert got["said"] == "none" and "parent" in got["reason"]


def test_by_case_a_case_failing_on_the_parent_and_on_the_patch_is_flagged():
    assert r3.verdict(FOUND, PARENT, SAME, same_case=True)["said"] == "reproduced"


def test_replay_5_takes_the_rule_its_registration_states_with_its_digest_and_folder_unchanged():
    assert r5.RELATION.same_case is True
    assert r5.INSTRUCTION == "fd779fd79374dfe4" and r5.FOLDER == "final_review_relation-fd779fd79374dfe4"
