# SPDX-License-Identifier: Apache-2.0
"""Snapshot callers retain the refusal contract before identity or execution work."""
from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc


@pytest.mark.parametrize("caller", ["snapshot", "dry", "live"])
@pytest.mark.parametrize("part", ["", "round2", "round3"])
@pytest.mark.parametrize("field", ["run", "checked_run"])
@pytest.mark.skipif(os.name != "nt", reason='Windows trailing-dot sealed alias semantics')
def test_index_refusal_precedes_snapshot_and_execution(monkeypatch, capsys, caller, part, field):
    # Moving identity work before the all-round index preflight must fail here.
    root = Path(__file__).absolute().parents[2] / ".build" / "snapshot-preflight-fake"
    row = {"event": "started", "kind": "check", "run": "check/black-5634/001",
           "checked_run": "run/black-5634/001"}
    row[field] = "sealed./checker"
    files = {root / "runs.jsonl": b"", root / part / "runs.jsonl": json.dumps(row).encode() + b"\n"}
    reads = []
    tasks = [SimpleNamespace(id="black-5634")]
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **k: path)
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(Path, "is_junction", lambda path: False)

    @contextmanager
    def opened(path, **kwargs):
        path = rc._absolute_path(path.resolve())
        yield rc._Opened(path, None, stat.S_IFREG if path in files else None)

    def read(source, rel):
        path = source.path(rel)
        if path not in files:
            raise FileNotFoundError(str(path))
        assert path.name == "runs.jsonl", f"candidate read before index refusal: {path}"
        reads.append(path)
        return files[path]

    def no_work(*args, **kwargs):
        pytest.fail("index refusal must precede snapshot hash, candidate I/O and process work")

    monkeypatch.setattr(rc, "_open_handle", opened)
    monkeypatch.setattr(rc.Source, "read_bytes", read)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: tasks)
    monkeypatch.setattr(rc.hashlib, "sha256", no_work)
    monkeypatch.setattr(rc.subprocess, "run", no_work)
    monkeypatch.setattr(rc.we, "Docker", no_work)
    for method in ("open", "read_text", "iterdir", "glob", "rglob"):
        monkeypatch.setattr(Path, method, no_work)

    if caller == "snapshot":
        snapshot = {}
        with pytest.raises(ValueError, match="forbidden archive path:"):
            rc.enumerate_cases(root, tasks, require_index=True, source_snapshot=snapshot)
        assert snapshot == {}
    else:
        arguments = ["--source", str(root), "--registered-commit", "a" * 40]
        if caller == "dry":
            arguments.append("--dry")
        assert rc.main(arguments) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("replay_rc: forbidden archive path:")
    expected = [root / "runs.jsonl"]
    if part:
        expected.append(root / part / "runs.jsonl")
    assert reads == expected
