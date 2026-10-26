# SPDX-License-Identifier: Apache-2.0
"""Robust valuation (remediations for winner's curse and ordering luck).

A single replay on the recorded trace order is one noisy draw of an
abstraction's value; an agent proposing many candidates will crown a lucky
one (winner's curse), and a mechanism can be regret-free on one ordering
yet unsound under another. Remediations:

  bootstrap_replay   replay under K permutations of the trace; report the
                     distribution of the combined benefit
                        b = value_gain / span + mu * evals_saved / budget
                     and the TOTAL regret events across permutations —
                     robust soundness demands zero regret on EVERY ordering
                     (a Lipschitz-honest mechanism is order-independently
                     sound, so permutation regret is a legitimate probe).

  selection-adjusted lower confidence bound: with k candidates already
                     evaluated on this domain, the "valuable" claim uses the
                     alpha/k Bonferroni quantile of the bootstrap benefit
                     distribution. With modest K this degrades to the
                     bootstrap minimum as k grows — deliberately: the more
                     you shop, the stronger the evidence you owe.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .abstractions import Abstraction, from_spec
from .domain import ConcreteDomain, Record
from .gate import replay


@dataclass
class RobustReport:
    n_permutations: int
    benefits: list[float]
    total_regret_events: int
    mean_benefit: float
    # Low order statistic of benefit over re-orderings of ONE fixed trace. It is an
    # order-sensitivity stress test, not a frequentist confidence bound: the permutations
    # reuse the same observations, and no coverage has been established (audit 001, F-05).
    # The field keeps its historical name for compatibility; read `lcb_interpretation`.
    lcb: float
    lcb_level: float                # the (Bonferroni-adjusted) quantile index used
    n_candidates_tried: int
    sound_robust: bool
    valuable: bool
    per_permutation: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"n_permutations": self.n_permutations,
                "mean_benefit": round(self.mean_benefit, 6),
                "lcb": round(self.lcb, 6),
                "lcb_level": round(self.lcb_level, 6),
                "lcb_interpretation": "permutation_order_quantile",
                "n_candidates_tried": self.n_candidates_tried,
                "total_regret_events": self.total_regret_events,
                "sound_robust": self.sound_robust,
                "valuable": self.valuable,
                "benefits": [round(b, 6) for b in self.benefits]}


def _permuted(domain: ConcreteDomain, rng: random.Random) -> ConcreteDomain:
    order = list(range(len(domain.records)))
    rng.shuffle(order)
    recs = [Record(domain.records[i].config, domain.records[i].objective, j)
            for j, i in enumerate(order)]
    return ConcreteDomain(params=domain.params, records=recs,
                          objective_name=domain.objective_name,
                          maximise=domain.maximise, name=domain.name + "/perm")


def bootstrap_replay(abstraction: Abstraction, domain: ConcreteDomain,
                     budget: int | None = None, k_permutations: int = 12,
                     mu: float = 0.5, n_candidates_tried: int = 1,
                     alpha: float = 0.05, seed: int = 0,
                     include_recorded_order: bool = True) -> RobustReport:
    rng = random.Random(seed)
    scores = [domain.score(r.objective) for r in domain.records]
    span = max(1e-9, max(scores) - min(scores)) if scores else 1.0

    benefits: list[float] = []
    per_perm: list[dict[str, Any]] = []
    regrets = 0
    doms = ([domain] if include_recorded_order else []) + [
        _permuted(domain, rng) for _ in range(max(0, k_permutations - 1))]
    for d in doms:
        rep = replay(from_spec(abstraction.spec()), d, budget=budget)
        value_term = rep.value_gain / span
        if rep.reorder_gain is not None:
            value_term = max(value_term, rep.reorder_gain / span)
        b = value_term + mu * (rep.evals_saved / max(1, rep.budget))
        benefits.append(b)
        regrets += len(rep.regret_events)
        per_perm.append({"benefit": round(b, 6),
                         "evals_saved": rep.evals_saved,
                         "n_regret": len(rep.regret_events)})

    benefits_sorted = sorted(benefits)
    level = min(0.5, alpha / max(1, n_candidates_tried))
    idx = min(len(benefits_sorted) - 1,
              max(0, int(level * len(benefits_sorted))))
    lcb = benefits_sorted[idx]
    mean_b = sum(benefits) / len(benefits)
    sound_robust = regrets == 0
    valuable = sound_robust and lcb > 0.0
    return RobustReport(n_permutations=len(doms), benefits=benefits,
                        total_regret_events=regrets, mean_benefit=mean_b,
                        lcb=lcb, lcb_level=level,
                        n_candidates_tried=n_candidates_tried,
                        sound_robust=sound_robust, valuable=valuable,
                        per_permutation=per_perm)
