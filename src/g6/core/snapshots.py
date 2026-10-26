# SPDX-License-Identifier: Apache-2.0
"""Snapshot and roll back ``$G6_HOME``, so every change G6 makes to its own state is reversible.

Snapshots are zip files in ``$G6_HOME/snapshots/``. SQLite databases are copied with SQLite's backup
API (a consistent copy even in WAL mode); their ``-wal``/``-shm`` side files are not copied.
``rollback`` first snapshots the current state, so a rollback can itself be undone. ``export`` writes
a snapshot elsewhere for sharing and always leaves out stored API keys.

The person's key file (``$G6_HOME/.env``, :mod:`g6.core.keys`) is theirs, not G6's state: no snapshot holds it, a
rollback neither deletes nor restores it, and a snapshot that holds one is refused before anything is touched.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from g6.core.keys import FILE_NAME as KEY_FILE
from g6.core.storage import g6_home

SNAPSHOT_DIR = "snapshots"
SECRET_FILES = frozenset({"bundles/settings.json"})   # never leave the machine
_NOT_STATE = frozenset({SNAPSHOT_DIR, KEY_FILE})      # never snapshotted, deleted or restored
_SIDE_FILES = ("-wal", "-shm", "-journal")
_LABEL = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class Snapshot:
    name: str
    path: Path
    size: int
    files: int


def _files(home: Path) -> list[Path]:
    out = []
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home).as_posix()
        if not p.is_file() or rel.split("/", 1)[0] in _NOT_STATE or p.name.endswith(_SIDE_FILES):
            continue
        out.append(p)
    return out


def _is_sqlite(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(16) == b"SQLite format 3\x00"


def _write(home: Path, target: Path, *, include_secrets: bool) -> int:
    count = 0
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in _files(home):
            rel = p.relative_to(home).as_posix()
            if not include_secrets and rel in SECRET_FILES:
                continue
            if _is_sqlite(p):
                copy = Path(tmp) / f"{count}.db"
                src, dst = sqlite3.connect(p), sqlite3.connect(copy)
                try:
                    src.backup(dst)
                finally:
                    src.close()
                    dst.close()
                zf.write(copy, rel)
            else:
                zf.write(p, rel)
            count += 1
    return count


def snapshot(label: str = "") -> Snapshot:
    home = g6_home()
    if not home.is_dir():
        raise FileNotFoundError(f"no state to snapshot: {home} does not exist")
    folder = home / SNAPSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = time.strftime("%Y%m%dT%H%M%S") + (f"-{_LABEL.sub('_', label)[:40]}" if label else "")
    target, n = folder / f"{stem}.zip", 1
    while target.exists():
        target, n = folder / f"{stem}-{n}.zip", n + 1
    files = _write(home, target, include_secrets=True)
    return Snapshot(target.stem, target, target.stat().st_size, files)


def list_snapshots() -> list[Snapshot]:
    folder = g6_home() / SNAPSHOT_DIR
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.zip")):
        with zipfile.ZipFile(p) as zf:
            out.append(Snapshot(p.stem, p, p.stat().st_size, len(zf.namelist())))
    return out


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = []
    for info in zf.infolist():
        rel = PurePosixPath(info.filename)
        if rel.is_absolute() or ".." in rel.parts or "\\" in info.filename or rel.parts[:1] == (SNAPSHOT_DIR,):
            raise ValueError(f"unsafe path in snapshot: {info.filename!r}")
        if rel.parts[:1] == (KEY_FILE,):
            raise ValueError(f"a snapshot never holds the person's key file ({KEY_FILE}); refusing {info.filename!r}")
        members.append(info)
    return members


def rollback(name: str) -> tuple[Snapshot, Snapshot]:
    """Restore snapshot ``name``. Returns ``(restored, safety_snapshot_of_previous_state)``."""
    match = [s for s in list_snapshots() if s.name == name]
    if not match:
        raise KeyError(f"no snapshot named {name!r}; see 'g6 state ls'")
    restored = match[0]
    home = g6_home()
    with zipfile.ZipFile(restored.path) as zf:
        members = _safe_members(zf)   # validate before touching anything
        safety = snapshot("before-rollback")
        for child in home.iterdir():
            if child.name in _NOT_STATE:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        for info in members:
            zf.extract(info, home)
    return restored, safety


def export(target: str | Path) -> Path:
    """Write the current state to ``target`` (a new .zip), without stored API keys."""
    home, target = g6_home(), Path(target)
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    if not home.is_dir():
        raise FileNotFoundError(f"no state to export: {home} does not exist")
    if home.resolve() in target.resolve().parents:
        raise ValueError("export outside the state folder (use 'g6 state snapshot' for a local copy)")
    _write(home, target, include_secrets=False)
    return target
