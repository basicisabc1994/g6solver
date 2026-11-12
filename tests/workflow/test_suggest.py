# SPDX-License-Identifier: Apache-2.0
"""The suggestion policy (design 17.3 "Suggestions"; AT-005, and AT-006 and AT-010 in part): zero to three, in the
published fixed order, with identities wording cannot change, no guessed saving, and nothing a hypothesis could use
to change a setting or weaken a safeguard."""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from g6.harness.workflow import diagnose as dg
from g6.harness.workflow import obstacle as ob
from g6.harness.workflow import record as rec
from g6.harness.workflow import suggest as sg
from tests.workflow.kit import event, sha

OUTCOME = ob.Outcome(sha("goal"), "add returns the sum", "attributed_label")
R1, R2 = "a" * 32, "b" * 32
NOW = datetime(2026, 11, 2, tzinfo=UTC)
DOC = Path(__file__).resolve().parents[2] / "docs" / "product_global_specs" / "suggestion_order.md"


class Open:
    """Decisions with nothing dismissed."""

    def standing(self, intervention_id, **_):
        return "open", ""


def _req(ident="R1", status="failed", checks=True, provenance="stated"):
    return {"id": ident, "status": status, "provenance": provenance, "checks": [
        {"id": f"tests/test_calc.py::test_{ident.lower()}", "verdict": "failed", "provenance": provenance}] if checks else []}


def _assessment(*requirements):
    return {"report": "REPORT", "requirements": list(requirements), "reasons": []}


def _run(run, start, ending=None, code=""):
    events = [event(run, "run_started", seconds=start, data={"origin_kind": "loop_session", "origin_sha256": sha(run)})]
    if ending:
        events.append(event(run, "loop_ended", seconds=start + 5, data={"ending": ending, **({"code": code} if code else {})}))
    return events


def _found(assessment=None, events=(), outcome=OUTCOME, **more):
    diagnosis = dg.diagnose(list(events) or _run(R1, 0), assessment=assessment, outcome=outcome, job_id="job-1")
    return sg.candidates(diagnosis, assessment, **more)


def _shown(found):
    return sg.ranked(found, Open(), task="t" * 64, now=NOW, outcome_sha256=OUTCOME.text_sha256)[0]


def test_zero_suggestions_is_valid():
    class AllDismissed:
        def standing(self, intervention_id, **_):
            return "suppressed", "you dismissed it for this task"
    shown, again = sg.ranked(_found(_assessment(_req())), AllDismissed(), task="t" * 64, now=NOW, outcome_sha256="")
    assert shown == () and again == ()
    assert sg.lines([], [], 3) == ["- **Suggestions:** none."]
    assert sg.candidates(dg.diagnose(_run(R1, 0), outcome=OUTCOME), None, allowed=()) == ()


def test_never_more_than_three():
    many = _assessment(*[_req(f"R{n}") for n in range(1, 9)])
    found = _found(many)
    assert len(found) > 3 and len(_shown(found)) == sg.MAX_SHOWN == 3
    rows = [one.to_dict() for one in _shown(found)]
    assert len(sg.lines(rows, [], 3)) == 3 and len(sg.lines(rows, [], 1)) == 1 and len(sg.lines(rows, [], 0)) == 1
    assert "(2 more in this reply's workflow field)" in sg.lines(rows, [], 1)[0]


def test_order_is_class_then_burden_then_id():
    def make(kind, target, cls, burden, **more):
        return sg.Suggestion(sg.Intervention(kind, target, "task"), cls, "r", burden, **more)
    receipt = rec.Ref("review", "job-1", sha("r"))
    mixed = [make("do_nothing", "", 3, "unknown", receipt=None), make("rerun_at_base", "R1", 2, "lower", receipt=None, hypothesis_id="h"),
             make("run_reproduction", "R2", 1, "unknown", receipt=receipt), make("run_reproduction", "R1", 1, "unknown", receipt=receipt),
             make("fix_setup", "X", 1, "known", receipt=receipt), make("grant_source", "reviewer", 1, "lower", receipt=receipt)]
    ordered = sg.order(mixed)
    assert [(one.cls, one.burden) for one in ordered] == [(1, "lower"), (1, "known"), (1, "unknown"), (1, "unknown"),
                                                         (2, "lower"), (3, "unknown")]
    ties = [one.intervention.id for one in ordered[2:4]]
    assert ties == sorted(ties)                                           # what remains is by intervention id
    assert sg.order(list(reversed(mixed))) == ordered                     # the input's order means nothing
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert sg.ORDER_VERSION in text and all(words in text for words in sg.CLASS_WORDS.values())


def test_unknown_burden_is_stated():
    (one,) = _shown(_found(_assessment(_req())))[:1]
    assert one.burden == "unknown" and one.to_dict()["burden_said"] == "burden unknown"
    assert "burden unknown" in sg.lines([one.to_dict()], [], 3)[0]
    measured = _found(_assessment(_req()), measured={"run_reproduction": 12.0, "rerun_at_base": 40.0})
    by_kind = {one.intervention.kind: one.burden for one in measured}
    assert by_kind == {"run_reproduction": "lower", "rerun_at_base": "known"}      # measured here, never guessed


def test_no_minutes_saved_or_score_in_any_line():
    found = _found(_assessment(_req(), _req("R2", status="not_established", checks=False)))
    rows = [one.to_dict() for one in sg.order(found)]
    said = " ".join(sg.lines(rows, [], 9)).lower() + " " + str(rows).lower()
    for banned in ("minute", "saves", "saved", "faster", "score", "utility", "probab", "likely", "%", "verified"):
        assert banned not in said, banned
    names = {f.name for cls in (sg.Suggestion, sg.Intervention) for f in dataclasses.fields(cls)}
    assert not names & {"score", "utility", "minutes", "probability", "confidence", "expected_impact", "rank"}


def test_action_outside_policy_is_not_a_candidate():
    everything = {one.intervention.kind for one in _found(_assessment(_req()))}
    assert everything == {"run_reproduction", "rerun_at_base"}
    limited = _found(_assessment(_req()), allowed=("rerun_at_base", "do_nothing"))
    assert {one.intervention.kind for one in limited} == {"rerun_at_base"}
    for kind in ("apply_patch", "change_setting", "delete_tests", "speed_up_stage"):
        with pytest.raises(ValueError):
            sg.Intervention(kind, "x", "task")


def test_identity_ignores_wording_job_ids_receipts_and_evidence_versions(monkeypatch):
    first = next(one for one in _found(_assessment(_req())) if one.intervention.kind == "run_reproduction")
    said_before = first.to_dict()["said"]
    monkeypatch.setitem(sg.WORDS, "run_reproduction", "reproduce it by running the stated check")       # reworded
    monkeypatch.setattr(sg, "ORDER_VERSION", "suggestion-order.v2")
    diagnosis = dg.diagnose(_run(R2, 500), assessment={**_assessment(_req()), "report": "ANOTHER REPORT"},
                            outcome=OUTCOME, job_id="job-2")                                           # new job, new receipt
    second = next(one for one in sg.candidates(diagnosis, _assessment(_req()))
                  if one.intervention.kind == "run_reproduction")
    assert second.receipt != first.receipt and second.to_dict()["said"] != said_before
    assert second.intervention.id == first.intervention.id
    assert sg.Intervention("run_reproduction", "R1", "task").id == first.intervention.id
    assert len({sg.Intervention("run_reproduction", "R1", "task").id, sg.Intervention("run_reproduction", "R2", "task").id,
                sg.Intervention("run_reproduction", "R1", "project").id, sg.Intervention("rerun_at_base", "R1", "task").id}) == 4


def test_do_nothing_is_offered_when_nothing_is_supported():
    passing = _assessment({"id": "R1", "status": "passed_on_stated_checks", "provenance": "stated",
                           "checks": [{"id": "t::a", "verdict": "passed"}]})
    (only,) = _found(passing)
    assert (only.intervention.kind, only.cls, only.reason_code) == ("do_nothing", 3, "nothing_supported")
    (line,) = sg.lines([only.to_dict()], [], 3)
    assert "do nothing" in line and "nothing above is supported" in line
    (missing,) = sg.candidates(dg.diagnose((), outcome=OUTCOME), None)
    assert (missing.intervention.kind, missing.intervention.target) == ("observe_more", "no_g6_observation")
    (unstated,) = _found(_assessment(_req()), outcome=None)               # an obstacle, but no stated outcome to tie it to
    assert (unstated.intervention.kind, unstated.intervention.target, unstated.cls) == ("observe_more", "stated_outcome", 3)


def test_a_hypothesis_never_yields_a_settings_change_or_external_action():
    denied = ("apply", "patch", "merge", "commit", "push", "install", "delete", "disable", "bypass", "skip", "consent",
              "confine", "sandbox", "approve", "permission", "setting", "config", "send", "upload", "external")
    for kind in sg.KINDS:
        assert not [word for word in denied if word in kind], kind
    code = "G6_E_NO_PYTHON_CHANGED"
    blocked = [*_run(R1, 0, "untested", code), *_run(R2, 20, "untested", code)]
    for found in (_found(_assessment(_req())), _found(None, blocked)):
        for one in found:
            assert one.intervention.kind in sg.KINDS and (one.hypothesis_id == "" or one.cls == 2)
            words = sg.WORDS[one.intervention.kind].lower()
            assert not [word for word in ("g6 will", "automatically", "is applied", "has been") if word in words]
    assert not hasattr(sg, "apply") and not hasattr(sg, "execute")


def test_action_addressing_the_stated_outcome_outranks_a_local_speedup():
    # AT-005: an action tied by a receipt to a requirement of the stated outcome is class 1 ...
    found = sg.order(_found(_assessment(_req())))
    first = found[0]
    assert (first.cls, first.intervention.kind, first.intervention.target) == (1, "run_reproduction", "R1")
    assert first.receipt == rec.Ref("review", "job-1", sha("REPORT")) and first.reason_code == "receipt_addresses_outcome"
    assert first.to_dict()["why_this_class"] == "backed by a receipt and addresses the stated outcome"
    # ... and a step that only shortens a stage the outcome does not wait on has no receipt for the outcome:
    # it is not admitted, with the reason said, however large its local gain
    speedup = sg.Suggestion(sg.Intervention("observe_more", "review_stage_seconds", "task"), 1, "local_speedup", "lower", None)
    assert sg.admit(speedup) == (False, "no receipt ties it to the stated outcome")
    assert speedup not in sg.order([speedup, *found]) and sg.order([speedup, *found])[0] == first
    slow = [*_run(R1, 0), event(R1, "tool_call", "t:1", seconds=5, data={"tool": "g6_review_patch", "seconds": 9000.0})]
    assert {one.intervention.kind for one in _found(_assessment(_req()), slow)} == {"run_reproduction", "rerun_at_base"}
    with pytest.raises(ValueError):
        sg.Intervention("speed_up_stage", "review", "task")


def test_a_non_g6_action_can_rank_first_and_do_nothing_is_offered():
    # AT-006, in part: what the person does without G6 ranks by the same rule as G6's own actions
    found = sg.order(_found(_assessment(_req("R2", status="not_established", checks=False))))
    assert [(one.intervention.kind, one.cls) for one in found] == [("state_a_check_for_requirement", 1)]
    code = "G6_E_NO_PYTHON_CHANGED"
    setup = sg.order(_found(None, [*_run(R1, 0, "untested", code), *_run(R2, 20, "untested", code)]))
    assert setup[0].intervention.kind == "fix_setup"                      # the person's own step comes first
    key = lambda one: (one.cls, sg.BURDENS.index(one.burden), one.intervention.id)       # noqa: E731
    assert [key(one) for one in setup] == sorted(key(one) for one in setup)               # by the rule, not by kind
    assert "kind" not in sg.order.__doc__ and _found(_assessment())[0].intervention.kind == "do_nothing"


def test_g6_as_obstacle_suggests_a_named_switch_and_never_a_safeguard():
    # AT-010, in part: G6 names a person-set switch for an optional feature; it touches no safeguard and applies nothing
    code = "G6_E_NO_PYTHON_CHANGED"
    found = _found(None, [*_run(R1, 0, "untested", code), *_run(R2, 20, "untested", code)])
    (switch,) = [one for one in found if one.intervention.kind == "reduce_g6_steps"]
    assert switch.intervention.target in sg.G6_SWITCHES and switch.cls == 3 and switch.reason_code == "g6_is_the_obstacle"
    for target in ("G6_ALLOW_UNCONFINED=1", "G6_MCP_SENSITIVE=patch_review", "g6_cloud_grant", "skip the review",
                   "G6_LOOP_CRITIC=off", "approve", ""):
        with pytest.raises(ValueError):
            sg.Intervention("reduce_g6_steps", target, "task")
    said = " ".join(sg.G6_SWITCHES).lower() + " " + sg.WORDS["reduce_g6_steps"].lower()
    for safeguard in ("consent", "confin", "sandbox", "sensitive", "review", "stop", "approv", "grant", "critic"):
        assert safeguard not in said, safeguard
    over = _found(_assessment(_req()), g6_over=("G6_QUESTIONS_PER_TASK=0", "G6_ALLOW_UNCONFINED=1"))
    assert [one.intervention.target for one in over if one.intervention.kind == "reduce_g6_steps"] == ["G6_QUESTIONS_PER_TASK=0"]
    doc = " ".join(sg.__doc__.split())
    assert "which the person sets" in doc and "G6 changes nothing" in doc
