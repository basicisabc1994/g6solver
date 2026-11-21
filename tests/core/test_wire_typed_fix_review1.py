# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/wire-typed-fix: four defects, each a case that failed on b612d171."""
import json
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("sources,kinds", [
    ({"https://example.org": ""}, {"https://example.org": "url"}),
    ({"https://example.org": "The limit is 42."}, {"missing": "url"}),
], ids=["empty-source", "unknown-source"])
def test_grounding_metadata_returns_mcp_envelope(monkeypatch, sources, kinds):
    from g6.core import door
    from g6.mcp import invoke_reply

    # Keep the person's model settings and key file out of this offline test.
    monkeypatch.setattr(door, "person_model", lambda: None)
    reply = invoke_reply("grounding", "check_claim", json.dumps({
        "claim": "The limit is 42.", "sources": sources, "source_kinds": kinds,
    }), allow_sensitive=frozenset())
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"].startswith("G6_E_")


def test_structured_record_null_is_input_refusal():
    from g6.bundles.compliance_gap import run

    result = run({"regulations": "GDPR", "records": "[null]", "as_of": "2026-01-01"})
    assert result.completion_state == "blocked-escalated"
    assert result.code == "G6_E_INPUT_VALIDATION"


def test_coding_pull_brief_uses_mcp_submission_instructions(monkeypatch):
    from g6.mcp import mission
    from g6.workforce import coding, service

    wf = SimpleNamespace(
        runtime=SimpleNamespace(brief_inputs=lambda *args: ({}, "")),
        ledger=SimpleNamespace(attempts=lambda *args: 0),
        missions=SimpleNamespace(messages=lambda *args, **kwargs: []))
    spec = SimpleNamespace(key="patch", goal="Repair the source.", output_schema={},
                           max_attempts=3, call=None)
    monkeypatch.setattr(coding, "frozen", lambda *args: {"scope": {}})
    monkeypatch.setattr(coding, "status", lambda *args: {
        "intake": {}, "standard": {"intake": {}}, "checks": []})
    brief = service._brief(wf, "m1", spec)
    assert brief["instructions"] == coding.OBJECTIVE_INSTRUCTIONS
    result = mission._here({"awaiting_chat": [brief]})
    assert result["awaiting_chat"][0]["instructions"] == mission.SUBMIT_HERE


def test_sent_fallback_is_not_reported_as_nothing_sent(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from g6._context_engine import _relations as relations
    from g6.core import cloud_consent as cloud, consent_callers, llm

    sent = []

    def transport(url, body, headers, timeout):
        sent.append(url)
        raise llm.LLMError("HTTP 500")

    @contextmanager
    def admission(provider, sources, payload_sha256, **kwargs):
        # Synthetic in-memory sharing state: the first backend is shared;
        # unknown fallback destinations retain the real refusal rule.
        if provider == "cloud:unknown":
            cloud._refuse_unshared({}, provider, sources, ())
        yield

    class Wrapped(consent_callers.CallerWrapper):
        def complete(self, *args, **kwargs):
            try:
                return self.inner.complete(*args, **kwargs)
            except llm.LLMError:
                raise llm.LLMError("providers unavailable") from None

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(llm, "_post_json", transport)
    monkeypatch.setattr(consent_callers, "admission", admission)
    monkeypatch.setattr(relations.engine, "fetch", lambda *a, **k: (
        {"passage": {"view": {"text": "Alice depends on Bob."}}}, ()))
    first = llm.OpenAICompatibleCaller("synthetic", base_url="https://provider.invalid/v1", api_key=None)
    fallback = SimpleNamespace(complete=lambda *a, **k: "must never be called")
    caller = Wrapped(llm.FallbackCaller([first, fallback]))
    with pytest.raises(llm.LLMError, match="providers unavailable"):
        try:
            relations.extract(None, ["p1"], caller=caller, limits=relations.Limits(model_calls=2))
        finally:
            assert len(sent) == 1


# --- the consent watcher: "not sent" only when no request was counted anywhere in its scope -----------------------

class _Count:
    def __init__(self):
        self.n = 0

    def __call__(self):
        return self.n


def _watch():
    from g6.core import cloud_consent
    from g6.core.refusals import ConsentWatch

    watch = ConsentWatch((cloud_consent.REQUIRED, cloud_consent.STATE_UNAVAILABLE))
    watch.counted = _Count()
    return watch, cloud_consent.REQUIRED


def _refusal(code):
    from g6.core.llm import LLMError

    error = LLMError(f"{code}: not shared")
    error.code = code
    return error


@pytest.mark.parametrize("order", ["refusal-then-send", "send-then-refusal", "send-fail-then-fallback-refusal"])
def test_any_counted_request_means_sent_whatever_the_order(order):
    from g6.core.llm import LLMError

    watch, required = _watch()
    for step in order.split("-then-"):
        if step.startswith("send"):
            watch.counted.n += 1
        else:
            watch.tell(_refusal(required))
    assert watch.withheld(LLMError("providers unavailable")) == ""
    assert watch.withheld(_refusal(required)) == ""   # a counted request: never "nothing left this machine"


def test_a_refusal_with_nothing_counted_is_withheld():
    from g6.core.llm import LLMError

    watch, required = _watch()
    watch.tell(_refusal(required))
    assert watch.withheld(LLMError("providers unavailable")) == required


def test_a_kind_may_name_a_source_whose_text_is_empty():
    from g6.grounding.retrieval import TextRetriever

    TextRetriever.from_texts({"https://example.org": ""}, source_kinds={"https://example.org": "url"})
    with pytest.raises(ValueError):
        TextRetriever.from_texts({"a": "text"}, source_kinds={"b": "url"})
