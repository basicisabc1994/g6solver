# SPDX-License-Identifier: Apache-2.0

"""How many times this process has asked a language model, and how many of those one request asked.

A caller that wants to say whether a model produced any part of its answer
reads :func:`model_calls` before and after the work and reports the
difference. The doors that note a call: every built-in caller in
:mod:`g6.core.llm` (Ollama, OpenAI-compatible, Anthropic, and ``FakeLLM``,
which stands in for a model), before the request is sent.

The count is process-wide, so a call made by other work running at the same
time is counted too: a positive number can over-report. Zero is NOT proof that
no model was asked: a caller you pass in that is not one of the built-ins is
not counted unless it calls :func:`note_model_call` itself. So "model-free" is
a declared property of an operation, with this count only as a cross-check.

``counting()`` also counts the calls noted in ONE context (one request, one
attempt) while it is open; cost is attributed from the scoped count, never the
process-wide one. A call made on a thread the request did not hand its context
to (a bare ``threading.Thread`` or a pool worker) is not counted in the scope,
though it is still counted process-wide: the scoped count can under-report a
request's own calls and never counts another request's.
"""
from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_count = 0


class _Scope:
    __slots__ = ("count", "before_call")

    def __init__(self, before_call: Callable[[], None] | None = None) -> None:
        self.count = 0
        self.before_call = before_call


#: The counting scopes the current context sits in, outermost first: a call counts for each of them.
_scopes: contextvars.ContextVar[tuple[_Scope, ...]] = contextvars.ContextVar("g6_model_call_scopes", default=())


def note_model_call() -> None:
    """Record that a language model is being asked (or answered from a cache)."""
    global _count
    for scope in _scopes.get():
        if scope.before_call is not None:
            scope.before_call()
    with _lock:
        _count += 1
        for scope in _scopes.get():
            scope.count += 1


def note_child_model_calls(*, scoped: int, process: int) -> None:
    """Import a trusted child's separate totals without billing its unscoped work.

    The conservative process tally includes supervised children. Only calls
    attributed to the child's request scope join the parent's active scopes.
    """
    if type(scoped) is not int or type(process) is not int or not 0 <= scoped <= process:
        raise ValueError("Child model counts must be nonnegative integers with scoped <= process")
    global _count
    with _lock:
        _count += process
        for scope in _scopes.get():
            scope.count += scoped


def model_calls() -> int:
    """The model calls noted in this process and imported from supervised children."""
    return _count


@contextmanager
def counting(*, before_call: Callable[[], None] | None = None) -> Iterator[Callable[[], int]]:
    """Count calls in this context; optionally observe each boundary before the backend overwrites its receipt.
    Child totals have no individual boundary. Observers must not call a model or raise.
    """
    scope = _Scope(before_call)
    token = _scopes.set((*_scopes.get(), scope))
    try:
        yield lambda: scope.count
    finally:
        _scopes.reset(token)


__all__ = ["counting", "model_calls", "note_child_model_calls", "note_model_call"]
