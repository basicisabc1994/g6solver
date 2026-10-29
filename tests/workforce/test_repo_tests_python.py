# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The Python that runs a repository's tests. Benchmark images install a repository's dependencies into the system
Python, and a founder's Poetry or conda environment lives outside the repository: neither is the repository's own
``.venv``, so G6's own Python would run the tests and every import would fail.

Pinned: ``G6_REPO_PYTHON`` in G6's own environment (a full path the person chose) comes first, then the repository's
own ``.venv`` or ``venv``, then G6's interpreter; the run says which (``interpreter_source``: ``setting``,
``repository`` or ``g6``) and the receipt binds that interpreter's file; a setting that names no Python is a refusal
before anything runs, never a quiet fall back; nothing a caller sends can name the interpreter, and the tests never
see the setting. Offline; real git and pytest; the confined case runs under bubblewrap (Docker on Windows)."""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest
from g6.harness import patch_review
from g6.workforce import receipt_store
from g6.workforce import repo_tests as rt

SETTING = "G6_REPO_PYTHON"
INVALID = "G6_E_REPO_PYTHON_INVALID"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
WRITES = "from pathlib import Path\n\n\ndef test_w():\n    Path(__file__).parents[1].joinpath('ran').write_text('')\n"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   check=True, capture_output=True, env=GIT_ENV)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv(SETTING, raising=False)
    repo = tmp_path / "calc"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def _python_file(folder: Path, name: str = "python.exe" if os.name == "nt" else "python3") -> Path:
    """A file named as a Python is, that this user may run (it is never started here)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_the_setting_comes_first_then_the_repositorys_own_environment_then_g6s(repo, tmp_path, monkeypatch):
    assert rt.interpreter(repo) == sys.executable
    own = _python_file(repo / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
                       "python.exe" if os.name == "nt" else "python")
    assert rt.interpreter(repo) == str(own)
    chosen = _python_file(tmp_path / "poetry-env" / "bin")
    monkeypatch.setenv(SETTING, str(chosen))
    assert rt.interpreter(repo) == str(chosen)
    monkeypatch.setenv(SETTING, "  ")   # set to nothing: not set
    assert rt.interpreter(repo) == str(own)


@pytest.mark.usefixtures("unconfined")
def test_a_run_with_the_setting_says_so_and_names_the_python(repo, monkeypatch):
    monkeypatch.setenv(SETTING, sys.executable)
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["verdict"], found["interpreter_source"], found["python"]) == (rt.PASSED, "setting", sys.executable)
    monkeypatch.delenv(SETTING)
    assert rt.run(repo, ["tests/test_calc.py"])["interpreter_source"] == "g6"


@pytest.mark.parametrize("kind", ["a relative path", "nothing there", "a folder", "a file not named as a Python",
                                  "a file this user cannot run"])
@pytest.mark.usefixtures("unconfined")
def test_a_setting_that_names_no_python_is_refused_before_anything_runs(repo, tmp_path, monkeypatch, kind):
    if kind == "a relative path":
        value = "python3"
    elif kind == "nothing there":
        value = str(tmp_path / "missing" / "python3")
    elif kind == "a folder":
        (tmp_path / "python3").mkdir()
        value = str(tmp_path / "python3")
    elif kind == "a file not named as a Python":
        value = str(_python_file(tmp_path / "bin", "ls"))
    else:
        if os.name == "nt":
            pytest.skip("Windows has no execute permission to take away")
        path = _python_file(tmp_path / "bin")
        path.chmod(0o644)
        value = str(path)
    monkeypatch.setenv(SETTING, value)
    (repo / "tests" / "test_w.py").write_text(WRITES, encoding="utf-8")
    for call in (lambda: rt.interpreter(repo), lambda: rt.run(repo, ["tests/test_w.py"]),
                 lambda: rt.run(repo, ["tests/test_w.py"], mutant=("calc.py", "def add(a, b):\n    return 0\n"))):
        with pytest.raises(rt.RepoRefused) as caught:
            call()
        assert caught.value.code == INVALID and SETTING in str(caught.value), caught.value
    assert not (repo / "ran").exists()


def test_nothing_a_caller_sends_names_the_interpreter_and_the_tests_never_see_the_setting(tmp_path, monkeypatch):
    for function in (rt.run, rt.run_before, rt.run_at_base, patch_review.review_patch):
        named = set(inspect.signature(function).parameters)
        assert not {"python", "interpreter", "executable", SETTING} & named, (function.__name__, named)
    monkeypatch.setenv(SETTING, sys.executable)
    assert SETTING not in rt.child_env(tmp_path, network=False)


def test_confined_a_run_with_a_python_the_person_chose_works_and_its_receipt_binds_it(repo, tmp_path, bwrap_backend,
                                                                                    monkeypatch):
    """A Python outside the repository and other than G6's own: a virtual environment made from G6's base Python that
    sees its packages (pytest among them), as a Poetry environment would."""
    env = tmp_path / "poetry-env"
    # --copies: an environment linked to G6's own Python is refused (L1)
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", "--copies", str(env)],
                   check=True,
                   capture_output=True, timeout=120)
    chosen = env / "bin" / "python"
    assert chosen.is_file() and str(chosen) != sys.executable
    monkeypatch.setenv(SETTING, str(chosen))
    found = rt.run(repo, ["tests/test_calc.py"], receipt=True)
    assert (found["verdict"], found["interpreter_source"], found["python"]) == (rt.PASSED, "setting", str(chosen)), \
        found["output"]
    assert found["isolation"]["backend"] == "bubblewrap" and found["receipt"], found
    body = receipt_store.lookup(found["receipt"], artifact_digest=found["source_before"])
    assert body["executable"]["path"] == str(chosen) and len(body["executable"]["sha256"]) == 64
