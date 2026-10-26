# SPDX-License-Identifier: Apache-2.0
"""How several agents share one workspace folder: a short exclusive lock, locks on paths, and a log of conflicts.

``_``-prefixed, so it never registers as a component. Everything here lives in the workspace's bookkeeping folder
(``.g6-workspace``), which no caller's path may name:

* the **mutex** (``mutex``): a persistent file with an OS-backed exclusive lock held across each critical section;
  the OS releases it on process death, never on age;
* **locks** (``locks/<key>.json``): one per path, with the holder's name (a claim, recorded as ``claimed_holder``), the
  SHA-256 of a token only the taker is given, and when it expires. A live lock refuses every write to its path that does
  not present the token; a lock past its time-to-live may be taken over, and the takeover is logged;
* the **log** (``coordination.jsonl``): one JSON line per refused write (a stale base or a lock held by another) and per
  takeover, appended and never rewritten.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from g6.core.storage import PathRefused, contained, plain_file
from g6.workspace._envelope import BUSY, INPUT_INVALID, LOCKED, OUTSIDE_ROOT, STALE_WRITE, WorkspaceRefused

#: How long a writer waits for the OS-backed mutex.
MUTEX_WAIT = 5.0
#: The longest a lock may be taken for, in seconds.
MAX_TTL = 3600.0
LOG = "coordination.jsonl"
#: How many log entries one reply lists (the newest).
MAX_LOGGED = 1000


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_holder(holder: str) -> str:
    """The holder's name as given (1 to 100 printable characters): a claim, never checked against anything."""
    if not isinstance(holder, str) or not holder.strip() or len(holder) > 100 or not holder.isprintable():
        raise WorkspaceRefused(INPUT_INVALID, "holder is a name of 1 to 100 printable characters (it is recorded as "
                                              "the caller's word, never checked)")
    return holder


def bookkeeping_path(folder: Path, *parts: str, create: bool = False) -> Path:
    """Resolve bookkeeping through the shared containment check, refusing linked ancestors."""
    folder = folder.absolute()
    try:
        path = contained(folder.parent, folder.joinpath(*parts), refuse_links=True)
        if create:
            path.mkdir(parents=True, exist_ok=True)
            path = contained(folder.parent, path, refuse_links=True)
        if os.path.lexists(path) and path.is_file():
            plain_file(path)
        return path
    except PathRefused as exc:
        raise WorkspaceRefused(OUTSIDE_ROOT, exc.reason) from None


def _open_mutex(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    # Delete sharing lets cleanup remove its workspace while still holding the OS lock.
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0xC0000000, 0x7, None, 4, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        close(handle)
        raise


class Coordination:
    """The mutex, the locks and the log of one workspace's bookkeeping folder."""

    def __init__(self, bookkeeping: Path, clock: Callable[[], float] = time.time) -> None:
        self._folder, self.clock = bookkeeping, clock

    # --- the mutex ----------------------------------------------------------------------------------------------------

    @property
    def folder(self) -> Path:
        return bookkeeping_path(self._folder)

    @contextlib.contextmanager
    def mutex(self) -> Iterator[None]:
        """Hold an OS lock; age never permits takeover, and death releases the handle's lock."""
        folder = bookkeeping_path(self._folder, create=True)
        path = bookkeeping_path(folder, "mutex")
        handle = _open_mutex(path)
        locked = False
        try:
            checked = plain_file(path)
            opened = os.fstat(handle)
            if (opened.st_dev, opened.st_ino) != (checked.st_dev, checked.st_ino):
                raise WorkspaceRefused(BUSY, "the mutex file changed while it was opened")
            deadline = time.monotonic() + MUTEX_WAIT
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(handle, 0, os.SEEK_SET)
                        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise WorkspaceRefused(BUSY, "another writer holds the workspace mutex; nothing was written") from None
                    time.sleep(0.01)
            # A waiter on an unlinked inode must never enter a successor workspace's critical section.
            checked = plain_file(bookkeeping_path(folder, "mutex"))
            if (opened.st_dev, opened.st_ino) != (checked.st_dev, checked.st_ino):
                raise WorkspaceRefused(BUSY, "the workspace mutex was replaced while waiting")
            yield
        except PathRefused as exc:
            raise WorkspaceRefused(BUSY, exc.reason) from None
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(handle, 0, os.SEEK_SET)
                    msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)
            os.close(handle)  # persistent file: never unlink a successor's mutex

    # --- locks --------------------------------------------------------------------------------------------------------

    def _lock_file(self, rel: str) -> Path:
        return bookkeeping_path(self.folder, "locks", hashlib.sha256(os.path.normcase(rel).encode("utf-8")).hexdigest() + ".json")

    def _read_record(self, path: Path, rel: str | None = None) -> dict[str, Any] | None:
        """Absent records are absent; malformed records block indefinitely until repaired by a person."""
        try:
            if not os.path.lexists(path):
                return None
            plain_file(path)
            found = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(found, dict):
                raise ValueError("not an object")
            name = found["path"]
            check_holder(found["claimed_holder"])
            if not isinstance(name, str) or not name or (rel is not None and name != rel):
                raise ValueError("wrong path")
            if self._lock_file(name) != path or not isinstance(found["token_sha256"], str) \
                    or not re.fullmatch(r"[0-9a-f]{64}", found["token_sha256"]):
                raise ValueError("wrong key or token")
            for key in ("acquired_at", "expires_at"):
                value = found[key]
                if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                    raise ValueError("invalid timestamp")
                _iso(value)  # reject timestamps the reply cannot represent
            if found["expires_at"] <= found["acquired_at"] or found.get("unreadable"):
                raise ValueError("invalid lifetime")
            return found
        except (OSError, ValueError, KeyError, TypeError, OverflowError, WorkspaceRefused):
            return {"path": rel or f"(unreadable lock {path.stem[:12]})", "claimed_holder": "",
                    "token_sha256": "", "acquired_at": 0, "expires_at": 0, "unreadable": True}

    def record(self, rel: str) -> dict[str, Any] | None:
        return self._read_record(self._lock_file(rel), rel)

    def live(self, record: dict[str, Any] | None) -> bool:
        return record is not None and (bool(record.get("unreadable")) or record["expires_at"] > self.clock())

    def holds(self, record: dict[str, Any] | None, token: str) -> bool:
        return bool(record and token and record.get("token_sha256")
                    and hmac.compare_digest(str(record["token_sha256"]), _token_hash(token)))

    def shown(self, record: dict[str, Any]) -> dict[str, Any]:
        """A lock as a reply shows it: never its token's hash."""
        return {"path": record.get("path", ""), "claimed_holder": record.get("claimed_holder", ""),
                "acquired_at": _iso(float(record.get("acquired_at", 0))),
                "expires_at": _iso(float(record.get("expires_at", 0))), "live": self.live(record),
                **({"unreadable": True} if record.get("unreadable") else {})}

    def take(self, rel: str, holder: str, ttl: float, token: str = "") -> dict[str, Any]:
        """Take (or, with its token, renew) the lock on ``rel`` for ``ttl`` seconds; under the mutex. A live lock held by
        another is refused (``LOCKED``); one past its time-to-live is taken over, and the takeover is logged."""
        holder = check_holder(holder)
        if not 0 < ttl <= MAX_TTL:
            raise WorkspaceRefused(INPUT_INVALID, f"ttl_seconds is more than 0 and at most {MAX_TTL:g}")
        with self.mutex():
            current = self.record(rel)
            renewed = self.live(current) and self.holds(current, token)
            if self.live(current) and not renewed:
                shown = self.shown(current)  # type: ignore[arg-type]
                raise WorkspaceRefused(LOCKED, f"{rel} is locked by {shown['claimed_holder'] or 'an unreadable lock'!r} "
                                               f"(their word) until {shown['expires_at']}")
            took_over = None
            if current is not None and not self.live(current):
                took_over = self.shown(current)
                self.log({"kind": "takeover", "path": rel, "claimed_holder": holder,
                          "previous_holder": took_over["claimed_holder"], "expired_at": took_over["expires_at"]})
            new_token = token if renewed else secrets.token_urlsafe(18)
            now = self.clock()
            record = {"path": rel, "claimed_holder": holder, "token_sha256": _token_hash(new_token),
                      "acquired_at": float(current["acquired_at"]) if renewed else now,  # type: ignore[index]
                      "expires_at": now + ttl}
            self._write_record(rel, record)
        return {**self.shown(record), "token": new_token, "renewed": renewed, "took_over": took_over}

    def release(self, rel: str, token: str) -> dict[str, Any]:
        """Release the lock on ``rel`` with its token; under the mutex. A live lock whose token differs is refused."""
        with self.mutex():
            current = self.record(rel)
            if current is None or not self.live(current) and not self.holds(current, token):
                return {"path": rel, "released": False, "was_locked": False}
            if not self.holds(current, token):
                raise WorkspaceRefused(LOCKED, f"{rel} is locked by {current.get('claimed_holder') or 'nobody named'!r} "
                                               "(their word); only the token given when it was taken releases it")
            self._lock_file(rel).unlink(missing_ok=True)
        return {"path": rel, "released": True, "was_locked": True}

    def _write_record(self, rel: str, record: dict[str, Any]) -> None:
        path = self._lock_file(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
        temp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, path)

    def locks(self) -> list[dict[str, Any]]:
        """Every lock on file, live or expired, as a reply shows it."""
        folder = bookkeeping_path(self.folder, "locks")
        if not folder.is_dir():
            return []
        found = []
        for path in sorted(folder.glob("*.json")):
            record = self._read_record(path)
            if record is not None:
                found.append(self.shown(record))
        return sorted(found, key=lambda r: r["path"])

    def live_locks(self) -> list[dict[str, Any]]:
        return [lock for lock in self.locks() if lock["live"]]

    # --- the log ------------------------------------------------------------------------------------------------------

    def log(self, entry: dict[str, Any]) -> None:
        """Append one entry to the log (with the time); called under the mutex."""
        self.folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": _iso(self.clock()), **entry}, sort_keys=True, ensure_ascii=False)
        with open(bookkeeping_path(self.folder, LOG), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def entries(self) -> tuple[list[dict[str, Any]], int]:
        """Every entry of the log in order, and how many lines could not be read (counted, never skipped silently)."""
        path = bookkeeping_path(self.folder, LOG)
        if not path.is_file():
            return [], 0
        found, unreadable = [], 0
        for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if isinstance(entry, dict):
                found.append(entry)
            else:
                unreadable += 1
        return found, unreadable


__all__ = ["BUSY", "LOCKED", "LOG", "MAX_LOGGED", "MAX_TTL", "MUTEX_WAIT", "STALE_WRITE",
           "Coordination", "check_holder"]
