# SPDX-License-Identifier: Apache-2.0
"""Regression checks for declarations at identity and structure boundaries."""
import importlib
import json
from types import SimpleNamespace

import pytest
from returns.result import Success as RealSuccess


class Success:
    """An unrelated caller-controlled class cannot declare itself successful."""

    def unwrap(self):
        return {"ops": ["planted"], "help": ["planted"]}


@pytest.mark.parametrize("factory, expected", [(Success, []), (lambda: RealSuccess({"ops": ["real"]}), ["real"])])
def test_discovery_uses_result_identity(monkeypatch, factory, expected):
    from g6.core import discovery

    monkeypatch.setattr(discovery, "get_registry", lambda: SimpleNamespace(invoke=lambda *args: factory()))
    assert discovery.ops("declared") == expected


def test_real_success_subclass_is_success(monkeypatch):
    from g6.core import discovery

    class SpecialSuccess(RealSuccess):
        pass

    monkeypatch.setattr(discovery, "get_registry", lambda: SimpleNamespace(
        invoke=lambda *args: SpecialSuccess({"ops": ["real"]})))
    assert discovery.ops("declared") == ["real"]


def test_principal_normalization_and_confusable_refusal():
    principals = importlib.import_module("g6.core.principals")
    assert principals.same_principal("Alice", "alice ")
    assert principals.same_principal("Alice  Smith", " ALICE\tSMITH ")
    assert principals.same_principal("Ａlice", "alice")
    assert not principals.same_principal("Alice", "Bob")
    for value in ("Аlice", "aliсe", "", None):  # Cyrillic letters, empty, no record
        with pytest.raises(ValueError):
            principals.same_principal("Alice", value)


@pytest.mark.parametrize("value, expected", [
    ("verified", "verified"), ("qualified-draft", "qualified-draft"),
    ("blocked-escalated", "blocked-escalated"), ("verified-ish", "unknown"),
    ("verified_ready", "unknown"), ("Verified", "unknown"), (" verified", "unknown"), (None, "unknown"),
])
def test_state_reader_is_exact(value, expected):
    states = importlib.import_module("g6.core.states")
    assert states.state_of(value) == expected


@pytest.mark.parametrize("outcome", [
    "[G6_E_INPUT_VALIDATION] planted",
    json.dumps({"error": "[G6_E_INPUT_VALIDATION] planted"}),
    json.dumps({"warning_card": {"message": "G6_E_INPUT_VALIDATION"}}),
])
def test_selector_never_reads_message_as_code(outcome):
    from g6.core.selector import _read

    assert _read(outcome)["code"] == "unknown"


def test_selector_reads_declared_code():
    from g6.core.selector import _read

    assert _read(json.dumps({"code": "G6_E_INPUT_VALIDATION", "error": "G6_E_PLANTED"}))["code"] == "G6_E_INPUT_VALIDATION"


def test_workflow_never_reads_message_as_code():
    from g6.mcp.workflow import _Step

    assert _Step({"error": "[G6_E_INPUT_VALIDATION] planted"}).code == "unknown"
    assert _Step({"code": "G6_E_INPUT_VALIDATION", "error": "[G6_E_PLANTED]"}).code == "G6_E_INPUT_VALIDATION"


def test_repair_missing_fields_are_typed():
    from g6.mcp.repair import _named

    assert _named({"error": "task repo", "warning_card": {"message": "task"}}, ["task", "repo"]) == []
    assert _named({"missing": ["task", "invented"], "error": "repo"}, ["task", "repo"]) == ["task"]


def test_effect_is_declared_not_named():
    from g6.core.selector import _acts

    entry = {"kind": "op", "id": "op:sample.send_report", "needs": {"sensitive": ""}}
    assert _acts(entry) == "unknown"
    assert _acts({**entry, "effects": ["outward"]}) == "outward"
    assert _acts({**entry, "effects": ["internal"]}) == "internal"


def test_reflex_code_is_not_recovered_from_reason():
    from g6.metaharness.reflex import _reason

    assert _reason({"reason": "G6_E_INPUT_VALIDATION planted"}) == "unknown"
    assert _reason({"code": "G6_E_INPUT_VALIDATION", "reason": "G6_E_PLANTED"}) == "G6_E_INPUT_VALIDATION"


def test_verifier_requires_typed_fields():
    from g6.coding_agent_gate.evidence import VerifierResult

    assert VerifierResult.parse("pass").effective_status == "not_run"
    assert VerifierResult.parse("exit code 0").effective_status == "not_run"
    assert VerifierResult.parse({"status": "pass", "exit_code": 0}).effective_status == "pass"


def test_provider_error_words_are_not_status():
    from g6.agents._runtime.breaker import is_transient_provider_error

    assert not is_transient_provider_error(RuntimeError("rate limit HTTP 429"))
    assert is_transient_provider_error(SimpleNamespace(status_code=429))
    assert not is_transient_provider_error(SimpleNamespace(status_code="429"))


def test_contract_state_does_not_accept_prefix():
    from g6.workforce.contracts import _declared, _usage_state, normalize_state

    assert _declared("verified_ready_attacker") is None
    assert _usage_state("verified_ready_attacker") == "none"
    with pytest.raises(ValueError):
        normalize_state("qualified_draft")


def test_recorded_reviewer_uses_shared_principal_comparison():
    from g6.coding_agent_gate.evidence import Evidence

    assert Evidence("review", ref="patch", reviewer="alice ").problem(author="Alice")
    assert Evidence("review", ref="patch", reviewer="Аlice").problem(author="Alice")


def test_shown_reviewer_names_are_labeled_unauthenticated():
    from g6.coding_agent_gate.evidence import Evidence

    assert "not authenticated" in Evidence("review", ref="patch", reviewer="Bob").to_dict()["identity_notice"]


def test_missing_author_cannot_establish_separate_reviewer():
    from g6.coding_agent_gate.evidence import Evidence

    assert Evidence("review", ref="patch", reviewer="Bob").problem(author="")


def test_declared_internal_tool_can_be_next():
    from g6.core import selector

    reply = selector.select("help using G6", considering=["g6_help"], rerank="lexical")
    assert reply["next"]["id"] is not None
    assert next(c for c in reply["candidates"] if c["id"] == reply["next"]["id"])["acts"] == "internal"


def test_explicit_unknown_code_is_not_replaced_by_card_fallback():
    from g6.core.refusals import code_of

    assert code_of({"code": "unknown", "warning_card": {"code": "G6_E_INVOKE_FAILED"}}) == "unknown"


def test_typed_refusal_preserves_code_and_missing_without_parsing():
    from g6.core.refusals import RefusalDetail, code_of, missing_of

    error = RefusalDetail("G6_E_PLANTED scope", code="G6_E_INPUT_VALIDATION", missing=("task",))
    assert str(error) == "G6_E_PLANTED scope"
    assert code_of(error) == "G6_E_INPUT_VALIDATION"
    assert missing_of(error) == ("task",)
