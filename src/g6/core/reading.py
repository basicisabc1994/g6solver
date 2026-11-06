# SPDX-License-Identifier: Apache-2.0
"""The one reader for what a check stands on: a file's bytes and a folder's listing, read so that any failure at all
raises ``Unreadable`` and nothing ever stands in for what could not be read.

A digest of a file that could not be read, or a listing that skipped a folder it could not open, would say the bytes are
what they were when nobody read them. So a read that fails, for whatever reason (a file another process holds without
sharing, a byte-range lock, a denied folder, a file removed while it was read, an error never seen before), raises, and
the caller decides what an unreadable reading means. The sealed split (``g6.core.vault``), the integrity manifest
(``g6.integrity.build_manifest``) and the loop's freeze (``g6.metaharness.loop``) all read through here.
"""
from __future__ import annotations

import hashlib
import os
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


def walk(root: str | os.PathLike[str], *, skipped: list[str] | None = None) -> Iterator[tuple[str, list[str], list[str]]]:
    """``os.walk`` of ``root``, top down and following no link. A folder that cannot be listed raises ``Unreadable``, or,
    with ``skipped``, is named there and the walk goes on, for a caller that must see all else the listing holds. A root
    that does not exist lists nothing, as ``files_under`` holds none for it."""
    try:
        os.lstat(root)
    except FileNotFoundError:
        return
    except Exception:  # noqa: BLE001 - a root that cannot be asked is the walk's to refuse, as a folder it cannot list
        pass

    def refused(error: OSError) -> None:
        name = said(getattr(error, "filename", None) or root)
        if skipped is None:
            raise Unreadable(f"{name} cannot be listed ({type(error).__name__})") from error
        skipped.append(name)

    yield from os.walk(root, followlinks=False, onerror=refused)


def files_under(root: str | os.PathLike[str]) -> list[Path]:
    """Every regular file at or under ``root``, sorted: the root itself when it is a file, and otherwise every file the
    walk finds under it, a link to a file counting as the file it names and a link to a folder below the root not
    followed. A root that does not exist holds none, so a caller compares its absence; any other failure to list a folder
    or to ask what an entry is raises ``Unreadable``."""
    base = Path(root)
    try:
        os.lstat(base)
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001 - a root that cannot even be asked is unreadable
        raise Unreadable(f"{said(base)} cannot be read ({type(exc).__name__})") from exc
    if _asked(base, Path.is_file):
        return [base]
    if not _asked(base, Path.is_dir):
        return []
    return sorted(Path(here) / name for here, _, files in walk(base) for name in files
                  if _asked(Path(here) / name, Path.is_file))


def _asked(path: Path, question) -> bool:  # noqa: ANN001 - a ``Path`` predicate
    """What ``question`` says of ``path``; an entry that cannot be asked is unreadable, never a "no"."""
    try:
        return question(path)
    except Exception as exc:  # noqa: BLE001 - an entry that cannot be asked is unreadable
        raise Unreadable(f"{said(path)} cannot be read ({type(exc).__name__})") from exc


__all__ = ["Unreadable", "files_under", "said", "sha256_of", "walk"]
