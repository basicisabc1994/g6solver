# SPDX-License-Identifier: Apache-2.0
"""Obstacles with receipts, and the stated outcome (design 17.2; local contract 1's narrow cases; AT-001 and AT-004):
only an accepted check G6 reproduced failing, an integrity finding, a question pending across runs, a refused run or
an unsent review is an obstacle; anything else names nothing."""
from __future__ import annotations

from types import SimpleNamespace

from g6.harness import question_store as qs
from g6.harness.workflow import obstacle as ob
from g6.harness.workflow import record as rec
from tests.workflow.kit import TASK, event, sha

RUN, OTHER = "a" * 32, "b" * 32


def _req(ident="R1", status="failed", provenance="stated", verdict="failed", check_provenance="stated"):
    return {"id": ident, "status": status, "provenance": provenance,
            "checks": [{"id": f"tests/test_calc.py::test_{ident.lower()}", "verdict": verdict,
                        "provenance": check_provenance}]}


def _assessment(*requirements, reasons=()):
    return {"report": "REPORT", "outcome": "defect_detected", "requirements": list(requirements),
            "reasons": list(reasons)}


def _started(run, seconds, kind="loop_session"):
    return event(run, "run_started", seconds=seconds, data={"origin_kind": kind, "origin_sha256": sha(run)})


def _pending(seconds=0):
    return event("c" * 32, "question_pending", "q1", seconds=seconds, refs=[rec.Ref("question", sha("q1"), sha("q1"))])


def test_reproduced_failing_stated_check_is_an_obstacle_scoped_to_that_expectation():
    (found,) = ob.obstacles(_assessment(_req("R1"), _req("R2", status="passed_on_stated_checks", verdict="passed")),
                            None, (), job_id="job-1")
    assert found.kind == "failing_accepted_check" and found.requirement == "R1"
    assert found.scope == "requirement R1 on this patch"                 # one expectation on one artefact
    assert found.receipt == rec.Ref("review", "job-1", sha("REPORT"))
    check = ob.next_check((found,), _assessment(_req("R1")))
    assert check == {"kind": "run_reproduction", "target": "R1", "command": "pytest tests/test_calc.py::test_r1"}
    confirmed = ob.obstacles(_assessment(_req("R3", provenance="person_confirmed", check_provenance="person_confirmed")),
                             None, ())
    assert [o.kind for o in confirmed] == ["failing_accepted_check"]


def test_model_written_failing_check_names_nothing():
    for requirement in (_req(provenance="model_hypothesis"), _req(check_provenance="model_hypothesis"),
                        _req(provenance="derived"), _req(check_provenance="proposed")):
        assert ob.obstacles(_assessment(requirement), None, ()) == ()
    # a reviewer's own failing test in a loop, and a derived defect reason, are not accepted checks either
    assert ob.obstacles(_assessment(reasons=[{"stage": "tier_2", "code": "derived_test_failed", "detail": "d"}]),
                        {"ending": "open", "report": "R"}, ()) == ()
    assert ob.obstacles(None, {"ending": "contested", "report": "R"}, ()) == ()


def test_a_check_that_did_not_fail_names_nothing():
    for requirement in (_req(status="not_established", verdict="collection_error"),
                        _req(status="passed_on_stated_checks", verdict="passed"), _req(status="failed", verdict="passed")):
        assert ob.obstacles(_assessment(requirement), None, ()) == ()


def test_slow_stage_alone_names_nothing():
    slow = [_started(RUN, 0), event(RUN, "loop_round", "1", seconds=5, data={"round": 1, "seconds": 9000.0}),
            event(RUN, "tool_call", "t:r1", seconds=6, data={"tool": "g6_review_patch", "seconds": 7200.0}),
            event(RUN, "report_returned", seconds=9000, data={"run_seconds": 9000.0})]
    assert ob.obstacles(None, None, slow) == ()
    assert ob.obstacles(_assessment(_req(status="passed_on_stated_checks", verdict="passed")), None, slow) == ()
    assert ob.next_check((), None) is None


def test_missing_events_give_insufficient_observation():
    assert ob.obstacles(None, None, ()) == ()
    assert ob.obstacles({}, {}, ()) == () and ob.next_check((), {}) is None


def test_integrity_block_comes_with_its_review_receipt_and_offers_no_command():
    found = ob.obstacles(_assessment(_req("R1"), reasons=[{"stage": "integrity", "code": "x", "detail": "d"}]), None,
                         (), job_id="job-2")
    assert [o.kind for o in found] == ["integrity_block", "failing_accepted_check"]      # an integrity finding leads
    assert found[0].receipt.id == "job-2"
    assert ob.next_check(found, _assessment(_req("R1"))) is None    # no check is offered over compromised tests


def test_unknown_intent_gives_no_outcome_and_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    for intent in (None, {}, {"goal": ""}, {"goal": "   "}, {"goal": 7}, {"success_criteria": ["x"]}, "a goal"):
        assert ob.stated_outcome(intent, (), "fix this bug") is None
    assert not (tmp_path / "home").exists()                               # nothing was reserved in A's store
    assert not qs.TaskQuestions(TASK).exists()


def test_the_outcome_is_the_confirmed_expectation_first_then_the_supplied_goal():
    one = SimpleNamespace(question=SimpleNamespace(function="shop.prices.total", requirement_id="R1", sha256=sha("q")),
                          answer=SimpleNamespace(option="B"))
    confirmed = ob.stated_outcome({"goal": "ship the refund flow"}, (one,), "fix this bug")
    assert confirmed.provenance == "person_confirmed" and "shop.prices.total" in confirmed.shown
    assert confirmed.text_sha256 == sha("q")
    stated = ob.stated_outcome({"goal": "ship the  refund\nflow " + "x" * 300}, (), "fix this bug")
    assert stated.provenance == "attributed_label" and len(stated.shown) <= 140 and "\n" not in stated.shown
    assert stated.shown.startswith("ship the refund flow") and "fix this bug" not in stated.shown


def test_pending_question_across_two_runs_is_an_obstacle_with_its_receipt():
    pending = _pending(seconds=10)
    one_run = [pending, _started(RUN, 20)]
    assert ob.obstacles(None, None, one_run) == ()                        # one run is not a pattern
    earlier = [pending, _started(RUN, 0), _started(OTHER, 5)]
    assert ob.obstacles(None, None, earlier) == ()                        # both began before it was asked
    calls = [pending, _started(RUN, 20), _started(OTHER, 30, kind="tool_calls")]
    assert ob.obstacles(None, None, calls) == ()                          # a task's call list is not a review run
    (found,) = ob.obstacles(None, None, [pending, _started(RUN, 20), _started(OTHER, 30)])
    assert found.kind == "pending_question" and found.receipt == rec.Ref("question", sha("q1"), sha("q1"))
    assert ob.next_check((found,), None)["kind"] == "answer_pending_question"


def test_review_unsent_for_want_of_a_grant_is_an_obstacle_attributed_to_g6_setup():
    loop = {"ending": "untested", "run_id": "f" * 32, "report": "The review was not sent.",
            "error": "[G6_E_CLOUD_CONSENT_REQUIRED] Results were withheld"}
    (found,) = ob.obstacles(None, loop, ())
    assert found.kind == "review_unsent" and "G6's own setup" in ob.WORDS[found.kind]
    assert found.receipt.kind == "report" and found.receipt.id == "f" * 32
    assert ob.next_check((found,), None)["kind"] == "grant_source"
    (refused,) = ob.obstacles(None, {**loop, "error": "[G6_E_NO_PYTHON_CHANGED] nothing"}, ())
    assert refused.kind == "refused_run" and "G6_E_NO_PYTHON_CHANGED" in refused.scope
    assert ob.obstacles(None, {**loop, "ending": "clean"}, ()) == ()
    assert ob.obstacles(None, {**loop, "error": "free text with no code"}, ()) == ()


def test_fixed_words_never_name_a_constraint_or_a_cause():
    said = " ".join(ob.WORDS.values()).lower()
    for banned in ("constraint", "bottleneck", "root cause", "because", "verified", "the cause"):
        assert banned not in said
    assert set(ob.WORDS) == set(ob.OBSTACLE_KINDS)
