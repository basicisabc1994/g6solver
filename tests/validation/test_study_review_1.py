# SPDX-License-Identifier: Apache-2.0
"""Study review 1's 22 findings on the trust fixes, each a test that failed on 67ea51d for the reason the review gave.

Class 1, attribution from a row's claims (findings 1, 2, 3, 4, 6, 7, 12, 13, 18, 22): the saved rows have one reader and one writer
(``wecommon.shelf``, ``wecommon.keep``). Class 2, state not durable (16, 17). Class 3, check then act (11, 20, 22). Class 4, the
oracle kept from candidate code (8, 9, 10, 15, 21), is in test_study_review_1_oracle.py. Class 5, boundaries (5, 19). Class 6, identity from rendering (14). Fakes only:
no Docker, no model, no network; git runs read-only, on files the test made."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402
import evolve  # noqa: E402
import export_reviewer_cases as ex  # noqa: E402
import final_review  # noqa: E402
import layers  # noqa: E402
import loop  # noqa: E402
import probe_replay  # noqa: E402
import replay_compare  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402


def finished(root, arm="K2", task="bug", repeat=1):
    """A finished run whose start names its arm, bug and repeat (None: a start that names none), and the row ``loop.one`` keeps."""
    archive = c.Archive(root)
    run = archive.start("run", task, arm=arm, **({"repeat": repeat} if repeat is not None else {}))
    run.write("final.patch", b"candidate patch\n")
    run.finish({"agent_started": True}, "finished")
    row = {"run": run.rel, "arm": arm, "task": task, "repeat": repeat or 1, "status": "finished", "passes": True, "failing": 0,
           c.MADE_UNDER: c.ending_sha256(archive, run.rel)}
    return archive, run, row


def save(root, row, name=None, amended=False):
    path = Path(root) / "loop" / ("amended" if amended else "")
    path.mkdir(parents=True, exist_ok=True)
    path /= name or c.saved_name(row["arm"], row["task"], row["repeat"])
    path.write_text(json.dumps(row), encoding="utf-8")
    return path


def tool_giving(row, *, passes=True, also=None):
    """``loop.tool``: the runner prints ``row`` for a run, and the check a final patch's verdict; ``also`` runs during the check."""
    def tool(command, *args, **kw):
        if command == "run":
            return json.dumps(row)
        if also:
            also()
        return json.dumps({"checks": [{"patch": "final", "passes": passes, "failing": [] if passes else ["t"]}]})
    return tool


@pytest.fixture
def no_amendment(monkeypatch):
    monkeypatch.setattr(we, "amendment", lambda task: None)


# --- class 1: attribution from a run's facts alone, through one reader -------------------------------------------------------
def test_finding_1_probe_replay_takes_each_runs_bug_arm_and_repeat_from_its_facts(tmp_path, no_amendment):
    archive, run, row = finished(tmp_path, arm="K2", task="actual", repeat=1)
    save(tmp_path, {**row, "arm": "A", "task": "requested", "repeat": 2, "passes": False})
    assert probe_replay.runs_of(tmp_path, "requested") == []
    assert probe_replay.runs_of(tmp_path, "actual") == []        # a refused row is no run's: it gives its own run nothing either


def test_finding_2_loop_one_reuses_the_row_the_one_reader_finds_whatever_its_files_name(tmp_path, monkeypatch, no_amendment):
    archive, run, row = finished(tmp_path)
    save(tmp_path, row, "moved-row.json")
    assert compare.saved(tmp_path)[("K2", "bug", 1)]["maintainers"] is True
    calls = []
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: calls.append(command) or tool_giving(row)(command))
    assert loop.one(tmp_path, "g6", "K2", "bug", 1)["run"] == run.rel
    assert calls == [], "a held row the one reader finds is never run or checked again"


def test_finding_3_loop_one_refuses_what_the_runner_printed_when_it_says_another_arm_of_the_run(tmp_path, monkeypatch,
                                                                                                no_amendment):
    archive, run, row = finished(tmp_path)
    monkeypatch.setattr(loop, "tool", tool_giving({**row, "arm": "A"}))
    with pytest.raises(SystemExit, match="refused|arm"):
        loop.one(tmp_path, "g6", "K2", "bug", 1)
    assert not (tmp_path / "loop" / "K2-bug-1.json").exists()


def test_finding_4_the_export_labels_a_final_patch_with_the_verdict_compare_gives(tmp_path, no_amendment):
    archive, run, row = finished(tmp_path)
    save(tmp_path, row)
    archive.start("check", "bug", checked_run=run.rel).finish(
        {"checks": [{"patch": "round-1", "passes": False}, {"patch": "final", "passes": False}]}, "finished")   # a newer word
    saved_verdict = compare.saved(tmp_path)[("K2", "bug", 1)]["maintainers"]
    exported = next(ok for task, patch, ok in ex.labelled(tmp_path, "bug") if patch == run.path / "final.patch")
    assert exported is saved_verdict is True


def test_finding_6_no_repeat_is_ever_taken_from_a_row(tmp_path, no_amendment):
    archive, run, row = finished(tmp_path, repeat=None)
    save(tmp_path, {**row, "repeat": 1})
    save(tmp_path, {**row, "repeat": 2})
    saved = compare.saved(tmp_path)
    assert sum(key[:2] == ("K2", "bug") for key in saved) <= 1, "one run cannot supply two independent repeats"
    assert not saved, "a run whose start names no repeat gives no row a repeat"


def test_finding_7_the_final_reviews_scheduler_refuses_a_row_that_says_another_bug(tmp_path, no_amendment):
    archive, run, row = finished(tmp_path, task="actual")
    save(tmp_path, {**row, "task": "requested"})
    assert final_review.waiting(tmp_path, ["requested"]) == []
    assert final_review.waiting(tmp_path, ["actual"]) == []


def test_finding_12_rescore_never_writes_over_another_runs_amended_row(tmp_path, monkeypatch):
    archive, run, row = finished(tmp_path)
    save(tmp_path, row)
    _, other, other_row = finished(tmp_path, arm="A")
    diff = tmp_path / "amend.patch"
    diff.write_bytes(b"amendment")
    path = save(tmp_path, {**other_row, "amended_passes": True, "amendment_sha256": c.sha256_file(diff)},
                c.saved_name("K2", "bug", 1), amended=True)
    before = path.read_bytes()
    monkeypatch.setattr(we, "amendment", lambda task: diff)
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("a refused amended verdict is never checked"))
    try:
        loop.rescore(tmp_path, "g6")
    except SystemExit as exc:
        assert "refused" in str(exc)
    assert path.read_bytes() == before


def test_finding_13_an_error_ending_is_a_failure_whatever_a_saved_row_says(tmp_path, monkeypatch, no_amendment):
    archive, run, row = finished(tmp_path)
    run.failed("capture failed", agent_started=True)
    row.update(status="finished", passes=True, failing=0, ending_sha256=c.ending_sha256(archive, run.rel))
    save(tmp_path, row)
    assert c.ending(archive, run.rel)["status"] == "error"
    assert c.verdict(archive, run.rel, row) is False
    assert compare.saved(tmp_path)[("K2", "bug", 1)]["maintainers"] is False
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("a row that stands is given as it is judged"))
    assert loop.one(tmp_path, "g6", "K2", "bug", 1)["passes"] is False


def test_finding_18_a_row_is_found_whatever_its_files_name(tmp_path, no_amendment):
    archive, run, row = finished(tmp_path)
    save(tmp_path, row, "moved.json")
    assert c.verdict(archive, run.rel, row) is True
    assert compare.saved(tmp_path).get(("K2", "bug", 1), {}).get("maintainers") is True


@pytest.mark.parametrize("consumer", ["layers", "replay_compare", "evolve"])
def test_finding_22_a_score_and_its_run_come_from_one_read(tmp_path, monkeypatch, no_amendment, consumer):
    archive, first, first_row = finished(tmp_path)
    _, second, second_row = finished(tmp_path)
    (second.path / "final.patch").write_bytes(b"another candidate patch\n")
    second_row.update(passes=False, failing=1)
    save(tmp_path, first_row)
    read_scores = compare.saved

    def replace_after_scores(root):
        scores = read_scores(root)
        save(root, second_row)          # another writer publishes a different run for the same arm, bug and repeat
        return scores

    monkeypatch.setattr(compare, "saved", replace_after_scores)
    if consumer == "layers":
        got = layers.runs_of(tmp_path, ["bug"], "maintainers")["bug"]
        identity, actual = ("first" if got[0][0] == c.sha256_file(first.path / "final.patch") else "second"), got[0][2]
    elif consumer == "replay_compare":
        got = replay_compare.patches(tmp_path, ["bug"])
        sha = next(iter(got))
        identity, actual = ("first" if sha == c.sha256_file(first.path / "final.patch") else "second"), got[sha]["maintainers"]
    else:
        got = evolve.verdicts(tmp_path, "K2", ["bug"], [1])
        identity, actual = ("first" if got[0]["run"] == first.rel else "second"), got[0]["pass"]
    assert (identity, actual) in {("first", True), ("second", False)}, "the new run inherited the old run's pass"


# --- class 2: state kept whole, and a restart that recovers ------------------------------------------------------------------
def test_finding_16_loop_one_recovers_a_run_whose_row_an_older_writer_left_torn(tmp_path, monkeypatch, no_amendment):
    archive, run, row = finished(tmp_path)
    torn = tmp_path / "loop" / "K2-bug-1.json"
    torn.parent.mkdir()
    torn.write_bytes(b'{"run":')                      # a write cut short by a stop, as the writer before this one could leave it
    monkeypatch.setattr(loop, "tool", tool_giving(row))
    try:
        resumed = loop.one(tmp_path, "g6", "K2", "bug", 1)
    except json.JSONDecodeError:
        pytest.fail("restart is blocked by the torn saved row")
    assert resumed["run"] == run.rel and resumed["passes"] is True
    assert json.loads(torn.read_text("utf-8"))["run"] == run.rel


def test_finding_16_a_stop_while_loop_one_writes_its_row_leaves_none_torn_and_a_restart_recovers(tmp_path, monkeypatch,
                                                                                                 no_amendment):
    archive, run, row = finished(tmp_path)
    monkeypatch.setattr(loop, "tool", tool_giving(row))
    real = os.replace

    def stopped(src, dst):
        raise OSError("simulated process stop during the write")

    monkeypatch.setattr(c.os, "replace", stopped)
    with pytest.raises(OSError, match="simulated"):
        loop.one(tmp_path, "g6", "K2", "bug", 1)
    assert sorted(p.name for p in (tmp_path / "loop").glob("*.json")) == []     # nothing published, and nothing torn left
    monkeypatch.setattr(c.os, "replace", real)
    resumed = loop.one(tmp_path, "g6", "K2", "bug", 1)
    assert resumed["run"] == run.rel and resumed["passes"] is True


def _amended(tmp_path, monkeypatch, passes=True, also=None):
    archive, run, row = finished(tmp_path)
    save(tmp_path, row)
    diff = tmp_path / "amend.patch"
    diff.write_bytes(b"amendment")
    monkeypatch.setattr(we, "amendment", lambda task: diff)

    def tool(*a, **kw):
        if also:
            also(run)
        return json.dumps({"checks": [{"patch": "final", "passes": passes}]})

    monkeypatch.setattr(loop, "tool", tool)
    return archive, run, row, diff


def test_finding_17_rescore_recovers_a_run_whose_amended_row_an_older_writer_left_torn(tmp_path, monkeypatch):
    archive, run, row, diff = _amended(tmp_path, monkeypatch)
    torn = tmp_path / "loop" / "amended" / "K2-bug-1.json"
    torn.parent.mkdir()
    torn.write_bytes(b'{"run":')
    try:
        resumed = loop.rescore(tmp_path, "g6")
    except json.JSONDecodeError:
        pytest.fail("restart is blocked by the torn amended row")
    assert resumed[0]["run"] == run.rel and resumed[0]["amended_passes"] is True
    assert json.loads(torn.read_text("utf-8"))["amended_passes"] is True


def test_finding_17_a_stop_while_rescore_writes_leaves_none_torn_and_a_restart_recovers(tmp_path, monkeypatch):
    archive, run, row, diff = _amended(tmp_path, monkeypatch)
    real = os.replace
    monkeypatch.setattr(c.os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("simulated process stop")))
    with pytest.raises(OSError, match="simulated"):
        loop.rescore(tmp_path, "g6")
    assert sorted(p.name for p in (tmp_path / "loop" / "amended").glob("*.json")) == []
    monkeypatch.setattr(c.os, "replace", real)
    resumed = loop.rescore(tmp_path, "g6")
    assert resumed[0]["run"] == run.rel and resumed[0]["amended_passes"] is True


# --- class 3: the ending read again after the check, before the result is kept -----------------------------------------------
def test_finding_11_loop_one_never_keeps_a_pass_after_the_ending_changes_during_its_check(tmp_path, monkeypatch, no_amendment):
    archive, run, row = finished(tmp_path)
    monkeypatch.setattr(loop, "tool", tool_giving(row, also=lambda: run.failed("failure recorded during the check",
                                                                              agent_started=True)))
    try:
        result = loop.one(tmp_path, "g6", "K2", "bug", 1)
    except SystemExit:
        assert c.ending(archive, run.rel)["status"] == "error" and not (tmp_path / "loop" / "K2-bug-1.json").exists()
        return
    assert c.ending(archive, run.rel)["status"] == "error"
    assert result["passes"] is c.verdict(archive, run.rel, result)


def test_finding_20_rescore_never_keeps_a_pass_after_the_ending_changes_during_its_check(tmp_path, monkeypatch):
    archive, run, row, diff = _amended(tmp_path, monkeypatch, also=lambda run: run.failed("failure during the amended check",
                                                                                         agent_started=True))
    try:
        result = loop.rescore(tmp_path, "g6")[0]
    except SystemExit:
        assert c.ending(archive, run.rel)["status"] == "error"
        assert not (tmp_path / "loop" / "amended" / "K2-bug-1.json").exists()
        return
    assert c.ending(archive, run.rel)["status"] == "error"
    assert result["amended_passes"] is c.verdict(archive, run.rel, result, amendment=c.sha256_file(diff))


# --- class 5: boundaries of the within-run comparison -------------------------------------------------------------------------
def test_finding_5_a_bug_with_a_repeat_missing_keeps_the_pairs_it_has():
    got = compare.within({("K2", "bug", 1): {"first": False, "maintainers": True}}, "K2", ["bug"], [1, 2])
    assert got["better"] == got["worse"] == got["same"] == 0          # its bug still counts neither way
    assert (got["pairs"], got["missing"], got["converted"]) == (1, 1, 1)


def test_finding_19_an_unknown_first_patch_verdict_is_missing_never_a_failed_baseline(tmp_path, no_amendment):
    for bug in "abcde":
        archive, run, row = finished(tmp_path, task=bug)
        save(tmp_path, row)
        archive.start("check", bug, checked_run=run.rel).finish(
            {"checks": [{"patch": "round-1", "passes": None}, {"patch": "final", "passes": True}]}, "finished")
    got = compare.within(compare.saved(tmp_path), "K2", "abcde", [1])
    assert not compare.within_judged(got), "an unread first verdict is missing, not a failed baseline"
    assert (got["pairs"], got["missing"]) == (0, 5)


def test_no_repeat_asked_is_no_pair_and_no_bug_counted_either_way():
    rows = {("K2", "bug", 1): {"first": False, "maintainers": True}, ("A", "bug", 1): {"maintainers": False}}
    got = compare.within(rows, "K2", ["bug"], [])
    assert (got["better"], got["worse"], got["same"], got["pairs"], got["missing"]) == (0, 0, 0, 0, 0)
    assert compare.by_bug(rows, "K2", "A", ["bug"], [], "maintainers") == {"better": 0, "worse": 0, "same": 0}


def test_a_checks_word_that_is_not_true_or_false_is_no_verdict_and_nothing_is_kept(tmp_path, monkeypatch, no_amendment):
    archive, run, row = finished(tmp_path)
    monkeypatch.setattr(loop, "tool", tool_giving(row, passes=None))
    with pytest.raises(SystemExit, match="no verdict"):
        loop.one(tmp_path, "g6", "K2", "bug", 1)
    assert not (tmp_path / "loop" / "K2-bug-1.json").exists()


# --- class 6: identity from the bytes --------------------------------------------------------------------------------------------
def test_finding_14_the_export_keeps_two_names_git_keeps_apart_apart_through_its_diff_text():
    def patch(byte):
        name = bytes([byte]) + b".py"
        return b"diff --git a/" + name + b" b/" + name + b"\n--- a/" + name + b"\n+++ b/" + name + b"\n@@ -1 +1 @@\n-old\n+new\n"

    first, second = patch(254), patch(255)
    assert ex.touched(first.decode("utf-8", "surrogateescape")) == ["\udcfe.py"]       # git itself names two paths
    assert ex.touched(second.decode("utf-8", "surrogateescape")) == ["\udcff.py"]
    assert set(ex.changed(ex.diff_of(first))) == {"\udcfe.py"}                      # as main() reads them, through diff_of
    assert set(ex.changed(ex.diff_of(second))) == {"\udcff.py"}
