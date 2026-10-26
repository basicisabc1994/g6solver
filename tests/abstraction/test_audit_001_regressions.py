# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 001 (docs/transition/audits/001_abstraction.md). Written before the fixes."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import g6.abstraction.gate as gate_mod
from g6.abstraction import ConcreteDomain, Library, Product, Quotient, Sufficient
from g6.abstraction.abstractions import TRACE_GENERIC, Abstraction
from g6.abstraction.gate import replay, run_gate
from g6.abstraction.lift import liftable
from g6.abstraction.pruning import advise_pruning
from g6.abstraction.robustness import bootstrap_replay

_UNIT = {"x": {"kind": "float", "low": 0.0, "high": 1.0}}


def _line(points: list[tuple[float, float]]) -> ConcreteDomain:
    return ConcreteDomain.from_rows([{"x": x, "score": s} for x, s in points], objective="score",
                                    maximise=True, param_spec=_UNIT, name="line")


def _smooth(n: int = 40) -> ConcreteDomain:
    pts = [(i / (n - 1), 1.0 - abs(i / (n - 1) - 0.7)) for i in range(n)]
    return _line(pts)


# T-01 / F-01 — a skipped evaluation's score must never enter the counterfactual model.
def test_replay_does_not_learn_from_skipped_points() -> None:
    dom = _line([(0.0, 10), (0.0, 10), (0.0, 10), (0.25, 5), (0.50, 4), (0.75, 3)])
    rep = replay(Quotient(radius=0.30, lipschitz=0.0), dom, budget=4)
    assert 3 in rep.skipped_indices        # x=0.25 is pruned using only the x=0 observations
    assert 4 in rep.evaluated_indices      # so x=0.50 is uncovered and must be evaluated
    assert set(rep.evaluated_indices).isdisjoint(rep.skipped_indices)


# T-02 / F-02 — an aborted mandatory check cannot yield a passing gate.
def test_aborted_property_check_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("injected")

    monkeypatch.setattr(gate_mod, "_subdomain", boom)
    rep = run_gate(Quotient(radius=0.2, lipschitz=5.0), _smooth(), max_examples=5)
    assert rep.passed is False
    assert rep.checks["extensivity"] is False and rep.checks["monotonicity"] is False
    assert any("inconclusive" in n for n in rep.notes)


# T-03 / F-03 — a status read from a state file never authorises pruning by itself.
def test_loaded_verified_status_does_not_authorise_pruning(tmp_path: Path) -> None:
    dom = _smooth()
    lib = Library(k_permutations=4)
    entry = lib.evaluate(Quotient(radius=0.2, lipschitz=5.0), dom, budget=20)
    path = tmp_path / "state.json"
    lib.save(str(path), {dom.name: dom})
    state = json.loads(path.read_text())
    for e in state["library"]["entries"]:
        e["status"] = "verified"  # hand-edited
    path.write_text(json.dumps(state))
    lib2, doms = Library.load(str(path))
    advice = advise_pruning(lib2, entry.entry_id, doms[dom.name], [{"x": 0.7}])
    assert advice["may_prune"] is False
    assert all(not row["prune"] for row in advice["candidates"])
    assert any("revalidate" in n for n in advice["notes"])


# T-04 / F-04 — non-finite objectives are refused on every ingress.
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_append_refuses_non_finite_objective(bad: float) -> None:
    dom = _smooth(8)
    with pytest.raises(ValueError):
        dom.append({"x": 0.5}, bad)


def test_state_file_with_non_finite_objective_is_refused(tmp_path: Path) -> None:
    dom = _smooth(8)
    lib = Library(k_permutations=4)
    path = tmp_path / "state.json"
    lib.save(str(path), {dom.name: dom})
    state = json.loads(path.read_text())
    state["domains"][dom.name]["records"][0]["objective"] = "NaN"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        Library.load(str(path))


# F-05 — the permutation statistic is labelled as what it is.
def test_permutation_statistic_is_not_called_a_confidence_bound() -> None:
    report = bootstrap_replay(Quotient(radius=0.2, lipschitz=5.0), _smooth(), budget=20, k_permutations=4).to_dict()
    assert report["lcb_interpretation"] == "permutation_order_quantile"


# T-05 / F-06 — an out-of-range candidate is never pruned, even next to the data.
def test_out_of_range_candidate_is_never_pruned() -> None:
    dom = _smooth()
    lib = Library(k_permutations=4)
    entry = lib.evaluate(Quotient(radius=0.2, lipschitz=0.0), dom, budget=20)
    entry.status = "checked"  # evaluated in this session, so its status is trusted
    advice = advise_pruning(lib, entry.entry_id, dom, [{"x": 0.02}, {"x": 1.02}], incumbent=10.0)
    in_range, out_of_range = advice["candidates"]
    assert in_range["prune"] is True       # control: the guard below is what stops the second one
    assert out_of_range["prune"] is False
    assert any("out_of_range" in g for g in out_of_range["guards"])


# T-06 / F-07 — refinement targets the child whose bound the counterexample violated.
class _Stub(Abstraction):
    KIND = "stub"
    signature = TRACE_GENERIC

    def __init__(self, b: float) -> None:
        super().__init__()
        self.b = b
        self.refined = False

    def bound(self, config):  # noqa: ANN001
        return self.b

    def refine(self, cex_config, cex_score=None):  # noqa: ANN001
        self.refined = True
        return self


def test_product_refines_the_violated_child() -> None:
    left, right = _Stub(10.0), _Stub(2.0)  # left conservative, right binding and unsound
    Product(left, right).refine({"x": 0.5}, cex_score=5.0)
    assert right.refined is True
    assert left.refined is False


# T-07 / F-08 — liftability depends on the target schema, not only a signature bit.
def test_liftable_checks_dimensions_against_meta_schema() -> None:
    meta = ConcreteDomain.from_rows(
        [{"radius": 0.1 * i, "bins": i, "objective": float(i)} for i in range(1, 6)], objective="objective"
    )
    assert liftable(Sufficient(dim="lr")) is True              # structurally eligible
    assert liftable(Sufficient(dim="lr"), meta) is False       # but not instantiable on this meta-domain
    assert liftable(Quotient(radius=0.2), meta) is True


# Found while fixing F-01: Product._absorb was a no-op, so record-by-record replay never taught it.
def test_product_learns_during_replay() -> None:
    dom = _line([(0.0, 10), (0.0, 10), (0.0, 10), (0.05, 1), (0.9, 9), (0.95, 9)])
    prod = Product(Quotient(radius=0.2, lipschitz=0.0), Quotient(radius=0.2, lipschitz=0.0))
    rep = replay(prod, dom, budget=6)
    # x=0.05 is covered by the evaluated x=0 cell (bound 10 <= incumbent 10); x=0.95 can only be
    # skipped because the product absorbed the evaluated x=0.9 (bound 9 <= 10).
    assert rep.skipped_indices == [3, 5]
    assert rep.evaluated_indices == [0, 1, 2, 4]
