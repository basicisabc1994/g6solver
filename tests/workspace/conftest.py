# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the workspace family's tests: a G6 home of the test's own, a folder outside every workspace with
a file in it, and the ways to make a link out of a workspace (a symbolic link, a Windows junction, a hard link)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A G6 home of the test's own, so each test's workspaces and library start empty."""
    folder = tmp_path / "g6-home"
    folder.mkdir()
    monkeypatch.setenv("G6_HOME", str(folder))
    return folder


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A folder outside every workspace, holding ``secret.txt`` ("keep")."""
    folder = tmp_path / "outside"
    folder.mkdir()
    (folder / "secret.txt").write_text("keep", encoding="utf-8")
    return folder


class Links:
    """Each maker returns whether the link was made (some need a privilege this account may not hold)."""

    @staticmethod
    def junction(link: Path, target: Path) -> bool:
        """A Windows directory junction (no privilege needed)."""
        if sys.platform != "win32":
            return False
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                              check=False)
        return done.returncode == 0

    @staticmethod
    def symlink(link: Path, target: Path) -> bool:
        try:
            os.symlink(target, link, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError):
            return False
        return True

    def folder(self, link: Path, target: Path) -> bool:
        """A link from ``link`` to the folder ``target``: a symbolic link, else a junction."""
        return self.symlink(link, target) or self.junction(link, target)

    @staticmethod
    def hard(link: Path, target: Path) -> bool:
        try:
            os.link(target, link)
        except OSError:
            return False
        return True


@pytest.fixture
def links() -> Links:
    return Links()
