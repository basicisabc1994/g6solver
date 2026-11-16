# SPDX-License-Identifier: Apache-2.0
"""The local record of overrides (design section 4 step 6): one entry per override run, appended to a hash-chained
ledger (``g6.core.ledger.ChainLedger``, used unchanged) at ``<G6 state folder>/agentic/overrides.jsonl``.

The file is owned by the same user as G6: it records what was done, it does not stop that user's own programs from
writing it. An entry holds digests, never the operation's parameters or result: the part, the trigger, the question's
digest, the person's answer and the name they gave (not authenticated), the model, the checks and the cost. It may
direct where the self-improvement loop looks next; it never counts as evidence that a change works (design 10.10)."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from g6.core.ledger import ChainLedger
from g6.core.storage import state_path

KIND = "agentic_override"
_LOCK = threading.Lock()


def path() -> Path:
    return state_path("agentic", "overrides.jsonl")


def append(body: dict[str, Any]) -> str:
    """Append one override's record; its entry hash. Raises ``g6.core.ledger.LedgerError`` (or ``OSError``) when it
    cannot be written, and then nothing is."""
    with _LOCK:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        return ChainLedger(target).append(KIND, uuid.uuid4().hex, body)


__all__ = ["KIND", "append", "path"]
