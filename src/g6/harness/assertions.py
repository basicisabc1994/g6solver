# SPDX-License-Identifier: Apache-2.0
"""JSON assertions as existing Check builders; no module loading or bundled scenarios."""
from __future__ import annotations

import json
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from g6.harness import Check

AssertionOp = Literal["contains", "eq", "equals", "falsey", "gt", "gte", "len_gte", "lt", "lte", "ne",
                      "not_contains", "not_equals", "truthy"]
_MISSING = object()
_COMPARISONS = {"eq": operator.eq, "equals": operator.eq, "ne": operator.ne, "not_equals": operator.ne,
                "gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le}


def resolve_field(value: Any, path: str, *, default: Any = None) -> Any:
    """Resolve JSON object keys, list indices and list.count. Never invoke attributes or methods."""
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part, _MISSING)
        elif isinstance(current, list):
            if part == "count":
                current = len(current)
            elif part.isascii() and part.isdigit() and len(part) <= 10 and int(part) < len(current):
                current = current[int(part)]
            else:
                return default
        else:
            return default
        if current is _MISSING:
            return default
    return current


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op in _COMPARISONS:
        return _COMPARISONS[op](actual, expected)
    if op == "truthy":
        return bool(actual)
    if op == "falsey":
        return not bool(actual)
    if op == "len_gte":
        return len(actual) >= expected
    contains = str(expected).casefold() in str(actual).casefold()
    return contains if op == "contains" else not contains


@dataclass(frozen=True)
class _FieldPredicate:
    field: str
    op: str
    expected_json: str

    def __call__(self, output: Any) -> bool:
        try:
            value = json.loads(output) if isinstance(output, str) else output
            actual = resolve_field(value, self.field, default=_MISSING)
            if actual is _MISSING:
                return False
            return _compare(actual, self.op, json.loads(self.expected_json))
        except (TypeError, ValueError, OverflowError):
            return False


class _AssertionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    op: AssertionOp = "eq"
    value: Any = None
    override: StrictBool = False
    known_bad: tuple[str, ...] = ()
    known_good: str | None = None


def check_field(field: str, op: AssertionOp, value: Any = None, *, check_id: str = "",
                known_bad: tuple[str, ...] = (), known_good: str | None = None) -> Check:
    """A caller-authored Check over JSON output; missing fields and malformed comparisons fail."""
    spec = _AssertionSpec(check_id=check_id or "field", field=field, op=op, value=value,
                          known_bad=known_bad, known_good=known_good)
    if any(not p.strip() for p in spec.field.split(".")):
        raise ValueError("G6_E_HARNESS_ASSERTION: a path needs non-empty segments")
    expected = json.dumps(value, sort_keys=True, allow_nan=False)
    return Check(f"{field} {op} {expected}", _FieldPredicate(field, op, expected), check_id=check_id,
                 known_bad=spec.known_bad, known_good=spec.known_good)


def confirm_suite(builtins: Sequence[Check], manifest: Sequence[Mapping[str, Any]]) -> tuple[Check, ...]:
    """Keep all built-ins; any replacement needs an explicit boolean override on that entry.

    This is suite construction, not approval or evidence. No manifest can import a Python module.
    """
    by_id = {c.key: c for c in builtins}
    if len(by_id) != len(builtins):
        raise ValueError("G6_E_HARNESS_SUITE: duplicate built-in check ids")
    seen = set()
    for raw in manifest:
        item = _AssertionSpec.model_validate(raw)
        if item.check_id in seen:
            raise ValueError("G6_E_HARNESS_SUITE: duplicate manifest check ids")
        seen.add(item.check_id)
        if item.check_id in by_id and not item.override:
            raise ValueError(f"G6_E_HARNESS_OVERRIDE: {item.check_id!r} needs explicit override")
        by_id[item.check_id] = check_field(item.field, item.op, item.value, check_id=item.check_id,
                                           known_bad=item.known_bad, known_good=item.known_good)
    if not by_id:
        raise ValueError("G6_E_HARNESS_SUITE: no checks")
    return tuple(by_id.values())
