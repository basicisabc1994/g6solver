# SPDX-License-Identifier: Apache-2.0
"""Evidence-constrained contextual method selection for G6.

Chooses a method *order and implementation*; never deletes a mandatory
professional obligation or changes an independent acceptance oracle.

Training is admitted by an OUTSIDE evaluator-owned predicate. No predictor
self-rating, LLM score, document presence or 'verified' string grants credit.
The reference algorithm is a smoothed contextual upper-confidence bound,
NOT a calibrated probability of research success.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .assurance import canonical_json, sha256
from .mixed_methods import ENGINE_SPECS, ENGINE_OP_VARIANTS, ClaimIR, MethodRouter, MethodPlan

CAPABILITIES = frozenset(ENGINE_SPECS)
RISK_BANDS = range(4)


@dataclass(frozen=True)
class ProblemSignature:
    domain: str
    uncertainty: str
    claim_kind: str
    risk: int
    budget: float
    available_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.domain, self.uncertainty, self.claim_kind)):
            raise ValueError("domain, uncertainty and claim kind are required")
        if self.risk not in RISK_BANDS or not math.isfinite(self.budget) or self.budget < 0:
            raise ValueError("risk must be 0..3, budget finite and nonnegative")
        if len(set(self.available_evidence)) != len(self.available_evidence):
            raise ValueError("duplicate evidence identifiers")


@dataclass(frozen=True)
class MethodVariant:
    """A trusted, typed implementation of one evidence obligation.

    Representations label the specific mapping: 'DAG-json', 'DAG-dot',
    'annotated-graph', 'SMT-logic', 'tabular-features' etc. The executable
    operation is restricted to the existing G6 ENGINE_SPECS route.
    """
    id: str
    method: str
    representation: str
    engine: str
    op: str
    estimated_cost: float
    expects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.method not in ENGINE_SPECS:
            raise ValueError("method not in existing G6 engines")
        expected = ENGINE_SPECS[self.method]
        if self.engine != expected["component"] or self.op not in ENGINE_OP_VARIANTS[self.method]:
            raise ValueError("method variant cannot arbitrarily substitute an executable backend")
        if not self.id or not self.representation:
            raise ValueError("variant needs ID and representation")
        if not math.isfinite(self.estimated_cost) or self.estimated_cost < 0:
            raise ValueError("estimated cost must be nonnegative and finite")


@dataclass(frozen=True)
class EvaluatedTrial:
    """Held-out evaluation outcome; provenance must be independently authenticated.

    `resolved` means the FROZEN evaluator adjudicated the method's measured
    contribution under its rubric, not that the method declared itself good.
    The `admit` hook checks provenance/receipts or recomputes the judgement;
    presence of these strings alone is NOT sufficient.
    """
    trial_id: str
    case_id: str
    cohort_id: str
    domain: str
    uncertainty: str
    claim_kind: str
    variant_id: str
    resolved: bool
    actual_cost: float
    evaluator_ref: str
    oracle_digest: str
    split: str = "heldout"
    critical_regression: bool = False

    def __post_init__(self) -> None:
        if not all((self.trial_id, self.case_id, self.cohort_id, self.domain,
                    self.uncertainty, self.claim_kind, self.variant_id, self.evaluator_ref)):
            raise ValueError("evaluation trial needs IDs, context and evidence provenance")
        if type(self.resolved) is not bool or type(self.critical_regression) is not bool:
            raise ValueError("evaluator decisions must be booleans")
        if self.split not in ("training", "heldout", "shadow"):
            raise ValueError("evaluation split invalid")
        if not math.isfinite(self.actual_cost) or self.actual_cost < 0:
            raise ValueError("trial cost invalid")
        if len(self.oracle_digest) != 64 or any(x not in '0123456789abcdef' for x in self.oracle_digest):
            raise ValueError("frozen oracle digest required")


@dataclass(frozen=True)
class SelectionDecision:
    ordered_variants: tuple[dict[str, Any], ...]
    requirements: tuple[str, ...]
    missing: tuple[str, ...]
    rejected_trials: int
    admitted_trials: int
    policy_digest: str
    completion_state: str
    limitations: tuple[str, ...] = (
        "Historical success estimates are uncalibrated contextual heuristics, not truth probabilities.",
        "If method assignment was not randomized or paired, off-policy comparisons may be confounded.",
        "Selection cannot waive, redefine, or relabel mandatory acceptance criteria.",
        "Claims remain candidate hypotheses until independently evidenced and promoted by G6.",
    )

    def to_dict(self) -> dict[str, Any]:
        return {"ordered_variants": list(self.ordered_variants),
                "requirements": list(self.requirements), "missing": list(self.missing),
                "rejected_trials": self.rejected_trials,
                "admitted_trials": self.admitted_trials,
                "policy_digest": self.policy_digest,
                "completion_state": self.completion_state,
                "limitations": list(self.limitations)}


class MetacognitiveMethodSelector:
    """Contextual evidence selector, optional bounded exploration for low-risk tasks.

    The `admit_trial` hook is a mandatory evaluator-side trust boundary.
    If omitted, NO historical data are used (safe cold start). A lambda
    returning True is useful for unit tests but is NOT a production oracle.
    """
    def __init__(self, trials: Sequence[EvaluatedTrial] = (), *,
                 admit_trial: Callable[[EvaluatedTrial], bool] | None = None,
                 exploration_weight: float = 0.15,
                 seed: int = 0):
        if not math.isfinite(exploration_weight) or not 0 <= exploration_weight <= 1:
            raise ValueError("exploration weight invalid")
        self.trials: tuple[EvaluatedTrial, ...] = ()
        self.rejected_trials = 0
        self.exploration_weight = exploration_weight
        self.seed = seed
        self.admit_trial = admit_trial
        clean: list[EvaluatedTrial] = []
        seen: set[str] = set()
        seen_cases: set[tuple[str, str, str]] = set()
        for t in trials:
            # A replayed record must be in evaluator-owned heldout/shadow, and
            # cannot duplicate a previous trial id. The trusted admission hook
            # MUST check signature, frozen suite, evaluator and data access.
            key = (t.oracle_digest, t.case_id, t.variant_id)
            if (t.trial_id in seen or key in seen_cases
                    or t.split not in ("heldout", "shadow") or admit_trial is None):
                self.rejected_trials += 1
                continue
            try:
                allowed = admit_trial(t)
            except Exception:
                allowed = False
            if allowed is not True:
                self.rejected_trials += 1
                continue
            clean.append(t)
            seen.add(t.trial_id)
            seen_cases.add(key)
        self.trials = tuple(clean)

    def _score(self, variant: MethodVariant, context: ProblemSignature) -> dict[str, Any]:
        specific = [x for x in self.trials
                    if x.variant_id == variant.id and x.domain == context.domain
                    and x.uncertainty == context.uncertainty and x.claim_kind == context.claim_kind]
        # Do not transfer patterns from unrelated domains by default; such a
        # transfer is a hypothesis that requires evaluation, not prior evidence.
        n = len(specific)
        wins = sum(x.resolved and not x.critical_regression for x in specific)
        critical = sum(x.critical_regression for x in specific)
        mean = (wins + 1) / (n + 2)  # Beta(1,1) shrinkage, not calibration.
        bonus = math.sqrt(2 * math.log(2 + max(1, len(self.trials))) / (n + 1))
        # Novel/sparse methods are only explored by a separate low-risk policy.
        # Here uncertainty is disclosed; it cannot inflate an unsafe high-risk
        # method above a trained non-regressing method.
        exploration = self.exploration_weight * bonus if context.risk < 2 else 0.0
        regression_penalty = critical / max(1, n)
        score = (mean + exploration - regression_penalty) / (1.0 + variant.estimated_cost)
        return {"variant_id": variant.id, "representation": variant.representation,
                "method": variant.method, "engine": variant.engine, "op": variant.op,
                "expected_cost": variant.estimated_cost,
                "training_cases": n, "historical_resolutions": wins,
                "critical_regressions": critical, "smoothed_success_proxy": mean,
                "exploration_bonus": exploration,
                "ranking_score": score,
                "admitted_trial_ids": [x.trial_id for x in specific],
                "ranking_note": "evaluator-admitted historical outcomes only; not a calibrated forecast"}

    def select(self, context: ProblemSignature, variants: Sequence[MethodVariant],
               *, required_methods: Sequence[str],
               max_optional: int = 0) -> SelectionDecision:
        if max_optional < 0 or max_optional > 10:
            raise ValueError("max optional methods invalid")
        required = tuple(dict.fromkeys(required_methods))
        if not required or set(required) - CAPABILITIES:
            raise ValueError("required methods must be explicit known G6 capabilities")
        if len({v.id for v in variants}) != len(variants):
            raise ValueError("duplicate candidate method variant ids")
        options = [v for v in variants if v.estimated_cost <= context.budget
                   and all(name in context.available_evidence for name in v.expects)]
        scored = [self._score(v, context) for v in options]
        if len(variants) > 100:
            raise ValueError("method selector cap: at most 100 explicit variants")
        selected: list[dict[str, Any]] = []
        missing: list[str] = []
        remaining = context.budget
        # Find a globally affordable mandatory portfolio rather than picking
        # one expensive option and falsely declaring that no full plan exists.
        # Bounds: <=5 obligations; <=12 variants per obligation. Keep low-cost
        # options to avoid a top-score-only feasibility error.
        pools: list[list[dict[str, Any]]] = []
        for need in required:
            choices = [s for s in scored if s["method"] == need]
            ordered = sorted(choices, key=lambda x: (-x["ranking_score"], x["variant_id"]))
            low_cost = sorted(choices, key=lambda x: (x["expected_cost"], x["variant_id"]))[:2]
            merged = {x["variant_id"]: x for x in (ordered[:10] + low_cost)}
            pools.append(list(merged.values()))
        best: tuple[float, float, tuple[str, ...], list[dict[str, Any]]] | None = None
        if all(pools):
            def search(i: int, remaining_budget: float, accumulated: list[dict[str, Any]],
                       utility: float, spent: float) -> None:
                nonlocal best
                if i == len(pools):
                    key = (utility, -spent, tuple(x["variant_id"] for x in accumulated))
                    if best is None or key[:2] > best[:2] or (
                            key[:2] == best[:2] and key[2] < best[2]):
                        best = (utility, -spent, key[2], list(accumulated))
                    return
                for option in pools[i]:
                    if option["expected_cost"] <= remaining_budget + 1e-12:
                        search(i + 1, remaining_budget - option["expected_cost"],
                               accumulated + [option], utility + option["ranking_score"],
                               spent + option["expected_cost"])
            search(0, context.budget, [], 0.0, 0.0)
        if best is not None:
            for chosen in best[3]:
                selected.append({**chosen, "obligation": "mandatory"})
                remaining -= chosen["expected_cost"]
        else:
            # Infeasible portfolio: disclose each uncovered obligation and do
            # not silently promote the incomplete subset. The partial list is
            # diagnostic only; it cannot be executed through the orchestrator.
            for need in required:
                choices = [s for s in scored if s["method"] == need
                           and s["expected_cost"] <= remaining]
                if not choices:
                    missing.append(need)
                    continue
                chosen = sorted(choices, key=lambda x: (-x["ranking_score"], x["variant_id"]))[0]
                selected.append({**chosen, "obligation": "mandatory"})
                remaining -= chosen["expected_cost"]
            if not missing:
                missing.append("combined_mandatory_budget_infeasible")
        # Optional methods only after mandatory coverage, not as substitutes.
        if not missing:
            extras = [s for s in scored if s["variant_id"] not in {x["variant_id"] for x in selected}
                      and s["expected_cost"] <= remaining]
            for s in sorted(extras, key=lambda x: (-x["ranking_score"], x["variant_id"]))[:max_optional]:
                if s["expected_cost"] <= remaining:
                    selected.append({**s, "obligation": "supplemental"})
                    remaining -= s["expected_cost"]
        policy = {"version": "meta-selector/0.2", "seed": self.seed,
                  "exploration_weight": self.exploration_weight,
                  "trials": sorted(t.trial_id for t in self.trials),
                  "required": list(required), "context": vars(context),
                  "candidate_ids": sorted(v.id for v in variants)}
        return SelectionDecision(tuple(selected), required, tuple(missing),
                                 self.rejected_trials, len(self.trials),
                                 sha256(canonical_json(policy)),
                                 "blocked-escalated" if missing else "qualified-draft")


class MetacognitivePlanner:
    """Join selection with G6's existing canonical, allowlisted MethodRouter.

    This returns a recommendation and the unchanged canonical execution plan.
    It NEVER replaces the plan's steps (the real executor checks them for
    tampering) or removes a mandatory evidence obligation.
    """
    def __init__(self, selector: MetacognitiveMethodSelector):
        self.selector = selector

    def plan(self, claims: Sequence[ClaimIR], context: ProblemSignature,
             variants: Sequence[MethodVariant], *,
             required_methods: Sequence[str]) -> dict[str, Any]:
        planned: MethodPlan = MethodRouter().plan(claims)
        required = set(required_methods)
        requested = {need for claim in claims for need in claim.evidence_needs}
        if not required.issubset(requested):
            return {"completion_state": "blocked-escalated",
                    "reason": "professional requirements not present in canonical claim obligations",
                    "missing": sorted(required - requested),
                    "plan": planned.to_dict()}
        decision = self.selector.select(context, variants, required_methods=required_methods)
        if planned.missing:
            return {"completion_state": "blocked-escalated", "reason": "typed models missing",
                    "selection": decision.to_dict(), "plan": planned.to_dict()}
        return {"completion_state": decision.completion_state,
                "selection": decision.to_dict(), "plan": planned.to_dict(),
                "note": "selection advises representation/order; the G6 executor still validates all mandatory steps"}


__all__ = ["ProblemSignature", "MethodVariant", "EvaluatedTrial", "SelectionDecision",
           "MetacognitiveMethodSelector", "MetacognitivePlanner"]
