# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What every attempt costs, recorded and summed (Phase E Part 1, E1a; Ledger §4, §5, §18, §21, §28 Phase 1).

Pinned: the door records one cost entry per call it sends (none for a call it refused), from what the tool's
reviewed contract declares, with the model calls THIS call's context noted (another tenant's are never charged to
it), and refuses a call that would pass the attempt's budget or, on a charged run, whose cost it cannot bound; the
ledger stores the entries as append-only rows with the attempt that made them, and the attempt's summary is exactly
their sum (the database refuses any other); an unknown amount is never 0; totals propagate from call to attempt,
unit, mission and account; a store from before this change migrates and its old attempts read as unknown cost.
Offline, scripted components.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from g6.core import model_calls as mc
from g6.safety_engine.schema import RuntimePolicy
from g6.workforce import store
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import economics as we
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt
from g6.workforce import workers as ww

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731
CEILING = RuntimePolicy(max_steps=10, max_tool_calls=5, max_shell_commands=0, max_files_modified=0,
                        max_lines_changed=0, max_retries=1, max_runtime_seconds=60, max_cost_cents=50,
                        max_privilege_level=0)
AUD30 = {"amount_minor_units": 30, "currency": "AUD"}


def _contract(component: str, op: str, **extra) -> wcap.ParamContract:
    return wcap.ParamContract.from_dict({"component": component, "op": op, "required": {"x": "int"},
                                         "probe_params": {"x": 1}, "expect_field": "y", "side_effect": "read",
                                         "check": "input_model", **extra})


CONTRACTS = {
    ("ledger", "total"): _contract("ledger", "total"),  # declares no cost
    ("solver", "check"): _contract("solver", "check", model_free=True,
                                   cost_ceiling={"amount_minor_units": 0, "currency": "AUD"}),
    ("search", "query"): _contract("search", "query", cost_ceiling=AUD30),
    ("broker", "quote"): _contract("broker", "quote", cost_ceiling={"amount_minor_units": 30, "currency": "USD"}),
    ("billing_stripe", "info"): _contract("billing_stripe", "info"),
}


class Spy:
    def __init__(self, reply=None, *, model_calls: int = 0) -> None:
        self.reply = {"ok": True, "completion_state": "qualified-draft", "result": {"y": 2}} if reply is None else reply
        self.model_calls, self.calls = model_calls, []

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op, params))
        for _ in range(self.model_calls):
            mc.note_model_call()
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _door(*pairs, charged: bool = False, spent: int = 0, spy=None, **policy) -> wcall.Door:
    allowed = tuple(pairs)
    # One narrowing: ``narrow`` refuses an empty allow-list (allow-any), so a policy override goes with the tools.
    worker = ww.WorkerSpec(template="doer", purpose="test", rung="deterministic", allowed=allowed,
                           policy=ww.narrow(CEILING, allowed_tools=frozenset(f"{c}.{o}" for c, o in allowed),
                                            **policy),
                           read_only=False)
    return wcall.Door(worker, contracts=lambda c, o: CONTRACTS.get((c, o)), run_id="run-1",
                      invoke=spy if spy is not None else Spy(), charged=charged, spent_cents=spent)


# --- the door (Task 5) ----------------------------------------------------------------------------------------


def test_a_call_that_answered_records_one_cost_entry_from_its_declared_cost():
    free = _door(("solver", "check")).call("solver", "check", {"x": 1})
    assert (free.cost.source, free.cost.amount_minor_units, free.cost.estimated, free.cost.category) == (
        "declared_free", 0, False, "compute")
    priced = _door(("search", "query")).call("search", "query", {"x": 1})
    assert (priced.cost.source, priced.cost.amount_minor_units, priced.cost.estimated) == ("declared_ceiling", 30, True)
    assert priced.contract_sha == CONTRACTS[("search", "query")].sha256


def test_a_tool_that_declares_no_cost_is_unknown_never_zero():
    result = _door(("ledger", "total")).call("ledger", "total", {"x": 1})
    assert (result.cost.source, result.cost.amount_minor_units, result.cost.currency) == ("unpriced", None, None)
    assert we.total([result.cost]).amount_minor_units is None


def test_the_model_calls_a_call_noted_are_its_quantity():
    result = _door(("ledger", "total"), spy=Spy(model_calls=2)).call("ledger", "total", {"x": 1})
    assert (result.cost.category, result.cost.quantity, result.charged_model_calls) == ("model_inference", 2, 2)
    assert result.model_calls >= 2 and result.cost.metadata["model_calls"] == 2


@pytest.mark.parametrize(("pairs", "call", "code"), [
    ((("solver", "check"),), ("ledger", "total", {"x": 1}), "refused:allowlist"),
    ((("solver", "check"), ("mailer", "send")), ("mailer", "send", {"x": 1}), "refused:undeclared"),
    ((("solver", "check"),), ("solver", "check", {"x": "1"}), "refused:params"),
])
def test_a_refused_call_costs_nothing_and_says_which_refusal(pairs, call, code):
    spy = Spy()
    result = _door(*pairs, spy=spy).call(*call)
    assert (result.refusal_code, result.cost, spy.calls) == (code, None, [])
    assert result.outcome.state == wc.BLOCKED and result.refused


def test_a_call_that_would_pass_its_budget_is_refused_before_it_is_sent():
    spy = Spy()
    door = _door(("search", "query"), spy=spy)
    first = door.call("search", "query", {"x": 1})
    second = door.call("search", "query", {"x": 2})
    assert first.cost.amount_minor_units == 30 and len(spy.calls) == 1
    assert (second.refusal_code, second.cost) == ("refused:budget", None)
    assert "may cost up to 0.30 AUD, and this step has 0.20 AUD left of its 0.50 AUD budget" in second.refused


def test_what_the_unit_spent_in_earlier_attempts_counts_against_this_one():
    spy = Spy()
    result = _door(("search", "query"), spy=spy, spent=40).call("search", "query", {"x": 1})
    assert result.refusal_code == "refused:budget" and spy.calls == []
    assert "0.10 AUD left of its 0.50 AUD budget" in result.refused


def test_a_charged_run_refuses_a_call_whose_cost_it_cannot_bound():
    spy = Spy()
    unpriced = _door(("ledger", "total"), charged=True, spy=spy).call("ledger", "total", {"x": 1})
    in_usd = _door(("broker", "quote"), charged=True, spy=spy).call("broker", "quote", {"x": 1})
    assert (unpriced.refusal_code, in_usd.refusal_code, spy.calls) == ("refused:unpriced", "refused:unpriced", [])
    assert "has no declared cost" in unpriced.refused and "declares its cost in USD" in in_usd.refused
    assert _door(("search", "query"), charged=True).call("search", "query", {"x": 1}).cost.amount_minor_units == 30


def test_a_cost_in_another_currency_is_kept_apart_on_a_local_run():
    result = _door(("broker", "quote")).call("broker", "quote", {"x": 1})
    assert (result.cost.amount_minor_units, result.cost.currency) == (30, "USD")
    found = we.total([result.cost])
    assert (found.known_minor_units, found.other_currency_entries, found.amount_minor_units) == (0, 1, None)


@pytest.mark.parametrize(("raised", "kind"), [(ConnectionError("down"), "unreachable"),
                                              (TimeoutError("slow"), "timeout"), (ValueError("bad"), "raised")])
def test_a_call_that_raised_says_what_kind_and_still_records_its_cost(raised, kind):
    result = _door(("search", "query"), spy=Spy(raised)).call("search", "query", {"x": 1})
    assert result.outcome.state == wc.BLOCKED and result.raised == kind
    assert result.cost.amount_minor_units == 30  # it was sent: its declared ceiling is charged


def test_the_tool_call_ceiling_is_its_own_refusal():
    door = _door(("solver", "check"), max_tool_calls=1)
    door.call("solver", "check", {"x": 1})
    assert door.call("solver", "check", {"x": 2}).refusal_code == "refused:ceiling"


def test_two_tenants_calling_at_once_are_each_charged_only_their_own_model_calls():
    barrier, results = threading.Barrier(2, timeout=20), {}

    class Waiting(Spy):
        def __call__(self, component, op, params):
            for _ in range(self.model_calls):
                mc.note_model_call()
            barrier.wait()  # both have noted their calls while both calls are open
            return self.reply

    def call(name: str, calls: int) -> None:
        results[name] = _door(("ledger", "total"), spy=Waiting(model_calls=calls)).call("ledger", "total", {"x": 1})

    threads = [threading.Thread(target=call, args=("a", 3)), threading.Thread(target=call, args=("b", 5))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert (results["a"].charged_model_calls, results["b"].charged_model_calls) == (3, 5)
    assert (results["a"].cost.quantity, results["b"].cost.quantity) == (3, 5)


class _Registry:
    def list_all(self):
        return [c for c, _ in CONTRACTS]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def test_a_probe_may_spend_one_calls_declared_cost_and_no_more(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    vstore.create_venture("alice", "Alice's", **_CAPS)
    caps = wcap.Capabilities(vstore.for_owner("alice"), contracts=CONTRACTS, registry=_Registry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    spy = Spy()
    assert caps.probe("search", "query", wcall.prober(caps, invoke=spy).reply, actor="code").level == "probe_passed"
    assert len(spy.calls) == 1


def test_a_free_charged_probe_preserves_the_tools_own_budget_error(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    caps = wcap.Capabilities(vstore.for_owner("customer:7"), contracts=CONTRACTS, registry=_Registry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    reply = {"ok": False, "code": "G6_E_COST_GATE_OVER_BUDGET", "error": "the solver's own quota is exhausted"}
    spy = Spy(reply)
    found = wcall.prober(caps, invoke=spy).reply("solver", "check", {"x": 1})
    assert len(spy.calls) == 1
    assert found == reply


def test_a_contract_may_declare_its_cost_and_how_long_its_results_stay_valid():
    contract = _contract("search", "query", cost_ceiling=AUD30, valid_for_seconds=3600)
    assert (contract.cost_ceiling, contract.valid_for_seconds) == (we.Money(30, "AUD"), 3600)


@pytest.mark.parametrize(("extra", "why"), [
    ({"cost_ceiling": {"amount_minor_units": 0.3, "currency": "AUD"}}, "cost_ceiling"),
    ({"cost_ceiling": {"amount_minor_units": 30, "currency": "aud"}}, "cost_ceiling"),
    ({"cost_ceiling": 30}, "cost_ceiling"),
    ({"valid_for_seconds": 0}, "valid_for_seconds"),
    ({"valid_for_seconds": True}, "valid_for_seconds"),
])
def test_a_badly_declared_cost_or_validity_is_refused(extra, why):
    with pytest.raises(ValueError, match=why):
        _contract("search", "query", **extra)


def _digest_before_part_1(raw: dict, **declared) -> str:
    """A contract file's digest as computed before Phase E Part 1 (no cost_ceiling, no valid_for_seconds), plus exactly
    the ``declared`` new fields."""
    body = {"component": raw["component"], "op": raw["op"], "required": raw["required"],
            "optional": raw.get("optional", {}), "probe_params": raw["probe_params"],
            "expect_field": raw["expect_field"], "side_effect": raw["side_effect"], "check": raw["check"],
            "backend_package": raw.get("backend_package", "")}
    if raw.get("model_free"):
        body["model_free"] = True
    if raw.get("shadow_probe"):
        body["shadow_probe"] = True
    if "packages" in raw:
        body["packages"] = list(raw["packages"])
    body.update(declared)
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_every_shipped_contract_keeps_its_digest():
    """Plan ruling R19: the new optional fields enter the digest only when declared, so no probe goes stale. A contract
    that declares one (PGA D3: the two formal ops declare a cost ceiling of 0) changes by exactly that field."""
    paths = sorted(wcap.PARAMS_DIR.glob("*.json"))
    assert paths
    declared = []
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        added = {key: raw[key] for key in ("cost_ceiling", "valid_for_seconds") if key in raw}
        if added:
            declared.append(path.name)
        assert wcap.ParamContract.from_dict(raw).sha256 == _digest_before_part_1(raw, **added), path.name
    assert declared == []  # the two formal ops that declared a ceiling are not shipped by the library


# --- the attempt ledger (Task 6) ----------------------------------------------------------------------------------


def _worker() -> ww.WorkerSpec:
    policy = RuntimePolicy(max_shell_commands=0, max_files_modified=0, max_lines_changed=0,
                           allowed_tools=frozenset({"solver.check"}))
    return ww.WorkerSpec(template="doer", purpose="p", rung="deterministic", allowed=(("solver", "check"),),
                         policy=policy, read_only=False)


@pytest.fixture
def books(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    alice = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    bob = vstore.create_venture("bob", "Bob's", **_CAPS)["venture_id"]
    a, b = wm.MissionStore(vstore.for_owner("alice")), wm.MissionStore(vstore.for_owner("bob"))

    def active(missions, venture):  # a mission standing as active (how it gets there is the graph's tests)
        mid = missions.create(venture, objective="Sum the ledger", risk_tier="R1", budget_cents=50, actor="chat")
        with vstore.conn:
            vstore.conn.execute("UPDATE missions SET status='active' WHERE mission_id=?", (mid,))
        return mid

    return SimpleNamespace(vstore=vstore, a=a, ledger=wr.RunLedger(a), other=wr.RunLedger(b), mid=active(a, alice),
                           second=active(a, alice), bob_mid=active(b, bob))


def _sent(amount, currency="AUD"):
    return we.call_cost(None if amount is None else we.Money(amount, currency), model_free=False, component="solver",
                        op="check", charged_model_calls=0, process_model_calls=0)


def _close(books, unit="sum", costs=(), mid=None):
    mid = mid or books.mid
    run = books.ledger.open(mid, unit, _worker())
    books.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), artifact={"schema": "t", "content": {"total": 6}},
                       costs=costs)
    return books.ledger.run(mid, run.run_id)


def test_an_attempts_cost_rows_are_written_with_its_close_and_its_summary_is_their_sum(books):
    row = _close(books, costs=(_sent(30), _sent(0)))
    assert (row["cost_cents"], row["cost_source"]) == (30, "estimated")
    entries = books.ledger.cost_entries(books.mid)
    assert [(e.attempt_id, e.work_unit_id, e.capability_invocation_id, e.worker_instance_id) for e in entries] == [
        (row["run_id"], "sum", f"{row['run_id']}#0", row["instance_id"]),
        (row["run_id"], "sum", f"{row['run_id']}#1", row["instance_id"])]
    assert all(e.id and e.recorded_at and e.mission_id == books.mid for e in entries)
    assert [e["kind"] for e in books.a.events(books.mid, kind="cost")] == ["cost", "cost"]


def test_a_call_refused_at_the_door_has_no_row_and_the_others_keep_their_call_index(books):
    row = _close(books, costs=(None, _sent(0)))
    assert [e.capability_invocation_id for e in books.ledger.cost_entries(books.mid)] == [f"{row['run_id']}#1"]


def test_an_unknown_amount_keeps_the_summary_unknown_never_zero(books):
    row = _close(books, costs=(_sent(None), _sent(30)))
    assert (row["cost_cents"], row["cost_source"]) == (30, "unknown")
    found = books.ledger.cost_total(books.mid)
    assert (found.amount_minor_units, found.known_minor_units, found.unknown_entries) == (None, 30, 1)
    assert _close(books, unit="free")["cost_source"] == "exact"  # an attempt that sent nothing spent nothing


def test_the_database_refuses_a_summary_that_is_not_the_sum_and_a_row_for_a_finished_attempt(books):
    run = books.ledger.open(books.mid, "sum", _worker())
    with pytest.raises(sqlite3.DatabaseError), books.vstore.conn:
        books.vstore.conn.execute("UPDATE task_runs SET state='closed', cost_cents=5, cost_source='exact'"
                                  " WHERE run_id=?", (run.run_id,))
    books.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), costs=(_sent(30),))
    with pytest.raises(sqlite3.DatabaseError), books.vstore.conn:
        books.a.append_event(books.mid, "cost", "code", {**_sent(30).as_detail(), "attempt_id": run.run_id}, "late")


def test_a_close_that_fails_leaves_no_cost_row_behind(books):
    run = books.ledger.open(books.mid, "sum", _worker())
    books.vstore.conn.execute("CREATE TEMP TRIGGER inject_close BEFORE INSERT ON main.evidence"
                              " WHEN NEW.kind='run.closed' BEGIN SELECT RAISE(ABORT, 'injected'); END;")
    with pytest.raises(sqlite3.DatabaseError):
        books.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), costs=(_sent(30),))
    books.vstore.conn.execute("DROP TRIGGER temp.inject_close")
    assert books.ledger.cost_entries(books.mid) == []
    assert books.ledger.run(books.mid, run.run_id)["state"] == "open"
    books.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), costs=(_sent(30),))  # the retried close lands once
    assert len(books.ledger.cost_entries(books.mid)) == 1


@pytest.mark.parametrize(("code", "rows", "summary"), [
    ("abandoned:claim", 0, "exact"), ("abandoned:lease", 1, "unknown"), ("abandoned:clock", 1, "unknown"),
    ("abandoned", 1, "unknown")])
def test_an_abandoned_attempts_cost_is_unknown_unless_it_never_started(books, code, rows, summary):
    run = books.ledger.open(books.mid, "sum", _worker())
    books.ledger.abandon(run.run_id, "it stopped", code=code)
    row = books.ledger.run(books.mid, run.run_id)
    assert (row["failure_code"], row["cost_source"], len(books.ledger.cost_entries(books.mid))) == (
        code, summary, rows)


def test_an_abandon_names_a_known_reason(books):
    run = books.ledger.open(books.mid, "sum", _worker())
    with pytest.raises(ValueError, match="abandoned"):
        books.ledger.abandon(run.run_id, "it stopped", code="gone")


def test_a_blocked_close_with_no_code_is_recorded_unclassified(books):
    run = books.ledger.open(books.mid, "sum", _worker())
    books.ledger.close(run.run_id, wc.Outcome(wc.BLOCKED, reasons=("no code given",)))
    assert books.ledger.run(books.mid, run.run_id)["failure_code"] == "unclassified"


def test_costs_propagate_from_attempt_to_unit_mission_and_account(books):
    a = _close(books, unit="sum", costs=(_sent(30),))
    b = _close(books, unit="sum", costs=(_sent(0),))
    c = _close(books, unit="check", costs=(_sent(None),))
    _close(books, unit="sum", costs=(_sent(10),), mid=books.second)
    ledger = books.ledger
    assert ledger.cost_total(books.mid, run_id=a["run_id"]).known_minor_units == 30
    assert ledger.cost_total(books.mid, unit_key="sum").known_minor_units == 30
    mission = ledger.cost_total(books.mid)
    assert (mission.known_minor_units, mission.entries, mission.unknown_entries) == (30, 3, 1)
    assert sum(ledger.cost_total(books.mid, run_id=r["run_id"]).entries for r in (a, b, c)) == mission.entries
    account = ledger.cost_total()
    assert (account.known_minor_units, account.entries) == (40, 4)


def test_another_owners_costs_are_refused_like_an_unknown_mission(books):
    _close(books, costs=(_sent(30),))
    with pytest.raises(LookupError):
        books.other.cost_total(books.mid)
    with pytest.raises(LookupError):
        books.other.cost_entries(books.mid)
    assert books.other.cost_total().entries == 0


def test_a_store_from_before_cost_rows_migrates_and_its_old_attempts_read_as_unknown(books):
    """Review Focus 3."""
    run = books.ledger.open(books.mid, "sum", _worker())
    conn = books.vstore.conn
    with conn:  # the store as the previous release left it: no cost rules, and a run closed the old way
        for name in ("cost_rows_while_open", "task_runs_cost_is_its_rows"):
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DROP INDEX idx_ev_kind_ref")
        conn.execute("UPDATE task_runs SET state='closed', completion_state='qualified-draft' WHERE run_id=?",
                     (run.run_id,))
    assert books.ledger.run(books.mid, run.run_id)["cost_source"] == "none"
    again = wr.RunLedger(books.a)  # the next process opens the store
    made = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger', 'index')")}
    assert {"cost_rows_while_open", "task_runs_cost_is_its_rows", "idx_ev_kind_ref"} <= made
    found = again.cost_total(books.mid)
    assert (found.unknown_entries, found.amount_minor_units, found.summary) == (1, None, "unknown")
    fresh = again.open(books.mid, "next", _worker())
    with pytest.raises(sqlite3.DatabaseError), conn:  # and from now on the rules hold
        conn.execute("UPDATE task_runs SET state='closed', cost_cents=7, cost_source='exact' WHERE run_id=?",
                     (fresh.run_id,))

# --- the runtime (Task 7) ------------------------------------------------------------------------------------------

RUN_CONTRACTS = {
    ("fake_ledger", "total"): wcap.ParamContract.from_dict({  # declares no cost
        "component": "fake_ledger", "op": "total", "required": {"numbers": "list"}, "probe_params": {"numbers": [1]},
        "expect_field": "total", "side_effect": "read", "check": "input_model"}),
    ("fake_solver", "check"): wcap.ParamContract.from_dict({
        "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
        "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
        "check": "input_model", "model_free": True, "cost_ceiling": {"amount_minor_units": 0, "currency": "AUD"}}),
}


class RunRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


class World:
    """A ledger that totals (noting ``model_calls`` model calls, then waiting at ``sync`` if set) and a solver."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.model_calls = 0
        self.sync = None

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op))
        if component == "fake_ledger":
            for _ in range(self.model_calls):
                mc.note_model_call()
            if self.sync is not None:
                self.sync.wait()
            return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": params["total"] != params["expected"]}}


def _mission_rig(tmp_path, owner="alice"):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture(owner, "Theirs", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner(owner))
    world = World()
    caps = wcap.Capabilities(vstore.for_owner(owner), contracts=RUN_CONTRACTS, registry=RunRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    for component, op in RUN_CONTRACTS:
        assert caps.probe(component, op, wcall.prober(caps, invoke=world).reply, actor="code").level == "probe_passed"
    world.calls.clear()
    graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
    ledger = wr.RunLedger(missions)

    def mission(plan: dict) -> str:
        mid = missions.create(venture, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0,
                              actor="chat")
        missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
        missions.add_plan(mid, plan, actor="chat", ready=caps.ready)
        graph.materialize(mid, actor="code")
        return mid

    return SimpleNamespace(missions=missions, world=world, caps=caps, graph=graph, ledger=ledger, mission=mission,
                           runtime=wrt.Runtime(missions, graph, ledger, caps, invoke=world))


def _check(key: str, verifies: str) -> dict:
    return {"key": key, "kind": "verify", "verifies": verifies, "goal": f"{verifies} totals 6",
            "capabilities": [["fake_solver", "check"]],
            "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6},
                       "bind": {"total": f"{verifies}.total"}, "expect": {"field": "satisfiable", "equals": False},
                       "kind": "formal"}}


SUM = {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
       "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}
SUMMARY = {"key": "summary", "kind": "do", "goal": "Write the total", "output_schema": "total"}  # the owner's chat


def test_phase_1_every_model_and_paid_invocation_appears_in_the_mission_total(tmp_path):
    """Ledger §28 Phase 1 acceptance."""
    rig = _mission_rig(tmp_path)
    rig.world.model_calls = 2
    mid = rig.mission({"units": [SUM, _check("check-sum", "sum"), SUMMARY, _check("check-summary", "summary")],
                       "edges": []})
    rig.runtime.run(mid)
    rig.runtime.submit(mid, "summary", {"total": 6})
    assert rig.runtime.run(mid)["completion_state"] == wc.VERIFIED
    runs, entries = rig.ledger.runs(mid), rig.ledger.cost_entries(mid)
    sent = [f"{r['run_id']}#{i}" for r in runs for i, c in enumerate(json.loads(r["calls_json"]))
            if c["refused"] is False]
    assert sorted(sent) == sorted(e.capability_invocation_id for e in entries if e.source != "owner_session")
    assert sorted((e.resource_name, e.quantity) for e in entries if e.category == "model_inference") == [
        ("fake_ledger.total", 2), ("owner's chat session", 1)]
    found = rig.ledger.cost_total(mid)
    assert (found.entries, found.unknown_entries, found.amount_minor_units) == (4, 2, None)  # never 0
    assert sum(rig.ledger.cost_total(mid, run_id=r["run_id"]).entries for r in runs) == found.entries
    assert rig.ledger.cost_total(mid, binding_only=True).unknown_entries == 1  # the owner's session binds no budget
    assert rig.ledger.runs(mid, "summary")[0]["cost_source"] == "unknown"


def test_one_tenants_model_calls_are_never_charged_to_another(tmp_path):
    barrier = threading.Barrier(2, timeout=30)
    a, b = _mission_rig(tmp_path / "a", owner="alice"), _mission_rig(tmp_path / "b", owner="bob")
    a.world.model_calls, b.world.model_calls = 3, 5
    a.world.sync = b.world.sync = barrier
    plan = {"units": [SUM, _check("check-sum", "sum")], "edges": []}
    mids = {"a": a.mission(plan), "b": b.mission(plan)}
    threads = [threading.Thread(target=rig.runtime.run, args=(mids[name],)) for name, rig in (("a", a), ("b", b))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for name, rig, own in (("a", a, 3), ("b", b, 5)):
        charged = [e.quantity for e in rig.ledger.cost_entries(mids[name]) if e.category == "model_inference"]
        assert charged == [own]
        assert rig.ledger.runs(mids[name], "sum")[0]["model_calls"] >= own  # the process-wide count may see both


def test_each_attempt_records_what_it_read_and_wrote_with_tokens_unknown(tmp_path):
    rig = _mission_rig(tmp_path)
    mid = rig.mission({"units": [SUM, _check("check-sum", "sum")], "edges": []})
    rig.runtime.run(mid)
    closed = {e["detail"]["unit"]: e["detail"] for e in rig.missions.events(mid, kind="run.closed")}
    assert closed["sum"]["context"]["output_bytes"] > 0
    assert closed["check-sum"]["context"]["retrieved_artifact_bytes"] > 0
    assert all(d["context"]["input_tokens"] is None and d["context"]["cache_hits"] is None for d in closed.values())
    assert (closed["sum"]["cost"]["summary"], closed["check-sum"]["cost"]["summary"]) == ("unknown", "exact")
