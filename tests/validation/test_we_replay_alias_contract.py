# SPDX-License-Identifier: Apache-2.0
"""Refusal contract checks; every filesystem read and process is a fake."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

COMMIT = "a" * 40
ROOT = Path(__file__).absolute().parents[2] / ".build" / "alias-contract-fake"


@pytest.fixture
def memory(monkeypatch):
    files, reads = {}, []
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **k: path)
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(Path, "is_junction", lambda path: False)
    monkeypatch.setattr(Path, "is_file", lambda path: path in files)

    def read(path):
        reads.append(path)
        assert path in files, f"unexpected read: {path}"
        assert path.name == "runs.jsonl", f"candidate read before full path preflight: {path}"
        return files[path]

    def no_io(*args, **kwargs):
        pytest.fail("refused input reached an open, listing, hash or process")

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "open", no_io)
    monkeypatch.setattr(Path, "read_text", no_io)
    monkeypatch.setattr(Path, "iterdir", no_io)
    monkeypatch.setattr(Path, "glob", no_io)
    monkeypatch.setattr(Path, "rglob", no_io)
    monkeypatch.setattr(rc.hashlib, "sha256", no_io)
    monkeypatch.setattr(rc.subprocess, "run", no_io)
    monkeypatch.setattr(rc.we, "Docker", no_io)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [SimpleNamespace(id="black-5634")])
    return files, reads


def index(files, root, rows):
    files[root / "runs.jsonl"] = b"".join(json.dumps(row).encode() + b"\n" for row in rows)


@pytest.mark.parametrize("part", ["", "round2", "round3"])
@pytest.mark.parametrize("field", ["run", "checked_run"])
def test_index_alias_has_stable_refusal_before_candidate_io(memory, capsys, part, field):
    files, reads = memory
    source = ROOT / "source"
    row = {"event": "started", "kind": "check", "run": "check/black-5634/001",
           "checked_run": "run/black-5634/001"}
    row[field] = "sealed./checker"
    index(files, source / part, [row])

    with pytest.raises(ValueError, match="forbidden archive path:"):
        rc.enumerate_cases(source, rc.we.load_tasks())
    assert reads == [source / part / "runs.jsonl"]
    reads.clear()

    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--dry"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("replay_rc: forbidden archive path:")
    assert reads == [source / part / "runs.jsonl"]


@pytest.mark.parametrize("part", ["round2", "round3"])
def test_later_index_refusal_precedes_all_candidate_reads(memory, part):
    files, reads = memory
    source = ROOT / "source"
    run, check = "run/black-5634/001", "check/black-5634/001"
    index(files, source, [
        {"event": "started", "kind": "run", "task": "black-5634", "run": run},
        {"event": "started", "kind": "check", "run": check, "checked_run": run},
        {"event": "ended", "run": check, "status": "finished"},
    ])
    # A valid earlier candidate must not be partially read before the refusal.
    files[source / run / "final.patch"] = b"synthetic patch"
    files[source / check / "run.json"] = json.dumps({
        "status": "finished", "checks": [{"patch": "final", "patch_applied": True, "passes": False}],
    }).encode()
    files[source / run / "rounds.json"] = b"[]"
    files[source / run / "prompt.txt"] = b"Issue #496: synthetic issue"
    index(files, source / part, [{"event": "started", "kind": "check", "run": "sealed./checker"}])

    with pytest.raises(ValueError, match="forbidden archive path:"):
        rc.enumerate_cases(source, rc.we.load_tasks())
    assert reads == [source / "runs.jsonl", source / part / "runs.jsonl"]


@pytest.mark.parametrize("alias", ["final-cohort.json.", "FiNaL-cohort.JsOn ", "sealed./batch.json"])
def test_score_alias_returns_refusal_without_open(memory, capsys, alias):
    _, reads = memory
    assert rc.main(["--score", str(ROOT / alias)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("replay_rc: forbidden archive path:")
    assert reads == []


@pytest.mark.parametrize("target", ["final-cohort.json. ", "sealed. /batch.json"])
def test_resolved_score_alias_returns_refusal_without_open(memory, monkeypatch, capsys, target):
    _, reads = memory
    score = ROOT / "batch.json"
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **k: ROOT / target if path == score else path)
    assert rc.main(["--score", str(score)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("replay_rc: forbidden archive path:")
    assert reads == []


def test_resolved_later_index_is_refused_before_any_index_read(memory, monkeypatch):
    files, reads = memory
    source = ROOT / "source"
    index(files, source, [])
    alias = source / "round3" / "runs.jsonl"
    monkeypatch.setattr(Path, "resolve", lambda path, *a, **k:
                        source / "sealed" / "runs.jsonl" if path == alias else path)
    with pytest.raises(ValueError, match="forbidden archive path:"):
        rc.enumerate_cases(source, rc.we.load_tasks())
    assert reads == []
