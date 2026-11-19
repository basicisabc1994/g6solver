# SPDX-License-Identifier: Apache-2.0
"""The final size check, after admission and every MCP reply transformation."""
from __future__ import annotations

import json

from mcp import types
from mcp.shared.message import SessionMessage

from g6.core.reply_budget import MAX_REPLY_BYTES, fit_reply, fixed_refusal, oversize_refusal, reply_code, WITHHELD


def budget_packet(packet, *, schema=None):
    root = packet.message.root
    if not isinstance(root, types.JSONRPCResponse) or "content" not in root.result:
        return packet
    if len(json.dumps(root.result).encode("utf-8")) <= MAX_REPLY_BYTES:
        return packet

    def render(value, *, lean=False):
        # Retain protocol error status and metadata, while text and structure
        # are rendered from the same final value with the same JSON escaping.
        metadata = {"isError": bool(root.result.get("isError"))} if lean else root.result
        return {**metadata, "content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value}

    original = None
    try:
        value = root.result.get("structuredContent")
        if value is None:
            content = root.result["content"]
            value = json.loads(content[0]["text"]) if len(content) == 1 else oversize_refusal()
        original = value
        value = fit_reply(value, schema=schema, refuse=False,
                          measure=lambda reply: len(json.dumps(render(reply)).encode("utf-8")))
        result = render(value)
        if len(json.dumps(result).encode("utf-8")) > MAX_REPLY_BYTES:
            # Retry from the ORIGINAL value: a failed metadata compaction must
            # not become the source of a lossy control projection.
            def measure(reply):
                return len(json.dumps(render(reply, lean=True)).encode("utf-8"))
            value = fit_reply(original, schema=schema, measure=measure)
            if measure(original) <= MAX_REPLY_BYTES and isinstance(original, dict):
                # Only protocol metadata was too large. Keep opaque units whole.
                value = {**oversize_refusal(original, schema=schema, measure=measure), **original,
                         "ok": False, "size_notice": WITHHELD}
                if measure(value) > MAX_REPLY_BYTES:
                    value = fit_reply(original, schema=schema, measure=measure)
            result = render(value, lean=True)
            result["isError"] = True
    except (ValueError, TypeError, KeyError, RecursionError, MemoryError):
        # A rendering/shortening failure cannot turn a real issued refusal into
        # input validation. Code selection uses the original declared reply.
        refused = oversize_refusal(original, schema=schema, measure=lambda reply:
                                  len(json.dumps(render(reply, lean=True)).encode("utf-8")))
        result = {"content": [{"type": "text", "text": json.dumps(refused)}],
                  "structuredContent": refused, "isError": bool(root.result.get("isError"))}
    # Check the bytes that will actually be handed off, after every mutation,
    # including exception/last-resort paths. The code-only template is bounded
    # independently of compaction, component values and protocol metadata.
    if len(json.dumps(result).encode("utf-8")) > MAX_REPLY_BYTES:
        result = render(fixed_refusal(reply_code(original, schema=schema)), lean=True)
        result["isError"] = True
    return SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=root.id, result=result)), metadata=packet.metadata)
