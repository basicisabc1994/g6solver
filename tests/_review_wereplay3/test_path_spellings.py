"""Review 3: ordinary spellings of the source and output roots that the name gate now refuses.

Synthetic folders only: no container, no model, no real archive, no confined run.
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from worked_examples import replay_rc as rc  # noqa: E402

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


def labelled_run(archive):
    run = archive.start("run", TASK.id, arm="B")
    run.write("final.patch", b"synthetic patch")
    run.write_json("rounds.json", [])
    run.write("prompt.txt", "Issue #496: synthetic issue")
    run.finish({}, "finished")
    check = archive.start("check", TASK.id, checked_run=run.rel)
    check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": False}]}, "finished")


def test_source_given_relative_to_a_parent_folder_is_read(tmp_path, monkeypatch):
    labelled_run(rc.c.Archive(tmp_path / "source"))
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    cases, excluded = rc.enumerate_cases(Path("..") / "source", [TASK])
    assert excluded == []
    assert [case.label for case in cases] == ["wrong"]


def test_output_root_given_relative_to_a_parent_folder_is_accepted(tmp_path, monkeypatch):
    (tmp_path / "source").mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    archive = rc.output_archive(tmp_path / "source", Path("..") / "output")
    assert isinstance(archive, rc.c.Archive)


def test_posix_folder_with_a_colon_is_not_a_forbidden_name():
    # The name gate is the same code on every platform (only the UNC branch is Windows-only).
    # A colon names a stream on NTFS; on POSIX it is an ordinary character, common in dated folders.
    rc._check_names(PurePosixPath("/srv/replay/out-2026-11-24T10:00:00/archive/runs.jsonl"))
