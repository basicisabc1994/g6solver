# SPDX-License-Identifier: Apache-2.0
"""Shared refusals every component can return the same way.

``unknown_op(component, op, valid_ops)`` is the one answer for an op a component does not have: a failed
:class:`~g6.core.result.Result` whose error starts ``[G6_E_UNKNOWN_OP]`` (the form ``g6_invoke`` reads a code from),
names the nearest op when one is close, and otherwise lists the ops in order.
"""
from __future__ import annotations

from collections.abc import Iterable
from difflib import get_close_matches

from g6.core.result import Result

UNKNOWN_OP = "G6_E_UNKNOWN_OP"
#: Untrusted code was not run: no operating-system backend can confine it here, and the person did not allow it to run
#: without one, or ``G6_REQUIRE_ISOLATION`` asks for one whatever was allowed (``g6.core.confine``).
ISOLATION_UNAVAILABLE = "G6_E_ISOLATION_UNAVAILABLE"
#: What a card that carries it suggests, the same words wherever it is said.
ISOLATION_ACTION = ("Run g6 doctor: it says what this computer needs for G6's operating-system boundary to confine "
                    "the run. Then run it again. To run such code without a boundary, with your own rights, the person "
                    "at this computer chooses it once with g6 setup.")
#: A block's ``screen_raw`` hook raised, or answered with something that is not a Result: the call is refused.
SCREEN_RAW_FAILED = "G6_E_SCREEN_RAW_FAILED"
_LISTED = 8


def unknown_op(component: str, op: str, valid_ops: Iterable[str]) -> Result:
    """The refusal for ``op``, which ``component`` does not have."""
    ops = sorted(set(valid_ops))
    close = get_close_matches(op, ops, n=1, cutoff=0.5)
    if close:
        hint = f"did you mean {close[0]!r}?"
    elif ops:
        more = f" and {len(ops) - _LISTED} more" if len(ops) > _LISTED else ""
        hint = f"its ops are {', '.join(ops[:_LISTED])}{more}"
    else:
        hint = "it has no ops"
    return Result.fail(f"[{UNKNOWN_OP}] {component} has no op {op!r}; {hint}")


__all__ = ["ISOLATION_ACTION", "ISOLATION_UNAVAILABLE", "SCREEN_RAW_FAILED", "UNKNOWN_OP", "unknown_op"]
