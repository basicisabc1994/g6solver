# SPDX-License-Identifier: Apache-2.0
"""Small shared boundaries for integration replies and JSON values."""
from __future__ import annotations

import math
from typing import Any

from g6.core.resources import RESOURCE_LIMIT
from g6.core.result import Result


class Refusal(ValueError):
    def __init__(self, code: str, reason: str, action: str = "Correct the input or ask the operator.") -> None:
        super().__init__(reason)
        self.code, self.reason, self.action = code, reason, action


def json_value(value: Any, max_bytes: int = 65_536) -> Any:
    """Validate with cumulative node/string/encoded-size budgets before serialising."""
    nodes = strings = size = 0
    max_nodes = min(4096, max_bytes)

    def charge(amount: int) -> None:
        nonlocal size
        size += amount
        if size > max_bytes:
            raise Refusal(RESOURCE_LIMIT, "JSON exceeds the byte limit")

    def visit(item: Any, depth: int = 0) -> None:
        nonlocal nodes, strings
        nodes += 1
        if nodes > max_nodes:
            raise Refusal(RESOURCE_LIMIT, "JSON exceeds the node limit")
        if depth > 20:
            raise ValueError("JSON nesting exceeds 20")
        if type(item) is str:
            strings += len(item)
            if strings > max_bytes:
                raise Refusal(RESOURCE_LIMIT, "JSON exceeds the cumulative string limit")
            charge(2)
            for char in item:
                # json.dumps defaults: ASCII escapes, including surrogate pairs.
                code = ord(char)
                charge(2 if char in '\"\\\b\f\n\r\t' else
                       1 if 32 <= code < 127 else 6 if code <= 0xffff else 12)
        elif item is None or type(item) is bool:
            charge(4 if item is None or item is True else 5)
        elif type(item) is int:
            if item.bit_length() > min(4096, max_bytes * 3):
                raise Refusal(RESOURCE_LIMIT, "JSON integer exceeds the size budget")
            charge(len(str(item)))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
            charge(len(repr(item)))
        elif type(item) in (list, dict):
            children = len(item) * (2 if type(item) is dict else 1)
            if nodes + children > max_nodes:
                raise Refusal(RESOURCE_LIMIT, "JSON exceeds the node limit")
            charge(2 + max(0, len(item) - 1) * 2)
            if type(item) is dict:
                charge(len(item) * 2)
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("JSON object keys must be strings")
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
        else:
            raise ValueError("only JSON primitives, arrays and string-keyed objects are accepted")

    visit(value)

    return value


def reply(component: str, data: Any, *, code: str, message: str, blocked: bool = False,
          action: str = "Treat returned values as reports; check them independently before relying on them.",
          **fields: Any) -> Result[dict]:
    observed = {key: fields[key] for key in ("job_id", "status", "queue_reports_finished", "outcomes",
                                            "basis", "gate") if key in fields}
    return Result.ok({**fields, "component": component, "op": data.op, "code": code, "reason": message,
                      "completion_state": "blocked-escalated" if blocked else "qualified-draft",
                      "warning_card": {"code": code, "message": message,
                                       "severity": "blocked" if blocked else "amber", "suggested_action": action},
                      "evidence": [{"source": component, "operation": data.op, "code": code, "summary": message, **observed}],
                      "request_id": data.request_id, "task_id": getattr(data, "task_id", ""), "run_id": data.run_id})
