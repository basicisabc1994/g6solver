# SPDX-License-Identifier: Apache-2.0
"""The record of the reviews of G6's own code and its root-cause counts, on made-up reports and a small git repository."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "own_code"))

import ledger  # noqa: E402

REPORT = """## Finding 1
Rule: A saved record must count once.
Why: `validation/x.py:12` counts it twice, and `src/g6/y.py:3` too.
Test: tests/_review/test_finding_1.py
Observed: assert 2 == 1

## Finding 2
Rule: A failure is no success.
Why: the exit is dropped.
Test: tests/_review/test_finding_2.py
Observed: assert 'none' == 'failed'

## Looked at
- everything
"""


@pytest.mark.parametrize(("folder", "expected"), [
    ("review-rpf2", ("claude/replays-followup", 2, "")), ("review-fx3b", ("claude/loop-followup-2", 3, "b")),
    ("review-k", ("claude/k-loop", 1, "")), ("review-kfu1b", ("claude/k-followup", 1, "b")),
    ("review-l4b", ("claude/loop-fixes", 4, "b")), ("review-b13", ("claude/loop-batch-1", 3, "")), ("review-zz", (None, None, "")),
])
def test_a_review_folder_names_its_loop_and_round(folder, expected):
    assert ledger.loop_of(folder) == expected


def _report(root: Path, folder: str, text: str) -> None:
    (root / folder / ".review").mkdir(parents=True)
    (root / folder / ".review" / "report.md").write_text(text, "utf-8")


def test_each_finding_is_a_row_and_a_report_with_none_is_kept_apart(tmp_path):
    _report(tmp_path, "review-rp1", REPORT)
    _report(tmp_path, "review-rp2", "No reproduced findings.\n\n## Looked at\n- all\n")
    (tmp_path / "review-rp3").mkdir()                     # a review that wrote no report is no row
    rows, clean = ledger.findings([tmp_path])
    assert [(r["folder"], r["finding"], r["round"]) for r in rows] == [("review-rp1", 1, 1), ("review-rp1", 2, 1)]
    assert rows[0]["rule"] == "A saved record must count once." and rows[0]["cites"] == ["src/g6/y.py:3", "validation/x.py:12"]
    assert rows[1]["cites"] == [] and "Looked at" not in rows[1]["why"]
    assert [c["folder"] for c in clean] == ["review-rp2"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, check=True, text=True).stdout


def test_a_later_finding_is_in_the_last_repair_only_where_that_commit_wrote_a_line_it_cites(tmp_path):
    repo = tmp_path / "review-rp2"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("one\ntwo\nthree\n", "utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "build")
    (repo / "a.py").write_text("one\nTWO\nthree\n", "utf-8")
    _git(repo, "commit", "-qam", "the repair this review read")
    rows = [{"folder": "review-rp2", "finding": 1, "round": 2, "cites": ["a.py:2"]},
            {"folder": "review-rp2", "finding": 2, "round": 2, "cites": ["a.py:1", "a.py:3"]},
            {"folder": "review-rp2", "finding": 3, "round": 2, "cites": ["gone.py:1"]},
            {"folder": "review-rp2", "finding": 4, "round": 2, "cites": []},
            {"folder": "review-rp2", "finding": 5, "round": 1, "cites": ["a.py:2"]}]
    assert ledger.blamed(rows, [tmp_path]) == {("review-rp2", 1): "last repair", ("review-rp2", 2): "earlier",
                                               ("review-rp2", 3): "unresolved", ("review-rp2", 4): "no cite",
                                               ("review-rp2", 5): "round 1"}


def test_an_older_line_is_one_round_1_read_or_one_an_earlier_repair_wrote(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    shas = []
    for at, text in enumerate(("one\ntwo\nthree\n", "one\nTWO\nthree\n", "one\nTWO\nTHREE\n")):
        (src / "a.py").write_text(text, "utf-8")
        _git(src, "add", "a.py")
        _git(src, "commit", "-qm", f"commit {at}")
        shas.append(_git(src, "rev-parse", "HEAD").strip())
    _git(src, "worktree", "add", "-q", "--detach", str(tmp_path / "review-rp1"), shas[0])   # the build, read by round 1
    _git(src, "worktree", "add", "-q", "--detach", str(tmp_path / "review-rp3"), shas[2])   # the second repair, read by round 3
    rows = [{"folder": "review-rp1", "finding": 1, "round": 1, "cites": ["a.py:1"]},
            {"folder": "review-rp3", "finding": 1, "round": 3, "cites": ["a.py:1"]},
            {"folder": "review-rp3", "finding": 2, "round": 3, "cites": ["a.py:2"]},
            {"folder": "review-rp3", "finding": 3, "round": 3, "cites": ["a.py:3"]},
            {"folder": "review-rp3", "finding": 4, "round": 3, "cites": ["a.py:1", "a.py:2"]}]
    assert ledger.blamed(rows, [tmp_path]) == {("review-rp1", 1): "round 1", ("review-rp3", 1): "before the loop",
                                               ("review-rp3", 2): "earlier repair", ("review-rp3", 3): "last repair",
                                               ("review-rp3", 4): "earlier repair"}


def test_a_class_its_repair_removed_comes_back_less_often_than_chance():
    rows = [{"folder": f"review-rp{k}", "finding": n, "branch": "b", "round": k} for k in (1, 2, 3) for n in (1, 2)]
    labels = {(f"review-rp{k}", n): {"mechanism": m, "effect": "latent"}
              for k, m in ((1, "boundary_case"), (2, "state_not_durable"), (3, "one_fact_two_rules")) for n in (1, 2)}
    got = ledger.by_chance(rows, labels, draws=500, seed=1)
    assert got["observed"] == 0 and got["mean"] > 1 and got["share_at_or_below"] < 0.2   # exactly 6 in 90 orders give 0
    assert ledger.by_chance(rows, labels, draws=500, seed=1) == got


def test_the_counts_name_a_class_that_came_back_after_the_first_repair():
    rows = [{"folder": f"review-rp{k}", "finding": n, "branch": "b", "round": k} for k, n in ((1, 1), (1, 2), (2, 1), (3, 1))]
    labels = {("review-rp1", 1): {"mechanism": "one_fact_two_rules", "effect": "changes_outcome"},
              ("review-rp1", 2): {"mechanism": "boundary_case", "effect": "latent"},
              ("review-rp2", 1): {"mechanism": "one_fact_two_rules", "effect": "changes_outcome"},
              ("review-rp3", 1): {"mechanism": "other", "other": "a new kind", "effect": "fails_loudly"}}
    lines = {("review-rp2", 1): "last repair", ("review-rp3", 1): "earlier"}
    got = ledger.analysis(rows, labels, lines)
    assert got["classes"]["one_fact_two_rules"] == {"findings": 2, "silent": 2, "loops": 1, "came_back": 1, "in_last_repair": 1}
    assert got["classes"]["other: a new kind"]["came_back"] == 0 and got["met_again"] == 1 and got["later_rounds"] == 2
    assert got["later_in_last_repair"] == 1 and got["later_resolved"] == 2
    assert got["later_by_lines"] == {"last repair": 1, "earlier": 1}
    assert got["by_loop"]["b"] == {"by_round": [2, 1, 1], "last_round": {"fails_loudly": 1},
                                   "later_lines": {"last repair": 1, "earlier": 1}}
    assert got["effect_by_round"][1] == {"changes_outcome": 1, "latent": 1}


def test_every_named_class_has_one_check_a_reviewer_and_a_repair_can_run():
    assert set(ledger.CHECKS) == set(ledger.CLASSES) - {"other"}
    assert all(check.endswith(".") and "test" in check for check in ledger.CHECKS.values())


def test_the_labels_classes_are_the_closed_list_the_labeller_is_given():
    assert len(set(ledger.CLASSES)) == 10 and ledger.CLASSES[-1] == "other" and ledger.EFFECTS == (
        "changes_outcome", "fails_loudly", "latent")
