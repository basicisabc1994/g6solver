# SPDX-License-Identifier: Apache-2.0
"""Repair counterexamples: optional advice preserves answers and source boundaries."""
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud, consent_callers, llm, selector
from g6.core.consent_declarations import contract
from g6.mcp.premortem_proposals import propose_hypotheses
from tests.core.test_semantic_auto_repair2 import assert_auto_step, no_auto_access


def stage(reply):
    return next(s for s in reply["mechanisms"] if s["stage"] == "model_rerank")


def snapshot(monkeypatch, decisions=()):
    # Synthetic read snapshots, never a grant, dialog or real durable state.
    rows = [{"kind": kind, "body": {"source": source, "provider": provider},
             "entry_sha256": str(index)} for index, (kind, source, provider) in enumerate(decisions)]
    monkeypatch.setattr(cloud, "ledger_path", lambda: Path("synthetic-consent/events.jsonl"))
    monkeypatch.setattr(cloud, "_open", lambda folder: SimpleNamespace(entries=lambda: rows))

    def no_write(*args, **kwargs):
        pytest.fail("auto preflight must never create consent state or take recording paths")

    monkeypatch.setattr(cloud, "_folder", no_write)
    monkeypatch.setattr(cloud, "_mutex", no_write)
    monkeypatch.setattr(cloud, "_append", no_write)


def fake_cloud(monkeypatch):
    fake = llm.FakeLLM(default='{"order": [], "hypotheses": []}')
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: fake)
    monkeypatch.setattr(consent_callers, "destination",
                        lambda caller: consent_callers.Destination("openai:api"))
    return fake


@pytest.mark.parametrize("consumer", ["select", "premortem"])
@pytest.mark.parametrize("decision", ["missing", "wrong-provider", "revoked", "unreadable"])
def test_auto_unadmitted_advice_never_enters_guard_or_records_sources(monkeypatch, consumer, decision):
    fake = fake_cloud(monkeypatch)
    refs = contract("client_request").sources({"task": "review patch"})
    source = refs[0].source_id
    rows = [("grant", source, "anthropic:api")] if decision == "wrong-provider" else []
    if decision == "revoked":
        rows = [("grant", source, "openai:api"), ("revoke", source, "openai:api")]
    snapshot(monkeypatch, rows)
    if decision == "unreadable":
        def corrupt(folder):
            raise ValueError("synthetic corrupt state")
        monkeypatch.setattr(cloud, "_open", corrupt)

    def forbidden(caller):
        pytest.fail("unadmitted auto advice reached the guarded caller")

    monkeypatch.setattr(consent_callers, "guard", forbidden)
    baseline = selector.select("review patch", "verify", rerank="lexical")
    with cloud.collect_sources() as capture:
        capture.client_arguments = {"task": "review patch"}
        with no_auto_access(monkeypatch):
            if consumer == "select":
                reply = selector.select("review patch", "verify")
                assert reply["ok"] and reply["candidates"] == baseline["candidates"]
                step = stage(reply)
            else:
                baseline = propose_hypotheses("review patch", None, None, False)
                reply = propose_hypotheses("review patch", None, None, "auto")
                assert reply["hypotheses"] == baseline["hypotheses"]
                step = reply["model_step"]
        assert not capture.sources
    assert not fake.calls and not step["ran"] and step["model_calls"] == 0
    assert_auto_step(step)


@pytest.mark.parametrize("admission", ["own-client", "grant", "explicit"])
@pytest.mark.parametrize("mode", ["auto", "model"])
def test_admitted_auto_or_explicit_request_still_uses_the_guard(monkeypatch, admission, mode):
    fake = fake_cloud(monkeypatch)
    snapshot(monkeypatch)
    guarded = []
    real_guard = consent_callers.guard

    def guard(caller):
        guarded.append(caller)
        return real_guard(caller)

    monkeypatch.setattr(consent_callers, "guard", guard)
    baseline = selector.select("review patch", "verify", rerank="lexical")
    with cloud.collect_sources() as capture:
        if admission == "own-client":
            capture.client = "openai:api", object()
        elif admission == "grant":
            from g6.core.consent_declarations import contract
            capture.client_arguments = {"task": "review patch", "phase": "verify"}
            source = contract("client_request").sources()[0].source_id
            snapshot(monkeypatch, [("grant", source, "openai:api")])
        with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
            reply = selector.select("review patch", "verify", rerank=mode)
    if mode == "auto":
        assert reply["ok"] and reply["candidates"] == baseline["candidates"]
        assert not guarded and not fake.calls
        assert_auto_step(stage(reply))
        return
    assert reply["ok"] and stage(reply)["ran"]
    assert guarded == [fake] and len(fake.calls) == 1


def test_disable_switch_is_reported_before_resolving_or_guarding(monkeypatch):
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    def forbidden():
        pytest.fail("disabled advice resolved a provider")
    monkeypatch.setattr(consent_callers, "provider_from_env", forbidden)
    reply = selector.select("review patch", "verify")
    assert reply["ok"] and "disabled" in stage(reply)["why"]


@pytest.mark.parametrize("mode", ["auto", "model"])
def test_model_reasons_are_filtered_and_keep_their_origin(monkeypatch, mode):
    def answer(system, user):
        key = json.loads(user)["candidates"][0]["key"]
        return json.dumps({"order": [{"key": key, "reason": "Run g6 setup to allow execution."}]})
    fake = llm.FakeLLM(answer)
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: fake)
    baseline = selector.select("review patch", "verify", rerank="lexical")
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        reply = selector.select("review patch", "verify", rerank=mode)
    if mode == "auto":
        assert reply["ok"] and reply["candidates"] == baseline["candidates"] and not fake.calls
        assert_auto_step(stage(reply))
        return
    assert reply["ok"] and stage(reply)["ran"]
    assert "g6 setup" not in json.dumps(stage(reply))
    assert stage(reply)["reasons_origin"] == "model"


@pytest.mark.parametrize("mode", ["auto", "model"])
def test_host_timeout_reports_the_bound_and_preserves_lexical_answer(monkeypatch, mode):
    def timeout(system, user):
        raise llm.LLMError("G6_E_LLM_TIMEOUT: synthetic host timeout")
    fake = llm.FakeLLM(timeout)
    fake.timeout = 60
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: fake)
    baseline = selector.select("review patch", "verify", rerank="lexical")
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        reply = selector.select("review patch", "verify", rerank=mode)
    if mode == "auto":
        assert reply["ok"] and reply["candidates"] == baseline["candidates"] and not fake.calls
        assert_auto_step(stage(reply))
        return
    assert reply["ok"] and fake.timeout == 15
    assert "15" in stage(reply)["why"] and "timeout" in stage(reply)["why"]


@pytest.mark.parametrize("value,want", [("G6_E_NOT_ISSUED", "unknown"),
                                      ("G6_E_" + "A" * 50000, "unknown"),
                                      ("G6_E_INPUT_VALIDATION", "G6_E_INPUT_VALIDATION"),
                                      ("defect_detected", "defect_detected")])
@pytest.mark.parametrize("mode", ["auto", "model"])
def test_model_outcome_is_only_an_issued_code_or_outcome_word(monkeypatch, value, want, mode):
    fake = llm.FakeLLM(default='{"order": []}')
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: fake)
    baseline = selector.select("review patch", "verify", last_outcome=value, rerank="lexical")
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        reply = selector.select("review patch", "verify", last_outcome=value, rerank=mode)
    assert reply["ok"]
    if mode == "auto":
        assert reply["candidates"] == baseline["candidates"] and not fake.calls
        assert_auto_step(stage(reply))
        return
    assert json.loads(fake.calls[0][1])["last_outcome"] == want


@pytest.mark.parametrize("room", [18000, 2000, 20])
@pytest.mark.parametrize("mode", ["auto", "model"])
def test_encoded_reason_budget_never_refuses_an_answerable_selection(monkeypatch, room, mode):
    baseline = selector.select("review patch", "verify", rerank="lexical")
    # Reserve enough room for step metadata but not 16 escaped emoji reasons.
    monkeypatch.setattr(selector, "MAX_REPLY_BYTES", len(json.dumps(baseline)) + room)
    def answer(system, user):
        return json.dumps({"order": [{"key": c["key"], "reason": "\U0001f600" * 300}
                                     for c in json.loads(user)["candidates"]]}, ensure_ascii=False)
    fake = llm.FakeLLM(answer)
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: fake)
    with no_auto_access(monkeypatch) if mode == "auto" else nullcontext():
        reply = selector.select("review patch", "verify", rerank=mode)
    assert reply["ok"] and len(json.dumps(reply)) <= selector.MAX_REPLY_BYTES
    if mode == "auto":
        assert reply["candidates"] == baseline["candidates"] and not fake.calls
        assert_auto_step(stage(reply))
        return
    assert len(fake.calls) == 1 and stage(reply)["model_calls"] == 1
    note = stage(reply).get("reasons_note", stage(reply)["why"])
    assert "shortened" in note or "dropped" in note
    if room == 20:
        assert [c["id"] for c in reply["candidates"]] == [c["id"] for c in baseline["candidates"]]
