# SPDX-License-Identifier: Apache-2.0
"""Collection uses the ordinary runner's interpreter preflight before any work."""
import os
from pathlib import Path

import pytest

from g6.workforce import repo_tests as rt


@pytest.mark.parametrize("proposed", [False, True])
@pytest.mark.parametrize("named", ["folder", "bin"])
def test_collection_refuses_unsafe_repository_environment_before_its_window(tmp_path, monkeypatch, proposed, named):
    monkeypatch.delenv(rt.REPO_PYTHON, raising=False)
    root = tmp_path / "repo"
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    outside = tmp_path / "people" / "someone" / ".ssh"
    outside.mkdir(parents=True)
    home = outside / "bin" if named == "bin" else outside
    (root / ".venv" / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
    monkeypatch.setattr(rt, "checked_repo", lambda repo: Path(repo))

    def no_work(*args, **kwargs):
        pytest.fail("collection entered its work window before refusing the unsafe interpreter")

    monkeypatch.setattr(rt, "within", no_work)
    with pytest.raises(rt.RepoRefused) as ordinary:
        rt.run(root, ["test_api.py"])
    with pytest.raises(rt.RepoRefused) as collection:
        rt.collect(root, ["test_api.py"], sources={"test_api.py": "def test_call(): pass\n"} if proposed else None)
    assert ordinary.value.code == collection.value.code == rt.REPO_PYTHON_INVALID
    assert str(ordinary.value) == str(collection.value)
    assert ".ssh" in str(collection.value)


def test_collection_checks_runner_environment_before_its_window(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "checked_repo", lambda repo: Path(repo))
    monkeypatch.setattr(rt, "interpreter", lambda repo: "python")
    monkeypatch.setenv(rt.REPO_ENV, "not JSON")
    with pytest.raises(rt.RepoRefused) as ordinary:
        rt.run(tmp_path, ["test_api.py"])
    with pytest.raises(rt.RepoRefused) as collection:
        rt.collect(tmp_path, ["test_api.py"])
    assert ordinary.value.code == collection.value.code == rt.REPO_ENV_INVALID
