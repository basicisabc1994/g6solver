# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""A row's manifest is frozen before the row runs: it cannot change, its cohorts are apart, its labels independent."""
from __future__ import annotations

import dataclasses
import json

import pytest

from tests.validation._formalisation import ROOT, case, load, manifest

M = load().manifest


def test_frozen_manifest_cannot_change(tmp_path):
    one = manifest()
    path = tmp_path / "m.json"
    assert M.freeze(one, path) == one.sha256 and M.load(path).sha256 == one.sha256
    looser = dataclasses.replace(one, thresholds={"min_right_acceptance": 0.1, "max_wrong_acceptance": 0.9})
    assert looser.sha256 != one.sha256
    with pytest.raises(M.Frozen, match="already frozen"):
        M.freeze(looser, path)
    held = json.loads(path.read_text(encoding="utf-8"))
    held["manifest"]["thresholds"]["max_wrong_acceptance"] = 0.9      # a threshold edited after the freeze
    path.write_text(json.dumps(held), encoding="utf-8")
    with pytest.raises(M.Frozen, match="changed after it was frozen"):
        M.load(path)


@pytest.mark.parametrize("change, names", [
    ({"thresholds": {"min_right_acceptance": 0.0, "max_wrong_acceptance": 0.05}}, "refusing everything must fail"),
    ({"thresholds": {"max_wrong_acceptance": 0.05}}, "min_right_acceptance"),
    ({"thresholds": {"min_right_acceptance": 0.9, "max_wrong_acceptance": 1.0}}, "max_wrong_acceptance"),
    ({"stop_rule": " "}, "pass/stop rule"), ({"environment": {}}, "environment"), ({"cases": ()}, "no cases"),
    ({"fragments": ("smt.other.v1",)}, "not a supported fragment"), ({"row": "anything"}, "row is one of"),
    ({"adapter_versions": {"smt.relation.int_bool.v1": "smt.return.int_bool.v0"}}, "versions"),
    ({"candidate_authors": ()}, "no candidate author"),
])
def test_a_manifest_missing_a_part_is_not_frozen(tmp_path, change, names):
    bad = dataclasses.replace(manifest(), **change)
    assert any(names in why for why in M.problems(bad)), M.problems(bad)
    with pytest.raises(M.Frozen):
        M.freeze(bad, tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()


def test_final_cohort_disjoint_from_development():
    dev = manifest(cases=(case("development", "t_inc"),))
    final = manifest(cases=(case("final", "t_other", requirement="another requirement", reference="(= result (+ x 2))"),))
    assert M.disjoint(dev, final) == []
    for shared in ({"case_id": "t_inc"}, {"requirement": dev.cases[0].requirement}, {"reference": "(= result (+ x 1))"}):
        both = manifest(cases=(dataclasses.replace(final.cases[0], **shared),))
        assert M.disjoint(dev, both)
    mixed = manifest(cases=(case("development", "a"), case("final", "b")))
    assert any("one cohort" in why for why in M.problems(mixed))
    assert M.disjoint(final, dev)   # the wrong way round is not one development and one final


def test_label_author_is_not_the_candidate():
    drafted = manifest(candidate_authors=("model:anthropic", "model:openai"))
    assert M.problems(drafted) == []                       # a person labelled, two families draft
    own = manifest(cases=(case(label_author="model:anthropic:claude-fable-5-2"),),
                   candidate_authors=("model:anthropic",))
    assert any("never writes both" in why for why in M.problems(own))
    assert M.problems(manifest(cases=(case(label_author="model:anthropic:claude-fable-5-2"),))) == []   # mutations
    assert any("never writes both" in why for why in M.problems(manifest(cases=(case(label_author=""),))))


def test_retired_cohort_refused_for_a_confirmatory_run(tmp_path):
    registry = tmp_path / "retired.json"
    final = manifest(cases=(case("final"),))
    M.confirmatory(final, registry=registry)
    with pytest.raises(M.Frozen, match="development cohort decides no bar"):
        M.confirmatory(manifest(), registry=registry)
    with pytest.raises(M.Frozen, match="with the reason"):
        M.retire(final.sha256, " ", registry=registry)
    M.retire(final.sha256, "its results informed a change to the renderer", registry=registry)
    with pytest.raises(M.Frozen, match="retired"):
        M.confirmatory(final, registry=registry)


def test_the_shipped_manifests_are_frozen_valid_and_apart():
    fixtures = ROOT / "fixtures"
    dev, final = M.load(fixtures / "smt_dev.json"), M.load(fixtures / "smt_final.json")
    assert M.problems(dev) == [] and M.problems(final) == [] and M.disjoint(dev, final) == []
    assert (dev.cohort, final.cohort) == ("development", "final") and len(final.cases) >= 8
    rows = {M.load(path).row for path in fixtures.glob("smt_final*.json")}
    assert rows == {"proposal", "rendering", "correspondence", "archive_defects"}
    for path in fixtures.glob("smt_*.json"):
        one = M.load(path)
        assert M.problems(one) == [] and one.thresholds["min_right_acceptance"] > 0 and one.stop_rule
        assert one.candidate_authors == ("g6:mutation",)
    assert not list(ROOT.rglob("card.json")) and not list(ROOT.rglob("receipts.json"))   # no row has been run