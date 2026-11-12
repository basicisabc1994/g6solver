# SPDX-License-Identifier: Apache-2.0
"""The one reader for what a check stands on: a file's bytes and a folder's listing, read so that any failure at all
raises ``Unreadable`` and nothing ever stands in for what could not be read.

A digest of a file that could not be read, or a listing that skipped a folder it could not open, would say the bytes are
what they were when nobody read them. So a read that fails, for whatever reason (a file another process holds without
sharing, a byte-range lock, a denied folder, a file removed while it was read, an error never seen before), raises, and
the caller decides what an unreadable reading means. Each root is listed once, by the one lister (``walk``): it takes
names from a folder's listing and nothing else, and decides what each entry is by a stat of its own that raises
(``asked``), never by a predicate that answers "no" for an entry it could not ask, the listing's own entries' among
them, which the standard library's walk reads as "not a folder" when they fail. Every stat, open and listing of a
listed entry is of the very entry its names spell (``exact``), never of what the platform rereads those names as, and
an entry has one answer: the lister hands on what its stat found, and a later ask that finds another entry was of a
listing changed meanwhile (``same``). A folder it goes into is listed whole or raises, and a selection is made from
that listing, never by listing again. The sealed split
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


def exact(path: str | os.PathLike[str]) -> str:
    """The one rule for a name the platform rereads: ``path`` as the platform opens the very entry its names spell.
    Windows rereads some names before it opens them (a last name ``nul`` is its null device, and dots and spaces are
    dropped from the end of a name), so a stat or an open of such a path answers for another entry, or for none. Where
    the platform reads ``path`` as itself it is handed back as it was given, so ``exact(path) == os.fspath(path)`` says
    exactly that, for a caller that would refuse such a name; otherwise the form the platform does not reread is handed
    back (``\\\\?\\`` and the path spelled out whole, from the folder the process is in when it is relative), for a
    caller that must still read the entry. The platform itself is asked how it reads the path (``GetFullPathNameW``,
    through ``os.path.abspath``), so no list of names is kept here. Elsewhere than on Windows a path is read as spelled.
    A path already in a form nothing rereads is left as it is."""
    plain = os.fspath(path)
    if os.name != "nt" or plain.startswith(("\\\\?\\", "\\\\.\\")):
        return plain
    spelled = os.path.normpath(os.path.join(os.getcwd(), plain))
    if os.path.abspath(plain) == spelled:
        return plain
    return "\\\\?\\UNC" + spelled[1:] if spelled.startswith("\\\\") else "\\\\?\\" + spelled


def _a_callers(root: str | os.PathLike[str]) -> None:
    """A root is a caller's name, not an entry a listing named: ordinary code reaches it as the platform reads it. One
    the platform rereads names two things at once, the entry so spelled and the entry the platform opens, so no reading
    stands on it: ``Unreadable``."""
    try:
        reread = exact(root) != os.fspath(root)
    except Exception as exc:  # noqa: BLE001 - a root whose reading cannot be asked is unreadable, never taken as read
        raise Unreadable(f"{said(root)} cannot be read ({type(exc).__name__})") from exc
    if reread:
        raise Unreadable(f"{said(root)} cannot be read (the platform reads this root's name as another's)")


def sha256_of(path: str | os.PathLike[str]) -> str:
    """The SHA-256 of a file's bytes, of the very entry ``path`` spells (``exact``); ``Unreadable`` on any failure at
    all."""
    try:
        return hashlib.sha256(Path(exact(path)).read_bytes()).hexdigest()
    except Exception as exc:  # noqa: BLE001 - every failure to read is unreadable, never a digest
        raise Unreadable(f"{said(path)} cannot be read ({type(exc).__name__})") from exc


def asked(path: str | os.PathLike[str], *, follow: bool = False, absent: bool = False) -> os.stat_result | None:
    """What ``path`` is, by a stat that raises, of the very entry it spells (``exact``): of the path itself
    (``os.lstat``), or of what a link there names when ``follow`` (``os.stat``). Any failure at all raises
    ``Unreadable``, an entry gone since it was listed among them, never a "no"; only with ``absent`` is a path that does
    not exist ``None``, for a root a caller compares by its absence, and only when the platform reads that path as
    itself: where it reads it as another's, no entry spelled so says nothing of the entry it would open."""
    try:
        return os.stat(exact(path)) if follow else os.lstat(exact(path))
    except Exception as exc:  # noqa: BLE001 - an entry that cannot be asked is unreadable, never a "no"
        if absent and isinstance(exc, FileNotFoundError) and exact(path) == os.fspath(path):
            return None
        raise Unreadable(f"{said(path)} cannot be read ({type(exc).__name__})") from exc


def linked(info: os.stat_result) -> bool:
    """Whether an entry, as ``asked`` found it, is a link or a junction: what it points at is no part of its folder."""
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT


def same(first: os.stat_result, now: os.stat_result) -> bool:
    """Whether two answers are of one entry: the same kind of thing, a link or a junction in both or in neither, and
    the identity the file system gives it. An entry asked twice has one answer, or it was changed between the two."""
    return (stat.S_IFMT(first.st_mode) == stat.S_IFMT(now.st_mode) and linked(first) == linked(now)
            and (first.st_dev, first.st_ino) == (now.st_dev, now.st_ino))


class Listed(str):
    """A name as the one lister found it: the name, and with it what its entry's own stat answered (``info``), so
    whoever asks that entry again compares the two answers and never decides afresh."""

    info: os.stat_result

    def __new__(cls, name: str, info: os.stat_result) -> Listed:
        found = super().__new__(cls, name)
        found.info = info
        return found


def walk(root: str | os.PathLike[str], *, skipped: list[str] | None = None) -> Iterator[tuple[str, list[str], list[str]]]:
    """The one lister: ``root`` and every folder under it, top down, each listed once, as ``(folder, folders, others)``
    by name. A listing gives names and nothing else: what each entry is, is asked of the entry itself, the very entry
    the listing named (``exact``), by a stat that raises (``os.lstat``), so no answer is ever "not a folder" because the
    entry could not be asked, or because the platform read its name as another's. That answer is handed on with the
    name (``Listed``). ``folders`` are what the stat finds a folder, a junction among them, as it is one to the file
    system; a symbolic link is among ``others`` whatever it names, and is never gone into. A caller that takes a name
    out of ``folders`` keeps the walk out of it, as the loop's listings keep it out of a junction (``_listing``,
    ``_links_under``). A folder is asked again before the walk goes into it, and it must be the entry the first answer
    was of (``same``): one that is another folder, a junction or a link by then, or no folder, was changed while it was
    listed.

    A folder that cannot be listed, an entry that cannot be asked or is gone since its folder was listed, a folder
    changed before it was gone into, and a root whose own name the platform rereads raise ``Unreadable``, whatever the
    error, one never seen before among them; or, with ``skipped``, each is named there and left out, and the walk goes
    on, for a caller that must see all else the listing holds. A root that does not exist lists nothing, as
    ``files_under`` holds none for it."""
    def refused(path: str, error: BaseException, what: str) -> None:
        if skipped is None:
            raise Unreadable(f"{said(path)} cannot be {what} ({type(error).__name__})") from error
        skipped.append(said(path))

    def found(path: str) -> os.stat_result | None:
        """What the entry ``path`` spells is, by its own stat; ``None``, refused, when it cannot be asked."""
        try:
            return os.lstat(exact(path))
        except Exception as exc:  # noqa: BLE001 - an entry that cannot be asked is unreadable, never "not a folder"
            refused(path, exc, "read")
            return None

    top = os.fspath(root)
    try:
        _a_callers(top)
        os.lstat(exact(top))
    except Exception as exc:  # noqa: BLE001 - absence lists nothing; a root that cannot be asked is refused
        if not isinstance(exc, FileNotFoundError):
            refused(top, exc, "listed")
        return
    pending = [top]
    while pending:
        here = pending.pop()
        try:
            with os.scandir(exact(here)) as listing:
                names = [entry.name for entry in listing]
        except Exception as exc:  # noqa: BLE001 - whatever keeps a folder from being listed whole, it was not listed
            refused(here, exc, "listed")
            continue
        kinds = {name: found(os.path.join(here, name)) for name in names}
        entries = [Listed(name, kinds[name]) for name in names if kinds[name] is not None]
        folders = [name for name in entries if stat.S_ISDIR(name.info.st_mode)]
        yield here, folders, [name for name in entries if not stat.S_ISDIR(name.info.st_mode)]
        for name in reversed(folders):                # the caller's, by now: what it took out is not gone into
            path = os.path.join(here, name)
            now, first = found(path), kinds.get(name)
            if now is None:
                continue
            if stat.S_ISDIR(now.st_mode) and (first is None or same(first, now)):
                pending.append(path)
            else:
                refused(path, NotADirectoryError(path), "listed, as it was changed while its folder was")


def files_under(root: str | os.PathLike[str]) -> list[Path]:
    """Every regular file at or under ``root``, sorted, from one listing: the root itself when it is a file, and otherwise
    every entry the walk lists under it that a stat that raises (``asked``, following a link) finds a regular file, a link
    to a file counting as the file it names and a link to a folder below the root not followed. An entry has one answer:
    what the lister's stat found of it is compared with this one (``same``), and an entry that is no link and is another
    entry by now, a folder where a file was among them, was changed while its folder was listed. A root that does not
    exist holds none, so a caller compares its absence; any other failure to list a folder or to ask what an entry is,
    an entry gone or changed since its folder was listed and a root whose own name the platform rereads among them,
    raises ``Unreadable``."""
    base = Path(root)
    _a_callers(base)
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
            now, first = asked(path, follow=True), getattr(name, "info", None)
            if first is not None and not stat.S_ISLNK(first.st_mode) and not same(first, now):
                raise Unreadable(f"{said(path)} cannot be read (it was changed while its folder was listed)")
            if stat.S_ISREG(now.st_mode):
                found.append(path)
    return sorted(found)

__all__ = ["Listed", "Unreadable", "asked", "exact", "files_under", "linked", "said", "same", "sha256_of", "walk"]
