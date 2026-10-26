# SPDX-License-Identifier: Apache-2.0
"""Regression cases for the workspace family review; run by the owner outside the sandbox."""
from __future__ import annotations

import builtins
import io
import json
import os
import tarfile
import threading
import time
from pathlib import Path

import pytest

from g6.core.registry import get_registry
from g6.core.resources import RESOURCE_LIMIT
from g6.workspace import _coordination, _files
from g6.workspace import _envelope as E
from g6.workspace._files import Workspace
from g6.workspace.align_prompt_library import Library
from g6.workspace.align_prompt_library import library as library_module


@pytest.mark.parametrize("operation", ["cleanup", "restore"])
@pytest.mark.parametrize("depth", [1, 3])
def test_delete_scandir_swap_refuses(home, outside, links, monkeypatch, operation, depth):
    # Fix: _files.py:185, _checked_listing(path, found); bypassing this checked traversal loses the swap refusal.
    ws = Workspace.for_task("race")
    rel = "/".join(["sub"] * depth)
    ws.write(f"{rel}/local.txt", "local", dry_run=False)
    backup = ws.backup(dry_run=False)["backup"]
    target = ws.root / rel
    real_scandir = os.scandir
    armed = False
    swapped = False
    real_remove = _files._remove

    def remove(path, *args, **kwargs):
        nonlocal armed
        armed = True
        return real_remove(path, *args, **kwargs)

    def scan(path):
        nonlocal swapped
        if armed and not swapped and isinstance(path, (str, os.PathLike)) and Path(path) == target:
            swapped = True
            target.rename(target.with_name("saved"))
            if not links.folder(target, outside):
                pytest.skip("directory links unavailable")
        return real_scandir(path)

    monkeypatch.setattr(_files, "_remove", remove)
    monkeypatch.setattr(_files.os, "scandir", scan)
    with pytest.raises(E.WorkspaceRefused):
        if operation == "cleanup":
            ws.cleanup(dry_run=False)
        else:
            ws.restore(backup, dry_run=False)
    assert swapped
    assert (outside / "secret.txt").read_text() == "keep"


@pytest.mark.parametrize("operation", ["write", "lock", "backup"])
@pytest.mark.parametrize("kind", ["folder", "junction"])
def test_linked_bookkeeping_refuses(home, outside, links, operation, kind):
    # Fix: _coordination.py:63, contained(..., refuse_links=True); bypassing containment permits outside writes.
    ws = Workspace.for_task("linked")
    ws.root.mkdir(parents=True)
    (ws.root / "local.txt").write_text("local")
    if not getattr(links, kind)(ws.root / ".g6-workspace", outside):
        pytest.skip(f"{kind} unavailable")
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    with pytest.raises(E.WorkspaceRefused):
        if operation == "write":
            ws.write("new.txt", "new", dry_run=False)
        elif operation == "lock":
            ws.lock("local.txt", "owner")
        else:
            ws.backup(dry_run=False)
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before
    assert not (ws.root / "new.txt").exists()


def test_backup_open_swap_refuses(home, outside, links, monkeypatch):
    # Fix: _files.py:518, opened/enumerated device-inode comparison; removing it archives outside bytes.
    ws = Workspace.for_task("backup")
    ws.write("local.txt", "local", dry_run=False)
    target = ws.root / "local.txt"
    real_open = builtins.open
    swapped = False

    def opening(path, *args, **kwargs):
        nonlocal swapped
        if Path(path) == target and not swapped:
            swapped = True
            target.unlink()
            if not links.symlink(target, outside / "secret.txt"):
                pytest.skip("file symlinks unavailable")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(_files, "open", opening, raising=False)
    with pytest.raises(E.WorkspaceRefused) as refused:
        ws.backup(dry_run=False)
    assert refused.value.code == E.NOT_A_FILE
    assert swapped and ws.backups() == []
    assert not list((ws.bookkeeping / "backups").iterdir())
    assert (outside / "secret.txt").read_text() == "keep"


@pytest.mark.parametrize("operation", ["cleanup", "restore"])
def test_destructive_lock_check_is_serialised(home, monkeypatch, operation):
    # Fix: _files.py:589/631, with self.coordination.mutex(); removing it allows a lock before deletion.
    ws = Workspace.for_task("serial")
    ws.write("protected.txt", "original", dry_run=False)
    backup = ws.backup(dry_run=False)["backup"]
    caller = Workspace(ws.root)
    checked, resume, attempted = threading.Event(), threading.Event(), threading.Event()
    errors, locks = [], []
    real_unlocked = ws._unlocked
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.15)

    def pause(what):
        real_unlocked(what)
        checked.set()
        assert resume.wait(5)

    def destroy():
        try:
            if operation == "cleanup":
                ws.cleanup(dry_run=False)
            else:
                ws.restore(backup, dry_run=False)
        except BaseException as exc:
            errors.append(exc)

    def take():
        try:
            locks.append(caller.lock("protected.txt", "contender"))
        except E.WorkspaceRefused as exc:
            if exc.code != E.BUSY:
                errors.append(exc)
        except BaseException as exc:
            errors.append(exc)
        finally:
            attempted.set()

    monkeypatch.setattr(ws, "_unlocked", pause)
    worker = threading.Thread(target=destroy)
    worker.start()
    try:
        assert checked.wait(5)
        contender = threading.Thread(target=take)
        contender.start()
        contender.join(3)
        assert attempted.is_set() and not locks
        assert ws.read("protected.txt") == b"original"
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive() and not errors


@pytest.mark.parametrize("bad", [[], None, {}, {"path": "protected.txt"},
    {"path": "protected.txt", "claimed_holder": "owner", "token_sha256": "a" * 64,
     "acquired_at": 1, "expires_at": "bad"},
    {"path": "protected.txt", "claimed_holder": "owner", "token_sha256": "a" * 64,
     "acquired_at": 1, "expires_at": float("nan")},
    {"path": "protected.txt", "claimed_holder": "owner", "token_sha256": "a" * 64,
     "acquired_at": 1, "expires_at": float("inf")},
    {"path": "protected.txt", "claimed_holder": "owner", "token_sha256": "a" * 64,
     "acquired_at": True, "expires_at": 2},
    *[{k: v for k, v in {"path": "protected.txt", "claimed_holder": "owner", "token_sha256": "a" * 64,
                         "acquired_at": 1, "expires_at": 2}.items() if k != missing}
      for missing in ("path", "claimed_holder", "token_sha256", "acquired_at", "expires_at")],
    *[{"path": "protected.txt", "claimed_holder": "owner", "token_sha256": "a" * 64,
       "acquired_at": 1, "expires_at": 2, **change}
      for change in ({"path": "different.txt"}, {"claimed_holder": []}, {"token_sha256": "bad"},
                     {"acquired_at": 3}, {"expires_at": -1}, {"expires_at": 10 ** 1000})]])
@pytest.mark.parametrize("operation", ["write", "restore", "cleanup"])
def test_invalid_lock_blocks(home, bad, operation):
    # Fix: _coordination.py:194, unreadable records stay live; removing this branch lets malformed locks expire.
    ws = Workspace.for_task("invalid")
    ws.write("protected.txt", "original", dry_run=False)
    backup = ws.backup(dry_run=False)["backup"]
    path = ws.coordination._lock_file("protected.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bad))
    os.utime(path, (1, 1))  # corrupt locks must not expire just because their file is old
    with pytest.raises(E.WorkspaceRefused) as refused:
        if operation == "write":
            ws.write("protected.txt", "lost", dry_run=False)
        elif operation == "restore":
            ws.restore(backup, dry_run=False)
        else:
            ws.cleanup(dry_run=False)
    assert refused.value.code == E.LOCKED
    assert ws.read("protected.txt") == b"original"


def test_live_mutex_never_expires(home, monkeypatch):
    # Fix: _coordination.py:129/132, msvcrt.locking/fcntl.flock; removing acquisition permits overlapping sections.
    ws = Workspace.for_task("mutex")
    entered, release = threading.Event(), threading.Event()
    failures = []
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.15)

    def hold():
        try:
            with ws.coordination.mutex():
                entered.set()
                assert release.wait(5)
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert entered.wait(5)
        path = ws.bookkeeping / "mutex"
        os.utime(path, (time.time() - 60, time.time() - 60))
        with pytest.raises(E.WorkspaceRefused) as refused:
            with Workspace(ws.root).coordination.mutex():
                pytest.fail("critical sections overlapped")
        assert refused.value.code == E.BUSY
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    with Workspace(ws.root).coordination.mutex():
        assert path.exists()


@pytest.mark.parametrize("op,params", [("put", {"kind": "prompt", "name": "p", "content": "changed"}),
                                      ("retire", {"name": "p"}), ("rollback", {"name": "p", "to_version": 1})])
def test_library_mutations_are_sensitive(home, op, params):
    # Fix: align_prompt_library/block.py:26, SENSITIVE_OPS; removing each op permits its blocked mutation.
    library = Library()
    library.put("prompt", "p", "original")
    library.put("prompt", "p", "second")
    before = library.path.read_bytes()
    result = get_registry().invoke("align_prompt_library", op, params, block_sensitive=True)
    assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(result.failure())
    assert library.path.read_bytes() == before


def test_render_size_checked_before_substitution(home, monkeypatch):
    # Fix: align_prompt_library/library.py:217, expanded_size > MAX_RENDERED_CHARS; removing it reaches sub.
    library = Library()
    library.put("prompt", "repeated", "{{x}}" * 40)
    pattern = library_module.PLACEHOLDER

    class NoSub:
        findall = pattern.findall
        finditer = pattern.finditer

        def sub(self, *args):
            pytest.fail("substitution started before the size refusal")

    monkeypatch.setattr(library_module, "MAX_RENDERED_CHARS", 100, raising=False)
    monkeypatch.setattr(library_module, "PLACEHOLDER", NoSub())
    with pytest.raises(E.WorkspaceRefused) as refused:
        library.render("repeated", values={"x": "long"})
    assert refused.value.code == RESOURCE_LIMIT


@pytest.mark.parametrize("path", [".partial-data/a", "nested/.partial-data/a", ".PARTIAL-data/a",
                                  ".partial-x/../accepted.txt"])
def test_partial_prefix_reserved(home, path):
    # Fix: _files.py:249, raw .partial- segment refusal; removing it admits .partial-x/../accepted.txt.
    ws = Workspace.for_task("quota", max_bytes=8)
    with pytest.raises(E.WorkspaceRefused) as refused:
        ws.write(path, "abcd", dry_run=False)
    assert refused.value.code == E.RESERVED
    ws.write("a", "12345", dry_run=False)
    ws.write("nested/b", "678", dry_run=False)
    assert ws.status()["bytes"] == 8
    assert {f["path"] for f in ws.files()[0]} == {"a", "nested/b"}
    with pytest.raises(E.WorkspaceRefused) as full:
        ws.write("c", "9", dry_run=False)
    assert full.value.code == RESOURCE_LIMIT


@pytest.mark.parametrize("member", ["./.g6-workspace/x", "a/../.g6-workspace/x", "./.partial-data/x"])
def test_restore_reserved_preflight_preserves_files(home, member):
    # Fix: _files.py:563, normalised reserved-destination refusal; removing it changes originals before failure.
    ws = Workspace.for_task("archive")
    ws.write("original.txt", "original", dry_run=False)
    folder = ws.bookkeeping / "backups"
    folder.mkdir()
    archive = folder / "crafted.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))
    before = {str(p.relative_to(ws.root)): p.read_bytes() for p in ws.root.rglob("*") if p.is_file()}
    with pytest.raises(E.WorkspaceRefused) as refused:
        ws.restore(archive.name, dry_run=False)
    assert refused.value.code == E.ARCHIVE_REFUSED
    assert {str(p.relative_to(ws.root)): p.read_bytes() for p in ws.root.rglob("*") if p.is_file()} == before
