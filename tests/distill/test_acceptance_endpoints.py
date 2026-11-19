"""Endpoint deadlines, chain ownership, and recovery cost floors."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from g6.distill import acceptance_gate as g
from . import test_acceptance_repair as repair


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


def active_receipt(journal, candidate, registration, attempt, upper=-1e9):
    entry = SimpleNamespace(entry_id="e" * 64, candidate_id=candidate.candidate_id,
        noninferiority="f" * 64, teacher="c" * 64, replacement_scope=[],
        replacement="R", authority="A1", status="probation")
    receipt = g._append(journal, "replacement_state", {"state": "active", "entry_id": entry.entry_id,
        "candidate_id": entry.candidate_id, "attempt": attempt, "pending_receipt": entry.noninferiority,
        "report": {"registration": registration, "candidate_id": entry.candidate_id,
            "replacement": {"state": "active", "teacher": entry.teacher, "scope": [],
                            "cpu_weight": 1., "validation_s": 0., "cost": {"upper_95": upper}}}})
    return entry, receipt


@pytest.mark.parametrize("stage", ["opening_error", "opening_slow", "close_slow"])
def test_endpoint_overrun_has_no_authority_and_retry_blocks(sample, monkeypatch, stage):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *args, **kwargs: replace(answer, entry_id="e" * 64))
    append = g._append
    delayed = [False]

    def write(journal, kind, body):
        target = "registration_closed" if stage == "close_slow" else "replacement_attempt_start"
        if kind == target and not delayed[0]:
            delayed[0] = True
            clock[0] += 20000.
            if stage == "opening_error":
                raise OSError("opening write interrupted")
        return append(journal, kind, body)

    monkeypatch.setattr(g, "_append", write)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict in {"block", "refuse"}
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)
    retry = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert retry.verdict in {"block", "refuse"}
    assert "cost recovery" in " ".join(retry.reasons)
    assert not g._active(g.Library().list())
    states = [r["body"] for r in g._journal().entries() if r["kind"] == "replacement_state"]
    if states:
        state = states[-1]
        entry = SimpleNamespace(entry_id=state["entry_id"], candidate_id=candidate.candidate_id,
            noninferiority=state["pending_receipt"], teacher=state["report"]["replacement"]["teacher"],
            replacement_scope=[], replacement="R", authority="A1", status="probation")
        assert not g._replacement_authorized(entry)


def test_endpoint_allowance_charged_once_per_interval(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    assert g._validation_total(frozen.registration_sha256, None) == 10.
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    assert g._validation_total(frozen.registration_sha256, None) == 20.


def test_failed_opening_row_cannot_lose_its_recorded_cost(sample, monkeypatch):
    _, frozen, _ = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "boot_id": g._boot_identity(), "monotonic_start": 0., "wall_start": 0.,
        "validation_s": 20000., "monotonic_end": 20000.}
    attempt = g._append(journal, "registration_open_failed", opening)
    g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "recovery_of": attempt, "validation_s": 0., "monotonic_end": 0.,
        "candidate_ids": [], "activation_receipts": []})
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


def test_opening_failure_requires_its_own_recovery_reference(sample, monkeypatch):
    _, frozen, _ = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "monotonic_start": 0., "wall_start": 0.,
        "validation_s": 7., "monotonic_end": 7.}
    attempt = g._append(journal, "registration_open_failed", opening)
    g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "candidate_ids": [], "activation_receipts": []})
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


def test_opening_deadline_is_bound_to_registration_data(sample, monkeypatch):
    _, frozen, _ = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "monotonic_start": 0., "wall_start": 0., "d_append_s": .01}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "validation_s": 0., "monotonic_end": 0., "candidate_ids": [], "activation_receipts": []})
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


def test_registered_deadline_controls_allowance_and_write_limit(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    policy, data = g._policy()
    data = data | {"registrations": {frozen.registration_sha256: {"d_append_s": 2.}}}
    monkeypatch.setattr(g, "_policy", lambda: (policy, data))
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    assert g._validation_total(frozen.registration_sha256, None) == 4.
    states = [r["body"] for r in g._journal().entries() if r["kind"] == "replacement_state"]
    assert states[-1]["report"]["replacement"]["validation_s"] == 4.
    append = g._append

    def slow(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "registration_closed":
            clock[0] += 3.
        return result

    monkeypatch.setattr(g, "_append", slow)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


@pytest.mark.parametrize("raises", [False, True])
def test_recovery_write_overrun_leaves_interval_incomplete(sample, monkeypatch, raises):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
        "phase": "assessment", "boot_id": g._boot_identity(), "monotonic_start": 0., "wall_start": 0.}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt)
    failure = g._append(journal, "registration_close_failed", opening | {
        "attempt": attempt, "validation_s": 0., "monotonic_end": 0.})
    append = journal.append

    def delayed(kind, ident, body):
        append(kind, ident, body)
        if kind == "registration_closed":
            clock[0] += 20000.
            if raises:
                raise OSError("recovery write interrupted after row")

    monkeypatch.setattr(journal, "append", delayed)
    with pytest.raises((OSError, ValueError)):
        g._append(journal, "registration_closed", opening | {"attempt": attempt,
            "recovery_of": failure, "validation_s": 0., "monotonic_end": 0.,
            "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]})
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)
    assert not g._replacement_authorized(entry)
    assert max(r["body"]["validation_s"] for r in journal.entries()
               if r["kind"] == "registration_close_failed") >= 20000.


@pytest.mark.parametrize("elapsed,end", [(0., 0.), (20000., 0.)])
def test_recovery_cannot_reduce_recorded_cost_or_end(sample, monkeypatch, elapsed, end):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
               "phase": "assessment", "boot_id": g._boot_identity(), "monotonic_start": 0., "wall_start": 0.}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt)
    failure = g._append(journal, "registration_close_failed", opening | {
        "attempt": attempt, "validation_s": 20000., "monotonic_end": 20000.})
    g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "recovery_of": failure, "validation_s": elapsed, "monotonic_end": end,
        "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]})
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)
    assert not g._replacement_authorized(entry)
    assert g._active([entry]) == []


@pytest.mark.parametrize("kind", ["registration_closed", "registration_close_failed", "recovery"])
def test_cross_chain_endpoint_never_closes_opening(sample, monkeypatch, tmp_path, kind):
    _, frozen, _ = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_interval_journal", lambda ident=None:
        g.ChainLedger(tmp_path / (ident + ".jsonl")) if ident else tmp_path)
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
               "phase": "assessment", "boot_id": g._boot_identity(), "monotonic_start": 0., "wall_start": 0.}
    a, b = g._interval_journal("a"), g._interval_journal("b")
    attempt = g._append(a, "replacement_attempt_start", opening)
    other = g._append(b, "replacement_attempt_start", opening | {"wall_start": 1.})
    body = opening | {"attempt": attempt, "validation_s": 0., "monotonic_end": 0.,
                      "candidate_ids": [], "activation_receipts": []}
    g._append(b, "registration_closed", body | {"attempt": other})
    if kind == "recovery":
        failure = g._append(a, "registration_close_failed", body)
        g._append(b, "registration_closed", body | {"recovery_of": failure})
    else:
        if kind == "registration_close_failed":
            g._append(a, "registration_closed", body)
            failure = g._append(b, kind, body)
            g._append(a, "registration_closed", body | {"recovery_of": failure})
        else:
            g._append(b, kind, body)
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


@pytest.mark.parametrize("kind", ["registration_closed", "registration_close_failed", "recovery"])
def test_chain_ownership_with_interleaved_rows(sample, monkeypatch, kind):
    _, frozen, _ = repair.meter_rig(sample, monkeypatch)
    a, b = g.ChainLedger(), g.ChainLedger()
    rows = []

    def write(journal, kind, body):
        ident = g._append(journal, kind, body)
        rows.append(journal.entries()[-1] | {"interval_chain": "a" if journal is a else "b"})
        return ident

    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
               "phase": "assessment", "boot_id": g._boot_identity(), "monotonic_start": 0., "wall_start": 0.}
    first = write(a, "replacement_attempt_start", opening)
    second = write(b, "replacement_attempt_start", opening | {"wall_start": 1.})
    body = opening | {"attempt": first, "validation_s": 0., "monotonic_end": 0.,
                      "candidate_ids": [], "activation_receipts": []}
    write(b, "registration_closed", body | {"attempt": second})
    if kind == "registration_close_failed":
        write(a, "registration_closed", body)
        failure = write(b, "registration_close_failed", body)
        write(a, "registration_closed", body | {"recovery_of": failure})
    elif kind == "recovery":
        failure = write(a, "registration_close_failed", body)
        write(b, "registration_closed", body | {"recovery_of": failure})
    else:
        write(b, kind, body)
    monkeypatch.setattr(g, "_interval_rows", lambda: rows)
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


@pytest.mark.parametrize("expression", ["sys._getframe().f_globals", "frames().f_globals",
    "frames().f_locals", "namespace.__dict__", "vars(namespace)", "getattr(namespace, 'x')"])
def test_n1_only_allows_static_names_and_nodes(expression):
    imports = "import sys\n" if expression.startswith("sys") else ""
    before = (imports + "def helper(): return 2\ndef test_a():\n"
              f" if answer(1)==0:\n  assert 'renamed' in {expression}\n assert helper()==2\n")
    after = before.replace("helper", "renamed")
    assert not g._normal_equivalent(before, after)


def test_measure_entry_applies_scope_before_execution(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    entry = SimpleNamespace(replacement="R", replacement_scope=[], candidate_id=candidate.candidate_id)
    monkeypatch.setattr(g, "_replacement_authorized", lambda entry: True)
    monkeypatch.setattr(g, "_candidate", lambda entry: candidate)
    monkeypatch.setattr(g, "_case_in_scope", lambda *args: False)
    monkeypatch.setattr(g, "_measure", lambda *args: pytest.fail("excluded replacement executed"))
    verdicts, costs = g._measure_entry(entry, frozen.acceptance[:1], [entry])
    assert verdicts == ["abstain"] and costs == [0., 0., 0.]
