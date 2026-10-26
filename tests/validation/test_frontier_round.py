# SPDX-License-Identifier: Apache-2.0
"""A development round end to end with a stub model, on the synthetic fixtures: every arm writes its rows, the splits
are written before any model call, a rerun redoes nothing, and the scoreboard is recomputable from the rows."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontier import ledger
from frontier import round as rounds
from g6.metaharness.reflect import REFLECT_PROMPT, REFLECT_SYSTEM

FIX = Path(__file__).with_name("fixtures") / "frontier_data"
SMALL = {"dev": 2, "selection": 1, "confirmation": 2, "dev_eval": 1}
KEYS = ("gsm8k", "massive")


class Stub:
    """The task model: a fixed reply per workflow; counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        return "alarm_set" if "Utterance:" in user else "Answer: 7"


class Reflector:
    calls = 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        return '{"prompt": "Work through it, then give the final answer on its own line."}'


class Stop(BaseException):
    pass


class Dies:
    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        raise Stop


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(ledger, "ROUNDS", tmp_path / "rounds")
    return tmp_path


def _run(tmp: Path, caller, **kw):
    return rounds.run_round(1, KEYS, root=FIX, out=tmp / "rounds" / "round-01", work=tmp / "work", caller=caller,
                            reflector=Reflector(), h_prompts={"gsm8k": "Show the sum.", "massive": "One label."},
                            meta={"round": 1}, log=lambda m: None, sizes=SMALL, **kw)


def test_a_round_writes_every_arm_and_a_scoreboard_it_can_recompute(isolated):
    out = isolated / "rounds" / "round-01"
    _run(isolated, Stub())
    for key in KEYS:
        rows = rounds.Rows(out, key).all()
        assert sorted(r["arm"] for r in rows) == sorted((*rounds.design(1)["arms"], rounds.REPEAT) * SMALL["dev_eval"]), key
        assert all(set(r) >= {"case_id", "success", "released", "model_calls", "output_sha256"} for r in rows)
        assert not any("output" in r for r in rows)                  # no model text in the committed rows
        for arm in ("D", "F"):
            tuned = json.loads((out / "tuning" / f"{key}-{arm}.json").read_text(encoding="utf-8"))
            assert tuned["run"]["outcome"] and tuned["head_profile"]["routing"] == {"answer": "local"}
    board = json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))
    assert board == json.loads(json.dumps(rounds.scoreboard(out, meta={"round": 1})))
    assert board["workflows"]["gsm8k"]["A"]["n"] == SMALL["dev_eval"]
    assert "Development, not a validation" in (out / "README.md").read_text(encoding="utf-8")


def test_the_splits_are_counted_before_any_model_call(isolated):
    with pytest.raises(Stop):
        _run(isolated, Dies())
    drawn = json.loads((isolated / "rounds" / "round-01" / "splits.json").read_text(encoding="utf-8"))["splits"]
    for key in KEYS:
        ids = {i for split in drawn[key].values() for i in split}
        assert len(ids) == sum(SMALL.values()) and ids <= ledger.used_ids(key)


def test_a_rerun_redoes_nothing(isolated):
    _run(isolated, Stub())
    again = Stub()
    _run(isolated, again)
    assert again.calls == 0


def test_the_canary_records_a_held_out_input_shown_to_the_reflector():
    held = "Question: Which synthetic option 2 is right?\n\nOptions:\nA. first"
    tap = rounds.Tap(Reflector(), [rounds.snippet(held)])
    tap.complete("You propose a change.", "A failing dev case: Which synthetic option 2 is right?")
    tap.complete("You propose a change.", "Only dev cases here.")
    assert len(tap.hits) == 1 and tap.calls == 2


def _reflection(inputs: list[str]) -> str:
    """The reflection prompt as ReflectiveStrategy builds it, with these inputs as its failing dev cases."""
    rows = [{"input": text, "expected": "x"} for text in inputs]
    return REFLECT_PROMPT.format(task="Do the task.", parent="{}", space="{}",
                                 failures=json.dumps(rows, ensure_ascii=False, indent=1))


def test_the_canary_finds_a_quoted_held_out_input_in_the_json_the_reflector_is_shown():
    held = 'Question: What do I do if an ATM "stole" my card?\n\nOptions:\nA. first'
    tap = rounds.Tap(Reflector(), [rounds.snippet(held)])
    tap.complete(REFLECT_SYSTEM, _reflection([held]))
    assert len(tap.hits) == 1


def test_a_held_out_input_already_in_allowed_text_is_counted_blind_not_hit():
    # round 1, massive-D: the held-out utterance "s." is in the reflection template's own "already pass."
    held = "Utterance: s.\n\nIntent labels: alarm_set"
    tap = rounds.Tap(Reflector(), [rounds.snippet(held), "a held-out text no prompt contains"],
                     allowed=[REFLECT_SYSTEM, REFLECT_PROMPT])
    tap.complete(REFLECT_SYSTEM, _reflection(["Utterance: wake me at five"]))
    assert tap.hits == [] and tap.blind == 1


class Drifts(Stub):
    """A task model whose reply changes from call to call, as a model server's can (round 1: its prompt cache)."""

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        return f"{super().complete(system, user)} ({self.calls})"


def test_each_arm_is_measured_from_a_freshly_loaded_model_and_a_rerun_loads_nothing(isolated):
    resets: list[int] = []
    _run(isolated, Stub(), reset=lambda: resets.append(1))
    assert len(resets) == len(KEYS) * (len(rounds.design(1)["arms"]) + 1)      # A, B, H, D, F and B again
    resets.clear()
    _run(isolated, Stub(), reset=lambda: resets.append(1))
    assert resets == []


def test_b_is_measured_again_and_the_round_reports_how_many_outputs_repeat(isolated):
    board = _run(isolated, Stub())
    assert board["repeat"] == {key: {"identical": SMALL["dev_eval"], "n": SMALL["dev_eval"]} for key in KEYS}
    assert "Identical outputs" in (isolated / "rounds" / "round-01" / "README.md").read_text(encoding="utf-8")


def test_the_repeat_control_raises_when_the_model_server_drifts(isolated):
    board = _run(isolated, Drifts())
    assert all(r["identical"] == 0 for r in board["repeat"].values())


def test_each_tuning_record_says_how_many_held_out_inputs_its_canary_cannot_see(isolated):
    _run(isolated, Stub())
    for key in KEYS:
        tuned = json.loads((isolated / "rounds" / "round-01" / "tuning" / f"{key}-D.json").read_text(encoding="utf-8"))
        assert isinstance(tuned["canary_blind"], int)


def _run3(tmp: Path, caller=None):
    return rounds.run_round(3, KEYS, root=FIX, out=tmp / "rounds" / "round-03", work=tmp / "work", caller=caller or Stub(),
                            reflector=Reflector(), h_prompts={}, meta={"round": 3}, log=lambda m: None, sizes=SMALL)


def test_rounds_one_and_two_keep_their_design():
    for n in (1, 2):
        assert rounds.design(n) == {"arms": ("A", "B", "H", "D", "F"), "calls": 3, "mean_calls": 3.0}


def test_round_three_runs_both_modes_together_at_v6bs_call_budget():
    d = rounds.design(3)
    assert d["arms"] == ("A", "B", "G", "D", "DG") and (d["calls"], d["mean_calls"]) == (8, 4.0)
    assert d["workflows"] == ("gsm8k", "massive", "scifact", "mbpp")      # where V6b measured arm G


def test_round_three_measures_every_component_on_and_tunes_from_there(isolated):
    out = isolated / "rounds" / "round-03"
    board = _run3(isolated)
    for key in KEYS:
        rows = rounds.Rows(out, key).all()
        assert sorted(r["arm"] for r in rows) == sorted((*rounds.design(3)["arms"], rounds.REPEAT) * SMALL["dev_eval"])
        calls = {r["arm"]: r["model_calls"] for r in rows}
        assert calls["G"] > calls["B"]                                  # G runs the self-review step as well
        every = rounds.workflows.SPECS[key].everything()
        tuned = json.loads((out / "tuning" / f"{key}-DG.json").read_text(encoding="utf-8"))
        assert tuned["initial_profile"]["pipeline"] == every["pipeline"]     # DG starts from G...
        assert tuned["head_profile"]["pipeline"] == every["pipeline"]        # ...and the method keeps G's steps here
        assert tuned["head_profile"]["exemplars"] == 8
        assert json.loads((out / "tuning" / f"{key}-D.json").read_text(encoding="utf-8"))["initial_profile"][
            "pipeline"] == []                                                # D starts from G6 as built
    assert set(board["pooled"]) == set(rounds.design(3)["arms"])         # no empty columns for arms it did not run
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "every component" in readme and "from G" in readme and "hand-written" not in readme


def test_a_design_can_size_one_workflow_differently(isolated):
    out = isolated / "rounds" / "round-09"
    small = {"dev": 1, "selection": 1, "confirmation": 1, "dev_eval": 1}
    got = rounds.draw_splits(9, KEYS, root=FIX, out=out, sizes=SMALL, by_workflow={"massive": small})
    assert {s: len(ids) for s, ids in got["massive"].items()} == small
    assert {s: len(ids) for s, ids in got["gsm8k"].items()} == SMALL
    assert json.loads((out / "splits.json").read_text(encoding="utf-8"))["sizes_by_workflow"] == {"massive": small}


def test_round_three_spends_mbpps_last_fresh_cases_and_still_confirms_on_enough():
    # 133 MBPP ids were fresh before round 3, fewer than the 150 a registered study needs, so nothing is kept back
    mbpp = rounds.design(3)["sizes"]["mbpp"]
    assert mbpp == {"dev": 20, "selection": 33, "confirmation": 40, "dev_eval": 40} and sum(mbpp.values()) == 133
    assert mbpp["confirmation"] >= rounds.workflows.POLICY.min_cases


def test_round_three_builds_its_workflows_at_its_call_budget(isolated, monkeypatch):
    seen: list[int] = []
    real = rounds.workflows.build

    def build(*a, **kw):
        wf = real(*a, **kw)
        seen.append(wf.models["local"].max_calls_per_run)
        return wf

    monkeypatch.setattr(rounds.workflows, "build", build)
    _run3(isolated)
    assert seen and set(seen) == {8}


def test_the_method_code_digest_changes_when_the_method_code_does(tmp_path):
    # design §4.3: a hand edit to the method makes a new v0; the Method data's digest does not see a code edit
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "select.py").write_text("x = 1\n", encoding="utf-8")
    first = rounds.code_digest(tmp_path / "a")
    assert first == rounds.code_digest(tmp_path / "a") and len(first) == 64
    (tmp_path / "a" / "select.py").write_text("x = 2\n", encoding="utf-8")
    assert rounds.code_digest(tmp_path / "a") != first
    assert len(rounds.code_digest()) == 64                       # the installed g6.metaharness
