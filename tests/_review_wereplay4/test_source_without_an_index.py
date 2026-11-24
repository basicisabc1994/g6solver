"""Review 4: a source folder that holds no archive index is a successful, finished replay.

Repair 3 made an absent source root exit 1. An existing folder with no ``runs.jsonl`` in the root,
``round2`` or ``round3`` (a mistyped ``--source``, such as the archive's parent) is still read as an
archive with nothing in it: exit 0, and in a live run a new output archive and a ``finished`` batch.

Synthetic folders and a fake container client only: no Docker, no model, no real archive.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

TREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TREE / "validation"))
from worked_examples import replay_rc as rc  # noqa: E402

COMMIT = "a" * 40
TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


class Docker:
    def __call__(self, *args, **kwargs):
        return subprocess.CompletedProcess(args, 0, b"[]", b"")


def not_an_archive(tmp_path: Path) -> Path:
    folder = tmp_path / "not-an-archive"
    (folder / "notes").mkdir(parents=True)
    (folder / "notes" / "readme.txt").write_text("no index here", encoding="utf-8")
    return folder


def test_dry_plan_of_a_folder_with_no_index_is_not_a_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    code = rc.main(["--source", str(not_an_archive(tmp_path)), "--registered-commit", COMMIT, "--dry"])
    printed = capsys.readouterr().out
    assert code == 1, f"exit {code} with a plan of {len(json.loads(printed)['cases'])} cases and no exclusion"


def test_live_replay_of_a_folder_with_no_index_saves_no_finished_batch(tmp_path, monkeypatch, capsys):
    output = tmp_path / "output"
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    code = rc.main(["--source", str(not_an_archive(tmp_path)), "--registered-commit", COMMIT, "--mode", "hook"],
                   docker=Docker())
    capsys.readouterr()
    finished = [json.loads(path.read_text(encoding="utf-8"))["status"]
                for path in output.glob("replay-rc-batch/development/*/run.json")]
    assert code == 1 and "finished" not in finished, f"exit {code}; saved batch statuses {finished}"
