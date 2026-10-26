# SPDX-License-Identifier: Apache-2.0
"""An outside program's refusal keeps its code over MCP (review A6).

``g6_invoke`` reads a failure's code from a leading ``[G6_E_...]``; ``require()`` and ``ServiceError`` said
``G6_E_...: message``, so a component passing either through reached the person as ``G6_E_INVOKE_FAILED``. Checked
through ``g6.mcp.invoke_reply`` and the real registry, with this module as the one component it finds. Offline.
"""
from __future__ import annotations

import pytest

from g6.core import outside
from g6.core.outside import OutsideProgram, ServiceError, require
from g6.core.registry import ComponentRef, ComponentRegistry
from g6.core.result import Result
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

_TOOL = OutsideProgram("Tool", "runs the tool", "pip install tool", "MIT", command="tool")
_TIMEOUT = "G6_E_OUTSIDE_SERVICE_TIMEOUT"
_SAID = "Test service did not finish answering GET / within 1 seconds"
_SLOW = ServiceError(_TIMEOUT, _SAID)

__g6_block__ = "_Block"  # this module is the component ``_OneComponent`` finds


class _Block:
    def infer(self, data):  # noqa: ANN001, ANN201 - untyped: the registry passes the parameters as they are
        if data["op"] == "require":
            return require(_TOOL, which=lambda name: None)
        if data["op"] == "as_failure":
            return _SLOW.as_failure()
        return Result.fail(str(_SLOW))  # a component that passes the exception's text on


class _OneComponent(ComponentRegistry):
    def _discover(self) -> dict[str, ComponentRef]:
        return {"demo": ComponentRef("demo", __name__, "extension")}


@pytest.mark.parametrize(("op", "code"), [("require", outside.OUTSIDE_PROGRAM_MISSING), ("as_failure", _TIMEOUT),
                                          ("str", _TIMEOUT)])
def test_the_card_shows_the_outside_programs_own_code(op: str, code: str) -> None:
    found = invoke_reply("demo", op, "{}", registry=_OneComponent(), allow_sensitive=frozenset())
    assert check_envelope(found) == [], found
    assert found["ok"] is False and found["completion_state"] == "blocked-escalated"
    assert found["warning_card"]["code"] == code, found["warning_card"]
    assert not found["warning_card"]["message"].startswith("[")  # the code is read off, the reason stays


def test_a_service_error_keeps_its_parts() -> None:
    assert outside.SERVICE_TIMEOUT == _TIMEOUT and outside.SERVICE_FAILED == "G6_E_OUTSIDE_SERVICE_FAILED"
    assert (_SLOW.code, _SLOW.message, _SLOW.status) == (_TIMEOUT, _SAID, None)
    assert str(_SLOW) == f"[{_TIMEOUT}] {_SAID}" == _SLOW.as_failure().error
