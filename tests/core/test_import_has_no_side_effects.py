# SPDX-License-Identifier: Apache-2.0
"""Importing g6.core must not touch the filesystem, the network or the import machinery."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = r"""
import importlib, pkgutil, socket, sys
def _no_net(*a, **k):
    raise AssertionError("network access during import")
socket.socket.connect = _no_net
hooks_before = list(sys.meta_path)
import g6.core
for m in pkgutil.walk_packages(g6.core.__path__, "g6.core."):
    importlib.import_module(m.name)
assert sys.meta_path == hooks_before, "an import hook was installed"
"""


def test_importing_every_core_module_is_inert(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "G6_HOME": str(home / ".g6")}
    subprocess.run([sys.executable, "-c", _SCRIPT], cwd=work, env=env, check=True)
    assert list(home.iterdir()) == []
    assert list(work.iterdir()) == []
