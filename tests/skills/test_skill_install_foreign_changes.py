# SPDX-License-Identifier: Apache-2.0
"""Foreign trees survive every durable transaction boundary, at their original name."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess

import pytest

from g6.skills import install


def inventory(folder):
    """Observe links as entries, without walking their targets."""
    result = {}

    def visit(directory):
        for path in directory.iterdir():
            name = path.relative_to(folder).as_posix()
            status = path.lstat()
            if path.is_symlink() or (hasattr(status, "st_file_attributes")
                                     and status.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                result[name] = "link"
            elif path.is_dir():
                result[name] = None
                visit(path)
            else:
                result[name] = path.read_bytes()

    visit(folder)
    return result


@pytest.mark.parametrize("phase", ["prepared", "staged", "renamed", "published", "cleanup"])
@pytest.mark.parametrize("change", ["edit", "add", "delete", "junction"])
@pytest.mark.parametrize("location", ["destination", "backup", "incoming"])
def test_foreign_tree_is_never_deleted_or_displaced(tmp_path, monkeypatch, phase, change, location):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    source, target = tmp_path / "source", tmp_path / "installed"
    source.write_bytes(b"old\n")
    files = {"SKILL.md": source, "nested/data": source}
    assert install.install_files(files, target) == install.INSTALLED
    state, = (tmp_path / "state/skill-installs").glob("*.json")
    before = json.loads(state.read_bytes())
    source.write_bytes(b"new\n")
    replacement = tmp_path / "replacement"
    assert install.install_files(files, replacement) == install.INSTALLED
    after = {**before, "tree": {name: hashlib.sha256(value).hexdigest() if value is not None else None
                               for name, value in inventory(replacement).items()}}
    staging = tmp_path / ".g6-install-foreign"
    staging.mkdir()
    backup, incoming = staging / "old", staging / "new"
    replacement.rename(incoming)
    if location == "backup" or phase in ("renamed", "published", "cleanup"):
        target.rename(backup)
        if phase in ("renamed", "published", "cleanup"):
            incoming.rename(target)
    # Exercise each location at every decision boundary, including a retained
    # incoming tree in cleanup and a still-live old destination before staging.
    if location == "incoming" and not incoming.exists():
        import shutil
        shutil.copytree(target, incoming)
    folder = {"destination": target, "backup": backup, "incoming": incoming}[location]
    if not folder.exists():
        backup.rename(target)
    held = {}
    for name, record in (("old", before), ("new", after)):
        if (staging / name).exists():
            held[name] = None
            held.update({f"{name}/{key}": value for key, value in record["tree"].items()})
    key = hashlib.sha256(before["destination"].encode()).hexdigest()
    pending = state.parent / f"{key}.pending"
    journal = {"before": before, "after": after, "staging": staging.name,
               "phase": "published" if phase in ("published", "cleanup") else "prepared",
               "work": held, "staged": phase != "prepared"}
    if phase == "cleanup":
        journal["garbage"] = held
        journal["removing"] = "old/nested/data"
        (backup / "nested/data").unlink()
    install._save(pending, journal)
    outside = tmp_path / "personal-junction-target"
    outside.mkdir()
    (outside / "precious").write_bytes(b"personal outside bytes\n")
    link = folder / "personal-link"
    try:
        if change == "edit":
            (folder / "SKILL.md").write_bytes(b"personal edit\n")
        elif change == "add":
            (folder / "personal").write_bytes(b"personal addition\n")
            (folder / "personal-empty").mkdir()
        elif change == "delete":
            (folder / "SKILL.md").unlink()
        elif os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                    capture_output=True, text=True, check=True)
            assert result.returncode == 0
        else:
            link.symlink_to(outside, target_is_directory=True)
        expected = inventory(folder)
        destination = inventory(target) if target.exists() else None
        for _ in range(2):
            try:
                reply = install.install_files(files, target)
            except OSError as exc:
                assert install.RECOVERY in str(exc)
            else:
                assert reply == install.KEPT
            assert folder.is_dir(), "foreign tree was displaced or deleted"
            assert inventory(folder) == expected, "foreign bytes or directory entries were deleted"
            if location == "destination":
                assert inventory(target) == destination, "personal destination was replaced"
            assert (outside / "precious").read_bytes() == b"personal outside bytes\n"
    finally:
        if os.path.lexists(link):
            if os.name == "nt":
                link.rmdir()
            else:
                link.unlink()
