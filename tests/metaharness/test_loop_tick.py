# SPDX-License-Identifier: Apache-2.0
"""One tick of the self-improvement loop (design 6.1), run unchanged on two kinds of work, FakeLLM only."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core.llm import FakeLLM
from g6.metaharness import Profile
from g6.metaharness.domain import NO_JUDGE, FailureRecord
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.propose import dev_run
from g6.self_maintenance.pacing import ProviderWindow

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)


def _loop(tmp_path, domain=None, proposer=None, settings=SETTINGS, now=lambda: NOW, **kw):
    domain = domain if domain is not None else domains.WordProblems(tmp_path)
    proposer = proposer if proposer is not None else domains.proposes({"prompt": domains.WORKING})
    return ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer, now=now, clock=domains.Clock(), **kw), domain


def _enrolled(tmp_path, **kw):
    loop, domain = _loop(tmp_path, **kw)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop, domain


def _success(domain, loop, profile, cases) -> float:
    """What a study would do: run a profile on cases the loop never saw, outside the loop."""
    wins = sum(dev_run(loop.workflow, loop.mh.factory, profile, c, theories=None, clock=domains.Clock())[0]
               for c in cases)
    return wins / len(cases)


# Enrolment -----------------------------------------------------------------------------------------------------------

def test_nothing_runs_until_a_person_has_enrolled_the_domain(tmp_path):
    loop, domain = _loop(tmp_path)
    got = loop.tick()
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == "G6_E_LOOP_NOT_ENROLLED"
    assert domain.llm.calls == []


@pytest.mark.parametrize("name", ["", "auto:loop"])
def test_enrolment_needs_a_person(tmp_path, name):
    loop, _ = _loop(tmp_path)
    assert loop.enrol(name)["completion_state"] == "blocked-escalated"
    assert loop.tick()["warning_card"]["code"] == "G6_E_LOOP_NOT_ENROLLED"


def test_a_domain_with_no_judge_is_refused_when_enrolled_and_on_every_tick(tmp_path):
    domain = domains.WordProblems(tmp_path, judged=False)
    loop, _ = _loop(tmp_path, domain)
    for got in (loop.enrol("Ada"), loop.tick()):
        assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == NO_JUDGE
        assert "self-grading is not improvement" in got["message"]
    assert domain.llm.calls == []


# A tick -------------------------------------------------------------------------------------------------------------

def test_the_loop_improves_a_planted_weak_profile_on_a_non_coding_domain(tmp_path):
    loop, domain = _enrolled(tmp_path)
    start = loop.mh._head()[1]
    assert domains.TERSE in start.prompt
    got = loop.tick()
    assert got["outcome"] == "adopted", got
    head = loop.mh._head()[1]
    assert domains.WORKING in head.prompt and head.digest() != start.digest()
    sealed = domain.sealed_cases
    assert _success(domain, loop, head, sealed) > _success(domain, loop, start, sealed) + 0.3


def test_the_same_loop_runs_unchanged_on_a_coding_shaped_domain(tmp_path):
    domain = domains.BugFixes(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=domains.proposes({"prompt": domains.CODE_HINT}))
    got = loop.tick()
    assert got["outcome"] == "adopted", got
    assert domains.CODE_HINT in loop.mh._head()[1].prompt
    assert got["constraint"] == "accepted_defect"            # the domain's own cause, read through the same seam


def test_a_person_store_is_untouched_by_the_loops_adoption(tmp_path):
    loop, _ = _enrolled(tmp_path)
    loop.tick()
    assert [v["kind"] for v in loop.mh.store.versions()] == ["initial"]


def test_later_ticks_run_on_what_was_adopted_so_the_loop_compounds(tmp_path):
    domain = domains.WordProblems(tmp_path, two_steps=True)

    def reads_the_base_it_is_shown(system: str, user: str) -> str:
        change = {"prompt": domains.WORKING}
        return json.dumps(change if "step by step" not in user else {**change, "skill_text": domains.UNITS})

    loop, _ = _enrolled(tmp_path, domain=domain, proposer=FakeLLM(reads_the_base_it_is_shown))
    start = loop.mh._head()[1]
    first = loop.tick()
    assert first["outcome"] == "adopted", first
    one = loop.mh._head()[1]
    second = loop.tick()
    assert second["outcome"] == "adopted", second
    two = loop.mh._head()[1]
    assert loop.mh._head()[0] == 2 and domains.WORKING in two.prompt and domains.UNITS in two.skill_text
    sealed = domain.sealed_cases
    scores = [_success(domain, loop, p, sealed) for p in (start, one, two)]
    assert scores[0] < scores[1] < scores[2], scores


def test_the_gate_decides_and_a_change_that_helps_nothing_is_not_adopted(tmp_path):
    loop, domain = _enrolled(tmp_path, proposer=domains.proposes({"prompt": "Please be polite."}))
    start = loop.mh._head()
    got = loop.tick()
    assert got["outcome"] in ("no_winner", "refused") and got["completion_state"] == "blocked-escalated"
    assert loop.mh._head() == start


def test_a_gain_too_small_to_confirm_is_refused_by_the_gate(tmp_path):
    domain = domains.WordProblems(tmp_path)
    domain.tutor.strong = domain.tutor.weak + 0.01                 # wins a race now and then, proves nothing
    loop, _ = _enrolled(tmp_path, domain=domain)
    start = loop.mh._head()
    assert loop.tick()["outcome"] in ("no_winner", "refused")
    assert loop.mh._head() == start


def test_with_nothing_failing_the_tick_is_idle_and_spends_no_held_out_use(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=1.1)
    loop, _ = _enrolled(tmp_path, domain=domain)
    got = loop.tick()
    assert got["outcome"] == "idle" and got["completion_state"] == "qualified-draft"
    assert loop.mh.held_out_left() == loop.workflow.policy.max_evaluations


# The constraint ------------------------------------------------------------------------------------------------------

def test_every_cycle_names_its_constraint_in_the_local_log(tmp_path):
    loop, _ = _enrolled(tmp_path)
    got = loop.tick()
    row = loop.log.cycles()[-1]
    assert got["constraint"] == row["constraint"] == "wrong_answer"
    assert row["basis"] == "min_effective_capacity_of_declared_serial_stages"
    assert row["observations"] and row["dev_cases"] == len(loop.cut.tune) and row["outcome"] == "adopted"
    assert loop.log.path.is_relative_to(loop.root) and loop.log.path.is_file()


def test_the_constraint_is_the_changeable_cause_that_costs_the_most(tmp_path):
    domain = domains.WordProblems(tmp_path)
    real = domain.failures

    def costed(view):
        rows = real(view)
        return (*(dataclasses.replace(r, cause="minor") for r in rows[:2]),
                *(dataclasses.replace(r, cause="major") for r in rows[2:-3]),
                *(dataclasses.replace(r, cause="biggest_but_only_a_person_can", changeable=False) for r in rows[-3:]),
                *(FailureRecord(f"extra-{i}", "biggest_but_only_a_person_can", False, "", True) for i in range(30)))

    domain.failures = costed
    loop, _ = _enrolled(tmp_path, domain=domain)
    assert loop.tick()["constraint"] == "major"


def test_proposers_reading_failures_are_shown_only_the_constraints_cases(tmp_path):
    domain = domains.WordProblems(tmp_path)
    real = domain.failures
    marked: list[str] = []

    def split(view):
        rows = real(view)
        marked[:] = [r.case_id for r in rows[:4]]
        return (*(dataclasses.replace(r, cause="wide") for r in rows[4:]),
                *(dataclasses.replace(r, cause="narrow", changeable=False) for r in rows[:4]))

    domain.failures = split
    seen: list[str] = []

    def reply(system: str, user: str) -> str:
        if "development failures" in system:                       # FailureTraceStrategy's own prompt
            seen.extend(f["case_id"] for f in json.loads(user)["failures"])
        return json.dumps({"prompt": domains.WORKING})

    loop, _ = _enrolled(tmp_path, domain=domain, proposer=FakeLLM(reply))
    loop.tick()
    assert seen and not set(seen) & set(marked)


def test_no_constraint_is_named_on_records_not_independently_observed(tmp_path):
    domain = domains.WordProblems(tmp_path)
    real = domain.failures
    domain.failures = lambda view: tuple(dataclasses.replace(r, independently_observed=False) for r in real(view))
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    got = loop.tick()
    assert got["outcome"] == "no_constraint" and got["constraint"] == "unknown"
    assert "independent" in got["message"] and proposer.calls == []
    assert loop.mh.held_out_left() == loop.workflow.policy.max_evaluations


def test_a_constraint_only_a_person_can_change_is_said_and_not_worked_on(tmp_path):
    domain = domains.WordProblems(tmp_path)
    real = domain.failures
    domain.failures = lambda view: tuple(dataclasses.replace(r, changeable=False) for r in real(view))
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    got = loop.tick()
    assert got["outcome"] == "no_constraint" and "a person" in got["message"] and proposer.calls == []


# The envelope fields -------------------------------------------------------------------------------------------------

def test_every_reply_carries_the_cross_surface_envelope_and_never_says_verified(tmp_path):
    loop, _ = _enrolled(tmp_path)
    for got in (loop.status(), loop.tick()):
        assert {"completion_state", "warning_card", "evidence", "request_id", "run_id"} <= set(got)
        assert got["completion_state"] in ("qualified-draft", "blocked-escalated")


def test_status_says_where_the_loop_is(tmp_path):
    loop, _ = _enrolled(tmp_path)
    loop.tick()
    got = loop.status()
    assert got["head_version"] == 1 and got["store_version"] == 0 and got["paused"] == ""
    assert got["held_out_left"] == loop.workflow.policy.max_evaluations - 1
    assert got["cycles"] == 1 and got["scope"] == "shadow"


def test_the_adopted_profile_is_data_and_the_workflow_it_ran_under_did_not_move(tmp_path):
    loop, _ = _enrolled(tmp_path)
    before = loop.workflow.digest()
    loop.tick()
    assert loop.workflow.digest() == before == loop.mh.store.confirmed()["workflow_digest"]
    assert isinstance(loop.mh._head()[1], Profile)
