# SPDX-License-Identifier: Apache-2.0
"""The final size check, after admission and every MCP reply transformation."""
from __future__ import annotations

import json

from mcp import types
from mcp.shared.message import SessionMessage

from g6.core.reply_budget import MAX_REPLY_BYTES, fit_reply, oversize_refusal


def budget_packet(packet):
    root = packet.message.root
    if not isinstance(root, types.JSONRPCResponse) or "content" not in root.result:
        return packet
    if len(json.dumps(root.result).encode("utf-8")) <= MAX_REPLY_BYTES:
        return packet

    def render(value):
        # Retain protocol error status and metadata, while text and structure
        # are rendered from the same final value with the same JSON escaping.
        return {**root.result, "content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value}

    try:
        value = root.result.get("structuredContent")
        if value is None:
            content = root.result["content"]
            value = json.loads(content[0]["text"]) if len(content) == 1 else oversize_refusal()
        value = fit_reply(value, measure=lambda reply: len(json.dumps(render(reply)).encode("utf-8")))
        result = render(value)
        if len(json.dumps(result).encode("utf-8")) > MAX_REPLY_BYTES:
            # An oversized protocol metadata field cannot make the fixed refusal grow.
            refused = oversize_refusal(value)
            result = {"content": [{"type": "text", "text": json.dumps(refused)}],
                      "structuredContent": refused, "isError": True}
    except (ValueError, TypeError, KeyError, RecursionError, MemoryError):
        refused = oversize_refusal()
        result = {"content": [{"type": "text", "text": json.dumps(refused)}],
                  "structuredContent": refused, "isError": bool(root.result.get("isError"))}
    return SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=root.id, result=result)), metadata=packet.metadata)
