# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests of the reflective abstraction harness."""
from pathlib import Path

import g6.abstraction as _g6_abstraction

import pytest

from g6.abstraction.abstractions import (Product, Project, Quotient,
                                          Sufficient, from_spec)
from g6.abstraction.cegar import find_counterexamples, refine_until
from g6.abstraction.domain import ConcreteDomain
from g6.abstraction.gate import replay, run_gate
from g6.abstraction.library import Library
from g6.abstraction.lift import lift, liftable, meta_domain
from g6.abstraction.mdl import score as mdl_score

DATA = str(Path(_g6_abstraction.__file__).parent / "examples" / "hyperparam_trace.csv")


@pytest.fixture(scope="module")
def dom():
    return ConcreteDomain.from_csv(DATA, objective="val_bpb",
                                   maximise=False, name="hp")


# ---------------- domain ---------------- #
def test_domain_load(dom):
    assert len(dom.records) == 120
    assert set(dom.param_names) == {"lr", "batch", "depth", "wd"}
    d = dom.describe()
    assert d["best_score"] > d["worst_score"]


def test_vector_normalised(dom):
    v = dom.vector(dom.records[0].config)
    assert all(0.0 <= x <= 1.0 for x in v)


def test_a0_append_validates(dom):
    with pytest.raises(ValueError):
        dom.append({"lr": "not-a-number", "batch": 32, "depth": 3,
                    "wd": 1e-4}, 1.0)


# ---------------- gate: sound mechanisms pass ---------------- #
def test_gate_quotient_generous_lipschitz(dom):
    a = Quotient(radius=0.15, lipschitz=5.0)
    rep = run_gate(a, dom)
    assert rep.checks["extensivity"]
    assert rep.checks["monotonicity"]
    assert rep.checks["bound_soundness"]
    assert rep.passed


def test_gate_catches_overclaimed_bound(dom):
    # lipschitz=0 claims the ball's best is a true upper bound: false here.
    a = Quotient(radius=0.4, lipschitz=0.0)
    rep = run_gate(a, dom)
    assert not rep.checks["bound_soundness"]
    assert any(c["kind"] == "bound" for c in rep.counterexamples)


def test_gate_sufficient_trivially_sound(dom):
    rep = run_gate(Sufficient(dim="lr", arms=4), dom)
    assert rep.passed  # bound = +inf, never over-claims


# ---------------- product = Proposition 1 ---------------- #
def test_product_bound_tightens(dom):
    left = Quotient(radius=0.2, lipschitz=5.0).fit(dom)
    r = Project(dims=["lr", "batch"], bins=3, lipschitz=5.0).fit(dom)
    p = Product(Quotient(radius=0.2, lipschitz=5.0),
                Project(dims=["lr", "batch"], bins=3, lipschitz=5.0)).fit(dom)
    for rec in dom.records[:20]:
        assert p.bound(rec.config) <= min(left.bound(rec.config),
                                          r.bound(rec.config)) + 1e-9


def test_spec_roundtrip(dom):
    spec = {"kind": "product",
            "left": {"kind": "quotient", "radius": 0.1, "lipschitz": 3.0,
                     "metric": "linf"},
            "right": {"kind": "sufficient", "dim": "lr", "arms": 4}}
    a = from_spec(spec)
    assert from_spec(a.spec()).spec() == a.spec()
    assert a.signature == "trace-generic"


# ---------------- replay ---------------- #
def test_replay_no_lookahead_and_budget(dom):
    rep = replay(Quotient(radius=0.15, lipschitz=5.0), dom, budget=40)
    assert rep.evals_used <= 40
    assert rep.horizon_reached <= len(dom.records)
    # generous lipschitz -> sound pruning -> no regret events
    assert rep.regret_events == []
    assert rep.best_with >= rep.best_without - 1e-9  # never worse (graceful)


def test_replay_detects_regret_for_reckless_pruner(dom):
    rep = replay(Quotient(radius=0.5, lipschitz=0.0), dom, budget=40)
    assert rep.evals_saved > 0
    assert len(rep.regret_events) > 0  # reckless pruning discards optima


def test_replay_reorder_gain_reported(dom):
    rep = replay(Sufficient(dim="lr", arms=4), dom, budget=30)
    assert rep.reorder_gain is not None


# ---------------- MDL ---------------- #
def test_mdl_compression_positive_for_structured_mechanism(dom):
    m = mdl_score(Project(dims=["lr", "batch"], bins=4, lipschitz=5.0), dom)
    assert m.raw_bits > 0 and m.coded_bits > 0
    assert m.compression > 1.0  # trace narrates shorter under the abstraction


# ---------------- CEGAR ---------------- #
def test_cegar_refines_unsound_to_sound_or_progress(dom):
    res = refine_until(Quotient(radius=0.4, lipschitz=0.5), dom,
                       max_iterations=6, budget=40)
    first = res.history[0]["n_soundness"]
    assert first > 0
    assert res.remaining_soundness_cexs <= first  # monotone progress


def test_precision_cexs_found_for_coarse_cells(dom):
    rep = find_counterexamples(Project(dims=["depth"], bins=2,
                                       lipschitz=10.0), dom, budget=40)
    assert len(rep.precision) > 0  # depth alone cannot separate scores


# ---------------- library / (BLP) ---------------- #
def test_library_clamps_unsound(dom):
    lib = Library(k_permutations=6)
    bad = lib.evaluate(Quotient(radius=0.5, lipschitz=0.0), dom, budget=40)
    assert bad.status == "rejected" and bad.blp_score <= 0
    # Sound-but-inert (generous lipschitz never prunes): provisional, not
    # checked — the richer lattice names this state instead of hiding it.
    good = lib.evaluate(Quotient(radius=0.15, lipschitz=5.0), dom, budget=40)
    assert good.status in ("checked", "provisional")
    board = lib.leaderboard()
    assert board[0]["entry_id"] == good.entry_id  # status-first ordering


# ---------------- lift ---------------- #
def test_lift_builds_meta_domain_and_applies(dom):
    lib = Library(k_permutations=4)
    for r_ in (0.1, 0.15, 0.2, 0.3):
        lib.evaluate(Quotient(radius=r_, lipschitz=5.0), dom, budget=40)
    # keep meta-domain construction fast in tests
    lib.k_permutations = 4
    for b in (3, 5):
        lib.evaluate(Project(dims=["lr", "batch"], bins=b, lipschitz=5.0),
                     dom, budget=40)
    base = Quotient(radius=0.2, lipschitz=2.0)
    assert liftable(base)
    lifted, meta = lift(base, lib)
    assert meta.name.startswith("meta(")
    assert len(meta.records) >= 6
    # meta-Tabu: a spec identical to an evaluated one falls in a ball
    row = meta.records[0].config
    assert lifted.covers(row)


def test_lift_rejects_too_small_library(dom):
    lib = Library(k_permutations=4)
    lib.evaluate(Quotient(radius=0.1, lipschitz=5.0), dom, budget=40)
    with pytest.raises(ValueError):
        meta_domain(lib)
