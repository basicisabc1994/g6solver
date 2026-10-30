# SPDX-License-Identifier: Apache-2.0
"""K-P0-1 kernel promotion law: a generator cannot assert 'verified', a doctored witness never promotes, and the law
holds at every door an envelope can be built through.

The law is always on (plan A1); its off, shadow and per-surface scope modes are gone, and
``tests/core/test_door_promotion.py`` pins that their switches change nothing. What a witness must be to promote (one
of G6's own re-checkers, reproducing at the door) is tested there too; these are the structural faults.
"""
from __future__ import annotations

import pytest
from g6.core.reliability_envelope import (
    ReliabilityEnvelope,
    build_promotion_witness,
    envelope_fields,
    promotion_witness_faults,
)


def _witness(**kwargs) -> dict:
    defaults = {
        "evaluator_id": "agent:independent-checker",
        "execution_id": "exec-001",
        "oracle": "subprocess-pytest",
        "verdict": "pass",
        "task_id": "task-1",
        "run_id": "run-1",
    }
    defaults.update(kwargs)
    return build_promotion_witness(**defaults)


def test_an_envelope_without_a_witness_dumps_no_witness_key() -> None:
    assert "promotion_witness" not in ReliabilityEnvelope().model_dump()
    assert "promotion_witness" not in envelope_fields()


def test_a_stated_verified_without_a_witness_is_demoted() -> None:
    # #135: a bare envelope defaults to qualified-draft, so it cannot exercise a DEMOTION. State verified; that is
    # what the law acts on.
    envelope = ReliabilityEnvelope(
        completion_state="verified", reliability_label="verified"
    )
    assert envelope.reliability_label == "qualified-draft"
    assert envelope.completion_state == "qualified-draft"
    assert envelope.degraded is True
    assert envelope.degradation_reason.startswith("kernel promotion law:")
    assert any("kernel promotion law" in c for c in envelope.missing_checks)
    # INV-STATE-2: the demoted envelope surfaces honest limitations.
    assert envelope.known_limitations
    assert envelope.recommended_next_steps


def test_the_builder_demotes_too() -> None:
    fields = envelope_fields(reliability_label="verified")
    assert fields["completion_state"] == "qualified-draft"
    assert fields["reliability_label"] == "qualified-draft"


def test_a_doctored_witness_is_a_fault() -> None:
    """A witness with a valid shape but rewritten content must not promote (mirrors
    test_doctored_verdict_rejected_at_the_ledger_door)."""
    witness = _witness()
    witness["verdict"] = "pass (doctored)"
    envelope = ReliabilityEnvelope(
        completion_state="verified",
        reliability_label="verified",
        task_id="task-1",
        run_id="run-1",
        promotion_witness=witness,
    )
    assert envelope.reliability_label == "qualified-draft"
    assert "content_digest" in envelope.degradation_reason


def test_a_witness_without_an_execution_identity_is_a_fault() -> None:
    """The digest excludes execution provenance (the sealing rule), so deleting it isolates the witnessed-execution
    check: 'did not run' is never a promotion."""
    witness = _witness()
    del witness["execution"]
    envelope = ReliabilityEnvelope(
        completion_state="verified",
        reliability_label="verified",
        task_id="task-1",
        run_id="run-1",
        promotion_witness=witness,
    )
    assert envelope.reliability_label == "qualified-draft"
    assert "execution" in envelope.degradation_reason


def test_a_witness_replayed_from_another_run_is_a_fault() -> None:
    envelope = ReliabilityEnvelope(
        completion_state="verified",
        reliability_label="verified",
        task_id="task-1",
        run_id="run-2",
        promotion_witness=_witness(),  # sealed for run-1
    )
    assert envelope.reliability_label == "qualified-draft"
    assert "never transfers between runs" in envelope.degradation_reason


def test_the_law_is_down_only_a_witness_never_upgrades() -> None:
    for label in ("qualified-draft", "blocked-escalated"):
        envelope = ReliabilityEnvelope(
            reliability_label=label,
            task_id="task-1",
            run_id="run-1",
            promotion_witness=_witness(),
        )
        assert envelope.reliability_label == label


def test_the_law_holds_at_the_revalidation_door() -> None:
    """Door 2 (the D2S2 parity lesson): a serialized dict claiming 'verified' without a witness demotes on
    re-validation, and a demoted envelope stays demoted on round-trip."""
    forged = {
        "completion_state": "verified",
        "reliability_label": "verified",
        "request_id": "req-1",
    }
    envelope = ReliabilityEnvelope.model_validate(forged)
    assert envelope.reliability_label == "qualified-draft"
    reloaded = ReliabilityEnvelope(**envelope.model_dump())
    assert reloaded.reliability_label == "qualified-draft"


def test_a_missing_witness_is_a_single_honest_fault() -> None:
    faults = promotion_witness_faults(None)
    assert len(faults) == 1
    assert "no promotion witness attached" in faults[0]


def test_an_unserializable_witness_core_fails_closed() -> None:
    witness = _witness()
    witness["details"] = {"payload": object()}
    assert any(
        "canonically serialized" in f for f in promotion_witness_faults(witness)
    )


def test_the_builder_rejects_empty_identities() -> None:
    with pytest.raises(ValueError):
        build_promotion_witness(evaluator_id="", execution_id="exec-1")
    with pytest.raises(ValueError):
        build_promotion_witness(evaluator_id="agent:c", execution_id="")
