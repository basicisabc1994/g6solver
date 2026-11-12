# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""The row runners, on a small test-only manifest. The shipped final cohort is never run by a test."""
from __future__ import annotations

import json

import pytest

z3 = pytest.importorskip("z3")

from tests.validation._formalisation import case, load, manifest  # noqa: E402

PKG = load()
M, R = PKG.manifest, PKG.run_rows


class NoCaller:
    def __getattr__(self, name):
        raise AssertionError("a row that needs no model called one")

    def __call__(self, *args, **kwargs):
        raise AssertionError("a row that needs no model called one")


def frozen(tmp_path, row, **over):
    path = tmp_path / f"{row}.json"
    M.freeze(manifest(row, **over), path)
    return path


def test_archive_defect_row_makes_no_model_call(tmp_path):
    made = R.run(frozen(tmp_path, "archive_defects"), out_dir=tmp_path / "out", callers=NoCaller())
    receipts = json.loads((tmp_path / "out" / "receipts.json").read_text(encoding="utf-8"))
    assert {one["case_id"] for one in receipts} == set(R.ARCHIVE_GROUPS)
    assert (made.right_accepted, made.wrong_refused, made.wrong_accepted, made.unrun) == (1, 1, 0, 3)
    assert [one for one in receipts if one["item"].startswith("a real consistency")][0]["native"] == "unsat"


def test_proposal_row_scores_right_and_labelled_mutants(tmp_path):
    made = R.run(frozen(tmp_path, "proposal"), out_dir=tmp_path / "out", callers=NoCaller())
    receipts = json.loads((tmp_path / "out" / "receipts.json").read_text(encoding="utf-8"))
    reference = [one for one in receipts if one["item"] == "reference"]
    assert reference and all(one["decision"] == "accepted" and one["label"] == "right" for one in reference)
    wrong = [one for one in receipts if one["label"] == "wrong"]
    assert wrong and all(one["witness"] for one in wrong)
    assert all(one["decision"] == "refused" for one in wrong if one["stratum"] == "differs_on_example")
    assert made.manifest_sha256 == M.load(tmp_path / "proposal.json").sha256 and made.total == len(receipts)
    assert (tmp_path / "out" / "card.md").read_text(encoding="utf-8").startswith("# Row: proposal")


def test_rendering_row_refuses_every_forged_pairing(tmp_path):
    made = R.run(frozen(tmp_path, "rendering", thresholds={"min_right_acceptance": 0.95, "max_wrong_acceptance": 0.0}),
                 out_dir=tmp_path / "out")
    assert made.right_accepted == 1 and made.wrong_accepted == 0 and made.wrong_refused >= 2 and made.passed is True


def test_correspondence_row_passes_right_code_and_no_wrong_code(tmp_path):
    made = R.run(frozen(tmp_path, "correspondence",
                        thresholds={"min_right_acceptance": 0.8, "max_wrong_acceptance": 0.0}),
                 out_dir=tmp_path / "out")
    receipts = json.loads((tmp_path / "out" / "receipts.json").read_text(encoding="utf-8"))
    by_item = {one["item"]: one for one in receipts}
    assert by_item["right code"]["decision"] == "accepted" and by_item["wrong code 0"]["decision"] == "refused"
    assert by_item["true model result, code 0 ran instead"]["replayed"] == "not_reproduced"
    assert made.wrong_accepted == 0 and made.passed is True
    (tmp_path / "b").mkdir()
    bare = R.run(frozen(tmp_path / "b", "correspondence", cases=(case(code="", wrong_code=()),)),
                 out_dir=tmp_path / "bare")
    assert bare.unsupported == 1 and bare.passed is None


def test_rows_that_need_people_are_unrun(tmp_path):
    for row in ("questions", "cost"):
        made = R.run(frozen(tmp_path, row), out_dir=tmp_path / row, callers=NoCaller())
        assert made.unrun == made.total == 1 and made.passed is None


def test_a_confirmatory_run_needs_an_unretired_final_cohort(tmp_path):
    registry = tmp_path / "retired.json"
    with pytest.raises(M.Frozen):
        R.run(frozen(tmp_path, "proposal"), out_dir=tmp_path / "out", registry=registry, confirm=True)
    assert not (tmp_path / "out").exists()
    final = tmp_path / "final.json"
    one = manifest("proposal", cases=(case("final"),))
    M.freeze(one, final)
    M.retire(one.sha256, "it informed a repair", registry=registry)
    with pytest.raises(M.Frozen, match="retired"):
        R.run(final, out_dir=tmp_path / "out", registry=registry, confirm=True)


def test_every_row_has_a_runner():
    assert set(R.ROWS) == set(M.ROWS)