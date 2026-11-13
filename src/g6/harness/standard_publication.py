# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A review's task-store guard, entered at confirmed evidence issuance.

The context is local to the executing review, including on an interruption; it carries no answer and persists no
state. Standalone library standards have no task store. MCP reviews install the store's counting guard, which shares
the answer writer's lock and reads its retained head before any evidence reaches the issuer.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Any

CODE = "G6_E_QUESTION_STANDARD_SUPERSEDED"


class StandardSuperseded(RuntimeError):
    """The task no longer requires the standard the review ran."""

    def __init__(self) -> None:
        super().__init__(f"[{CODE}] the task's confirmed standard changed while the review ran; review again "
                         "against the current standard: none of this review's evidence is issued")


Guard = Callable[[Any], AbstractContextManager[None]]
_guard: ContextVar[Guard | None] = ContextVar("g6_standard_publication", default=None)


@contextmanager
def observing(guard: Guard) -> Iterator[None]:
    """Install the task-store guard for this executing review, restoring its caller's context on every exit."""
    token = _guard.set(guard)
    try:
        yield
    finally:
        _guard.reset(token)


@contextmanager
def counting(standard: Any) -> Iterator[None]:
    """Hold the current task standard through issuance, when the review has a task-store guard."""
    guard = _guard.get()
    if guard is None:
        yield
    else:
        with guard(standard):
            yield
