# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What a repository's tests see of the environment. G6 gives them only a few system variables (and the operating
system's boundary strips the rest), so a repository that needs one of its own (qutebrowser's ``QUTE_QT_WRAPPER``,
``QT_QPA_PLATFORM``) could not run; and Hypothesis writes its database beside the tests, which a confined run keeps
read only.

Pinned: ``G6_REPO_ENV`` in G6's own environment (a JSON object, names to text) reaches the tests, set by G6's own
bootstrap before pytest and the repository are imported; names that could change the runner or escape G6's control are
refused before anything runs; the run, the review's scope and the receipt record each name with the SHA-256 of its
value, never the value; ``HYPOTHESIS_STORAGE_DIRECTORY`` points at the run's own writable folder unless the setting
names it, and the run says G6 set it. Nothing a caller sends sets these. Offline; real git and pytest; the confined
cases run under bubblewrap (Docker on Windows)."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest
from g6.harness import patch_review
from g6.workforce import receipt_store
from g6.workforce import repo_tests as rt

SETTING = "G6_REPO_ENV"
INVALID = "G6_E_REPO_ENV_INVALID"
SECRET = "s3cret-value-never-reported"
READS = ("import os\n\n\ndef test_reads():\n    assert os.environ['QUTE_QT_WRAPPER'] == 'PyQt6'\n"
         f"    assert os.environ['API_TOKEN'] == {SECRET!r}\n")
STORAGE = ("import os\nfrom pathlib import Path\n\n\ndef test_storage():\n"
           "    folder = Path(os.environ['HYPOTHESIS_STORAGE_DIRECTORY'])\n"
           "    assert not folder.resolve().is_relative_to(Path(__file__).resolve().parents[1])\n"
           "    (folder / 'written').write_text('')\n")
#: A Hypothesis test where warnings are errors (qutebrowser's settings): it fails when Hypothesis cannot write its
#: database.
GIVEN = ("from hypothesis import given, strategies as st\n\n\n@given(st.integers())\n"
         "def test_given(n):\n    assert n == n\n")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **{key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   check=True, capture_output=True, env=GIT_ENV)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv(SETTING, raising=False)
    repo = tmp_path / "app"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "tests" / "test_reads.py").write_text(READS, encoding="utf-8")
    (repo / "tests" / "test_storage.py").write_text(STORAGE, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _setting(monkeypatch, **values: str) -> None:
    monkeypatch.setenv(SETTING, json.dumps(values))


@pytest.mark.usefixtures("unconfined")
def test_a_variable_the_setting_names_reaches_the_tests_and_only_its_digest_is_reported(repo, monkeypatch):
    _setting(monkeypatch, QUTE_QT_WRAPPER="PyQt6", API_TOKEN=SECRET)
    found = rt.run(repo, ["tests/test_reads.py"])
    assert found["verdict"] == rt.PASSED, found["output"]
    assert found["repo_env"] == {"API_TOKEN": _sha(SECRET), "QUTE_QT_WRAPPER": _sha("PyQt6")}
    assert SECRET not in json.dumps(found)
    monkeypatch.delenv(SETTING)
    assert rt.run(repo, ["tests/test_reads.py"])["verdict"] == rt.FAILED   # survival: without it the test fails


@pytest.mark.usefixtures("unconfined")
def test_hypothesis_stores_its_database_in_the_runs_own_folder_unless_the_setting_names_one(repo, tmp_path,
                                                                                           monkeypatch):
    found = rt.run(repo, ["tests/test_storage.py"])
    assert found["verdict"] == rt.PASSED, found["output"]
    assert found["env_set_by_g6"] == ["HYPOTHESIS_STORAGE_DIRECTORY"] and found["repo_env"] == {}
    chosen = tmp_path / "my-hypothesis"
    chosen.mkdir()
    _setting(monkeypatch, HYPOTHESIS_STORAGE_DIRECTORY=str(chosen))
    mine = rt.run(repo, ["tests/test_storage.py"])
    assert mine["verdict"] == rt.PASSED and (chosen / "written").exists(), mine["output"]
    assert mine["env_set_by_g6"] == [] and list(mine["repo_env"]) == ["HYPOTHESIS_STORAGE_DIRECTORY"]


@pytest.mark.parametrize("name", ["PYTHONPATH", "pythonhome", "PYTHONSTARTUP", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
                                  "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "G6_ALLOW_UNCONFINED", "PATH", "HOME",
                                  "USERPROFILE", "TMP", "TEMP", "TMPDIR", "COV_CORE_SOURCE", "A=B", "A\0B", ""])
@pytest.mark.usefixtures("unconfined")
def test_a_name_that_could_change_the_runner_is_refused_before_anything_runs(repo, monkeypatch, name):
    (repo / "tests" / "test_w.py").write_text("from pathlib import Path\n\n\ndef test_w():\n"
                                              "    Path(__file__).parents[1].joinpath('ran').write_text('')\n",
                                              encoding="utf-8")
    monkeypatch.setenv(SETTING, json.dumps({"QUTE_QT_WRAPPER": "PyQt6", name: SECRET}))
    for call in (lambda: rt.run(repo, ["tests/test_w.py"]),
                 lambda: rt.run(repo, ["tests/test_w.py"], mutant=("calc.py", "def add(a, b):\n    return 0\n"))):
        with pytest.raises(rt.RepoRefused) as caught:
            call()
        assert caught.value.code == INVALID and SETTING in str(caught.value) and SECRET not in str(caught.value)
    assert not (repo / "ran").exists()


@pytest.mark.parametrize("raw", ["not json", "[\"QUTE_QT_WRAPPER\"]", "{\"QUTE_QT_WRAPPER\": 6}",
                                 "{\"QUTE_QT_WRAPPER\": \"Py\\u0000Qt6\"}"])
def test_a_setting_that_is_not_an_object_of_names_to_text_is_refused(repo, monkeypatch, raw):
    monkeypatch.setenv(SETTING, raw)
    with pytest.raises(rt.RepoRefused) as caught:
        rt.run(repo, ["tests/test_reads.py"])
    assert caught.value.code == INVALID and "Qt6" not in str(caught.value)   # a value is never repeated


def test_nothing_a_caller_sends_sets_the_variables(tmp_path, monkeypatch):
    for function in (rt.run, rt.run_before, rt.run_at_base, patch_review.review_patch):
        named = set(inspect.signature(function).parameters)
        assert not {"env", "environment", "variables", SETTING} & named, (function.__name__, named)
    _setting(monkeypatch, QUTE_QT_WRAPPER="PyQt6")
    assert SETTING not in rt.child_env(tmp_path, network=False)


# --- confined (Linux bubblewrap; Docker on Windows) ------------------------------------------------------------------


def test_confined_the_variables_reach_the_tests_and_the_receipt_holds_only_digests(repo, bwrap_backend, monkeypatch):
    _setting(monkeypatch, QUTE_QT_WRAPPER="PyQt6", API_TOKEN=SECRET)
    found = rt.run(repo, ["tests/test_reads.py", "tests/test_storage.py"], receipt=True)
    assert found["verdict"] == rt.PASSED and found["isolation"]["backend"] == "bubblewrap", found["output"]
    body = receipt_store.lookup(found["receipt"], artifact_digest=found["source_before"])
    assert body["repo_env"] == {"API_TOKEN": _sha(SECRET), "QUTE_QT_WRAPPER": _sha("PyQt6")}
    assert body["env_set_by_g6"] == ["HYPOTHESIS_STORAGE_DIRECTORY"]
    assert SECRET not in json.dumps(body) and SECRET not in json.dumps(found)


def test_confined_a_hypothesis_test_runs_in_a_read_only_repository_where_warnings_are_errors(repo, bwrap_backend):
    (repo / "tests" / "test_given.py").write_text(GIVEN, encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\nfilterwarnings =\n    error\n", encoding="utf-8")
    found = rt.run(repo, ["tests/test_given.py"])
    assert found["verdict"] == rt.PASSED, found["output"]
    assert not (repo / ".hypothesis").exists()


def test_confined_a_review_reports_the_variables_by_digest_only(tmp_path, bwrap_backend, monkeypatch):
    repo = tmp_path / "calc"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text("import os\nfrom calc import add\n\n\ndef test_add():\n"
                                       "    assert os.environ['API_TOKEN']\n    assert add(2, 3) == 5\n",
                                       encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _setting(monkeypatch, API_TOKEN=SECRET)
    got = patch_review.review_patch(repo, task="Repair addition.", requirements=[
        {"id": "R1", "text": "add returns the sum", "checks": ["test_calc.py::test_add"]}])
    environment = got["assessment"]["scope"]["environment"]
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]
    assert environment["repo_env"] == {"API_TOKEN": _sha(SECRET)}
    assert environment["env_set_by_g6"] == ["HYPOTHESIS_STORAGE_DIRECTORY"]
    assert SECRET not in json.dumps(got)
