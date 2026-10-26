# SPDX-License-Identifier: Apache-2.0
"""Owned child-process execution inside a trusted call window (deadline + operator stop), for the workforce Door.

This is a trusted same-user child, not a tenant or generated-code sandbox, unless the caller passes a confinement
plan (``confined``, :mod:`g6.core.confine`): then the child starts through the plan's operating-system isolation, inside
the same Job Object. The parent retains permissions, leases, reservations and ledger writes. Killing this process tree does not prove cancellation of an external server's work.
The archive's registry transport (``registry_invoke``, which forwarded billing identity to a component worker) is not
ported: library calls run in process through the workforce's own table.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from g6.core import confine
from g6.core.process_tree import _Tree

MAX_MESSAGE_BYTES = 16 * 1024 * 1024
POLL_SECONDS = .05


class ContainmentFailure(ConnectionResetError):
    """Even a declared read cannot be retried when process cleanup is unconfirmed."""


class ExecutionInterrupted(TimeoutError):
    """A trusted allowance ended; an operator stop must survive tool handlers."""

    def __init__(self, message: str, *, stopped: bool = False):
        super().__init__(message)
        self.stopped = stopped
        self.stdout = self.stderr = b""


@dataclass
class CallWindow:
    deadline: float
    stop_requested: Callable[[], bool] | None
    execution: dict = field(default_factory=dict)
    containment_failure: ContainmentFailure | None = None


_WINDOW: ContextVar[CallWindow | None] = ContextVar("workforce_call_window", default=None)


@contextmanager
def window(seconds: float, stop_requested: Callable[[], bool] | None = None) -> Iterator[CallWindow]:
    outer = _WINDOW.get()
    deadline = time.monotonic() + max(0., seconds)
    if outer is not None:
        deadline = min(deadline, outer.deadline)
        current_stop = stop_requested
        stop_requested = lambda: bool((outer.stop_requested and outer.stop_requested())  # noqa: E731
                                      or (current_stop and current_stop()))
    current = CallWindow(deadline, stop_requested, containment_failure=outer.containment_failure if outer else None)
    token = _WINDOW.set(current)
    try:
        yield current
    finally:
        _WINDOW.reset(token)
        if outer is not None and current.containment_failure is not None:
            outer.containment_failure = outer.containment_failure or current.containment_failure
        if outer is not None and current.execution.get("process_count"):
            child = current.execution
            total = outer.execution
            total.setdefault("boundary", "native_children")
            total["process_count"] = total.get("process_count", 0) + child["process_count"]
            total["cleanup_confirmed"] = total.get("cleanup_confirmed", True) and child.get("cleanup_confirmed", False)
            total["child_exited"] = total.get("child_exited", True) and child.get("child_exited", False)
            total["elapsed_seconds"] = total.get("elapsed_seconds", 0.) + child.get("elapsed_seconds", 0.)
            total["last_pid"] = child.get("pid", child.get("last_pid"))
            total.setdefault("model_calls_known", False)
            total.setdefault("external_work_cancelled", None)


def checkpoint() -> float | None:
    """Check the current trusted window, if any; return its remaining seconds."""
    limit = _WINDOW.get()
    if limit is None:
        return None
    if limit.containment_failure is not None:
        raise limit.containment_failure
    try:
        stopped = limit.stop_requested and limit.stop_requested()
    except OSError as exc:
        raise ExecutionInterrupted("The operator stop control could not be read", stopped=True) from exc
    if stopped:
        raise ExecutionInterrupted("The operator requested stop during execution", stopped=True)
    remaining = limit.deadline - time.monotonic()
    if remaining <= 0:
        raise ExecutionInterrupted("The worker runtime allowance expired during execution")
    return remaining


def run_process(argv: list[str], *, env: dict[str, str], cwd: str | Path | None = None,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                memory_bytes: int | None = None, confined: confine.Plan | None = None) -> int:
    """Internal owned execution with trusted stream handles and the current allowance.

    Stream ownership stays with the caller. No executable or handle is accepted
    from the registry wire. This is the same process authority for captured
    component replies and file-streamed repository logs. ``confined``: a plan
    with a backend starts the child through ``confine.popen``; one without (or
    None) starts it as the owner, as before.
    """
    limit = _WINDOW.get()
    if limit is None:
        raise RuntimeError("Contained execution requires a trusted call allowance")
    checkpoint()
    started = time.monotonic()
    spawn = partial(confine.popen, confined) if confined is not None and confined.backend else None
    tree = _Tree(argv, env=env, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr, memory_bytes=memory_bytes,
                 **({"spawn": spawn} if spawn else {}))
    limit.execution.update(pid=tree.proc.pid, process_count=1)
    limit.execution.setdefault("boundary", "owned_process")
    try:
        while True:
            remaining = checkpoint()
            try:
                tree.proc.wait(timeout=min(POLL_SECONDS, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        # Clean descendants even when their parent reported success.
        limit.execution["cleanup_confirmed"] = False
        try:
            tree.kill(strict=True)
            if tree.proc.poll() is None:
                raise OSError("owned child remains alive")
            limit.execution["cleanup_confirmed"] = True
        except Exception as exc:
            limit.containment_failure = ContainmentFailure("G6 could not confirm cleanup of the owned process tree")
            raise limit.containment_failure from exc
        finally:
            limit.execution["elapsed_seconds"] = time.monotonic() - started
            limit.execution["child_exited"] = tree.proc.poll() is not None
    checkpoint()  # completion/cleanup may have consumed the allowance or received stop
    return tree.proc.returncode


def run_contained(argv: list[str], *, env: dict[str, str], cwd: str | Path | None = None,
                  input_bytes: bytes = b"", memory_bytes: int | None = None,
                  confined: confine.Plan | None = None) -> subprocess.CompletedProcess:
    """Owned execution with bounded reply capture, including interruption tails.

    File-backed output avoids blocking Windows pipe writes and inherited pipes.
    Callers with streamed logs use run_process without this reply-size limit.
    ``memory_bytes`` is a per-process cap on Windows (see :mod:`g6.core.process_tree`).
    """
    with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as outgoing, tempfile.TemporaryFile() as errors:
        incoming.write(input_bytes)
        incoming.seek(0)
        try:
            code = run_process(argv, env=env, cwd=cwd, stdin=incoming, stdout=outgoing, stderr=errors,
                               memory_bytes=memory_bytes, **({"confined": confined} if confined is not None else {}))
        except ExecutionInterrupted as exc:
            outgoing.seek(0)
            errors.seek(0)
            exc.stdout, exc.stderr = outgoing.read(MAX_MESSAGE_BYTES), errors.read(MAX_MESSAGE_BYTES)
            raise
        outgoing.seek(0)
        errors.seek(0)
        out, err = outgoing.read(MAX_MESSAGE_BYTES + 1), errors.read(MAX_MESSAGE_BYTES + 1)
        if max(len(out), len(err)) > MAX_MESSAGE_BYTES:
            raise ConnectionResetError("The owned process exceeded the bounded output protocol")
        return subprocess.CompletedProcess(argv, code, out, err)
