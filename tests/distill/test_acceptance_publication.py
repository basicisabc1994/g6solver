"""Publication boundaries, owner grants and replacement-only deadlines."""
import ast
import inspect
import subprocess
import types
from dataclasses import replace

import pytest

from g6.core.approval_authority import ApprovalAuthority
from g6.distill import acceptance_gate as g
from . import test_acceptance_repair as repair
from .test_acceptance_confirmation import entry_from_state, live_confirmation, owner_grant, owner_recovery
from .test_acceptance_endpoints import active_receipt


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


def admitted(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="e" * 64))
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    return candidate, frozen, clock, entry_from_state(candidate)


def test_final_assessment_tail_is_charged(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    final = g._final_replacement_cost
    def delayed(*args):
        if inspect.currentframe().f_back.f_code.co_name == "_confirm_interval":
            clock[0] += 20000.
        return final(*args)
    monkeypatch.setattr(g, "_final_replacement_cost", delayed)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict in {"block", "refuse"}
    assert not any(r["kind"] == "authority_confirmed" for r in g._journal().entries())


def test_final_sample_immediately_precedes_append():
    tree = ast.parse(inspect.getsource(g._confirm_interval))
    statements = tree.body[0].body
    position = next(i for i, node in enumerate(statements)
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "_sample_confirmation")
    following = statements[position + 1]
    assert isinstance(following, ast.Assign) and following.value.func.id == "_write"


@pytest.mark.parametrize("stage", ["deadline", "journal", "entries", "interval", "interval_read", "reconcile"])
@pytest.mark.parametrize("raises", [False, True])
def test_authority_read_deadline_serves_teacher_for_call(sample, monkeypatch, stage, raises):
    _, _, clock, entry = admitted(sample, monkeypatch)
    assert g._replacement_authorized(entry)
    journal = g._journal()
    before = journal.entries()
    names = {"deadline": "_append_deadline", "journal": "_journal", "interval": "_interval_journal",
             "interval_read": "_interval_rows", "reconcile": "_validation_total"}
    target, name = (journal, "entries") if stage == "entries" else (g, names[stage])
    original = getattr(target, name)
    def delayed(*args, **kwargs):
        clock[0] += 20000.
        if raises:
            raise OSError("serving read unavailable")
        return original(*args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(target, name, delayed)
        assert not g._replacement_authorized(entry)
    assert journal.entries() == before
    assert g._replacement_authorized(entry)
    assert any(d["deadline_exceeded"] for group in g._BOOKKEEPING_DIAGNOSTICS.values() for d in group)


def test_other_journal_deadline_does_not_veto_current_receipt(sample, monkeypatch):
    with monkeypatch.context() as previous:
        candidate, frozen, _ = repair.meter_rig(sample, previous)
        policy, data = g._policy()
        previous.setattr(g, "_policy", lambda: (policy, data | {"registrations": {
            frozen.registration_sha256: {"d_append_s": 2.}}}))
        previous.setattr(g, "_persist", lambda c, answer, *a, **kw: replace(answer, entry_id="d" * 64))
        assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    _, _, _, entry = admitted(sample, monkeypatch)
    assert g._replacement_authorized(entry)


def test_confirmation_clone_and_replay_cannot_inherit_durable_binding(sample, monkeypatch):
    candidate, _, clock, entry = admitted(sample, monkeypatch)
    owning = g._journal()
    close = next(r["id"] for r in owning.entries() if r["kind"] == "registration_closed")
    clone = g.ChainLedger()
    for row in owning.entries():
        clone.append(row["kind"], row["id"], row["body"])
    g._RETURNED_CONFIRMATIONS.clear()
    with monkeypatch.context() as reading:
        reading.setattr(g, "_interval_journal", lambda ident=None: clone)
        assert not g._replacement_authorized(entry)
    with pytest.raises(ValueError, match="incomplete"):
        g._confirm_interval(clone, close, owner_grant=owner_grant(monkeypatch, clone, close))
    g._recover_interval(clone, close)
    replay = clone.entries()[-1]
    owning.append(replay["kind"], replay["id"], replay["body"])
    clock[0] += 20000.
    assert not g._replacement_authorized(entry)
    assert candidate.candidate_id == entry.candidate_id


def test_restart_keeps_durable_confirmation_without_new_approval(sample, monkeypatch):
    _, _, _, entry = admitted(sample, monkeypatch)
    journal = g._journal()
    close = next(r["id"] for r in journal.entries() if r["kind"] == "registration_closed")
    g._RETURNED_CONFIRMATIONS.clear()
    assert g._replacement_authorized(entry)
    before = journal.entries()
    g._confirm_interval(journal, close)
    assert journal.entries() == before


def test_existing_person_approval_cannot_reconfirm_incomplete_interval(sample, monkeypatch):
    _, _, _, entry = admitted(sample, monkeypatch)
    journal = g._journal()
    close = next(r["id"] for r in journal.entries() if r["kind"] == "registration_closed")
    body = next(r["body"] for r in journal.entries() if r["id"] == close)
    failure = g._append(journal, "registration_close_failed", body)
    close = g._append(journal, "registration_closed", body | {"recovery_of": failure})
    authority = ApprovalAuthority()
    monkeypatch.setattr(g, "get_approval_authority", lambda: authority)
    grant = authority.issue(reviewer_id="Caller inside process", scope=g.reconfirmation_scope(journal, close))
    g._RETURNED_CONFIRMATIONS.clear()
    before = journal.entries()
    with pytest.raises(ValueError, match="owner"):
        g._recover_interval(journal, close, owner_grant=grant.approval_id, seconds_to_charge=1000.)
    assert journal.entries() == before
    assert not g._replacement_authorized(entry)
    assert authority.check(grant.approval_id, scope=grant.scope, require_human=True)


@pytest.mark.parametrize("bad", ["mapping", "invented", "multiuse", "scope"])
def test_person_approval_boundary_fails_closed(sample, monkeypatch, bad):
    _, _, _, entry = admitted(sample, monkeypatch)
    journal = g._journal()
    close = next(r["id"] for r in journal.entries() if r["kind"] == "registration_closed")
    body = next(r["body"] for r in journal.entries() if r["id"] == close)
    failure = g._append(journal, "registration_close_failed", body)
    close = g._append(journal, "registration_closed", body | {"recovery_of": failure})
    grant = owner_grant(monkeypatch, journal, close, single_use=bad != "multiuse")
    if bad == "mapping":
        grant = {"approval_id": grant}
    if bad == "invented":
        grant = "invented"
    g._RETURNED_CONFIRMATIONS.clear()
    before = journal.entries()
    with pytest.raises(ValueError, match="owner"):
        g._recover_interval(journal, close, owner_grant=grant,
                            seconds_to_charge=1. if bad == "scope" else 10.)
    assert journal.entries() == before
    assert not g._replacement_authorized(entry)


@pytest.mark.parametrize("bad", ["invented", "wrong_scope", "oracle", "revoked", "expired"])
def test_reconfirmation_rejects_invalid_grants(sample, monkeypatch, bad):
    _, _, _, entry = admitted(sample, monkeypatch)
    journal = g._journal()
    close = next(r["id"] for r in journal.entries() if r["kind"] == "registration_closed")
    body = next(r["body"] for r in journal.entries() if r["id"] == close)
    failure = g._append(journal, "registration_close_failed", body)
    close = g._append(journal, "registration_closed", body | {"recovery_of": failure})
    authority = ApprovalAuthority()
    monkeypatch.setattr(g, "get_approval_authority", lambda: authority)
    kwargs = {"reviewer_id": "Fixture owner", "scope": g.reconfirmation_scope(journal, close)}
    if bad == "wrong_scope":
        kwargs["scope"] = "another close"
    if bad == "oracle":
        kwargs.update(issuer_kind="policy_oracle", policy_version="fixture")
    if bad == "expired":
        kwargs["ttl_seconds"] = -1.
    record = authority.issue(**kwargs)
    grant = record.approval_id
    if bad == "revoked":
        authority.revoke(record.approval_id)
    if bad == "invented":
        grant = "invented"
    g._RETURNED_CONFIRMATIONS.clear()
    before = journal.entries()
    with pytest.raises(ValueError, match="owner"):
        g._recover_interval(journal, close, owner_grant=grant, seconds_to_charge=10.)
    assert journal.entries() == before
    assert not g._replacement_authorized(entry)


@pytest.mark.parametrize("boot", [None, "different-boot"])
def test_recovery_rejects_positive_uptime_after_reboot(sample, monkeypatch, boot):
    _, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_boot_identity", lambda: boot, raising=False)
    clock[0] = 101.
    monkeypatch.setattr(g.time, "time", lambda: 60000.)
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
               "monotonic_start": 100., "wall_start": 0., "boot_id": "old-boot"}
    with pytest.raises(ValueError, match="owner"):
        g._interval_elapsed(opening, recovery=True)


@pytest.mark.parametrize("upper", [-50000., -100000.])
def test_cross_boot_owner_recovery_charges_full_cost_without_authority(sample, monkeypatch, upper):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    monkeypatch.setattr(g, "_boot_identity", lambda: "old-boot")
    clock[0] = 100.
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
               "phase": "assessment", "monotonic_start": 100., "wall_start": 0., "boot_id": "old-boot"}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt, upper=upper)
    close = g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "validation_s": 0., "monotonic_end": 100., "candidate_ids": [candidate.candidate_id],
        "activation_receipts": [receipt]})
    clock[0] = 101.
    monkeypatch.setattr(g.time, "time", lambda: 60000.)
    monkeypatch.setattr(g, "_boot_identity", lambda: "new-boot")
    before = journal.entries()
    with pytest.raises(ValueError, match="owner"):
        g._recover_interval(journal, close)
    assert journal.entries() == before
    result = owner_recovery(monkeypatch, journal, close, 60010.)
    assert result["state"] == "voided", result
    assert g._validation_total(frozen.registration_sha256, None) == 60010.
    assert not g._replacement_authorized(entry)


def test_file_clone_and_replaced_file_do_not_inherit_return(sample, monkeypatch, tmp_path):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    journal = g.ChainLedger(tmp_path / "owner.jsonl")
    monkeypatch.setattr(g, "_journal", lambda: journal)
    monkeypatch.setattr(g, "_interval_journal", lambda ident=None: journal)
    opening = {"registration": frozen.registration_sha256, "interval_version": 1,
               "phase": "assessment", "monotonic_start": 0., "wall_start": 0., "boot_id": g._boot_identity()}
    attempt = g._append(journal, "replacement_attempt_start", opening)
    entry, receipt = active_receipt(journal, candidate, frozen.registration_sha256, attempt)
    close = g._append(journal, "registration_closed", opening | {"attempt": attempt,
        "validation_s": 0., "monotonic_end": 0., "candidate_ids": [candidate.candidate_id],
        "activation_receipts": [receipt]})
    live_confirmation(monkeypatch, journal, close)
    assert g._replacement_authorized(entry)
    content = journal.path.read_bytes()
    clone = tmp_path / "clone.jsonl"
    clone.write_bytes(content)
    monkeypatch.setattr(g, "_interval_journal", lambda ident=None: g.ChainLedger(clone))
    assert not g._replacement_authorized(entry)
    journal.path.unlink()
    clone.rename(journal.path)
    monkeypatch.setattr(g, "_interval_journal", lambda ident=None: g.ChainLedger(journal.path))
    assert not g._replacement_authorized(entry)


def test_missing_file_id_uses_journal_open_identity(monkeypatch):
    class Journal:
        path = "journal.jsonl"

    class Path:
        def __init__(self, path):
            pass

        def resolve(self, **kwargs):
            return self

        def stat(self):
            return types.SimpleNamespace(st_dev=0, st_ino=0)

        def __str__(self):
            return "resolved/journal.jsonl"

    monkeypatch.setattr(g, "Path", Path)
    first, clone = Journal(), Journal()
    identity = g._journal_identity(first, "same-opening-row")
    assert identity == g._journal_identity(first, "same-opening-row")
    assert identity != g._journal_identity(clone, "same-opening-row")


@pytest.mark.parametrize("delay", [6., 20000.])
@pytest.mark.parametrize("setting", [None, -1., .01])
def test_add_matches_original_with_delayed_opening(sample, monkeypatch, delay, setting):
    historical = subprocess.run(["git", "show", "42279df2:src/g6/distill/acceptance_gate.py"],
                                check=True, capture_output=True, text=True).stdout
    baseline = types.ModuleType(g.__name__)
    baseline.__package__ = "g6.distill"
    baseline.__file__ = g.__file__
    exec(compile(historical, "<42279df2>", "exec"), baseline.__dict__)
    outcomes = []
    for module in (baseline, g):
        persist = module._persist
        with monkeypatch.context() as scoped:
            candidate, frozen, clock = repair.meter_rig(sample, scoped)
            # Use the same isolated public rig for both module versions.
            for name in ("_journal", "_load", "_policy", "_writer", "_look_available", "_spend",
                         "Stores", "Library", "_record", "_persist", "_measure", "_replacement_quality", "_equal"):
                scoped.setattr(module, name, getattr(g, name))
            policy, data = module._policy()
            scoped.setattr(module, "_policy", lambda: (policy, data | {"registrations": {
                frozen.registration_sha256: {"d_append_s": setting}}}))
            append = module._append
            def delayed(journal, kind, body):
                if kind == "replacement_attempt_start":
                    clock[0] += delay
                return append(journal, kind, body)
            scoped.setattr(module, "_append", delayed)
            certificates = []
            class Store:
                def get(self, ident):
                    raise KeyError(ident)

                def put(self, value):
                    pass

                def list(self):
                    return []

            def save(body):
                certificates.append(g.canonical(body) + "\n")
                return g.digest(body)

            def persist_add(c, answer, evidence, own, frozen, data, library):
                with monkeypatch.context() as storage:
                    storage.setattr(module, "Stores", lambda: types.SimpleNamespace(for_model=lambda cls: Store()))
                    storage.setattr(module, "_save_report", save)
                    return persist(c, answer, evidence, own, replace(frozen, acceptance=()), data, library)

            scoped.setattr(module, "_persist", persist_add)
            answer = module.decide(candidate, module.Library(), frozen.registration_sha256)
            outcomes.append((answer.verdict, answer.reasons, answer.report_sha256, certificates[-1]))
    assert outcomes[1] == outcomes[0]
