# SPDX-License-Identifier: Apache-2.0
"""Immutable checker and origin records shared by local adapters."""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass


class TaskError(ValueError):
    """A stable task-supply refusal code, without source contents."""

    @property
    def code(self) -> str:
        return str(self)


def content_digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Provenance:
    source: str
    licence_id: str
    original_id: str
    content_digest: str


@dataclass(frozen=True)
class CheckSpec:
    kind: str
    tests: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        values = (self.tests, self.inputs, self.outputs)
        if any(type(v) is not tuple or any(type(s) is not str for s in v) for v in values):
            raise TaskError("G6_E_RSI_CHECK_INVALID")
        if self.kind == "function":
            if not self.tests or self.inputs or self.outputs:
                raise TaskError("G6_E_RSI_CHECK_INVALID")
            try:
                for text in self.tests:
                    tree = ast.parse(text)
                    if not tree.body or not any(isinstance(node, ast.Assert) for node in ast.walk(tree)):
                        raise ValueError
                    compile(tree, "<task-check>", "exec")
            except (ValueError, SyntaxError, TypeError):
                raise TaskError("G6_E_RSI_CHECK_INVALID") from None
        elif self.kind == "stdin_stdout":
            if self.tests or not self.inputs or len(self.inputs) != len(self.outputs):
                raise TaskError("G6_E_RSI_CHECK_INVALID")
        else:
            raise TaskError("G6_E_RSI_CHECK_INVALID")

    def to_record(self) -> dict:
        return {"kind": self.kind, "tests": list(self.tests), "inputs": list(self.inputs), "outputs": list(self.outputs)}

    @classmethod
    def from_record(cls, record: dict) -> CheckSpec:
        if type(record) is not dict or set(record) != {"kind", "tests", "inputs", "outputs"}:
            raise TaskError("G6_E_RSI_RECORD_INVALID")
        if any(type(record[k]) is not list for k in ("tests", "inputs", "outputs")):
            raise TaskError("G6_E_RSI_RECORD_INVALID")
        return cls(record["kind"], tuple(record["tests"]), tuple(record["inputs"]), tuple(record["outputs"]))
