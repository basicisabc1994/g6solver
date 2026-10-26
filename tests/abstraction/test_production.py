# SPDX-License-Identifier: Apache-2.0
"""Tests for the production remediations: spec validation, incremental
fitting, bootstrap robustness, degeneracy, staleness, guards, persistence,
self-diagnosis, and performance."""
from pathlib import Path

import g6.abstraction as _g6_abstraction
import json
import math
import random
import shutil
import time

import pytest

from g6.abstraction import server as S
from g6.abstraction.abstractions import (Product, Project, Quotient,
                                          SpecError, Sufficient, from_spec)
from g6.abstraction.diagnostics import diagnose
from g6.abstraction.domain import ConcreteDomain
from g6.abstraction.library import Library
from g6.abstraction.robustness import bootstrap_replay

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


# ---------------- spec validation (remediation: spec bombs) ----------- #
def test_spec_limits():
    with pytest.raises(SpecError):
        from_spec({"kind": "project", "dims": ["x"], "bins": 10 ** 6})
    with pytest.raises(SpecError):
        from_spec({"kind": "quotient", "radius": 1e-9})
    with pytest.raises(SpecError):
        from_spec({"kind": "sufficient", "dim": "x", "arms": 1})
    s = {"kind": "quotient", "radius": 0.2}
    for _ in range(5):
        s = {"kind": "product", "left": s,
             "right": {"kind": "quotient", "radius": 0.2}}
    with pytest.raises(SpecError):
        from_spec(s)
    with pytest.raises(SpecError):
        from_spec({"kind": "nonsense"})


# ---------------- incremental fit equivalence -------------------------- #
@pytest.mark.parametrize("mk", [
    lambda: Quotient(radius=0.15, lipschitz=2.0),
    lambda: Project(dims=["lr", "batch"], bins=4, lipschitz=2.0),
    lambda: Sufficient(dim="lr", arms=4),
    lambda: Product(Quotient(radius=0.2, lipschitz=2.0),
                    Sufficient(dim="lr", arms=4)),
])
def test_incremental_equals_fresh(dom, mk):
    inc = mk()
    for i in (10, 40, 80, 120):
        inc.fit(dom, upto=i)          # incremental path
        fresh = mk().fit(dom, upto=i)  # from-scratch path
        for rec in dom.records[:i:7]:
            assert inc.covers(rec.config) == fresh.covers(rec.config)
            bi, bf = inc.bound(rec.config), fresh.bound(rec.config)
            assert (bi == bf) or math.isclose(bi, bf, rel_tol=1e-9)


# ---------------- bootstrap robustness (winner's curse, ordering) ------ #
def test_bootstrap_rejects_order_lucky_mechanism(dom):
    rob = bootstrap_replay(Quotient(radius=0.5, lipschitz=0.0), dom,
                           budget=40, k_permutations=8)
    assert not rob.sound_robust and rob.total_regret_events > 0


def test_selection_adjustment_tightens_with_candidates(dom):
    a = Quotient(radius=0.18, lipschitz=1.8)
    r1 = bootstrap_replay(a, dom, budget=40, k_permutations=10,
                          n_candidates_tried=1)
    r9 = bootstrap_replay(a, dom, budget=40, k_permutations=10,
                          n_candidates_tried=50)
    assert r9.lcb <= r1.lcb  # more shopping -> stricter lower bound


def test_pure_noise_candidates_rarely_verified():
    # A domain whose objective is pure noise: no abstraction should earn
    # 'checked' across many candidate proposals (winner's-curse defence).
    rng = random.Random(0)
    rows = [{"x": rng.random(), "y": rng.random(),
             "objective": rng.gauss(0, 1)} for _ in range(60)]
    noise = ConcreteDomain.from_rows(rows, objective="objective", name="noise")
    lib = Library(k_permutations=8)
    checked = 0
    for i in range(8):
        e = lib.evaluate(Quotient(radius=0.1 + 0.05 * i, lipschitz=0.5 * i),
                         noise, budget=25)
        checked += e.status == "checked"
    assert checked == 0


# ---------------- degeneracy ------------------------------------------- #
def test_degenerate_single_cell_clamped(dom):
    lib = Library(k_permutations=4)
    e = lib.evaluate(Quotient(radius=2.0, lipschitz=10.0), dom, budget=40)
    assert e.status == "degenerate" and e.blp_score <= 0


# ---------------- staleness + guards (server level) -------------------- #
def _load(name="hp"):
    return call(S.load_dataset, path="hyperparam_trace.csv", objective="val_bpb",
                maximise=False, name=name)


def test_staleness_refuses_pruning_then_revalidate():
    _load()
    e = call(S.evaluate_abstraction, domain="hp", budget=40,
             spec_json=json.dumps({"kind": "quotient", "radius": 0.18,
                                   "lipschitz": 1.8}))
    dom = S._domains["hp"]
    n0 = len(dom.records)
    for i in range(n0 + 5):  # grow trace by >100%
        rec = dom.records[i % n0]
        call(S.record_evaluation, domain="hp",
             config_json=json.dumps(rec.config), objective=rec.objective)
    r = call(S.advise_pruning, entry_id=e["entry_id"],
             candidates_json=json.dumps([dom.records[0].config]))
    assert any("stale" in n for n in r["notes"])
    assert all(not c["prune"] for c in r["candidates"])
    rv = call(S.revalidate, entry_id=e["entry_id"], budget=40)
    assert rv["new_entry"]["parent"] == e["entry_id"]
    assert rv["new_entry"]["trace_len"] == len(dom.records)


def test_support_guard_blocks_extrapolated_pruning():
    _load()
    e = call(S.evaluate_abstraction, domain="hp", budget=40,
             spec_json=json.dumps({"kind": "quotient", "radius": 0.18,
                                   "lipschitz": 1.8}))
    far = {"lr": 0.09999, "batch": 256, "depth": 12, "wd": 0.0099}
    r = call(S.advise_pruning, entry_id=e["entry_id"],
             candidates_json=json.dumps([far]))
    c = r["candidates"][0]
    # This candidate is also outside the declared parameter ranges, so the out-of-range guard
    # (audit 001, F-06) refuses it before the support guard is reached. Either way: no pruning.
    assert not c["prune"]
    assert any("insufficient_support" in g or "out_of_range" in g for g in c["guards"])


def test_rejected_entry_never_prunes():
    _load()
    e = call(S.evaluate_abstraction, domain="hp", budget=40,
             spec_json=json.dumps({"kind": "quotient", "radius": 0.5,
                                   "lipschitz": 0.0}))
    assert e["status"] == "rejected"
    cfg = S._domains["hp"].records[0].config
    r = call(S.advise_pruning, entry_id=e["entry_id"],
             candidates_json=json.dumps([cfg]))
    assert all(not c["prune"] for c in r["candidates"])


# ---------------- dataset validation ----------------------------------- #
def test_nan_objective_rejected(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("x,objective\n1.0,nan\n2.0,1.0\n3.0,2.0\n4.0,3.0\n")
    r = call(S.load_dataset, path=str(p), objective="objective", name="bad")
    assert "error" in r and "NaN" in r["error"]


# ---------------- persistence ------------------------------------------ #
def test_persistence_roundtrip(tmp_path, dom):
    lib = Library(k_permutations=4)
    lib.evaluate(Quotient(radius=0.18, lipschitz=1.8), dom, budget=40)
    lib.evaluate(Project(dims=["lr", "batch"], bins=4, lipschitz=3.0),
                 dom, budget=40)
    path = str(tmp_path / "state.json")
    lib.save(path, {"hp": dom})
    lib2, doms = Library.load(path)
    assert lib.leaderboard() == lib2.leaderboard()
    assert len(doms["hp"].records) == len(dom.records)
    assert lib2.selection_pressure() == lib.selection_pressure()


# ---------------- self-diagnosis --------------------------------------- #
def test_diagnose_flags_inert_and_no_verified(dom):
    lib = Library(k_permutations=4)
    # sound but inert (bounds never bind at lipschitz=8)
    lib.evaluate(Quotient(radius=0.15, lipschitz=8.0), dom, budget=40)
    d = diagnose(lib, {"hp": dom}).to_dict()
    codes = {f["code"] for f in d["findings"]}
    assert "INERT_MECHANISM" in codes
    inert = next(f for f in d["findings"] if f["code"] == "INERT_MECHANISM")
    assert inert["remediation"]["tool"] == "tighten_abstraction"


def test_diagnose_flags_stale_and_health(dom):
    lib = Library(k_permutations=4)
    _ = lib.evaluate(Quotient(radius=0.18, lipschitz=1.8), dom, budget=40)
    grown = ConcreteDomain(params=dom.params,
                           records=dom.records + dom.records,
                           objective_name=dom.objective_name,
                           maximise=dom.maximise, name=dom.name)
    d = diagnose(lib, {"hp": grown}).to_dict()
    codes = {f["code"] for f in d["findings"]}
    assert "STALE_ENTRY" in codes
    assert d["summary"]["healthy"] is False  # 'act' severity present


def test_diagnose_flags_rejected_only_domain(dom):
    lib = Library(k_permutations=4)
    lib.evaluate(Quotient(radius=0.5, lipschitz=0.0), dom, budget=40)
    d = diagnose(lib, {"hp": dom}).to_dict()
    codes = {f["code"] for f in d["findings"]}
    assert "NO_CHECKED" in codes
    f = next(x for x in d["findings"] if x["code"] == "NO_CHECKED")
    assert f["remediation"]["tool"] == "refine_abstraction"


# ---------------- performance (remediation: O(n^2) refits) -------------- #
def test_large_trace_evaluation_fast():
    rng = random.Random(1)
    rows = []
    for _ in range(1500):
        x, y = rng.random(), rng.random()
        rows.append({"x": x, "y": y,
                     "objective": math.sin(6 * x) + 0.5 * y
                     + rng.gauss(0, 0.05)})
    big = ConcreteDomain.from_rows(rows, objective="objective", name="big")
    lib = Library(k_permutations=4)
    t0 = time.time()
    e = lib.evaluate(Quotient(radius=0.08, lipschitz=3.0), big, budget=400)
    dt = time.time() - t0
    assert dt < 25, f"evaluation took {dt:.1f}s on 1500 records"
    assert e.status in ("checked", "provisional", "rejected", "degenerate")
