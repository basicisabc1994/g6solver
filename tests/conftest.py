# SPDX-License-Identifier: Apache-2.0
"""Test isolation: all durable state goes to a throwaway G6_HOME, never the user's real one, no sensitive op is
allowed over MCP from the shell's own G6_MCP_SENSITIVE (a test that wants one passes ``allow_sensitive``), and no
model is chosen by the shell's own G6_LLM_PROVIDER (g6_invoke passes the configured model to the components that take
one)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

# Set before any g6 module is imported, so module-level defaults resolve inside it.
os.environ["G6_HOME"] = tempfile.mkdtemp(prefix="g6-test-home-")
os.environ.pop("G6_MCP_SENSITIVE", None)
for _setting in ("G6_LLM_PROVIDER", "G6_LLM_MODEL"):
    os.environ.pop(_setting, None)


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    """A G6_HOME of the test's own and a writer for the person's key file in it: G6 reads keys only from that file
    (owner ruling, 2026-10-21), and the session's shared G6_HOME would carry a key on into later tests."""
    home = tmp_path / "g6-home"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    for name in ("G6_LLM_PROVIDER", "G6_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    def write(text: str) -> None:
        (home / ".env").write_text(text, encoding="utf-8")

    write.path = home / ".env"
    return write


@pytest.fixture
def registry_reset():
    """A fresh component registry, and fresh site catalogue caches, before and after the test."""
    import g6.core.registry as registry

    def clear() -> None:
        registry._REGISTRY = None
        catalogue = sys.modules.get("pages.catalogue")
        if catalogue is not None:
            catalogue._rows.cache_clear()
            catalogue._extension_rows.cache_clear()

    clear()
    yield
    clear()


#: Top-level folders of an interpreter's standard library a confined child does not need, and the packages pytest needs.
_NOT_NEEDED = {"site-packages", "idlelib", "tkinter", "turtledemo", "ensurepip", "test", "lib2to3", "pydoc_data"}
_PYTEST = ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments", "colorama", "py.py")


@pytest.fixture(scope="session")
def confined_python(tmp_path_factory):
    """A Python the Windows AppContainer can start (``g6.core.confine``), or a skip that says why: this interpreter when
    the owner has made the one-time grant, else a copy of its base installation, with pytest, in a folder this session
    made, to which the session gives ALL APPLICATION PACKAGES read and execute (the owner's one-time grant). Nothing
    here changes the access of a folder the tests did not make."""
    if sys.platform != "win32":
        pytest.skip("the AppContainer backend is Windows's")
    import shutil
    from pathlib import Path

    from g6.core import confine

    if confine.backend(sys.executable)[0] is not None:
        return sys.executable
    root, base = tmp_path_factory.mktemp("confined-python"), Path(sys.base_prefix)
    for item in base.iterdir():
        if item.is_file():
            shutil.copy2(item, root / item.name)
    shutil.copytree(base / "DLLs", root / "DLLs")
    shutil.copytree(base / "Lib", root / "Lib",
                    ignore=lambda folder, names: [n for n in names if Path(folder) == base / "Lib" and n in _NOT_NEEDED])
    site = root / "Lib" / "site-packages"
    site.mkdir()
    for entry in (Path(sys.prefix) / "Lib" / "site-packages").iterdir():
        if entry.name in _PYTEST or (entry.name.endswith(".dist-info") and entry.name.split("-")[0].lower() in _PYTEST):
            (shutil.copytree if entry.is_dir() else shutil.copy2)(entry, site / entry.name)
    chosen, _ = confine._windows()
    chosen.grant(str(root), chosen.all_packages, write=False)   # the owner's one-time grant, on the session's copy
    python = str(root / "python.exe")
    name, why = confine.backend(python)
    if name is None:
        pytest.skip(f"the Windows backend cannot run here, even on a copy of the interpreter: {why}")
    return python