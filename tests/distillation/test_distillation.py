# SPDX-License-Identifier: Apache-2.0
"""g6.distillation against audit 025's falsification tests (HD-T05, T11, T12) and its §6 admission rules: a training
case is never a holdout, a negative control that passes makes the report inconclusive, and nothing passes on cases
that cannot tell right from wrong. Real child processes; offline."""
from __future__ import annotations

import sys

import pytest

from g6.distillation import Candidate, HoldoutCase, check_source, evaluate, run_batch, run_sandboxed

REVERSE = Candidate("def solve(x):\n    return x[::-1]\n", origin_episode_ids=("e1",), training_case_ids=("t1", "t2"))
IDENTITY = Candidate("def solve(x):\n    return x\n")
HOLDOUT = [HoldoutCase("h1", "abc", "cba"), HoldoutCase("h2", "ab", "ba"), HoldoutCase("h3", "", "")]


def test_t11_dunder_access_through_format_is_refused():
    assert check_source("def solve(x):\n    return '{0.__class__}'.format(x)\n") == ["attribute 'format' is not allowed"]
    assert run_sandboxed("def solve(x):\n    return '{0.__class__}'.format(x)\n", 1).status == "refused"


@pytest.mark.parametrize("source", ["def solve(x):\n    return type(x)\n", "import os\ndef solve(x):\n    return 1\n",
                                    "def solve(x):\n    return x.__class__\n", "def solve(x):\n    return open('f')\n"])
def test_the_pre_filter_refuses_reaching_the_interpreter(source):
    assert check_source(source)


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS caps no memory (g6.core.sandbox.memory_limit)")
def test_t12_a_huge_allocation_fails_under_the_memory_cap():
    found = run_sandboxed("def solve(x):\n    return len('a' * 10 ** 10)\n", 1, timeout_s=20, memory_mb=128)
    assert not found.ok and found.status in ("error", "timeout"), found


def test_a_batch_runs_each_input_and_keeps_errors_apart():
    runs = run_batch("def solve(x):\n    return 10 // x\n", [2, 0, 5])
    assert [r.status for r in runs] == ["ok", "error", "ok"] and [runs[0].value, runs[2].value] == [5, 2]
    assert "ZeroDivisionError" in runs[1].reason


def test_an_answer_that_is_not_json_is_an_error():
    assert run_sandboxed("def solve(x):\n    return {1, 2}\n", 0).status == "error"


def test_a_right_candidate_passes_with_a_failing_negative_control():
    report = evaluate(REVERSE, HOLDOUT, negative_control=IDENTITY, positive_control=REVERSE)
    assert (report.verdict, report.completion_state) == ("passed", "qualified-draft")
    assert report.negative_control_failed == ("h1", "h2") and report.candidate_digest == REVERSE.digest


def test_t05_a_wrong_candidate_fails():
    wrong = Candidate("def solve(x):\n    return 'WRONG'\n")
    report = evaluate(wrong, HOLDOUT, negative_control=IDENTITY)
    assert (report.verdict, report.completion_state) == ("failed", "blocked-escalated")
    assert report.failed == ("h1", "h2", "h3")


def test_a_training_case_is_never_held_out():
    report = evaluate(REVERSE, [*HOLDOUT, HoldoutCase("t1", "xy", "yx")], negative_control=IDENTITY)
    assert report.verdict == "refused" and "t1" in report.reason and not report.outcomes


def test_cases_a_wrong_control_also_passes_are_inconclusive():
    """Only palindromes: the identity function, a wrong reverse, passes them all."""
    palindromes = [HoldoutCase("p1", "aba", "aba"), HoldoutCase("p2", "", "")]
    report = evaluate(REVERSE, palindromes, negative_control=IDENTITY)
    assert report.verdict == "inconclusive" and "negative control" in report.reason


def test_a_failing_positive_control_says_the_cases_are_wrong():
    bad_key = [HoldoutCase("h1", "abc", "abc!")]
    report = evaluate(REVERSE, bad_key, negative_control=IDENTITY, positive_control=REVERSE)
    assert report.verdict == "inconclusive" and "positive control" in report.reason


def test_a_baseline_is_compared_pair_by_pair():
    report = evaluate(REVERSE, HOLDOUT, negative_control=IDENTITY, baseline=lambda x: x)
    assert report.baseline == {"baseline_passed": 1, "wins": 2, "losses": 0, "p_one_sided": 0.25}


def test_nothing_runs_without_cases_or_with_a_refused_source():
    assert evaluate(REVERSE, [], negative_control=IDENTITY).verdict == "refused"
    refused = evaluate(Candidate("import os\ndef solve(x):\n    return x\n"), HOLDOUT, negative_control=IDENTITY)
    assert refused.verdict == "refused" and "imports" in refused.reason


@pytest.mark.parametrize("source", ["", "import os", "def solve(x):\n    raise ValueError('no')\n",
                                    "def answer(x):\n    return x\n"])
def test_a_negative_control_that_does_not_run_does_not_satisfy_the_control(source):
    """No solve, refused by the pre-filter, or raising on every case: it answered nothing, so it shows nothing."""
    report = evaluate(REVERSE, HOLDOUT, negative_control=Candidate(source))
    assert (report.verdict, report.completion_state) == ("refused", "blocked-escalated")
    assert report.code == "G6_E_DISTILLATION_NEGATIVE_CONTROL_DID_NOT_RUN" and "negative control" in report.reason
    assert report.to_dict()["code"] == report.code


def test_a_negative_control_that_fails_only_by_raising_is_inconclusive():
    """It answers h3 right and raises on h1 and h2: it returned no wrong answer, so the cases are not shown to tell."""
    raising = Candidate("def solve(x):\n    if x:\n        raise ValueError(x)\n    return x\n")
    report = evaluate(REVERSE, HOLDOUT, negative_control=raising)
    assert report.verdict == "inconclusive" and report.negative_control_failed == ()
