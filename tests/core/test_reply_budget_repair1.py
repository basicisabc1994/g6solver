"""Declared reply positions, opaque data and lossless oversize controls; offline."""
import copy
import json

import pytest

from g6.core.door import blocked_reply
from g6.core.reply_budget import MAX_REPLY_BYTES as CAP, fit_reply


@pytest.mark.parametrize("field", ["result", "results", "data", "metadata"])
def test_application_data_is_never_partially_compacted(field):
    payload = {"mechanisms": [{"stage": "model_rerank", "reasons": {"a": "x" * CAP}}],
               "model_step": {"why": "x" * CAP}, "request_id": "x" * CAP,
               "result": {"run_id": "x" * CAP}}
    value = {"ok": True, field: payload}
    original = copy.deepcopy(value)
    shown = fit_reply(value)
    assert value == original
    assert shown.get(field) in (payload, "[withheld for size]")
    assert len(json.dumps(shown).encode()) <= CAP


@pytest.mark.parametrize("payload", [
    {"mechanisms": ["human-review"]}, {"mechanisms": None}, {"model_step": []},
    {"mechanisms": [{"stage": "model_rerank", "reasons": {"a": "x" * CAP}}]},
])
def test_job_result_with_g6_names_is_one_opaque_unit(payload):
    from g6.metaharness.jobs import Jobs

    payload = {**payload, "padding": "x" * CAP}
    snapshot = copy.deepcopy(payload)
    jobs = Jobs()
    try:
        started = jobs.start(lambda: payload)
    finally:
        jobs.close()
    shown = jobs.status(started["job_id"])
    assert shown["status"] == "completed" and shown["job_id"] == started["job_id"]
    assert shown["result"] == "[withheld for size]"
    assert payload == snapshot


def test_component_output_cannot_declare_itself_a_typed_reply():
    payload = {"ok": True, "completion_state": "qualified-draft",
               "warning_card": {"code": "G6_E_CLOUD_CONSENT_REQUIRED"},
               "mechanisms": [{"stage": "model_rerank", "reasons": {"a": "x" * CAP}}]}
    shown = fit_reply({"ok": True, "result": payload})
    assert shown["result"] == "[withheld for size]"
    assert "G6_E_CLOUD_CONSENT_REQUIRED" not in json.dumps(shown)


def test_fallback_keeps_every_declared_code_card_and_identity():
    from g6.core.reply_budget import SELECTOR

    original = blocked_reply("[G6_E_CLOUD_CONSENT_REQUIRED] Sharing withheld.", request_id="request-1")
    original.update(run_id="run-1", cloud_consent_code="G6_E_CLOUD_CONSENT_REQUIRED",
                    remedy={"code": "G6_E_JOB_FAILED", "call": {"data": "x" * CAP}},
                    repair={"code": "G6_E_JOB_UNKNOWN"},
                    mechanisms=[{"stage": "facts", "facts": {"last_outcome": "G6_E_LLM_DISABLED"}}],
                    payload="x" * CAP)
    shown = fit_reply(original, schema=SELECTOR)
    assert shown["warning_card"] == original["warning_card"]
    assert shown["completion_state"] == original["completion_state"]
    assert shown["request_id"] == "request-1" and shown["run_id"] == "run-1"
    for code in ("G6_E_CLOUD_CONSENT_REQUIRED", "G6_E_JOB_FAILED", "G6_E_JOB_UNKNOWN", "G6_E_LLM_DISABLED"):
        assert code in json.dumps(shown)
    assert "G6_E_INPUT_VALIDATION" not in json.dumps(shown)


def test_minimal_body_too_large_uses_original_code_and_fixed_text():
    original = blocked_reply("[G6_E_CLOUD_CONSENT_REQUIRED] Sharing withheld.")
    original["warning_card"]["message"] = "x" * CAP
    original["run_id"] = "y" * CAP
    shown = fit_reply(original)
    assert shown["warning_card"]["code"] == "G6_E_CLOUD_CONSENT_REQUIRED"
    assert "withheld for size" in json.dumps(shown)
    assert len(json.dumps(shown).encode()) <= CAP


def test_schema_is_producer_declared_and_survives_a_job_snapshot():
    from g6.core.reply_budget import declare_reply, SELECTOR
    from g6.metaharness.jobs import Jobs

    original = declare_reply({"ok": True, "request_id": "nested-request", "run_id": "nested-run",
                              "mechanisms": [{"stage": "model_rerank", "reasons": {"a": "x" * CAP}}]}, SELECTOR)
    jobs = Jobs()
    try:
        started = jobs.start(lambda: original)
    finally:
        jobs.close()
    shown = jobs.status(started["job_id"])
    assert shown["result"]["request_id"] == "nested-request"
    assert shown["result"]["run_id"] == "nested-run"
    assert shown["result"]["mechanisms"][0]["reasons"]["a"] != "x" * CAP


def test_a_stale_transport_ticket_cannot_declare_positions_in_new_data():
    import asyncio
    from types import SimpleNamespace
    from mcp import types
    from mcp.shared.message import SessionMessage
    from g6.core.reply_budget import ReplySchema, SELECTOR
    from g6.mcp import consent_transport

    opaque = {"mechanisms": [{"stage": "model_rerank", "reasons": {"a": "x" * CAP}}]}
    value = {"ok": True, "job_id": "synthetic", "status": "completed", "result": opaque}
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc="2.0", id=1,
        result={"content": [{"type": "text", "text": json.dumps(value)}], "structuredContent": value})))

    class Sink:
        async def send(self, packet):
            self.value = packet.message.root.result["structuredContent"]

    sink = Sink()
    ticket = consent_transport.Ticket((), "openai:codex", frozenset(), "stale-digest",
                                      reply_schema=ReplySchema("job", SELECTOR))
    token = consent_transport._OUTBOUND.set(ticket)
    try:
        asyncio.run(consent_transport._Stream(sink, SimpleNamespace())._egress(packet))
    finally:
        consent_transport._OUTBOUND.reset(token)
    assert sink.value["result"] == "[withheld for size]"


def test_compaction_never_indexes_an_opaque_mapping():
    class Opaque(dict):
        def get(self, key, default=None):
            pytest.fail(f"compaction read an undeclared application key: {key}")

    value = {"ok": True, "result": Opaque(mechanisms=None, model_step=[], padding="x" * CAP)}
    shown = fit_reply(value)
    assert shown["result"] == "[withheld for size]"


def test_host_layout_metadata_is_not_part_of_a_consent_refusal():
    from g6.mcp.cloud_consent import withheld_reply
    from g6.core.reply_budget import SELECTOR

    shown = withheld_reply(metadata={"reply_schema": SELECTOR.to_data(), "request_id": "request-1"})
    assert "reply_schema" not in shown
    assert shown["request_id"] == "request-1"


def test_a_job_with_oversize_controls_keeps_polling_its_completed_status():
    from g6.core import cloud_consent as cloud
    from g6.core.reply_budget import declare_reply
    from g6.metaharness.jobs import Jobs

    value = blocked_reply("[G6_E_CLOUD_CONSENT_REQUIRED] Sharing withheld.")
    value["warning_card"]["message"] = "x" * CAP
    jobs = Jobs()
    try:
        started = jobs.start(lambda: declare_reply(value))
    finally:
        jobs.close()
    # The real MCP poll runs under a source capture, including failed futures.
    with cloud.collect_sources():
        shown = jobs.status(started["job_id"])
    assert shown["status"] == "completed" and shown["job_id"] == started["job_id"]
    assert shown["warning_card"]["code"] == "G6_E_CLOUD_CONSENT_REQUIRED"
    assert len(json.dumps(shown).encode()) <= CAP
