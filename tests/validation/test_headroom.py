# SPDX-License-Identifier: Apache-2.0
"""Headroom (PLAN.md, 2026-11-04, evening): where a harness can still gain over one run, bug by bug, and what would have to
change where it cannot. Plain code over recorded patches and saved reviews."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import headroom as hr  # noqa: E402

LAYERS = ("review", "symptom")


def p(truth, **flags):
    return {"truth": truth, "flags": {name: flags.get(name) for name in LAYERS}}


def test_a_bug_the_issue_does_not_determine_gets_no_claim_whatever_its_patches():
    assert hr.classify([p(True), p(False, review=True)], LAYERS, determined=False)[0] == "undetermined"


def test_every_patch_right_is_solved_and_no_patch_right_is_a_generation_limit():
    assert hr.classify([p(True, review=False), p(True)], LAYERS, determined=True)[0] == "solved"
    assert hr.classify([p(False, review=True), p(False)], LAYERS, determined=True)[0] == "generation"


def test_a_layer_that_flags_a_wrong_patch_and_no_right_one_makes_the_bug_separable():
    kind, by = hr.classify([p(True, review=False, symptom=False), p(False, review=True, symptom=False)], LAYERS, determined=True)
    assert kind == "separable" and by == ["review"]


def test_a_layer_that_also_flags_a_right_patch_or_never_judged_one_does_not_separate():
    flags_right = [p(True, review=True), p(False, review=True)]
    assert hr.classify(flags_right, LAYERS, determined=True) == ("inseparable", [])
    never_judged_right = [p(True, review=None), p(False, review=True)]
    assert hr.classify(never_judged_right, LAYERS, determined=True) == ("inseparable", [])


def test_an_unknown_determinacy_is_read_as_the_patches_say_and_never_as_undetermined():
    assert hr.classify([p(True)], LAYERS, determined=None)[0] == "solved"


def test_the_bound_is_the_share_right_after_dropping_the_separating_layers_flags():
    patches = [p(True, review=False), p(False, review=True), p(False, review=True)]
    runs = [True, False, False, False]                 # one run right in four
    assert hr.bound(patches, runs, ["review"]) == pytest.approx(1.0)
    assert hr.bound(patches, runs, []) == pytest.approx(0.25)


def test_the_report_counts_each_class_and_names_what_must_change(capsys):
    bugs = {"b1": ([p(True)], [True, True], True), "b2": ([p(False, review=True)], [False], True),
            "b3": ([p(True, review=False), p(False, review=True)], [True, False], True), "b4": ([p(True)], [True], False)}
    got = hr.summary(bugs, LAYERS)
    assert {k: v["bugs"] for k, v in got.items() if v["bugs"]} == {"solved": ["b1"], "generation": ["b2"], "separable": ["b3"],
                                                                   "undetermined": ["b4"]}
    assert got["separable"]["baseline"] == pytest.approx(0.5) and got["separable"]["bound"] == pytest.approx(1.0)
    assert all(hr.WHAT[kind] for kind in hr.KINDS)
