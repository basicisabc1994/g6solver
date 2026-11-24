# SPDX-License-Identifier: Apache-2.0
"""Saved evidence is usable only under its own rule or a fresh source reading."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)
COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64


class Docker:
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            value = {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, *args, **kwargs):
        value = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")


@pytest.fixture
def batch_factory(tmp_path, monkeypatch, capsys):
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *args: (f"g6we-task:{TASK.id}-synthetic", "unused"))

    def make(*, missing_final=False):
        source, output = tmp_path / "source", tmp_path / "output"
        archive = rc.c.Archive(source)
        runs, checks = [], []
        for passes in (True, False):
            run = archive.start("run", TASK.id, arm="B")
            run.write("final.patch", b"right" if passes else b"wrong")
            run.write("prompt.txt", "Issue #496: synthetic issue")
            run.write_json("rounds.json", [])
            run.finish({}, "finished")
            check = archive.start("check", TASK.id, checked_run=run.rel)
            check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
            runs.append(run)
            checks.append(source / check.rel / "run.json")
        if missing_final:
            archive.start("check", TASK.id, checked_run=runs[0].rel).finish({"checks": [
                {"patch": "base", "patch_applied": True, "passes": False}]}, "finished")
        monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
        assert rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                       docker=Docker()) == 0
        live = json.loads(capsys.readouterr().out)
        path = next(output.glob("replay-rc-batch/development/*/run.json"))
        saved = json.loads(path.read_text(encoding="utf-8"))
        return source, path, saved, live, checks

    return make


def save(path, saved):
    path.write_text(json.dumps(saved), encoding="utf-8")


@pytest.mark.parametrize("missing_final", [False, True])
def test_current_batch_records_rule_and_rescores_without_source(batch_factory, capsys, missing_final):
    source, path, saved, live, _ = batch_factory(missing_final=missing_final)
    assert type(saved["evidence_rule_version"]) is int
    assert saved["evidence_rule_version"] == rc.EVIDENCE_RULE_VERSION
    assert saved["plan"]["source_snapshot"]["files"]["runs.jsonl"]
    source.rename(source.with_name("moved-source"))
    before = path.read_bytes()
    assert rc.main(["--score", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == live
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", [None, 0, 999, "1", True])
def test_unbound_legacy_batch_is_incomplete_for_every_enabled_prediction(batch_factory, capsys, version):
    _, path, saved, _, _ = batch_factory()
    saved.pop("evidence_rule_version", None)
    if version is not None:
        saved["evidence_rule_version"] = version
    saved["plan"].pop("source_snapshot", None)
    saved["plan"]["modes"] = list(rc.MODES)
    for record in saved["records"]:
        record["review"]["assessment"]["outcome"] = "test_integrity_compromised"
    save(path, saved)
    before = path.read_bytes()
    assert rc.main(["--score", str(path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert all(p["status"] == "incomplete" and p["prediction_met"] is None and p["kill"] is None
               for p in result["predictions"].values())
    assert result["by_bug"] == {}  # no saved labels or review outcomes were counted
    assert result["index_excluded"][0]["reason"] == "saved under an older evidence rule"
    assert path.read_bytes() == before


@pytest.mark.parametrize("missing_final", [False, True])
def test_old_rule_reenumerates_unchanged_source_instead_of_saved_flags(batch_factory, capsys, missing_final):
    _, path, saved, live, _ = batch_factory(missing_final=missing_final)
    saved["evidence_rule_version"] = 0
    saved["plan"]["index_excluded"] = []
    saved["plan"]["excluded"] = [{"bug": TASK.id, "reason": "stale saved exclusion"}]
    # Poison both saved copies of the labels; current source labels must win.
    for row in saved["plan"]["cases"] + saved["records"]:
        row["label"] = "wrong" if row["label"] == "right" else "right"
    save(path, saved)
    assert rc.main(["--score", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == live


@pytest.mark.parametrize("change", ["check", "index", "prompt", "round folder", "missing source", "manifest"])
def test_old_rule_requires_unchanged_source_manifest(batch_factory, capsys, change):
    source, path, saved, _, checks = batch_factory()
    saved["evidence_rule_version"] = 0
    if change == "check":
        record = json.loads(checks[0].read_text(encoding="utf-8"))
        record["checks"][0]["patch"] = "base"
        save(checks[0], record)
    elif change == "index":
        with (source / "runs.jsonl").open("ab") as stream:
            stream.write(b'{"event":')
    elif change == "prompt":
        next(source.glob("run/*/*/prompt.txt")).unlink()
    elif change == "round folder":
        (source / "round2").mkdir()
    elif change == "missing source":
        source.rename(source.with_name("moved-source"))
    else:
        saved["plan"]["source_snapshot"]["files"].pop("runs.jsonl")
    save(path, saved)
    assert rc.main(["--score", str(path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["predictions"]["P3"]["prediction_met"] is None
    assert result["predictions"]["P2"]["status"] == "disabled"
    assert any(row["reason"] == "saved under an older evidence rule" for row in result["index_excluded"])


def test_new_rule_version_refreshes_existing_source(batch_factory, monkeypatch, capsys):
    _, path, saved, live, _ = batch_factory()
    monkeypatch.setattr(rc, "EVIDENCE_RULE_VERSION", saved["evidence_rule_version"] + 1)
    assert rc.main(["--score", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == live


def test_refreshed_records_must_match_current_review_arguments(batch_factory, capsys):
    _, path, saved, _, _ = batch_factory()
    saved["evidence_rule_version"] = 0
    saved["records"][0]["arguments"]["task"] = "different synthetic issue"
    save(path, saved)
    assert rc.main(["--score", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["predictions"]["P3"]["prediction_met"] is None
    assert any(row["reason"] == "saved review no longer matches current evidence"
               for row in result["index_excluded"])


def test_source_refresh_requires_the_same_task_selection(batch_factory, monkeypatch, capsys):
    _, path, saved, _, _ = batch_factory()
    saved["evidence_rule_version"] = 0
    save(path, saved)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK, SimpleNamespace(id="other-1")])
    assert rc.main(["--score", str(path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["index_excluded"][0]["reason"] == "saved under an older evidence rule"


@pytest.mark.parametrize("field", ["root", "tasks", "files", "inventory"])
def test_malformed_source_manifest_is_an_incomplete_batch(batch_factory, capsys, field):
    _, path, saved, _, _ = batch_factory()
    saved["evidence_rule_version"] = 0
    saved["plan"]["source_snapshot"][field] = None
    save(path, saved)
    assert rc.main(["--score", str(path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["predictions"]["P3"]["status"] == "incomplete"
    assert result["index_excluded"][0]["reason"] == "saved under an older evidence rule"


def test_old_rule_keeps_review_pins_mandatory(batch_factory, capsys):
    _, path, saved, _, _ = batch_factory()
    saved["evidence_rule_version"] = 0
    saved["records"][0]["image_id"] = "sha256:" + "d" * 64
    save(path, saved)
    assert rc.main(["--score", str(path)]) == 1
    captured = capsys.readouterr()
    assert "replay record image ID differs from plan" in captured.err and not captured.out
