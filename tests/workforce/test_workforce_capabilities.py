# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Capability readiness: exists is not ready (workforce OS plan, P0 T4).

Pinned: each (component, op) climbs declared < registered < loads < deps_installed < probe_passed,
and stops at the first rung it fails, saying why; a block that does not accept the op is refused by
name before any probe; only a probe through the door (persisted with its time, judged against its
TTL and its contract's digest, re-read on every question) reaches ``probe_passed``; ``match`` returns
nothing below it; a plan naming an unready capability is refused with the reason. ParamContracts
refuse unknown, missing and mistyped keys. Offline, temp db, fake registry.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from g6.workforce import store
from g6.workforce import capabilities as wcap
from g6.workforce import missions as wm
from g6.workforce import plans as wp

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731


def _contract(**over) -> dict:
    body = {"component": "solver", "op": "solve", "required": {"constraints": "list"},
            "optional": {"timeout_s": "number"}, "probe_params": {"constraints": ["x > 1"]},
            "expect_field": "satisfiable", "side_effect": "read", "check": "input_model"}
    body.update(over)
    return body


class FakeRegistry:
    """name -> {"loads": bool, "installed": True | False | None}; get_class counts its calls."""

    def __init__(self, components: dict[str, dict]) -> None:
        self.components = components
        self.loads_asked = 0

    def list_all(self) -> list[str]:
        return sorted(self.components)

    def get_class(self, name: str):
        self.loads_asked += 1
        spec = self.components.get(name)
        return type(name, (), {}) if spec and spec["loads"] else None

    def describe(self, name: str) -> dict:
        spec = self.components[name]
        return {"name": name, "available": True,
                "backend": {"installed": spec["installed"], "packages": {}, "checked": "fake"}}


class Clock:
    def __init__(self) -> None:
        self.at = T0

    def __call__(self) -> datetime:
        return self.at


def _reply(**answer) -> dict:
    """The door's envelope around a component's own answer."""
    return {"ok": True, "completion_state": "qualified-draft", "degraded": False, "result": answer}


class Door:
    """A probe's invoke: records each call and answers with the reply it holds (or raises it)."""

    def __init__(self, reply) -> None:
        self.reply, self.calls = reply, []

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op, params))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def rig(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    registry = FakeRegistry({"solver": {"loads": True, "installed": True},
                             "broken": {"loads": False, "installed": True},
                             "needs_z3": {"loads": True, "installed": False}})
    contracts = {(c["component"], c["op"]): wcap.ParamContract.from_dict(c) for c in (
        _contract(), _contract(component="broken"), _contract(component="needs_z3"),
        _contract(component="absent"), _contract(op="bogus"),
        _contract(op="write", side_effect="local_write"))}
    ops = {"solver": frozenset({"solve", "write"}), "broken": None, "needs_z3": frozenset({"solve"})}
    clock = Clock()

    def caps(owner: str = "alice", **over):
        kw = {"contracts": contracts, "registry": registry, "ops_for": ops.get, "now": clock}
        kw.update(over)
        return wcap.Capabilities(vstore.for_owner(owner), **kw, authority=SCRIPTED)

    return SimpleNamespace(vstore=vstore, registry=registry, contracts=contracts, clock=clock, caps=caps)


# --- the ladder -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("component", "op", "level", "why"), [
    ("solver", "lint", "undeclared", "no ParamContract"),
    ("absent", "solve", "declared", "not registered"),
    ("broken", "solve", "registered", "does not load"),
    ("solver", "bogus", "loads", "does not accept op 'bogus'"),
    ("needs_z3", "solve", "loads", "backend"),
    ("solver", "solve", "deps_installed", "no probe"),
])
def test_each_pair_stops_at_the_first_rung_it_fails_and_says_why(rig, component, op, level, why):
    found = rig.caps().readiness(component, op)
    assert (found.level, found.component, found.op) == (level, component, op)
    assert why in found.reason
    assert not found.at_least("probe_passed")


def test_the_levels_are_ordered_as_the_spec_states():
    assert wcap.LEVELS == ("undeclared", "declared", "registered", "loads", "deps_installed", "probe_passed",
                           "measured_suitable")


def test_an_undeclared_op_set_is_left_to_the_probe(rig):
    """A block whose accepted ops cannot be read by name is not refused by name: the probe decides."""
    caps = rig.caps(ops_for=lambda name: None)
    assert caps.readiness("solver", "bogus").level == "deps_installed"


# --- probes ---------------------------------------------------------------------------------------


def test_a_passing_probe_is_persisted_with_its_time_and_reaches_probe_passed(rig):
    door = Door(_reply(satisfiable=True))
    found = rig.caps().probe("solver", "solve", door, actor="code")
    assert found.level == "probe_passed" and found.probed_at == T0.isoformat(timespec="seconds")
    assert door.calls == [("solver", "solve", {"constraints": ["x > 1"]})]
    (row,) = [dict(r) for r in rig.vstore.conn.execute("SELECT * FROM preflight_runs")]
    assert (row["component"], row["op"], row["passed"], row["actor"]) == ("solver", "solve", 1, "code")
    assert row["contract_sha"] == rig.contracts[("solver", "solve")].sha256
    assert rig.caps().readiness("solver", "solve").level == "probe_passed"  # a new instance reads the row


def test_a_probe_is_never_cached_in_process(rig):
    caps = rig.caps()
    caps.probe("solver", "solve", Door(_reply(satisfiable=True)), actor="code")
    assert caps.readiness("solver", "solve").level == "probe_passed"
    caps.probe("solver", "solve", Door(_reply(error="z3 crashed")), actor="code")
    found = caps.readiness("solver", "solve")
    assert found.level == "deps_installed" and "last probe failed" in found.reason


def test_a_probe_expires_after_its_ttl(rig):
    caps = rig.caps(ttl=timedelta(hours=1))
    caps.probe("solver", "solve", Door(_reply(satisfiable=True)), actor="code")
    rig.clock.at = T0 + timedelta(minutes=59)
    assert caps.readiness("solver", "solve").level == "probe_passed"
    rig.clock.at = T0 + timedelta(hours=1, seconds=1)
    found = caps.readiness("solver", "solve")
    assert found.level == "deps_installed" and "expired" in found.reason


@pytest.mark.parametrize(("reply", "why"), [
    (_reply(satisfiable=True, degraded=False) | {"ok": False, "error": "boom"}, "blocked"),
    (_reply(satisfiable=True, degraded=True), "degraded"),
    (_reply(answer=42), "satisfiable"),
    ({"ok": True, "satisfiable": True, "result": {"answer": 1}}, "satisfiable"),
    (RuntimeError("socket closed"), "RuntimeError"),
    ("not a reply", "blocked"),
])
def test_a_probe_that_blocks_raises_or_lacks_the_expected_field_fails(rig, reply, why):
    """Including the expected field on the ENVELOPE only: the component's own answer must carry it."""
    found = rig.caps().probe("solver", "solve", Door(reply), actor="code")
    assert found.level == "deps_installed" and why in found.reason
    assert rig.vstore.conn.execute("SELECT passed FROM preflight_runs").fetchone()[0] == 0


def test_a_changed_contract_does_not_inherit_an_old_probe(rig):
    rig.caps().probe("solver", "solve", Door(_reply(satisfiable=True)), actor="code")
    changed = dict(rig.contracts)
    changed[("solver", "solve")] = wcap.ParamContract.from_dict(_contract(optional={}))
    assert rig.caps(contracts=changed).readiness("solver", "solve").level == "deps_installed"


@pytest.mark.parametrize(("component", "op"), [("broken", "solve"), ("solver", "bogus"), ("solver", "write")])
def test_a_probe_is_refused_without_a_call_below_deps_installed_or_for_a_writing_op(rig, component, op):
    door = Door(_reply(satisfiable=True))
    found = rig.caps().probe(component, op, door, actor="code")
    assert door.calls == [] and found.level != "probe_passed"
    assert rig.vstore.conn.execute("SELECT COUNT(*) FROM preflight_runs").fetchone()[0] == 0


def test_a_writing_op_says_its_probe_needs_approval(rig):
    found = rig.caps().readiness("solver", "write")
    assert found.level == "deps_installed" and "local_write" in found.reason


def test_probe_rows_are_append_only(rig):
    rig.caps().probe("solver", "solve", Door(_reply(satisfiable=True)), actor="code")
    conn = rig.vstore.conn
    for sql in ("UPDATE preflight_runs SET passed=0", "DELETE FROM preflight_runs",
                "INSERT OR REPLACE INTO preflight_runs SELECT * FROM preflight_runs"):
        with pytest.raises(sqlite3.DatabaseError), conn:
            conn.execute(sql)


def test_a_probe_needs_a_known_actor(rig):
    with pytest.raises(ValueError):
        rig.caps().probe("solver", "solve", Door(_reply(satisfiable=True)), actor="anyone")


# --- match and plans ------------------------------------------------------------------------------


def test_match_returns_only_probe_passed_pairs(rig):
    caps = rig.caps()
    pairs = [("solver", "solve"), ("broken", "solve"), ("solver", "bogus"), ("solver", "lint")]
    assert caps.match(pairs) == []
    caps.probe("solver", "solve", Door(_reply(satisfiable=True)), actor="code")
    assert [(r.component, r.op) for r in caps.match(pairs)] == [("solver", "solve")]
    assert caps.ready("solver", "solve") is None and "does not load" in caps.ready("broken", "solve")


def test_a_plan_naming_an_unready_capability_is_refused_with_the_reason(rig):
    venture = rig.vstore.create_venture("alice", "Alice's", budget_aud_month=0, model_cap_usd_month=0,
                                        task_cap_usd=0, max_turns=5)["venture_id"]
    missions = wm.MissionStore(rig.vstore.for_owner("alice"))
    mid = missions.create(venture, objective="Prove the totals", risk_tier="R1", budget_cents=0, actor="chat")
    missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
    plan = {"units": [
        {"key": "solve", "kind": "do", "goal": "Check the totals", "capabilities": [["solver", "solve"]]},
        {"key": "check", "kind": "verify", "verifies": "solve", "goal": "Totals hold",
         "capabilities": [["broken", "solve"]]}], "edges": []}
    caps = rig.caps()
    with pytest.raises(wp.PlanRejected) as refused:
        missions.add_plan(mid, plan, actor="chat", ready=caps.ready)
    text = " | ".join(refused.value.problems)
    assert "unit 'solve': capability solver.solve is not ready" in text and "no probe" in text
    assert "unit 'check': capability broken.solve is not ready" in text and "does not load" in text
    caps.probe("solver", "solve", Door(_reply(satisfiable=True)), actor="code")
    plan["units"][1]["capabilities"] = [["solver", "solve"]]
    assert missions.add_plan(mid, plan, actor="chat", ready=caps.ready) == 1


# --- ParamContracts -------------------------------------------------------------------------------


def test_a_contract_refuses_unknown_missing_and_mistyped_keys_before_dispatch():
    contract = wcap.ParamContract.from_dict(_contract())
    assert contract.violations({"constraints": ["x > 1"], "timeout_s": 2.5}) == []
    assert contract.violations({"constraints": [], "timeout_s": 3}) == []
    problems = " | ".join(contract.violations({"timeout_s": "soon", "solver": "z3", "op": "solve"}))
    assert "missing required 'constraints'" in problems and "unknown key(s) ['op', 'solver']" in problems
    assert "'timeout_s' must be number" in problems
    assert contract.violations({"constraints": "x > 1"}) == ["'constraints' must be list"]
    assert contract.violations({"constraints": [], "timeout_s": True}) == ["'timeout_s' must be number"]


@pytest.mark.parametrize("over", [
    {"side_effect": "maybe"}, {"check": "trust_me"}, {"required": {"x": "tensor"}}, {"expect_field": ""},
    {"probe_params": {}}, {"probe_params": {"constraints": ["x"], "extra": 1}}, {"notes": "hi"},
    {"required": {"op": "str"}}, {"component": "Bad Name"}, {"optional": {"constraints": "list"}},
])
def test_a_malformed_contract_is_refused(over):
    with pytest.raises(ValueError):
        wcap.ParamContract.from_dict(_contract(**over))


def test_a_partly_installed_backend_counts_only_through_the_package_the_contract_names(rig):
    rig.registry.components["multi"] = {"loads": True, "installed": "partial"}
    packages = {"formal-z3": {"installed": True}, "formal-lean": {"installed": False}}
    rig.registry.describe = lambda name: {"backend": {"installed": rig.registry.components[name]["installed"],
                                                      "packages": packages if name == "multi" else {}}}
    named = wcap.ParamContract.from_dict(_contract(component="multi", backend_package="formal-z3"))
    unnamed = wcap.ParamContract.from_dict(_contract(component="multi", op="other"))
    lean = wcap.ParamContract.from_dict(_contract(component="multi", op="lean", backend_package="formal-lean"))
    caps = rig.caps(contracts={("multi", "solve"): named, ("multi", "other"): unnamed, ("multi", "lean"): lean},
                    ops_for=lambda name: None)
    assert caps.readiness("multi", "solve").level == "deps_installed"
    assert caps.readiness("multi", "other").level == "loads" and "partly" in caps.readiness("multi", "other").reason
    assert caps.readiness("multi", "lean").level == "loads" and "formal-lean" in caps.readiness("multi", "lean").reason


# --- per-op packages (plan ruling: dependencies are judged per op, through the op's contract) ---------------------


#: The six shipped contracts' digests at a8d32d29b1, before ``packages`` existed: declaring nothing new keeps them.
SHIPPED_DIGESTS = {  # the library ships only the workforce's own ops (the archive's other four are not ported)
    # run_tests gained optional regression mode (A0-capped), E2E Brief 2, plan replicated-mapping-taco
    ("workforce", "run_tests"): "2cd3668b53b6bd43ce10426bb7995e70627debffb2d471d32b849946a999f125",
}


def test_the_six_shipped_contracts_keep_their_digests():
    """The six original contracts keep their digests; contracts promoted since (Phase W) may sit beside them."""
    shipped = wcap.load_contracts()
    assert {key: getattr(shipped.get(key), "sha256", None) for key in SHIPPED_DIGESTS} == SHIPPED_DIGESTS


def test_packages_are_declared_import_names_and_enter_the_digest_only_when_declared():
    plain = wcap.ParamContract.from_dict(_contract())
    none = wcap.ParamContract.from_dict(_contract(packages=[]))
    z3 = wcap.ParamContract.from_dict(_contract(packages=["z3", "google.cloud"]))
    assert (plain.packages, none.packages, z3.packages) == (None, (), ("z3", "google.cloud"))
    assert len({plain.sha256, none.sha256, z3.sha256}) == 3  # an empty list is a declaration too


@pytest.mark.parametrize("packages", ["z3", ["z3", "z3"], ["bad name"], ["9lives"], [""], [3], ["x" * 65], None])
def test_a_malformed_package_list_is_refused(packages):
    with pytest.raises(ValueError, match="packages is a list of distinct import names"):
        wcap.ParamContract.from_dict(_contract(packages=packages))


def test_a_declared_package_that_is_not_installed_stops_the_op_at_loads(rig):
    missing = wcap.ParamContract.from_dict(_contract(packages=["json", "definitely_not_installed_pkg"]))
    found = rig.caps(contracts={("solver", "solve"): missing}).readiness("solver", "solve")
    assert (found.level, found.reason) == (
        "loads", "solver.solve needs definitely_not_installed_pkg, which is not installed here")
    door = Door(_reply(satisfiable=True))
    rig.caps(contracts={("solver", "solve"): missing}).probe("solver", "solve", door, actor="code")
    assert door.calls == []  # below deps_installed: never probed


def test_declared_packages_replace_the_component_wide_backend_check(rig):
    """needs_z3's backend reads not installed: an op that declares the packages it needs is judged by them alone;
    an op that declares nothing keeps the component rule; a named backend package keeps its meaning."""
    declared = wcap.ParamContract.from_dict(_contract(component="needs_z3", packages=[]))
    installed = wcap.ParamContract.from_dict(_contract(component="needs_z3", packages=["json"]))
    undeclared = wcap.ParamContract.from_dict(_contract(component="needs_z3"))
    for contract, level in ((declared, "deps_installed"), (installed, "deps_installed"), (undeclared, "loads")):
        found = rig.caps(contracts={("needs_z3", "solve"): contract}).readiness("needs_z3", "solve")
        assert found.level == level, (contract.packages, found)
    named = wcap.ParamContract.from_dict(_contract(component="needs_z3", packages=[], backend_package="formal-z3"))
    found = rig.caps(contracts={("needs_z3", "solve"): named}).readiness("needs_z3", "solve")
    assert found.level == "loads" and "backend package formal-z3 is not installed" in found.reason


def test_a_draft_contract_is_never_loaded(tmp_path):
    """The drafter marks every draft ``_draft``: an unknown field, so a draft left in params/ refuses to load."""
    (tmp_path / "solver.solve.json").write_text(json.dumps({**_contract(), "_draft": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="_draft"):
        wcap.load_contracts(tmp_path)


def test_contracts_load_from_files_named_for_their_pair(tmp_path):
    (tmp_path / "solver.solve.json").write_text(json.dumps(_contract()), encoding="utf-8")
    loaded = wcap.load_contracts(tmp_path)
    assert list(loaded) == [("solver", "solve")]
    (tmp_path / "solver.other.json").write_text(json.dumps(_contract()), encoding="utf-8")
    with pytest.raises(ValueError, match="solver.other.json"):
        wcap.load_contracts(tmp_path)


# --- Codex review of T4-T6: a probe's outcome is one owner's ----------------------------------------


def test_a_probe_is_one_owners_and_never_reaches_another(rig):
    """Codex: Alice's failed probe (with her diagnostic) showed in Bob's readiness, and Bob's pass made her
    capability probe_passed. Each owner's probes are recorded and read as theirs alone."""
    alice, bob = rig.caps("alice"), rig.caps("bob")
    alice.probe("solver", "solve", Door(_reply(error="ALICE_PRIVATE_DIAGNOSTIC")), actor="code")
    seen = bob.readiness("solver", "solve")
    assert "ALICE_PRIVATE_DIAGNOSTIC" not in seen.reason and "no probe has run" in seen.reason
    bob.probe("solver", "solve", Door(_reply(satisfiable=True)), actor="code")
    assert bob.readiness("solver", "solve").level == "probe_passed"
    assert alice.readiness("solver", "solve").level == "deps_installed"  # her failed probe still stands
    assert "ALICE_PRIVATE_DIAGNOSTIC" in alice.readiness("solver", "solve").reason


def test_capabilities_are_read_for_one_owner(rig):
    with pytest.raises(TypeError, match="owner"):
        wcap.Capabilities(rig.vstore, contracts=rig.contracts, registry=rig.registry, authority=SCRIPTED)
