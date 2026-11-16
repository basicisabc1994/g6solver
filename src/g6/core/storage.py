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
import time
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
    if os.name == "nt" and (named := _read_two_ways(Path(*parts).parts)):
        raise ValueError(f"state path escapes G6_HOME: {'/'.join(parts)} ({_TWO_WAYS.format(named)})")
    p: Path | None = root.joinpath(*parts).resolve()
    if os.name == "nt":
        # resolving while another thread deletes or makes a file or folder on the path can name a $Extend\$Deleted
        # file outside the home; then the words joined onto the home are the answer, when no link is on them
        p = _opened_inside(root, p) or _opened_inside(root, root.joinpath(*parts))
    if p is None or p != root and root not in p.parents:
        raise ValueError(f"state path escapes G6_HOME: {'/'.join(parts)}")
    return p


#: Why a name ending in a space or a dot is refused (Windows only).
_TWO_WAYS = ("{!r} ends in a space or a dot, which Windows drops from the last part of a path but keeps (a space) on "
             "a part before it, so the name is read two ways")


def _read_two_ways(parts: tuple[str, ...]) -> str | None:
    """The first of ``parts`` whose name ends in a space or a dot (Windows reads it two ways), or None."""
    return next((part for part in parts if part not in (".", "..") and part != part.rstrip(" .")), None)


def _verbatim(path: Path) -> str:
    """``path`` (absolute, as open reads it) in its ``\\\\?\\`` form: its words reach the file system as they are, so a
    trailing space or dot on its last part is not dropped."""
    text = str(path)
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def _opened_inside(root: Path, path: Path) -> Path | None:
    """Windows: ``path`` as a caller's open reads its words (a local ``\\\\?\\X:\\`` prefix dropped, ``..`` taken, a
    trailing dot dropped), when that is ``root`` or inside it, no part of it below ``root`` ends in a space or a dot,
    and lstat finds no symbolic link, junction or other reparse point on any existing part of it below ``root``; None
    otherwise. :class:`PathRefused` (not outside) for a name Windows cannot hold.

    ``Path.resolve`` follows every link it can, but a link whose target names no path yet, or none as written, stops
    it: it gives the link's words joined with the rest, and open reads them again, perhaps out of ``root`` (``..``),
    through a link (``sub.`` opens ``sub``) or into a folder a caller makes beyond a link on the way. A path resolving
    followed to the end holds no link, so a link still on the answer is refused. Each part is asked about in its
    ``\\\\?\\`` form, exactly as open goes through it: plain lstat of ``a `` would ask about ``a``."""
    text = str(path)
    if text.startswith("\\\\?\\") and text[5:7] == ":\\":
        text = text[4:]
    plain = Path(os.path.abspath(text))
    if plain != root and root not in plain.parents or _read_two_ways(plain.parts[len(root.parts):]):
        return None
    step = root
    for part in plain.parts[len(root.parts):]:
        step = step / part
        # in a folder being deleted just now lstat answers access denied (5) or a sharing violation (32) until it is
        # gone (a few ms): ask again, for up to about 50 ms, then refuse. Any other error is no such race.
        found: os.stat_result | None = None
        for _ in range(50):
            try:
                found = os.lstat(_verbatim(step))
                break
            except FileNotFoundError:
                return plain
            except OSError as error:
                if getattr(error, "winerror", None) in (123, 161, 206):  # an invalid name, path, or a name too long
                    raise PathRefused(f"{part!r} is not a name Windows can hold") from None
                if getattr(error, "winerror", None) not in (5, 32):
                    return None
                time.sleep(0.001)
        if found is None or stat.S_ISLNK(found.st_mode) or (getattr(found, "st_file_attributes", 0)
                                                            & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            return None
    return plain


def contained(root: str | os.PathLike[str], raw: str | os.PathLike[str], *, refuse_links: bool = False) -> Path:
    """Where ``raw`` really is, when that is ``root`` or inside it; :class:`PathRefused` otherwise.

    A relative ``raw`` is joined onto ``root``, never onto the folder G6 runs in; an absolute one is taken as given.
    Both are resolved, ``..`` and every symbolic link and junction followed, so a link out of the folder counts as
    outside; on Windows the answer is checked as open reads it, and a link resolving could not follow counts as outside
    too. Refused before anything is resolved: an empty path; a control character; a network or device path
    (``\\\\host\\share``, ``//host/share``, ``\\\\?\\``, ``\\\\.\\``), because resolving one on Windows opens it over the
    network; a ``:`` in a relative path (a drive, or a file's alternate stream, on Windows); a part named for a
    Windows device (``CON``, ``nul.txt``); and, on Windows, a part whose name ends in a space or a dot, which Windows
    reads two ways. With ``refuse_links=True``, every existing path component is checked with lstat before resolution
    (on Windows each as open goes through it); symbolic links and reparse points are refused. It reads no file's content:
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
    if os.name == "nt" and (named := _read_two_ways(given.parts)):
        raise PathRefused(_TWO_WAYS.format(named))
    if refuse_links:
        # Bookkeeping must not traverse even a link whose target remains inside the root.
        candidate = given if given.is_absolute() else Path(root).absolute() / given
        if os.name == "nt":                 # each part as open goes through it ('..' taken, then the words as they are)
            candidate = Path(os.path.abspath(candidate))
        for ancestor in (*reversed(candidate.parents), candidate):
            try:
                found = os.lstat(_verbatim(ancestor) if os.name == "nt" else ancestor)
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
    real: Path | None = (given if given.is_absolute() else base / given).resolve()
    if os.name == "nt":
        real = _opened_inside(base, real)  # refuses a link resolving could not follow
    if real is None or real != base and base not in real.parents:
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
