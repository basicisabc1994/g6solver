# SPDX-License-Identifier: Apache-2.0
"""Tests for the v0.3 remediations: the tighten operator, supersession,
dedup, the closed self_debug loop, deterministic gating, atomic persistence,
and load-time spec revalidation."""
from pathlib import Path

import g6.abstraction as _g6_abstraction
import json
import os
import shutil

import pytest

from g6.abstraction import server as S
from g6.abstraction.abstractions import Quotient, Sufficient
from g6.abstraction.cegar import tighten_until
from g6.abstraction.diagnostics import diagnose
from g6.abstraction.domain import ConcreteDomain
from g6.abstraction.gate import run_gate
from g6.abstraction.library import Library

DATA = str(Path(_g6_abstraction.__file__).parent / "examples" / "hyperparam_trace.csv")


def call(fn, **kw):
    return json.loads(getattr(fn, "fn", fn)(**kw))


@pytest.fixture(scope="module")
def dom():
    return ConcreteDomain.from_csv(DATA, objective="val_bpb",
                                   maximise=False, name="hp")


@pytest.fixture(autouse=True)
def clean_server_state(monkeypatch, tmp_path):
    S._domains.clear()
    S._library = Library(k_permutations=6)
    # The server's file tools run only when its operator allows them, inside the directory they declare.
    monkeypatch.setenv("G6_MCP_SENSITIVE", "abstraction")
    monkeypatch.setenv("ABSTRACTION_MCP_DIR", str(tmp_path))
    monkeypatch.delenv("ABSTRACTION_MCP_STATE", raising=False)
    shutil.copy(DATA, tmp_path / "hyperparam_trace.csv")
    yield


# ---------------- tighten: closes the inert-remediation gap ------------ #
def test_tighten_converts_slack_to_pruning_power(dom):
    res = tighten_until(Quotient(radius=0.15, lipschitz=8.0), dom,
                        budget=40, k_permutations=6)
    assert res.tightened
    assert res.spec_after["lipschitz"] < 8.0
    lib = Library(k_permutations=6)
    from g6.abstraction.abstractions import from_spec
    e = lib.evaluate(from_spec(res.spec_after), dom, budget=40)
    assert e.status in ("checked", "provisional")
    assert e.robust["total_regret_events"] == 0
    assert e.replay["evals_saved"] > 0  # inert no more


def test_tighten_noop_for_guidance_mechanism(dom):
    res = tighten_until(Sufficient(dim="lr", arms=4), dom, budget=40)
    assert not res.tightened and "no lipschitz slack" in res.note


def test_tighten_redirects_unsound_to_refine(dom):
    res = tighten_until(Quotient(radius=0.5, lipschitz=0.0), dom,
                        budget=40, k_permutations=6)
    assert not res.tightened and "refine" in res.note


# ---------------- dedup + supersession --------------------------------- #
def test_dedup_same_spec_same_trace(dom):
    lib = Library(k_permutations=4)
    e1 = lib.evaluate(Quotient(radius=0.18, lipschitz=1.8), dom, budget=40)
    e2 = lib.evaluate(Quotient(radius=0.18, lipschitz=1.8), dom, budget=40)
    assert e1.entry_id == e2.entry_id
    assert lib.selection_pressure()["hp"] == 1  # k not inflated
    e3 = lib.evaluate(Quotient(radius=0.18, lipschitz=1.8), dom, budget=40,
                      force=True)
    assert e3.entry_id != e1.entry_id  # explicit re-evaluation still possible


def test_supersession_silences_diagnose(dom):
    lib = Library(k_permutations=4)
    inert = lib.evaluate(Quotient(radius=0.15, lipschitz=8.0), dom, budget=40)
    d1 = diagnose(lib, {"hp": dom}).to_dict()
    assert any(f["code"] == "INERT_MECHANISM" and f["subject"] == inert.entry_id
               for f in d1["findings"])
    child = lib.evaluate(Quotient(radius=0.15, lipschitz=2.3), dom, budget=40,
                         parent=inert.entry_id, supersedes=inert.entry_id)
    assert lib.get(inert.entry_id).superseded_by == child.entry_id
    d2 = diagnose(lib, {"hp": dom}).to_dict()
    assert not any(f["subject"] == inert.entry_id for f in d2["findings"])
    assert d2["summary"]["n_superseded"] == 1


# ---------------- the closed self_debug loop ---------------------------- #
def test_self_debug_closes_the_loop():
    call(S.load_dataset, path="hyperparam_trace.csv", objective="val_bpb",
         maximise=False, name="hp")
    inert = call(S.evaluate_abstraction, domain="hp", budget=40,
                 spec_json=json.dumps({"kind": "quotient", "radius": 0.15,
                                       "lipschitz": 8.0}))
    _reckless = call(S.evaluate_abstraction, domain="hp", budget=40,
                    spec_json=json.dumps({"kind": "quotient", "radius": 0.5,
                                          "lipschitz": 0.0}))
    r = call(S.self_debug, max_rounds=3, budget=40)
    assert r["n_actions"] >= 1
    assert r["outcome"] in ("healthy", "converged", "budget_exhausted")
    # the inert entry must have been superseded by a tightened child
    assert S._library.get(inert["entry_id"]).superseded_by is not None
    # no finding may still target a superseded entry
    frontier_subjects = {f[2] for f in r["final"]["findings"]}
    assert inert["entry_id"] not in frontier_subjects
    # a checked mechanism should now exist on the frontier
    board = call(S.leaderboard)["leaderboard"]
    assert any(row["status"] == "checked" for row in board)


def test_self_debug_remediation_tabu():
    call(S.load_dataset, path="hyperparam_trace.csv", objective="val_bpb",
         maximise=False, name="hp")
    call(S.evaluate_abstraction, domain="hp", budget=40,
         spec_json=json.dumps({"kind": "sufficient", "dim": "depth",
                               "arms": 4}))
    r = call(S.self_debug, max_rounds=4, budget=40)
    pairs = [(a["tool"], a["entry_id"])
             for rd in r["rounds"] for a in rd["actions"]]
    assert len(pairs) == len(set(pairs))  # no (tool, entry) repeated


# ---------------- determinism ------------------------------------------- #
def test_gate_verdicts_reproducible(dom):
    a = Quotient(radius=0.4, lipschitz=0.0)
    r1 = run_gate(a, dom, max_examples=25)
    r2 = run_gate(Quotient(radius=0.4, lipschitz=0.0), dom, max_examples=25)
    assert r1.checks == r2.checks
    assert len(r1.counterexamples) == len(r2.counterexamples)


# ---------------- persistence hardening --------------------------------- #
def test_atomic_save_leaves_no_temp(tmp_path, dom):
    lib = Library(k_permutations=4)
    lib.evaluate(Quotient(radius=0.18, lipschitz=1.8), dom, budget=40)
    path = str(tmp_path / "state.json")
    lib.save(path, {"hp": dom})
    assert os.path.exists(path)
    assert not [f for f in os.listdir(tmp_path) if ".tmp." in f]
    lib2, _ = Library.load(path)
    assert lib2.leaderboard() == lib.leaderboard()


def test_load_rejects_wrong_version(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"version": 99, "library": {}, "domains": {}}))
    with pytest.raises(ValueError):
        Library.load(str(p))


def test_load_flags_spec_drift(tmp_path, dom):
    lib = Library(k_permutations=4)
    lib.evaluate(Quotient(radius=0.18, lipschitz=1.8), dom, budget=40)
    path = str(tmp_path / "state.json")
    lib.save(path, {"hp": dom})
    state = json.load(open(path))
    ent = state["library"]["entries"][0]
    ent["spec"]["bins"] = None
    ent["spec"]["kind"] = "project"
    ent["spec"]["dims"] = []
    json.dump(state, open(path, "w"))
    lib2, doms = Library.load(path)
    assert lib2.invalid_specs == [ent["entry_id"]]
    d = diagnose(lib2, doms).to_dict()
    assert any(f["code"] == "INVALID_SPEC" for f in d["findings"])
    assert d["summary"]["healthy"] is False
