# SPDX-License-Identifier: Apache-2.0
"""One ownership rule and recoverable transaction for every skill writer."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from g6.core.storage import g6_home

MARKER = ".g6-installed.json"
INSTALLED = "G6_SKILL_INSTALLED"
UPDATED = "G6_SKILL_UPDATED"
KEPT = "G6_SKILL_KEPT_PERSONAL"
RECORDS = ("LICENSE", "VENDORED.md")
MISSING = "G6_SKILL_SOURCE_RECORD_MISSING"
DUPLICATE = "G6_SKILL_DUPLICATE_DESTINATION"
RECOVERY = "G6_SKILL_RECOVERY_REQUIRED"


def unique_destinations(names) -> None:
    """Reject portable destination collisions before a batch writes anything."""
    seen = set()
    for name in names:
        key = name.casefold().rstrip(". ")
        if key in seen:
            raise ValueError(f"{DUPLICATE}: {name}")
        seen.add(key)


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


def _tree(folder: Path, *, provenance: bool = True) -> dict[str, str | None]:
    """Hash every regular file and record every directory, including provenance by default."""
    found: dict[str, str | None] = {}

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            if not _plain(path):
                raise OSError("linked installed item")
            relative = path.relative_to(folder).as_posix()
            if relative == MARKER and not provenance:
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
    record = {"owner": "g6", "version": 1, "tree": _tree(folder, provenance=False)}
    return (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")


def _matches(folder: Path, record: dict | None) -> bool:
    try:
        return (record is not None and _plain_ancestors(folder) and folder.is_dir()
                and _tree(folder) == record["tree"])
    except (OSError, ValueError):
        return False


def _load(path: Path) -> dict | None:
    try:
        if not _plain_ancestors(path) or not path.is_file():
            return None
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _sync_dir(path: Path) -> None:
    # Windows cannot open directories with os.open; file contents are still flushed before publication.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _save(path: Path, value: dict | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    else:
        temporary = path.with_suffix(".tmp")
        if not _plain_ancestors(temporary):
            raise OSError(f"{RECOVERY}: linked state file")
        _write_bytes(temporary, (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"))
        os.replace(temporary, path)
    _sync_dir(path.parent)


def _record(value: dict | None, destination: str, skill_id: str) -> dict | None:
    if (isinstance(value, dict) and value.get("version") == 1 and value.get("destination") == destination
            and value.get("skill_id") == skill_id and isinstance(value.get("tree"), dict)
            and value["tree"]):
        return value
    return None


@contextmanager
def _lock(path: Path):
    """OS-held destination lock releases on process death, unlike a lock-directory lease."""
    if not _plain_ancestors(path):
        raise OSError(f"{RECOVERY}: linked lock file")
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _discard(staging: Path, before: dict | None) -> None:
    backup = staging / "old"
    if os.path.lexists(backup):
        # Do not delete an earlier tree that a person changed through an open handle during the swap.
        if not _matches(backup, before):
            raise OSError(f"{RECOVERY}: retained changed earlier tree at {backup}")
        shutil.rmtree(backup)
    shutil.rmtree(staging)
    _sync_dir(staging.parent)


def _recover(target: Path, pending: Path, state: Path, destination: str, skill_id: str) -> None:
    if not os.path.lexists(pending):
        return
    journal = _load(pending)
    if journal is None:
        raise OSError(f"{RECOVERY}: unreadable transaction")
    after = _record(journal.get("after"), destination, skill_id)
    before = _record(journal.get("before"), destination, skill_id)
    name = journal.get("staging")
    if (after is None or (journal.get("before") is not None and before is None)
            or not isinstance(name, str) or not name.startswith(".g6-install-") or Path(name).name != name):
        raise OSError(f"{RECOVERY}: transaction does not belong to this destination and skill")
    staging = target.parent / name
    if not _plain_ancestors(staging):
        raise OSError(f"{RECOVERY}: linked transaction folder")
    backup = staging / "old"
    if not os.path.lexists(target) and os.path.lexists(backup):
        if not _plain_ancestors(backup) or not backup.is_dir():
            raise OSError(f"{RECOVERY}: invalid earlier tree")
        backup.rename(target)
        _sync_dir(target.parent)
        _save(state, before)
    elif _matches(target, after):
        _save(state, after)
    elif _matches(target, before):
        _save(state, before)
    elif os.path.lexists(backup):
        raise OSError(f"{RECOVERY}: destination changed; retained both trees")
    elif os.path.lexists(target):
        # A change before the first rename is personal, never grounds for replacement.
        _save(state, before)
    else:
        _save(state, before)
    if staging.exists():
        _discard(staging, before)
    _save(pending, None)


def recover_installs(target: Path, *, children: bool = False) -> None:
    """Recover pending destinations before a writer discovers or validates any source.

    Identity comes from the G6-held journal so a removed or renamed source cannot
    postpone recovery. Only the named destination (or its direct children) is in scope.
    """
    root = g6_home() / "skill-installs"
    if not _plain_ancestors(root):
        raise OSError(f"{RECOVERY}: linked state folder")
    if not root.exists():
        return
    scope = os.path.normcase(str(target.absolute().resolve()))
    for pending in sorted(root.glob("*.pending")):
        journal = _load(pending)
        after = journal.get("after") if journal else None
        if (not isinstance(after, dict) or not isinstance(after.get("destination"), str)
                or not isinstance(after.get("skill_id"), str)):
            raise OSError(f"{RECOVERY}: unreadable transaction destination")
        destination, skill_id = after["destination"], after["skill_id"]
        folder = Path(destination)
        if (not folder.is_absolute() or os.path.normcase(str(folder.resolve())) != destination):
            raise OSError(f"{RECOVERY}: invalid transaction destination")
        if (os.path.normcase(str(folder.parent)) if children else destination) != scope:
            continue
        if not _plain_ancestors(folder) or root.resolve().is_relative_to(folder.resolve()):
            raise OSError(f"{RECOVERY}: invalid transaction path")
        key = hashlib.sha256(destination.encode("utf-8")).hexdigest()
        if pending.name != f"{key}.pending":
            raise OSError(f"{RECOVERY}: transaction key does not match destination")
        identity = hashlib.sha256((destination + "\0" + skill_id).encode("utf-8")).hexdigest()
        with _lock(root / f"{key}.lock"):
            _recover(folder, pending, root / f"{identity}.json", destination, skill_id)


def install_files(files: dict[str, Path], target: Path, *, skill_id: str | None = None) -> str:
    """Only G6-held state for this exact destination and identity authorizes replacement.

    A destination receipt is provenance, never authority. Ordinary directory renames need a brief name gap;
    caught failures roll back and a durable journal lets the next invocation recover after process death.
    """
    recover_installs(target)
    target = target.absolute()
    if not _plain_ancestors(target):
        return KEPT
    destination = os.path.normcase(str(target.resolve()))
    skill_id = skill_id or f"skill/{target.name.casefold()}"
    root = g6_home() / "skill-installs"
    if not _plain_ancestors(root) or root.resolve().is_relative_to(target.resolve()):
        raise OSError("G6_SKILL_STATE_INVALID: state must be outside the destination and contain no links")
    key = hashlib.sha256(destination.encode("utf-8")).hexdigest()
    identity = hashlib.sha256((destination + "\0" + skill_id).encode("utf-8")).hexdigest()
    state, pending = root / f"{identity}.json", root / f"{key}.pending"
    if (os.path.lexists(target) and not os.path.lexists(pending)
            and not _matches(target, _record(_load(state), destination, skill_id))):
        return KEPT
    root.mkdir(parents=True, exist_ok=True)
    with _lock(root / f"{key}.lock"):
        _recover(target, pending, state, destination, skill_id)
        before = _record(_load(state), destination, skill_id)
        present = os.path.lexists(target)
        if not _plain_ancestors(target) or (present and not _matches(target, before)):
            return KEPT
        return _install(files, target, state, pending, before, destination, skill_id, present)


def _install(files, target, state, pending, before, canonical_destination, skill_id, present):
    if not files:
        raise OSError("G6_SKILL_SOURCE_INVALID: empty payload")
    unique_destinations(files)
    for name in files:
        relative = PurePosixPath(name)
        if (name.casefold() == MARKER.casefold() or not relative.parts or relative.is_absolute()
                or ".." in relative.parts or "\\" in name
                or Path(name).drive or relative.as_posix() != name):
            raise OSError("G6_SKILL_SOURCE_INVALID: invalid payload path")
        if not _plain_ancestors(files[name]) or not stat.S_ISREG(files[name].stat().st_mode):
            raise OSError("G6_SKILL_SOURCE_INVALID: non-regular source item")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".g6-install-", dir=target.parent))
    incoming, backup = staging / "new", staging / "old"
    after = None
    committed = False
    rollback_complete = True
    try:
        incoming.mkdir()
        for name, source in files.items():
            destination = incoming / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes(destination, source.read_bytes())
        _write_bytes(incoming / MARKER, _receipt(incoming))
        for folder in sorted((p for p in incoming.rglob("*") if p.is_dir()), reverse=True):
            _sync_dir(folder)
        _sync_dir(incoming)
        _sync_dir(staging)
        _sync_dir(target.parent)
        after = {"version": 1, "destination": canonical_destination, "skill_id": skill_id, "tree": _tree(incoming)}
        _save(pending, {"before": before, "after": after, "staging": staging.name})
        if present:
            if not _matches(target, before):
                return KEPT
            target.rename(backup)
            incoming.rename(target)
        elif not _plain_ancestors(target) or os.path.lexists(target):
            return KEPT
        else:
            incoming.rename(target)
        _sync_dir(target.parent)
        _sync_dir(staging)
        if present and before["tree"] == after["tree"] and not _matches(backup, before):
            # No distinct new content would be lost: restore the person's edited
            # earlier tree. Distinct versions are both retained by _discard below.
            target.rename(incoming)
            backup.rename(target)
            _sync_dir(target.parent)
            _sync_dir(staging)
            return KEPT
        _save(state, after)
        committed = True
    except BaseException:
        # An interrupt can arrive AFTER a successful rename or state-file publication.
        rollback_complete = False
        if os.path.lexists(backup):
            if _matches(target, after) and not os.path.lexists(incoming):
                target.rename(incoming)
                backup.rename(target)
                _sync_dir(target.parent)
            elif not os.path.lexists(target):
                backup.rename(target)
                _sync_dir(target.parent)
        elif not present and _matches(target, after):
            target.rename(incoming)
        if not os.path.lexists(backup):
            _save(state, before)
            rollback_complete = True
        raise
    finally:
        # Retain both names and the journal if rollback could not complete.
        if committed or (rollback_complete and not os.path.lexists(backup)):
            _discard(staging, before)
            _save(pending, None)
    return UPDATED if present else INSTALLED
