# SPDX-License-Identifier: Apache-2.0
"""Coding as a domain of the loop: a reviewer reads recorded patches, and the maintainers' tests say if it read them right. FakeLLM only."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from g6.core.llm import FakeLLM
from g6.metaharness.coding_replay import ACCEPTED_DEFECT, FALSE_ALARM, READ_AS_WRITTEN, CodingReplay, load_cases, right_verdict, verdict_of
from g6.metaharness.domain import NO_JUDGE, NO_SEALED, cut_pool, domain_problems
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.propose import DevFailure
from g6.metaharness.schema import SuiteCase
from g6.self_maintenance.pacing import ProviderWindow

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)
HINT = "Check every branch the patch leaves out, not only the example in the issue."


def _share(text: str) -> float:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big") / 2 ** 64


def _row(bug: str, i: int) -> dict:
    label = "wrong" if _share(f"{bug}:{i}") < 0.4 else "right"
    return {"id": f"{bug}-{i}", "bug": bug, "input": f"The issue of {bug}.\n\nPatch {i} ({label})", "label": label}


def _cases(folder, bugs, *, name="cases.jsonl", per_bug=5, first=0):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("".join(json.dumps(_row(b, i)) + "\n" for b in bugs for i in range(first, first + per_bug)), encoding="utf-8")
    return path


class Reader:
    """A reviewer with a planted weakness: without the hint it calls every patch right, so it misses each wrong one."""

    def __call__(self, system: str, user: str) -> str:
        wrong = "(wrong)" in user
        return f"RULE: a rule\nVERDICT: {'WRONG' if wrong and HINT in system else 'RIGHT'}"


def _domain(tmp_path, *, bugs=60, sealed=True, caller=None):
    pool = _cases(tmp_path / "dev", [f"bug{n}" for n in range(bugs)])
    kept = _cases(tmp_path / "sealed", [f"held{n}" for n in range(8)]) if sealed else None
    return CodingReplay(pool, kept, caller or FakeLLM(Reader()))


def _proposer():
    return FakeLLM(lambda system, user: json.dumps({"prompt": HINT}))


def _loop(tmp_path, domain):
    return ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=_proposer(), now=lambda: START + timedelta(days=2),
                      clock=_Clock())


class _Clock:
    t = 0.0

    def __call__(self) -> float:
        self.t += 0.001
        return self.t


@pytest.mark.parametrize(("reply", "said"), [
    ("RULE: x\nVERDICT: RIGHT", "right"),
    ("VERDICT: wrong\n", "wrong"),
    ("VERDICT: RIGHT\nsome more\nVERDICT: WRONG", "wrong"),     # the last one stands: a patch's text can hold the word
    ("the patch looks right", None),
    ("VERDICT: MAYBE", None),
    ("", None),
])
def test_a_verdict_is_read_from_the_reviewers_own_line(reply, said):
    assert verdict_of(reply) == said


def test_only_the_right_verdict_on_a_label_counts():
    assert right_verdict("VERDICT: WRONG", "wrong") is True
    assert right_verdict("VERDICT: RIGHT", "wrong") is False
    assert right_verdict("no verdict", "right") is False


def test_a_case_is_read_with_its_bug_as_the_group_it_is_cut_by(tmp_path):
    case = load_cases(_cases(tmp_path, ["a"], per_bug=1))[0]
    assert case.case_id == "a-0" and case.meta["group"] == "a" and case.expected in ("right", "wrong")


@pytest.mark.parametrize("row", [
    {"id": "a", "bug": "a", "input": "x", "label": "maybe"},
    {"id": "a", "bug": "", "input": "x", "label": "right"},
    {"id": "a", "bug": "a", "input": "", "label": "right"},
])
def test_a_case_with_no_label_or_bug_or_text_is_refused(tmp_path, row):
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


def test_every_patch_of_a_bug_lands_on_one_side_of_the_cut(tmp_path):
    """Patches of one bug share its issue. A proposer that saw one, scored on another, would be scored on what it tuned on."""
    cut = cut_pool(load_cases(_cases(tmp_path, [f"bug{n}" for n in range(40)])), 7)
    sides = [{c.meta["group"] for c in getattr(cut, name)} for name in ("tune", "selection", "held_out", "monitor")]
    assert sum(len(s) for s in sides) == len(set().union(*sides)) == 40


def test_a_case_with_no_group_is_cut_as_before():
    cases = [SuiteCase(f"c{i}", "x", "y") for i in range(50)]
    grouped = [SuiteCase(c.case_id, c.input, c.expected, meta={"group": c.case_id}) for c in cases]
    assert [c.case_id for c in cut_pool(cases, 7).held_out] == [c.case_id for c in cut_pool(grouped, 7).held_out]


def test_a_bug_on_both_sides_of_the_seal_is_refused(tmp_path):
    pool = _cases(tmp_path / "dev", ["a", "b"])
    kept = _cases(tmp_path / "sealed", ["b", "c"], name="other.jsonl")
    with pytest.raises(ValueError, match="b"):
        CodingReplay(pool, kept, FakeLLM(Reader()))


def test_the_domain_passes_the_loops_own_checks_with_the_maintainers_tests_as_its_judge(tmp_path):
    domain = _domain(tmp_path)
    assert domain_problems(domain) == []
    judge = domain.judge()
    assert judge.answers_by == "independent" and judge.is_model is False


def test_with_no_sealed_split_the_loop_refuses_it(tmp_path):
    assert NO_SEALED in [code for code, _ in domain_problems(_domain(tmp_path, sealed=False))]


def test_a_judge_that_answers_nothing_is_not_enough(tmp_path):
    assert NO_JUDGE not in [code for code, _ in domain_problems(_domain(tmp_path))]
    assert right_verdict("this is not an answer", "right") is False and right_verdict("", "wrong") is False


def test_a_miss_and_a_false_alarm_are_different_causes(tmp_path):
    domain = _domain(tmp_path, bugs=2)
    wrong = next(c for c in domain.development() if c.expected == "wrong")
    right = next(c for c in domain.development() if c.expected == "right")
    view = type("View", (), {"failures": (_failure(wrong), _failure(right))})()
    causes = {r.case_id: r.cause for r in domain.failures(view)}
    assert causes == {wrong.case_id: ACCEPTED_DEFECT, right.case_id: FALSE_ALARM}


def _failure(case):
    return DevFailure(case_id=case.case_id, input=case.input, output="VERDICT: RIGHT", expected="", failed_checks=(), oracle="fail",
                      failure_codes=())


def test_the_starting_prompt_asks_for_the_verdict_the_judge_reads(tmp_path):
    assert "VERDICT: RIGHT" in READ_AS_WRITTEN and "VERDICT: WRONG" in READ_AS_WRITTEN


def test_the_loop_improves_a_planted_weak_reader_on_recorded_patches(tmp_path):
    """The whole path, on the real loop: a proposer's prompt, the gate on bugs it never saw, adoption in shadow, no model called on the seal."""
    domain = _domain(tmp_path)
    loop = _loop(tmp_path, domain)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    start = loop.mh._head()[1]
    got = loop.tick()
    assert got["outcome"] == "adopted", got
    assert HINT in loop.mh._head()[1].prompt and loop.mh._head()[1].digest() != start.digest()
    assert domain.llm.calls and not any("held" in str(call) for call in domain.llm.calls)


def test_a_new_patch_of_a_bug_proposers_saw_is_not_fresh_evidence(tmp_path):
    """After a re-cut, a patch no proposer read can still be a bug they tuned on: its id and its text are new, and its issue is not."""
    bugs = [f"bug{n}" for n in range(60)]
    first = CodingReplay(_cases(tmp_path / "a", bugs), _cases(tmp_path / "a-sealed", ["held0"]), FakeLLM(Reader()))
    assert ShadowLoop(first, tmp_path / "loop", SETTINGS, proposer=_proposer(), now=lambda: START + timedelta(days=2),
                      clock=_Clock()).enrol("Ada")["completion_state"] == "qualified-draft"
    later = CodingReplay(_cases(tmp_path / "b", bugs, first=5), _cases(tmp_path / "b-sealed", ["held0"]), FakeLLM(Reader()))
    again = ShadowLoop(later, tmp_path / "loop", dataclasses.replace(SETTINGS, seed=SETTINGS.seed + 1), proposer=_proposer(),
                       now=lambda: START + timedelta(days=2), clock=_Clock())
    assert again.enrol("Ada")["warning_card"]["code"] == "G6_E_LOOP_SPLIT_REUSED"


def test_too_few_bugs_is_refused_and_not_run(tmp_path):
    domain = _domain(tmp_path, bugs=7)
    loop = _loop(tmp_path, domain)
    assert loop.enrol("Ada")["warning_card"]["code"] == "G6_E_LOOP_POOL_TOO_SMALL"
    assert domain.llm.calls == []
