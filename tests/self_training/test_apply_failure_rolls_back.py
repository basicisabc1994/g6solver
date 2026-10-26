# SPDX-License-Identifier: Apache-2.0
"""Review B1-6: when ``apply_fn`` raises halfway, the cycle that holds ``rollback_fn`` uses it, and says whether the
rollback returned or raised. The result stays ``blocked-escalated`` either way. Offline, fakes only."""
from __future__ import annotations

import pytest

import g6.self_training.improvement_loop as loop
from g6.core import stop
from g6.invariants import assert_envelope
from g6.self_training import HarnessPatch, PatchEvalReport, run_improvement_cycle

DIFF = "--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n"


@pytest.fixture(autouse=True)
def _own_home_and_no_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6_home"))
    monkeypatch.delenv("G6_STOP", raising=False)
    stop.release()
    yield
    stop.release()


class _HalfWritten:
    """A system whose apply writes one of two files, then raises."""

    def __init__(self, rollback_raises: bool = False) -> None:
        self.files = {"a": "old", "b": "old"}
        self.rollback_raises = rollback_raises
        self.rollbacks = 0

    def apply(self, patch: HarnessPatch) -> None:
        self.files["a"] = "new"
        raise RuntimeError("disk full after the first file")

    def rollback(self, patch: HarnessPatch) -> None:
        self.rollbacks += 1
        if self.rollback_raises:
            raise OSError("the backup is gone")
        self.files = {"a": "old", "b": "old"}


def _cycle(system: _HalfWritten):
    patch = HarnessPatch(patch_id="p1", target_surfaces=["documentation"], agent_id="generator-a", diff=DIFF)
    passing = PatchEvalReport(targeted_failure_fixed=True, regression_pass_rate=1.0, target_metric_improvement=0.1,
                              evaluator_confidence=0.9, independent_eval=True, evaluator_id="evaluator-b")
    return run_improvement_cycle(sense_fn=lambda: 0.5, propose_fn=lambda sensed: patch,
                                 evaluate_fn=lambda p: passing, apply_fn=system.apply, rollback_fn=system.rollback,
                                 remeasure_fn=lambda: 0.5, regressed_fn=lambda before, after: False, enabled=True,
                                 apply_enabled=True, autonomous_low_risk=True)


def test_a_half_applied_patch_is_rolled_back_by_the_cycle() -> None:
    system = _HalfWritten()
    result = _cycle(system)
    assert system.rollbacks == 1 and system.files == {"a": "old", "b": "old"}, "the cycle did not roll back"
    assert (result.code, result.completion_state) == (loop.APPLY_FAILED, "blocked-escalated")
    assert (result.applied, result.rollback_attempted, result.rolled_back, result.rollback_failed) == (
        False, True, True, False)
    assert result.patch_in_effect is False and "rolled it back" in result.message
    assert any("disk full" in r for r in result.reasons)
    out = result.to_dict()
    assert_envelope(out)
    assert out["rolled_back"] is True and out["warning_card"]["code"] == loop.APPLY_FAILED


def test_a_rollback_that_raises_after_a_failed_apply_is_reported() -> None:
    system = _HalfWritten(rollback_raises=True)
    result = _cycle(system)
    assert system.rollbacks == 1
    assert (result.code, result.completion_state) == (loop.ROLLBACK_FAILED, "blocked-escalated")
    assert (result.rollback_attempted, result.rolled_back, result.rollback_failed) == (True, False, True)
    assert result.patch_in_effect is None and "partly applied" in result.message
    assert any("disk full" in r for r in result.reasons) and any("the backup is gone" in r for r in result.reasons)
    assert_envelope(result.to_dict())
