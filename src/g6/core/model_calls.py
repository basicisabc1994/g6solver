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

A scope opened with an observer (``counting(before_call=...)``) is a boundary
every one of its request's notes must pass, so it reaches further: a
``threading.Thread`` started while such a scope is open in the starting
context runs in a copy of that context, as a thread handed the context by its
request does. What a caller's backend sends from a thread it starts itself is
then noted in the scope and passes the observer before it goes. A thread that
was already running is not reached, nor one started through ``_thread``
directly. A thread keeps the context it started in: what it notes once the
scope has closed still passes that scope's observer, as a copied context does.

Each note that passes an observer has a record (:class:`Note`), which the
observer reads with :func:`noting`: the frame at the send's boundary, where the
request is handed to its backend. A failure is read back to the send it is the
outcome of by that frame (:func:`outcome_of`), never by what a frame's names
hold later. The record holds the frame while the outermost scope with an
observer is open, which is while a failure of that request can still come out.
"""
from __future__ import annotations

import contextvars
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from types import FrameType

_lock = threading.Lock()
_count = 0
#: Guards what observed scopes keep (their notes' records, the thread hook). Never held while ``_lock`` is, or the other
#: way round, and never while an observer runs or a frame is let go.
_kept = threading.Lock()


class Note:
    """The record of one send at its boundary, made as the request is noted and before any observer runs: ``frame`` is
    the call that hands the request to its backend (the caller of :func:`note_model_call`, or a frame further out that a
    helper names with ``boundary``). The interpreter makes a frame once for each call of a function and never reuses it
    while this record holds it, so it names this send and no other made from another call. A frame keeps the stack it
    ran under alive, so the record holds it only while the outermost scope with an observer it was noted in is open:
    once that scope closes ``frame`` is ``None``, and no failure is read back to this send by it again."""

    __slots__ = ("frame",)

    def __init__(self, frame: FrameType | None) -> None:
        self.frame = frame


class _Scope:
    __slots__ = ("count", "before_call", "notes", "closed")

    def __init__(self, before_call: Callable[[], None] | None = None) -> None:
        self.count = 0
        self.before_call = before_call
        self.notes: list[Note] = []     # the records made while this is the outermost scope with an observer
        self.closed = False


#: The counting scopes the current context sits in, outermost first: a call counts for each of them.
_scopes: contextvars.ContextVar[tuple[_Scope, ...]] = contextvars.ContextVar("g6_model_call_scopes", default=())
#: The record of the note the observers of this context are being run for.
_noting: contextvars.ContextVar[Note | None] = contextvars.ContextVar("g6_model_call_noting", default=None)
#: ``threading.Thread.start`` as it was before the first observer was set (``_carry_into_threads``).
_thread_start: Callable[[threading.Thread], None] | None = None


def _observed() -> bool:
    return any(scope.before_call is not None for scope in _scopes.get())


def _carry_into_threads() -> None:
    """From the first observer on, a thread started where one is open runs in a copy of the starting context, so the
    observer, and whatever else the request keeps in its context, reaches what the thread notes. Set once for the
    process. A thread started where no observer is open starts as it always did."""
    global _thread_start
    if _thread_start is not None:
        return
    with _kept:
        if _thread_start is not None:
            return
        _thread_start = started = threading.Thread.start

        def start(thread: threading.Thread) -> None:
            if not _observed():
                return started(thread)
            context, run, own = contextvars.copy_context(), thread.run, "run" in vars(thread)

            def run_in_context() -> None:
                try:
                    context.run(run)
                finally:              # the thread is over: it no longer holds the context, or a name of ours
                    if own:
                        thread.run = run
                    else:
                        vars(thread).pop("run", None)

            thread.run = run_in_context
            try:
                return started(thread)
            except BaseException:
                if own:
                    thread.run = run
                else:
                    vars(thread).pop("run", None)
                raise

        threading.Thread.start = start


def note_model_call(*, boundary: int = 0) -> None:
    """Record that a language model is being asked (or answered from a cache).

    Each observer of the scopes this context sits in runs first. What one raises goes to the caller, and the request is
    then neither counted nor, where the caller notes before it sends, sent. ``boundary``: how many frames out from the
    caller of this function the send's boundary is, for a helper that notes on behalf of the function that called it."""
    global _count
    scopes = _scopes.get()
    outermost = next((scope for scope in scopes if scope.before_call is not None), None)
    if outermost is not None:
        try:
            note = Note(sys._getframe(1 + boundary))
        except ValueError:
            note = Note(None)
        with _kept:     # the record holds its frame while that scope is open
            kept = not outermost.closed
            if kept:
                outermost.notes.append(note)
        if not kept:    # noted after the scope closed (a thread that kept its context): nothing reads it back
            note.frame = None
        token = _noting.set(note)
        try:
            for scope in scopes:
                if scope.before_call is not None:
                    scope.before_call()
        finally:
            _noting.reset(token)
    with _lock:
        _count += 1
        for scope in _scopes.get():
            scope.count += 1


def noting() -> Note | None:
    """The record of the note an observer is being run for, or ``None`` outside an observer's run."""
    return _noting.get()


def outcome_of(error: BaseException, notes: Sequence[Note]) -> Note | None:
    """Which of ``notes`` the failure ``error`` is the outcome of: the one whose boundary frame ``error`` left through,
    read from its own traceback, innermost frame first, then from what it was raised from. Re-raised later, from
    another place or after other sends, an error still holds the frames it first left through, so it names its own send.
    ``None`` when it left through no boundary here, or through one that several of ``notes`` were made at, which cannot
    say which of them failed."""
    seen: set[int] = set()
    failed: BaseException | None = error
    while failed is not None and id(failed) not in seen:
        seen.add(id(failed))
        frames, tb = [], failed.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        for frame in reversed(frames):
            made = [note for note in notes if note.frame is frame]
            if made:
                return made[0] if len(made) == 1 else None
        failed = failed.__cause__ or failed.__context__
    return None


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
    Child totals have no individual boundary. An observer must not call a model. It may raise: what it raises goes to
    the caller that noted the request, before the request is counted or sent, which is how an admission check refuses
    one. With an observer, a thread started inside starts in a copy of this context (see the module docstring).
    """
    if before_call is not None:
        _carry_into_threads()
    scope = _Scope(before_call)
    token = _scopes.set((*_scopes.get(), scope))
    try:
        yield lambda: scope.count
    finally:
        _scopes.reset(token)
        with _kept:
            scope.closed = True
            made, scope.notes = scope.notes, []
        # The records made under it let their frames go, and the stacks those hold. Outside the lock: a frame that goes
        # can run whatever its names held as they are finalised, and that may note a call or close a scope of its own.
        for note in made:
            note.frame = None


__all__ = ["Note", "counting", "model_calls", "note_child_model_calls", "note_model_call", "noting", "outcome_of"]
