# SPDX-License-Identifier: Apache-2.0
"""Disk-backed synthetic boundary regressions; containers are recording fakes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)
COMMIT = "a" * 40


@pytest.mark.parametrize("component", range(4))
@pytest.mark.parametrize("suffix", [":payload", "::$DATA", ":", ".", " "])
def test_ambiguous_component_is_refused_before_read(tmp_path, component, suffix):
    parts = ["run", TASK.id, "001", "final.patch"]
    parts[component] += suffix
    with pytest.raises(ValueError):
        rc.Source(tmp_path).read_bytes("/".join(parts))


@pytest.mark.parametrize("name", ["final-synthetic.json:payload", "sealed:payload/child"])
def test_forbidden_stream_is_refused_without_open(tmp_path, monkeypatch, name):
    def unexpected(*args, **kwargs):
        pytest.fail("forbidden name reached a content open")

    monkeypatch.setattr(Path, "open", unexpected)
    with pytest.raises(ValueError, match="forbidden archive path:"):
        rc.Source(tmp_path).read_bytes(name)


@pytest.mark.parametrize("rel,content", [("payload.txt", b"inside"), ("runs.jsonl", b'{"event":"other"}\n')])
def test_bytes_and_index_listing_do_not_reopen_names(tmp_path, monkeypatch, rel, content):
    (tmp_path / rel).write_bytes(content)
    source = rc.Source(tmp_path)

    def pathname_read(*args, **kwargs):
        pytest.fail("archive content reopened by pathname")

    read_bytes, fdopen = Path.read_bytes, os.fdopen
    dispatches, descriptors = [], []

    def dispatched_read(path):
        dispatches.append(path)
        return read_bytes(path)

    def descriptor_open(fd, *args, **kwargs):
        descriptors.append(os.fstat(fd).st_ino)
        return fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(Path, "open", pathname_read)
    monkeypatch.setattr(Path, "read_bytes", dispatched_read)
    monkeypatch.setattr(os, "fdopen", descriptor_open)
    if rel == "runs.jsonl":
        assert source.rows() == [{"event": "other"}]
    else:
        assert source.read_bytes(rel) == b"inside"
    assert dispatches == [tmp_path / rel]
    assert descriptors == [(tmp_path / rel).stat().st_ino]


@pytest.mark.parametrize("boundary", ["source_path", "read_bytes"])
def test_swap_between_path_gate_and_read_cannot_escape(tmp_path, monkeypatch, boundary):
    root, outside = tmp_path / "source", tmp_path / "outside"
    parent, moved = root / "run", root / "old-run"
    parent.mkdir(parents=True)
    outside.mkdir()
    (parent / "payload.txt").write_bytes(b"inside")
    (outside / "payload.txt").write_bytes(b"outside")
    source = rc.Source(root)
    original = rc.Source.path if boundary == "source_path" else Path.read_bytes
    swapped = []

    def swap():
        if not swapped:
            parent.rename(moved)
            try:
                if os.name == "nt":
                    import _winapi
                    _winapi.CreateJunction(str(outside), str(parent))
                else:
                    parent.symlink_to(outside, target_is_directory=True)
            except OSError:
                moved.rename(parent)
                raise
            swapped.append(True)

    def gate(self, rel):
        checked = original(self, rel)
        swap()
        return checked

    def read(path):
        swap()
        return original(path)

    if boundary == "source_path":
        monkeypatch.setattr(rc.Source, "path", gate)
    else:
        monkeypatch.setattr(Path, "read_bytes", read)
    try:
        try:
            result = source.read_bytes("run/payload.txt")
        except ValueError as exc:
            assert "links are refused" in str(exc)
            result = None
        assert swapped
        assert result in (None, b"inside")
    finally:
        if swapped:
            if os.name == "nt":
                parent.rmdir()
            else:
                parent.unlink()
            moved.rename(parent)


def test_link_refused_before_descendant_metadata(tmp_path, monkeypatch):
    root, outside = tmp_path / "source", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "payload.txt").write_bytes(b"synthetic")
    alias = root / "alias"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(alias))
    else:
        alias.symlink_to(outside, target_is_directory=True)
    original = Path.stat
    descendants = []

    def stat(path, *args, **kwargs):
        if path == alias / "payload.txt":
            descendants.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    try:
        with pytest.raises(ValueError, match="links are refused"):
            rc.Source(root).read_bytes("alias/payload.txt")
        assert descendants == []
    finally:
        alias.rmdir() if os.name == "nt" else alias.unlink()


def synthetic_archive(root, *, check_task=TASK.id, path_task=TASK.id, prefix="check"):
    rows = []
    for stamp, label in (("001", True), ("002", False)):
        run, check = f"run/{TASK.id}/{stamp}", f"{prefix}/{path_task}/{stamp}"
        folder = root / run
        folder.mkdir(parents=True)
        (folder / "final.patch").write_bytes(stamp.encode())
        (folder / "rounds.json").write_text("[]", encoding="utf-8")
        (folder / "prompt.txt").write_text("Issue #496: synthetic issue", encoding="utf-8")
        record = root / check / "run.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"status": "finished", "checks": [
            {"patch": "final", "patch_applied": True, "passes": label}]}), encoding="utf-8")
        rows += [{"event": "started", "kind": "run", "task": TASK.id, "run": run},
                 {"event": "ended", "run": run, "status": "finished"},
                 {"event": "started", "kind": "check", "task": check_task,
                  "run": check, "checked_run": run},
                 {"event": "ended", "run": check, "status": "finished"}]
    (root / "runs.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")


@pytest.mark.parametrize("event", ["started", "ended", "other"])
@pytest.mark.parametrize("field", ["run", "checked_run"])
def test_every_index_destination_has_structural_allowlist(tmp_path, event, field):
    synthetic_archive(tmp_path)
    with (tmp_path / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n" + json.dumps({"event": event, "kind": "check", "task": TASK.id,
                                        "run": f"check/{TASK.id}/003",
                                        field: f"cohort/unseen/check/{TASK.id}/003"}))
    with pytest.raises(ValueError, match="invalid .*path:"):
        rc.enumerate_cases(tmp_path, [TASK])


@pytest.mark.parametrize("kind", ["cohort", "unseen", "unknown"])
def test_unknown_index_kind_is_refused_before_candidate_read(tmp_path, monkeypatch, kind):
    synthetic_archive(tmp_path)
    with (tmp_path / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n" + json.dumps({"event": "started", "kind": kind, "task": TASK.id,
                                       "run": f"{kind}/{TASK.id}/003"}))
    read_bytes = Path.read_bytes

    def read(path):
        assert path.name == "runs.jsonl", "candidate read before index refusal"
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ValueError, match="invalid run path:"):
        rc.enumerate_cases(tmp_path, [TASK])


@pytest.mark.parametrize("check_task,path_task", [
    ("click-3769", "click-3769"), ("click-3769", TASK.id), (TASK.id, "click-3769"), (None, TASK.id),
])
def test_foreign_task_check_is_excluded_in_full_fake_replay(
        tmp_path, monkeypatch, capsys, check_task, path_task):
    source, output = tmp_path / "source", tmp_path / "output"
    synthetic_archive(source, check_task=check_task, path_task=path_task)
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))

    class NoContainers:
        def __init__(self):
            self.calls = []

        def __call__(self, *args, **kwargs):
            self.calls.append(args)
            pytest.fail("foreign checks caused a container operation")

    docker = NoContainers()
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                   docker=docker) == 0
    evaluation = json.loads(capsys.readouterr().out)
    saved = json.loads(next(output.glob("replay-rc-batch/development/*/run.json")).read_text(encoding="utf-8"))
    assert saved["records"] == []
    assert docker.calls == []
    assert all(item["reason"] == "foreign archived check" for item in saved["plan"]["excluded"])
    assert evaluation["by_bug"][TASK.id]["hook"]["reviewed"] == 0
    assert evaluation["by_bug"][TASK.id]["hook"]["missing"] == 2


def test_same_task_checks_still_supply_labels(tmp_path):
    synthetic_archive(tmp_path)
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert excluded == []
    assert {case.label for case in cases} == {"right", "wrong"}
    assert len(cases) == 2


@pytest.mark.parametrize("kind", [
    "build", "dryrun", "validate", "amend", "recheck", "selfcheck", "g6replay",
    "replay", "modelcheck", "freecheck",
])
def test_real_harness_index_kinds_are_metadata_only(tmp_path, monkeypatch, kind):
    synthetic_archive(tmp_path)
    # Archive.start uses this UTC stamp, with -N when the clock repeats.
    rel = f"{kind}/{TASK.id}/20261029T123456123456Z-1"
    with (tmp_path / "runs.jsonl").open("a", encoding="utf-8") as stream:
        for row in [
            {"event": "started", "kind": kind, "task": TASK.id, "run": rel,
             **({"checked_run": f"run/{TASK.id}/001"} if kind == "recheck" else {})},
            {"event": "ended", "run": rel, "status": "finished"},
        ]:
            stream.write("\n" + json.dumps(row))
    read_bytes = Path.read_bytes

    def read(path):
        assert kind not in path.relative_to(tmp_path).parts, "ancillary record supplied candidate content"
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert excluded == []
    assert {case.label for case in cases} == {"right", "wrong"}
    assert len(cases) == 2


@pytest.mark.parametrize("part", ["", "round2", "round3"])
def test_archive_writer_shapes_remain_in_scope(tmp_path, monkeypatch, part):
    # The actual writer supplies task fields and timestamped paths. Freeze its
    # clock so the second candidate also exercises the writer's -1 suffix.
    datetime = rc.c.dt.datetime

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 29, 12, 34, 56, 123456, tzinfo=tz)

    monkeypatch.setattr(rc.c.dt, "datetime", Clock)
    archive = rc.c.Archive(tmp_path / part)
    for label in (True, False):
        run = archive.start("run", TASK.id)
        run.write("final.patch", b"right" if label else b"wrong")
        run.write_json("rounds.json", [])
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.finish({}, "finished")
        check = archive.start("check", TASK.id, checked_run=run.rel)
        check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": label}]}, "finished")
    for kind, task in [
        ("build", "base"), ("build", TASK.id), ("dryrun", TASK.id),
        ("validate", TASK.id), ("amend", TASK.id), ("recheck", TASK.id),
        ("selfcheck", TASK.id), ("g6replay", TASK.id), ("replay", TASK.id),
        ("modelcheck", TASK.id), ("freecheck", TASK.id),
    ]:
        archive.start(kind, task).finish({}, "finished")

    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert excluded == []
    assert {case.label for case in cases} == {"right", "wrong"}
    origins = {origin["run"] for case in cases for origin in case.origins}
    prefix = f"{part}/" if part else ""
    assert origins == {f"{prefix}run/black-5634/20261029T123456123456Z",
                       f"{prefix}run/black-5634/20261029T123456123456Z-1"}


def test_score_uses_same_handle_boundary(tmp_path, monkeypatch, capsys):
    # A score file outside an archive layout is still an explicit CLI input.
    score = tmp_path / "batch.json"
    score.write_text("{}", encoding="utf-8")
    def pathname_read(*args, **kwargs):
        pytest.fail("score reopened a pathname for content")

    monkeypatch.setattr(Path, "open", pathname_read)
    monkeypatch.setattr(rc, "validate_record_pins", lambda *args: None)
    # Input parsing reaches the absent records key only after a safe handle read.
    with pytest.raises(KeyError, match="records"):
        rc.main(["--score", str(score)])
    assert capsys.readouterr().err == ""
