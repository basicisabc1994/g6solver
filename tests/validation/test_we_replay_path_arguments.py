# SPDX-License-Identifier: Apache-2.0
"""Normalize public path arguments before platform-specific name checks."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from worked_examples import replay_rc as rc


@pytest.mark.parametrize("argument", ["source", "output", "score", "registration_repo"])
@pytest.mark.parametrize("spelling", ["parent", "colon"])
def test_each_path_argument_accepts_ordinary_spellings(tmp_path, monkeypatch, capsys, argument, spelling):
    if spelling == "colon" and os.name == "nt":
        pytest.skip("colons are stream names on Windows")
    base = tmp_path / ("dated:folder" if spelling == "colon" else "folder")
    base.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    path = Path("..") / base.name if spelling == "parent" else base
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    if argument == "source":
        assert rc.enumerate_cases(path, []) == ([], [])
    elif argument == "output":
        assert rc.output_archive(base, path / ".." / "output").root == tmp_path / "output"
    elif argument == "score":
        monkeypatch.setattr(rc, "load_registration", lambda: registration)
        saved = {"records": [], "plan": {"cases": [], "modes": ["hook"], "registered_commit": "a" * 40},
                 "image_ids": {}, "s0": "before", "registration_sha256": registration.sha256}
        (base / "score.json").write_text(json.dumps(saved), encoding="utf-8")
        assert rc.main(["--score", str(path / "." / "score.json")]) == 2
        assert json.loads(capsys.readouterr().out)["predictions"]["P4"]["prediction_met"] is None
    else:
        content = rc.REGISTRATION.read_bytes()
        (base / "REPLAY_RC.md").write_bytes(content)
        monkeypatch.setattr(rc.we, "REPO_ROOT", path)
        monkeypatch.setattr(rc, "REGISTRATION", path / "REPLAY_RC.md")
        monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k:
                            subprocess.CompletedProcess(a, 0, content, b""))
        assert rc.load_registration().sha256 == registration.sha256


@pytest.mark.parametrize("argument", ["source", "output", "score", "registration_repo"])
def test_posix_name_gate_accepts_colon_and_trailing_dot_or_space(argument):
    rc._check_names(PurePosixPath("/synthetic/dated:folder/tail. /" + argument))


@pytest.mark.parametrize("suffix", [":stream", ".", " "])
def test_windows_name_gate_still_refuses_ambiguous_components(suffix):
    with pytest.raises(ValueError, match="forbidden archive path"):
        rc._check_names(PureWindowsPath("C:/synthetic/folder" + suffix + "/file"))
