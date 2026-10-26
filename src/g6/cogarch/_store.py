# SPDX-License-Identifier: Apache-2.0
"""Named JSON store transactions: exclusive lock files and atomic, unique temporary writes.

Wait at most five seconds for a lock. A lock older than five minutes is an abandoned lease and is removed;
normal bounded operations take at most 120 seconds. An expired or replaced lease refuses the write before replace.
The owner token keeps an expired owner from removing a replacement lock.
"""
from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

LOCK_TIMEOUT_S = 5.0
STALE_LOCK_S = 300.0
_HELD: ContextVar[tuple[Path, Path, str, float] | None] = ContextVar("cogarch_store_lease", default=None)


class StoreBusy(TimeoutError):  # noqa: N818 - names a bounded store refusal
    """The named store could not be acquired within its lock timeout."""


@contextmanager
def store_lock(path: Path) -> Iterator[None]:
    """Hold this store's O_EXCL lock across load, change and save; different names stay independent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    token = uuid4().hex
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                before = lock.stat()
                if time.time() - before.st_mtime > STALE_LOCK_S and lock.stat() == before:
                    lock.unlink(missing_ok=True)
            except FileNotFoundError:
                pass  # the previous owner released it while we checked
            if time.monotonic() >= deadline:
                raise StoreBusy(f"the store {path.stem!r} is busy (lock timeout {LOCK_TIMEOUT_S:g} s)") from None
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token)
        break
    held = _HELD.set((path.resolve(), lock, token, time.monotonic() + STALE_LOCK_S))
    try:
        yield
    finally:
        _HELD.reset(held)
        try:
            if lock.read_text(encoding="utf-8") == token:
                lock.unlink()
        except FileNotFoundError:
            pass


def _check_lease(path: Path) -> None:
    held = _HELD.get()
    if held is None or held[0] != path.resolve():
        return
    _, lock, token, expires = held
    try:
        owned = lock.read_text(encoding="utf-8") == token
    except FileNotFoundError:
        owned = False
    if not owned or time.monotonic() >= expires:
        raise StoreBusy(f"the store {path.stem!r} lost its lock lease; retry from a fresh load")


def atomic_write(path: Path, text: str) -> None:
    """Replace a JSON file only after its unique temporary file in the same directory is closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".part", dir=path.parent)
    part = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        _check_lease(path)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
