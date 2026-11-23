# SPDX-License-Identifier: Apache-2.0
"""One ownership rule and recoverable transaction for every skill writer."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import time
import uuid
import warnings
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
CLEANUP = "G6_SKILL_CLEANUP_PENDING"
QUARANTINED = "G6_SKILL_BACKUP_QUARANTINED"


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
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _remove_tree(path: Path) -> None:
    """Remove one inventory-checked leaf; never recurse through an unchecked tree."""
    if not _plain_ancestors(path):
        raise OSError(f"{RECOVERY}: linked garbage entry")
    if path.is_dir():
        path.rmdir()
    else:
        path.unlink()


def _stage_entry(staging: Path, pending: Path, journal: dict, name: str, data: bytes | None) -> None:
    # The intent binds every possible interrupted write prefix to G6-held bytes.
    # Other entries must still match the complete last recorded inventory.
    journal["intent"] = {"name": name, "data": data.hex() if data is not None else None}
    _save(pending, journal)
    path = staging / name
    if data is None:
        path.mkdir()
    else:
        _write_bytes(path, data)
    journal["work"][name] = hashlib.sha256(data).hexdigest() if data is not None else None
    del journal["intent"]
    _save(pending, journal)


def _staging_inventory(staging: Path, journal: dict) -> dict:
    """Select only an inventory described by the held write or tree records."""
    expected = dict(journal.get("work", {}))
    intent = journal.get("intent")
    if intent:
        path = staging / intent["name"]
        if os.path.lexists(path):
            if not _plain_ancestors(path):
                raise OSError(f"{RECOVERY}: linked staged entry")
            data = intent["data"]
            if data is None:
                if not path.is_dir():
                    raise OSError(f"{RECOVERY}: changed staged directory")
                expected[intent["name"]] = None
            else:
                actual = path.read_bytes()
                if not bytes.fromhex(data).startswith(actual):
                    raise OSError(f"{RECOVERY}: changed interrupted write")
                expected[intent["name"]] = hashlib.sha256(actual).hexdigest()
    if journal.get("staged") or "work" not in journal:
        expected = {}
        if os.path.lexists(staging / "new"):
            expected["new"] = None
            expected.update({f"new/{name}": digest for name, digest in journal["after"]["tree"].items()})
    if os.path.lexists(staging / "old"):
        before = journal.get("before")
        if before is None:
            raise OSError(f"{RECOVERY}: unrecorded earlier tree")
        expected["old"] = None
        expected.update({f"old/{name}": digest for name, digest in before["tree"].items()})
    return expected


def _cleanup(staging: Path, pending: Path, journal: dict) -> list[str]:
    try:
        expected = journal.get("garbage")
        if expected is None:
            try:
                expected = _staging_inventory(staging, journal)
            except OSError as exc:
                raise OSError(f"{RECOVERY}: cannot check staged inventory: {exc}") from exc
        if not os.path.lexists(staging) and expected:
            raise OSError(f"{RECOVERY}: recorded staging tree is missing")
        while os.path.lexists(staging):
            if not _plain_ancestors(staging):
                raise OSError(f"{RECOVERY}: linked staging folder")
            try:
                actual = _tree(staging)
            except OSError as exc:
                raise OSError(f"{RECOVERY}: cannot check garbage inventory: {exc}") from exc
            removing = journal.get("removing")
            remainder = dict(expected)
            if removing in remainder:
                del remainder[removing]
            if actual != expected and not (removing and actual == remainder):
                raise OSError(f"{RECOVERY}: retained changed transaction tree at {staging}")
            expected = actual
            if expected:
                name = sorted(expected, key=lambda entry: (entry.count("/"), entry), reverse=True)[0]
                leaf = staging / name
            else:
                name, leaf = ".", staging
            journal["garbage"], journal["removing"] = expected, name
            _save(pending, journal)
            # Recheck after publishing the intent; a callback or another process
            # may have edited a different entry while the journal was flushed.
            if not _matches(staging, {"tree": expected}):
                raise OSError(f"{RECOVERY}: garbage changed before removal")
            _remove_tree(leaf)
            _sync_dir(leaf.parent)
            expected = dict(expected)
            if name != ".":
                del expected[name]
        _sync_dir(staging.parent)
        _save(pending, None)
    except OSError as exc:
        if RECOVERY in str(exc):
            raise
        journal["cleanup_error"] = str(exc)
        try:
            _save(pending, journal)
        except OSError:
            # The existing decision journal is sufficient to retry cleanup.
            pass
        return [f"{CLEANUP}: {exc}"]
    return []


def _recover(target: Path, pending: Path, state: Path, destination: str, skill_id: str) -> list[str]:
    if not os.path.lexists(pending):
        return []
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
    backup, incoming = staging / "old", staging / "new"
    phase = journal.get("phase")
    if phase is None:
        # Preserve the former recovery contract for journals without a phase:
        # a complete renamed replacement was already considered published.
        phase = "published" if _matches(target, after) else "prepared"
        journal["phase"] = phase
        _save(pending, journal)
    if phase not in ("prepared", "published", "rolled_back"):
        raise OSError(f"{RECOVERY}: invalid transaction phase")
    notices = []
    personal_return = journal.get("personal_return")
    if personal_return:
        # A live, content-identical swap can return the just-moved personal
        # directory to its original name. This record authorizes only that
        # return, never deletion or later replacement of the personal tree.
        if _matches(backup, personal_return):
            if os.path.lexists(target):
                if not _matches(target, after) or os.path.lexists(incoming):
                    raise OSError(f"{RECOVERY}: personal return destination changed")
                os.replace(target, incoming)
            if not _matches(backup, personal_return):
                raise OSError(f"{RECOVERY}: personal return tree changed")
            backup.rename(target)
            _sync_dir(target.parent)
            _sync_dir(staging)
        if not _matches(target, personal_return) or os.path.lexists(backup):
            raise OSError(f"{RECOVERY}: personal return inventory changed")
        journal["phase"] = "rolled_back"
        _save(pending, journal)
        _save(state, before)
        return _cleanup(staging, pending, journal)
    if os.path.lexists(target) and not (_matches(target, after) or _matches(target, before)):
        # Keep a live edit at its destination. Only the separately inventoried
        # G6 staging entries may be cleaned; changed backup/staging still refuse.
        journal["phase"] = "published" if phase == "published" else "rolled_back"
        _save(pending, journal)
        _save(state, after if phase == "published" else before)
        return _cleanup(staging, pending, journal)
    # An interrupted garbage deletion has its own durable, shrinking inventory.
    if "garbage" in journal:
        selected = after if phase == "published" else before
        if _load(state) != selected:
            _save(state, selected)
        return _cleanup(staging, pending, journal)
    if phase == "prepared":
        if os.path.lexists(backup) and not _matches(backup, before):
            raise OSError(f"{RECOVERY}: retained changed earlier tree at {backup}")
        if os.path.lexists(backup):
            if os.path.lexists(target):
                if not _matches(target, after) or os.path.lexists(incoming):
                    raise OSError(f"{RECOVERY}: destination changed; retained both trees")
                target.rename(incoming)
            if not _matches(backup, before):
                raise OSError(f"{RECOVERY}: earlier tree changed during restoration")
            backup.rename(target)
            _sync_dir(target.parent)
            _sync_dir(staging)
        elif before is None and _matches(target, after):
            target.rename(incoming)
            _sync_dir(target.parent)
            _sync_dir(staging)
        elif before is not None and not _matches(target, before):
            raise OSError(f"{RECOVERY}: recorded earlier tree is missing")
        journal["phase"] = "rolled_back"
        _save(pending, journal)
    # Never restore old after the durable publish decision, even if old is now partial.
    selected = after if journal["phase"] == "published" else before
    if _load(state) != selected:
        _save(state, selected)
    return notices + _cleanup(staging, pending, journal)


def recover_installs(target: Path, *, children: bool = False) -> list[str]:
    """Recover pending destinations before a writer discovers or validates any source.

    Identity comes from the G6-held journal so a removed or renamed source cannot
    postpone recovery. Only the named destination (or its direct children) is in scope.
    """
    root = g6_home() / "skill-installs"
    if not _plain_ancestors(root):
        raise OSError(f"{RECOVERY}: linked state folder")
    if not root.exists():
        return []
    scope = os.path.normcase(str(target.absolute().resolve()))
    notices = []
    # Enumerate while holding the same lock used to read and retire each journal.
    # Names alone provide the lock identity, so no journal bytes are read unlocked.
    keys = sorted(p.stem for p in root.glob("*.pending"))
    for key in keys:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise OSError(f"{RECOVERY}: invalid transaction key")
        with _lock(root / f"{key}.lock"):
            pending = root / f"{key}.pending"
            if pending not in root.glob("*.pending"):
                continue
            journal = _load(pending)
            if journal is None and not os.path.lexists(pending):
                continue
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
            if key != hashlib.sha256(destination.encode("utf-8")).hexdigest():
                raise OSError(f"{RECOVERY}: transaction key does not match destination")
            identity = hashlib.sha256((destination + "\0" + skill_id).encode("utf-8")).hexdigest()
            notices.extend(_recover(folder, pending, root / f"{identity}.json", destination, skill_id))
    for notice in notices:
        warnings.warn(notice, RuntimeWarning, stacklevel=2)
    return notices


def install_files(files: dict[str, Path], target: Path, *, skill_id: str | None = None) -> str:
    """Only G6-held state for this exact destination and identity authorizes replacement.

    A destination receipt is provenance, never authority. Ordinary directory renames need a brief name gap;
    caught failures roll back and a durable journal lets the next invocation recover after process death.
    """
    notices = recover_installs(target)
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
        notices.extend(_recover(target, pending, state, destination, skill_id))
        before = _record(_load(state), destination, skill_id)
        present = os.path.lexists(target)
        if not _plain_ancestors(target) or (present and not _matches(target, before)):
            return KEPT
        if pending.exists():
            # A retained cleanup journal must not be overwritten by another install.
            return ": ".join([UPDATED if present else INSTALLED, *notices])
        reply = _install(files, target, state, pending, before, destination, skill_id, present)
        return ": ".join([reply, *notices])


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
    # Compute and bind the complete replacement before any staging mutation.
    payload = {name: source.read_bytes() for name, source in files.items()}
    tree = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    for name in payload:
        for folder in PurePosixPath(name).parents:
            if folder != PurePosixPath("."):
                tree[folder.as_posix()] = None
    payload[MARKER] = (json.dumps({"owner": "g6", "version": 1, "tree": tree}, sort_keys=True) + "\n").encode()
    tree[MARKER] = hashlib.sha256(payload[MARKER]).hexdigest()
    after = {"version": 1, "destination": canonical_destination, "skill_id": skill_id, "tree": tree}
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".g6-install-{uuid.uuid4().hex}"
    incoming, backup = staging / "new", staging / "old"
    journal = {"before": before, "after": after, "staging": staging.name,
               "phase": "prepared", "work": {}}
    _save(pending, journal)
    try:
        staging.mkdir()
        _stage_entry(staging, pending, journal, "new", None)
        for name, data in payload.items():
            for folder in reversed(PurePosixPath("new", name).parents):
                if folder != PurePosixPath(".") and folder.as_posix() not in journal["work"]:
                    _stage_entry(staging, pending, journal, folder.as_posix(), None)
            _stage_entry(staging, pending, journal, f"new/{name}", data)
        for folder in sorted((p for p in incoming.rglob("*") if p.is_dir()), reverse=True):
            _sync_dir(folder)
        _sync_dir(incoming)
        _sync_dir(staging)
        _sync_dir(target.parent)
        if not _matches(incoming, after):
            raise OSError("G6_SKILL_SOURCE_INVALID: staged bytes changed")
        journal["staged"] = True
        _save(pending, journal)
        if present:
            if not _matches(target, before):
                _recover(target, pending, state, canonical_destination, skill_id)
                return KEPT
            target.rename(backup)
            incoming.rename(target)
        elif not _plain_ancestors(target) or os.path.lexists(target):
            _recover(target, pending, state, canonical_destination, skill_id)
            return KEPT
        else:
            incoming.rename(target)
        _sync_dir(target.parent)
        _sync_dir(staging)
        if present and not _matches(backup, before):
            if before["tree"] == after["tree"] and _plain_ancestors(backup):
                # Return an edit to the very directory this live no-op swap
                # moved. Never turn these personal hashes into install authority.
                journal["personal_return"] = {"tree": _tree(backup)}
                _save(pending, journal)
                _recover(target, pending, state, canonical_destination, skill_id)
                return KEPT
            raise OSError(f"{RECOVERY}: retained changed earlier tree at {backup}")
        _save(state, after)
        journal["phase"] = "published"
        _save(pending, journal)
    except BaseException:
        # Read the durable phase: a raised write may already have replaced its file.
        _recover(target, pending, state, canonical_destination, skill_id)
        raise
    notices = _cleanup(staging, pending, journal)
    return ": ".join([UPDATED if present else INSTALLED, *notices])
