# SPDX-License-Identifier: Apache-2.0
from g6.self_maintenance.experience import UserSignal, summarize_experience, experience_obligations


def test_refuses_to_infer_experience_from_unconsented_signals():
    signals = [UserSignal("new-user", True, 10, 5, 0, False, False, True) for _ in range(20)]
    result = summarize_experience(signals)
    assert result.state == "insufficient_evidence"
    assert result.task_completion_rate is None


def test_feedback_metrics_have_missingness_and_friction():
    rows = [UserSignal("first-time", i != 0, float(i + 1), 4 if i % 2 else 0,
                       i % 3, i == 0, True, True) for i in range(10)]
    result = summarize_experience(rows)
    assert result.consented_sessions == 10
    assert result.task_completion_rate == .9
    assert result.satisfaction_mean == 4
    assert result.accessibility_issues == 1
    assert any("compulsive" in s for s in experience_obligations())
