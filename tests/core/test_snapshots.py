# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import zipfile

import pytest

from g6.cli import main
from g6.core import snapshots
from g6.core.storage import g6_home, open_db, state_path


@pytest.fixture(autouse=True)
def _fresh_home(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))


def _seed():
    db = open_db("thing.db")
    db.execute("create table t (v text)")
    db.execute("insert into t values ('one')")
    db.commit()
    db.close()
    state_path("bundles").mkdir(parents=True, exist_ok=True)
    state_path("bundles", "settings.json").write_text('{"api_key": "sk-secret"}')
    state_path("notes.txt").write_text("v1")


def test_snapshot_and_rollback_restore_exactly():
    _seed()
    snap = snapshots.snapshot("baseline")
    db = open_db("thing.db")
    db.execute("insert into t values ('two')")
    db.commit()
    db.close()
    state_path("notes.txt").write_text("v2")
    state_path("new.txt").write_text("added later")
    restored, safety = snapshots.rollback(snap.name)
    assert restored.name == snap.name and "before-rollback" in safety.name
    assert state_path("notes.txt").read_text() == "v1" and not state_path("new.txt").exists()
    conn = open_db("thing.db")
    rows = conn.execute("select v from t").fetchall()
    conn.close()                          # an open handle blocks the next rollback on Windows
    assert [r[0] for r in rows] == ["one"]
    snapshots.rollback(safety.name)       # the rollback itself can be undone
    assert state_path("new.txt").exists()


def test_export_leaves_out_keys(tmp_path):
    _seed()
    out = snapshots.export(tmp_path / "share.zip")
    names = zipfile.ZipFile(out).namelist()
    assert "notes.txt" in names and "bundles/settings.json" not in names
    with pytest.raises(FileExistsError):
        snapshots.export(out)
    with pytest.raises(ValueError):
        snapshots.export(g6_home() / "inside.zip")


def test_the_persons_key_file_is_never_in_a_snapshot_and_a_rollback_leaves_it_alone(tmp_path):
    # owner ruling 2026-10-21: the person writes $G6_HOME/.env and G6 never writes it; a rollback that emptied the state
    # folder deleted the key file and put back whatever copy the snapshot held
    _seed()
    key_file = g6_home() / ".env"
    key_file.write_text("OPENROUTER_API_KEY=first\n", encoding="utf-8")
    snap = snapshots.snapshot("with-a-key-file")
    assert ".env" not in zipfile.ZipFile(snap.path).namelist()
    key_file.write_text("OPENROUTER_API_KEY=second\n", encoding="utf-8")
    snapshots.rollback(snap.name)
    assert key_file.read_text(encoding="utf-8") == "OPENROUTER_API_KEY=second\n"
    assert ".env" not in zipfile.ZipFile(snapshots.export(tmp_path / "share.zip")).namelist()


def test_a_snapshot_that_holds_a_key_file_is_refused_before_touching_state():
    _seed()
    (g6_home() / ".env").write_text("A_KEY=mine\n", encoding="utf-8")
    folder = g6_home() / snapshots.SNAPSHOT_DIR
    folder.mkdir(exist_ok=True)
    with zipfile.ZipFile(folder / "planted.zip", "w") as zf:
        zf.writestr(".env", "A_KEY=planted\n")
    with pytest.raises(ValueError):
        snapshots.rollback("planted")
    assert (g6_home() / ".env").read_text(encoding="utf-8") == "A_KEY=mine\n"
    assert state_path("notes.txt").read_text() == "v1"


def test_rollback_refuses_unsafe_archive_before_touching_state():
    _seed()
    folder = g6_home() / snapshots.SNAPSHOT_DIR
    folder.mkdir(exist_ok=True)
    with zipfile.ZipFile(folder / "evil.zip", "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError):
        snapshots.rollback("evil")
    assert state_path("notes.txt").read_text() == "v1"
    assert not (g6_home().parent / "escape.txt").exists()


def test_cli(capsys):
    assert main(["state", "snapshot"]) == 1          # nothing to snapshot yet
    _seed()
    assert main(["state", "snapshot", "--label", "first try"]) == 0
    assert main(["state", "ls"]) == 0
    out = capsys.readouterr().out
    assert "first_try" in out
    assert main(["state", "rollback", "nope"]) == 1
