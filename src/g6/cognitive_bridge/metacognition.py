# SPDX-License-Identifier: Apache-2.0
"""Evidence-constrained contextual method selection for G6.

Chooses a method *order and implementation*; never deletes a mandatory
professional obligation or changes an independent acceptance oracle.

Training is admitted by an OUTSIDE evaluator-owned predicate. No predictor
self-rating, LLM score, document presence or label string grants credit.
The reference algorithm is a smoothed contextual upper-confidence bound,
NOT a calibrated probability of research success.
"""
from __future__ import annotations

import math
from dataclasses import field
from typing import Any, Callable, Mapping, Sequence

from .assurance import canonical_json, is_risk, sha256
from .codes import _is_type
from .codes import MODEL_MISSING, OBLIGATION_NOT_PLANNED, SELECTION_INCOMPLETE, affirmed, only_fields, refusal, settled
from .mixed_methods import ENGINE_SPECS, ENGINE_OP_VARIANTS, ClaimIR, MethodRouter, MethodPlan
from .snapshot import publish, record_data, _admit_length, fresh, issue, number, record, returned, snapshot

CAPABILITIES = frozenset(ENGINE_SPECS)
#: The splits a trial can come from (``EvaluatedTrial.split``); a selector learns from the ones its ``learn_from`` names.
SPLITS = ("training", "heldout", "shadow")
#: A heuristic's kind, and the one method, engine and op a heuristic variant names (0.3 design, 14.2). A heuristic is a
#: strategy: how to search or decompose. It is never evidence, so no obligation can require it and no selection holds it.
HEURISTIC = "heuristic"
STRATEGY_METHOD, STRATEGY_ENGINE, STRATEGY_OP = "strategy", "g6.learning", "apply"
#: A selection row's fields: what ``MetacognitiveMethodSelector`` writes for a chosen variant, and all a row may carry.
ROW_FIELDS = ("variant_id", "kind", "representation", "method", "engine", "op", "expected_cost", "training_cases",
              "repeated_trials", "historical_resolutions", "critical_regressions", "smoothed_success_proxy",
              "exploration_bonus", "ranking_score", "admitted_trial_ids", "ranking_note", "obligation")


@record
class ProblemSignature:
    """The features of a task the selector matches trials on. ``risk`` is the number of G6's ``risk_tier``: 0 to 3
    for R0 to R3."""
    domain: str
    uncertainty: str
    claim_kind: str
    risk: int
    budget: float
    available_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.domain, self.uncertainty, self.claim_kind)):
            raise ValueError("domain, uncertainty and claim kind are required")
        if not is_risk(self.risk) or not math.isfinite(self.budget) or self.budget < 0:
            raise ValueError("risk is the risk tier's number, 0 to 3 for R0 to R3; budget finite and nonnegative")
        if len(set(self.available_evidence)) != len(self.available_evidence):
            raise ValueError("duplicate evidence identifiers")

    @property
    def risk_tier(self) -> str:
        """G6's ``risk_tier`` for this task: ``R0`` to ``R3``."""
        return publish(f"R{self.risk}", where="ProblemSignature.risk_tier")


def _eligible(variant: MethodVariant, context: ProblemSignature) -> bool:
    """The one rule for a variant a task can use: it costs no more than the task's budget, and every evidence it expects
    is available. The selector ranks only these, and a selection is taken only when each of its rows is one."""
    return variant.estimated_cost <= context.budget and all(name in context.available_evidence
                                                            for name in variant.expects)


def _fits(cost: float, remaining: float) -> bool:
    """The one rule for affording a cost from what is left of a budget (to 1e-12)."""
    return cost <= remaining + 1e-12


@record
class MethodVariant:
    """A trusted, typed way to meet one evidence obligation: any named method.

    ``kind`` says what it is. ``"engine"`` (the default) is a G6 solver op: its method is one of ENGINE_SPECS, its
    engine that method's component and its op an allowlisted variant, so it can become an executable step.
    Representations label the specific mapping: 'DAG-json', 'DAG-dot', 'annotated-graph', 'SMT-logic',
    'tabular-features' etc. Any other kind (``"reviewer"``, ``"arm"`` of a study, ...) is ranked from its trials
    like an engine, but names its own method, engine and op, is never one of ENGINE_SPECS' methods and is never
    executed by the router.

    ``"heuristic"`` is a strategy (how to search or decompose), bound to the bytes that are applied: its
    ``content_digest`` is the SHA-256 of its strategy artefact, and it joins the definition digest, so two strategies
    with one name and different bytes share no history. A heuristic names the method ``strategy``, which no other kind
    may name; it is never an option for an obligation and never a row of a selection.
    """
    id: str
    method: str
    representation: str
    engine: str
    op: str
    estimated_cost: float
    expects: tuple[str, ...] = ()
    kind: str = "engine"
    content_digest: str = ""

    def __post_init__(self) -> None:
        if not _is_type(type(self.kind), (str,)) or not self.kind.strip():
            raise ValueError("a variant's kind names what it is: engine, reviewer, arm, ...")
        if self.kind == HEURISTIC:
            if len(self.content_digest) != 64 or any(x not in '0123456789abcdef' for x in self.content_digest):
                raise ValueError("a heuristic variant names its strategy artefact's 64-digit SHA-256 content digest")
            if (self.method, self.engine, self.op) != (STRATEGY_METHOD, STRATEGY_ENGINE, STRATEGY_OP):
                raise ValueError("a heuristic variant is the method strategy, applied by g6.learning's op apply")
        elif self.content_digest:
            raise ValueError("only a heuristic variant carries a content digest")
        elif self.method == STRATEGY_METHOD:
            raise ValueError("only a heuristic variant names the method strategy")
        if self.kind == "engine":
            if self.method not in ENGINE_SPECS:
                raise ValueError("method not in existing G6 engines")
            expected = ENGINE_SPECS[self.method]
            if self.engine != expected["component"] or self.op not in ENGINE_OP_VARIANTS[self.method]:
                raise ValueError("method variant cannot arbitrarily substitute an executable backend")
        elif self.method in ENGINE_SPECS:
            raise ValueError("only an engine variant meets a G6 solver method")
        elif not all((self.method, self.engine, self.op)):
            raise ValueError("variant needs a method, an engine and an op")
        if not self.id or not self.representation:
            raise ValueError("variant needs ID and representation")
        if not math.isfinite(self.estimated_cost) or self.estimated_cost < 0:
            raise ValueError("estimated cost must be nonnegative and finite")

    @property
    def definition_digest(self) -> str:
        """The digest of what this variant is (its kind, method, representation, engine and op, and for a heuristic the
        content digest of its strategy's bytes), not its name: a trial credits a variant only when it names this
        digest. A variant with no content digest has the digest it always had."""
        definition = {"kind": self.kind, "method": self.method, "representation": self.representation,
                      "engine": self.engine, "op": self.op}
        if self.content_digest:
            definition["content"] = self.content_digest
        return publish(sha256(canonical_json(definition)), where="MethodVariant.definition_digest")


@record
class EvaluatedTrial:
    """Held-out evaluation outcome; provenance must be independently authenticated.

    `resolved` means the FROZEN evaluator adjudicated the method's measured
    contribution under its rubric, not that the method declared itself good.
    The `admit` hook checks provenance/receipts or recomputes the judgement;
    presence of these strings alone is NOT sufficient.

    ``variant_digest`` is the ``MethodVariant.definition_digest`` of the variant the evaluator ran: a trial credits
    only a variant with that id and that definition, so a new method cannot inherit history by reusing a name. A
    trial that names no definition credits no variant.
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
    oracle_digest: str = field(metadata={'max_length': 64, 'size_error': 'frozen oracle digest required'})
    split: str = "heldout"
    critical_regression: bool = False
    variant_digest: str = field(default='', metadata={'max_length': 64,
                                'size_error': "a variant digest is a 64-digit SHA-256 of the variant's definition"})

    def __post_init__(self) -> None:
        if not all((self.trial_id, self.case_id, self.cohort_id, self.domain,
                    self.uncertainty, self.claim_kind, self.variant_id, self.evaluator_ref)):
            raise ValueError("evaluation trial needs IDs, context and evidence provenance")
        if type(self.resolved) is not bool or type(self.critical_regression) is not bool:
            raise ValueError("evaluator decisions must be booleans")
        if self.split not in SPLITS:
            raise ValueError("evaluation split invalid")
        if not math.isfinite(self.actual_cost) or self.actual_cost < 0:
            raise ValueError("trial cost invalid")
        if len(self.oracle_digest) < 64 or any(x not in '0123456789abcdef' for x in self.oracle_digest):
            raise ValueError("frozen oracle digest required")
        if self.variant_digest and (len(self.variant_digest) < 64
                                    or any(x not in '0123456789abcdef' for x in self.variant_digest)):
            raise ValueError("a variant digest is a 64-digit SHA-256 of the variant's definition")


def _incomplete(missing: Sequence[str]) -> Mapping[str, Any] | None:
    """The refusal of a selection that misses the mandatory methods ``missing``, or None when it misses none: the one
    rule for a selection's ``refused`` field."""
    return refusal(SELECTION_INCOMPLETE, "no affordable variant covers: " + ", ".join(missing)) if missing else None


@record
class SelectionDecision:
    ordered_variants: tuple[dict[str, Any], ...]
    requirements: tuple[str, ...]
    missing: tuple[str, ...]
    rejected_trials: int
    admitted_trials: int
    policy_digest: str
    refused: Mapping[str, Any] | None  # the refusal (codes.refusal) when a mandatory method is not covered
    limitations: tuple[str, ...] = (
        "Historical success estimates are uncalibrated contextual heuristics, not truth probabilities.",
        "If method assignment was not randomized or paired, off-policy comparisons may be confounded.",
        "Selection cannot waive, redefine, or relabel mandatory acceptance criteria.",
        "Claims remain candidate hypotheses until independently evidenced and promoted by G6.",
    )

    def __post_init__(self) -> None:
        # Whoever makes a decision (a selector, a replace(), a caller), each row has only a row's fields and no claim,
        # and it is refused exactly as its missing methods give (_incomplete): the two never disagree.
        for row in self.ordered_variants:
            only_fields(row, allowed=ROW_FIELDS, required=ROW_FIELDS, where="a selection row")
        if self.refused != snapshot(_incomplete(self.missing)):
            raise ValueError("a selection is refused exactly when a method is missing (SELECTION_INCOMPLETE naming "
                             "them): its refused field is not that refusal")

    @property
    def completion_state(self) -> str:
        return publish(settled(self.refused)["completion_state"], where="SelectionDecision.completion_state")

    def to_dict(self) -> dict[str, Any]:
        return publish(settled(self.refused, ordered_variants=list(self.ordered_variants),
                       requirements=list(self.requirements), missing=list(self.missing),
                       rejected_trials=self.rejected_trials, admitted_trials=self.admitted_trials,
                       policy_digest=self.policy_digest, limitations=list(self.limitations)), where="SelectionDecision.to_dict")


class MetacognitiveMethodSelector:
    """Contextual evidence selector, optional bounded exploration for low-risk tasks.

    The `admit_trial` hook is a mandatory evaluator-side trust boundary.
    If omitted, NO historical data are used (safe cold start). A lambda
    returning True is useful for unit tests but is NOT a production oracle.

    ``learn_from`` names the splits whose trials it learns from (``training``, ``heldout``, ``shadow``); the default,
    held-out and shadow, keeps the development split out of the evidence. Repeated trials of one variant on one case
    (one ``case_id`` in one ``cohort_id``, whatever the oracle's revision) are all kept and grouped: the case counts
    once, with the share of its trials that resolved. The same record twice (one ``trial_id``) is one record. A trial
    credits a variant only when it names that variant's id and its ``definition_digest``.
    """
    def __init__(self, trials: Sequence[EvaluatedTrial] = (), *,
                 admit_trial: Callable[[EvaluatedTrial], bool] | None = None,
                 exploration_weight: float = 0.15,
                 seed: int = 0,
                 learn_from: Sequence[str] = ("heldout", "shadow")):
        trials = snapshot(trials, where="trials")
        self.exploration_weight = snapshot(exploration_weight, where="exploration_weight")
        self.seed = snapshot(seed, where="seed")
        learn_from = snapshot(learn_from, where="learn_from")
        number(self.seed, where="seed")
        if not 0 <= number(self.exploration_weight, where="exploration_weight") <= 1:
            raise ValueError("exploration weight invalid")
        learn = tuple(dict.fromkeys(learn_from))
        if not learn or set(learn) - set(SPLITS):
            raise ValueError(f"learn_from names the splits to learn from, among {', '.join(SPLITS)}")
        self.trials: tuple[EvaluatedTrial, ...] = ()
        self.rejected_trials = 0
        self.admit_trial = admit_trial
        self.learn_from = snapshot(learn)
        clean: list[EvaluatedTrial] = []
        seen: set[str] = set()
        for t in trials:
            # A replayed record must be in a split it learns from, and cannot
            # duplicate a previous trial id. The trusted admission hook
            # MUST check signature, frozen suite, evaluator and data access.
            if t.trial_id in seen or t.split not in self.learn_from or admit_trial is None:
                self.rejected_trials += 1
                continue
            try:
                allowed = affirmed(admit_trial(fresh(t)))  # the hook is handed a copy: what it admits is what is kept
            except Exception:
                allowed = False
            if not allowed:
                self.rejected_trials += 1
                continue
            clean.append(t)
            seen.add(t.trial_id)
        self.trials = snapshot(clean)
        #: How many (case, variant) groups were admitted: the evidence count, whatever the repeats or oracle revisions.
        self._groups = len({(t.cohort_id, t.case_id, t.variant_id, t.variant_digest) for t in self.trials})

    def _score(self, variant: MethodVariant, context: ProblemSignature, *,
               trials: Sequence[EvaluatedTrial] | None = None) -> dict[str, Any]:
        digest = variant.definition_digest
        if trials is None:
            trials = snapshot(self.trials, where="trials")
        specific = [x for x in trials
                    if x.variant_id == variant.id and x.variant_digest == digest and x.domain == context.domain
                    and x.uncertainty == context.uncertainty and x.claim_kind == context.claim_kind]
        # Do not transfer patterns from unrelated domains by default; such a
        # transfer is a hypothesis that requires evaluation, not prior evidence.
        cases: dict[tuple[str, str], list[EvaluatedTrial]] = {}
        for x in specific:  # one variant's repeated trials on one case are one case, whatever the oracle's revision
            cases.setdefault((x.cohort_id, x.case_id), []).append(x)
        n = len(cases)
        wins = sum(sum(x.resolved and not x.critical_regression for x in group) / len(group)
                   for group in cases.values())
        critical = sum(sum(x.critical_regression for x in group) / len(group) for group in cases.values())
        mean = (wins + 1) / (n + 2)  # Beta(1,1) shrinkage, not calibration.
        bonus = math.sqrt(2 * math.log(2 + max(1, self._groups)) / (n + 1))
        # Novel/sparse methods are only explored by a separate low-risk policy.
        # Here uncertainty is disclosed; it cannot inflate an unsafe high-risk
        # method above a trained non-regressing method.
        exploration = self.exploration_weight * bonus if context.risk < 2 else 0.0
        regression_penalty = critical / max(1, n)
        score = (mean + exploration - regression_penalty) / (1.0 + variant.estimated_cost)
        return {"variant_id": variant.id, "kind": variant.kind, "representation": variant.representation,
                "method": variant.method, "engine": variant.engine, "op": variant.op,
                "expected_cost": variant.estimated_cost,
                "training_cases": n, "repeated_trials": len(specific) - n, "historical_resolutions": wins,
                "critical_regressions": critical, "smoothed_success_proxy": mean,
                "exploration_bonus": exploration,
                "ranking_score": score,
                "admitted_trial_ids": [x.trial_id for x in specific],
                "ranking_note": "evaluator-admitted historical outcomes only; not a calibrated forecast"}

    def select(self, context: ProblemSignature, variants: Sequence[MethodVariant],
               *, required_methods: Sequence[str],
               max_optional: int = 0) -> SelectionDecision:
        _admit_length(variants, 100, "method selector cap: at most 100 explicit variants")
        _admit_length(required_methods, 5, "method selector cap: at most 5 obligations")
        context = snapshot(context, where="context")
        variants = snapshot(variants, where="variants")
        required_methods = snapshot(required_methods, where="required_methods")
        max_optional = snapshot(max_optional, where="max_optional")
        trials = snapshot(self.trials, where="trials")
        learn_from = snapshot(self.learn_from, where="learn_from")
        if not 0 <= number(max_optional, where="max_optional") <= 10:
            raise ValueError("max optional methods invalid")
        required = tuple(dict.fromkeys(required_methods))
        # A heuristic proposes a strategy, never evidence: it sits beside the evidence plan. It is dropped here, before
        # anything is ranked, so a selection is the same with and without it, and "strategy" is never a known method.
        variants = tuple(v for v in variants if v.kind != HEURISTIC)
        # A required method is a G6 solver capability or the method a non-engine variant names (a review, an arm).
        known = CAPABILITIES | {v.method for v in variants if v.kind != "engine"}
        if not required or set(required) - known:
            raise ValueError("required methods must be explicit known G6 capabilities or methods a variant names")
        if len({v.id for v in variants}) != len(variants):
            raise ValueError("duplicate candidate method variant ids")
        options = [v for v in variants if _eligible(v, context)]
        scored = [self._score(v, context, trials=trials) for v in options]
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
                    if _fits(option["expected_cost"], remaining_budget):
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
                  "exploration_weight": self.exploration_weight, "learn_from": list(learn_from),
                  "trials": sorted(t.trial_id for t in trials),
                  "required": list(required), "context": record_data(context),
                  "candidate_ids": sorted(v.id for v in variants)}
        return publish(issue(self, SelectionDecision(tuple(selected), required, tuple(missing),
                                             self.rejected_trials, len(trials),
                                             sha256(canonical_json(policy)), _incomplete(missing))), where="MetacognitiveMethodSelector.select")


def checked_selection(decision: Any, variants: Sequence[MethodVariant], context: ProblemSignature,
                      required: Sequence[str]) -> SelectionDecision:
    """A selector's answer as the planner and the orchestrator use it (a ``snapshot.returned`` check), bound to what
    the selector was handed: a SelectionDecision (whose rows carry only a row's fields) for the ``required`` methods,
    refused exactly when one is missing and, when none is, meeting each with one mandatory row; whose every row names
    one of the ``variants``, says that variant's kind, method, representation, engine, op and cost, is eligible under
    the ``context`` (:func:`_eligible`) and is a mandatory or supplemental obligation; and whose rows together fit the
    context's budget (:func:`_fits`, row by row, as the selector spends it)."""
    if type(decision) is not SelectionDecision:
        raise ValueError("a method selector returns a SelectionDecision")
    needs = sorted(dict.fromkeys(required))
    if sorted(decision.requirements) != needs:  # refused exactly when one is missing: the record's own rule
        raise ValueError("a selection is for the methods it was handed")
    mandatory = sorted(row["method"] for row in decision.ordered_variants if row["obligation"] == "mandatory")
    if not decision.missing and mandatory != needs:
        raise ValueError("a complete selection meets each required method with one mandatory variant")
    given = {v.id: v for v in variants}
    remaining = context.budget
    for row in decision.ordered_variants:
        variant = given.get(row["variant_id"])
        if variant is None or (row["kind"], row["method"], row["representation"], row["engine"], row["op"],
                               row["expected_cost"]) != (variant.kind, variant.method, variant.representation,
                                                         variant.engine, variant.op, variant.estimated_cost):
            raise ValueError("a selection row does not name a variant the selector was given, as it was given")
        if not _eligible(variant, context) or row["obligation"] not in ("mandatory", "supplemental"):
            raise ValueError(f"the selection row for {variant.id!r} is not an obligation this context can meet: its "
                             "cost exceeds the budget or an evidence it expects is not available")
        if not _fits(variant.estimated_cost, remaining):
            raise ValueError("the selected variants together cost more than the context's budget")
        remaining -= variant.estimated_cost
    return decision


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
        claims = snapshot(claims, where="claims")
        context = snapshot(context, where="context")
        variants = snapshot(variants, where="variants")
        required_methods = snapshot(required_methods, where="required_methods")
        planned: MethodPlan = MethodRouter().plan(claims)
        required = set(required_methods)
        requested = {need for claim in claims for need in claim.evidence_needs}
        if not required.issubset(requested):
            return publish(refusal(OBLIGATION_NOT_PLANNED, "professional requirements not present in canonical claim obligations",
                           missing=sorted(required - requested), plan=planned.to_dict()), where="MetacognitivePlanner.plan")
        decision = returned(self.selector.select(fresh(context), fresh(variants),
                                                 required_methods=fresh(required_methods)), checked_selection, variants,
                            context, required_methods)
        if planned.missing:
            return publish(refusal(MODEL_MISSING, "typed models missing", selection=decision.to_dict(), plan=planned.to_dict()), where="MetacognitivePlanner.plan")
        return publish(settled(decision.refused, selection=decision.to_dict(), plan=planned.to_dict(),
                       note="selection advises representation/order; the G6 executor still validates all mandatory steps"), where="MetacognitivePlanner.plan")


__all__ = ["ProblemSignature", "MethodVariant", "EvaluatedTrial", "SelectionDecision",
           "MetacognitiveMethodSelector", "MetacognitivePlanner"]
