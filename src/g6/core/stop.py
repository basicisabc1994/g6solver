# SPDX-License-Identifier: Apache-2.0
"""One process-wide stop: while it is engaged, nothing that consults it starts new work (audit 007 SE-010).

Three ways to engage it, all read on every check:

- in process: :func:`engage` (and :func:`release`, which clears only this one);
- the environment variable ``G6_STOP``, set to any non-empty value;
- a file named ``STOP`` in ``$G6_HOME`` (:func:`stop_file`), so a person can stop a running process from outside it.
  Its first line, if any, is the reason. A stop file that exists but cannot be read counts as engaged.

Consulted by the workforce Door (before each call, and polled while a contained call runs), the safety engine's
policy engine (its kill-switch check, ahead of everything else), the operator loop before a tick, and
``g6.harness.run`` before the executor. ``G6_DISABLE_LLM`` stays the model-call switch in ``g6.core.llm``.

Limits: a check, not a preemption. Work already running finishes unless it polls (contained child processes poll
through ``g6.core.component_process``), and work outside G6, or a caller that never consults the stop, is not
stopped by it.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from g6.core.storage import g6_home

#: The stable code a refusal carries when the stop is engaged.
STOPPED = "G6_E_STOPPED"
_LOCK = threading.Lock()
_ENGAGED: list[str] = []


def stop_file() -> Path:
    """Where a person puts the stop: ``$G6_HOME/STOP``."""
    return g6_home() / "STOP"


def engage(reason: str = "") -> None:
    """Engage the in-process stop (the first reason is kept)."""
    with _LOCK:
        if not _ENGAGED:
            _ENGAGED.append(reason.strip() or "stopped in process")


def release() -> None:
    """Clear the in-process stop. ``G6_STOP`` and the stop file are cleared by whoever set them."""
    with _LOCK:
        _ENGAGED.clear()


def engaged() -> str | None:
    """Why the stop is engaged, or ``None`` when it is not."""
    with _LOCK:
        if _ENGAGED:
            return _ENGAGED[0]
    value = os.environ.get("G6_STOP", "").strip()
    if value:
        return f"G6_STOP is set ({value})"
    path = stop_file()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"the stop file {path} exists but could not be read ({type(exc).__name__})"
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    return f"the stop file {path} is present" + (f": {first}" if first else "")


def stopped() -> bool:
    """``engaged() is not None``: the shape a ``stop_requested`` callable takes."""
    return engaged() is not None
