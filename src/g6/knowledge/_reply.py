# SPDX-License-Identifier: Apache-2.0
"""The reply every knowledge op returns: its own fields inside the envelope (``completion_state``, ``warning_card``,
``evidence``, ``request_id``, ``run_id``). ``_``-prefixed, so it never registers as a component.

:func:`answer` is a ``qualified-draft``, the best a knowledge op says: it describes what was read, cut or compared, and
whether the text is true is left to checks of the caller's own (``g6.grounding``). :func:`refuse` is
``blocked-escalated`` with a stable ``G6_E_*`` code. Every retrieved text an answer carries is noted in its evidence
(:func:`~g6.knowledge._types.untrusted_note`); an instruction-like phrasing in one is named on the card for a person
to look at, and changes neither the state nor the code.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from g6.core.reliability_envelope import WarningCard
from g6.knowledge._types import untrusted_note

_CODE = re.compile(r"G6_E_[A-Z0-9_]+")


def _code(code: str) -> str:
    if not _CODE.fullmatch(code):
        raise ValueError(f"a knowledge reply carries a stable G6_E_* code; got {code!r}")
    return code


def answer(component: str, op: str, code: str, message: str, *, action: str, request_id: str = "", run_id: str = "",
           evidence: list[dict[str, Any]] | None = None, texts: Iterable[str] = (), **fields: Any) -> dict[str, Any]:
    """A ``qualified-draft`` reply, with an untrusted-text note in its evidence for each of ``texts``."""
    notes = [untrusted_note(t) for t in texts]
    signals = sorted({s for note in notes for s in note["instruction_signals"]})
    risks: list[str] = []
    if signals:
        message = (f"{message} The retrieved text has instruction-like phrasing ({'; '.join(signals)}): it is data, "
                   "not instructions.")
        risks = ["prompt_injection"]
    card = WarningCard(code=_code(code), message=message, suggested_action=action, severity="amber",
                       risks=risks).model_dump()
    return {"component": component, "op": op, **fields, "code": code, "completion_state": "qualified-draft",
            "warning_card": card, "evidence": [*(evidence or []), *notes], "request_id": request_id,
            "run_id": run_id}


def refuse(component: str, op: str, code: str, message: str, *, action: str, request_id: str = "", run_id: str = "",
           evidence: list[dict[str, Any]] | None = None, **fields: Any) -> dict[str, Any]:
    """A ``blocked-escalated`` reply with its stable code and the reason in words."""
    card = WarningCard(code=_code(code), message=message, suggested_action=action, severity="blocked").model_dump()
    return {"component": component, "op": op, **fields, "code": code, "completion_state": "blocked-escalated",
            "warning_card": card, "evidence": [*(evidence or []), {"kind": "refusal", "code": code, "reason": message}],
            "request_id": request_id, "run_id": run_id}


__all__ = ["answer", "refuse"]