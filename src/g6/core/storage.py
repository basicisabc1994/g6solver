# SPDX-License-Identifier: Apache-2.0
"""One root for durable state: ``$G6_HOME`` (default ``~/.g6``).

Every component that persists anything opens it through :func:`open_db` or :func:`state_path`,
so state is inspectable, portable and never scattered relative to the working directory.
Nothing is created until a component actually writes.

:func:`contained` and :func:`plain_file` are the one check that a path a caller names stays inside a folder (a
workspace, a declared root): resolved against the folder, ``..``, symbolic links and junctions followed, network and
device paths refused before anything is resolved, and a file to be read kept under one name only.
"""
from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

#: Names Windows keeps for devices in every folder (``CON``, ``nul.txt``): a path naming one is never a plain file.
_DEVICES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$", *(f"com{i}" for i in range(10)),
                      *(f"lpt{i}" for i in range(10))})


class PathRefused(ValueError):
    """A path that does not stay inside its folder (``outside``), or that G6 will not resolve, with the reason in
    words."""

    def __init__(self, reason: str, *, outside: bool = False) -> None:
        super().__init__(reason)
        self.reason, self.outside = reason, outside


def g6_home() -> Path:
    """The state root. ``G6_HOME`` overrides; otherwise ``~/.g6``."""
    raw = os.environ.get("G6_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".g6"


def confined_path(raw: str | os.PathLike[str], roots: tuple[Path, ...]) -> Path:
    """Where an absolute ``raw`` really is, when that is inside one of ``roots``: :func:`contained` over several
    trusted, absolute roots, so every refusal is its own. :class:`PathRefused` (a ``ValueError``) when ``raw`` is
    relative or lies in none of them; filesystem errors propagate. A concurrent change to a link can still race it."""
    text = os.fspath(raw)
    if not isinstance(text, str) or not Path(text).is_absolute():
        raise PathRefused("an absolute local path is required")
    refused = PathRefused("no root is declared", outside=True)
    for root in roots:
        try:
            return contained(root, text)
        except PathRefused as error:
            if not error.outside:
                raise
            refused = error
    raise refused


def state_path(*parts: str) -> Path:
    """A path under the state root. Relative parts may not escape it."""
    root = g6_home().resolve()
    p = root.joinpath(*parts).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"state path escapes G6_HOME: {'/'.join(parts)}")
    return p


def contained(root: str | os.PathLike[str], raw: str | os.PathLike[str], *, refuse_links: bool = False) -> Path:
    """Where ``raw`` really is, when that is ``root`` or inside it; :class:`PathRefused` otherwise.

    A relative ``raw`` is joined onto ``root``, never onto the folder G6 runs in; an absolute one is taken as given.
    Both are resolved, ``..`` and every symbolic link and junction followed, so a link out of the folder counts as
    outside. Refused before anything is resolved: an empty path; a control character; a network or device path
    (``\\\\host\\share``, ``//host/share``, ``\\\\?\\``, ``\\\\.\\``), because resolving one on Windows opens it over the
    network; a ``:`` in a relative path (a drive, or a file's alternate stream, on Windows); and a part named for a
    Windows device (``CON``, ``nul.txt``). With ``refuse_links=True``, every existing path component is checked with
    lstat before resolution; symbolic links and reparse points are refused. It reads no file's content:
    :func:`plain_file` checks a file to be read."""
    text = os.fspath(raw)
    if not isinstance(text, str) or not text.strip():
        raise PathRefused("the path is empty")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise PathRefused("the path holds a control character")
    if text[:2] in ("\\\\", "//", "\\/", "/\\"):
        raise PathRefused(f"{text!r} is a network or device path, which G6 never resolves", outside=True)
    given = Path(text)
    if not given.is_absolute() and ":" in text:
        raise PathRefused(f"{text!r} names a drive or a file's stream (':'); give a path inside the folder")
    for part in given.parts[1 if given.anchor else 0:]:
        if part.split(".", 1)[0].rstrip(" ").lower() in _DEVICES:
            raise PathRefused(f"{part!r} is a name Windows keeps for a device")
    if refuse_links:
        # Bookkeeping must not traverse even a link whose target remains inside the root.
        candidate = given if given.is_absolute() else Path(root).absolute() / given
        for ancestor in (*reversed(candidate.parents), candidate):
            try:
                found = os.lstat(ancestor)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(found.st_mode) or (getattr(found, "st_file_attributes", 0)
                                             & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise PathRefused(f"{ancestor.name!r} is a linked path component", outside=True)
    # On the words first, before anything is resolved: resolving a path outside the folder could follow a link out of
    # it (a junction to a network share is opened by resolving it)
    anchor = Path(os.path.normpath(Path(root).absolute()))
    lexical = Path(os.path.normpath(given if given.is_absolute() else anchor / given))
    if lexical != anchor and anchor not in lexical.parents:
        raise PathRefused(f"{text!r} is outside the folder (a '..' or a link out of it counts as outside)",
                          outside=True)
    base = Path(root).resolve()
    real = (given if given.is_absolute() else base / given).resolve()
    if real != base and base not in real.parents:
        raise PathRefused(f"{text!r} is outside the folder (a '..' or a link out of it counts as outside)",
                          outside=True)
    return real


def plain_file(path: str | os.PathLike[str]) -> os.stat_result:
    """``path``'s status when it is a regular file kept under this one name; :class:`PathRefused` when it is missing,
    is not a regular file, or is a link: a symbolic link, a Windows reparse point (a junction, a mount point) or a hard
    link (a second name for a file kept elsewhere, which resolving never shows)."""
    try:
        found = os.lstat(path)
    except OSError:
        raise PathRefused(f"{Path(path).name!r} is not a file there") from None
    reparse = getattr(found, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if stat.S_ISLNK(found.st_mode) or reparse or (stat.S_ISREG(found.st_mode) and found.st_nlink > 1):
        raise PathRefused(f"{Path(path).name!r} is a link (symbolic, a junction or a hard link), not a file kept here")
    if not stat.S_ISREG(found.st_mode):
        raise PathRefused(f"{Path(path).name!r} is not a regular file")
    return found


def open_db(name_or_path: str | os.PathLike[str], *, check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a SQLite database with WAL and foreign keys on.

    ``":memory:"`` opens an in-memory database. A bare name such as ``"action_gating.db"`` is
    placed under ``$G6_HOME``; an absolute path is used as given (callers that accept a
    user-supplied location pass it here unchanged).
    """
    target = str(name_or_path)
    if target != ":memory:":
        path = Path(target)
        if not path.is_absolute():
            path = state_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = str(path)
    conn = sqlite3.connect(target, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
