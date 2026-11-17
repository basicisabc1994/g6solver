# SPDX-License-Identifier: Apache-2.0
"""G6's repair log: ``repairs/repairs.jsonl`` under the state folder, append-only, codes and ids only (plan revision 8,
the repair loop).

``g6.mcp.repair`` writes one ``refused`` row for each repair block it adds to a reply, and one ``next`` row saying how
that repair ended: the next call of the same tool in the session was refused again (``not``) or not (``resolved``), or
the block sent the agent to the person (``person``). No argument and no word of text is ever written.
:func:`repair_stats` reads it (resolution rate by code) for the self-improvement loop; nothing reads it automatically
yet.
"""
from __future__ import annotations

import atexit
import errno
import json
import os
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LOG = ("repairs", "repairs.jsonl")
#: The only fields a row may hold.
FIELDS = frozenset({"event", "id", "session", "tool", "code", "fix_by", "by", "outcome"})
#: Maximum number of waiting rows per process; overflow never waits for disk.
QUEUE_CAPACITY = 10_000
#: How long one writer waits for another server's lock on either platform.
LOCK_WAIT_S = 120.0
LOCK_RETRY_S = 0.01
#: Windows: the byte locked, beyond the file's contents, so reading the lock file never meets a locked region.
LOCK_OFFSET = 0x7FFFFFFF


def _drop_counts(path: Path) -> dict[str, int]:
    """Only opaque process ids and counts; malformed counters are never overwritten."""
    from g6.core.storage import plain_file

    try:
        path.lstat()
        plain_file(path)
        found = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(found, dict) or any(
        not isinstance(key, str) or len(key) != 32 or any(c not in "0123456789abcdef" for c in key)
        or type(count) is not int or count < 0 for key, count in found.items()
    ):
        raise ValueError("invalid repair drop counter")
    return found


@contextmanager
def _process_lock(path: Path):
    """All writers of this log share one OS lock, released if the process exits."""
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            # The locked byte lies past anything written, so a reader of the lock file never meets the lock.
            def acquire():
                lock.seek(LOCK_OFFSET)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                lock.seek(LOCK_OFFSET)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                # Unsupported offsets and other permanent failures cannot be
                # fixed by waiting. Keep the same lock protocol or drop the row.
                if exc.errno not in {None, errno.EACCES, errno.EAGAIN, errno.EDEADLK} \
                        and getattr(exc, "winerror", None) not in {32, 33}:
                    raise
                left = deadline - time.monotonic()
                if left <= 0:
                    raise
                time.sleep(min(LOCK_RETRY_S, left))
        try:
            yield
        finally:
            release()


class _Writer:
    """One lazy daemon writer per process; no producer takes the file lock."""

    def __init__(self):
        self.rows: queue.Queue[tuple[Path, str]] = queue.Queue(QUEUE_CAPACITY)
        self.dropped = 0
        self.written = 0
        self._id = uuid.uuid4().hex
        self._home_drops: dict[Path, int] = {}
        self._saved_drops: dict[Path, int] = {}
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None

    def drop(self, home: Path):
        with self._guard:
            self.dropped += 1
            self._home_drops[home] = self._home_drops.get(home, 0) + 1

    def _persist_drops(self, home: Path):
        """Best effort under the row's existing OS lock: no extra lock wait.

        A lock/containment failure stays in memory until a later row for this
        captured home reaches the lock. A counter failure never changes a row.
        """
        from g6.core.storage import contained

        with self._guard:
            count = self._home_drops.get(home, 0)
        if count == self._saved_drops.get(home, 0):
            return
        temp = None
        try:
            target = contained(home, Path("repairs", "repairs.dropped"), refuse_links=True)
            counts = _drop_counts(target)
            counts[self._id] = max(counts.get(self._id, 0), count)
            temp = contained(home, Path("repairs", f"drops-{self._id}.tmp"), refuse_links=True)
            with temp.open("w", encoding="utf-8", newline="\n") as out:
                out.write(json.dumps(counts, sort_keys=True) + "\n")
            temp.replace(target)
            self._saved_drops[home] = count
        except Exception:  # noqa: BLE001 - bookkeeping cannot lose or block a later row
            pass
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def submit(self, home: Path, line: str):
        with self._guard:
            if self._thread is None:
                thread = threading.Thread(target=self._run, name="g6-repair-log", daemon=True)
                thread.start()
                self._thread = thread
        try:
            self.rows.put_nowait((home, line))
        except queue.Full:
            self.drop(home)

    def _run(self):
        from g6.core.storage import contained

        while True:
            home, line = self.rows.get()
            counted = False
            try:
                # Validate the captured home with the same storage boundary; never read a later G6_HOME.
                path = contained(home, Path(*LOG))
                path.parent.mkdir(parents=True, exist_ok=True)
                with _process_lock(path.with_suffix(".lock")):
                    try:
                        with path.open("a", encoding="utf-8", newline="\n") as out:
                            out.write(line)
                    except Exception:
                        self.drop(home)
                        counted = True
                        raise
                    finally:
                        self._persist_drops(home)
                with self._guard:
                    self.written += 1
            except Exception:  # noqa: BLE001 - bookkeeping must not kill the writer or hold a reply
                if not counted:
                    self.drop(home)
            finally:
                self.rows.task_done()


_writer = _Writer()


def _after_fork():
    global _writer
    _writer = _Writer()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def append(row: dict[str, Any]) -> None:
    """Queue a snapshot of codes and ids without disk I/O; count overflow, retain best-effort disk writes."""
    from g6.core.storage import g6_home

    try:
        line = json.dumps({k: v for k, v in row.items() if k in FIELDS}, sort_keys=True) + "\n"
        _writer.submit(g6_home().absolute(), line)
    except (OSError, ValueError, TypeError, RuntimeError):
        pass


def flush(timeout: float | None = None) -> bool:
    """Drain accepted rows for shutdown or an explicit reader, within a bounded wait. Replies never call this."""
    deadline = time.monotonic() + (LOCK_WAIT_S + 5.0 if timeout is None else max(0.0, timeout))
    rows = _writer.rows
    with rows.all_tasks_done:
        while rows.unfinished_tasks:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            rows.all_tasks_done.wait(left)
    return True


#: How long a process waits at exit with no queued row written; past it the daemon writer ends with the process.
EXIT_FLUSH_S = 2.0
#: Absolute exit budget even while the writer keeps making progress.
EXIT_TOTAL_S = 10.0


def _flush_at_exit() -> None:
    """Drain at exit while rows are being written; never hold an exit for another server's lock. Rows still queued
    after EXIT_FLUSH_S with none written (or after EXIT_TOTAL_S in all) are lost, a stated limit."""
    rows = _writer.rows
    end = time.monotonic() + EXIT_TOTAL_S
    with rows.all_tasks_done:
        while rows.unfinished_tasks:
            before = _writer.written
            rows.all_tasks_done.wait(min(EXIT_FLUSH_S, max(0.0, end - time.monotonic())))
            if _writer.written == before or time.monotonic() >= end:
                return


atexit.register(_flush_at_exit)


def repair_stats(path: Path | None = None) -> dict[str, Any]:
    """Resolution by code: refused, resolved, not, person, and the rate resolved / (resolved + not), ``None`` before
    any repair the agent made has ended. A nonzero ``dropped`` field counts this process's queue overflows and
    abandoned writer rows (including lock timeouts, OS errors and containment refusals);
    absence means zero. Shared counters carry abandoned rows to other processes
    when the writer can persist them under an acquired lock. This reads a
    snapshot and never waits for the writer."""
    from g6.core.storage import state_path

    path = path or state_path(*LOG)
    out: dict[str, Any] = {}
    with _writer._guard:
        dropped = _writer.dropped
    try:
        counts = _drop_counts(path.with_suffix(".dropped"))
        # This process's count may be both in memory and on disk: count it once.
        dropped = sum(count for key, count in counts.items() if key != _writer._id) + max(
            dropped, counts.get(_writer._id, 0))
    except (OSError, ValueError, TypeError, RecursionError, AttributeError):
        pass
    if dropped:
        out["dropped"] = dropped
    try:
        lines = path.read_bytes().splitlines()
    except (OSError, UnicodeError):
        return out
    for line in lines:
        try:
            row = json.loads(line.decode("utf-8"))
            if not isinstance(row, dict) or not isinstance(row.get("code"), str):
                continue
            stats = out.setdefault(row["code"], {"refused": 0, "resolved": 0, "not": 0, "person": 0})
            if row.get("event") == "refused":
                stats["refused"] += 1
            elif row.get("event") == "next" and row.get("outcome") in ("resolved", "not", "person"):
                stats[row["outcome"]] += 1
        except Exception:  # noqa: BLE001 - one malformed row, including recursion errors, never stops the reader
            continue
    for code, stats in out.items():
        if code == "dropped":
            continue
        ended = stats["resolved"] + stats["not"]
        stats["rate"] = round(stats["resolved"] / ended, 3) if ended else None
    return dict(sorted(out.items()))


__all__ = ["FIELDS", "LOG", "append", "flush", "repair_stats"]
