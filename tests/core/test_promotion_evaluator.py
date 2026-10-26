# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""K-P0-4 structural seam: the evaluator authority makes a promotion witness
unforgeable by the generator. With a key configured, only an evaluator-stamped
witness promotes; a self-minted one (structurally perfect but unsigned) demotes.
With no key configured, behavior is byte-identical to the structural-only law.
"""
from __future__ import annotations

import pytest
from g6.core import promotion_evaluator
from g6.core.reliability_envelope import (
    build_promotion_witness,
    envelope_fields,
    promotion_witness_faults,
)

_KEY = "test-evaluator-secret-key-abc123"


def _witness(**overrides):
    kwargs = {
        "evaluator_id": "lean-kernel-reverify",
        "execution_id": "kernel-hash-xyz",
        "oracle": "lean_kernel_reverify",
        "verdict": "pass",
    }
    kwargs.update(overrides)
    return build_promotion_witness(**kwargs)


@pytest.fixture(autouse=True)
def _clean_oracle_registry():
    """Snapshot/restore the process-global replay registry so registering a fake
    oracle in one test never leaks into another."""
    saved = dict(promotion_evaluator._ORACLE_REPLAYS)
    yield
    promotion_evaluator._ORACLE_REPLAYS.clear()
    promotion_evaluator._ORACLE_REPLAYS.update(saved)


def _reproducing(execution_id):
    """A replay that always reproduces, observing ``execution_id``."""
    def _fn(_witness_arg):
        return promotion_evaluator.ReplayOutcome(True, execution_id=execution_id)
    return _fn


# ── authority configuration ────────────────────────────────────────────────
def test_no_key_configured_is_the_default(key_file):
    key_file("")
    assert promotion_evaluator.evaluator_configured() is False
    assert promotion_evaluator.evaluator_key_id() == ""
    # verify is always False without an authority (the law only treats that as a
    # fault when an authority IS configured).
    assert promotion_evaluator.verify(_witness()) is False


def test_blank_key_is_treated_as_unconfigured(key_file):
    key_file("G6_PROMOTION_EVALUATOR_KEY=   \n")
    assert promotion_evaluator.evaluator_configured() is False


def test_a_key_in_the_environment_is_not_the_evaluator_key(monkeypatch, key_file):
    # keys come only from the person's key file (owner ruling 2026-10-21); the environment is never read
    key_file("")
    monkeypatch.setenv("G6_PROMOTION_EVALUATOR_KEY", _KEY)
    assert promotion_evaluator.evaluator_configured() is False
    with pytest.raises(RuntimeError, match=r"G6_PROMOTION_EVALUATOR_KEY is not in .*\.env"):
        promotion_evaluator.sign("d", "e")


def test_key_id_is_non_secret_stable_and_rotates(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    kid = promotion_evaluator.evaluator_key_id()
    assert kid.startswith("evk:") and _KEY not in kid
    assert promotion_evaluator.evaluator_key_id() == kid  # stable
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY + '-rotated'}\n")
    assert promotion_evaluator.evaluator_key_id() != kid  # rotates


# ── sign / stamp / verify ──────────────────────────────────────────────────
def test_sign_is_deterministic(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    a = promotion_evaluator.sign("sha256:abc", "exec-1")
    b = promotion_evaluator.sign("sha256:abc", "exec-1")
    assert a == b and a.startswith("hmac-sha256:")
    assert promotion_evaluator.sign("sha256:abc", "exec-2") != a  # binds execution


def test_sign_and_stamp_require_an_authority(key_file):
    key_file("")
    with pytest.raises(RuntimeError):
        promotion_evaluator.sign("sha256:abc", "exec-1")
    with pytest.raises(RuntimeError):
        promotion_evaluator.stamp(_witness())


def test_stamp_produces_a_verifiable_witness(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    signed = promotion_evaluator.stamp(_witness())
    assert signed["evaluator_signature"].startswith("hmac-sha256:")
    assert signed["evaluator_key_id"] == promotion_evaluator.evaluator_key_id()
    assert promotion_evaluator.verify(signed) is True


def test_unsigned_witness_does_not_verify(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    assert promotion_evaluator.verify(_witness()) is False


def test_forged_signature_does_not_verify(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    forged = dict(_witness())
    forged["evaluator_signature"] = "hmac-sha256:" + "0" * 64
    assert promotion_evaluator.verify(forged) is False


def test_non_ascii_signature_fails_closed_not_crashes(monkeypatch, key_file):
    """A non-ASCII signature must be a clean False (fail closed), never a
    TypeError out of hmac.compare_digest that would crash envelope construction
    instead of demoting."""
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    bad = dict(_witness())
    bad["evaluator_signature"] = "hmac-sha256:ÿ☃中"  # non-ASCII
    assert promotion_evaluator.verify(bad) is False
    # end-to-end under enforce: the law demotes cleanly, does not raise.
    monkeypatch.setenv("G6_KERNEL_PROMOTION_LAW", "enforce")
    env = envelope_fields(reliability_label="verified", promotion_witness=bad)
    assert env["completion_state"] == "qualified-draft"


def test_signature_binds_execution_id_beyond_the_digest(key_file):
    """content_digest excludes execution; the signature binds execution_id, so
    swapping the witnessed execution after signing invalidates the signature
    even though the content_digest still matches its core."""
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    signed = promotion_evaluator.stamp(_witness(execution_id="exec-A"))
    swapped = dict(signed)
    swapped["execution"] = {"execution_id": "exec-B"}
    assert promotion_evaluator.verify(swapped) is False


def test_witness_signed_by_another_key_does_not_verify(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    signed = promotion_evaluator.stamp(_witness())
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY + '-other'}\n")
    assert promotion_evaluator.verify(signed) is False


# ── law integration: promotion_witness_faults ──────────────────────────────
def test_no_authority_keeps_structural_witness_valid(key_file):
    """Non-regression: without an authority, a structurally-valid (unsigned)
    witness has zero faults, exactly as before this module existed."""
    key_file("")
    assert promotion_witness_faults(_witness()) == []


def test_authority_requires_a_signature(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    # self-minted (unsigned) → the new signature fault fires
    faults = promotion_witness_faults(_witness())
    assert any("not signed by the evaluator authority" in f for f in faults)
    # evaluator-stamped → no faults
    assert promotion_witness_faults(promotion_evaluator.stamp(_witness())) == []


# ── law integration: end-to-end through the envelope under enforce ──────────
def test_enforce_with_authority_demotes_self_minted_promotes_stamped(monkeypatch, key_file):
    monkeypatch.setenv("G6_KERNEL_PROMOTION_LAW", "enforce")
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")

    # self-minted (unsigned) witness → demotes, even though it is structurally
    # perfect: the generator cannot promote itself past the evaluator authority.
    self_minted = envelope_fields(
        reliability_label="verified", promotion_witness=_witness()
    )
    assert self_minted["completion_state"] == "qualified-draft"

    # evaluator-stamped witness → survives enforce (earned).
    stamped = envelope_fields(
        reliability_label="verified",
        promotion_witness=promotion_evaluator.stamp(_witness()),
    )
    assert stamped["completion_state"] == "verified"


def test_enforce_without_authority_is_unchanged(monkeypatch, key_file):
    """With no evaluator key, the structural law stands alone: a structurally
    valid witness still promotes under enforce (no signature required)."""
    monkeypatch.setenv("G6_KERNEL_PROMOTION_LAW", "enforce")
    key_file("")
    env = envelope_fields(reliability_label="verified", promotion_witness=_witness())
    assert env["completion_state"] == "verified"


def test_law_off_is_byte_identical_regardless_of_authority(monkeypatch, key_file):
    """Flag OFF: the law never runs, so an evaluator key changes nothing and a
    plain verified envelope dumps without a promotion_witness key."""
    monkeypatch.setenv("G6_KERNEL_PROMOTION_LAW", "off")
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    env = envelope_fields(reliability_label="verified")
    assert env["completion_state"] == "verified"
    assert "promotion_witness" not in env


# ── independent-replay layer: holding the key is not enough ─────────────────
def test_registry_registers_and_reports(monkeypatch):
    assert promotion_evaluator.oracle_registered("fake_oracle") is False
    promotion_evaluator.register_oracle("fake_oracle", _reproducing("exec-1"))
    assert promotion_evaluator.oracle_registered("fake_oracle") is True


def test_register_oracle_rejects_bad_input():
    with pytest.raises(ValueError):
        promotion_evaluator.register_oracle("", _reproducing("x"))
    with pytest.raises(TypeError):
        promotion_evaluator.register_oracle("x", "not-callable")


def test_stamp_replayed_signs_when_oracle_reproduces(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    promotion_evaluator.register_oracle("fake_oracle", _reproducing("exec-1"))
    signed = promotion_evaluator.stamp_replayed(
        _witness(oracle="fake_oracle", execution_id="exec-1")
    )
    # a replayed stamp is a normal evaluator signature — the law accepts it.
    assert promotion_evaluator.verify(signed) is True


def test_stamp_replayed_refuses_when_oracle_does_not_reproduce(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")

    def _no(_w):
        return promotion_evaluator.ReplayOutcome(False, detail="verdict differs")

    promotion_evaluator.register_oracle("fake_oracle", _no)
    with pytest.raises(ValueError):
        promotion_evaluator.stamp_replayed(
            _witness(oracle="fake_oracle", execution_id="exec-1")
        )


def test_stamp_replayed_refuses_on_identity_mismatch(key_file):
    """Holding the key AND a reproducing oracle is still not enough when the
    replay reproduced a DIFFERENT artifact than the witness claims."""
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    promotion_evaluator.register_oracle("fake_oracle", _reproducing("exec-OBSERVED"))
    outcome = promotion_evaluator.replay_reproduces(
        _witness(oracle="fake_oracle", execution_id="exec-CLAIMED")
    )
    assert outcome.reproduced is False and "does not match" in outcome.detail
    with pytest.raises(ValueError):
        promotion_evaluator.stamp_replayed(
            _witness(oracle="fake_oracle", execution_id="exec-CLAIMED")
        )


def test_stamp_replayed_refuses_unregistered_oracle(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    outcome = promotion_evaluator.replay_reproduces(
        _witness(oracle="unregistered", execution_id="exec-1")
    )
    assert outcome.reproduced is False and "no independent replay" in outcome.detail
    with pytest.raises(ValueError):
        promotion_evaluator.stamp_replayed(
            _witness(oracle="unregistered", execution_id="exec-1")
        )


def test_replay_fails_closed_when_replay_raises(key_file):
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")

    def _boom(_w):
        raise RuntimeError("toolchain unavailable")

    promotion_evaluator.register_oracle("boom", _boom)
    outcome = promotion_evaluator.replay_reproduces(
        _witness(oracle="boom", execution_id="exec-1")
    )
    assert outcome.reproduced is False and "raised" in outcome.detail


def test_replay_requires_reproduced_is_exactly_true(key_file):
    """A stray truthy value (1, 'yes') from a buggy replay must not slip past the
    gate: only ``reproduced is True`` reproduces."""
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")

    def _truthy(_w):
        return promotion_evaluator.ReplayOutcome(1, execution_id="exec-1")  # type: ignore[arg-type]

    promotion_evaluator.register_oracle("truthy", _truthy)
    outcome = promotion_evaluator.replay_reproduces(
        _witness(oracle="truthy", execution_id="exec-1")
    )
    assert outcome.reproduced is False and "non-bool" in outcome.detail
    with pytest.raises(ValueError):
        promotion_evaluator.stamp_replayed(
            _witness(oracle="truthy", execution_id="exec-1")
        )


def test_stamp_replayed_requires_authority(key_file):
    """No point replaying if we cannot sign: with no key, stamp_replayed raises
    RuntimeError before doing any work."""
    key_file("")
    promotion_evaluator.register_oracle("fake_oracle", _reproducing("exec-1"))
    with pytest.raises(RuntimeError):
        promotion_evaluator.stamp_replayed(
            _witness(oracle="fake_oracle", execution_id="exec-1")
        )


def test_raw_stamp_bypasses_replay_by_design(key_file):
    """Documented honest scope: in-process, raw ``stamp`` still signs WITHOUT a
    replay. The replay gate is on the ``stamp_replayed`` mint path, not the
    signature bytes; the bypass closes only when the key moves out of process.
    This test pins that KNOWN limitation so a future reader sees it is intended."""
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    # No replay registered; this oracle would never reproduce here — yet raw
    # stamp signs and verify accepts it.
    signed = promotion_evaluator.stamp(_witness(oracle="fake_oracle"))
    assert promotion_evaluator.verify(signed) is True


def test_replayed_witness_survives_enforce_end_to_end(monkeypatch, key_file):
    """End-to-end: a witness minted via the replay path promotes under the law's
    enforce mode exactly like any evaluator-signed witness."""
    monkeypatch.setenv("G6_KERNEL_PROMOTION_LAW", "enforce")
    key_file(f"G6_PROMOTION_EVALUATOR_KEY={_KEY}\n")
    promotion_evaluator.register_oracle("fake_oracle", _reproducing("exec-1"))
    signed = promotion_evaluator.stamp_replayed(
        _witness(oracle="fake_oracle", execution_id="exec-1")
    )
    env = envelope_fields(reliability_label="verified", promotion_witness=signed)
    assert env["completion_state"] == "verified"
