# SPDX-License-Identifier: Apache-2.0
"""One ownership rule for skill writers: only an unchanged, receipted G6 tree may be replaced."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath

MARKER = ".g6-installed.json"
INSTALLED = "G6_SKILL_INSTALLED"
UPDATED = "G6_SKILL_UPDATED"
KEPT = "G6_SKILL_KEPT_PERSONAL"
RECORDS = ("LICENSE", "VENDORED.md")
MISSING = "G6_SKILL_SOURCE_RECORD_MISSING"


def _plain(path: Path) -> bool:
    status = path.lstat()
    return (not status.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            if hasattr(status, "st_file_attributes") else not path.is_symlink())


def _plain_ancestors(path: Path) -> bool:
    return all(_plain(p) for p in (path, *path.parents) if os.path.lexists(p))


def source_files(source: Path) -> dict[str, Path]:
    """Read regular source files without following links or junctions; omit bytecode caches."""
    if not _plain_ancestors(source) or not source.is_dir():
        raise OSError("G6_SKILL_SOURCE_INVALID: source must be a plain directory")
    found = {}

    def visit(folder: Path) -> None:
        for path in sorted(folder.iterdir()):
            if not _plain(path):
                raise OSError("G6_SKILL_SOURCE_INVALID: linked source item")
            if path.is_dir():
                if path.name != "__pycache__":
                    visit(path)
            elif stat.S_ISREG(path.stat().st_mode):
                found[path.relative_to(source).as_posix()] = path
            else:
                raise OSError("G6_SKILL_SOURCE_INVALID: non-regular source item")

    visit(source)
    return found


def with_records(files: dict[str, Path], source: Path) -> dict[str, Path]:
    """Require and carry byte-identical source records for each vendored payload."""
    payload = dict(files)
    for name in RECORDS:
        record = source / name
        if not _plain_ancestors(record) or not record.is_file() or not record.stat().st_size:
            raise FileNotFoundError(f"{MISSING}: {source.name}/{name}")
        if name in payload and payload[name].read_bytes() != record.read_bytes():
            raise FileNotFoundError(f"G6_SKILL_SOURCE_RECORD_CONFLICT: {source.name}/{name}")
        payload[name] = record
    return payload


def _tree(folder: Path) -> dict[str, str | None]:
    """Hash every regular file and record every directory, excluding only the root receipt."""
    found: dict[str, str | None] = {}

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            if not _plain(path):
                raise OSError("linked installed item")
            relative = path.relative_to(folder).as_posix()
            if relative == MARKER:
                continue
            if path.is_dir():
                found[relative] = None
                visit(path)
            elif stat.S_ISREG(path.stat().st_mode):
                found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                raise OSError("non-regular installed item")

    visit(folder)
    return found


def _receipt(folder: Path) -> bytes:
    record = {"owner": "g6", "version": 1, "tree": _tree(folder)}
    return (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")


def _owned(folder: Path) -> bool:
    try:
        if not _plain_ancestors(folder) or not folder.is_dir() or not _plain(folder / MARKER):
            return False
        return (folder / MARKER).read_bytes() == _receipt(folder)
    except (OSError, ValueError):
        return False


def install_files(files: dict[str, Path], target: Path) -> str:
    """Install a complete payload, or leave a personal destination untouched and return its stable code.

    Receipts describe the installed bytes, so a later source version can replace an unchanged earlier version.
    Missing, malformed or stale receipts never establish ownership, even for a byte-identical payload.
    """
    # lexists includes dangling links, which also belong to the person.
    present = target.exists() or os.path.lexists(target)
    if not _plain_ancestors(target) or (present and not _owned(target)):
        return KEPT
    if not files:
        raise OSError("G6_SKILL_SOURCE_INVALID: empty payload")
    for name in files:
        relative = PurePosixPath(name)
        if (name == MARKER or not relative.parts or relative.is_absolute() or ".." in relative.parts or "\\" in name
                or Path(name).drive or relative.as_posix() != name):
            raise OSError("G6_SKILL_SOURCE_INVALID: invalid payload path")
        if not _plain_ancestors(files[name]) or not stat.S_ISREG(files[name].stat().st_mode):
            raise OSError("G6_SKILL_SOURCE_INVALID: non-regular source item")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".g6-install-", dir=target.parent))
    incoming, backup = staging / "new", staging / "old"
    try:
        incoming.mkdir()
        for name, source in files.items():
            destination = incoming / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        (incoming / MARKER).write_bytes(_receipt(incoming))
        if present:
            if not _owned(target):
                return KEPT
            target.rename(backup)
            if not _owned(backup):
                backup.rename(target)
                return KEPT
        try:
            incoming.rename(target)
        except OSError:
            if backup.exists() and not os.path.lexists(target):
                backup.rename(target)
            raise
        if backup.exists():
            if not _owned(backup):
                raise OSError(f"G6_SKILL_RECOVERY_REQUIRED: retained changed earlier tree at {backup}")
            shutil.rmtree(backup)
        return UPDATED if present else INSTALLED
    finally:
        # An earlier tree that changed during installation is retained for the person, even on an I/O failure.
        if not os.path.lexists(backup):
            shutil.rmtree(staging)
