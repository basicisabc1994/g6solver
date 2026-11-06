# SPDX-License-Identifier: Apache-2.0
"""The one reader for what a check stands on: a file's bytes and a folder's listing, read so that any failure at all
raises ``Unreadable`` and nothing ever stands in for what could not be read.

A digest of a file that could not be read, or a listing that skipped a folder it could not open, would say the bytes are
what they were when nobody read them. So a read that fails, for whatever reason (a file another process holds without
sharing, a byte-range lock, a denied folder, a file removed while it was read, an error never seen before), raises, and
the caller decides what an unreadable reading means. Each root is listed once: a folder the listing goes into is listed
whole or raises, every other entry is decided by a stat that raises (``asked``), never by a predicate that answers "no"
for an entry it could not ask, and a selection is made from that listing, never by listing again. The sealed split
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
    """``os.walk`` of ``root``, top down and following no link, each folder listed once. A folder that cannot be listed
    raises ``Unreadable``, or, with ``skipped``, is named there and the walk goes on, for a caller that must see all else
    the listing holds. Whatever else ends the listing, an error ``os.walk`` hands no handler (one that is no ``OSError``,
    never seen before) among them, ends it the same way, the folder named, so nothing past it is taken for listed. A
    root that does not exist lists nothing, as ``files_under`` holds none for it."""
    try:
        os.lstat(root)
    except Exception as exc:  # noqa: BLE001 - absence lists nothing; a root that cannot be asked is the walk's to refuse
        if isinstance(exc, FileNotFoundError):
            return

    def refused(error: BaseException) -> None:
        name = said(getattr(error, "filename", None) or root)
        if skipped is None:
            raise Unreadable(f"{name} cannot be listed ({type(error).__name__})") from error
        skipped.append(name)

    listing = os.walk(root, followlinks=False, onerror=refused)
    while True:
        try:
            found = next(listing, None)
        except Exception as exc:  # noqa: BLE001 - whatever ends the listing early, it was not listed whole
            if isinstance(exc, Unreadable):
                raise
            refused(exc)
            return
        if found is None:
            return
        yield found


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
