"""Owner rulings for durable authority, recovery floors and elapsed cost."""
import inspect
from dataclasses import replace

import pytest

from g6.core.approval_authority import ApprovalAuthority
from g6.core.ledger import ChainLedger
from g6.distill import acceptance_gate as g
from . import test_acceptance_repair as repair
from .test_acceptance_confirmation import entry_from_state, owner_recovery
from .test_acceptance_endpoints import active_receipt


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


def admitted(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation", answer.reasons
    return candidate, frozen, clock, entry_from_state(candidate)


def interrupted(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g.time, "time", lambda: clock[0])
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
               "phase": "assessment", "monotonic_start": 0., "wall_start": 0., "boot_id": g._boot_identity()}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt)
    failure = g._append(journal, "registration_close_failed", opening | {
        "attempt": attempt, "validation_s": 4., "monotonic_end": 4.})
    clock[0] = 60000.
    close = g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "recovery_of": failure, "validation_s": 4., "monotonic_end": 4.,
        "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]})
    return journal, close, frozen, clock, entry


def human_grant(monkeypatch, journal, close, **kwargs):
    authority = ApprovalAuthority()
    monkeypatch.setattr(g, "get_approval_authority", lambda: authority)
    return authority.issue(reviewer_id="Fixture owner", scope=g.reconfirmation_scope(journal, close), **kwargs).approval_id


def test_restart_after_durable_confirmation_keeps_authority(sample, monkeypatch):
    _, frozen, _, entry = admitted(sample, monkeypatch)
    assert any(r["kind"] == "confirmation_observed" for r in g._journal().entries())
    monkeypatch.setattr(g, "_RETURNED_CONFIRMATIONS", {})
    monkeypatch.setattr(g, "_BOOKKEEPING_DIAGNOSTICS", {})
    owned = g._owned_rows(g._journal())
    confirmation = next(r for r in owned if r["kind"] == "authority_confirmed")
    assert g._returned_confirmation(confirmation, owned), owned[-2:]
    assert g._replacement_authorized(entry)
    assert g._validation_total(frozen.registration_sha256, None) == 10.


@pytest.mark.parametrize("damage", ["missing", "digest", "close", "opening", "journal", "chain", "foreign_confirmation"])
def test_missing_or_foreign_marker_grants_no_authority(sample, monkeypatch, damage):
    _, frozen, _, entry = admitted(sample, monkeypatch)
    journal = g._journal()
    rows = journal.entries()
    marker = next(r for r in rows if r["kind"] == "confirmation_observed")
    if damage == "missing":
        rows = [r for r in rows if r is not marker]
    else:
        fields = {"digest": "confirmation_digest", "close": "close", "opening": "attempt",
                  "journal": "journal_identity", "chain": "interval_chain"}
        if damage == "foreign_confirmation":
            confirmation = next(r for r in rows if r["kind"] == "authority_confirmed")
            confirmation["body"] = confirmation["body"] | {"confirmation_nonce": "foreign"}
        else:
            marker["body"] = marker["body"] | {fields[damage]: "foreign"}
    monkeypatch.setattr(journal, "entries", lambda: rows)
    assert not g._replacement_authorized(entry)
    with pytest.raises(ValueError, match="recovery"):
        g._validation_total(frozen.registration_sha256, None)


@pytest.mark.parametrize("failure", ["delay", "raise"])
def test_marker_preparation_failure_leaves_no_marker_or_authority(sample, monkeypatch, failure):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    append = g._append
    def interrupted_marker(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "confirmation_observed":
            if failure == "raise":
                raise OSError("marker interrupted")
            clock[0] += 6.
        return result
    monkeypatch.setattr(g, "_append", interrupted_marker)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"
    assert not any(r["kind"] == "confirmation_observed" for r in g._journal().entries())
    assert not g._replacement_authorized(entry_from_state(candidate))


def test_recovery_after_sixty_thousand_idle_seconds_charges_elapsed(sample, monkeypatch):
    journal, close, frozen, _, entry = interrupted(sample, monkeypatch)
    g._recover_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60010.
    assert not g._replacement_authorized(entry)


def test_no_detached_signature_path_remains():
    source = inspect.getsource(g)
    assert "owner_reconfirmation_public_key" not in source
    assert "cryptography" not in source
    assert "reconfirmation_payload" not in source


def test_incomplete_interval_is_voided_instead_of_reconfirmed(sample, monkeypatch):
    journal, close, frozen, _, entry = interrupted(sample, monkeypatch)
    before = journal.entries()
    with pytest.raises(ValueError, match="owner"):
        g._confirm_interval(journal, close)
    assert journal.entries() == before
    grant = human_grant(monkeypatch, journal, close)
    with pytest.raises(ValueError, match="incomplete"):
        g._confirm_interval(journal, close, owner_grant=grant)
    g._recover_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60010.
    assert not g._replacement_authorized(entry)


@pytest.mark.parametrize("wall,mono,allowed", [(60000., 60000., True), (60000., 1., False),
    (1., -1., False), (102., 100., True), (103., 100., False), (1005., 1000., True)])
def test_clock_comparison_establishes_boot_continuity(monkeypatch, wall, mono, allowed):
    monkeypatch.setattr(g.sys, "platform", "win32")
    monkeypatch.setattr(g, "_boot_identity", lambda: None)
    monkeypatch.setattr(g.time, "time", lambda: wall)
    monkeypatch.setattr(g.time, "monotonic", lambda: mono)
    opening = {"monotonic_start": 0., "wall_start": 0., "boot_id": None}
    if allowed:
        assert g._interval_elapsed(opening, recovery=True) == mono
    else:
        with pytest.raises(ValueError, match="owner"):
            g._interval_elapsed(opening, recovery=True)


def test_clock_disagreement_recovery_requires_approval(sample, monkeypatch):
    journal, close, frozen, clock, entry = interrupted(sample, monkeypatch)
    clock[0] = 5.
    monkeypatch.setattr(g.time, "time", lambda: 60000.)
    with pytest.raises(ValueError, match="owner"):
        g._recover_interval(journal, close)
    result = owner_recovery(monkeypatch, journal, close, 60010.)
    assert result["state"] == "voided", result
    assert g._validation_total(frozen.registration_sha256, None) == 60010.
    assert not g._replacement_authorized(entry)


def test_bounded_bookkeeping_is_charged_from_opening_to_final_sample(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    append = g._append
    def delayed(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "replacement_attempt":
            clock[0] += 2.
        return result
    monkeypatch.setattr(g, "_append", delayed)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    assert g._validation_total(frozen.registration_sha256, None) == 12.


def test_disk_restart_durable_confirmation_keeps_authority(sample, monkeypatch, tmp_path):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g.time, "time", lambda: clock[0])
    journal = ChainLedger(tmp_path / "interval.jsonl")
    monkeypatch.setattr(g, "_journal", lambda: journal)
    monkeypatch.setattr(g, "_interval_journal", lambda ident=None: journal)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    entry = entry_from_state(candidate)
    journal = ChainLedger(journal.path)
    monkeypatch.setattr(g, "_RETURNED_CONFIRMATIONS", {})
    assert g._replacement_authorized(entry)
