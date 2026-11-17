# SPDX-License-Identifier: Apache-2.0
"""Phase C: model advice cannot expand authority, calls, or disclosed inputs."""
import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import consent_callers, selector
from g6.core.llm import EmptyRetry, FakeLLM, LLMError, NoModel
from g6.mcp import mission
from g6.skills import corpus
from tests.core.test_semantic_auto_repair2 import assert_auto_step, no_auto_access


def stage(reply):
    return next(s for s in reply["mechanisms"] if s["stage"] == "model_rerank")


def ids(reply):
    return [c["id"] for c in reply["candidates"]]


def install(monkeypatch, responder=None, default="OK"):
    fake = FakeLLM(responder, default=default)
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: fake)
    return fake


@pytest.mark.parametrize("mode", ["auto", "model"])
def test_rerank_reorders_only_offered_calls_and_records_disagreements(monkeypatch, mode):
    baseline = selector.select("review my patch", "verify", rerank="lexical", limit=8)
    token_limits = []
    original = FakeLLM.complete

    def complete(self, system, user, *, max_tokens):
        token_limits.append(max_tokens)
        return original(self, system, user, max_tokens=max_tokens)

    monkeypatch.setattr(FakeLLM, "complete", complete)

    def reverse(system, user):
        offered = json.loads(user)["candidates"]
        return json.dumps({"order": [{"key": c["key"], "reason": "fits the task"}
                                     for c in reversed(offered)]})

    fake = install(monkeypatch, reverse)
    fake.timeout = 60
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        result = selector.select("review my patch", "verify", rerank=mode, limit=8)
    if mode == "auto":
        assert result["candidates"] == baseline["candidates"] and not fake.calls
        assert result["completion_state"] == "qualified-draft"
        assert_auto_step(stage(result))
        return
    offered = json.loads(fake.calls[0][1])["candidates"]
    assert len(fake.calls) == 1 and len(offered) <= 16
    assert token_limits == [2048] and fake.timeout <= 15
    assert ids(result) != ids(baseline)
    assert stage(result)["ran"] and stage(result)["model"] == "FakeLLM"
    assert stage(result)["disagreements"]
    assert all({"lexical", "model"} <= set(d) for d in stage(result)["disagreements"])
    for candidate in result["candidates"]:
        entry = next(e for e in corpus._expanded(corpus.load()) if e["id"] == candidate["id"])
        assert candidate["call"] == entry["call"]
        assert candidate["validated"] == selector._validate(entry, candidate["call"])[0]
    assert result["completion_state"] == "qualified-draft"


def test_untrusted_order_drops_unknown_duplicates_and_changed_calls(monkeypatch):
    baseline = selector.select("review my patch", "verify", rerank="lexical", limit=8)

    def corrupt(system, user):
        offered = json.loads(user)["candidates"]
        return json.dumps({"order": [
            {"key": "tool:invented", "reason": "invented"},
            {"key": offered[1]["key"], "reason": "first"},
            {"key": offered[1]["key"], "reason": "duplicate"},
            {"key": offered[0]["key"], "reason": "changed", "call": {"tool": "invented"}},
        ]})

    fake = install(monkeypatch, corrupt)
    result = selector.select("review my patch", "verify", rerank="model", limit=8)
    offered = json.loads(fake.calls[0][1])["candidates"]
    assert ids(result)[0] == offered[1]["key"]
    assert len(ids(result)) == len(set(ids(result)))
    assert set(ids(result)) == set(ids(baseline))
    originals = {c["id"]: c["call"] for c in baseline["candidates"]}
    assert all(c["call"] == originals[c["id"]] for c in result["candidates"])
    assert ids(result)[1:] == [k for k in ids(baseline) if k != offered[1]["key"]]


@pytest.mark.parametrize("mode", ["auto", "model"])
@pytest.mark.parametrize("failure", ["no model", "model failed", "reply unreadable", "consent withheld",
                                     "oversize", "wrong shape", "decoder exhausted", "empty"])
def test_rerank_falls_back_with_reason(monkeypatch, mode, failure):
    baseline = selector.select("review my patch", "verify", rerank="lexical")
    if failure == "decoder exhausted":
        original_loads = json.loads

        def loads(text, *args, **kwargs):
            if text == "synthetic-decode-limit":
                raise MemoryError("synthetic decoder limit")
            return original_loads(text, *args, **kwargs)

        monkeypatch.setattr(json, "loads", loads)

    def fail(system, user):
        if failure == "consent withheld":
            raise LLMError("G6_E_CLOUD_CONSENT_REQUIRED: withheld")
        if failure == "model failed":
            raise LLMError("synthetic failure with private detail")
        if failure == "oversize":
            return json.dumps({"order": [], "padding": "x" * 40000})
        if failure == "wrong shape":
            return '{"order": "not a list"}'
        if failure == "decoder exhausted":
            return "synthetic-decode-limit"
        if failure == "empty":
            return ""
        return "unreadable"

    fake = install(monkeypatch, fail)
    if failure == "empty":
        monkeypatch.setattr(consent_callers, "provider_from_env", lambda: EmptyRetry(fake))
    if failure == "consent withheld":
        def deny(caller):
            def complete(system, user, *, max_tokens):
                from g6.core.cloud_consent import current_sources
                assert current_sources(), "the guarded send must carry client_request sources"
                raise LLMError("G6_E_CLOUD_CONSENT_REQUIRED: withheld")
            return SimpleNamespace(complete=complete)
        monkeypatch.setattr(consent_callers, "guard", deny)
    if failure == "no model":
        monkeypatch.setattr(consent_callers, "provider_from_env", NoModel)
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        result = selector.select("review my patch", "verify", rerank=mode)
    assert result["ok"] and ids(result) == ids(baseline)
    if mode == "auto":
        assert result["candidates"] == baseline["candidates"] and not fake.calls
        assert "private detail" not in json.dumps(result)
        assert_auto_step(stage(result))
        return
    reason = "reply unreadable" if failure in ("oversize", "wrong shape", "decoder exhausted", "empty") else failure
    assert not stage(result)["ran"] and reason in stage(result)["why"]
    assert "private detail" not in json.dumps(result)
    if failure == "consent withheld":
        assert not fake.calls and stage(result)["model_calls"] == 0
    if failure == "empty":
        assert len(fake.calls) == 1


@pytest.mark.parametrize("mode", ["auto", "model"])
def test_rerank_prompt_excludes_repository_arguments_and_outcome_payload(monkeypatch, mode):
    fake = install(monkeypatch, default='{"order": []}')
    secret = "synthetic-private-repository"
    arguments = dict(considering=[{
        "tool": "g6_review_patch", "arguments": {"repo": secret, "task": "review"}}],
        last_outcome=json.dumps({"code": "G6_E_INPUT_VALIDATION", "payload": secret}))
    baseline = selector.select("review changes", "verify", rerank="lexical", **arguments)
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        result = selector.select("review changes", "verify", rerank=mode, **arguments)
    if mode == "auto":
        assert result["ok"] and result["candidates"] == baseline["candidates"] and not fake.calls
        assert_auto_step(stage(result))
        return
    assert len(fake.calls) == 1
    prompt = json.loads(fake.calls[0][1])
    assert set(prompt) == {"task", "phase", "last_outcome", "candidates"}
    assert secret not in str(fake.calls)


def test_lexical_never_resolves_a_model(monkeypatch):
    def forbidden():
        pytest.fail("lexical must not resolve a model")
    monkeypatch.setattr(consent_callers, "provider_from_env", forbidden)
    result = selector.select("review changes", rerank="lexical")
    assert not stage(result)["ran"] and "lexical" in stage(result)["why"]


def test_remedy_availability_comes_only_from_corpus():
    assert "g6_agentic_run" not in selector.PENDING_TOOLS
    data = copy.deepcopy(corpus.load())
    component = next(iter(data["components"].values()))
    component["limits"] = [*component.get("limits", []), "G6_E_SYNTHETIC_LIMIT"]
    found = selector.remedy_for("G6_E_SYNTHETIC_LIMIT", data)
    assert selector.REMEDIES["component_limit"]["available"]
    assert found and found["available"] and found["call"]["tool"] == "g6_agentic_run"
    data["entries"] = [e for e in data["entries"] if e["id"] != "tool:g6_agentic_run"]
    absent = selector.remedy_for("G6_E_SYNTHETIC_LIMIT", data)
    assert absent and not absent["available"]


def check():
    return {"id": "check-1", "requirement_id": "R1", "intent": "change", "template": "example.v1",
            "bindings": {"function": "sample.operation", "cases": [{"args": [1], "returns": 2}]}}


def hypothesis(test=None):
    return {"claim": "A boundary may fail", "predicts": "The boundary example disagrees", "check": test or check()}


@pytest.fixture
def synthetic_repo(monkeypatch):
    monkeypatch.setattr(mission.repo_tests, "checked_repo", lambda value: Path("synthetic-private-repository"))
    monkeypatch.setattr(mission.repo_tests, "_git", lambda *args: b"tests/test_private_name.py\0")


def test_agent_hypotheses_require_valid_check_schema(monkeypatch, synthetic_repo):
    install(monkeypatch)
    bad_binding = check()
    bad_binding["bindings"] = {"function": "invalid"}
    proposed = {"id": "free", "requirement_id": "R1", "intent": "change", "file": "test_boundary.py",
                "source": "def test_boundary():\n    assert 1 == 2\n"}
    result = mission.premortem("synthetic-private-repository", "Handle the boundary", hypotheses=[
        hypothesis(), hypothesis(proposed), hypothesis({"template": "invented", "bindings": {}}),
        hypothesis(bad_binding)], propose=False)
    agents = [h for h in result["hypotheses"] if h["origin"] == "agent"]
    assert len(agents) == 2 and len(result["untestable"]) == 2
    assert all(h["why"] for h in result["untestable"])
    assert all(h["normative"] is False and h["can_pass"] is False for h in agents)


@pytest.mark.parametrize("mode", ["auto", True])
def test_model_hypotheses_are_testable_advice_without_repository_input(monkeypatch, synthetic_repo, mode):
    fake = install(monkeypatch, default=json.dumps({"hypotheses": [hypothesis(), hypothesis({"tool": "invented"})]}))
    baseline = mission.premortem("synthetic-private-repository", "Handle the boundary", propose=False)
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        result = mission.premortem("synthetic-private-repository", "Handle the boundary", propose=mode)
    if mode == "auto":
        assert result["ok"] and result["hypotheses"] == baseline["hypotheses"] and not fake.calls
        assert result["completion_state"] == "qualified-draft"
        assert_auto_step(result["model_step"], "premortem")
        return
    proposals = [h for h in result["hypotheses"] if h["origin"] == "model_proposal"]
    assert len(proposals) == 1 and proposals[0]["normative"] is False and proposals[0]["can_pass"] is False
    assert result["untestable"][0]["origin"] == "model_proposal"
    assert result["model_step"]["ran"] and result["model_calls"] == 1
    assert len(fake.calls) == 1 and "private" not in str(fake.calls)
    assert set(json.loads(fake.calls[0][1])) == {"task", "intent"}
    assert result["completion_state"] == "qualified-draft"
    assert proposals[0]["check"]["provenance"] == "model_hypothesis"


def test_propose_false_makes_no_call_and_fixed_risks_name_templates(monkeypatch, synthetic_repo):
    fake = install(monkeypatch)
    result = mission.premortem("synthetic", "Handle the boundary", propose=False)
    from g6.harness.verification_templates import REGISTRY
    assert not fake.calls and result["model_calls"] == 0
    assert not result["model_step"]["ran"]
    assert result["hypotheses"]
    assert all(h["check"]["template"] in REGISTRY for h in result["hypotheses"])
    assert all(h["status"] == "needs_bindings" for h in result["hypotheses"])


@pytest.mark.parametrize("failure", ["no model", "model failed", "reply unreadable", "consent withheld"])
def test_premortem_model_failure_keeps_local_results(monkeypatch, synthetic_repo, failure):
    def answer(system, user):
        if failure in ("model failed", "consent withheld"):
            raise LLMError("G6_E_CLOUD_CONSENT_REQUIRED" if failure == "consent withheld" else "failure")
        return "unreadable"
    install(monkeypatch, answer)
    if failure == "no model":
        monkeypatch.setattr(consent_callers, "provider_from_env", NoModel)
    result = mission.premortem("synthetic", "Handle the boundary", propose=True)
    assert result["ok"] and result["hypotheses"]
    assert not result["model_step"]["ran"] and failure in result["model_step"]["why"]


def test_premortem_declares_new_arguments():
    from g6.core.consent_declarations import TOOL_ARGUMENTS
    assert TOOL_ARGUMENTS["g6_premortem"]["hypotheses"] == "content"
    assert TOOL_ARGUMENTS["g6_premortem"]["propose"] == "content"
