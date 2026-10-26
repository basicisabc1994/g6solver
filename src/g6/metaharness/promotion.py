# SPDX-License-Identifier: Apache-2.0
"""Optional readiness cap. Modes do not authorise actions or bypass the existing meta-gate."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import digest

MODES = ("shadow", "draft", "assisted", "autopilot")


class PromotionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft: float = Field(ge=0, le=1, allow_inf_nan=False)
    assisted: float = Field(ge=0, le=1, allow_inf_nan=False)
    autopilot: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def monotone(self):
        if not self.draft <= self.assisted <= self.autopilot:
            raise ValueError("promotion pass rates must not decrease")
        return self


class PromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requested: Literal["shadow", "draft", "assisted", "autopilot"]
    report_digest: str = ""
    accepted_by: str = Field("", max_length=200)
    security_claims: dict[str, StrictBool] = Field(default_factory=dict, max_length=64)


def assess(mh, request: PromotionRequest | None = None):
    from g6.metaharness.api import _blocked, _envelope, judging_sha256

    refused = mh._refusal()
    if refused:
        return refused
    if mh.store.confirmed().get("judging_sha256") != judging_sha256():
        return _blocked("G6_E_META_PROTECTED", "the judging code changed since confirmation")
    changed = mh._vault_changed()
    if changed:
        return changed
    policy = mh.workflow.promotion_policy
    if policy is None:
        return _blocked("G6_E_META_PROMOTION_DISABLED", "no promotion policy was confirmed")
    _, profile = mh._head()
    rows = [r for r in mh.ledger.rows("ticket") if r.get("kind") == "promotion"
            and r.get("profile_digest") == profile.digest() and r.get("workflow_digest") == mh.workflow.digest()]
    current = rows[-1]["mode"] if rows else "shadow"
    wanted = request.requested if request else current
    claims = {"accepted_by": request.accepted_by if request else "", "security": dict(request.security_claims) if request else {},
              "kind": "claim", "identity": "recorded as given; not authenticated"}
    evaluations = [r for r in mh.ledger.rows("evaluation") if r.get("split") == "held_out"
                   and r.get("candidate_digest") == profile.digest() and r.get("workflow_digest") == mh.workflow.digest()]
    ident = request.report_digest if request and request.report_digest else (
        evaluations[-1].get("report_digest", "") if evaluations else "")
    report, why = mh._recorded("report", ident) if ident else (None, "no recorded held-out report")
    valid = (bool(evaluations) and ident == evaluations[-1].get("report_digest")
             and report is not None and report.split == "held_out" and not report.partial
             and report.n >= mh.workflow.policy.min_cases and report.n == len(mh.workflow.suite.held_out)
             and report.split_digest == mh.workflow.suite.digest("held_out")
             and report.workflow_digest == mh.workflow.digest() and report.candidate_digest == profile.digest()
             and report.reuse_index <= mh.workflow.policy.max_evaluations
             and report.negative_control.get("p_one_sided", 1.0) <= mh.workflow.policy.alpha)
    rate = sum(c["candidate"]["success"] for c in report.cases) / report.n if valid else None
    # No declaration raises this cap. An adverse security claim may tighten it.
    cap = 0
    if rate is not None and not any(v is False for v in claims["security"].values()):
        for i, mode in enumerate(MODES[1:], 1):
            if rate < getattr(policy, mode):
                break
            cap = i
    rank, target = MODES.index(current), MODES.index(wanted)
    reason = why if not valid else "held-out pass rate measured by this harness"
    mode = MODES[min(rank, target, cap)]  # the autonomous path can only move down
    if target > rank:
        who = claims["accepted_by"].strip()
        used = {r["report_digest"] for r in rows if r.get("promoted")}
        # Each step has its own report and a separately recorded person's decision. A claim alone opens nothing.
        if (valid and cap >= target and target == rank + 1 and who and not is_automated(who)
                and ident not in used and request is not None and request.report_digest):
            mode = wanted
        else:
            reason = "each upward step needs its own full held-out pass rate, unused report and person's approval claim"
    row = {"kind": "promotion", "workflow_digest": mh.workflow.digest(), "profile_digest": profile.digest(),
           "mode": mode, "requested": wanted, "report_digest": ident, "pass_rate": rate, "claims": claims,
           "promoted": MODES.index(mode) > rank, "reason": reason}
    # A fresh explicit claim is recorded even when refused; a repeated monitor consultation writes nothing.
    if request is not None or mode != current:
        mh.ledger.append("ticket", f"promotion-{len(mh.ledger.rows('ticket'))}-{digest(row)}", row)
    state = "qualified-draft" if mode == wanted else "blocked-escalated"
    return _envelope(state, "G6_E_META_PROMOTION" if state == "qualified-draft" else "G6_E_META_PROMOTION_REQUIRED",
                     reason, mode=mode, requested=wanted, claims=claims, pass_rate=rate, report_digest=ident)
