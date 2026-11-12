# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""A row's card: every count with its denominator; a non-decision is never a detection."""
from __future__ import annotations

import dataclasses

import pytest

from tests.validation._formalisation import load, manifest

C = load().cards


def results(**counts):
    out = []
    for name, n in counts.items():
        label, decision = name.split("__")
        out += [{"label": label, "decision": decision, "stratum": "differs_on_example"}] * n
    return out


def test_bars_met_only_with_right_accepted_and_wrong_refused():
    made = C.card(results(right__accepted=19, right__refused=1, wrong__refused=40), manifest())
    assert made.passed is True and (made.eligible_right, made.eligible_wrong) == (20, 40)
    assert made.intervals["right_acceptance"][0] < 0.95 < made.intervals["right_acceptance"][1]
    assert C.card(results(right__accepted=20, wrong__accepted=4, wrong__refused=36), manifest()).passed is False


def test_refusing_everything_fails():
    made = C.card(results(right__refused=20, wrong__refused=40), manifest())
    assert made.passed is False and made.right_accepted == 0 and made.wrong_refused == 40


def test_zero_denominator_is_unavailable():
    assert C.card(results(wrong__refused=40), manifest()).passed is None
    assert C.card(results(right__accepted=20), manifest()).passed is None
    assert C.card([], manifest()).passed is None
    assert "unavailable" in C.render_markdown(C.card([], manifest()))


def test_unsupported_and_timeout_not_detections():
    made = C.card(results(right__accepted=20, wrong__unsupported=10, wrong__timed_out=10, wrong__unavailable=10,
                          wrong__unrun=10), manifest())
    assert (made.wrong_refused, made.eligible_wrong, made.passed) == (0, 0, None)
    assert (made.unsupported, made.timed_out, made.unavailable, made.unrun) == (10, 10, 10, 10)
    kept = C.card(results(right__accepted=20, wrong__refused=1, wrong__timed_out=39), manifest())
    assert kept.eligible_wrong == 1 and kept.timed_out == 39 and kept.total == 60


def test_every_count_has_its_denominator():
    made = C.card(results(right__accepted=18, right__refused=2, wrong__accepted=1, wrong__refused=39,
                          unlabelled__accepted=3, wrong__unsupported=2), manifest())
    text = C.render_markdown(made)
    assert "18 of 20" in text and "2 of 20" in text and "1 of 40" in text and "39 of 40" in text
    assert "Unsupported: 2 of 65" in text and "Unlabelled: 3 of 65" in text
    assert made.manifest_sha256 == manifest().sha256 and made.manifest_sha256 in text
    assert "not a chance" in text and "probab" not in text.lower()
    counted = sum(getattr(made, f.name) for f in dataclasses.fields(made)
                  if f.name in ("right_accepted", "right_refused", "wrong_accepted", "wrong_refused", "unsupported",
                                "unrun", "timed_out", "unavailable", "unlabelled"))
    assert counted == made.total == 65


def test_a_stratum_scored_alone_is_still_reported_whole():
    mixed = (results(right__accepted=20, wrong__refused=30)
             + [{"label": "wrong", "decision": "accepted", "stratum": "agrees_on_example"}] * 10)
    scored = manifest(thresholds={"min_right_acceptance": 0.9, "max_wrong_acceptance": 0.05, "scored_stratum_only": 1.0})
    made = C.card(mixed, scored)
    assert made.passed is True and made.wrong_accepted == 10 and made.strata["agrees_on_example"] == (10, 0)
    assert C.card(mixed, manifest()).passed is False        # scored over all wrong items, the same results fail
    assert "stratum agrees_on_example: accepted 10 of 10" in C.render_markdown(made)


def test_an_unknown_label_or_decision_is_refused():
    with pytest.raises(ValueError):
        C.card([{"label": "right", "decision": "proved"}], manifest())