"""Collect issued controls before pruning; exercise actual rendered MCP bytes."""
import copy
import json
import re

import pytest
from mcp import types
from mcp.shared.message import SessionMessage

from g6.core import reply_budget as budget
from g6.core.door import blocked_reply
from g6.mcp.reply_egress import budget_packet
from g6.metaharness.jobs import Jobs


CODES = ("G6_E_CLOUD_CONSENT_REQUIRED", "G6_E_JOB_FAILED", "G6_E_LLM_DISABLED")
CAP = budget.MAX_REPLY_BYTES


def packet(value, **extra):
    return SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=1, result={"content": [{"type": "text", "text": json.dumps(value)}],
                                     "structuredContent": value, **extra})))


def sent(value, schema, **extra):
    wire = budget_packet(packet(value, **extra), schema=schema).message.root.result
    assert len(json.dumps(wire).encode()) <= CAP
    assert json.loads(wire["content"][0]["text"]) == wire["structuredContent"]
    return wire["structuredContent"]


def nested(value, depth, schema=budget.ENVELOPE):
    for index in range(depth):
        value = {"ok": True, "job_id": f"job-{index}", "status": "completed", "result": value}
        schema = budget.ReplySchema("job", schema)
    return value, schema


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("depth", range(1, 33))
def test_depth_1_to_32_keeps_leaf_controls_at_egress(depth, code):
    leaf = blocked_reply(f"[{code}] Synthetic refusal.", request_id="leaf-request")
    leaf.update(run_id="leaf-run", payload="x" * CAP)
    value, schema = nested(leaf, depth)
    before = copy.deepcopy(value)
    shown = sent(value, schema)
    encoded = json.dumps(shown)
    assert set(re.findall(r"G6_E_[A-Z0-9_]+", encoded)) == {code}
    current = shown
    for index in reversed(range(depth)):
        assert current["job_id"] == f"job-{index}"
        assert current["status"] == "completed"
        current = current["result"]
    assert current["warning_card"] == leaf["warning_card"]
    assert current["request_id"] == "leaf-request" and current["run_id"] == "leaf-run"
    assert value == before


@pytest.mark.parametrize("depth", [16, 17, 32])
def test_real_completed_jobs_retain_leaf_controls(depth):
    leaf = budget.declare_reply(blocked_reply(f"[{CODES[0]}] Synthetic refusal.", request_id="leaf-request"))
    leaf.update(run_id="leaf-run", payload="x" * 160000)
    value = leaf
    for _ in range(depth):
        jobs = Jobs()
        try:
            start = jobs.start(lambda: value)
        finally:
            jobs.close()
        value = jobs.status(start["job_id"])
        assert value["status"] == "completed"
    shown = sent(value, budget.schema_of(value))
    encoded = json.dumps(shown)
    assert CODES[0] in encoded and "leaf-request" in encoded and "leaf-run" in encoded
    assert "G6_E_INPUT_VALIDATION" not in encoded


def test_real_mcp_completed_job_retains_leaf_controls():
    import asyncio
    from fastmcp import Client
    from g6.mcp import build_server
    from g6.mcp.cloud_consent import public_tool

    value = budget.declare_reply(blocked_reply(f"[{CODES[0]}] Synthetic refusal.", request_id="leaf-request"))
    value.update(run_id="leaf-run", payload="x" * 160000)
    for _ in range(17):
        jobs = Jobs()
        try:
            start = jobs.start(lambda: value)
        finally:
            jobs.close()
        value = jobs.status(start["job_id"])
    server = build_server()

    @server.tool()
    @public_tool
    def synthetic_completed_job() -> dict:
        return value

    async def call():
        async with Client(server) as client:
            return await client.call_tool("synthetic_completed_job", {}, raise_on_error=False)

    result = asyncio.run(call())
    shown = result.structured_content
    assert json.loads(result.content[0].text) == shown
    encoded = json.dumps(shown)
    assert CODES[0] in encoded and "leaf-request" in encoded and "leaf-run" in encoded
    assert "G6_E_INPUT_VALIDATION" not in encoded


def test_no_issued_code_uses_size_withholding_instead_of_input_validation():
    shown = sent({"payload": "x" * CAP}, budget.ENVELOPE)
    assert shown["warning_card"]["code"] == budget.REPLY_WITHHELD
    assert "G6_E_INPUT_VALIDATION" not in json.dumps(shown)


def test_projection_deduplicates_codes_and_lists_counts_before_dropping_diagnostics():
    codes = [f"G6_E_{index}" for index in range(600)]
    leaf = {"request_id": "leaf-request", "run_id": "leaf-run", "mechanisms": [
        {"stage": "facts", "facts": {"last_outcome": code}} for code in codes for _ in range(3)]}
    value, schema = nested(leaf, 1, budget.SELECTOR)
    projection = budget.result_control(budget.declare_reply(value, schema))
    assert len(json.dumps(projection).encode()) <= budget.CONTROL_MAX_BYTES
    assert projection["issued_codes"] == [{"code": code, "count": 3} for code in codes]
    assert projection["request_id"] == "leaf-request" and projection["run_id"] == "leaf-run"
    published = {"ok": True, "job_id": "outer", "status": "completed", "result": budget.WITHHELD,
                 "result_control": projection, "payload": "x" * CAP}
    encoded = json.dumps(sent(published, budget.JOB))
    assert all(code in encoded for code in codes)
    assert "G6_E_INPUT_VALIDATION" not in encoded


def test_projection_target_cannot_discard_survivors_that_fit_reply_budget():
    codes = ["G6_E_" + str(index).zfill(50) for index in range(900)]
    leaf = {"mechanisms": [{"stage": "facts", "facts": {"last_outcome": code}} for code in codes]}
    value, schema = nested(leaf, 1, budget.SELECTOR)
    projection = budget.result_control(budget.declare_reply(value, schema))
    published = {"result_control": projection, "payload": "x" * CAP}
    encoded = json.dumps(sent(published, budget.JOB))
    assert all(code in encoded for code in codes)


def test_projected_codes_survive_another_job_snapshot_and_compaction():
    codes = [f"G6_E_{index}" for index in range(600)]
    value = {"request_id": "leaf-request", "run_id": "leaf-run", "payload": "x" * CAP,
             "mechanisms": [{"stage": "facts", "facts": {"last_outcome": code}} for code in codes]}
    compacted = budget.fit_reply(value, schema=budget.SELECTOR)
    jobs = Jobs()
    try:
        start = jobs.start(lambda: compacted)
    finally:
        jobs.close()
    value = jobs.status(start["job_id"])
    value["payload"] = "x" * CAP
    shown = sent(value, budget.schema_of(value))
    assert all(code in json.dumps(shown) for code in codes)
    assert "leaf-request" in json.dumps(shown) and "leaf-run" in json.dumps(shown)


@pytest.mark.parametrize("count", [0, -1, True, None, budget.CONTROL_COUNT_MAX + 1, "x" * CAP],
                         ids=["zero", "negative", "boolean", "null", "over-max", "oversize-text"])
def test_summary_count_validation_cannot_discard_an_issued_code(count):
    published = {"result_control": {"issued_codes": [{"code": CODES[0], "count": count}]},
                 "payload": "x" * CAP}
    shown = sent(published, budget.JOB)
    assert CODES[0] in json.dumps(shown)
    assert shown["result"]["issued_codes"] == [{"code": CODES[0], "count": budget.WITHHELD}]


def test_summary_count_overflow_is_withheld_without_losing_the_code():
    controls = {"issued_codes": [{"code": CODES[0], "count": budget.CONTROL_COUNT_MAX}],
                "mechanisms": [{"stage": "facts", "facts": {"last_outcome": CODES[0]}} for _ in range(600)]}
    value, schema = nested(controls, 1, budget.ReplySchema("controls"))
    projection = budget.result_control(budget.declare_reply(value, schema))
    assert projection["issued_codes"] == [{"code": CODES[0], "count": budget.WITHHELD}]


def test_many_opaque_markers_cannot_displace_collected_controls():
    leaf = blocked_reply(f"[{CODES[0]}] Synthetic refusal.", request_id="leaf-request")
    leaf["run_id"] = "leaf-run"
    value, schema = nested(leaf, 17)
    value.update({f"opaque-{index}": "x" * 20 for index in range(10000)})
    shown = sent(value, schema)
    current = shown
    for _ in range(17):
        current = current["result"]
    assert current["warning_card"] == leaf["warning_card"]
    assert current["request_id"] == "leaf-request" and current["run_id"] == "leaf-run"


@pytest.mark.parametrize("path", ["normal", "last_resort", "final_guard", "exception"])
def test_every_compaction_path_uses_innermost_issued_code(monkeypatch, path):
    import g6.mcp.reply_egress as egress

    value, schema = nested({"code": CODES[0], "request_id": "leaf-request", "run_id": "leaf-run",
                            "payload": "x" * CAP}, 20)
    value["code"] = CODES[1]
    if path == "last_resort":
        # Bounded cards together overflow the actual duplicate MCP rendering.
        current = value
        for _ in range(20):
            current["warning_card"] = {"code": CODES[1], "message": "\U0001f642" * 650}
            current = current["result"]
    elif path == "final_guard":
        monkeypatch.setattr(egress, "fit_reply", lambda original, **kwargs: original)
    elif path == "exception":
        def fail(*args, **kwargs):
            raise ValueError("synthetic compactor failure")
        monkeypatch.setattr(egress, "fit_reply", fail)
    shown = sent(value, schema)
    assert CODES[0] in json.dumps(shown)
    assert set(re.findall(r"G6_E_[A-Z0-9_]+", json.dumps(shown))) <= set(CODES[:2])
    if path in ("last_resort", "final_guard"):
        assert shown["warning_card"]["code"] == CODES[0]
        assert "rest were withheld for size" in shown["size_notice"]
    if path == "exception":
        assert "leaf-request" in json.dumps(shown) and "leaf-run" in json.dumps(shown)


def test_preliminary_shortening_keeps_bounded_identity_and_issued_outcome():
    code = "G6_E_" + "A" * 64
    value = {"request_id": "r" * 200, "run_id": "s" * 200,
             "remedy": {"key": "unknown_code", "code": code},
             "mechanisms": [{"stage": "facts", "facts": {"last_outcome": code}}]}
    # Old shortening could make this fit by truncating identities and real codes.
    limit = len(json.dumps(value).encode()) - 100
    shown = budget.fit_reply(value, schema=budget.SELECTOR, limit=limit)
    assert code in json.dumps(shown)
    assert not any(shown.get(key, "").endswith(budget.TRUNCATED) for key in ("request_id", "run_id"))


def test_bounded_controls_cannot_lose_code_when_other_fields_are_withheld():
    leaf = {"code": CODES[0], "request_id": "r" * 257, "run_id": "s" * 257,
            "warning_card": {"code": CODES[0], "message": "x" * 2049}, "payload": "x" * CAP}
    value, schema = nested(leaf, 32)
    encoded = json.dumps(sent(value, schema))
    assert CODES[0] in encoded
    assert "r" * 257 not in encoded and "s" * 257 not in encoded
    assert "x" * 2049 not in encoded


def test_opaque_result_control_names_do_not_issue_codes():
    payload = {"result_control": {"code": CODES[0]}, "issued_codes": [{"code": CODES[1], "count": 1}],
               "mechanisms": [{"facts": {"last_outcome": CODES[2]}}], "padding": "x" * CAP}
    shown = sent({"code": "G6_E_JOB_UNKNOWN", "result": payload}, budget.ENVELOPE)
    assert shown["result"] == budget.WITHHELD
    assert not any(code in json.dumps(shown) for code in CODES)
