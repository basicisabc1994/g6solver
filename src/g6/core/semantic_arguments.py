# SPDX-License-Identifier: Apache-2.0
"""Pure argument checks shared by semantic Python and MCP entry points.

These checks must precede source declarations: declaring a repository source
resolves its path. No caller value is included in a validation message.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_DEFAULTS = {
    "g6_select": {"task": "", "phase": "", "last_outcome": "", "limit": 5, "rerank": "auto"},
    "g6_premortem": {"repo": None, "task": "", "intent": None, "hypotheses": None, "propose": "auto"},
}
_CHECKED: ContextVar[tuple | None] = ContextVar("g6_semantic_arguments", default=None)


def _normalized(tool, arguments):
    # bool/int and int/float compare equal in Python; strict JSON types must
    # remain distinct when a nested call tries to reuse a checked argument.
    return {key: (type(value), value) for key, default in _DEFAULTS.get(tool, {}).items()
            for value in (arguments.get(key, default),)}


@contextmanager
def checked_arguments(tool, arguments):
    """Reuse only this request's exact checked values, including omitted defaults.

    Middleware enters after validation, before source declaration. This is an
    argument check receipt, never a consent/source admission receipt.
    """
    token = _CHECKED.set((tool, deepcopy(_normalized(tool, arguments))))
    try:
        yield
    finally:
        _CHECKED.reset(token)


def argument_error(tool, arguments):
    checked = _CHECKED.get()
    if checked is not None and checked == (tool, _normalized(tool, arguments)):
        return None
    return validate_arguments(tool, arguments)


def validate_arguments(tool: str, arguments: dict) -> str | None:
    if tool not in (*_DEFAULTS, "g6_premortem_proposals"):
        return None
    field = "rerank" if tool == "g6_select" else "propose"
    mode = arguments.get(field, "auto")
    if field == "propose":
        if type(mode) is not bool and (type(mode) is not str or mode not in ("auto", "true", "false")):
            return "propose must be auto, true or false (strings or JSON booleans)"
    elif type(mode) is not str or mode not in ("auto", "model", "lexical", "colbert"):
        return "rerank is auto, model or lexical; colbert is not offered"
    task = arguments.get("task", "")
    if type(task) is not str or not task.strip():
        return "task must say what the change is for"
    if tool == "g6_select":
        if type(arguments.get("phase", "")) is not str or arguments.get("phase", "") not in (
                "", "plan", "implement", "verify", "diagnose", "stuck"):
            return "phase is plan, implement, verify, diagnose, stuck, or empty"
        if type(arguments.get("limit", 5)) is not int:
            return "limit is a whole number"
        if type(arguments.get("last_outcome", "")) is not str:
            return "last_outcome must be text"
    else:
        if tool == "g6_premortem":
            repo = arguments.get("repo")
            if type(repo) is not str or not repo.strip():
                return "repo must be a repository path"
        if arguments.get("intent") is not None and type(arguments["intent"]) is not dict:
            return "intent must be an object"
        hypotheses = arguments.get("hypotheses")
        if hypotheses is not None and (type(hypotheses) is not list or len(hypotheses) > 32):
            return "hypotheses must be a list of at most 32 entries"
    return None
