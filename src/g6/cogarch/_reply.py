# SPDX-License-Identifier: Apache-2.0
"""The reply every cogarch op returns: its own fields inside the envelope (``completion_state``, ``warning_card``,
``evidence``, ``request_id``, ``run_id``). ``_``-prefixed, so it never registers as a component.

:func:`answer` is a ``qualified-draft``, the best a cognitive architecture says: each member re-checks its outcome with
something the architecture does not control (a plan replayed, a goal test, regret against a random policy), and even
then the check is against the model the caller declared, not the world. :func:`refuse` is ``blocked-escalated`` with a
stable ``G6_E_*`` code and the reason in words.
"""
from __future__ import annotations

import re
from typing import Any

from g6.core.reliability_envelope import WarningCard

_CODE = re.compile(r"G6_E_[A-Z0-9_]+")


def _code(code: str) -> str:
    if not _CODE.fullmatch(code):
        raise ValueError(f"a cogarch reply carries a stable G6_E_* code; got {code!r}")
    return code


def answer(component: str, op: str, code: str, message: str, *, action: str, request_id: str = "", run_id: str = "",
           evidence: list[dict[str, Any]] | None = None, **fields: Any) -> dict[str, Any]:
    """A ``qualified-draft`` reply with its code, its message and what to do next."""
    card = WarningCard(code=_code(code), message=message, suggested_action=action, severity="amber").model_dump()
    return {"component": component, "op": op, **fields, "code": code, "completion_state": "qualified-draft",
            "warning_card": card, "evidence": list(evidence or []), "request_id": request_id, "run_id": run_id}


def refuse(component: str, op: str, code: str, message: str, *, action: str, request_id: str = "", run_id: str = "",
           evidence: list[dict[str, Any]] | None = None, **fields: Any) -> dict[str, Any]:
    """A ``blocked-escalated`` reply with its stable code and the reason in words."""
    card = WarningCard(code=_code(code), message=message, suggested_action=action, severity="blocked").model_dump()
    return {"component": component, "op": op, **fields, "code": code, "completion_state": "blocked-escalated",
            "warning_card": card, "evidence": [*(evidence or []), {"kind": "refusal", "code": code, "reason": message}],
            "request_id": request_id, "run_id": run_id}


__all__ = ["answer", "refuse"]
