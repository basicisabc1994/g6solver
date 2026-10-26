# SPDX-License-Identifier: Apache-2.0
"""The workspace folder the family's members share: one folder, and every path in it checked as part of the act.

``_``-prefixed, so it never registers as a component. :class:`Workspace` is a folder G6 keeps files in for one piece of
work: :meth:`Workspace.for_task` gives each task its own under the G6 home (``state_path("workspaces", task_id)``),
and ``Workspace(root)`` uses a folder a Python caller chooses. Every path a caller names goes through
``g6.core.storage.contained``: joined onto the folder, resolved (``..``, symbolic links and junctions followed) and
refused if it leaves it. The check is part of each read and write, not a separate step a caller may skip
(WORKSPACE_MANAGER-01): a write makes its file beside the target, resolves that file's real folder again once it is
open, and only then puts it in place, so a folder swapped for a link after the check is caught (WORKSPACE_MANAGER-04);
it replaces a file in one step, so a hard link to a file elsewhere is never written through. A read refuses a hard link
and checks that the file it opened is the one it checked.

G6 keeps its own bookkeeping in ``.g6-workspace`` inside the folder (a marker saying G6 made the workspace, and the
backups); no caller's path may name it. Backups are gzip tar archives of the plain files (links are skipped and
named); a restore takes only a backup kept there, checks every member with ``tarfile``'s ``data`` filter before
anything changes, extracts with it into a folder of its own, backs up what it replaces, and only then swaps the files
in. Clean-up removes only a folder G6 made a workspace, and never follows a link out of it. Each changing method is a
dry run unless ``dry_run`` is false.

For several agents in one folder (``shared_workspace``), a write may say which version it read (``expect_sha256``):
the check and the replace happen under the workspace's short exclusive lock (``g6.workspace._coordination``), so a
write based on a version someone has since replaced is refused with both hashes and logged. A live lock on a path
refuses every write to it that does not carry the lock's token, a plain write included; a restore or clean-up is
refused while any path is locked.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import secrets
import stat
import tarfile
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import EllipsisType
from typing import Any

from g6.core.resources import RESOURCE_LIMIT
from g6.core.storage import PathRefused, contained, plain_file, state_path
from g6.workspace._coordination import Coordination, bookkeeping_path
from g6.workspace._envelope import (ARCHIVE_REFUSED, INPUT_INVALID, LOCKED, NOT_A_FILE, NOT_A_WORKSPACE, NOT_FOUND,
                                    OUTSIDE_ROOT, RESERVED, STALE_WRITE, WorkspaceRefused)

#: G6's own folder inside each workspace; no caller's path may name it.
BOOKKEEPING = ".g6-workspace"
MARKER = "workspace.json"
BACKUPS = "backups"
#: How much a workspace may hold, one file may be, and how many entries an archive may have.
MAX_WORKSPACE_BYTES = 500 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_MEMBERS = 20_000
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DEVICES = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))})


def _plain_name(name: str, what: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or ".." in name or name.endswith(".") \
            or name.split(".", 1)[0].lower() in _DEVICES:
        raise WorkspaceRefused(INPUT_INVALID, f"{what} {name!r} is not a plain name (letters, digits, '.', '_' and "
                                              "'-', at most 100, no folder, no '..')")
    return name


def check_task(task_id: str) -> str:
    """``task_id`` if it may name a task's workspace folder, else :class:`WorkspaceRefused` (``INPUT_INVALID``)."""
    return _plain_name(task_id, "task_id")


def check_backup(name: str) -> str:
    """``name`` if it may name a backup (a plain name ending ``.tar.gz``), else ``INPUT_INVALID``."""
    if not str(name).endswith(".tar.gz"):
        raise WorkspaceRefused(INPUT_INVALID, f"backup {name!r} is not a backup's name (one ending .tar.gz, as "
                                              "status lists them)")
    return _plain_name(name, "backup")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _linked(found: os.stat_result) -> bool:
    """A symbolic link or a Windows reparse point (a junction, a mount point): never followed, never archived."""
    return stat.S_ISLNK(found.st_mode) or bool(getattr(found, "st_file_attributes", 0)
                                               & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _same_folder(path: Path, expected: os.stat_result) -> None:
    """Fail closed if a checked directory was replaced, including by a junction."""
    try:
        current = os.lstat(path)
    except OSError:
        raise WorkspaceRefused(OUTSIDE_ROOT, "a folder disappeared during deletion") from None
    if _linked(current) or not stat.S_ISDIR(current.st_mode) \
            or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise WorkspaceRefused(OUTSIDE_ROOT, "a folder changed during deletion; traversal refused")


def _open_directory(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    # No delete sharing: while this handle is held the named directory cannot be replaced.
    handle = create(str(path), 0x80000000, 0x3, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        close(handle)
        raise


def _checked_listing(path: Path, expected: os.stat_result) -> list[str]:
    """List an opened directory whose device/inode match, rejecting links at every depth."""
    handle = -1
    try:
        # Opening the path listing is followed by a fresh check before any child is used.
        with os.scandir(path):
            _same_folder(path, expected)
            handle = _open_directory(path)
            opened = os.fstat(handle)
            if _linked(opened) or not stat.S_ISDIR(opened.st_mode) \
                    or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                raise WorkspaceRefused(OUTSIDE_ROOT, "the opened directory differs from the checked folder")
            _same_folder(path, expected)
            if os.name == "nt":
                # The directory handle pins the name; reopen the listing while it is pinned.
                with os.scandir(path) as pinned:
                    names = [entry.name for entry in pinned]
            else:
                with os.scandir(handle) as pinned:
                    names = [entry.name for entry in pinned]
            _same_folder(path, expected)
    except OSError:
        raise WorkspaceRefused(OUTSIDE_ROOT, "a folder could not be opened unchanged for deletion") from None
    finally:
        if handle != -1:
            os.close(handle)
    # Leave bookkeeping, and its held mutex, until all other children have gone.
    return sorted(names, key=lambda name: (name.lower() in (BOOKKEEPING, "mutex"), name))

def _remove(path: Path, ancestors: tuple[tuple[Path, os.stat_result], ...] = ()) -> None:
    """Remove links themselves; recheck every directory before listing and each child operation.

    These path checks do not make deletion atomic against arbitrary external renames: a swap after the
    last check remains possible. Cooperating G6 callers are serialised by the workspace mutex.
    """
    for ancestor, expected in ancestors:
        _same_folder(ancestor, expected)
    found = os.lstat(path)
    if _linked(found):
        if stat.S_ISDIR(found.st_mode):
            os.rmdir(path)  # Windows junction: remove the junction, never enter it
        else:
            os.unlink(path)
        return
    if stat.S_ISDIR(found.st_mode):
        for name in _checked_listing(path, found):
            _same_folder(path, found)
            _remove(path / name, (*ancestors, (path, found)))
        _same_folder(path, found)
        os.rmdir(path)
        return
    try:
        os.unlink(path)
    except PermissionError:
        # Never chmod a link swapped in after the first check (chmod would follow it).
        current = os.lstat(path)
        if _linked(current) or (current.st_dev, current.st_ino) != (found.st_dev, found.st_ino):
            raise WorkspaceRefused(OUTSIDE_ROOT, "a file changed during deletion") from None
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.unlink(path)

def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _replace(source: Path, target: Path) -> None:
    """``os.replace``, tried again for up to a second while a reader on Windows still has ``target`` open."""
    for attempt in range(50):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


class Workspace:
    """A folder G6 keeps one piece of work's files in; every path in it is checked as part of the act."""

    def __init__(self, root: str | os.PathLike[str], *, max_bytes: int = MAX_WORKSPACE_BYTES, label: str = "",
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes
        #: How replies name the folder: a task's is named under the G6 home, never by its path on the machine.
        self.label = label or str(self.root)
        #: The short exclusive lock, the locks on paths and the log of conflicts (``g6.workspace._coordination``).
        self.coordination = Coordination(self.root / BOOKKEEPING, clock)

    @classmethod
    def for_task(cls, task_id: str, **kwargs: Any) -> Workspace:
        """The task's own workspace folder, ``workspaces/<task_id>`` under the G6 home."""
        name = check_task(task_id)
        return cls(state_path("workspaces", name), label=f"workspaces/{name} under the G6 home", **kwargs)

    @property
    def bookkeeping(self) -> Path:
        return bookkeeping_path(self.root / BOOKKEEPING)

    def is_workspace(self) -> bool:
        """Whether G6 made this folder a workspace (its marker is there)."""
        return bookkeeping_path(self.bookkeeping, MARKER).is_file()

    # --- paths --------------------------------------------------------------------------------------------------------

    def contained(self, path: str | os.PathLike[str]) -> Path:
        """Where ``path`` really is, resolved against the folder; :class:`WorkspaceRefused` when it leaves the folder
        (``OUTSIDE_ROOT``), names G6's bookkeeping (``RESERVED``) or is not a path G6 resolves (``INPUT_INVALID``)."""
        raw_parts = os.fspath(path).replace("\\", "/").split("/")
        if any(part.lower().startswith(".partial-") for part in raw_parts):
            raise WorkspaceRefused(RESERVED, "the .partial- prefix is reserved for G6 temporary files")
        try:
            real = contained(self.root, path)
        except PathRefused as exc:
            raise WorkspaceRefused(OUTSIDE_ROOT if exc.outside else INPUT_INVALID, exc.reason) from None
        parts = real.relative_to(self.root).parts
        if any(part.lower().startswith(".partial-") for part in parts):
            raise WorkspaceRefused(RESERVED, "the .partial- prefix is reserved for G6 temporary files")
        if parts and parts[0].lower() == BOOKKEEPING:
            raise WorkspaceRefused(RESERVED, f"{os.fspath(path)!r} is inside G6's own bookkeeping folder ({BOOKKEEPING})")
        return real

    def relative(self, real: Path) -> str:
        return real.relative_to(self.root).as_posix() or "."

    # --- reading ------------------------------------------------------------------------------------------------------

    def read(self, path: str | os.PathLike[str], *, max_bytes: int = MAX_FILE_BYTES) -> bytes:
        """The bytes of a plain file in the workspace. A hard link, a file that is not regular, one larger than
        ``max_bytes`` and one that changed between the check and the read are refused."""
        real = self.contained(path)
        rel = self.relative(real)
        try:
            found = plain_file(real)
        except PathRefused as exc:
            code = NOT_FOUND if not os.path.lexists(real) else NOT_A_FILE
            raise WorkspaceRefused(code, f"{rel}: {exc.reason}") from None
        if found.st_size > max_bytes:
            raise WorkspaceRefused(RESOURCE_LIMIT, f"{rel} is {found.st_size} bytes, over the {max_bytes} read here")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(real, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_ino, opened.st_dev) != (found.st_ino, found.st_dev) or opened.st_nlink > 1:
                raise WorkspaceRefused(NOT_A_FILE, f"{rel} changed between the check and the read")
            return handle.read(max_bytes)

    def _entries(self) -> tuple[list[tuple[str, Path, os.stat_result]], list[dict[str, str]]]:
        """The plain files (path, real path, status) and what was skipped (links, hard links, other kinds), sorted;
        G6's bookkeeping is left out, and no link is followed."""
        files: list[tuple[str, Path, os.stat_result]] = []
        skipped: list[dict[str, str]] = []
        stack = [self.root] if self.root.is_dir() else []
        while stack:
            folder = stack.pop()
            try:
                with os.scandir(folder) as listing:
                    names = [entry.name for entry in listing]
            except FileNotFoundError:
                continue                                    # removed while it was listed
            for entry_name in names:
                path = folder / entry_name
                if folder == self.root and entry_name.lower() == BOOKKEEPING or entry_name.lower().startswith(".partial-"):
                    continue                                # G6's own, or a write still in progress
                try:
                    found = os.lstat(path)  # not DirEntry.stat: on Windows it leaves st_nlink at zero
                except FileNotFoundError:
                    continue
                rel = self.relative(path)
                if _linked(found):
                    skipped.append({"path": rel, "reason": "a link (symbolic or a junction): never followed"})
                elif stat.S_ISDIR(found.st_mode):
                    stack.append(path)
                elif not stat.S_ISREG(found.st_mode):
                    skipped.append({"path": rel, "reason": "not a regular file"})
                elif found.st_nlink > 1:
                    skipped.append({"path": rel, "reason": "a hard link, a second name for a file kept elsewhere"})
                else:
                    files.append((rel, path, found))
        return sorted(files, key=lambda f: f[0]), sorted(skipped, key=lambda s: s["path"])

    def files(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Each plain file's path, size and SHA-256, and what was skipped and why."""
        found, skipped = self._entries()
        return [{"path": rel, "bytes": st.st_size, "sha256": sha256_file(path)} for rel, path, st in found], skipped

    def backups(self) -> list[dict[str, Any]]:
        folder = bookkeeping_path(self.bookkeeping, BACKUPS)
        if not folder.is_dir():
            return []
        return [{"backup": entry.name, "bytes": entry.stat(follow_symlinks=False).st_size}
                for entry in sorted(os.scandir(folder), key=lambda e: e.name)
                if entry.name.endswith(".tar.gz") and entry.is_file(follow_symlinks=False)]

    def status(self) -> dict[str, Any]:
        """What is there: whether the folder exists and G6 made it a workspace, its files and bytes, whether it holds a
        ``.git`` folder (looked at, never asked), and its backups."""
        found, skipped = self._entries()
        return {"exists": self.root.is_dir(), "workspace": self.is_workspace(), "files": len(found),
                "bytes": sum(st.st_size for _, _, st in found), "skipped": len(skipped),
                "git": os.path.lexists(self.root / ".git"), "backups": self.backups(),
                "locks": self.coordination.live_locks()}

    # --- writing ------------------------------------------------------------------------------------------------------

    def _current(self, real: Path, rel: str) -> tuple[str | None, int]:
        """The SHA-256 and size of what ``real`` holds now (``None`` and 0 when nothing is there). A folder is refused;
        a hard link is not hashed (its bytes belong to a file kept elsewhere too)."""
        try:
            found = os.lstat(real)
        except FileNotFoundError:
            return None, 0
        if stat.S_ISDIR(found.st_mode) or not stat.S_ISREG(found.st_mode):
            raise WorkspaceRefused(NOT_A_FILE, f"{rel} is a folder or not a regular file")
        if found.st_nlink > 1:
            return "", found.st_size
        return sha256_file(real), found.st_size

    def _size(self, real: Path, rel: str) -> int:
        """The size of what ``real`` holds now (0 when nothing is there), read without opening it; a folder is
        refused."""
        try:
            found = os.lstat(real)
        except FileNotFoundError:
            return 0
        if not stat.S_ISREG(found.st_mode):
            raise WorkspaceRefused(NOT_A_FILE, f"{rel} is a folder or not a regular file")
        return found.st_size

    def _mark(self) -> None:
        """Make the folder a workspace: the folder, G6's bookkeeping folder and the marker saying G6 made it."""
        folder = bookkeeping_path(self.root / BOOKKEEPING, create=True)
        marker = bookkeeping_path(folder, MARKER)
        if not marker.exists():
            marker.write_text(json.dumps({"made_by": "g6.workspace", "created_at": _now()}) + "\n", encoding="utf-8")

    def _total(self) -> int:
        return sum(st.st_size for _, _, st in self._entries()[0])

    def _admit(self, rel: str, real: Path, expect: str | None | EllipsisType, token: str, holder: str, *,
               record: bool) -> str | None:
        """What ``real`` holds now (its SHA-256, ``None`` when nothing is there), once the write may go ahead: a live
        lock on the path held by another (no matching ``token``) refuses it (``LOCKED``), and so does a file that is
        not the one the writer read (``STALE_WRITE``, with both hashes; ``expect`` ``None`` means "nothing there yet",
        ``...`` means no expectation). With ``record`` (a real write, under the mutex) each refusal is logged."""
        lock = self.coordination.record(rel)
        if self.coordination.live(lock) and not self.coordination.holds(lock, token):
            by = lock.get("claimed_holder", "") if lock else ""
            if record:
                self.coordination.log({"kind": "locked_write", "path": rel, "claimed_holder": holder,
                                       "locked_by": by})
            raise WorkspaceRefused(LOCKED, f"{rel} is locked by {by or 'an unreadable lock'!r} (their word); the write "
                                           "was refused", locked_by=by)
        current = self._current(real, rel)[0]
        if expect is not ... and current != expect:
            if record:
                self.coordination.log({"kind": "stale_write", "path": rel, "claimed_holder": holder,
                                       "expected_sha256": expect, "current_sha256": current})
            said = "nothing" if current is None else current
            raise WorkspaceRefused(STALE_WRITE, f"{rel} is not the version the writer read: it expected "
                                                f"{'nothing there' if expect is None else expect}, and it holds {said}; "
                                                "nothing was written", expected_sha256=expect, current_sha256=current)
        return current

    def write(self, path: str | os.PathLike[str], data: str | bytes, *, expect_sha256: str | None | EllipsisType = ...,
              token: str = "", holder: str = "", dry_run: bool = True) -> dict[str, Any]:
        """Write ``data`` (text is written as UTF-8) to ``path`` in the workspace: a dry run (nothing written) unless
        ``dry_run`` is false. Says the file's SHA-256, the one it replaces (``None`` when it is new) and, once written,
        the SHA-256 read back.

        Refused when the path leaves the workspace, the file is over the size limit or the workspace would be over its
        limit; when another holds a live lock on the path (``token`` is the lock's, if the writer took it); and, given
        ``expect_sha256``, when the file is not that version (``None``: when a file is there at all). The check and the
        replace happen under the workspace's short exclusive lock, so of two writers that read the same version only
        the first is written; the second is refused with both hashes and logged (``holder`` is recorded as the
        writer's word)."""
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        if expect_sha256 is not ... and expect_sha256 is not None and not _SHA256.fullmatch(str(expect_sha256)):
            raise WorkspaceRefused(INPUT_INVALID, "expect_sha256 is the 64-character SHA-256 a read gave, or null for "
                                                  "a file not there yet")
        real = self.contained(path)
        rel = self.relative(real)
        if real == self.root:
            raise WorkspaceRefused(INPUT_INVALID, "the path names the workspace folder itself, not a file in it")
        if len(payload) > MAX_FILE_BYTES:
            raise WorkspaceRefused(RESOURCE_LIMIT, f"{rel}: {len(payload)} bytes is over the {MAX_FILE_BYTES} a file "
                                                   "may be")
        if self._total() - self._size(real, rel) + len(payload) > self.max_bytes:
            raise WorkspaceRefused(RESOURCE_LIMIT, f"writing {rel} would put the workspace over its {self.max_bytes} "
                                                   "bytes")
        out: dict[str, Any] = {"path": rel, "bytes": len(payload), "sha256": sha256_bytes(payload),
                               "replaces": None, "dry_run": dry_run, "written": False}
        if dry_run:
            out["replaces"] = self._admit(rel, real, expect_sha256, token, holder, record=False)
            return out
        self._mark()
        real.parent.mkdir(parents=True, exist_ok=True)
        folder = self.contained(real.parent)                 # the folders made are checked again
        with self.coordination.mutex():
            if self._total() - self._size(real, rel) + len(payload) > self.max_bytes:
                raise WorkspaceRefused(RESOURCE_LIMIT, "the write would exceed the workspace byte limit")
            out["replaces"] = self._admit(rel, real, expect_sha256, token, holder, record=True)
            handle, temp = tempfile.mkstemp(prefix=".partial-", dir=str(folder))
            try:
                opened = Path(temp).resolve(strict=True)
                if opened.parent != folder or self.root not in opened.parents:
                    raise WorkspaceRefused(OUTSIDE_ROOT, f"{rel}: its folder was swapped for a link out of the "
                                                         "workspace after the check; nothing was written")
                with os.fdopen(handle, "wb") as stream:
                    handle = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                _replace(opened, folder / real.name)
            except BaseException:
                if handle != -1:
                    os.close(handle)
                Path(temp).unlink(missing_ok=True)
                raise
            out.update(written=True, read_back=sha256_file(folder / real.name))
        return out

    # --- locks (shared_workspace) -------------------------------------------------------------------------------------

    def lock(self, path: str | os.PathLike[str], holder: str, ttl: float = 60.0, *, token: str = "") -> dict[str, Any]:
        """Lock ``path`` for ``ttl`` seconds, or renew a lock with its ``token``. ``holder`` is recorded as the caller's
        word; the reply's ``token`` is what writes through the lock and releases it. A live lock held by another is
        refused (``LOCKED``); one past its time-to-live is taken over, and the takeover is logged (``took_over``)."""
        return self.coordination.take(self.relative(self._lockable(path)), holder, ttl, token)

    def unlock(self, path: str | os.PathLike[str], token: str) -> dict[str, Any]:
        """Release the lock on ``path`` with its token; a live lock held by another is refused (``LOCKED``)."""
        return self.coordination.release(self.relative(self._lockable(path)), token)

    def _lockable(self, path: str | os.PathLike[str]) -> Path:
        real = self.contained(path)
        if real == self.root:
            raise WorkspaceRefused(INPUT_INVALID, "a lock is on a file's path, not on the workspace folder")
        return real

    def locks(self) -> list[dict[str, Any]]:
        return self.coordination.locks()

    def conflicts(self) -> dict[str, Any]:
        """The log of refused writes (a stale version, a lock held by another) and of takeovers, oldest first."""
        entries, unreadable = self.coordination.entries()
        refused = [e for e in entries if e.get("kind") in ("stale_write", "locked_write")]
        return {"conflicts": refused, "takeovers": [e for e in entries if e.get("kind") == "takeover"],
                "unreadable_lines": unreadable}

    def _unlocked(self, what: str) -> None:
        held = self.coordination.live_locks()
        if held:
            names = ", ".join(f"{h['path']} ({h['claimed_holder'] or 'unreadable'})" for h in held[:5])
            raise WorkspaceRefused(LOCKED, f"{len(held)} path(s) are locked ({names}); a {what} would change them",
                                   locked=[h["path"] for h in held])

    # --- backups, restores and clean-up -------------------------------------------------------------------------------

    def backup(self, *, dry_run: bool = True) -> dict[str, Any]:
        """A gzip tar archive of the workspace's plain files in its backups folder (links are skipped and named): a dry
        run unless ``dry_run`` is false. Says the archive's name, its members, its size and its SHA-256."""
        if not self.root.is_dir():
            raise WorkspaceRefused(NOT_FOUND, f"there is no workspace folder at {self.label}")
        found, skipped = self._entries()
        out: dict[str, Any] = {"backup": "", "members": len(found), "bytes": sum(st.st_size for _, _, st in found),
                               "skipped": skipped, "dry_run": dry_run, "written": False}
        if dry_run:
            return out
        self._mark()
        folder = bookkeeping_path(self.bookkeeping, BACKUPS, create=True)
        name = f"backup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}.tar.gz"
        handle, temp = tempfile.mkstemp(prefix=".partial-", dir=str(folder))
        os.close(handle)
        try:
            with tarfile.open(temp, "w:gz") as tar:
                for rel, path, enumerated in found:
                    with open(path, "rb") as source:
                        now = os.fstat(source.fileno())
                        if (now.st_dev, now.st_ino) != (enumerated.st_dev, enumerated.st_ino) \
                                or now.st_nlink > 1 or not stat.S_ISREG(now.st_mode):
                            raise WorkspaceRefused(NOT_A_FILE, f"{rel} became a link while it was backed up")
                        info = tarfile.TarInfo(rel)
                        info.size, info.mtime, info.mode = now.st_size, int(now.st_mtime), 0o644
                        tar.addfile(info, source)
            os.replace(temp, folder / name)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        archive = folder / name
        out.update(backup=name, bytes=archive.stat().st_size, sha256=sha256_file(archive), written=True)
        return out

    def _archive(self, name: str) -> Path:
        path = bookkeeping_path(self.bookkeeping, BACKUPS) / check_backup(name)
        try:
            contained(self.root, path, refuse_links=True)
            plain_file(path)
        except PathRefused as exc:
            code = NOT_FOUND if not os.path.lexists(path) else NOT_A_FILE
            raise WorkspaceRefused(code, f"backup {name}: {exc.reason}") from None
        if path.stat().st_size > self.max_bytes:
            raise WorkspaceRefused(RESOURCE_LIMIT, f"backup {name} is larger than the workspace may hold")
        return path

    def _members(self, archive: Path, name: str, destination: Path) -> tuple[int, int]:
        """Every member checked with ``tarfile``'s ``data`` filter against ``destination`` before anything is
        extracted, and the archive's count and size held to the workspace's limits: (members, bytes)."""
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = tar.getmembers()
                if len(members) > MAX_MEMBERS:
                    raise WorkspaceRefused(RESOURCE_LIMIT, f"backup {name} has over {MAX_MEMBERS} members")
                total = sum(m.size for m in members if m.isreg())
                if total > self.max_bytes:
                    raise WorkspaceRefused(RESOURCE_LIMIT, f"backup {name} would hold over {self.max_bytes} bytes")
                for member in members:
                    if member.name.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", member.name):
                        # the data filter would strip the slash and extract it inside; an archive G6 made never has one
                        raise WorkspaceRefused(ARCHIVE_REFUSED, f"backup {name} has a member with an absolute name, "
                                                                f"{member.name!r}; nothing was changed")
                    tarfile.data_filter(member, str(destination))
                    normalised = posixpath.normpath(member.name.replace("\\", "/"))
                    parts = normalised.split("/")
                    if parts[0].lower() == BOOKKEEPING or any(p.lower().startswith(".partial-") for p in parts):
                        raise WorkspaceRefused(ARCHIVE_REFUSED, f"backup {name} writes into G6's bookkeeping folder")
        except tarfile.FilterError as exc:
            raise WorkspaceRefused(ARCHIVE_REFUSED, f"backup {name}: {exc}; nothing was changed") from None
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise WorkspaceRefused(ARCHIVE_REFUSED, f"backup {name} cannot be read as a gzip tar archive "
                                                    f"({type(exc).__name__}); nothing was changed") from None
        return len(members), total

    def restore(self, name: str, *, dry_run: bool = True) -> dict[str, Any]:
        """Put a backup's files back in place of the workspace's: a dry run unless ``dry_run`` is false. Only a backup
        kept in the workspace's backups folder is taken; every member is checked with the ``data`` filter before
        anything changes; what is replaced is backed up first (``previous_backup``), so a restore can be undone."""
        if not self.root.is_dir():
            raise WorkspaceRefused(NOT_FOUND, f"there is no workspace folder at {self.label}")
        if not self.is_workspace():
            raise WorkspaceRefused(NOT_A_WORKSPACE, f"{self.label} is not a folder G6 made a workspace")
        archive = self._archive(name)
        staging = bookkeeping_path(self.bookkeeping, f"staging-{secrets.token_hex(4)}")
        members, total = self._members(archive, name, staging)
        out: dict[str, Any] = {"backup": name, "members": members, "bytes": total,
                               "replaces": len(self._entries()[0]), "dry_run": dry_run, "restored": False,
                               "previous_backup": ""}
        if dry_run:
            self._unlocked("restore")
            return out
        with self.coordination.mutex():
            self._unlocked("restore")
            return self._restore_locked(archive, staging, name, out)

    def _restore_locked(self, archive: Path, staging: Path, name: str, out: dict[str, Any]) -> dict[str, Any]:
        staging.mkdir()
        try:
            try:
                with tarfile.open(archive, "r:gz") as tar:
                    tar.extractall(staging, filter="data")
            except (tarfile.TarError, EOFError) as exc:
                raise WorkspaceRefused(ARCHIVE_REFUSED, f"backup {name} could not be extracted ({exc}); nothing was "
                                                        "changed") from None
            if out["replaces"]:
                out["previous_backup"] = self.backup(dry_run=False)["backup"]
            root_stat = os.lstat(self.root)
            for entry_name in _checked_listing(self.root, root_stat):
                if entry_name.lower() != BOOKKEEPING:
                    _remove(self.root / entry_name, ((self.root, root_stat),))
            for entry in list(os.scandir(staging)):
                _replace(Path(entry.path), self.root / entry.name)
        finally:
            if staging.exists():
                _remove(staging)
        out["restored"] = True
        return out

    def cleanup(self, *, dry_run: bool = True) -> dict[str, Any]:
        """Remove the workspace folder, its backups with it: a dry run unless ``dry_run`` is false. Only a folder G6
        made a workspace is removed; a link in it is removed itself, never followed."""
        if not self.root.exists():
            raise WorkspaceRefused(NOT_FOUND, f"there is no workspace folder at {self.label}")
        if not self.is_workspace():
            raise WorkspaceRefused(NOT_A_WORKSPACE, f"{self.label} is not a folder G6 made a workspace, so it is not "
                                                    "removed")
        found, skipped = self._entries()
        out: dict[str, Any] = {"files": len(found), "bytes": sum(st.st_size for _, _, st in found),
                               "links": len(skipped), "backups": len(self.backups()), "dry_run": dry_run,
                               "removed": False}
        if dry_run:
            self._unlocked("clean-up")
            return out
        with self.coordination.mutex():
            self._unlocked("clean-up")
            _remove(self.root)
        out["removed"] = True
        return out


__all__ = ["BACKUPS", "BOOKKEEPING", "MARKER", "MAX_FILE_BYTES", "MAX_MEMBERS", "MAX_WORKSPACE_BYTES", "Workspace",
           "check_backup", "check_task", "sha256_bytes", "sha256_file"]
