"""Assessment accounting and returned authority confirmations."""
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from g6.distill import acceptance_gate as g
from . import test_acceptance_repair as repair
from .test_acceptance_endpoints import active_receipt


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


def entry_from_state(candidate):
    state = [r["body"] for r in g._journal().entries() if r["kind"] == "replacement_state"][-1]
    return SimpleNamespace(entry_id=state["entry_id"], candidate_id=candidate.candidate_id,
        noninferiority=state["pending_receipt"], teacher=state["report"]["replacement"]["teacher"],
        replacement_scope=[], replacement="R", authority="A1", status="probation")


@pytest.mark.parametrize("stage", ["setup", "acquisition", "replacement_attempt", "registration_closed",
                                  "writer", "release", "post_read", "active_receipt", "deadline"])
@pytest.mark.parametrize("delay", [2., 20000.])
def test_bookkeeping_is_diagnostic_and_deadline_bounded(sample, monkeypatch, stage, delay):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    journal = g._interval_journal
    append = g._append
    reconcile = g._validation_total
    calls = [0]
    read_delayed = [False]
    released = [False]
    deadline = g._append_deadline
    deadline_delayed = [False]
    def registered_deadline(registration):
        if stage == "deadline" and not deadline_delayed[0]:
            deadline_delayed[0] = True
            clock[0] += delay
        return deadline(registration)

    @contextmanager
    def writer():
        if stage == "writer":
            clock[0] += delay
        try:
            yield
        finally:
            released[0] = True
            if stage == "release":
                clock[0] += delay

    def read(registration, active):
        if stage == "post_read" and not read_delayed[0] and any(
                r["kind"] == "registration_meter_closed" for r in g._journal().entries()):
            read_delayed[0] = True
            clock[0] += delay
        return reconcile(registration, active)

    def acquire(ident=None):
        calls[0] += 1
        if (stage == "setup" and calls[0] == 1) or (stage == "acquisition" and calls[0] == 3):
            clock[0] += delay
        return journal(ident)

    def write(journal, kind, body):
        result = append(journal, kind, body)
        if kind == stage or (stage == "active_receipt" and kind == "replacement_state" and body["state"] == "active"):
            clock[0] += delay
        return result

    monkeypatch.setattr(g, "_interval_journal", acquire)
    monkeypatch.setattr(g, "_append", write)
    monkeypatch.setattr(g, "_writer", writer)
    monkeypatch.setattr(g, "_validation_total", read)
    monkeypatch.setattr(g, "_append_deadline", registered_deadline)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    if stage == "writer":
        assert released[0]
    if delay == 2.:
        assert answer.verdict == "admit_probation", answer.reasons
        assert g._validation_total(frozen.registration_sha256, None) == 10.
    else:
        assert answer.verdict == "block", answer.reasons
        with pytest.raises(ValueError, match="cost recovery"):
            g._validation_total(frozen.registration_sha256, None)
    diagnostics = [d for r in g._journal().entries() for d in r["body"].get("bookkeeping", [])]
    assert any(d["latency_s"] == delay for d in diagnostics)


@pytest.mark.parametrize("kind", ["registration_closed", "authority_confirmed"])
def test_landed_slow_publication_with_failed_markers_never_authorizes(sample, monkeypatch, kind):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    append = g._append
    observed = []

    def delayed(journal, event, body):
        if event in {"registration_close_failed", "registration_meter_failure"}:
            raise OSError("failure marker unavailable")
        result = append(journal, event, body)
        if event == kind:
            observed.append(g._replacement_authorized(entry_from_state(candidate)))
            clock[0] += 20000.
        return result

    monkeypatch.setattr(g, "_append", delayed)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "block"
    assert observed == [False]
    assert not g._replacement_authorized(entry_from_state(candidate))


def test_close_alone_has_no_authority(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "monotonic_start": 0., "wall_start": 0.}
    journal = g._journal()
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt)
    g._append(journal, "registration_closed", opening | {"attempt": attempt, "validation_s": 0.,
        "monotonic_end": 0., "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]})
    assert not g._replacement_authorized(entry)
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


def test_setup_error_requires_recovery_before_retry(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    acquire = g._interval_journal
    calls = [0]
    def interrupted(ident=None):
        calls[0] += 1
        if calls[0] == 1:
            clock[0] += 20000.
            raise OSError("setup unavailable")
        return acquire(ident)
    monkeypatch.setattr(g, "_interval_journal", interrupted)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"
    assert any(d["failed"] and d["latency_s"] == 20000. for row in g._journal().entries()
               for d in row["body"].get("bookkeeping", []))


def test_unrecorded_setup_failure_withholds_existing_authority_and_retry(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    entry = entry_from_state(candidate)
    assert g._replacement_authorized(entry)
    acquire = g._interval_journal
    def unavailable(ident=None):
        clock[0] += 20000.
        raise OSError("all interval storage unavailable")
    monkeypatch.setattr(g, "_interval_journal", unavailable)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"
    monkeypatch.setattr(g, "_interval_journal", acquire)
    assert not g._replacement_authorized(entry)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"
    journal = g._journal()
    opening = g._UNRECORDED_BOOKKEEPING[g._unrecorded_key(frozen.registration_sha256)]
    attempt = g._append(journal, "registration_open_failed", opening)
    close = g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "recovery_of": attempt, "validation_s": 40000., "monotonic_end": 40000.,
        "candidate_ids": [], "activation_receipts": []})
    g._confirm_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 40020.


def test_unavailable_registered_deadline_blocks(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    def unavailable(registration):
        raise ValueError("registered endpoint deadline unavailable")
    monkeypatch.setattr(g, "_append_deadline", unavailable)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"


def test_cold_reader_needs_owner_recovery_and_returned_reconfirmation(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    entry = entry_from_state(candidate)
    assert g._replacement_authorized(entry)
    monkeypatch.setattr(g, "_RETURNED_CONFIRMATIONS", {})
    assert not g._replacement_authorized(entry)
    journal = g._journal()
    body = next(r["body"] for r in journal.entries() if r["kind"] == "registration_closed")
    failure = g._append(journal, "registration_close_failed", body)
    close = g._append(journal, "registration_closed", body | {"recovery_of": failure})
    assert not g._replacement_authorized(entry)
    g._confirm_interval(journal, close)
    assert g._replacement_authorized(entry)


def test_recovery_confirmation_return_is_deadline_checked(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "monotonic_start": 0., "wall_start": 0.}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt)
    close = g._append(journal, "registration_closed", opening | {"attempt": attempt, "validation_s": 0.,
        "monotonic_end": 0., "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]})
    append = g._append
    def delayed(journal, kind, body):
        if kind == "registration_close_failed":
            raise OSError("marker unavailable")
        result = append(journal, kind, body)
        if kind == "authority_confirmed":
            clock[0] += 20000.
        return result
    monkeypatch.setattr(g, "_append", delayed)
    with pytest.raises(ValueError, match="cost recovery"):
        g._confirm_interval(journal, close)
    assert not g._replacement_authorized(entry)


def test_recovery_recomputes_continuous_sixty_thousand_seconds(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "monotonic_start": 0., "wall_start": 0.}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt, upper=-50000.)
    failure = g._append(journal, "registration_close_failed", opening | {"attempt": attempt,
        "validation_s": 40000., "monotonic_end": 40000.})
    clock[0] = 60000.
    close = g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "recovery_of": failure, "validation_s": 40000., "monotonic_end": 40000.,
        "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]})
    # A recovery close carries its own continuously sampled floor before confirmation.
    row = next(r for r in journal.entries() if r["id"] == close)
    assert row["body"].get("recovery_elapsed_s", row["body"]["validation_s"]) == 60000.
    g._confirm_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60010.
    assert not g._replacement_authorized(entry)


def test_close_and_two_slow_markers_cannot_lose_terminal_twenty_thousand_seconds(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "monotonic_start": 0., "wall_start": 0.}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt, upper=-50000.)
    body = opening | {"attempt": attempt, "validation_s": 0., "monotonic_end": 0.,
        "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]}
    append = g._append
    def slow(journal, kind, body):
        result = append(journal, kind, body)
        if kind in {"registration_closed", "registration_close_failed"}:
            clock[0] += 20000.
        return result
    monkeypatch.setattr(g, "_append", slow)
    with pytest.raises(ValueError, match="cost recovery"):
        g._endpoint_append(journal, "registration_closed", body)
    g._interval_failure("synthetic", opening, attempt)
    assert clock[0] == 60000.
    failed = journal.entries()[-1]
    assert failed["body"]["validation_s"] == 40000.
    monkeypatch.setattr(g, "_append", append)
    close = g._append(journal, "registration_closed", body | {"recovery_of": failed["id"],
        "validation_s": 40000., "monotonic_end": 40000.})
    g._confirm_interval(journal, close)
    assert g._validation_total(frozen.registration_sha256, None) == 60010.
    assert not g._replacement_authorized(entry)


_REAL_MEASURE = g._measure


def test_shared_overrun_rule_collects_three_hundred_runs_before_scatter(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_measure", _REAL_MEASURE)
    entry = SimpleNamespace(replacement="R", replacement_scope=[], candidate_id=candidate.candidate_id)
    monkeypatch.setattr(g, "_replacement_authorized", lambda entry: True)
    monkeypatch.setattr(g, "_candidate", lambda entry: candidate)
    excluded = frozen.acceptance[0]
    monkeypatch.setattr(g, "_case_in_scope", lambda scope, case: case is not excluded.case)
    calls = [0]

    def run(*args):
        calls[0] += 1
        if calls[0] == 1:
            clock[0] += candidate.contract.cost_bound.wall_s + 1.
        return "concern"

    monkeypatch.setattr(g, "_run", run)
    verdicts, costs = g._measure_entry(entry, frozen.acceptance[:101], [entry])
    assert calls[0] == 300
    assert verdicts == ["abstain"] + ["concern"] * 100
    assert costs[:3] == [0., 0., 0.]
    assert sum(c > candidate.contract.cost_bound.wall_s for c in costs) == 1
