# SPDX-License-Identifier: Apache-2.0
"""regression_delta: new failures, lost tests and newly skipped tests all fail; nothing to compare is unevaluable."""
from __future__ import annotations

from g6.self_training import (
    HarnessPatch,
    RunSummary,
    failing_node_ids,
    govern_patch_promotion,
    regression_delta,
)

A, B, C = "tests/demo/test_x.py::test_a", "tests/demo/test_x.py::test_b", "tests/demo/test_x.py::test_c"
BASE = RunSummary(failing=frozenset({C}), collected=frozenset({A, B, C}))


def _run(*, failing=(), collected=(A, B, C), skipped=(), evaluable=True) -> RunSummary:
    return RunSummary(frozenset(failing), frozenset(collected), frozenset(skipped), evaluable)


def test_the_parser_reads_failed_and_error_lines_through_ansi_colour() -> None:
    out = f"\x1b[31mFAILED\x1b[0m {A} - assert 1 == 2\nERROR {B}\n  FAILED not-a-summary-line\nPASSED {C}\n"
    assert failing_node_ids(out) == {A, B, "not-a-summary-line"}
    assert failing_node_ids("\x1b[1m\x1b[31mFAILED\x1b[0m " + C) == {C}


def test_an_old_failure_is_not_a_regression() -> None:
    verdict = regression_delta(_run(failing=[C]), BASE)
    assert verdict.state == "passed" and verdict.new_failures == frozenset()


def test_a_new_failure_fails() -> None:
    verdict = regression_delta(_run(failing=[A, C]), BASE)
    assert verdict.state == "failed" and verdict.new_failures == {A}


def test_a_skip_all_or_deselect_all_change_fails() -> None:
    """REL-003: a conftest that skips (or deselects) every test made the archive's gate green."""
    skipped = regression_delta(_run(skipped=[A, B, C]), BASE)
    assert skipped.state == "failed" and skipped.newly_skipped == {A, B, C}
    lost = regression_delta(_run(collected=[A]), BASE)
    assert lost.state == "failed" and lost.lost == {B, C}


def test_nothing_to_compare_is_unevaluable_never_a_pass() -> None:
    assert regression_delta(_run(evaluable=False), BASE).state == "unevaluable"
    assert regression_delta(_run(collected=()), BASE).state == "unevaluable"
    assert regression_delta(_run(), None).state == "unevaluable"
    assert regression_delta(_run(), _run(evaluable=False)).state == "unevaluable"
    assert regression_delta(_run(failing=[A]), None).state == "failed"   # no baseline: any failure counts


def test_the_gate_holds_a_passing_regression_check_below_verified() -> None:
    report = regression_delta(_run(failing=[C]), BASE).to_eval_report()
    assert report.independent_eval is False and report.regression_pass_rate == 1.0
    decision = govern_patch_promotion(HarnessPatch(agent_id="agent", target_surfaces=["documentation"]), report)
    assert decision.reliability_label != "verified"
