# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from pydantic import ValidationError

from g6.core.reliability_envelope import ReliabilityEnvelope


def test_reliability_envelope_accepts_only_canonical_labels() -> None:
    for label in ("qualified-draft", "blocked-escalated"):
        envelope = ReliabilityEnvelope(reliability_label=label)
        assert envelope.reliability_label == label
    # Promotion law (enforce by default): a bare claim of `verified` with no
    # promotion witness is demoted — a label alone is never evidence.
    assert ReliabilityEnvelope(reliability_label="verified").reliability_label == "qualified-draft"

    with pytest.raises(ValidationError):
        ReliabilityEnvelope(reliability_label="verified_ready")


def test_reliability_envelope_includes_cross_surface_fields() -> None:
    envelope = ReliabilityEnvelope(
        reliability_label="qualified-draft",
        request_id="req-1",
        task_id="task-1",
        run_id="run-1",
        warning_card={"code": "G6_E_TEST", "message": "degraded"},
        evidence=[{"source": "unit-test", "summary": "checked"}],
    )

    dumped = envelope.model_dump()
    for field in (
        "completion_state",
        "request_id",
        "task_id",
        "run_id",
        "warning_card",
        "evidence",
        "reliability_label",
    ):
        assert field in dumped
    assert dumped["warning_card"]["code"] == "G6_E_TEST"
