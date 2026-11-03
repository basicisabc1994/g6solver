# SPDX-License-Identifier: Apache-2.0
"""The tick's own root cause analysis (loop plan Part 24): what the hand analysis of stage 3's refused tick did, as plain code, and
each analysis scored by the next tick as a prediction."""
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core.llm import FakeLLM
from g6.metaharness import reflex
from g6.metaharness.lessons import FAILED, HELD, OPEN
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.tick_rca import CAUSES, TickDiagnosis, TickRecord, analysis_is_the_constraint, scored, tick_rca
from g6.self_maintenance.pacing import ProviderWindow

STAGE3 = json.loads((Path(__file__).parent / "fixtures" / "stage3_tick.json").read_text(encoding="utf-8"))


def _codes(record):
    return [cause.code for cause in tick_rca(record).causes]


def test_stage_3s_refused_tick_is_read_as_the_hand_analysis_read_it_where_the_records_bear_it_out():
    record = TickRecord.from_dict(STAGE3)
    found = {cause.code: cause for cause in tick_rca(record).causes}
    assert set(found) == {"threshold_move", "race_noise", "edit_space_short"}
    assert "gate_underpowered" not in found          # it lost, 14 to 22: more cases would not have made it a gain
    # The hand analysis also named a skewed tuning split. The records do not bear it out: the base's tuning failures were 23 of 27
    # false alarms and its held-out failures 21 of 22, ten points apart, so the tuning split taught the held-out split's lesson.
    assert "split_mix_skew" not in found
    moved = found["threshold_move"].evidence
    assert moved["wins"] == {"right": 14} and moved["losses"] == {"wrong": 22}
    assert found["edit_space_short"].evidence["fields"] == ["exemplars", "prompt"]
    assert found["race_noise"].evidence["margin"] == 1 and found["race_noise"].evidence["tie_dropped"] is True


def test_the_causes_come_in_a_fixed_order_each_with_a_check_a_remedy_and_who_acts():
    diagnosis = tick_rca(TickRecord.from_dict(STAGE3))
    codes = [cause.code for cause in diagnosis.causes]
    assert codes == [code for code in CAUSES if code in codes]
    for cause in diagnosis.causes:
        assert cause.check and cause.remedy and cause.who in ("proposer", "method", "person")
        assert cause.prediction.measure == cause.code and cause.prediction.before == cause.measure


def _record(*, wins=(), losses=(), ties=(), both_wrong=(), tuning_failed=(), labels=None, races=(), fields=(("prompt",),),
            outcome="refused", power=0.9):
    held_out = ([(case, False, True) for case in wins] + [(case, True, False) for case in losses]
                + [(case, True, True) for case in ties] + [(case, False, False) for case in both_wrong])
    labels = labels or {}
    return TickRecord(outcome=outcome, labels=labels, tuning_failed=tuple(tuning_failed), held_out=tuple(held_out),
                      races=tuple(races), candidates=tuple({"candidate": f"c{i}", "fields": list(f)} for i, f in enumerate(fields)),
                      power={"gain_0.10": power}, enrolment={"method": "e1", "cut": "e1", "domain": "e1"})


def _labelled(prefix, label, count):
    return {f"{prefix}{i}": label for i in range(count)}


def test_a_gain_spread_over_both_labels_is_no_threshold_move():
    labels = {**_labelled("r", "right", 6), **_labelled("w", "wrong", 6)}
    real = _record(wins=["r0", "r1", "r2", "w0", "w1", "w2"], losses=["w3"], labels=labels)
    assert "threshold_move" not in _codes(real)


def test_wins_on_one_label_paid_for_with_losses_on_the_other_is_a_threshold_move():
    labels = {**_labelled("r", "right", 6), **_labelled("w", "wrong", 6)}
    traded = _record(wins=["r0", "r1", "r2", "r3"], losses=["w0", "w1", "w2", "w3", "r4"], labels=labels)
    assert "threshold_move" in _codes(traded)       # 4 of 5 losses on the other label: at the 80% line


def test_tuning_failures_shaped_like_the_held_out_ones_are_no_skew():
    labels = {**_labelled("t", "right", 10), **_labelled("u", "wrong", 10), **_labelled("r", "right", 6), **_labelled("w", "wrong", 6)}
    same = _record(tuning_failed=["t0", "t1", "u0", "u1"], both_wrong=["r0", "w0"], ties=["r1", "w1"], labels=labels)
    assert "split_mix_skew" not in _codes(same)     # the base fails half on each label in both splits
    skewed = _record(tuning_failed=["t0", "t1", "t2", "t3", "u0"], both_wrong=["w0", "w1", "w2"], ties=["r0"], labels=labels)
    assert "split_mix_skew" in _codes(skewed)       # 80% of tuning failures on right, none of the held-out ones


def _race(*rows):
    """Rows of (candidate, wins, losses, dropped_at_block[, other fields]), the winner first."""
    return {"winner": rows[0][0], "ranking": [{"candidate": r[0], "wins": r[1], "losses": r[2], "dropped_at_block": r[3],
                                              **(r[4] if len(r) > 4 else {})} for r in rows]}


def test_a_winner_clear_of_noise_with_nothing_tied_dropped_is_no_race_noise():
    clear = _record(races=[_race(("a", 12, 1, None), ("b", 3, 2, 1))])
    assert "race_noise" not in _codes(clear)


def test_a_winner_by_one_case_or_a_dropped_tie_is_race_noise():
    assert "race_noise" in _codes(_record(races=[_race(("a", 2, 1, None))]))
    assert "race_noise" in _codes(_record(races=[_race(("a", 12, 1, None, {"n": 20}), ("b", 12, 1, 1, {"n": 20}))]))


def test_a_candidate_that_changed_structure_is_no_short_edit_space():
    assert "edit_space_short" not in _codes(_record(fields=[("prompt",), ("retries",)]))
    assert "edit_space_short" in _codes(_record(fields=[("prompt",), ("exemplars", "hints")]))
    assert "edit_space_short" not in _codes(_record(fields=[("prompt",)], outcome="adopted"))


def test_too_few_held_out_cases_is_the_cause_only_where_the_candidate_did_not_lose():
    labels = _labelled("r", "right", 6)
    ahead = _record(wins=["r0", "r1", "r2"], losses=["r3"], labels=labels, power=0.3)
    assert "gate_underpowered" in _codes(ahead)
    behind = _record(wins=["r0"], losses=["r1", "r2", "r3"], labels=labels, power=0.3)
    assert "gate_underpowered" not in _codes(behind)
    powered = _record(wins=["r0", "r1", "r2"], losses=["r3"], labels=labels, power=0.8)
    assert "gate_underpowered" not in _codes(powered)


def test_no_held_out_evaluation_names_no_cause_that_needs_one():
    codes = _codes(_record(fields=[("retries",)]))
    assert not {"threshold_move", "split_mix_skew", "gate_underpowered"} & set(codes)


def test_the_next_tick_scores_each_cause_as_a_prediction():
    before = tick_rca(TickRecord.from_dict(STAGE3))
    labels = {**_labelled("r", "right", 6), **_labelled("w", "wrong", 6)}
    better = _record(wins=["r0", "r1", "w0", "w1"], losses=["r2"], labels=labels, races=[_race(("a", 20, 1, None))],
                     fields=[("retries",)])
    outcomes = dict(scored(before, better))
    assert outcomes["threshold_move"] == HELD and outcomes["race_noise"] == HELD and outcomes["edit_space_short"] == HELD
    again = TickRecord.from_dict({**STAGE3, "enrolment": {"method": "changed"}})     # a person changed something, and it came back
    assert dict(scored(before, again))["threshold_move"] == FAILED


def test_a_cause_the_next_tick_cannot_measure_stays_open():
    before = tick_rca(TickRecord.from_dict(STAGE3))
    nothing = _record(fields=[("prompt",)])                 # no held-out evaluation, no race
    outcomes = dict(scored(before, nothing))
    assert outcomes["threshold_move"] == OPEN and outcomes["race_noise"] == OPEN


def test_a_remedy_for_a_person_left_unapplied_is_not_the_analysis_failing():
    before = tick_rca(TickRecord.from_dict(STAGE3))
    same_enrolment = TickRecord.from_dict(STAGE3)
    assert dict(scored(before, same_enrolment))["edit_space_short"] == OPEN     # nobody gave the domain a new action
    enrolled_again = TickRecord.from_dict({**STAGE3, "enrolment": {"domain": "v2"}})
    assert dict(scored(before, enrolled_again))["edit_space_short"] == FAILED   # a new version, and still only wording


def test_a_diagnosis_kept_in_a_row_reads_back_as_the_same_predictions():
    before = tick_rca(TickRecord.from_dict(STAGE3))
    again = TickDiagnosis.from_dict(json.loads(json.dumps(before.to_dict())))
    assert [(c.code, c.prediction, c.who) for c in again.causes] == [(c.code, c.prediction, c.who) for c in before.causes]


# --- in the tick ------------------------------------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)
MONDAY = datetime(2026, 10, 26, tzinfo=UTC)


def _loop(tmp_path, proposer):
    method = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, use_recovery=False, use_reflection=False)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, MONDAY),), method=method, audit_per_tick=60)
    loop = ShadowLoop(domains.WordProblems(tmp_path), tmp_path / "loop", settings, proposer=proposer,
                      now=lambda: MONDAY + timedelta(days=2), clock=domains.Clock())
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop


def _wording(*_):
    _wording.n = getattr(_wording, "n", 0) + 1
    return json.dumps({"prompt": f"Note {_wording.n}."})        # a new wording each time, and one that wins nothing


def _named(row):
    return [cause["code"] for cause in row["rca"]["causes"]]


def test_a_tick_that_tuned_and_adopted_nothing_says_why_in_its_row(tmp_path):
    loop = _loop(tmp_path, FakeLLM(_wording))
    loop.tick()
    row = loop.log.cycles()[-1]
    assert row["outcome"] != "adopted" and row["rca"]["outcome"] == row["outcome"]
    assert "edit_space_short" in _named(row)


def test_an_adopted_tick_names_no_short_edit_space(tmp_path):
    loop = _loop(tmp_path, domains.proposes({"prompt": domains.WORKING}))
    loop.tick()
    row = loop.log.cycles()[-1]
    assert row["outcome"] == "adopted" and "edit_space_short" not in _named(row)


def test_the_next_tick_scores_the_last_ones_analysis_and_a_person_has_changed_nothing(tmp_path):
    loop = _loop(tmp_path, FakeLLM(_wording))
    loop.tick()
    loop.tick()
    first, second = loop.log.cycles()[-2:]
    assert second["rca_scored"] == [[code, OPEN] for code in _named(first)]


def test_an_analysis_that_cannot_be_made_costs_the_tick_nothing(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    def broken(record):
        raise RuntimeError("analysis fault")

    monkeypatch.setattr(loop_module, "tick_rca", broken)
    loop = _loop(tmp_path, domains.proposes({"prompt": domains.WORKING}))
    loop.tick()
    row = loop.log.cycles()[-1]
    assert row["outcome"] == "adopted" and row["rca"] == {"unreadable": "RuntimeError: analysis fault"}


def test_two_ticks_whose_remedies_failed_make_the_analysis_the_loops_constraint():
    rows = [{"tick": i, "outcome": "refused", "constraint": "false_alarm", "rca_scored": [["threshold_move", FAILED]]}
            for i in (1, 2)]
    found = reflex.diagnose(rows)
    assert found.constraint == reflex.ANALYSIS and found.level == reflex.LOOP
    rows[1]["rca_scored"] = [["threshold_move", HELD]]
    assert reflex.diagnose(rows).constraint != reflex.ANALYSIS


def test_the_replay_domain_labels_each_case_by_the_maintainers_verdict(tmp_path):
    from tests.metaharness.test_coding_replay import _domain

    domain = _domain(tmp_path)
    case = domain.development()[0]
    assert domain.label_of(case) == case.expected


def test_a_tick_whose_cases_cannot_be_labelled_keeps_its_outcome_and_says_the_analysis_was_unreadable(tmp_path):
    loop = _loop(tmp_path, FakeLLM(_wording))

    def broken(case):
        raise RuntimeError("label reader failed")

    loop.domain.label_of = broken
    loop.tick()
    row = loop.log.cycles()[-1]
    assert row["outcome"] not in ("error", "adopted") and row["rca"] == {"unreadable": "RuntimeError: label reader failed"}


def test_a_trade_the_next_tick_cannot_label_stays_open():
    before = tick_rca(TickRecord.from_dict(STAGE3))
    unlabelled = TickRecord.from_dict({**STAGE3, "labels": {}, "enrolment": {"method": "changed"}})
    assert dict(scored(before, unlabelled))["threshold_move"] == OPEN


def test_a_change_to_the_method_applies_no_remedy_of_the_domains():
    before = tick_rca(TickRecord.from_dict(STAGE3))
    method_only = TickRecord.from_dict({**STAGE3, "enrolment": {"method": "a larger selection budget"}})
    assert dict(scored(before, method_only))["edit_space_short"] == OPEN


def test_the_race_read_is_the_last_one_that_chose_the_champion():
    noisy_last = _record(races=[_race(("a", 20, 0, None)), _race(("a", 2, 1, None))])
    noisy_last = TickRecord(**{**noisy_last.__dict__, "champion": "a"})
    assert "race_noise" in _codes(noisy_last)


def test_a_gap_of_exactly_twenty_five_points_is_on_the_line_and_names_no_skew():
    labels = {**_labelled("t", "right", 20), **_labelled("r", "right", 20), **_labelled("u", "wrong", 20), **_labelled("w", "wrong", 20)}
    tuned = [f"t{i}" for i in range(11)] + [f"u{i}" for i in range(9)]        # 55% right
    unseen = [f"r{i}" for i in range(6)] + [f"w{i}" for i in range(14)]       # 30% right
    assert "split_mix_skew" not in _codes(_record(tuning_failed=tuned, both_wrong=unseen, labels=labels))


@pytest.mark.parametrize(("history", "stuck"), [
    ([[FAILED], [FAILED]], True),
    ([[FAILED], [HELD]], False),
    ([[FAILED, HELD], [OPEN], [FAILED]], True),          # an open score decides nothing, so two failed in a row
    ([[FAILED]], False),
    ([], False),
])
def test_two_remedies_failing_in_a_row_make_the_analysis_itself_the_constraint(history, stuck):
    assert analysis_is_the_constraint(history) is stuck


def test_a_trade_needs_eighty_percent_of_the_losses_on_one_other_label():
    labels = {"win": "A", **_labelled("b", "B", 8), **_labelled("c", "C", 5)}
    spread = _record(wins=["win"], losses=[*(f"b{i}" for i in range(5)), *(f"c{i}" for i in range(5))], labels=labels)
    assert "threshold_move" not in _codes(spread)       # half on each of two other labels: neither is at 80%
    one = _record(wins=["win"], losses=[*(f"b{i}" for i in range(8)), "c0", "c1"], labels=labels)
    assert "threshold_move" in _codes(one)               # 8 of 10 on one other label


def test_a_case_the_domain_gives_no_label_has_none_in_the_record():
    from types import SimpleNamespace

    from g6.metaharness.schema import SuiteCase

    loop = object.__new__(ShadowLoop)
    loop.mh = SimpleNamespace(ledger=SimpleNamespace(rows=lambda kind: []))
    loop.log = SimpleNamespace(enrolled=lambda: {})
    loop.settings = SimpleNamespace(method=Method())
    loop.domain = SimpleNamespace(label_of=lambda case: case.expected)
    loop.cut = SimpleNamespace(tune=(SuiteCase("t", "x", "right"),), selection=(), held_out=(SuiteCase("h", "y", None),))
    record = loop._read_for_analysis(SimpleNamespace(confirmation=None, champion=""), {"proposal": 0, "selection": 0, "report": 0})
    assert record.labels == {"t": "right"}


def test_a_dropped_candidate_is_compared_with_the_winner_on_the_cases_both_ran():
    winner = ("a", 10, 2, None, {"n": 12, "net_by_block": [10, 8]})
    behind = _record(races=[_race(winner, ("b", 9, 0, 1, {"n": 10, "net_by_block": [9]}))])
    assert "race_noise" not in _codes(behind)            # 9 against the winner's 10 on the first ten cases
    level = _record(races=[_race(winner, ("b", 10, 0, 1, {"n": 10, "net_by_block": [10]}))])
    assert "race_noise" in _codes(level)                 # as good as the winner on those ten: a dropped tie


def test_a_dropped_row_that_cannot_say_what_it_shared_with_the_winner_is_no_tie():
    unknown = _record(races=[_race(("a", 10, 2, None, {"n": 12}), ("b", 9, 0, 1, {"n": 10}))])
    assert "race_noise" not in _codes(unknown)
    found = tick_rca(_record(races=[_race(("a", 2, 1, None, {"n": 12}), ("b", 9, 0, 1, {"n": 10}))]))
    assert found.causes[0].evidence["ties_unmeasured"] == 1


def test_a_race_records_each_candidates_net_after_every_block():
    from g6.core.llm import FakeLLM as Fake
    from g6.harness import Check
    from g6.metaharness import Model, Profile, Suite, SuiteCase, Workflow
    from g6.metaharness.executor import model_call_factory
    from g6.metaharness.select import race

    def reply(system, user):
        i = int(user.strip())
        if "first" in system:
            return "1" if i < 10 else "0"
        if "second" in system:
            return "1" if i < 9 else "0"
        return "0" if i < 10 else "1"

    workflow = Workflow(name="blocks", task="{input}", oracle=lambda out, expected: out == expected,
                        checks=(Check("a bit", lambda out: out in ("0", "1"), check_id="bit", known_bad=("",), known_good="1"),),
                        suite=Suite(dev=(), held_out=(), selection=tuple(SuiteCase(str(i), str(i), "1") for i in range(12))),
                        models={"local": Model(Fake(reply))})
    base = Profile(routing={"answer": "local"})
    first, second = (base.model_copy(update={"prompt": p}) for p in ("first", "second"))
    result = race(workflow, model_call_factory(), base, [first, second], block=10, keep=0.5, budget=100)
    kept, dropped = result.ranking
    assert kept["net_by_block"] == [10, 8] and dropped["net_by_block"] == [9] and dropped["dropped_at_block"] == 1


def test_ticks_that_decided_nothing_do_not_hide_the_two_before_them_that_failed():
    rows = [{"tick": i, "outcome": "refused", "rca_scored": [["race_noise", FAILED]]} for i in (1, 2)]
    rows += [{"tick": i, "outcome": "no_winner", "rca_scored": [["race_noise", OPEN]]} for i in range(3, 3 + reflex.RECENT)]
    assert reflex.diagnose(rows).constraint == reflex.ANALYSIS
    assert reflex.diagnose(iter(rows)).constraint == reflex.ANALYSIS


_DOMAIN_FILE = '''
from dataclasses import replace
from pathlib import Path
from g6.core.llm import FakeLLM
from g6.metaharness.method import Method
from g6.metaharness.pool import EditSpace
from tests.metaharness.test_tick_rca import domains, _wording


class Wider(domains.WordProblems):
    def execution(self):
        return replace(super().execution(), space=EditSpace({"retries": [0, 1, 2]}))


LOOP = {"domain": domains.WordProblems(Path(__file__).parent), "proposer": FakeLLM(_wording),
        "method": Method(cycles=1, propose_budget=120, select_budget=400, race_block=12,
                         use_recovery=False, use_reflection=False), "audit_per_tick": 60}
'''


def _opened(folder, source):
    from g6.metaharness.runner import open_loop

    domain_file = folder / "domain.py"
    domain_file.write_text(source, encoding="utf-8")
    settings = {"domain_file": str(domain_file), "read_account": False,
                "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": MONDAY.isoformat()}]}
    loop, _ = open_loop(folder / "loop", settings=settings, now=lambda: MONDAY + timedelta(days=2))
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop


def test_a_method_changed_in_the_domains_own_file_applies_no_remedy_of_the_domains(tmp_path):
    first = _opened(tmp_path, _DOMAIN_FILE)
    first.tick()
    assert "edit_space_short" in _named(first.log.cycles()[-1])
    second = _opened(tmp_path, _DOMAIN_FILE.replace("race_block=12", "race_block=13"))
    second.tick()
    assert dict(second.log.cycles()[-1]["rca_scored"])["edit_space_short"] == OPEN


def test_an_analysis_whose_words_cannot_be_written_as_utf8_still_leaves_the_tick_its_row(tmp_path):
    loop = _loop(tmp_path, domains.proposes({"prompt": domains.WORKING}))

    def unreadable(case):
        raise ValueError("label reader failed on \ud800")

    loop.domain.label_of = unreadable
    loop.tick()
    row = loop.log.cycles()[-1]
    assert row["outcome"] == "adopted" and row["rca"]["unreadable"].endswith("\\ud800")


def test_a_label_that_cannot_be_written_as_utf8_is_written_spelled_out():
    from types import SimpleNamespace

    loop = object.__new__(ShadowLoop)
    loop.log = SimpleNamespace(cycles=lambda: [])
    labels = {**_labelled("r", "right\udc80", 5), **_labelled("w", "wrong", 5)}
    found = loop._analysis(_record(wins=["r0", "r1", "r2", "r3"], losses=["w0", "w1", "w2", "w3", "w4"], labels=labels))
    json.dumps(found, ensure_ascii=False).encode("utf-8")          # as a cycle's row is written
    assert "threshold_move" in [cause["code"] for cause in found["rca"]["causes"]]


def _reenrolled(loop, monkeypatch, execution):
    monkeypatch.setattr(loop.domain, "execution", lambda: execution)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    loop.tick()
    return dict(loop.log.cycles()[-1]["rca_scored"])["edit_space_short"]


def test_the_same_edits_in_another_order_apply_no_remedy_of_the_domains(tmp_path, monkeypatch):
    from dataclasses import replace

    from g6.metaharness.pool import EditSpace

    loop = _loop(tmp_path, FakeLLM(_wording))
    loop.tick()
    assert "edit_space_short" in _named(loop.log.cycles()[-1])
    reordered = replace(loop.domain.execution(), space=EditSpace({"retries": [1, 0]}))
    assert _reenrolled(loop, monkeypatch, reordered) == OPEN


def _step(output, contract, ctx):
    return output


def _another_step(output, contract, ctx):
    return output.strip()


def test_an_action_replaced_under_its_own_name_applies_the_domains_remedy(tmp_path, monkeypatch):
    from dataclasses import replace

    from g6.metaharness.schema import PipelineStep

    loop = _loop(tmp_path, FakeLLM(_wording))
    loop.tick()
    first = replace(loop.domain.execution(), pipeline_steps={"tidy": PipelineStep("tidy", "review", "keep", _step)})
    _reenrolled(loop, monkeypatch, first)                       # a new step is a new version of the domain too
    assert "edit_space_short" in _named(loop.log.cycles()[-1])
    again = replace(first, pipeline_steps={"tidy": PipelineStep("tidy", "review", "strip", _another_step)})
    assert _reenrolled(loop, monkeypatch, again) != OPEN


def test_a_check_replaced_under_its_own_name_applies_the_domains_remedy(tmp_path, monkeypatch):
    from dataclasses import replace

    from g6.harness import Check

    loop = _loop(tmp_path, FakeLLM(_wording))
    loop.tick()
    run = loop.domain.execution()
    name = next(iter(run.optional_checks))
    first = replace(run, optional_checks={name: Check("a number", lambda out: any(ch.isdigit() for ch in out), check_id=name,
                                                      known_bad=("",), known_good="1")})
    _reenrolled(loop, monkeypatch, first)
    assert "edit_space_short" in _named(loop.log.cycles()[-1])
    again = replace(run, optional_checks={name: Check("a short number", lambda out: out.strip().isdigit(), check_id=name,
                                                      known_bad=("",), known_good="1")})
    assert _reenrolled(loop, monkeypatch, again) != OPEN


def test_a_wider_edit_space_applies_the_domains_remedy(tmp_path):
    first = _opened(tmp_path, _DOMAIN_FILE)
    first.tick()
    assert "edit_space_short" in _named(first.log.cycles()[-1])
    second = _opened(tmp_path, _DOMAIN_FILE.replace('"domain": domains.WordProblems(', '"domain": Wider('))
    second.tick()
    assert dict(second.log.cycles()[-1]["rca_scored"])["edit_space_short"] != OPEN
