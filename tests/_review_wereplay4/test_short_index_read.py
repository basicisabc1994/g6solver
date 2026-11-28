"""Review 4: an index read short is replayed and scored as if it were whole.

Repair 3 lets ``Source.rows`` drop a final line that does not parse, so that a harness append in
progress does not refuse the replay. Nothing records the drop. A truncated index (an interrupted
copy or sync of the archive) is indistinguishable from an append in progress, and its dropped row
can be the ``started`` row of a run whose patch, check and label are all on disk.

Synthetic folders, the real ``wecommon.Archive`` writer and a fake container client only: no Docker,
no model, no real archive, no confined run.
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
from worked_examples import replay_rc as rc  # noqa: E402

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


def truncated_archive(root: Path) -> str:
    """Three labelled runs (right, wrong, wrong) by the harness's writer; the index is then cut
    inside the third run's ``started`` row. Returns the third run's folder, intact on disk."""
    archive = rc.c.Archive(root)
    last = ""
    for patch, passes in ((b"right patch", True), (b"wrong patch one", False), (b"wrong patch two", False)):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", patch)
        run.write_json("rounds.json", [])
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.finish({}, "finished")
        check = archive.start("check", TASK.id, checked_run=run.rel)
        check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
        last = run.rel
    lines = archive.index.read_bytes().split(b"\n")
    assert len(lines) == 13 and json.loads(lines[8])["run"] == last
    archive.index.write_bytes(b"\n".join(lines[:8]) + b"\n" + lines[8][: len(lines[8]) // 2])
    assert (root / last / "final.patch").read_bytes() == b"wrong patch two"
    return last


class Docker:
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            value = {"ok": True, "assessment": {"outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, name, script, *args, **kwargs):
        value = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b"")


def test_dry_plan_from_a_truncated_index_names_what_it_did_not_read(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source"
    lost = truncated_archive(source)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *args: (f"g6we-task:{TASK.id}-fake", "unused"))
    code = rc.main(["--source", str(source), "--registered-commit", COMMIT, "--dry"])
    printed = capsys.readouterr().out
    if code == 1:
        return                                               # a refusal is an honest answer
    plan = json.loads(printed)
    named = json.dumps([plan["excluded"], plan["index_excluded"]])
    assert "runs.jsonl" in named or lost in named, (
        f"exit {code}; {len(plan['cases'])} of 3 archived patches planned; "
        f"excluded={plan['excluded']} index_excluded={plan['index_excluded']}")


def test_live_replay_of_a_truncated_index_does_not_report_a_prediction_met(tmp_path, monkeypatch, capsys):
    source, output = tmp_path / "source", tmp_path / "output"
    truncated_archive(source)
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *args: (f"g6we-task:{TASK.id}-fake", "unused"))
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    code = rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"], docker=Docker())
    printed = capsys.readouterr().out
    if code == 1:
        return
    p3 = json.loads(printed)["predictions"]["P3"]
    group = json.loads(printed)["by_bug"][TASK.id]["hook"]
    assert p3["status"] != "met", (
        f"exit {code}; P3 {p3['status']} (prediction_met={p3['prediction_met']}, missing={p3['missing']}) "
        f"from {group['reviewed']} of 3 archived labelled patches; excluded={group['excluded']}")


def test_reference_the_diff_base_refused_the_same_truncated_index(tmp_path):
    """Passes: at ff18554b the same index is a refusal, so the silent short read is new in this diff."""
    code = subprocess.run(["git", "-C", str(TREE), "show", "ff18554b:validation/worked_examples/replay_rc.py"],
                          capture_output=True, check=True).stdout
    path = tmp_path / "replay_rc_base.py"
    path.write_bytes(code)
    spec = importlib.util.spec_from_file_location("worked_examples.replay_rc_base4", path)
    base = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = base
    spec.loader.exec_module(base)
    truncated_archive(tmp_path / "source")
    with pytest.raises(ValueError):
        base.enumerate_cases(tmp_path / "source", [TASK])
