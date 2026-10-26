# SPDX-License-Identifier: Apache-2.0
"""A confined child must be able to read the folder G6 itself is imported from (S5).

With an editable install, ``g6``'s source sits outside both the base Python and the virtual environment, so an access
check of those two alone said ok while a confined child then failed with "No module named 'g6'". The folder that holds
the imported ``g6`` package is now one of the paths the check covers, named with the owner's one-time grant like the
others; and a confined child that still cannot import G6 says why. A temporary folder stands in for that folder here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.core import component_process, confine, sandbox
from g6.core.errors import ISOLATION_UNAVAILABLE

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the AppContainer backend is Windows's")


def _outside_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A ``g6`` package in a folder outside this interpreter's base and environment, as the imported one."""
    src = tmp_path / "src"
    (src / "g6").mkdir(parents=True)
    (src / "g6" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(g6, "__file__", str(src / "g6" / "__init__.py"))
    return os.path.realpath(src)


def test_the_folder_holding_g6_is_read_by_a_child_of_this_interpreter(tmp_path, monkeypatch) -> None:
    # Fails if confine.interpreter_paths stops adding the folder that holds the imported g6 package.
    src = _outside_source(tmp_path, monkeypatch)
    paths = confine.interpreter_paths(sys.executable)
    assert src in paths
    assert os.path.realpath(sys.base_prefix) in paths and os.path.realpath(sys.prefix) in paths


def test_a_g6_inside_the_environment_adds_no_folder(monkeypatch) -> None:
    # Fails if confine.interpreter_paths adds the g6 folder even when the environment already holds it.
    monkeypatch.setattr(g6, "__file__", os.path.join(sys.prefix, "Lib", "site-packages", "g6", "__init__.py"))
    assert confine.interpreter_paths(sys.executable) == tuple(
        dict.fromkeys(os.path.realpath(path) for path in (sys.base_prefix, sys.prefix)))


@windows_only
def test_the_access_check_names_the_unreadable_g6_folder_with_its_grant(tmp_path, monkeypatch) -> None:
    # Fails if the check says ok (or omits the grant) while the container cannot read the folder that holds g6.
    from g6.cli.doctor import _isolation

    src = _outside_source(tmp_path, monkeypatch)
    chosen, _ = confine._windows()
    monkeypatch.setattr(chosen, "readable", lambda path: os.path.realpath(path) != src)
    name, why = confine.backend(sys.executable, probe=False)
    grant = f'icacls "{src}" /grant "*S-1-15-2-1:(OI)(CI)(RX)"'
    assert name is None and grant in why, why
    check = _isolation()
    assert check.status == "warn" and grant in check.fix, check.fix


def test_a_confined_child_that_cannot_import_g6_says_why(tmp_path, monkeypatch) -> None:
    # Fails if sandbox.run_script returns a confined child's "No module named 'g6'" without confine.import_failure's
    # named reason.
    src = _outside_source(tmp_path, monkeypatch)
    monkeypatch.setattr(confine, "plan", lambda confinement, python: confine.Plan(confine.APPCONTAINER, "", confinement))
    failed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"ModuleNotFoundError: No module named 'g6'\n")
    monkeypatch.setattr(component_process, "run_contained", lambda *args, **kwargs: failed)
    run = sandbox.run_script("import g6", timeout_s=30)
    assert run.status == "error" and run.isolation["backend"] == confine.APPCONTAINER
    assert run.stderr.startswith(f"[{ISOLATION_UNAVAILABLE}]") and src in run.stderr, run.stderr
    assert "No module named 'g6'" in run.stderr


def test_an_unconfined_child_s_error_is_left_as_it_is(monkeypatch) -> None:
    # Fails if the named reason is added to a run that was not confined (its import failure is not the container's).
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    failed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"ModuleNotFoundError: No module named 'g6'\n")
    monkeypatch.setattr(component_process, "run_contained", lambda *args, **kwargs: failed)
    run = sandbox.run_script("import g6", timeout_s=30)
    assert run.stderr == "ModuleNotFoundError: No module named 'g6'\n"
