# SPDX-License-Identifier: Apache-2.0
"""CEGAR loop (framework section 5, Stage 2, and Proposition 2).

Counterexamples come from two sources:
  soundness  gate bound violations + replay regret events
             (a pruned config was better than the incumbent)
  precision  cells whose observed scores straddle a wide range: the
             abstraction is sound but uselessly coarse there

refine_until: repeatedly apply the abstraction's own split operator R on the
worst soundness counterexample until none remain within the iteration cap.
Progress is guaranteed while R strictly refines (Quotient halves a radius,
Project/Sufficient double resolution); the cap enforces graceful stop and
the caller compares MDL before/after to decide whether refinement earned
its added description length.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .abstractions import Abstraction, from_spec
from .domain import ConcreteDomain
from .gate import replay, run_gate
from .robustness import bootstrap_replay


@dataclass
class CexReport:
    soundness: list[dict[str, Any]] = field(default_factory=list)
    precision: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"n_soundness": len(self.soundness),
                "n_precision": len(self.precision),
                "soundness": self.soundness[:10],
                "precision": self.precision[:10]}


def find_counterexamples(abstraction: Abstraction, domain: ConcreteDomain,
                         budget: int | None = None,
                         precision_spread: float = 0.5) -> CexReport:
    rep = CexReport()

    gate = run_gate(abstraction, domain, max_examples=25)
    rep.soundness.extend(c for c in gate.counterexamples if c.get("kind") == "bound")

    rp = replay(abstraction, domain, budget=budget)
    for ev in rp.regret_events:
        rep.soundness.append({"kind": "regret", **ev})

    fitted = from_spec(abstraction.spec()).fit(domain)
    cells: dict[Any, list[float]] = {}
    for rec in domain.records:
        if fitted.covers(rec.config):
            cells.setdefault(fitted.cell_of(rec.config), []).append(
                domain.score(rec.objective))
    scores = [domain.score(r.objective) for r in domain.records]
    span = max(scores) - min(scores) if scores else 1.0
    for cell, vals in cells.items():
        if len(vals) >= 2 and span > 0 and (max(vals) - min(vals)) / span > precision_spread:
            rep.precision.append({"kind": "precision", "cell": str(cell),
                                  "spread": max(vals) - min(vals),
                                  "n_members": len(vals)})
    return rep


@dataclass
class RefineResult:
    spec_before: dict[str, Any]
    spec_after: dict[str, Any]
    iterations: int
    remaining_soundness_cexs: int
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"spec_before": self.spec_before, "spec_after": self.spec_after,
                "iterations": self.iterations,
                "remaining_soundness_cexs": self.remaining_soundness_cexs,
                "history": self.history}


def refine_until(abstraction: Abstraction, domain: ConcreteDomain,
                 max_iterations: int = 5,
                 budget: int | None = None) -> RefineResult:
    spec0 = abstraction.spec()
    current = abstraction
    history: list[dict[str, Any]] = []
    it = 0
    while it < max_iterations:
        rep = find_counterexamples(current, domain, budget=budget)
        history.append({"iteration": it,
                        "n_soundness": len(rep.soundness),
                        "n_precision": len(rep.precision)})
        if not rep.soundness:
            break
        cex = max(rep.soundness,
                  key=lambda c: c.get("score", 0) - c.get("incumbent",
                                                          c.get("bound", 0)))
        cfg = cex.get("config")
        if cfg is None:
            break
        current.fit(domain)
        current = current.refine(cfg, cex.get("score"))
        it += 1
    final = find_counterexamples(current, domain, budget=budget)
    return RefineResult(spec_before=spec0, spec_after=current.spec(),
                        iterations=it,
                        remaining_soundness_cexs=len(final.soundness),
                        history=history)


# ---------------------------------------------------------------------- #
# Tighten: the dual of refine (remediation for INERT mechanisms).
# ---------------------------------------------------------------------- #
# CEGAR's refine LOOSENS toward soundness (raise lipschitz / shrink cells)
# driven by soundness counterexamples. An inert mechanism has the opposite
# defect: it is sound *vacuously* — its bounds carry so much certified
# slack they can never undercut an incumbent, so it never prunes. Refine
# cannot help (there are no soundness cexs to drive it); the self-debugging
# demo exposed exactly this open loop. tighten_until closes it: binary-
# search the smallest uniform scale on all lipschitz fields that STILL
# passes the gate and shows zero regret on every bootstrap ordering. The
# result converts certified slack into pruning power while keeping the
# same empirical soundness standard as evaluation itself.

@dataclass
class TightenResult:
    spec_before: dict[str, Any]
    spec_after: dict[str, Any]
    iterations: int
    tightened: bool
    history: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"spec_before": self.spec_before,
                "spec_after": self.spec_after,
                "iterations": self.iterations, "tightened": self.tightened,
                "history": self.history, "note": self.note}


def _scale_lipschitz(spec: dict[str, Any], s: float) -> dict[str, Any]:
    out = dict(spec)
    if "lipschitz" in out:
        out["lipschitz"] = max(0.0, float(out["lipschitz"]) * s)
    for side in ("left", "right"):
        if side in out:
            out[side] = _scale_lipschitz(out[side], s)
    return out


def _has_lipschitz(spec: dict[str, Any]) -> bool:
    if "lipschitz" in spec:
        return True
    return any(_has_lipschitz(spec[s]) for s in ("left", "right") if s in spec)


def _max_lipschitz(spec: dict[str, Any]) -> float:
    own = float(spec.get("lipschitz", 0.0))
    return max([own] + [_max_lipschitz(spec[s])
                        for s in ("left", "right") if s in spec])


def tighten_until(abstraction: Abstraction, domain: ConcreteDomain,
                  budget: int | None = None, iterations: int = 6,
                  k_permutations: int = 8, mu: float = 0.5) -> TightenResult:
    spec0 = abstraction.spec()
    # Check order matters (boundary case caught by tests): lipschitz == 0
    # scales to itself, so "scaling leaves the spec unchanged" wrongly
    # classified unsound zero-lipschitz mechanisms as guidance no-ops.
    # The right tests: field PRESENCE for applicability, then soundness,
    # then remaining slack.
    if not _has_lipschitz(spec0):
        return TightenResult(spec0, spec0, 0, False,
                             note="mechanism has no lipschitz slack to "
                                  "tighten (guidance-only or lipschitz "
                                  "absent); tighten is a no-op")

    def safe(spec: dict[str, Any]):
        a = from_spec(spec)
        if not run_gate(a, domain, max_examples=25).passed:
            return False, None
        rob = bootstrap_replay(a, domain, budget=budget,
                               k_permutations=k_permutations, mu=mu)
        return rob.sound_robust, rob

    ok0, rob0 = safe(spec0)
    if not ok0:
        return TightenResult(spec0, spec0, 0, False,
                             note="entry is not sound at its current scale; "
                                  "use refine_abstraction instead")
    if _max_lipschitz(spec0) <= 0.0:
        return TightenResult(spec0, spec0, 0, False,
                             note="already fully tight: lipschitz is 0 and "
                                  "the mechanism is sound")

    history: list[dict[str, Any]] = []
    lo, hi = 0.0, 1.0
    best_spec = spec0
    for i in range(iterations):
        mid = (lo + hi) / 2
        cand = _scale_lipschitz(spec0, mid)
        ok, rob = safe(cand)
        history.append({"iteration": i, "scale": round(mid, 4), "sound": ok,
                        "mean_benefit": (round(rob.mean_benefit, 6)
                                         if rob else None)})
        if ok:
            hi = mid
            best_spec = cand
        else:
            lo = mid
    return TightenResult(spec_before=spec0, spec_after=best_spec,
                         iterations=len(history),
                         tightened=best_spec != spec0, history=history)
