# SPDX-License-Identifier: Apache-2.0
"""Bad source roots refuse dry/live execution before any output or container."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc


@pytest.mark.parametrize("dry", [True, False])
@pytest.mark.parametrize("problem", ["missing", "unreadable", "file"])
def test_bad_source_root_refuses_without_writes(tmp_path, monkeypatch, capsys, dry, problem):
    source, output = tmp_path / "source", tmp_path / "output"
    if problem == "file":
        source.write_bytes(b"not a directory")
    if problem == "unreadable":
        source.mkdir()
        original = rc._open_handle

        @contextmanager
        def opened(path, **kwargs):
            if rc._absolute_path(path) == source:
                raise PermissionError("synthetic source denial")
            with original(path, **kwargs) as handle:
                yield handle

        monkeypatch.setattr(rc, "_open_handle", opened)
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    calls = []
    code = rc.main(["--source", str(source), "--registered-commit", "a" * 40,
                    *(["--dry"] if dry else [])], docker=lambda *a, **k: calls.append(a))
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.strip() == "replay_rc: source root is missing or unreadable"
    assert captured.out == ""
    assert not output.exists()
    assert calls == []


def test_existing_empty_source_and_absent_optional_rounds_still_plan(tmp_path):
    assert rc.enumerate_cases(tmp_path, []) == ([], [])


def test_explicit_path_byte_reader_source_remains_supported(tmp_path, monkeypatch):
    root = tmp_path / "virtual-source"
    original = Path.read_bytes

    def read(path):
        if path == root / "runs.jsonl":
            return b""
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert rc.enumerate_cases(root, []) == ([], [])
