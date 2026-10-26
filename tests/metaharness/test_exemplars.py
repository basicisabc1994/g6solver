# SPDX-License-Identifier: Apache-2.0
"""Labelled reference examples (``Profile.exemplars``): a recovery remedy the meta-harness can propose itself."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from g6.metaharness import Profile, Suite, SuiteCase, adapt
from g6.metaharness import schema as S
from g6.metaharness.exemplars import HEADER, Bank
from g6.metaharness.executor import context_for, model_call_factory

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

LABELS = "Labels: alarm_set, cooking_recipe, cooking_query"
BANK = tuple(SuiteCase(f"e{i}", f"Utterance: {u}\n\n{LABELS}", lab) for i, (u, lab) in enumerate((
    ("wake me up at seven", "alarm_set"), ("how do i make pancakes", "cooking_recipe"),
    ("what can i cook with eggs", "cooking_query"), ("set an alarm for noon", "alarm_set"))))


def test_the_nearest_examples_come_first_and_the_shared_label_list_is_left_out() -> None:
    bank = Bank(BANK)
    got = bank.nearest("Utterance: how do i make waffles\n\n" + LABELS, 2)
    assert got[0] == ("Utterance: how do i make pancakes", "cooking_recipe")
    shown = bank.render("Utterance: how do i make waffles\n\n" + LABELS, 2, 0)
    assert shown.startswith(HEADER) and LABELS not in shown


def test_a_case_never_sees_its_own_example() -> None:
    bank = Bank(BANK)
    task = f"Classify. {BANK[1].input} Reply with the label."
    assert all(a != "cooking_recipe" or "pancakes" not in s for s, a in bank.nearest(task, 4))


TEMPLATE = "Label the utterance. Labels: alarm_set, play_music, weather_query\n{input}"
WORDY = tuple(SuiteCase(f"w{i}", f"Utterance: {u}", lab) for i, (u, lab) in enumerate((
    ("play music now", "play_music"), ("will it rain", "weather_query"), ("set the alarm", "alarm_set"))))


def test_the_match_ignores_what_every_task_repeats() -> None:
    # V6b-F1: label words in every task matched the same examples first, whatever the input said
    task = TEMPLATE.replace("{input}", "Utterance: rain later")
    assert Bank(WORDY, TEMPLATE).nearest(task, 1)[0][1] == "weather_query"
    listed = "\n\nLabels: alarm_set, play_music, weather_query"
    in_input = tuple(replace(c, input=c.input + listed) for c in WORDY)
    assert Bank(in_input).nearest("Utterance: rain later" + listed, 1)[0][1] == "weather_query"


def test_a_capped_search_still_tries_every_value_of_the_first_field() -> None:
    from g6.metaharness import SearchStrategy
    from g6.metaharness.propose import ProposerView

    # V6b-F2: the first 32 of 72 combinations in order never set ``exemplars`` (it sorts first) to 8
    space = {"exemplars": [0, 8], "prompt": [f"p{i}" for i in range(18)], "retries": [0, 1]}
    view = ProposerView("w", "t", (), ("answer",), ("local",), (), {"routing": {"answer": "local"}},
                        tuple({"case_id": f"d{i}", "input": ""} for i in range(8)), (), 0)
    runs = []
    for _ in range(2):
        tried: list[Profile] = []
        SearchStrategy(space, max_profiles=32).propose(view, lambda p, ids: tried.append(p) or [True] * len(ids))
        runs.append([p.digest() for p in tried])
        assert {p.exemplars for p in tried} == {0, 8} and len(set(runs[-1])) <= 32
    assert runs[0] == runs[1]                                    # the sample is fixed, so a search is reproducible


def test_the_budget_caps_what_is_shown() -> None:
    shown = Bank(BANK).render("Utterance: set an alarm\n\n" + LABELS, 4, len(HEADER) + 60)
    assert shown.count("\n- ") <= 1


@dataclass
class Spy(rig.Stub):
    users: list[str] = field(default_factory=list)

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.users.append(user)
        return super().complete(system, user, max_tokens=max_tokens)


def test_the_executor_shows_examples_only_when_the_profile_asks() -> None:
    wf, _ = rig.build(stub=Spy(1))
    stub = wf.models["local"].caller
    for k, expect in ((0, False), (3, True)):
        ctx = context_for(wf, Profile(routing={"answer": "local"}, exemplars=k))
        model_call_factory()(ctx)(type("C", (), {"task": "Compute this. What is 12+5? Reply with the number only."}))
        assert (HEADER in stub.users[-1]) is expect


def test_examples_are_part_of_what_a_person_confirms_and_never_held_out_cases() -> None:
    wf, _ = rig.build()
    with_bank = replace(wf, examples=BANK)
    assert with_bank.digest() != wf.digest()
    leaked = replace(wf, examples=(wf.suite.held_out[0],))
    assert any(c == S.NOT_HELD_OUT for c, _ in leaked.problems())


def test_a_profile_without_exemplars_keeps_its_digest() -> None:
    base = Profile(routing={"answer": "local"})
    assert base.digest() == Profile(routing={"answer": "local"}, exemplars=0).digest()
    assert base.digest() != Profile(routing={"answer": "local"}, exemplars=4).digest()


def test_exemplars_need_a_bank() -> None:
    wf, _ = rig.build()
    empty = replace(wf, suite=Suite((), wf.suite.held_out, wf.suite.provenance))
    assert any("exemplars needs" in p for _, p in Profile(routing={"answer": "local"}, exemplars=2).problems_for(empty))


def test_search_can_propose_exemplars_and_the_gate_judges_them(tmp_path) -> None:
    from g6.metaharness import SearchStrategy

    wf, _ = rig.build()
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    mh.confirm("Ada")
    got = mh.propose(SearchStrategy({"exemplars": [0, 4]}))
    assert got["proposals"] and got["proposals"][0].candidate.exemplars == 4
    decided = mh.decide(mh.evaluate(got["proposals"][0])["report"], got["proposals"][0])
    # judged like any other change: recommended, or refused with its recovery
    assert decided["warning_card"]["code"] == S.RECOMMENDED or decided["recovery"]["causes"]
