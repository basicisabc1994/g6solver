# SPDX-License-Identifier: Apache-2.0
"""What every code extension shares: the reply each op returns, and the refusal an op raises inside itself.

``_``-prefixed, so it never registers as a component (``g6.core.registry``). Pure Python.

:func:`answer` is a ``qualified-draft``: the best a code op says of its own work. A scaffold, an edit, a repair or a
reading of a repository is a draft until checks the op cannot influence (the package's own tests, a person's review)
say otherwise; any check the op ran is named in its evidence with what it would have caught. :func:`refuse` is
``blocked-escalated`` with a stable ``G6_E_*`` code and the reason in words. Both carry the cross-surface envelope
(``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``). :class:`CodeRefused` is the
exception an op raises where it cannot go on; the block turns it into :func:`refuse`.
"""
from __future__ import annotations

import re
from typing import Any

from g6.core.reliability_envelope import WarningCard

#: A requested checker cannot run because its Python package is absent.
CHECKER_NOT_INSTALLED = "G6_E_CODE_CHECKER_NOT_INSTALLED"

_CODE = re.compile(r"G6_E_[A-Z0-9_]+")


def _code(code: str) -> str:
    if not _CODE.fullmatch(code):
        raise ValueError(f"a code reply carries a stable G6_E_* code; got {code!r}")
    return code


class CodeRefused(Exception):
    """An op that cannot go on, with its stable code, the reason in words and any evidence gathered so far."""

    def __init__(self, code: str, reason: str, evidence: list[dict[str, Any]] | None = None,
                 **fields: Any) -> None:
        super().__init__(f"[{_code(code)}] {reason}")
        self.code, self.reason, self.evidence, self.fields = code, reason, list(evidence or []), fields


def answer(component: str, op: str, code: str, message: str, *, action: str, request_id: str = "", run_id: str = "",
           evidence: list[dict[str, Any]] | None = None, severity: str = "amber", risks: list[str] | None = None,
           **fields: Any) -> dict[str, Any]:
    """A ``qualified-draft`` reply; ``risks`` names failure modes a person should look for in it."""
    card = WarningCard(code=_code(code), message=message, suggested_action=action, severity=severity,
                       risks=list(risks or [])).model_dump()
    return {"component": component, "op": op, **fields, "code": code, "completion_state": "qualified-draft",
            "warning_card": card, "evidence": list(evidence or []), "request_id": request_id, "run_id": run_id}


def refuse(component: str, op: str, code: str, message: str, *, action: str, request_id: str = "", run_id: str = "",
           evidence: list[dict[str, Any]] | None = None, **fields: Any) -> dict[str, Any]:
    """A ``blocked-escalated`` reply with its stable code and the reason in words."""
    card = WarningCard(code=_code(code), message=message, suggested_action=action, severity="blocked").model_dump()
    return {"component": component, "op": op, **fields, "code": code, "completion_state": "blocked-escalated",
            "warning_card": card, "evidence": [*(evidence or []), {"kind": "refusal", "code": code, "reason": message}],
            "request_id": request_id, "run_id": run_id}


__all__ = ["CHECKER_NOT_INSTALLED", "CodeRefused", "answer", "refuse"]
