"""Review 3 reference (these PASS): the regressions' inputs against the base's module.

The base's replay_rc.py is read with ``git show f34d9a97:...`` into pytest's temporary folder and
imported beside the tree's own run.py and wecommon.py (unchanged by the diff). Nothing is checked out.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TREE / "validation"))
import worked_examples  # noqa: E402,F401

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)
ROW = {"event": "started", "kind": "run", "task": "black-5634", "run": "run/black-5634/001"}


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    source = subprocess.run(["git", "-C", str(TREE), "show", "f34d9a97:validation/worked_examples/replay_rc.py"],
                            capture_output=True, check=True).stdout
    path = tmp_path_factory.mktemp("base") / "replay_rc_base.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("worked_examples.replay_rc_base", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def labelled_runs(archive):
    for label in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", b"right" if label else b"wrong")
        run.write_json("rounds.json", [])
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.finish({}, "finished")
        check = archive.start("check", TASK.id, checked_run=run.rel)
        check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": label}]}, "finished")


def test_base_reads_an_archive_with_round3_distillation_rows(base, tmp_path):
    round3 = base.c.Archive(tmp_path / "round3")
    labelled_runs(round3)
    folder = round3.start("distill", "G1", parent="B")
    folder.write("text.txt", "synthetic generation text")
    folder.finish({"cost_usd": 0.0, "named": [], "parent": "B"}, "finished")
    cases, excluded = base.enumerate_cases(tmp_path, [TASK])
    assert excluded == [] and {case.label for case in cases} == {"right", "wrong"}


def test_base_reads_an_archive_with_a_checked_dry_run(base, tmp_path):
    archive = base.c.Archive(tmp_path)
    labelled_runs(archive)
    dry = archive.start("dryrun", TASK.id)
    dry.write("final.patch", b"")
    dry.finish({}, "finished")
    check = archive.start("check", TASK.id, checked_run=dry.rel)
    check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": False}]}, "finished")
    cases, excluded = base.enumerate_cases(tmp_path, [TASK])
    assert excluded == [] and {case.label for case in cases} == {"right", "wrong"}


def test_base_index_read_survives_an_append_in_progress(base, tmp_path):
    index = tmp_path / "runs.jsonl"
    index.write_text(json.dumps(ROW) + "\n", encoding="utf-8")
    with open(index, "a", encoding="utf-8", newline="\n"):
        assert base.Source(tmp_path).rows() == [ROW]


def test_base_index_read_does_not_refuse_the_harness_writer(base, tmp_path):
    archive = base.c.Archive(tmp_path)
    archive.append(ROW)
    with base.Source(tmp_path).path("runs.jsonl").open("rb"):      # how the base's read_bytes holds the file
        archive.append({"event": "ended", "run": ROW["run"], "status": "finished"})
    assert len(archive.rows()) == 2


def test_base_reads_a_source_given_relative_to_a_parent_folder(base, tmp_path, monkeypatch):
    (tmp_path / "source").mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    assert base.enumerate_cases(Path("..") / "source", [TASK]) == ([], [])
    assert isinstance(base.output_archive(tmp_path / "source", Path("..") / "output"), base.c.Archive)
