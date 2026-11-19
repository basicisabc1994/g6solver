"""The client dialog is the only issuer for a cross-boot recovery floor."""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from g6.core.approval_authority import ApprovalAuthority
from g6.distill import acceptance_gate as g
from g6.mcp import distill
from tests.distill import test_acceptance_repair as repair
from tests.distill.test_acceptance_rulings import interrupted


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


def rig(sample, monkeypatch):
    journal, close, frozen, clock, entry = interrupted(sample, monkeypatch)
    clock[0] = 5.
    monkeypatch.setattr(g.time, "time", lambda: 60000.)
    monkeypatch.setattr(g, "_interval_journal", lambda ident=None: journal)
    authority = ApprovalAuthority()
    monkeypatch.setattr(g, "get_approval_authority", lambda: authority)
    return journal, close, frozen, clock, entry, authority


def tool():
    class Server:
        def __init__(self):
            self.tools = {}
        def tool(self):
            def attach(fn):
                self.tools[fn.__name__] = fn
                return fn
            return attach
    server = Server()
    distill.register(server, object, object(), backend=object())
    return server.tools["g6_library_recover"]


def context(action="accept", content=None, unavailable=False, shown=None):
    class Session:
        async def elicit_form(self, **kwargs):
            if shown is not None:
                shown.append(kwargs["message"])
            if unavailable:
                raise NotImplementedError("client has no elicitation")
            return SimpleNamespace(action=action, content=content)
    return SimpleNamespace(session=Session(), request_id="recovery-request")


def test_preissued_record_cannot_approve_cross_boot_floor(sample, monkeypatch):
    journal, close, _, _, entry, authority = rig(sample, monkeypatch)
    binding = g._recovery_binding(journal, close, seconds_to_charge=60020.)
    record = authority.issue(reviewer_id="Owner", scope="distill:reconfirm:" + g.digest(binding),
        run_id=binding["interval_id"], single_use=True, ttl_seconds=60.,
        disclosure="Caller-created dialog text", acknowledgement='{"confirm":"change","name":"Owner"}')
    with pytest.raises(ValueError, match="owner"):
        g._recover_interval(journal, close, owner_grant=record.approval_id, seconds_to_charge=60020.)
    assert not g._replacement_authorized(entry)


def test_dialog_grant_cannot_confirm_old_interval(sample, monkeypatch):
    journal, close, _, _, entry, _ = rig(sample, monkeypatch)
    original = g._recover_interval
    def recover(journal, close, **kwargs):
        with pytest.raises(ValueError, match="incomplete"):
            g._confirm_interval(journal, close, owner_grant=kwargs["owner_grant"])
        return original(journal, close, **kwargs)
    monkeypatch.setattr(g, "_recover_interval", recover)
    run_id = next(r["id"] for r in journal.entries() if r["kind"] == "replacement_attempt_start")
    result = asyncio.run(tool()(run_id, close, 60020., context(content={"confirm": "change", "name": "Owner"})))
    assert result["state"] == "voided", result
    assert not g._replacement_authorized(entry)


def test_stale_close_is_refused_before_dialog_or_issuance(sample, monkeypatch):
    journal, close, _, _, entry, authority = rig(sample, monkeypatch)
    body = next(r["body"] for r in journal.entries() if r["id"] == close)
    failure = g._append(journal, "registration_close_failed", body)
    g._append(journal, "registration_closed", body | {"recovery_of": failure})
    shown = []
    run_id = next(r["id"] for r in journal.entries() if r["kind"] == "replacement_attempt_start")
    result = asyncio.run(tool()(run_id, close, 60020., context(
        content={"confirm": "change", "name": "Owner"}, shown=shown)))
    assert "G6_E_DISTILL_RECOVERY_INPUT" in str(result)
    assert shown == []
    assert not authority._store._records
    assert not g._replacement_authorized(entry)


def test_approved_floor_must_cover_work_until_final_recovery_sample(sample, monkeypatch):
    journal, close, _, clock, entry, authority = rig(sample, monkeypatch)
    wall = [60000.]
    monkeypatch.setattr(g.time, "time", lambda: wall[0])
    original = g._validation_total
    def delayed(registration, active):
        wall[0] += 30.
        clock[0] += 30.
        return original(registration, active)
    monkeypatch.setattr(g, "_validation_total", delayed)
    run_id = next(r["id"] for r in journal.entries() if r["kind"] == "replacement_attempt_start")
    result = asyncio.run(tool()(run_id, close, 60020., context(content={"confirm": "change", "name": "Owner"})))
    assert "G6_E_" in str(result)
    assert not any(r["kind"] == "registration_voided" for r in journal.entries())
    assert next(iter(authority._store._records.values())).consumed_at is not None
    assert not g._replacement_authorized(entry)


@pytest.mark.parametrize("bad", ["oracle", "expired", "revoked", "multiuse", "scope", "run_id"])
def test_adapter_record_must_still_pass_authority_checks(sample, monkeypatch, bad):
    journal, close, _, _, entry, authority = rig(sample, monkeypatch)
    issue = authority.issue
    def changed(**kwargs):
        if bad == "oracle":
            kwargs.update(issuer_kind="policy_oracle", policy_version="fixture")
        if bad == "expired":
            kwargs["ttl_seconds"] = -1.
        if bad == "multiuse":
            kwargs["single_use"] = False
        if bad in {"scope", "run_id"}:
            kwargs[bad] = "different"
        record = issue(**kwargs)
        if bad == "revoked":
            authority.revoke(record.approval_id)
        return record
    monkeypatch.setattr(authority, "issue", changed)
    run_id = next(r["id"] for r in journal.entries() if r["kind"] == "replacement_attempt_start")
    result = asyncio.run(tool()(run_id, close, 60020., context(content={"confirm": "change", "name": "Owner"})))
    assert "G6_E_" in str(result)
    assert not any(r["kind"] == "registration_voided" for r in journal.entries())
    assert not g._replacement_authorized(entry)


@pytest.mark.parametrize("action,content,unavailable", [
    ("decline", None, False), ("cancel", None, False), ("accept", None, True),
    ("accept", {"confirm": "keep", "name": "Owner"}, False),
    ("accept", {"confirm": "change", "name": ""}, False),
])
def test_no_dialog_acceptance_leaves_no_authority(sample, monkeypatch, action, content, unavailable):
    journal, close, _, _, entry, authority = rig(sample, monkeypatch)
    run_id = next(r["id"] for r in journal.entries() if r["kind"] == "replacement_attempt_start")
    before = journal.entries()
    result = asyncio.run(tool()(run_id, close, 60020., context(action, content, unavailable)))
    assert "G6_E_DISTILL_RECOVERY_DIALOG" in str(result)
    assert journal.entries() == before
    assert not g._replacement_authorized(entry)
    assert not any(r["kind"] == "authority_reconfirmed" for r in journal.entries())
    assert not authority._store._records


def test_accepted_dialog_records_exact_scope_and_consumes_once(sample, monkeypatch):
    journal, close, frozen, _, entry, authority = rig(sample, monkeypatch)
    opening = next(r for r in journal.entries() if r["kind"] == "replacement_attempt_start")
    shown = []
    fn = tool()
    assert set(inspect.signature(fn).parameters) == {"interval_id", "close_id", "seconds_to_charge", "ctx"}
    result = asyncio.run(fn(opening["id"], close, 60020., context(
        content={"confirm": "change", "name": "Owner"}, shown=shown)))
    assert result["state"] == "voided", result
    assert g._validation_total(frozen.registration_sha256, None) == 60020.
    assert not g._replacement_authorized(entry)
    row = next(r for r in journal.entries() if r["kind"] == "authority_reconfirmed")
    body = row["body"]
    record = authority.resolve(body["approval_id"])
    assert record.run_id == opening["id"]
    assert record.single_use and record.consumed_at is not None
    assert 0 < record.expires_at - record.created_at <= 60.
    assert record.disclosure == shown[0]
    assert '"confirm":"change"' in record.acknowledgement
    assert body["dialog_text"] == shown[0]
    assert body["dialog_reply"] == record.acknowledgement
    binding = body["approval_binding"]
    assert set(binding) == {"interval_id", "close_id", "report_sha256", "candidate", "action", "seconds_to_charge"}
    assert binding["interval_id"] == opening["id"] and binding["close_id"] == close
    assert binding["candidate"] == entry.candidate_id
    assert binding["action"] == "void" and binding["seconds_to_charge"] == 60020.
    for value in binding.values():
        assert str(value) in shown[0]
    assert record.scope == body["approval_scope"] == "distill:reconfirm:" + g.digest(binding)
    assert not distill._consume_recovery_approval(record.approval_id, binding)


@pytest.mark.parametrize("field", ["interval_id", "close_id", "report_sha256", "candidate", "action", "seconds_to_charge"])
def test_scope_change_in_any_dialog_field_is_refused(sample, monkeypatch, field):
    journal, close, _, _, entry, authority = rig(sample, monkeypatch)
    original = g._recover_interval
    def changed(journal, close, **kwargs):
        binding = g._recovery_binding(journal, close, seconds_to_charge=60020.)
        binding[field] = 60021. if field == "seconds_to_charge" else "different"
        assert not distill._consume_recovery_approval(kwargs["owner_grant"], binding)
        return original(journal, close, **kwargs)
    monkeypatch.setattr(g, "_recover_interval", changed)
    run_id = next(r["id"] for r in journal.entries() if r["kind"] == "replacement_attempt_start")
    result = asyncio.run(tool()(run_id, close, 60020., context(content={"confirm": "change", "name": "Owner"})))
    assert "G6_E_" in str(result)
    assert not g._replacement_authorized(entry)
    assert not any(r["kind"] == "registration_voided" for r in journal.entries())
    records = list(authority._store._records.values())
    assert len(records) == 1 and records[0].consumed_at is None
