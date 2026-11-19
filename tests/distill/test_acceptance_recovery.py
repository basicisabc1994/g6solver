"""Incomplete intervals become cost-only records and use a fresh assessment."""
from dataclasses import replace
from contextlib import contextmanager

import pytest

from g6.core.approval_authority import ApprovalAuthority
from g6.distill import acceptance_gate as g
from . import test_acceptance_repair as repair
from .test_acceptance_rulings import admitted, interrupted


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


def test_incomplete_interval_never_authorizes_even_with_issued_approval(sample, monkeypatch):
    journal, close, _, _, entry = interrupted(sample, monkeypatch)
    authority = ApprovalAuthority()
    monkeypatch.setattr(g, "get_approval_authority", lambda: authority)
    record = authority.issue(reviewer_id="Owner", scope=g.reconfirmation_scope(journal, close))
    with pytest.raises(ValueError, match="incomplete"):
        g._confirm_interval(journal, close, owner_grant=record.approval_id)
    assert not g._replacement_authorized(entry)


def test_idle_and_recovery_work_are_charged_before_void(sample, monkeypatch):
    journal, close, frozen, clock, entry = interrupted(sample, monkeypatch)
    original = g._validation_total
    def reconciliation(registration, active):
        clock[0] += 3.
        return original(registration, active)
    with monkeypatch.context() as scoped:
        scoped.setattr(g, "_validation_total", reconciliation)
        g._recover_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60013.
    assert not g._replacement_authorized(entry)
    assert journal.entries()[-1]["kind"] == "registration_voided"


def test_complete_restart_retains_recorded_cost_without_approval(sample, monkeypatch):
    _, frozen, clock, entry = admitted(sample, monkeypatch)
    monkeypatch.setattr(g.time, "time", lambda: clock[0])
    clock[0] = 60000.
    g._RETURNED_CONFIRMATIONS.clear()
    assert g._replacement_authorized(entry)
    assert g._validation_total(frozen.registration_sha256, None) == 10.


def test_recovery_charge_includes_writer_release(sample, monkeypatch):
    journal, close, frozen, clock, entry = interrupted(sample, monkeypatch)
    @contextmanager
    def writer():
        yield
        clock[0] += 4.
    monkeypatch.setattr(g, "_writer", writer)
    g._recover_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60014.
    assert not g._replacement_authorized(entry)


def test_recovery_samples_after_final_boot_check(sample, monkeypatch):
    journal, close, frozen, clock, _ = interrupted(sample, monkeypatch)
    boot = g._boot_identity
    def delayed_boot():
        clock[0] += 3.
        return boot()
    monkeypatch.setattr(g, "_boot_identity", delayed_boot)
    g._recover_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60016.
    row = journal.entries()[-1]["body"]
    assert row["recovery_sample"] == 60006.


def test_recovery_samples_after_obligation_lookup(sample, monkeypatch):
    journal, close, frozen, clock, _ = interrupted(sample, monkeypatch)
    key = g._unrecorded_key
    def delayed_key(registration):
        clock[0] += 3.
        return key(registration)
    with monkeypatch.context() as scoped:
        scoped.setattr(g, "_unrecorded_key", delayed_key)
        g._recover_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60016.


def test_void_after_later_failure_cannot_revive_earlier_confirmation(sample, monkeypatch):
    _, frozen, _, entry = admitted(sample, monkeypatch)
    journal = g._journal()
    close = next(r for r in journal.entries() if r["kind"] == "registration_closed")
    g._append(journal, "registration_close_failed", close["body"])
    g._recover_interval(journal, close["id"])
    assert g._validation_total(frozen.registration_sha256, None) == 10.
    assert not g._replacement_authorized(entry)
    with pytest.raises(ValueError, match="void"):
        g._confirm_interval(journal, close["id"])


def test_void_reassessment_has_new_interval_and_no_extra_held_out_look(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g.time, "time", lambda: clock[0])
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    # Use the real reservation functions; the public rig replaces them for unrelated checks.
    monkeypatch.setattr(g, "_look_available", _REAL_LOOK)
    monkeypatch.setattr(g, "_spend", _REAL_SPEND)
    monkeypatch.setattr(g, "_record", _REAL_RECORD)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    journal = g._journal()
    close = next(r for r in journal.entries() if r["kind"] == "registration_closed")
    g._append(journal, "registration_close_failed", close["body"])
    g._recover_interval(journal, close["id"])
    before = [r for r in journal.entries() if r["kind"] == "reserve"]
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation", answer.reasons
    after = [r for r in journal.entries() if r["kind"] == "reserve"]
    assert len(before) == len(after) == 1
    starts = [r for r in journal.entries() if r["kind"] == "replacement_attempt_start"]
    assert len(starts) == 2 and starts[0]["id"] != starts[1]["id"]
    assert any(r["kind"] == "look_reused" for r in journal.entries())
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    assert len([r for r in journal.entries() if r["kind"] == "reserve"]) == 2


def test_add_reservation_keeps_legacy_path(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    monkeypatch.setattr(g._BOOKKEEPING, "meter", None, raising=False)
    monkeypatch.setattr(g, "_look_available", lambda registration: (journal, 1))
    monkeypatch.setattr(g, "_reusable_look", lambda *args: pytest.fail("add entered replacement recovery"))
    index, reservation = _REAL_SPEND(frozen.registration_sha256, candidate, "legacy-meter")
    assert index == 1
    assert journal.get("reserve", reservation)["meter"] == "legacy-meter"


_REAL_LOOK = g._look_available
_REAL_SPEND = g._spend
_REAL_RECORD = g._record
