# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Risk-tiered harness-patch PROMOTION gate (spec: agentic_self_training.md §H/§I).

THE self-training family's anti-bullshit centerpiece. The family's entire thesis is

    "Agents that get better because their harnesses LEARN" — NOT "agents that self-modify."  (§15)

The blog states this as the spec (principles.html): improvements are allowed in the
**safe-to-change surfaces** — prompts, routing/retry policies, failure classifiers, examples,
documentation — while **core governance, security boundaries, billing, and human-approval rules
are OFF-LIMITS to self-modification**; and (about.html, Self-Improvement) the system "proposes
new strategies, tests them against benchmarks, and **promotes winners** … under safety
constraints." This gate is the deterministic, model-free realisation of "promotes winners under
safety constraints": given a proposed harness patch and its independent evaluation, it decides
the promotion action and can only ever TIGHTEN it (DOWN-only) — never loosen.

It is complementary to, not a replacement for, the path-level check
:func:`g6.self_training.change_scope.classify_change_scope`, which decides *which paths a patch may
touch*. This gate answers the higher-level question that check does not: **given a passing evaluation
and a risk tier, what promotion action is authorised — and is the patch even allowed to touch what it
touched?** The action is the strictest one any rule asks for, and never weaker than the review its
risk tier needs: an off-limits surface or a missing independent evaluation can only add a person,
never lower an R3 or R4 review to plain approval. Autonomy is off unless the caller turns it on
(``autonomous_low_risk=True``).

Hard, fail-closed invariants (spec §6 Phase I rules 1-7; blog off-limits surfaces):
  * a patch must NOT expand tool permissions            -> BLOCK
  * a patch must NOT weaken safety policy               -> BLOCK
  * a patch must NOT remove human review                -> BLOCK
  * a patch must be versioned + reversible (rollback)   -> else BLOCK
  * a patch must NOT treat untrusted content as code    -> BLOCK
  * an R5 (prohibited) patch                            -> BLOCK
  * a patch generator may NOT be its own sole judge     -> no auto-promote without independent eval

Maps onto G6's reliability states: `promote_auto` / `canary` / `require_approval` /
`require_security_approval` / `require_expert_review` -> `qualified-draft` (its inputs are caller-declared, so no
decision is `verified`; audit 008 ST-001); `reject` (failed eval) / `block` (hard safety violation) ->
`blocked-escalated`.
"""
from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
from dataclasses import dataclass, field, fields

from collections.abc import Callable
from typing import Any

# --- Promotion decisions (the operational action) --------------------------
PROMOTE_AUTO = "promote_auto"                       # auto-deploy (R0, all criteria met)
CANARY = "canary"                                   # auto-deploy to a slice + monitor (R1)
REQUIRE_APPROVAL = "require_approval"               # human approval required (R2)
REQUIRE_SECURITY_APPROVAL = "require_security_approval"   # human + security approval (R3)
REQUIRE_EXPERT_REVIEW = "require_expert_review"     # expert review, no autonomous promotion (R4)
REJECT = "reject"                                   # evaluation gate failed — re-propose after fixing
BLOCK = "block"                                     # hard safety violation — structurally disallowed

# Every input to this gate is declared by its caller, so no decision can carry `verified`
# (audit 008, ST-001/ST-007); `verified` needs independent post-apply remeasurement.
_DECISION_TO_LABEL = {
    PROMOTE_AUTO: "qualified-draft",
    CANARY: "qualified-draft",
    REQUIRE_APPROVAL: "qualified-draft",
    REQUIRE_SECURITY_APPROVAL: "qualified-draft",
    REQUIRE_EXPERT_REVIEW: "qualified-draft",
    REJECT: "blocked-escalated",
    BLOCK: "blocked-escalated",
}
_HUMAN_DECISIONS = {REQUIRE_APPROVAL, REQUIRE_SECURITY_APPROVAL, REQUIRE_EXPERT_REVIEW}
_AUTO_DECISIONS = {PROMOTE_AUTO, CANARY}

# --- §H promotion criteria thresholds --------------------------------------
REGRESSION_PASS_RATE_MIN = 0.98
TARGET_METRIC_IMPROVEMENT_MIN = 0.03
EVALUATOR_CONFIDENCE_MIN = 0.75

# --- Harness surfaces (spec §4; blog principles.html) ----------------------
# The safe-to-change surfaces the spec names: what a learner may propose to change. The gate does not read this set;
# it decides by risk tier (below), and only the R0 and R1 surfaces among these can be promoted without a person.
SAFE_SURFACES = frozenset({
    "documentation", "examples", "few_shot_examples", "prompt",
    "retry_policy", "failure_classifier", "routing_rule", "rubric",
    "evaluation_suite", "skill_file", "workflow_graph", "preference_memory",
})
# Off-limits to self-modification (blog): a learned patch may never AUTONOMOUSLY touch these,
# and any *weakening* of them is hard-blocked above.
OFF_LIMITS_SURFACES = frozenset({
    "governance", "security_boundary", "billing", "human_approval_rule",
    "tool_permission", "safety_policy", "kill_switch",
    # The promotion authority itself, its thresholds and the evaluators it trusts (ST-005).
    "promotion_gate", "promotion_thresholds", "evaluator",
})

# Paths a self-modification may never touch, whatever surfaces it declares (ST-005: declared
# surfaces were not bound to effects, and the gate's own source was unprotected). Matched on
# normalised, case-folded paths so `Safety_Engine//x.py` cannot slip past.
PROTECTED_PATH_FRAGMENTS = (
    # The whole package, not only this gate: the improvement loop, the rubrics and the scorecard store judge a patch too
    # (found when the loop was ported, 2026-10-18).
    "self_training/",
    "core/stop",         # the process-wide kill switch
    "invariants/",       # the envelope rule checker is a grader
    "safety_engine/",
    "action_gating/",
    "action_logic/",
    "governance/",
    "csf",
    "core/reliability_envelope",
    "core/approval_",
    "core/promotion_",
    "tests/",            # tests are evaluators; a learner may not edit its own grader
    "policies/",
    # What judges a meta-harness change (META_HARNESS_PLAN.md Phase 0, owner rulings DECISIONS #26): its own
    # evaluator, gate, store, ledger and monitor; the label issuer; the learning and comparison gates it reuses; the
    # task spine that adjudicates each run; and the modules graduated from research for it (audit 037).
    "metaharness/",
    "evidence/",
    "learning/promotion",
    "learning/paired",
    "reasoning/compare",
    "distillation/admission",
    "harness/",
    "core/ledger",
    "core/vault",
    "core/adaptation_gate",
)

_TIER_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}
_RANK_TIER = {v: k for k, v in _TIER_RANK.items()}

# Per-surface base risk tier (spec §4 allowed-update sensitivity; AdaptiveAgentSpec
# allowed_learning_surfaces: prompts/examples auto, rubric/routing/skills human-review,
# tool_permissions forbidden-auto, memory user-confirmed).
_SURFACE_BASE_TIER = {
    "documentation": "R0",
    "examples": "R1", "few_shot_examples": "R1", "prompt": "R1",
    "retry_policy": "R1", "failure_classifier": "R1",
    "rubric": "R2", "routing_rule": "R2", "skill_file": "R2",
    "evaluation_suite": "R2", "workflow_graph": "R2", "preference_memory": "R2",
    "governance": "R3", "billing": "R3", "security_boundary": "R3",
    "tool_permission": "R3", "safety_policy": "R3", "human_approval_rule": "R3",
    "kill_switch": "R4", "promotion_gate": "R4", "promotion_thresholds": "R4", "evaluator": "R4",
}


@dataclass
class HarnessPatch:
    """A proposed harness change (spec §G HarnessPatchProposal, decision-relevant fields)."""
    patch_id: str = ""
    agent_id: str = ""
    base_harness_version: str = ""
    patch_type: str = ""                            # e.g. routing_patch (informational)
    target_surfaces: list[str] = field(default_factory=list)
    permission_change: str = "none"                 # none | narrowed | expanded
    safety_policy_change: str = "none"              # none | strengthen | weaken
    data_sensitivity_change: str = "none"           # none | lower | higher
    external_action_impact: str = "none"            # none | reversible | irreversible
    removes_human_review: bool = False
    reversible: bool = True
    # How the change is reverted, as a component declares it in its component_metadata.yaml
    # (for example extension_revert or stateless). The gate reads only "irreversible", which it
    # blocks (rule 7).
    rollback_method: str = "extension_revert"
    treats_untrusted_content_as_instructions: bool = False
    high_impact_domain: bool = False                # legal/medical/financial/employment/safety/infra
    prohibited: bool = False                         # malicious/illegal/unsafe (§I R5)
    # Files the change actually modifies (from its diff). Checked against
    # PROTECTED_PATH_FRAGMENTS independently of the declared surfaces.
    touched_paths: list[str] = field(default_factory=list)
    # The concrete change (a unified diff, or a structured delta as text). Hashed into the
    # decision's ``patch_digest`` so an applier applies exactly what was decided (audit 008 ST-018).
    diff: str = ""


@dataclass
class PatchEvalReport:
    """The independent evaluation of a candidate patch (spec §H PromotionCriteria)."""
    targeted_failure_fixed: bool = False
    regression_pass_rate: float = 0.0
    safety_violations: int = 0
    injection_resilience_not_worse: bool = True
    target_metric_improvement: float = 0.0
    evaluator_confidence: float = 0.0
    independent_eval: bool = False                   # §H: patch generator must not be sole judge
    evaluator_id: str = ""                         # identity of the evaluator, distinct from HarnessPatch.agent_id


@dataclass
class PromotionDecision:
    decision: str
    risk_tier: str
    reliability_label: str
    requires_human: bool
    auto_promotable: bool
    reasons: list[str] = field(default_factory=list)
    patch_digest: str = ""   # patch_digest() of the patch decided on

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "risk_tier": self.risk_tier,
            "reliability_label": self.reliability_label,
            "requires_human": self.requires_human,
            "auto_promotable": self.auto_promotable,
            "reasons": list(self.reasons),
            "patch_digest": self.patch_digest,
        }


def patch_digest(patch: HarnessPatch) -> str:
    """sha256 of every field of the patch: the change, the surfaces, the declared effects and who proposed it, so a
    patch edited after the decision (a flag, an identity) no longer matches it. List order does not count."""
    body = {f.name: getattr(patch, f.name) for f in fields(patch)}
    body["touched_paths"], body["target_surfaces"] = sorted(patch.touched_paths), sorted(patch.target_surfaces)
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=repr).encode("utf-8")).hexdigest()


def decision_matches(decision: PromotionDecision, patch: HarnessPatch) -> bool:
    """True when ``decision`` was made on exactly this change; an applier checks this before applying."""
    return bool(decision.patch_digest) and decision.patch_digest == patch_digest(patch)


def classify_patch_risk(patch: HarnessPatch) -> str:
    """§I risk tiering: final = max(surface base tiers, effect-flag floors). Fail-closed:
    an unknown surface is treated as R3 (sensitive), never R0."""
    if patch.prohibited:
        return "R5"
    rank = 0
    for surface in patch.target_surfaces:
        rank = max(rank, _TIER_RANK[_SURFACE_BASE_TIER.get(surface, "R3")])
    if patch.permission_change == "expanded":
        rank = max(rank, _TIER_RANK["R3"])
    if patch.data_sensitivity_change == "higher":
        rank = max(rank, _TIER_RANK["R3"])
    if patch.external_action_impact == "reversible":
        rank = max(rank, _TIER_RANK["R3"])
    if patch.external_action_impact == "irreversible":
        rank = max(rank, _TIER_RANK["R4"])
    if patch.removes_human_review:
        rank = max(rank, _TIER_RANK["R3"])
    if patch.high_impact_domain:
        rank = max(rank, _TIER_RANK["R4"])
    return _RANK_TIER[min(rank, 4)]                  # R0..R4 (R5 only via `prohibited`)


def _normalise_path(path: str) -> str:
    """Resolve ``.``/``..``/``//`` and separators so a fragment cannot be hidden (audit 024 REL-004)."""
    return posixpath.normpath(path.replace("\\", "/").casefold()) if path.strip() else ""


def _outside_repo(normalised: str) -> bool:
    return (not normalised or normalised == "." or normalised.startswith(("/", "../")) or normalised == ".."
            or re.match(r"^[a-z]:", normalised) is not None)


def protected_paths_touched(patch: HarnessPatch) -> list[str]:
    """Touched paths that hit a protected fragment, or that cannot be placed inside the repository."""
    out = []
    for path in patch.touched_paths:
        norm = _normalise_path(path)
        if _outside_repo(norm) or any(frag in norm for frag in PROTECTED_PATH_FRAGMENTS):
            out.append(path)
    return sorted(out)


def _identity(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def _invalid_scores(ev: PatchEvalReport) -> list[str]:
    """Scores must be finite and in range (ST-003: NaN compared false against every minimum
    and so passed all three thresholds)."""
    out = []
    for name, lo, hi in (("regression_pass_rate", 0.0, 1.0), ("evaluator_confidence", 0.0, 1.0),
                         ("target_metric_improvement", -1.0, 1.0)):
        value = getattr(ev, name)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            out.append(f"{name}={value!r} is not a finite value in [{lo:g}, {hi:g}]")
    return out


def _promotion_criteria_failures(patch: HarnessPatch, ev: PatchEvalReport) -> list[str]:
    """§H PromotionCriteria — every clause must hold to be eligible for ANY promotion."""
    out: list[str] = _invalid_scores(ev)
    if out:
        return out
    if not ev.targeted_failure_fixed:
        out.append("targeted failure not fixed by the patch")
    if ev.regression_pass_rate < REGRESSION_PASS_RATE_MIN:
        out.append(f"regression pass rate {ev.regression_pass_rate:.0%} < {REGRESSION_PASS_RATE_MIN:.0%}")
    if ev.safety_violations != 0:
        out.append(f"{ev.safety_violations} safety violation(s) in evaluation")
    if not ev.injection_resilience_not_worse:
        out.append("prompt-injection resilience regressed")
    if ev.target_metric_improvement < TARGET_METRIC_IMPROVEMENT_MIN:
        out.append(f"target-metric improvement {ev.target_metric_improvement:.0%} "
                   f"< {TARGET_METRIC_IMPROVEMENT_MIN:.0%}")
    if ev.evaluator_confidence < EVALUATOR_CONFIDENCE_MIN:
        out.append(f"evaluator confidence {ev.evaluator_confidence:.2f} < {EVALUATOR_CONFIDENCE_MIN:.2f}")
    return out


# The review each risk tier needs on its own (§I), and how strict each human or automatic action is: a decision is
# the strictest action any rule asks for, so it is never weaker than its tier's review.
_TIER_REVIEW = {
    "R0": (PROMOTE_AUTO, "R0 cosmetic/local: the tier allows auto-promotion (§I)"),
    "R1": (CANARY, "R1 low-risk task-quality: the tier allows an auto-canary with an audit log (§I)"),
    "R2": (REQUIRE_APPROVAL, "R2 professional / user-facing — human approval required (§I)"),
    "R3": (REQUIRE_SECURITY_APPROVAL, "R3 external action / sensitive data — human + security approval required (§I)"),
    "R4": (REQUIRE_EXPERT_REVIEW,
           "R4 high-impact / regulated / irreversible — expert review, no autonomous promotion (§I)"),
}
_STRICTNESS = {PROMOTE_AUTO: 0, CANARY: 1, REQUIRE_APPROVAL: 2, REQUIRE_SECURITY_APPROVAL: 3, REQUIRE_EXPERT_REVIEW: 4}


def govern_patch_promotion(
    patch: HarnessPatch,
    eval_report: PatchEvalReport,
    *,
    autonomous_low_risk: bool = False,
    formal_verifier: Callable[[HarnessPatch], Any] | None = None,
) -> PromotionDecision:
    """Decide the promotion action for ``patch``. Fail-closed, DOWN-only: hard safety rules and a
    failed evaluation can only TIGHTEN the action; nothing here can loosen it.

    ``autonomous_low_risk`` models the agent's promotion_policy (spec §6 AdaptiveAgentSpec). It is
    False by default (manual-only), so even R0/R1 go to a person; a caller opts in with True, and an
    R0/R1 patch that meets every criterion with an independent evaluation is then promoted without one.
    """
    tier = classify_patch_risk(patch)

    # CSF formal verification (claims LA-31 / FAQ-13) — the Computational Safety
    # Framework runs on EVERY self-modification handed to the gate, fail-closed.
    # It is an independent formal safety layer (defense-in-depth) atop the
    # declarative rules below: it verifies the formal safety constraint
    # (bounded-reachability vub <= epsilon), rollback semantics, and resource
    # bounds, and attaches that structured evidence to every decision.
    # Optional formal layer. The archive always ran a "CSF formal verification" that, per
    # audit 008 (ST-008), only restated the patch's declared flags and opened the working
    # directory as a git repo. A real verifier can be injected; it must return an object with
    # ``block_reasons`` (list[str]) and ``summary()``. Without one, the evidence says so.
    if formal_verifier is not None:
        csf = formal_verifier(patch)
        _csf_evidence = [csf.summary()]
        _formal_blocks = list(csf.block_reasons)
    else:
        _csf_evidence = ["formal verification: not performed (no verifier supplied)"]
        _formal_blocks = []

    def _mk(decision: str, reasons: list[str]) -> PromotionDecision:
        return PromotionDecision(
            decision=decision,
            risk_tier=tier,
            reliability_label=_DECISION_TO_LABEL[decision],
            requires_human=decision in _HUMAN_DECISIONS,
            auto_promotable=decision in _AUTO_DECISIONS,
            reasons=reasons + _csf_evidence,
            patch_digest=patch_digest(patch),
        )

    # 0. Validity — a patch with no declared surface cannot be promoted (fail-closed).
    if not patch.target_surfaces:
        return _mk(REJECT, ["patch declares no target surfaces — nothing to promote (fail-closed)"])

    # 1. R5 prohibited — block outright (§I R5).
    if tier == "R5":
        return _mk(BLOCK, ["patch is R5 (prohibited / unsafe) — blocked"])

    # 2. HARD SAFETY LAYER — CSF formal verdict + declarative rules (§6 Phase I
    #    rules 1-7). Any trip blocks, regardless of evaluation. CSF block reasons
    #    lead (the formal layer); the declarative flag rules corroborate.
    hard: list[str] = list(_formal_blocks)
    if patch.permission_change == "expanded":
        hard.append("expands tool permissions (rule 1) — off-limits to self-modification")
    if patch.safety_policy_change == "weaken":
        hard.append("weakens safety policy (rule 2)")
    if patch.removes_human_review:
        hard.append("removes human review from the workflow (rule 3)")
    if patch.treats_untrusted_content_as_instructions:
        hard.append("treats untrusted external content as instructions (rule 5)")
    if not patch.reversible:
        hard.append("not versioned/reversible — no rollback available (rule 7)")
    if patch.rollback_method == "irreversible":
        hard.append("rollback_method is irreversible -- no reversible rollback available (rule 7)")
    protected = protected_paths_touched(patch)
    if protected:
        hard.append(f"modifies protected paths {protected} (promotion authority, evaluators, safety) "
                    "— off-limits to self-modification")
    if hard:
        return _mk(BLOCK, hard)

    # 3. PROMOTION CRITERIA (§H) — a single miss forbids promotion at ANY tier.
    crit = _promotion_criteria_failures(patch, eval_report)
    if crit:
        return _mk(REJECT, ["promotion criteria not met: " + "; ".join(crit)])

    # 4-6. Each rule below can only ask for a stricter review; the decision is the strictest one asked for, and the
    #      risk tier's own review (§I) is always asked for, so no rule can lower it.
    asked: list[tuple[str, str]] = []

    # 4. Off-limits surface touched (blog) — never autonomous; escalate by sensitivity.
    offlimits = sorted(set(patch.target_surfaces) & OFF_LIMITS_SURFACES)
    if offlimits:
        if "kill_switch" in offlimits:
            # COPY: Protected kill-switch changes require expert review.
            asked.append((REQUIRE_EXPERT_REVIEW,
                          f"touches off-limits kill-switch surface {offlimits} — expert review, "
                          "no autonomous promotion (off-limits to self-modification)"))
        elif {"security_boundary", "tool_permission"} & set(offlimits):
            # COPY: Protected security surfaces require security approval.
            asked.append((REQUIRE_SECURITY_APPROVAL,
                          f"touches off-limits security surface(s) {offlimits} — security approval "
                          "required (off-limits to self-modification)"))
        else:
            # COPY: Other protected surfaces remain subject to human approval.
            asked.append((REQUIRE_APPROVAL,
                          f"touches off-limits surface(s) {offlimits} (governance/billing/human-approval/"
                          "safety-policy) — human approval required (off-limits to autonomous self-modification)"))

    # 5. Independent-evaluation gate (§H gotcha) — the patch generator cannot be its own sole judge.
    # An empty generator id means the generator is unknown, so independence cannot be shown
    # (ST-002: the archive accepted any evaluator when agent_id was empty, its product default).
    generator, evaluator = _identity(patch.agent_id), _identity(eval_report.evaluator_id)
    if not eval_report.independent_eval or not generator or not evaluator or generator == evaluator:
        # COPY: Missing or same-producer identity cannot establish independent evaluation.
        asked.append((REQUIRE_APPROVAL,
                      "no independent evaluation path — patch generator may not be its own sole judge (§H); "
                      "human approval required even though criteria passed"))

    # 6. RISK-TIER LADDER (§I): R0/R1 are autonomous only by the caller's opt-in, and only bound to a concrete change.
    if tier in ("R0", "R1") and not autonomous_low_risk:
        asked.append((REQUIRE_APPROVAL,
                      f"{tier} eligible for autonomy but promotion_policy is manual-only — approval required"))
    if tier in ("R0", "R1") and not patch.diff.strip():
        asked.append((REQUIRE_APPROVAL,
                      f"{tier} eligible for autonomy but the patch carries no concrete change (diff) for the "
                      "promotion to bind to (ST-018) — approval required"))
    asked.append(_TIER_REVIEW[tier])
    decision = max((d for d, _ in asked), key=_STRICTNESS.__getitem__)
    return _mk(decision, [reason for _, reason in asked])
