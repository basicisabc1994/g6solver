# SPDX-License-Identifier: Apache-2.0
"""The meta-gate: deterministic, fail-closed, down-only (META_HARNESS_PLAN.md 5.3). Its best answer is ``recommend``,
labelled ``qualified-draft``, and a person still decides.

In order: the stop; the workflow and the judging code unchanged since confirmation; the candidate a valid profile that
keeps every mandatory check; the held-out split frozen and within its reuse limit; a suite that can tell the base from
an empty answer; enough cases and enough disagreements; a win by the exact sign test at alpha / K; no more compute
than the base, or a win over the base given the same compute; every cost objective non-inferior, every bound held and no
rise in false accepts; and a complete evaluation inside its budget.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from g6.core import stop
from g6.core.ledger import digest
from g6.metaharness.evaluate import EvaluationReport
from g6.self_training.change_scope import ChangeScope, ScopeVerdict, classify_change_scope
from g6.self_training.patch_promotion_gate import BLOCK, REJECT, HarnessPatch, PatchEvalReport, govern_patch_promotion
from g6.metaharness.schema import (
    BUDGET_EXCEEDED,
    HOLDOUT_REUSED,
    INSUFFICIENT_EVIDENCE,
    NO_GAIN,
    NOT_COMPUTE_MATCHED,
    NOT_CONFIRMED,
    OBJECTIVE_REGRESSED,
    PATCH_EVIDENCE_REQUIRED,
    PROTECTED,
    RECOMMENDED,
    SCHEMA,
    STANDARD_CHANGED,
    SUITE_CANNOT_DISCRIMINATE,
    PatchEvidence,
    Profile,
    Proposal,
    Workflow,
)

VERDICTS = ("recommend", "refuse", "block")


@dataclass(frozen=True)
class GateDecision:
    verdict: str
    code: str
    reasons: tuple[str, ...]
    workflow_digest: str
    base_digest: str
    candidate_digest: str
    report_digest: str
    proposal_digest: str
    proposer: str
    risk_tier: str = "R1"            # a profile is data; code-level changes (Phase 5) are tiered by self_training
    requires_person: bool = True
    checks: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.verdict == "recommend" else "blocked-escalated"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self) | {"completion_state": self.completion_state}
        if not self.measurements:  # old decisions retain their recorded digest
            data.pop("measurements")
        return data

    def digest(self) -> str:
        return digest(self.to_dict())


def _code_change(proposal: Proposal, base: Profile, workflow: Workflow,
                 report: EvaluationReport) -> tuple[str, str, tuple[str, ...]] | None:
    """None when the change may go on to the evidence rules; else (verdict, code, reasons)."""
    from pathlib import Path

    folder = Path(workflow.code_root).name if workflow.code_root is not None else ""
    changed = sorted(n for n in set(proposal.candidate.code) | set(base.code)
                     if proposal.candidate.code.get(n) != base.code.get(n))
    paths = tuple(f"{folder}/{n}" for n in changed)
    scope = classify_change_scope(ChangeScope("usercode", changed_paths=paths), roots=(f"{folder}/",))
    if scope.verdict == ScopeVerdict.BLOCK:
        return "block", PROTECTED, scope.reasons
    remedy = ("Configure Workflow.patch_evaluator(base, candidate, artifacts) to run independent safety and "
              "injection checks for this patch and measure evaluator confidence; return the measured fields "
              "with checked_by, reconfirm the workflow and re-evaluate the patch.")
    try:
        observed = PatchEvidence.model_validate(report.patch_evidence)
    except (TypeError, ValueError):
        detail = report.patch_evidence.get("error", "missing safety_violations, injection_resilience_not_worse, "
                                           "evaluator_confidence and checked_by")
        return "block", PATCH_EVIDENCE_REQUIRED, (f"patch evidence unavailable: {detail}", remedy)
    missing = [name for name in ("safety_violations", "injection_resilience_not_worse", "evaluator_confidence")
               if getattr(observed, name) is None]
    if not observed.checked_by or any(not name.strip() for name in observed.checked_by):
        missing.append("checked_by")
    if missing:
        return "block", PATCH_EVIDENCE_REQUIRED, (f"missing patch evidence: {', '.join(missing)}", remedy)
    if observed.base_digest != base.digest() or observed.candidate_digest != proposal.candidate_digest:
        return "block", PATCH_EVIDENCE_REQUIRED, ("patch evidence belongs to a different base or candidate", remedy)
    s = report.success or {}
    n = max(report.n, 1)
    base_passed = sum(1 for c in report.cases if c["base"]["success"])
    patch = HarnessPatch(patch_id=proposal.digest()[:16], agent_id=proposal.proposer, base_harness_version=base.digest(),
                         patch_type="code_patch", target_surfaces=["skill_file"], touched_paths=list(paths),
                         diff=digest(dict(proposal.candidate.code)), reversible=True)
    # regressions: the share of the cases the base solved that the candidate broke (the patch gate wants >= 98% kept)
    evaluation = PatchEvalReport(targeted_failure_fixed=s.get("wins", 0) > s.get("losses", 0),
                                 regression_pass_rate=1.0 - s.get("losses", 0) / base_passed if base_passed else 1.0,
                                 safety_violations=observed.safety_violations,
                                 injection_resilience_not_worse=observed.injection_resilience_not_worse,
                                 target_metric_improvement=max(-1.0, min(1.0, (s.get("wins", 0) - s.get("losses", 0)) / n)),
                                 evaluator_confidence=observed.evaluator_confidence)
    decision = govern_patch_promotion(patch, evaluation, autonomous_low_risk=False)
    if decision.decision == BLOCK or observed.safety_violations or not observed.injection_resilience_not_worse:
        return "block", PROTECTED, tuple(decision.reasons)
    if decision.risk_tier in ("R3", "R4", "R5"):
        return "refuse", PROTECTED, (f"the patch gate tiers this change {decision.risk_tier}: it needs a named security "
                                     "or domain reviewer, which this version does not have",)
    if decision.decision == REJECT:
        return "refuse", PATCH_EVIDENCE_REQUIRED, (*decision.reasons, remedy)
    return None


def decide(report: EvaluationReport, proposal: Proposal, workflow: Workflow, *, confirmed: dict | None,
           judging_sha256: str, base: Profile) -> GateDecision:
    """The decision for one evaluation of one proposal. ``confirmed`` is the store's confirmation row and
    ``judging_sha256`` the judging code's digest now."""
    trail: list[dict[str, Any]] = []

    def out(verdict: str, code: str, *reasons: str) -> GateDecision:
        return GateDecision(verdict, code, tuple(reasons), report.workflow_digest, report.base_digest,
                            report.candidate_digest, report.digest(), proposal.digest(), proposal.proposer,
                            checks=tuple(trail), measurements={
                                name: dict(report.estimates.get(name) or {"status": "unknown"})
                                for name in ("success", "cost_usd", "wall_clock_s")})

    def passed(step: str, detail: Any = "") -> None:
        trail.append({"step": step, "result": "pass", "detail": detail})

    # 1. the stop
    engaged = stop.engaged()
    if engaged is not None:
        return out("block", stop.STOPPED, f"G6 is stopped: {engaged}")
    passed("stop")
    # 2. what judges is unchanged
    if confirmed is None:
        return out("block", NOT_CONFIRMED, "the workflow has not been confirmed by a person")
    current = workflow.digest()
    if not (confirmed.get("workflow_digest") == current == report.workflow_digest):
        return out("block", STANDARD_CHANGED, "the workflow changed since a person confirmed it (re-baseline)")
    if confirmed.get("judging_sha256") != judging_sha256:
        return out("block", PROTECTED, "the code that judges a change differs from the code confirmed")
    passed("workflow and judging code unchanged", current)
    # 3. a valid profile that keeps every mandatory check (the profile type has no field that removes one)
    problems = proposal.candidate.problems_for(workflow) + base.problems_for(workflow)
    if problems:
        return out("block", SCHEMA, *(why for _, why in problems))
    if (proposal.candidate_digest != report.candidate_digest or base.digest() != report.base_digest
            or proposal.base_digest != report.base_digest):
        return out("block", SCHEMA, "the report is not for this proposal and base")
    passed("schema; checks only added", list(proposal.candidate.extra_checks))
    if report.candidate_digest == report.base_digest:
        return out("refuse", NO_GAIN, "the candidate is the base: there is no change to recommend")
    # 4. a code-level change (owner ruling Q7): the change-scope rule and the patch gate, never autonomous (MH-3)
    if proposal.candidate.code != base.code:
        code_check = _code_change(proposal, base, workflow, report)
        if code_check is not None:
            verdict, code, why = code_check
            return out(verdict, code, *why)
        passed("code change: scope and patch gate", sorted(proposal.candidate.code))
    # 5. held-out discipline
    if report.split != "held_out" or report.split_digest != confirmed.get("held_out_digest"):
        return out("refuse", HOLDOUT_REUSED if report.split == "held_out" else INSUFFICIENT_EVIDENCE,
                   "the evaluation is not on the held-out split a person confirmed")
    if report.reuse_index > workflow.policy.max_evaluations:
        return out("refuse", HOLDOUT_REUSED, f"the held-out split has been evaluated {report.reuse_index} times; "
                   f"the limit is {workflow.policy.max_evaluations}: freeze a fresh split")
    passed("held-out split", {"reuse_index": report.reuse_index})
    # 11 (checked early: a partial evaluation proves nothing). MH-13
    if any(not stage["passed"] for stage in report.stages):
        return out("refuse", "G6_E_META_STAGE_FAILED", "the candidate failed a staged evaluation checkpoint")
    if report.partial:
        return out("refuse", BUDGET_EXCEEDED, f"the evaluation did not finish: {report.stopped}")
    # 6. the suite can discriminate
    neg = report.negative_control
    policy = workflow.policy
    if not (neg.get("wins", 0) > neg.get("losses", 0) and neg.get("p_one_sided", 1.0) <= policy.alpha):
        return out("refuse", SUITE_CANNOT_DISCRIMINATE,
                   "the suite cannot tell the base from an empty answer: its checks and oracle measure nothing here")
    passed("negative control", neg)
    # 7. enough evidence
    s = report.success
    if report.n < policy.min_cases or s.get("discordant", 0) < policy.min_discordant:
        return out("refuse", INSUFFICIENT_EVIDENCE,
                   f"{report.n} held-out cases ({policy.min_cases} needed) and {s.get('discordant', 0)} where base and "
                   f"candidate differ ({policy.min_discordant} needed)", f"power: {report.power}")
    passed("evidence size", {"n": report.n, "discordant": s["discordant"]})
    # 8. success (a sequential confirmation tests against the boundary of the look it stopped at)
    level = s.get("boundary", policy.threshold)
    if not (s["wins"] > s["losses"] and s["p_one_sided"] <= level):
        where = f" at look {s['look']} of {s['looks']}" if "look" in s else ""
        return out("refuse", NO_GAIN, f"wins {s['wins']}, losses {s['losses']}, p {s['p_one_sided']} "
                   f"(needs p <= {level:g}{where})")
    passed("success", s)
    # 9. compute
    calls = report.estimates.get("model_calls") or {}
    over = calls.get("candidate_mean", 0.0) > calls.get("base_mean", 0.0) * policy.call_tolerance
    if over:
        ctl = report.control
        if not ctl or not (ctl["wins"] > ctl["losses"] and ctl["p_one_sided"] <= policy.threshold):
            return out("refuse", NOT_COMPUTE_MATCHED,
                       "the candidate used more calls than the base and did not beat the base given the same calls")
    passed("compute", {"over_base": over, "control": report.control})
    # 10. objectives
    for objective in workflow.objectives:
        est = report.estimates.get(objective.name) or {}
        if not est:
            return out("refuse", INSUFFICIENT_EVIDENCE, f"{objective.name}: measurement unavailable")
        if objective.direction == "min":
            allowed = objective.tolerance * abs(est.get("base_mean", 0.0))
            if est.get("diff_upper_95", 0.0) > allowed + 1e-12:
                return out("refuse", OBJECTIVE_REGRESSED,
                           f"{objective.name}: the upper bound on the increase {est.get('diff_upper_95')} exceeds "
                           f"{objective.tolerance:.0%} of the base ({allowed:g})")
        elif objective.direction == "bound" and est.get("candidate_upper_95", 0.0) > float(objective.bound):
            return out("refuse", OBJECTIVE_REGRESSED, f"{objective.name}: the upper bound "
                       f"{est.get('candidate_upper_95')} exceeds its bound {objective.bound}")
    cost = report.estimates.get("cost_usd")
    if proposal.strategy == "trace_cost" and not cost:
        return out("refuse", INSUFFICIENT_EVIDENCE, "cost proposer: no measured held-out costs")
    if cost and not any(o.name == "cost_usd" for o in workflow.objectives):
        if cost["diff_upper_95"] > 0.05 * abs(cost["base_mean"]) + 1e-12:
            return out("refuse", OBJECTIVE_REGRESSED, "measured cost rose beyond the default 5% margin")
    fa = report.estimates.get("false_accepts")
    if fa is not None:      # a rise whose upper bound passes the margin (V6b-F5: a zero-margin count fires on noise)
        if fa.get("diff_mean", 0.0) > 0 and fa.get("diff_upper_95", 0.0) > policy.false_accept_margin + 1e-12:
            return out("refuse", OBJECTIVE_REGRESSED,
                       f"false accepts: the upper bound on the rise in their rate, {fa.get('diff_upper_95')}, exceeds "
                       f"the margin {policy.false_accept_margin:g} ({report.false_accepts})")
    elif report.false_accepts["candidate"] > report.false_accepts["base"]:   # a report from before the margin rule
        return out("refuse", OBJECTIVE_REGRESSED, f"false accepts rose: {report.false_accepts}")
    passed("objectives and false accepts", report.false_accepts)
    reasons = ["the candidate beat the base on the held-out split under every rule; a person must approve it before "
               "it is used"]
    if report.provenance != "independent":
        reasons.append(f"held-out not independent: the split is {report.provenance}-authored, so this evidence is only "
                       "as good as its author's answers")
    return out("recommend", RECOMMENDED, *reasons)
