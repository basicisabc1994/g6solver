# SPDX-License-Identifier: Apache-2.0
"""The two replays' counts (PLAN.md, 2026-11-03, afternoon), on a small made-up archive: counts only, never a text."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402
import final_review as fr  # noqa: E402
import product_review as pr  # noqa: E402
import replay3 as r3  # noqa: E402
import replay_compare as rc  # noqa: E402
import run as we  # noqa: E402
from g6.core.binomial import lower_bound  # noqa: E402

SECRET = "SECRET"


class _Archive:
    """Runs with saved patches, each run's two verdicts, and each reviewer's saved word on each patch."""

    def __init__(self, root: Path, monkeypatch):
        self.root, self.n = root, 0
        self.diff = root / "amendment.patch"
        root.mkdir(parents=True, exist_ok=True)
        self.diff.write_text("an amendment", "utf-8")
        monkeypatch.setattr(compare.we, "amendment", lambda task: self.diff)
        (root / "loop" / "amended").mkdir(parents=True)

    def run(self, task: str, body: str, maintainers: bool, amended: bool) -> str:
        """One finished run of ``task`` whose patch is ``body``; its digest."""
        self.n += 1
        arm, rel = f"A{self.n}", f"run/A{self.n}"
        (self.root / rel).mkdir(parents=True)
        (self.root / rel / "final.patch").write_text(f"{SECRET} patch {body}", "utf-8")
        row = {"arm": arm, "task": task, "repeat": 1, "run": rel, "status": "finished", "passes": maintainers, "failing": 0}
        (self.root / "loop" / f"{arm}-{task}-1.json").write_text(json.dumps(row), "utf-8")
        (self.root / "loop" / "amended" / f"{arm}-{task}-1.json").write_text(json.dumps(
            {"amendment_sha256": hashlib.sha256(b"an amendment").hexdigest(), "run": rel, "amended_passes": amended}), "utf-8")
        return hashlib.sha256(f"{SECRET} patch {body}".encode()).hexdigest()

    def said(self, folder: str, sha: str, said: str, verdict: str | None = None) -> None:
        verdict = verdict or {"reproduced": "defect", "opinion": "defect", "none": "none", "defect": "defect"}.get(said, "failed")
        (self.root / "loop" / folder).mkdir(exist_ok=True)
        (self.root / "loop" / folder / f"{sha}.json").write_text(json.dumps(
            {"task": "x", "said": said, "verdict": verdict, "rule": f"{SECRET} rule", "why": f"{SECRET} why", "test": f"{SECRET} test",
             "error": f"{SECRET} error", "reply_end": f"{SECRET} reply", "reproduce": {"note": f"{SECRET} note"}}), "utf-8")


def _report(root, capsys, tasks, *, futile=False, block=rc.BLOCK):
    rc.report(root, [("stage 1 and 2" if futile else "some bugs", tasks, futile)], block=block)
    return capsys.readouterr().out


def _six(tmp_path, monkeypatch):
    """Six patches on two bugs. Truth on the amended tests: three right, three wrong; the last wrong one is right by the
    maintainers'. The final review is right on two, the reproducing review on five."""
    archive = _Archive(tmp_path / "round3", monkeypatch)
    cells = [("x-1", True, True, "reproduced", "none"), ("x-1", True, True, "none", "none"), ("x-1", True, True, "opinion", "none"),
             ("y-1", False, False, "none", "reproduced"), ("y-1", False, False, "reproduced", "reproduced"),
             ("y-1", True, False, "none", "opinion")]
    for n, (task, maintainers, amended, frozen, reproduce) in enumerate(cells):
        sha = archive.run(task, str(n), maintainers, amended)
        archive.said(fr.FOLDER, sha, frozen)
        archive.said(fr.REPRODUCE, sha, reproduce)
    return archive


def _line(out, *parts):
    found = [line for line in out.splitlines() if all(part in line for part in parts)]
    assert len(found) == 1, (parts, out)
    return found[0]


def test_agreement_false_alarms_and_misses_each_with_an_exact_one_sided_bound(tmp_path, monkeypatch, capsys):
    out = _report(_six(tmp_path, monkeypatch).root, capsys, ["x-1", "y-1"])
    frozen = _line(out, "amended", "final review", "agree")
    assert f"agree 2/6 (33.3%, 95% lower {lower_bound(2, 6, 0.05):.1%})" in frozen
    assert f"false alarms 2/3 (66.7%, 95% upper {0.95 ** (1 / 3):.1%})" in frozen
    assert f"misses 2/3 (66.7%, 95% upper {0.95 ** (1 / 3):.1%})" in frozen
    reproduce = _line(out, "amended", "reproduce first", "agree")
    assert f"agree 5/6 (83.3%, 95% lower {lower_bound(5, 6, 0.05):.1%})" in reproduce
    assert f"false alarms 0/3 (0.0%, 95% upper {1 - 0.05 ** (1 / 3):.1%})" in reproduce
    assert "misses 1/3 (33.3%" in reproduce
    assert "agree 0/0 (n/a)" in _line(out, "amended", "product", "agree")


def test_the_maintainers_verdict_is_reported_beside_the_truth(tmp_path, monkeypatch, capsys):
    out = _report(_six(tmp_path, monkeypatch).root, capsys, ["x-1", "y-1"])
    assert "agree 3/6" in _line(out, "maintainers", "final review", "agree")
    assert "false alarms 1/4" in _line(out, "maintainers", "reproduce first", "agree")


def test_the_paired_sign_test_counts_the_patches_where_exactly_one_is_right(tmp_path, monkeypatch, capsys):
    out = _report(_six(tmp_path, monkeypatch).root, capsys, ["x-1", "y-1"])
    paired = _line(out, "amended", "reproduce first against final review")
    assert "6 paired, final review right 2, reproduce first right 5" in paired
    assert f"only reproduce first right 3, only final review right 0, one-sided p {compare.sign_test(3, 0):.4f}" in paired
    assert "p 0.1250" in paired and "bugs better 2 worse 0 same 0" in paired


def test_replay_3_is_counted_and_paired_as_replay_1_is(tmp_path, monkeypatch, capsys):
    archive = _six(tmp_path, monkeypatch)
    shas = sorted(p.stem for p in (archive.root / "loop" / fr.FOLDER).glob("*.json"))
    by_run = {json.loads(p.read_text("utf-8"))["run"]: p for p in (archive.root / "loop").glob("*-*.json")}
    order = [hashlib.sha256((archive.root / run / "final.patch").read_bytes()).hexdigest() for run in sorted(by_run)]
    for sha, said in zip(order, ("none", "none", "reproduced", "reproduced", "reproduced", "none")):
        archive.said(r3.FOLDER, sha, said)
    assert sorted(order) == shas
    out = _report(archive.root, capsys, ["x-1", "y-1"])
    assert "agree 4/6" in _line(out, "amended", "rule test", "agree")
    paired = _line(out, "amended", "rule test against final review")
    assert "6 paired, final review right 2, rule test right 4; only rule test right 2, only final review right 0" in paired
    assert f"one-sided p {compare.sign_test(2, 0):.4f}" in paired and "bugs better 2 worse 0 same 0" in paired
    assert "futility rule test" not in out


def test_a_review_that_failed_is_no_review_and_is_counted_nowhere(tmp_path, monkeypatch, capsys):
    archive = _six(tmp_path, monkeypatch)
    sha = sorted((archive.root / "loop" / fr.FOLDER).glob("*.json"))[0].stem
    archive.said(fr.FOLDER, sha, "failed")
    out = _report(archive.root, capsys, ["x-1", "y-1"])
    assert "agree " in _line(out, "amended", "final review", "agree") and "/5 (" in _line(out, "amended", "final review", "agree")
    assert "5 paired" in _line(out, "amended", "reproduce first against final review")
    assert "final review 5 (1)" in _line(out, "distinct patches")


def test_a_patch_whose_runs_disagree_has_no_truth(tmp_path, monkeypatch, capsys):
    archive = _Archive(tmp_path / "round3", monkeypatch)
    sha = archive.run("x-1", "same", True, True)
    assert archive.run("x-1", "same", False, True) == sha
    archive.said(fr.FOLDER, sha, "none")
    out = _report(archive.root, capsys, ["x-1"])
    assert "1 distinct patches; truth on the amended tests 1, on the maintainers' 0; split amended 0, maintainers 1" in out
    assert "agree 1/1" in _line(out, "amended", "final review", "agree")
    assert "agree 0/0" in _line(out, "maintainers", "final review", "agree")


def test_the_products_verdict_is_matched_to_the_final_reviews_and_too_long_is_apart(tmp_path, monkeypatch, capsys):
    archive = _six(tmp_path, monkeypatch)
    shas = sorted(p.stem for p in (archive.root / "loop" / fr.FOLDER).glob("*.json"))
    frozen = {sha: json.loads((archive.root / "loop" / fr.FOLDER / f"{sha}.json").read_text("utf-8"))["verdict"] for sha in shas}
    archive.said(pr.FOLDER, shas[0], frozen[shas[0]])
    archive.said(pr.FOLDER, shas[1], "none" if frozen[shas[1]] == "defect" else "defect")
    archive.said(pr.FOLDER, shas[2], "too long", verdict="failed")
    archive.said(pr.FOLDER, shas[3], "failed")
    out = _report(archive.root, capsys, ["x-1", "y-1"])
    assert "verdicts match on 1 of 3 (33.3%), too long 1" in _line(out, "product against final review")
    assert "agree " in _line(out, "amended", "product", "agree") and "/2 (" in _line(out, "amended", "product", "agree")
    assert "too long 1" in _line(out, "amended", "product", "agree")


def _four(tmp_path, monkeypatch):
    """Four right patches in digest order: the final review clears the first two and flags the last two."""
    archive = _Archive(tmp_path / "round3", monkeypatch)
    shas = sorted(archive.run("x-1", str(n), True, True) for n in range(4))
    for sha, said in zip(shas, ("none", "none", "reproduced", "reproduced")):
        archive.said(fr.FOLDER, sha, said)
    return archive, shas


def test_the_futility_check_runs_after_each_block_and_stops_only_when_the_prediction_is_out_of_reach(tmp_path, monkeypatch, capsys):
    archive, shas = _four(tmp_path, monkeypatch)
    for sha, said in zip(shas, ("none", "reproduced", "reproduced", "reproduced")):
        archive.said(fr.REPRODUCE, sha, said)
    out = _report(archive.root, capsys, ["x-1"], futile=True, block=2)
    first = _line(out, "futility reproduce first, block 1")
    assert "(patches 1-2 by digest): 2 reviewed, 2 remaining of 4" in first
    assert "at best agreement +25.0 points (needs +10.0), false alarms -25.0 points (at most +5.0): can still be reached" in first
    second = _line(out, "futility reproduce first, block 2")
    assert "4 reviewed, 0 remaining of 4" in second and "agreement -25.0 points" in second and "cannot be reached" in second


@pytest.mark.parametrize("raw", [b'{"said":', b"[1]", b"\xff\xfe{}"])
def test_a_review_record_that_cannot_be_read_is_no_review(tmp_path, monkeypatch, capsys, raw):
    archive, shas = _four(tmp_path, monkeypatch)
    for sha in shas[1:]:
        archive.said(fr.REPRODUCE, sha, "none")
    (archive.root / "loop" / fr.REPRODUCE / f"{shas[0]}.json").write_bytes(raw)
    assert rc.patches(archive.root, ["x-1"])[shas[0]][rc.REPRODUCE] is None
    assert rc.futile(archive.root, ["x-1"], rc.REPRODUCE) == ""
    assert "futility reproduce first" not in _report(archive.root, capsys, ["x-1"], futile=True, block=2)


@pytest.mark.parametrize("record", [{"task": "x-1", "first_seen_in": "run/A1"}, {"said": None}, {"said": "maybe"},
                                    {"said": ["none"]}])
def test_a_record_with_no_word_a_reviewer_gives_is_no_review_to_the_counts_and_the_scheduler(tmp_path, monkeypatch, record):
    archive = _Archive(tmp_path / "round3", monkeypatch)
    sha = archive.run("x-1", "one", True, True)
    for _, folder in rc.REVIEWERS:
        (archive.root / "loop" / folder).mkdir(exist_ok=True)
        (archive.root / "loop" / folder / f"{sha}.json").write_text(json.dumps(record), "utf-8")
        assert len(fr.waiting(archive.root, ["x-1"], folder)) == 1
    rows = rc.patches(archive.root, ["x-1"])
    for name, _ in rc.REVIEWERS:
        assert not rc.counted(rows[sha][name]) and rc.tally(rows, name, "amended")["n"] == 0, name


def test_a_record_with_no_word_a_reviewer_gives_completes_no_block(tmp_path, monkeypatch, capsys):
    archive = _Archive(tmp_path / "round3", monkeypatch)
    shas = sorted(archive.run("x-1", str(n), True, True) for n in range(4))
    for sha in shas:
        archive.said(fr.FOLDER, sha, "none")
    (archive.root / "loop" / pr.FOLDER).mkdir()
    (archive.root / "loop" / pr.FOLDER / f"{shas[0]}.json").write_text(json.dumps({"said": "maybe"}), "utf-8")
    archive.said(pr.FOLDER, shas[1], "defect")
    assert rows_none(rc.patches(archive.root, ["x-1"]), shas[0], rc.PRODUCT)
    assert rc.futile(archive.root, ["x-1"], rc.PRODUCT, block=2) == ""
    assert "futility product" not in _report(archive.root, capsys, ["x-1"], futile=True, block=2)


def rows_none(rows, sha, name):
    return rows[sha][name] is None


def test_a_last_block_short_of_a_whole_one_is_no_checkpoint(tmp_path, monkeypatch, capsys):
    archive = _Archive(tmp_path / "round3", monkeypatch)
    shas = sorted(archive.run("x-1", str(n), True, True) for n in range(3))
    for sha in shas:
        archive.said(fr.FOLDER, sha, "none")
    for sha, said in zip(shas, ("failed", "none", "defect")):     # the failed one waits to be made again
        archive.said(pr.FOLDER, sha, said)
    assert rc.futile(archive.root, ["x-1"], rc.PRODUCT, block=2) == ""
    out = _report(archive.root, capsys, ["x-1"], futile=True, block=2)
    assert "futility product, block 1" in out and "futility product, block 2" not in out


@pytest.mark.parametrize("last", ["failed", "reproduced"])
def test_a_failed_review_is_still_to_be_made_so_the_stop_still_applies(tmp_path, monkeypatch, last):
    archive, shas = _four(tmp_path, monkeypatch)
    for sha, said in zip(shas, ("reproduced", "reproduced", "reproduced", last)):
        archive.said(fr.REPRODUCE, sha, said)
    stop = rc.futile(archive.root, ["x-1"], rc.REPRODUCE, block=2)
    if last == "failed":           # made again on a resumed run, as ``waiting`` queues it: the block that ended it still stops it
        assert "futility reproduce first, block 2" in stop and "cannot be reached" in stop
    else:                          # every review counts: there is nothing left to stop
        assert stop == ""


def test_the_predictions_are_read_on_stage_1_and_2_together_and_never_on_training():
    groups = {name: set(tasks) for name, tasks, _ in rc.groups()}
    assert rc.predicted() == groups["stage 1 and 2"] and not rc.predicted() & groups["training"]


def test_the_futility_check_needs_both_parts_of_the_prediction(tmp_path, monkeypatch, capsys):
    archive = _Archive(tmp_path / "round3", monkeypatch)
    cells = [(True, "none", "reproduced")] + [(True, "none", "none")] * 3 + [(False, "none", "reproduced")] * 4
    for n, (truth, frozen, reproduce) in enumerate(cells):     # far ahead on agreement, by more false alarms than it may have
        sha = archive.run("x-1", str(n), truth, truth)
        archive.said(fr.FOLDER, sha, frozen)
        archive.said(fr.REPRODUCE, sha, reproduce)
    line = _line(_report(archive.root, capsys, ["x-1"], futile=True, block=8), "futility reproduce first, block 1")
    assert "agreement +37.5 points" in line and "false alarms +25.0 points" in line and "cannot be reached" in line


def test_the_products_futility_check_is_on_matching_verdicts_and_waits_for_a_whole_block(tmp_path, monkeypatch, capsys):
    archive, shas = _four(tmp_path, monkeypatch)
    for sha, said in zip(shas[:3], ("none", "none", "none")):
        archive.said(pr.FOLDER, sha, said)
    out = _report(archive.root, capsys, ["x-1"], futile=True, block=2)
    assert "2 reviewed, 2 remaining of 4; at best 100.0% of verdicts match (needs 85.0%): can still be reached" in \
        _line(out, "futility product, block 1")
    assert "futility product, block 2" not in out                # the fourth patch has no review yet
    archive.said(pr.FOLDER, shas[3], "failed")
    out = _report(archive.root, capsys, ["x-1"], futile=True, block=2)
    assert "3 reviewed, 1 remaining of 4; at best 75.0% of verdicts match (needs 85.0%): cannot be reached" in \
        _line(out, "futility product, block 2")
    assert "futility" not in _report(archive.root, capsys, ["x-1"])     # only stage 1 and 2 together carry the predictions


def test_nothing_but_counts_is_printed(tmp_path, monkeypatch, capsys):
    archive = _six(tmp_path, monkeypatch)
    for sha in (p.stem for p in (archive.root / "loop" / fr.FOLDER).glob("*.json")):
        archive.said(pr.FOLDER, sha, "defect")
    out = _report(archive.root, capsys, ["x-1", "y-1"], futile=True, block=2)
    assert out and SECRET not in out and "x-1" not in out and "y-1" not in out


def test_the_groups_are_the_plans():
    groups = {name: (set(tasks), futile) for name, tasks, futile in rc.groups()}
    fresh, unseen = {t.id for t in we.load_tasks(we.TASKS_FRESH)}, {t.id for t in we.load_tasks(we.TASKS_UNSEEN)}
    assert groups["stage 1"] == (fresh | set(we.R3_HELD), False) and groups["stage 2"] == (unseen, False)
    assert groups["stage 1 and 2"] == (fresh | set(we.R3_HELD) | unseen, True)
    assert groups["training"] == (set(fr.TRAINING), False)


@pytest.mark.parametrize("name", ["patches", "right", "tally", "paired", "matched", "futility", "report", "groups"])
def test_the_module_says_what_each_step_does(name):
    assert getattr(rc, name).__doc__
