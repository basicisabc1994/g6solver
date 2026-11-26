"""Review 3: the index allowlist against the real writers of the three indexes.

Synthetic folders and the real ``wecommon.Archive`` writer only: no container,
no model, no real archive, no confined run.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from worked_examples import replay_rc as rc  # noqa: E402

TASK = SimpleNamespace(id="black-5634", repo="/synthetic/black", parent="c" * 40)


def labelled_runs(archive):
    """Two labelled runs written by the harness's own writer, as run.py's run and check do."""
    for label in (True, False):
        run = archive.start("run", TASK.id, arm="B")
        run.write("final.patch", b"right" if label else b"wrong")
        run.write_json("rounds.json", [])
        run.write("prompt.txt", "Issue #496: synthetic issue")
        run.finish({}, "finished")
        check = archive.start("check", TASK.id, checked_run=run.rel)
        check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": label}]}, "finished")


def test_round3_distillation_rows_do_not_refuse_the_replay(tmp_path):
    # loop.py:166 and evolve.py:208 write c.Archive(<archive>/round3).start("distill", <generation>, parent=<arm>)
    # into round3's own runs.jsonl; harness/G1.txt is the committed product of one such row.
    round3 = rc.c.Archive(tmp_path / "round3")
    labelled_runs(round3)
    folder = round3.start("distill", "G1", parent="B")
    folder.write("text.txt", "synthetic generation text")
    folder.finish({"cost_usd": 0.0, "named": [], "parent": "B"}, "finished")

    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert excluded == []
    assert {case.label for case in cases} == {"right", "wrong"}


def test_check_of_a_dry_run_folder_does_not_refuse_the_replay(tmp_path):
    # run.py:2207 check(ctx, task, run_rel) records whatever folder --run names as checked_run;
    # a dry run (run.py:1920, kind "dryrun") writes a final.patch like any run.
    archive = rc.c.Archive(tmp_path)
    labelled_runs(archive)
    dry = archive.start("dryrun", TASK.id)
    dry.write("final.patch", b"")
    dry.finish({}, "finished")
    check = archive.start("check", TASK.id, checked_run=dry.rel)
    check.finish({"checks": [{"patch": "final", "patch_applied": True, "passes": False}]}, "finished")

    cases, excluded = rc.enumerate_cases(tmp_path, [TASK])
    assert excluded == []
    assert {case.label for case in cases} == {"right", "wrong"}
