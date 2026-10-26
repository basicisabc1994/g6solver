# SPDX-License-Identifier: Apache-2.0
"""GatedCalls: how a framework adapter calls a tool, each call decided first by the run's ``ToolGate``.

A framework (LangGraph, AutoGen, smolagents) asks for a tool by name, with arguments. ``call`` asks the gate; on any
verdict but ``run`` it returns the verdict, its code and the reason, and nothing runs, so the framework (and its model)
sees the refusal as the tool's result and nothing is raised. On ``run`` the tool is a Python callable passed in for
that name, or a G6 component's op named ``component.op``, called through the registry's door for untrusted callers;
the component running the agent may not be called by it. What the call came to is
``{"verdict", "code", "reason" | "result" | "error"}``; ``ran`` holds the ids of the calls that ran, for the run's
``tool_result`` events.

What a G6 component's op does comes from that component's own declarations, never from the effect the caller declared
for it (which can only make it stricter): an op in its ``__g6_sensitive_ops__`` is refused before the gate is asked,
and an op not in its ``__g6_read_only_ops__`` counts as changing something, so the gate treats it as outward and a dry
run does not run it (``ToolGate.decide(call, changes=True)``). A dry run therefore runs only the ops a component declares
read-only; a component that declares none, or is not known, has none.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from g6.agents._runtime import codes
from g6.agents._runtime.gate import ToolGate
from g6.agents._runtime.model import ToolCall

_CODE = re.compile(r"G6_E_[A-Z0-9_]+")
#: The registry's code for a sensitive op refused on a surface for untrusted callers.
SENSITIVE_DENIED = "G6_E_SENSITIVE_COMPONENT_DENIED"


def declared(component: str, op: str) -> tuple[bool, bool]:
    """``(sensitive, read_only)`` for ``component.op``, as the component itself declares them; a component that is not
    known, or does not import, is neither (so its ops count as changing something)."""
    from g6.core.registry import get_registry

    try:
        module = get_registry().get_module(component)
    except Exception:  # noqa: BLE001 - unknown or broken: not read-only (fail closed); the door answers for it
        return False, False
    return (op in tuple(getattr(module, "__g6_sensitive_ops__", ()) or ()),
            op in tuple(getattr(module, "__g6_read_only_ops__", ()) or ()))


class GatedCalls:
    """See the module docstring. ``own`` is the registry name of the component running the agent."""

    def __init__(self, gate: ToolGate, tools: Mapping[str, Callable[..., Any]] | None = None, *, own: str = "") -> None:
        self.gate, self.tools, self.own = gate, dict(tools or {}), own
        self.ran: list[str] = []

    def call(self, name: str, arguments: Mapping[str, Any] | None = None, paths: Sequence[str] = (),
             fn: Callable[..., Any] | None = None) -> dict[str, Any]:
        """Decide, then run, one call; see the module docstring. ``fn`` is the tool itself, when the framework holds
        it rather than G6."""
        arguments = dict(arguments or {})
        call = ToolCall(tool=name, arguments=arguments, paths=tuple(str(p) for p in paths))
        tool = fn or self.tools.get(name)
        component, _, op = name.partition(".")
        changes = False
        if tool is None and op:   # a G6 component's op: what it does is its component's word, not the caller's
            sensitive, read_only = declared(component, op)
            if sensitive:
                return {"verdict": "refused", "code": SENSITIVE_DENIED,
                        "reason": f"{name} is one of {component}'s sensitive ops, which an agent may not call"}
            changes = not read_only
        decision = self.gate.decide(call, changes=changes)
        if not decision.runs:
            return {"verdict": decision.verdict, "code": decision.code, "reason": decision.reason}
        if tool is None and self.own and component == self.own:
            return {"verdict": "refused", "code": codes.SELF_CALL,
                    "reason": f"an agent run by {self.own} may not call {self.own} itself"}
        try:
            if tool is not None:
                result = tool(**arguments)
            elif op:
                from g6.core.registry import get_registry

                answered = get_registry().invoke(component, op, arguments, block_sensitive=True)
                if answered.__class__.__name__ != "Success":
                    said = str(answered.failure())
                    found = _CODE.search(said)
                    return {"verdict": "refused", "code": found.group(0) if found else codes.TOOL_FAILED,
                            "reason": said[:500]}
                result = answered.unwrap()
            else:
                return {"verdict": "refused", "code": codes.TOOL_FAILED,
                        "reason": f"{name} is declared, but no tool of that name was passed in"}
        except Exception as exc:  # noqa: BLE001 - a tool's error is its result, not the end of the run
            self.ran.append(call.call_id)
            return {"verdict": "run", "code": codes.TOOL_FAILED, "error": f"{type(exc).__name__}: {exc}"[:500]}
        self.ran.append(call.call_id)
        return {"verdict": "run", "code": codes.TOOL_ALLOWED, "result": result}

    def take(self) -> list[str]:
        """The ids of the calls that ran since the last ``take``."""
        done, self.ran = self.ran, []
        return done


__all__ = ["SENSITIVE_DENIED", "GatedCalls", "declared"]
