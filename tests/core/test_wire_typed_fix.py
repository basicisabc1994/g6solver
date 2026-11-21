# SPDX-License-Identifier: Apache-2.0
"""The repair of the typed-reader branch: a declared field is read first; where none is declared, the base's exact
reading stays; a declaration may only tighten a gate, never open one. Each test here failed at the branch tip."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

# --- slice 1: actor kind and principals (causes A, A2, A3) -------------------------------------------------------------

H1, H2 = "a" * 64, "b" * 64


def _proposal():
    from g6.core.adaptation_gate import AdaptationProposal

    return AdaptationProposal(proposal_id="p1", proposer="learner", source_case_ids=("c1",),
                              target_component="harness.hints", intended_change="add hint h3",
                              predicted_benefit="fewer unit errors", falsifiers=("no change",),
                              test_plan="held-out split", candidate_hash=H1, baseline_hash=H2,
                              transfer_hypothesis="similar tasks", cost_cap=1.0, rollback="drop h3")


def _decide(reviewer, **extra):
    from g6.core.adaptation_gate import Control, decide_promotion, validate_locally

    p = _proposal()
    controls = [Control("neg", "negative", {"bad": True}, "task-author"),
                Control("pos", "positive", {"bad": False}, "task-author")]
    v = validate_locally(p, lambda case: bool(case["bad"]), controls)
    return decide_promotion(p, v, policy_version="v", reviewer=reviewer, owner_confirmed=True, regression="pass",
                            budget="pass", security="pass", **extra)


def test_the_auto_marker_decides_and_a_declared_kind_only_tightens():
    from g6.core.adaptation_gate import is_automated

    assert is_automated("auto:loop", actor_kind="person") is True
    assert is_automated(" AUTO:loop") is True
    assert is_automated("Ada") is False
    assert is_automated("Ada", actor_kind="person") is False
    assert is_automated("Ada", actor_kind="automated") is True


@pytest.mark.parametrize("reviewer, disposition, reasons, digest", [
    # literals from the base's decide_promotion for the same inputs
    ("owner", "shadow_approved", ("all mandatory inputs passed; shadow use only",),
     "f1c4a1af73a49b1b42803c3c37ff4e4d514ba1108d48c8461fff1d25720367e0"),
    ("auto:ci-bot", "blocked", ("the reviewer is an automated name (auto:), not a person",),
     "89e5de4fc4304516c0d3249d95d6994fc955ab45e65581e1ea8cef1e569b14b9"),
    ("Learner ", "blocked", ("the reviewer is the proposer",),
     "a058131a3a745ca9144677e0a29b09e52397f2e538f6034da79c53c19c0d88b9"),
])
def test_a_promotion_decision_for_base_inputs_is_the_base_decision(reviewer, disposition, reasons, digest):
    d = _decide(reviewer)
    assert (d.disposition, d.reasons, d.digest) == (disposition, reasons, digest)


def test_a_declared_kind_other_than_person_blocks_a_promotion():
    d = _decide("owner", reviewer_kind="automated")
    assert d.disposition == "blocked" and any("automated" in r for r in d.reasons)


def test_a_shadow_adoption_that_names_a_person_is_refused():
    from g6.metaharness import shadow

    body = {"by": "Ada", "scope": shadow.SCOPE, "version": 1, "decision_digest": "d", "profile": {"x": 1}}
    assert "an adoption is the loop's own: it never names a person" in shadow._rules("adopt", "1", body, {})
    body["by"] = shadow.ADOPTER
    assert shadow._rules("adopt", "1", body, {}) == []


def test_a_stored_cycle_log_with_a_persons_rows_is_read_back(tmp_path):
    from g6.core.ledger import ChainLedger
    from g6.self_maintenance.envelope import CycleLog

    path = tmp_path / "cycles.jsonl"
    plain = ChainLedger(path)
    plain.append("enrolled", "1", {"by": "Ada"})
    plain.append("paused", "2", {"reason": "a person paused it"})
    assert [kind for kind, _ in CycleLog(path)._rows()] == ["enrolled", "paused"]


def test_a_store_activation_without_a_proposer_reports_and_never_raises():
    from g6.metaharness.store import _store_rules

    problems = _store_rules("version", "0", {"kind": "activate", "version": 0, "accepted_by": "Ada"}, {})
    assert problems and "activate: the acceptor is the proposer" not in problems


def _queue(tmp_path):
    from g6.operator import ActionQueue, OperatorStore

    store = OperatorStore(tmp_path / "operator.db")
    scope = store.for_owner("dana")
    venture = scope.create_venture("newsletter", caps={"model": 1.0, "cash": 500.0})["venture_id"]
    now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    return store, ActionQueue(scope, clock=lambda: now), venture


def _draft(queue, venture):
    reply = queue.enqueue(venture, task_id="t1", action={"type": "post", "body": "The pilot starts Monday."},
                          drafted_by="agent-1",
                          review={"state": "qualified-draft", "code": "G6_E_OPERATOR_CHECKS_PASSED"})
    return reply["action_id"]


def test_an_auto_name_declared_a_person_cannot_decide_and_a_named_person_can(tmp_path):
    from g6.operator import QueueError
    from g6.operator.queue import _person

    with pytest.raises(QueueError, match="never recorded"):
        _person("auto:bot", "drafter", actor_kind="person")
    with pytest.raises(QueueError):
        _person("Bob", "drafter", actor_kind="automated")
    store, queue, venture = _queue(tmp_path)
    try:
        first, second = _draft(queue, venture), _draft(queue, venture)
        with pytest.raises(QueueError):
            queue.approve(venture, first, reviewer="auto:bot", reviewer_kind="person")
        assert queue.get(venture, first)["status"] == "proposed"
        assert queue.approve(venture, second, reviewer="dana")["status"] in ("approved", "voided", "caught")
    finally:
        store.close()


def test_the_queue_trigger_refuses_an_auto_name_in_sql():
    import sqlite3

    from g6.operator.queue import _SCHEMA

    with sqlite3.connect(":memory:") as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO actions (action_id, venture_id, task_id, action_type, body, meta_json, drafted_by, "
                     "state, review_json, status, decoy, created_at) VALUES ('a', 'v', 't', 'email', 'd', '{}', "
                     "'Alice', 'qualified-draft', '{}', 'proposed', 0, 'now')")
        conn.execute("INSERT INTO decisions (action_id, venture_id, outcome, reviewer, reviewer_kind, decoy, "
                     "text_before, text_after, note, at) VALUES ('a', 'v', 'approved', 'auto:bot', 'person', 0, 'd', "
                     "'d', '', 'now')")
        with pytest.raises(sqlite3.DatabaseError, match="needs a decision recorded"):
            conn.execute("UPDATE actions SET status='approved' WHERE action_id='a'")
        # a pure-SQL trigger: no connection needs a Python function registered to change a status
        conn.execute("INSERT INTO decisions (action_id, venture_id, outcome, reviewer, reviewer_kind, decoy, "
                     "text_before, text_after, note, at) VALUES ('a', 'v', 'approved', 'Bob', 'unknown', 0, 'd', "
                     "'d', '', 'now')")
        conn.execute("UPDATE actions SET status='approved' WHERE action_id='a'")
        assert conn.execute("SELECT status FROM actions").fetchone()[0] == "approved"


# --- slice 2: codes and missing fields (causes B1-B6, C) ---------------------------------------------------------------

def test_a_mission_refusal_with_no_code_keeps_the_stable_default_and_the_code_leads_its_words():
    from g6.mcp import mission

    def fails():
        raise ValueError("no such mission")

    reply = mission._guarded(fails)
    assert reply["warning_card"]["code"] == mission.REFUSED
    assert reply["error"].startswith(f"[{mission.REFUSED}]")


def test_no_route_declares_the_code_its_first_coded_reason_leads_with():
    from g6.core.refusals import code_of
    from g6.workforce.scheduler import NoRoute

    refused = NoRoute((("a", "too dear"), ("reproduce:run_tests", "[G6_E_ISOLATION_UNAVAILABLE] no boundary here")))
    assert code_of(refused) == "G6_E_ISOLATION_UNAVAILABLE"
    assert code_of(NoRoute((("a", "too dear G6_E_INSIDE"),))) == "unknown"


def test_a_review_step_with_no_task_is_an_input_refusal_on_its_card():
    from g6.mcp import review_loop

    assert review_loop._step("repo", "", "session")["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_a_code_that_is_not_a_g6_code_never_hides_the_card():
    from g6.core import cloud_consent
    from g6.core.refusals import code_of

    assert code_of({"code": "not a code", "warning_card": {"code": cloud_consent.REQUIRED}}) == cloud_consent.REQUIRED


@pytest.mark.parametrize("text, expected", [
    ("G6_E_LLM_TIMEOUT: no answer in time", "G6_E_LLM_TIMEOUT"),
    ("[G6_E_SENSITIVE_COMPONENT_DENIED] refused", "G6_E_SENSITIVE_COMPONENT_DENIED"),
    ("G6_E_ALONE", "G6_E_ALONE"),
    ("HTTP 500 from https://model: G6_E_MODEL_REFUSED: a body", "unknown"),
    ("see G6_E_INSIDE here", "unknown"),
    ("G6_E_AT_THE_END.", "unknown"),
    ("[G6_E_UNCLOSED refused", "unknown"),
])
def test_a_code_is_read_only_where_it_leads_the_words(text, expected):
    from g6.core.refusals import code_of

    assert code_of(ValueError(text)) == expected
    assert code_of({"error": text}) == expected


def test_a_step_that_was_not_refused_records_no_code():
    from g6.mcp.workflow import _Step

    assert _Step({"action": "stop", "ending": "clean", "ok": True,
                  "warning_card": {"code": "G6_E_REVIEW_UNTIL_CLEAN"}}).code == ""
    assert _Step({"action": "stop"}).code == ""
    assert _Step({"action": "stop", "ok": False, "error": "[G6_E_GIT_REPO_INVALID] x",
                  "warning_card": {"code": "G6_E_GIT_REPO_INVALID"}}).code == "G6_E_GIT_REPO_INVALID"


def _limits(failed):
    from types import SimpleNamespace

    from g6.harness import verification_proposer, verification_search

    return verification_proposer._Limits(SimpleNamespace(check=lambda: None, complete=failed),
                                         SimpleNamespace(last_usage=None), dict(verification_search.PROPOSER_SETTINGS),
                                         ["synthetic"])


def test_a_consent_refusal_before_any_request_is_recorded_as_not_sent():
    from g6.core import cloud_consent as cloud
    from g6.core.llm import LLMError

    def refused(*args, **kwargs):
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: withheld"))   # what the consent guard raises

    limited = _limits(refused)
    with pytest.raises(LLMError):
        limited.complete("system", "user")
    assert limited.calls[-1]["sent"] is False


def test_an_error_naming_the_consent_code_after_a_counted_request_was_sent():
    from g6.core import cloud_consent as cloud
    from g6.core.llm import LLMError
    from g6.core.model_calls import note_model_call

    def sent_then_failed(*args, **kwargs):
        note_model_call()
        raise LLMError(f"{cloud.REQUIRED}: the provider's words")

    limited = _limits(sent_then_failed)
    with pytest.raises(LLMError):
        limited.complete("system", "user")
    assert limited.calls[-1]["sent"] is True


def test_a_fallback_that_was_refused_then_sent_and_failed_was_sent():
    from g6.core import cloud_consent as cloud
    from g6.core.llm import LLMError
    from g6.core.model_calls import note_model_call

    def two_callers(*args, **kwargs):
        try:
            raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: withheld"))
        except LLMError as first:
            note_model_call()
            raise LLMError(f"every caller failed: {first}; HTTP 500") from None

    limited = _limits(two_callers)
    with pytest.raises(LLMError):
        limited.complete("system", "user")
    assert limited.calls[-1]["sent"] is True


# --- slice 3: the selector (causes D-acts, D-read) ---------------------------------------------------------------------

def test_a_safety_question_is_routed_to_the_action_gate():
    from g6.core import selector

    assert selector.select("is it safe to run this git push", phase="implement", rerank="lexical")["next"]["id"] == (
        "op:action_gating.evaluate")


def test_a_declared_effect_only_adds_acting():
    from g6.core.selector import _acts

    entry = {"kind": "op", "id": "op:sample.send_report", "needs": {"sensitive": ""}}
    assert _acts({**entry, "effects": ["internal"]})                       # its name says it sends
    assert _acts({**entry, "needs": {"sensitive": "runs code"}, "effects": ["internal"]})
    quiet = {"kind": "op", "id": "op:sample.summarise", "needs": {"sensitive": ""}}
    assert _acts(quiet) == "" and _acts({**quiet, "effects": ["internal"]}) == ""
    assert _acts({**quiet, "effects": ["outward"]})


def test_the_selector_reads_a_text_reply_and_a_replys_error_by_their_lead():
    import json

    from g6.core.selector import _read

    assert _read("[G6_E_INPUT_VALIDATION] [ACTION_GATING_ERROR] evaluate: action")["code"] == "G6_E_INPUT_VALIDATION"
    assert _read(json.dumps({"error": "[G6_E_INPUT_VALIDATION] planted"}))["code"] == "G6_E_INPUT_VALIDATION"
    assert _read("the words name G6_E_INPUT_VALIDATION")["code"] == ""
    assert _read("defect_detected")["word"] == "defect_detected"


# --- slice 4: input grammars (causes E1-E8) ----------------------------------------------------------------------------

def test_compliance_answers_read_the_same_as_lines_and_as_structure():
    from g6.bundles.compliance_gap import run

    lines = run({"regulations": "GDPR", "answers": "GDPR-30: yes | a processing register\nGDPR-35: no",
                 "records": "gdpr access request received 2026-01-05, answered 2026-02-03, extended no",
                 "as_of": "2026-03-01"})
    shaped = run({"regulations": "GDPR", "as_of": "2026-03-01",
                  "answers": {"GDPR-30": {"answer": "yes", "evidence": "a processing register"},
                              "GDPR-35": {"answer": "no", "evidence": ""}},
                  "records": '[{"regulation": "GDPR", "kind": "request", "fields": {"received": "2026-01-05", '
                             '"answered": "2026-02-03", "extended": false}}]'})
    assert lines.code != "G6_E_INPUT_VALIDATION" and lines.rows
    assert [(r.get("id"), r.get("result")) for r in lines.rows] == [(r.get("id"), r.get("result")) for r in shaped.rows]


def test_a_message_that_points_outside_the_code_raises_the_posture_for_an_exception_too():
    from g6.triage import classify

    got = classify(ValueError("permission denied: /etc/shadow"), "permission denied: /etc/shadow")
    assert got.posture == "review"
    assert classify("builtins.ZeroDivisionError").category == "arithmetic"
    assert classify(KeyError("k")).category == classify("KeyError").category == "key"


def test_a_reviewer_whose_name_is_part_of_the_authors_is_a_self_review():
    from g6.coding_agent_gate.evidence import Evidence

    assert Evidence("review", ref="patch", reviewer="Claude").problem(author="Claude Opus")
    assert Evidence("review", ref="patch", reviewer="Ada").problem(author="Claude Opus") == ""


def test_a_verifiers_exact_words_and_its_typed_fields_are_both_read():
    from g6.coding_agent_gate.evidence import VerifierResult

    assert VerifierResult.parse("pass").effective_status == "pass"
    assert VerifierResult.parse("exit code 1").effective_status == "fail"
    assert VerifierResult.parse("looks fine").effective_status == "not_run"
    assert VerifierResult.parse({"status": "pass", "exit_code": 0}).effective_status == "pass"


def test_a_declared_source_kind_cannot_split_one_host():
    from g6.grounding.lineage import _lineage
    from g6.grounding.retrieval import Passage

    assert _lineage(Passage("t", "https://www.example.org/a", source_kind="corpus")) == "host:example.org"
    assert _lineage(Passage("t", "https://example.org/b")) == "host:example.org"
    assert _lineage(Passage("t", "doi:10.1/x")) == "host:doi.org"


def test_a_rate_limit_is_read_from_typed_fields_first():
    from g6.agents._runtime.breaker import is_transient_provider_error
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import llm

    assert is_transient_provider_error(AgentRefusal(llm.RATE_LIMITED, "slow down", backend_failure=True))
    assert is_transient_provider_error(AgentRefusal("G6_E_AGENT_X", "x", detail={"status_code": 429}))
    assert not is_transient_provider_error(AgentRefusal("G6_E_AGENT_X", "rate limit", detail={"status_code": 500}))


def test_the_catalogue_shows_a_bare_command_and_hides_a_path_even_when_declared():
    from g6.mcp.catalogue import HOST_VALUE, _needs

    assert _needs("outside", [{"name": "t", "command": "git"}])[0]["command"] == "git"
    assert _needs("outside", [{"name": "t", "command": "git", "catalogue_command": "C:/tools/git.exe"}])[0][
        "command"] == HOST_VALUE


@pytest.mark.parametrize("state", ["blocked", "blocked_escalated", "failed", "error"])
def test_a_blocked_or_failed_state_is_read_as_blocked(state):
    from g6.workforce.contracts import BLOCKED, Outcome, _blocks, _usage_state

    assert Outcome.from_envelope({"completion_state": state}).state == BLOCKED
    assert _blocks({"completion_state": state})
    assert _usage_state(state) in ("blocked-escalated", "error")


def test_mission_steps_are_named_for_this_surface_where_g6_wrote_them():
    from g6.mcp.mission import _here

    got = _here({"more": 'mission_status with detail="full" adds: units',
                 "clarification": {"next_action": {"tool": "mission_accept"}},
                 "awaiting_human": [{"kind": "acceptance",
                                     "question": 'confirm it at your terminal: python -P -m g6.workforce.decide confirm '
                                                 'm1 --reason "..."'}],
                 "notes": "mission_create is caller data"})
    assert got["more"].startswith("g6_mission_status with")
    assert got["clarification"]["next_action"]["tool"] == "g6_mission_clarify"
    assert "g6_mission_confirm" in got["awaiting_human"][0]["question"]
    assert "g6.workforce.decide" not in got["awaiting_human"][0]["question"]
    assert got["notes"] == "mission_create is caller data"


def test_missing_fields_are_declared_first_then_read_from_the_schema_names_the_refusal_says():
    from g6.mcp.repair import _named

    assert _named({"missing": ["task"], "error": "[G6_E_INPUT_VALIDATION] repo"}, ["task", "repo"]) == ["task"]
    assert _named({"missing": "task", "error": "[G6_E_INPUT_VALIDATION] repo"}, ["task", "repo"]) == []
    assert _named({"error": "[G6_E_INPUT_VALIDATION] field task is required"}, ["task", "repo"]) == ["task"]
