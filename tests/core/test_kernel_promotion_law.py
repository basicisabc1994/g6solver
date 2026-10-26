# SPDX-License-Identifier: Apache-2.0
"""K-P0-1 kernel promotion law (G6_KERNEL_PROMOTION_LAW) — dark-slice tests.

Mirrors the science_harness S3 status-authority suite (test_leakage /
test_hidden_evaluator): a generator cannot assert 'verified', a doctored
witness never promotes, and the law holds at every door an envelope can be
built through. The OFF-mode tests are the no-regression guarantee for the
dark landing; the enforce-mode tests are the revert-fail guard.
"""
from __future__ import annotations

import json
import logging

import pytest
from g6.core.reliability_envelope import (
    ReliabilityEnvelope,
    build_promotion_witness,
    effective_promotion_law_mode,
    envelope_fields,
    promotion_enforce_scope,
    promotion_law_mode,
    promotion_witness_faults,
)

FLAG = "G6_KERNEL_PROMOTION_LAW"


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


# --------------------------------------------------------------------- #
# Flag parsing
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "off"),
        ("", "off"),
        ("0", "off"),
        ("off", "off"),
        ("OFF ", "off"),
        ("false", "off"),
        ("no", "off"),
        ("shadow", "shadow"),
        ("Observe", "shadow"),
        ("observe-only", "shadow"),
        ("log", "shadow"),
        ("1", "enforce"),
        ("on", "enforce"),
        ("enforce", "enforce"),
        ("true", "enforce"),
        # A garbled value fails toward the strictest mode, never silently off.
        ("shdow", "enforce"),
    ],
)
def test_promotion_law_mode_words(monkeypatch, raw, expected) -> None:
    if raw is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, raw)
    assert promotion_law_mode() == expected


# --------------------------------------------------------------------- #
# OFF (default): the dark landing changes nothing
# --------------------------------------------------------------------- #
def test_off_mode_default_envelope_unchanged(monkeypatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    # #135: this used to build a BARE envelope and assert it came back
    # verified -- an assertion that pinned the fail-open DEFAULT, which is
    # exactly what #135 removes. The subject here is the LAW's inertness
    # while OFF, so the envelope states its claim explicitly.
    envelope = ReliabilityEnvelope(
        completion_state="verified", reliability_label="verified"
    )
    assert envelope.reliability_label == "verified"
    assert envelope.completion_state == "verified"
    assert envelope.degraded is False


def test_off_mode_dump_shape_has_no_witness_key(monkeypatch) -> None:
    """An envelope without a witness dumps byte-identically to the pre-law
    shape: no promotion_witness key appears (golden/parity suites see no
    change while the flag is OFF)."""
    monkeypatch.delenv(FLAG, raising=False)
    assert "promotion_witness" not in ReliabilityEnvelope().model_dump()
    assert "promotion_witness" not in envelope_fields()


# --------------------------------------------------------------------- #
# SHADOW: verdict computed and logged, nothing applied
# --------------------------------------------------------------------- #
def test_shadow_mode_logs_would_demote_but_does_not_apply(
    monkeypatch, caplog
) -> None:
    monkeypatch.setenv(FLAG, "shadow")
    with caplog.at_level(logging.WARNING, logger="g6.core.reliability_envelope"):
        envelope = ReliabilityEnvelope(
            completion_state="verified",
            reliability_label="verified",
            request_id="req-9",
            run_id="run-9",
        )
    assert envelope.reliability_label == "verified"
    assert envelope.completion_state == "verified"
    records = [r for r in caplog.records if "would-demote" in r.getMessage()]
    assert records, "shadow mode must log the would-demote verdict"
    payload = json.loads(records[0].getMessage().split(": ", 1)[1])
    assert payload["would_demote_to"] == "qualified-draft"
    assert payload["request_id"] == "req-9"
    assert payload["run_id"] == "run-9"
    assert payload["faults"]


def test_shadow_mode_is_silent_for_non_verified_states(
    monkeypatch, caplog
) -> None:
    monkeypatch.setenv(FLAG, "shadow")
    with caplog.at_level(logging.WARNING, logger="g6.core.reliability_envelope"):
        ReliabilityEnvelope(reliability_label="qualified-draft")
        ReliabilityEnvelope(reliability_label="blocked-escalated")
    assert not [r for r in caplog.records if "would-demote" in r.getMessage()]


def test_shadow_mode_is_silent_for_witnessed_verified(
    monkeypatch, caplog
) -> None:
    monkeypatch.setenv(FLAG, "shadow")
    with caplog.at_level(logging.WARNING, logger="g6.core.reliability_envelope"):
        ReliabilityEnvelope(
            completion_state="verified",
            reliability_label="verified",
            task_id="task-1",
            run_id="run-1",
            promotion_witness=_witness(),
        )
    assert not [r for r in caplog.records if "would-demote" in r.getMessage()]


# --------------------------------------------------------------------- #
# ENFORCE: a generator cannot assert 'verified'
# --------------------------------------------------------------------- #
def test_enforce_demotes_bare_default_envelope(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    # #135: a bare envelope now defaults to qualified-draft, so it cannot
    # exercise a DEMOTION. State verified; that is what the law acts on.
    envelope = ReliabilityEnvelope(
        completion_state="verified", reliability_label="verified"
    )
    assert envelope.reliability_label == "qualified-draft"
    assert envelope.completion_state == "qualified-draft"
    assert envelope.degraded is True
    assert envelope.degradation_reason.startswith("kernel promotion law:")
    assert any("G6_KERNEL_PROMOTION_LAW" in c for c in envelope.missing_checks)
    # INV-STATE-2: the demoted envelope surfaces honest limitations.
    assert envelope.known_limitations
    assert envelope.recommended_next_steps


def test_enforce_demotes_envelope_fields_builder(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "enforce")
    fields = envelope_fields(reliability_label="verified")
    assert fields["completion_state"] == "qualified-draft"
    assert fields["reliability_label"] == "qualified-draft"


def test_enforce_accepts_a_valid_evaluator_witness(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    envelope = ReliabilityEnvelope(
        completion_state="verified",
        reliability_label="verified",
        task_id="task-1",
        run_id="run-1",
        promotion_witness=_witness(),
    )
    assert envelope.reliability_label == "verified"
    assert envelope.completion_state == "verified"
    assert envelope.degraded is False


def test_enforce_witnessed_envelope_round_trips(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    envelope = ReliabilityEnvelope(
        completion_state="verified",
        reliability_label="verified",
        task_id="task-1",
        run_id="run-1",
        promotion_witness=_witness(),
    )
    reloaded = ReliabilityEnvelope(**envelope.model_dump())
    assert reloaded.reliability_label == "verified"


def test_enforce_via_builder_passthrough(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    fields = envelope_fields(
        reliability_label="verified",
        task_id="task-1",
        run_id="run-1",
        promotion_witness=_witness(),
    )
    assert fields["completion_state"] == "verified"
    assert fields["promotion_witness"]["content_digest"].startswith("sha256:")


def test_enforce_rejects_non_evaluator_role(monkeypatch) -> None:
    """The generator's own stamp never promotes (S3: a generator structurally
    cannot promote its own theory)."""
    monkeypatch.setenv(FLAG, "1")
    witness = _witness()
    witness["evaluator"] = {"agent_id": "agent:maker", "role": "generator"}
    envelope = ReliabilityEnvelope(
        completion_state="verified",
        reliability_label="verified",
        task_id="task-1",
        run_id="run-1",
        promotion_witness=witness,
    )
    assert envelope.reliability_label == "qualified-draft"
    assert "evaluator" in envelope.degradation_reason


def test_enforce_rejects_doctored_witness(monkeypatch) -> None:
    """A witness with a valid shape but rewritten verdict content must not
    promote (mirrors test_doctored_verdict_rejected_at_the_ledger_door)."""
    monkeypatch.setenv(FLAG, "1")
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


def test_enforce_rejects_witness_without_execution_identity(monkeypatch) -> None:
    """The digest excludes execution provenance (the sealing rule), so
    deleting it isolates the witnessed-execution check: 'did not run' is
    never a promotion."""
    monkeypatch.setenv(FLAG, "1")
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


def test_enforce_rejects_witness_replayed_from_another_run(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    envelope = ReliabilityEnvelope(
        completion_state="verified",
        reliability_label="verified",
        task_id="task-1",
        run_id="run-2",
        promotion_witness=_witness(),  # sealed for run-1
    )
    assert envelope.reliability_label == "qualified-draft"
    assert "never transfers between runs" in envelope.degradation_reason


def test_enforce_law_is_down_only_witness_never_upgrades(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "1")
    for label in ("qualified-draft", "blocked-escalated"):
        envelope = ReliabilityEnvelope(
            reliability_label=label,
            task_id="task-1",
            run_id="run-1",
            promotion_witness=_witness(),
        )
        assert envelope.reliability_label == label


def test_enforce_holds_at_the_revalidation_door(monkeypatch) -> None:
    """Door 2 (the D2S2 parity lesson): a serialized dict claiming 'verified'
    without a witness demotes on re-validation, and a demoted envelope stays
    demoted on round-trip."""
    monkeypatch.setenv(FLAG, "1")
    forged = {
        "completion_state": "verified",
        "reliability_label": "verified",
        "request_id": "req-1",
    }
    envelope = ReliabilityEnvelope.model_validate(forged)
    assert envelope.reliability_label == "qualified-draft"
    reloaded = ReliabilityEnvelope(**envelope.model_dump())
    assert reloaded.reliability_label == "qualified-draft"


# --------------------------------------------------------------------- #
# Witness checker + builder unit behavior
# --------------------------------------------------------------------- #
def test_valid_witness_has_no_faults() -> None:
    assert promotion_witness_faults(_witness(), task_id="task-1", run_id="run-1") == []


def test_missing_witness_is_a_single_honest_fault() -> None:
    faults = promotion_witness_faults(None)
    assert len(faults) == 1
    assert "no promotion witness attached" in faults[0]


def test_witness_missing_independence_flag_is_a_fault() -> None:
    witness = _witness()
    witness["independent_evaluation"] = False
    assert any(
        "independent_evaluation" in f for f in promotion_witness_faults(witness)
    )


def test_unserializable_witness_core_fails_closed() -> None:
    witness = _witness()
    witness["details"] = {"payload": object()}
    assert any(
        "canonically serialized" in f for f in promotion_witness_faults(witness)
    )


def test_builder_rejects_empty_identities() -> None:
    with pytest.raises(ValueError):
        build_promotion_witness(evaluator_id="", execution_id="exec-1")
    with pytest.raises(ValueError):
        build_promotion_witness(evaluator_id="agent:c", execution_id="")


# --------------------------------------------------------------------- #
# Safe-path S4: per-surface enforce scope (G6_PROMOTION_ENFORCE_SURFACES)
#
# The scope narrows `enforce` to a named surface cohort so it can be enabled one
# cohort at a time with instant rollback. Unset ⇒ global (byte-identical). It can
# only NARROW enforce, never widen it.
# --------------------------------------------------------------------- #
SCOPE = "G6_PROMOTION_ENFORCE_SURFACES"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        (" , ", None),
        ("coder", frozenset({"coder"})),
        ("coder, lean_prover", frozenset({"coder", "lean_prover"})),
        # A wildcard anywhere disables scoping (⇒ global enforce).
        ("*", None),
        ("all", None),
        ("__all__", None),
        ("coder,*", None),
        # Case-insensitive, like promotion_law_mode: wildcards + cohort names.
        ("ALL", None),
        ("All", None),
        ("Coder", frozenset({"coder"})),
        ("CODER, Lean_Prover", frozenset({"coder", "lean_prover"})),
        # A disable-word is NOT a wildcard: it becomes a (non-matching) cohort,
        # so enforce degrades to shadow (fail-safe) rather than going global.
        ("none", frozenset({"none"})),
    ],
)
def test_promotion_enforce_scope_parsing(monkeypatch, raw, expected) -> None:
    if raw is None:
        monkeypatch.delenv(SCOPE, raising=False)
    else:
        monkeypatch.setenv(SCOPE, raw)
    assert promotion_enforce_scope() == expected


def test_effective_mode_off_and_shadow_ignore_scope(monkeypatch) -> None:
    """Scoping only touches enforce; off/shadow are global and unchanged."""
    monkeypatch.setenv(SCOPE, "coder")
    monkeypatch.setenv(FLAG, "off")
    assert effective_promotion_law_mode("anything") == "off"
    monkeypatch.setenv(FLAG, "shadow")
    assert effective_promotion_law_mode("anything") == "shadow"
    assert effective_promotion_law_mode(None) == "shadow"


def test_effective_mode_enforce_unscoped_is_global(monkeypatch) -> None:
    monkeypatch.delenv(SCOPE, raising=False)
    monkeypatch.setenv(FLAG, "enforce")
    assert effective_promotion_law_mode("coder") == "enforce"
    assert effective_promotion_law_mode(None) == "enforce"  # core door too


def test_effective_mode_enforce_scoped_narrows(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "coder,lean_prover")
    assert effective_promotion_law_mode("coder") == "enforce"        # in cohort
    assert effective_promotion_law_mode("CODER") == "enforce"        # case-insensitive
    assert effective_promotion_law_mode("lean_prover") == "enforce"  # in cohort
    assert effective_promotion_law_mode("self_model") == "shadow"    # out of cohort
    assert effective_promotion_law_mode(None) == "shadow"            # core door: no id
    assert effective_promotion_law_mode("") == "shadow"              # empty name


def test_effective_mode_wildcard_enforces_globally(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "*")
    assert effective_promotion_law_mode("self_model") == "enforce"
    assert effective_promotion_law_mode(None) == "enforce"


def test_core_door_scoped_out_stays_verified_and_logs(monkeypatch, caplog) -> None:
    """Under a scoped enforce the identity-less core door resolves to shadow: a
    witnessless verified stays verified but the would-demote is logged with the
    enforce_scoped_out marker (the product path is held back, not demoted)."""
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "coder")  # core door is not a named surface
    with caplog.at_level(logging.WARNING, logger="g6.core.reliability_envelope"):
        envelope = ReliabilityEnvelope(
            completion_state="verified",
            reliability_label="verified",
            run_id="run-9",
        )
    assert envelope.reliability_label == "verified"  # NOT demoted (scoped out)
    assert envelope.completion_state == "verified"
    rec = next(r for r in caplog.records if "would-demote" in r.getMessage())
    payload = json.loads(rec.getMessage().split(": ", 1)[1])
    assert payload["enforce_scoped_out"] is True
    assert payload["would_demote_to"] == "qualified-draft"


def test_core_door_unscoped_enforce_still_demotes(monkeypatch) -> None:
    """Darkness: with the scope var unset the S4 logic is inert and the core door
    demotes exactly as pre-S4 global enforce."""
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.delenv(SCOPE, raising=False)
    # #135: see the wildcard test below -- a bare default no longer
    # discriminates, so the envelope must state verified for the law to act.
    envelope = ReliabilityEnvelope(
        completion_state="verified", reliability_label="verified"
    )
    assert envelope.reliability_label == "qualified-draft"
    assert envelope.degradation_reason.startswith("kernel promotion law:")


def test_core_door_wildcard_scope_demotes(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "all")
    # #135: a BARE envelope is now qualified-draft by DEFAULT, so asserting
    # that here would pass with the law deleted. State the claim.
    envelope = ReliabilityEnvelope(
        completion_state="verified", reliability_label="verified"
    )
    assert envelope.reliability_label == "qualified-draft"
    assert envelope.degradation_reason.startswith("kernel promotion law:")


def test_scope_disable_word_is_a_nonmatching_cohort_fail_safe(monkeypatch) -> None:
    """A value like 'none'/'off' is NOT a wildcard: it becomes a cohort name that
    matches no surface, so enforce degrades to shadow (the fail-safe direction).
    To go global, unset the var or use a wildcard (*/all)."""
    monkeypatch.setenv(FLAG, "enforce")
    monkeypatch.setenv(SCOPE, "none")
    assert promotion_enforce_scope() == frozenset({"none"})
    assert effective_promotion_law_mode("coder") == "shadow"
    assert effective_promotion_law_mode(None) == "shadow"
