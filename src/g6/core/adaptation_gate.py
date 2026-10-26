# SPDX-License-Identifier: Apache-2.0
"""Local validation and the promotion gate for an adaptation proposal (graduated from ``research.ahi``; ECHA §6
Phases C and E; audit 037).

``validate_locally`` runs a candidate guard on pre-registered controls: at least one negative (a defect it must flag)
and one positive (valid behaviour it must let through), none authored by the proposer. ``decide_promotion`` is the gate:
an unknown verdict blocks, a failure rejects, and the most it grants is ``shadow_approved``, isolated shadow use. It never
activates anything: a person still decides.

Fixed on graduation (audit 037):

- GATE-001: an automated reviewer name (``auto:``...) is refused, as the operator queue refuses it.
- GATE-002: a local validation is bound to the proposal's content digest, not only its id, so a proposal edited after it
  was validated is blocked.
- GATE-003: the protected list now covers gates, judges, verifiers, invariants, approvals, ledgers, vaults, the stop,
  the meta-harness and every test or test configuration (tests are evaluators), not only oracle and fixture names.

The verdicts and ``owner_confirmed`` are the caller's statements: the gate cannot check them, and its decision says so
only by what it was given.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from g6.core.ledger import digest

PROPOSAL_STATUSES = ("proposed", "local_validated", "shadow_approved", "rejected", "blocked")
VERDICTS = ("pass", "fail", "unknown")
# What judges a proposal: a path containing any of these, in any case, is protected.
PROTECTED_FRAGMENTS = (
    "oracle", "fixture", "grader", "standard", "threshold", "promotion", "evaluator", "answer_key", "holdout",
    "held_out", "gate", "judge", "verif", "invariant", "approv", "ledger", "vault", "metaharness", "core/stop",
    "self_training/", "conftest",
)
# Tests and test configuration are evaluators too: matched per path segment, so ``latest.py`` is not a test.
_TEST_SEGMENT = re.compile(r"^(tests?|test_.*|.*_test\.py|pytest\.ini|tox\.ini|setup\.cfg|pyproject\.toml|noxfile\.py)$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")  # noqa: TRY004 - every refusal is one ValueError to a caller
    if not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _texts(name: str, value: Any, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{name} must be a list of non-empty texts")
    if required and not value:
        raise ValueError(f"{name} is required")
    return tuple(value)


def _hex64(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise ValueError(f"{name} must be a sha256 hex digest")
    return value


def is_automated(name: str) -> bool:
    return name.strip().lower().startswith("auto:")


def protected_hits(paths: tuple[str, ...]) -> list[str]:
    """The paths that touch what judges a proposal."""
    hits = []
    for path in paths:
        low = path.replace("\\", "/").lower()
        if any(f in low for f in PROTECTED_FRAGMENTS) or any(_TEST_SEGMENT.match(s) for s in low.split("/") if s):
            hits.append(path)
    return hits


@dataclass(frozen=True)
class AdaptationProposal:
    proposal_id: str
    proposer: str
    source_case_ids: tuple[str, ...]
    target_component: str
    intended_change: str              # the exact change, not a summary
    predicted_benefit: str            # pre-registered, for one defect class
    falsifiers: tuple[str, ...]
    test_plan: str
    candidate_hash: str
    baseline_hash: str
    transfer_hypothesis: str
    cost_cap: float
    rollback: str
    touched_paths: tuple[str, ...] = ()
    status: str = "proposed"

    def __post_init__(self) -> None:
        for name in ("proposal_id", "proposer", "target_component", "intended_change", "predicted_benefit",
                     "test_plan", "transfer_hypothesis", "rollback"):
            _text(name, getattr(self, name))
        _texts("source_case_ids", list(self.source_case_ids), required=True)
        _texts("falsifiers", list(self.falsifiers), required=True)
        _texts("touched_paths", list(self.touched_paths))
        _hex64("candidate_hash", self.candidate_hash)
        _hex64("baseline_hash", self.baseline_hash)
        if self.candidate_hash == self.baseline_hash:
            raise ValueError("candidate_hash equals baseline_hash: there is no change to evaluate")
        if not (isinstance(self.cost_cap, (int, float)) and math.isfinite(self.cost_cap) and self.cost_cap > 0):
            raise ValueError("cost_cap must be a finite positive number")
        if self.status not in PROPOSAL_STATUSES:
            raise ValueError(f"status must be one of {', '.join(PROPOSAL_STATUSES)}")

    @property
    def digest(self) -> str:
        return digest(self.__dict__)


@dataclass(frozen=True)
class Control:
    """A pre-registered control case. ``negative``: a defect the guard must flag. ``positive``: valid
    behaviour (e.g. an authorised migration) the guard must let through."""

    name: str
    kind: str
    case: Any
    authored_by: str

    def __post_init__(self) -> None:
        _text("name", self.name)
        _text("authored_by", self.authored_by)
        if self.kind not in ("negative", "positive"):
            raise ValueError("kind must be negative or positive")


@dataclass(frozen=True)
class LocalValidation:
    proposal_id: str
    status: str                     # local_validated | rejected | blocked
    outcomes: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...]
    proposal_digest: str = ""       # the content validated; "" (unbound) never passes the gate

    @property
    def digest(self) -> str:
        return digest({"proposal_id": self.proposal_id, "status": self.status, "outcomes": list(self.outcomes),
                       "reasons": list(self.reasons), "proposal_digest": self.proposal_digest})


def validate_locally(proposal: AdaptationProposal, guard: Callable[[Any], bool],
                     controls: list[Control]) -> LocalValidation:
    """Run the candidate guard on every control. ``guard(case)`` returns True when it flags the case.

    Needs at least one negative and one positive control, none authored by the proposer. A guard that
    raises is ``unknown`` for that control, which blocks; a missed defect or a false rejection rejects.
    """
    reasons: list[str] = []
    kinds = {c.kind for c in controls}
    if kinds != {"negative", "positive"}:
        reasons.append("needs at least one negative and one positive control")
    if any(c.authored_by.strip().lower() == proposal.proposer.strip().lower() for c in controls):
        reasons.append("controls authored by the proposer are not independent")
    outcomes = []
    for control in controls:
        try:
            case_digest = digest(control.case)
        except ValueError:
            case_digest = "not-json"
        try:
            flagged = guard(control.case)
            if not isinstance(flagged, bool):
                raise TypeError(f"guard returned {type(flagged).__name__}, not bool")
            ok = flagged if control.kind == "negative" else not flagged
            outcomes.append({"control": control.name, "kind": control.kind, "result": "pass" if ok else "fail",
                             "case_digest": case_digest})
        except Exception as exc:  # noqa: BLE001 - a guard that cannot run is unknown, never a pass
            outcomes.append({"control": control.name, "kind": control.kind, "result": "unknown",
                             "error": f"{type(exc).__name__}: {exc}", "case_digest": case_digest})
    results = [o["result"] for o in outcomes]
    if "unknown" in results:
        reasons.append("a control could not be evaluated")
    if reasons:
        status = "blocked"
    elif "fail" in results:
        status = "rejected"
        reasons += [f"{o['kind']} control {o['control']!r} failed" for o in outcomes if o["result"] == "fail"]
    else:
        status = "local_validated"
    return LocalValidation(proposal.proposal_id, status, tuple(outcomes), tuple(reasons), proposal.digest)


@dataclass(frozen=True)
class PromotionDecision:
    proposal_id: str
    policy_version: str
    reviewer: str
    disposition: str                 # shadow_approved | rejected | blocked
    reasons: tuple[str, ...]
    evidence_hashes: tuple[str, ...]
    verdicts: dict[str, str]

    @property
    def digest(self) -> str:
        return digest(self.__dict__)


def decide_promotion(proposal: AdaptationProposal, validation: LocalValidation | None, *, policy_version: str,
                     reviewer: str, owner_confirmed: bool, regression: str, budget: str,
                     security: str) -> PromotionDecision:
    """The gate. Unknown blocks; a failure rejects; the most it grants is isolated shadow use."""
    _text("policy_version", policy_version)
    verdicts = {"regression": regression, "budget": budget, "security": security}
    blocked, rejected = [], []
    for name, value in verdicts.items():
        if value not in VERDICTS:
            raise ValueError(f"{name} must be one of {', '.join(VERDICTS)}")
        if value == "unknown":
            blocked.append(f"{name} verdict unknown")
        elif value == "fail":
            rejected.append(f"{name} check failed")
    hits = protected_hits(proposal.touched_paths)
    if hits:
        blocked.append(f"touches what judges it: {', '.join(hits)}")
    if not reviewer.strip():
        blocked.append("no reviewer")
    elif reviewer.strip().lower() == proposal.proposer.strip().lower():
        blocked.append("the reviewer is the proposer")
    elif is_automated(reviewer):
        blocked.append("the reviewer is an automated name (auto:), not a person")
    if owner_confirmed is not True:
        blocked.append("owner has not confirmed")
    if validation is None:
        blocked.append("no local validation")
    elif validation.proposal_id != proposal.proposal_id:
        blocked.append("local validation is for a different proposal")
    elif validation.proposal_digest != proposal.digest:
        blocked.append("local validation is for a different version of this proposal (its content changed)")
    elif validation.status == "blocked":
        blocked.append("local validation blocked: " + "; ".join(validation.reasons))
    elif validation.status != "local_validated":
        rejected.append("local validation rejected: " + "; ".join(validation.reasons))
    disposition = "blocked" if blocked else ("rejected" if rejected else "shadow_approved")
    evidence = (proposal.digest,) + ((validation.digest,) if validation else ())
    return PromotionDecision(proposal.proposal_id, policy_version, reviewer, disposition,
                             tuple(blocked + rejected) or ("all mandatory inputs passed; shadow use only",),
                             evidence, verdicts)
