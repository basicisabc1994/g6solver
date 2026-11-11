# SPDX-License-Identifier: Apache-2.0
"""The one reader for what a check stands on: a file's bytes and a folder's listing, read so that any failure at all
raises ``Unreadable`` and nothing ever stands in for what could not be read.

A digest of a file that could not be read, or a listing that skipped a folder it could not open, would say the bytes are
what they were when nobody read them. So a read that fails, for whatever reason (a file another process holds without
sharing, a byte-range lock, a denied folder, a file removed while it was read, an error never seen before), raises, and
the caller decides what an unreadable reading means. Each root is listed once, by the one lister (``walk``): it takes
names from a folder's listing and nothing else, and decides what each entry is by a stat of its own that raises
(``asked``), never by a predicate that answers "no" for an entry it could not ask, the listing's own entries' among
them, which the standard library's walk reads as "not a folder" when they fail. A folder it goes into is listed whole
or raises, and a selection is made from that listing, never by listing again. The sealed split
(``g6.core.vault``), the integrity manifest
(``g6.integrity.build_manifest``) and the loop's freeze (``g6.metaharness.loop``) all read through here.
"""
from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path


class Unreadable(OSError):
    """A file or folder a check stands on could not be read or listed whole: no value stands for it."""


def said(path: object) -> str:
    """A path as a message can carry it: a name the file system gives that is no text (a lone surrogate) spelled out."""
    return str(path).encode("utf-8", "backslashreplace").decode("utf-8")


def sha256_of(path: str | os.PathLike[str]) -> str:
    """The SHA-256 of a file's bytes; ``Unreadable`` on any failure at all."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception as exc:  # noqa: BLE001 - every failure to read is unreadable, never a digest
        raise Unreadable(f"{said(path)} cannot be read ({type(exc).__name__})") from exc


def asked(path: str | os.PathLike[str], *, follow: bool = False, absent: bool = False) -> os.stat_result | None:
    """What ``path`` is, by a stat that raises: of the path itself (``os.lstat``), or of what a link there names when
    ``follow`` (``os.stat``). Any failure at all raises ``Unreadable``, an entry gone since it was listed among them, never
    a "no"; only with ``absent`` is a path that does not exist ``None``, for a root a caller compares by its absence."""
    try:
        return os.stat(path) if follow else os.lstat(path)
    except Exception as exc:  # noqa: BLE001 - an entry that cannot be asked is unreadable, never a "no"
        if absent and isinstance(exc, FileNotFoundError):
            return None
        raise Unreadable(f"{said(path)} cannot be read ({type(exc).__name__})") from exc


def linked(info: os.stat_result) -> bool:
    """Whether an entry, as ``asked`` found it, is a link or a junction: what it points at is no part of its folder."""
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT


def walk(root: str | os.PathLike[str], *, skipped: list[str] | None = None) -> Iterator[tuple[str, list[str], list[str]]]:
    """The one lister: ``root`` and every folder under it, top down, each listed once, as ``(folder, folders, others)``
    by name. A listing gives names and nothing else: what each entry is, is asked of the entry itself by a stat that
    raises (``os.lstat``), so no answer is ever "not a folder" because the entry could not be asked. ``folders`` are
    what that stat finds a folder, a junction among them, as it is one to the file system; a symbolic link is among
    ``others`` whatever it names, and is never gone into. A caller that takes a name out of ``folders`` keeps the walk
    out of it, as the loop's listings keep it out of a junction (``_listing``, ``_links_under``). A folder is asked
    again before the walk goes into it, and one that is a folder no more was changed while it was listed.

    A folder that cannot be listed, an entry that cannot be asked or is gone since its folder was listed, and a folder
    changed before it was gone into raise ``Unreadable``, whatever the error, one never seen before among them; or, with
    ``skipped``, each is named there and left out, and the walk goes on, for a caller that must see all else the
    listing holds. A root that does not exist lists nothing, as ``files_under`` holds none for it."""
    try:
        os.lstat(root)
    except Exception as exc:  # noqa: BLE001 - absence lists nothing; a root that cannot be asked is the walk's to refuse
        if isinstance(exc, FileNotFoundError):
            return

    def refused(path: str, error: BaseException, what: str) -> None:
        if skipped is None:
            raise Unreadable(f"{said(path)} cannot be {what} ({type(error).__name__})") from error
        skipped.append(said(path))

    def folder(path: str) -> bool | None:
        """Whether ``path`` is a folder, by its own stat; ``None``, refused, when it cannot be asked."""
        try:
            return stat.S_ISDIR(os.lstat(path).st_mode)
        except Exception as exc:  # noqa: BLE001 - an entry that cannot be asked is unreadable, never "not a folder"
            refused(path, exc, "read")
            return None

    pending = [os.fspath(root)]
    while pending:
        here = pending.pop()
        try:
            with os.scandir(here) as listing:
                names = [entry.name for entry in listing]
        except Exception as exc:  # noqa: BLE001 - whatever keeps a folder from being listed whole, it was not listed
            refused(here, exc, "listed")
            continue
        kinds = {name: folder(os.path.join(here, name)) for name in names}
        folders = [name for name in names if kinds[name]]
        yield here, folders, [name for name in names if kinds[name] is False]
        for name in reversed(folders):                # the caller's, by now: what it took out is not gone into
            path = os.path.join(here, name)
            still = folder(path)
            if still:
                pending.append(path)
            elif still is False:
                refused(path, NotADirectoryError(path), "listed, as it was changed while its folder was")


def files_under(root: str | os.PathLike[str]) -> list[Path]:
    """Every regular file at or under ``root``, sorted, from one listing: the root itself when it is a file, and otherwise
    every entry the walk lists under it that a stat that raises (``asked``, following a link) finds a regular file, a link
    to a file counting as the file it names and a link to a folder below the root not followed. A root that does not
    exist holds none, so a caller compares its absence; any other failure to list a folder or to ask what an entry is,
    an entry gone since its folder was listed among them, raises ``Unreadable``."""
    base = Path(root)
    if asked(base, absent=True) is None:
        return []
    kind = asked(base, follow=True).st_mode
    if stat.S_ISREG(kind):
        return [base]
    if not stat.S_ISDIR(kind):
        return []
    found = []
    for here, _, files in walk(base):
        for name in files:
            path = Path(here) / name
            if stat.S_ISREG(asked(path, follow=True).st_mode):
                found.append(path)
    return sorted(found)


__all__ = ["Unreadable", "asked", "files_under", "linked", "said", "sha256_of", "walk"]
