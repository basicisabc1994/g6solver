# SPDX-License-Identifier: Apache-2.0
"""CLI refuses an indexless folder; the enumeration API retains its empty result."""
from __future__ import annotations

import json

import pytest

from worked_examples import replay_rc as rc


@pytest.mark.parametrize("dry", [True, False])
def test_no_index_refuses_before_any_output(tmp_path, monkeypatch, capsys, dry):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", "a" * 40,
                    *(["--dry"] if dry else [])]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("replay_rc:") and "runs.jsonl" in captured.err
    assert not captured.out and not output.exists()


def test_empty_enumeration_remains_compatible(tmp_path):
    assert rc.enumerate_cases(tmp_path, []) == ([], [])


@pytest.mark.parametrize("part", ["", "round2", "round3"])
def test_any_present_index_distinguishes_an_empty_archive(tmp_path, monkeypatch, capsys, part):
    folder = tmp_path / part
    folder.mkdir(exist_ok=True)
    (folder / "runs.jsonl").write_bytes(b"")
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [])
    assert rc.main(["--source", str(tmp_path), "--registered-commit", "a" * 40, "--dry"]) == 0
    assert json.loads(capsys.readouterr().out)["cases"] == []
