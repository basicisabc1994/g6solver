# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""``venture.weakened_tests`` measured against a labelled corpus (Gate W D-W7, founder 2026-10-10).

The corpus is ``weakened_tests_corpus.py`` (beside this file): each case is a test file before and after, labelled
``weakened`` or ``benign`` by what a reviewer would say, with the detector's behaviour pinned. This file checks that the
corpus is sound (every label is unique and covered), that the detector still behaves as pinned on every case, that the
measured recall and false-alarm rate match the figures in ``docs/validation/2026-10-10-weakened-tests-corpus.md`` (so the
report cannot drift), and that the blind spots stay listed. The detector remains partial evidence: it compares assertion
syntax and runs nothing, and a clean result never certifies that tests are as strong as before.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from g6.workforce.verify import weakened_tests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPORT = ROOT / "docs/validation/2026-10-10-weakened-tests-corpus.md"

_spec = importlib.util.spec_from_file_location("weakened_tests_corpus", HERE / "weakened_tests_corpus.py")
corpus = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = corpus  # a dataclass resolves its own module while it is built
_spec.loader.exec_module(corpus)  # the corpus is data and a few helpers; it imports nothing of the product


def test_the_corpus_is_sound():
    ids = [c.id for c in corpus.CASES]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    labels = {c.label for c in corpus.CASES}
    assert labels == {"weakened", "benign"}
    assert sum(c.label == "weakened" for c in corpus.CASES) >= 20 and sum(c.label == "benign" for c in corpus.CASES) >= 8
    wanted = {"ordinary assertion", "unittest style", "expected exception", "swallowed failure", "skip or expected failure",
              "mock", "fixture", "parametrisation", "test deletion", "class-based test", "helper", "addition", "formatting",
              "equivalent rewrite", "refactor"}
    assert wanted <= {c.category for c in corpus.CASES}, sorted(wanted - {c.category for c in corpus.CASES})
    for case in corpus.CASES:
        compile(case.before, case.id, "exec")
        if case.after:
            compile(case.after, case.id, "exec")


@pytest.mark.parametrize("case", corpus.CASES, ids=[c.id for c in corpus.CASES])
def test_the_detector_behaves_as_pinned_on_every_case(case):
    before, after = corpus.as_sources(case)
    assert bool(weakened_tests(before, after)) is case.expect_alarm, (case.id, weakened_tests(before, after))


def test_survival_the_measure_can_see_a_detector_that_is_broken():
    """A detector that never alarms, or always alarms, must not produce the measured figures."""
    never = corpus.measure(lambda b, a: [])
    always = corpus.measure(lambda b, a: ["x"])
    real = corpus.measure(weakened_tests)
    assert never["recall"] == 0.0 and always["false_alarm_rate"] == 1.0
    assert 0.0 < real["recall"] < 1.0 and 0.0 < real["false_alarm_rate"] < 1.0 and real["unexpected"] == []


@pytest.mark.skipif(not REPORT.is_file(), reason="the corpus report is in the source repository only")
def test_the_measured_figures_match_the_report():
    real = corpus.measure(weakened_tests)
    text = REPORT.read_text(encoding="utf-8")
    pattern = (r"weakened: (\d+)\s*\|\s*caught: (\d+)\s*\|\s*recall: ([0-9.]+)\s*\|\s*benign: (\d+)\s*\|\s*false alarms: (\d+)"
               r"\s*\|\s*false-alarm rate: ([0-9.]+)")
    found = re.search(pattern, text)
    assert found, "the report must carry its figures on one line in the stated shape"
    assert [float(x) for x in found.groups()] == [real["weakened"], real["caught"], real["recall"], real["benign"],
                                                  real["false_alarms"], real["false_alarm_rate"]]
    for blind in real["blind_spots"]:
        assert blind in text, f"blind spot {blind} is not named in the report"
    for false_alarm in real["false_alarm_cases"]:
        assert false_alarm in text, f"false alarm {false_alarm} is not named in the report"
    assert "partial evidence" in text.lower() and "never certif" in text.lower()


def test_a_clean_result_is_not_a_claim_about_strength():
    """The blind spots are real: a weakening the detector cannot see returns no finding, which is why a clean result
    is evidence of nothing more than 'no change to the asserts it counts'."""
    blind = [c for c in corpus.CASES if c.label == "weakened" and not c.expect_alarm]
    assert len(blind) >= 5
    for case in blind:
        before, after = corpus.as_sources(case)
        assert weakened_tests(before, after) == [], case.id
