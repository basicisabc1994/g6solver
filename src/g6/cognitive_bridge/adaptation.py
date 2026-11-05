# SPDX-License-Identifier: Apache-2.0
"""Failure-mode composition and conservative, frozen-evaluator adaptation gating.

This module can recommend an isolated shadow trial, not activate a guard,
change protected evaluators, mint G6 `verified`, or bypass owner merge.
Real separation requires evaluator-owned records plus isolated execution.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .assurance import CandidateDossier

HEX = re.compile(r"^[0-9a-f]{64}$")
PROTECTED_PARTS = ("test", "fixture", "oracle", "verifier", "evaluator", "approval", "promotion", "gate",
                   "certificate", "invariant", "policy", "ledger", "vault", "harness",
                   "risk", "benchmark", "pyproject.toml", "lock", ".github")


@dataclass(frozen=True)
class FailureModeFinding:
    code: str
    criterion_id: str
    artifact_digest: str
    evidence_ref: str
    failure_detail: str
    severity: str
    status: str = "observed_failure"  # Not an independently established root cause


def detect_failure_modes(dossier: CandidateDossier) -> tuple[FailureModeFinding, ...]:
    if not dossier.intact():
        raise ValueError("dossier content digest mismatch")
    out = []
    for entry in dossier.payload["checks"]:
        if entry["result"] == "passed":
            continue
        artifact = entry.get("artifact") or {}
        for code in entry["failure_modes"] or ("unclassified_assurance_gap",):
            out.append(FailureModeFinding(
                code=code, criterion_id=entry["criterion_id"],
                artifact_digest=artifact.get("sha256", ""),
                evidence_ref=artifact.get("source_ref", ""),
                failure_detail=entry.get("reason", "not evaluated"),
                severity=entry["severity"]))
    return tuple(out)


@dataclass(frozen=True)
class MitigationHypothesis:
    id: str
    failure_code: str
    root_cause_hypothesis: str
    guard_mechanism: str
    falsifying_case: str
    origin: str = "model_proposed"  # proposal only; not evidence


class CompositionalFailureModel:
    """Links observed defects to hypotheses about guards; never conflates them."""
    def map(self, findings: Sequence[FailureModeFinding],
            proposals: Sequence[MitigationHypothesis]) -> dict[str, Any]:
        codes = {x.code for x in findings}
        edges = []
        unresolved = []
        for proposed in proposals:
            if proposed.failure_code not in codes:
                unresolved.append(proposed.id)
                continue
            if not all((proposed.root_cause_hypothesis, proposed.guard_mechanism, proposed.falsifying_case)):
                raise ValueError("guard proposal needs falsification and an explicit mechanism")
            edges.append({"from": proposed.failure_code, "to": proposed.id,
                          "edge": "proposed_mitigation_not_established",
                          "test_to_falsify": proposed.falsifying_case})
        return {"observed": [vars(f) for f in findings],
                "candidate_guards": [vars(p) for p in proposals],
                "edges": edges, "unmatched_proposals": unresolved,
                "completion_state": "qualified-draft",
                "warning": "A failure signature is not a confirmed root cause; an LLM's guard is not an established mitigation."}


@dataclass(frozen=True)
class GuardChange:
    id: str
    code_digest: str
    baseline_digest: str
    suite_digest: str
    changed_paths: tuple[str, ...]
    target_component: str
    candidate_actor: str
    evaluator_actor: str
    mitigates: tuple[str, ...]
    previously_used_case_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not all((self.id, self.target_component, self.candidate_actor, self.evaluator_actor)):
            raise ValueError("change identity, author and evaluator required")
        if not all(HEX.fullmatch(x) for x in (self.code_digest, self.baseline_digest, self.suite_digest)):
            raise ValueError("patch, baseline and frozen suite require SHA-256 pins")
        if not self.mitigates or not self.changed_paths:
            raise ValueError("change must name the observed failure modes and modified paths")


@dataclass(frozen=True)
class PairedTrial:
    case_id: str
    suite_digest: str
    baseline_passed: bool
    adapted_passed: bool
    critical: bool
    phase: str
    evaluator_receipt: str

    def __post_init__(self) -> None:
        if not self.case_id or not self.evaluator_receipt or not HEX.fullmatch(self.suite_digest):
            raise ValueError("paired trial must bind suite and evaluator receipt")
        if self.phase not in ("holdout", "shadow"):
            raise ValueError("paired trial must be separately held out")
        if any(type(x) is not bool for x in (self.baseline_passed, self.adapted_passed, self.critical)):
            raise ValueError("paired outcomes must be booleans")


@dataclass(frozen=True)
class ControlTrial:
    case_id: str
    correct_detection: bool
    category: str  # negative | positive
    evaluator_receipt: str

    def __post_init__(self) -> None:
        if self.category not in ("negative", "positive"):
            raise ValueError("control must be known-positive or planted-negative")
        if type(self.correct_detection) is not bool or not self.case_id or not self.evaluator_receipt:
            raise ValueError("control outcome invalid")


def exact_sign_p(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / (2 ** n)


def protected_paths(paths: Sequence[str]) -> list[str]:
    """Conservative local denylist PLUS real G6 protected hits when installed.

    A failure in G6's existing policy fails closed rather than being silently
    replaced by this extension's shorter static list.
    """
    local = [p for p in paths if p.startswith("/") or ".." in p.replace("\\", "/").split("/")
             or p.replace("\\", "/").startswith(("src/g6/core/", "g6/core/", ".github/"))
             or any(part in p.lower().replace("\\", "/") for part in PROTECTED_PARTS)]
    try:
        from g6.core.adaptation_gate import protected_hits as existing_policy
    except ImportError:
        return local  # standalone unit-test environment; not a production approval
    try:
        upstream = existing_policy(tuple(paths))
    except Exception:
        return list(paths)  # upstream policy broken => refuse every changed path
    return sorted(set(local).union(upstream))


class AdaptationPromotionGate:
    def __init__(self, *, admit_pair: Callable[[PairedTrial], bool] | None = None,
                 admit_control: Callable[[ControlTrial], bool] | None = None,
                 min_cases: int = 20, min_discordant: int = 6,
                 alpha: float = 0.05, max_evaluations: int = 5):
        if not 1 <= min_cases <= 100000 or not 1 <= min_discordant <= min_cases:
            raise ValueError("invalid minimum sample sizes")
        if not 0 < alpha < 1 or not 1 <= max_evaluations <= 100:
            raise ValueError("invalid multiplicity policy")
        self.admit_pair, self.admit_control = admit_pair, admit_control
        self.min_cases, self.min_discordant = min_cases, min_discordant
        self.alpha, self.max_evaluations = alpha, max_evaluations

    def assess(self, change: GuardChange, pairs: Sequence[PairedTrial],
               controls: Sequence[ControlTrial], *, detected_failures: Sequence[str]) -> dict[str, Any]:
        violations: list[str] = []
        if change.candidate_actor == change.evaluator_actor:
            violations.append("candidate and evaluator are not independent")
        protected = protected_paths(change.changed_paths)
        if protected:
            violations.append("proposed patch changes protected evaluator/policy paths: " + ", ".join(protected))
        if not set(change.mitigates).issubset(set(detected_failures)):
            violations.append("proposed guard is not bound to observed failure modes")
        if self.admit_pair is None or self.admit_control is None:
            violations.append("independent evaluator admission callbacks are missing")
        if len({x.case_id for x in pairs}) != len(pairs):
            violations.append("duplicate held-out case IDs")
        if any(x.case_id in change.previously_used_case_ids for x in pairs):
            violations.append("test set overlaps adaptation/development cases")
        if any(x.suite_digest != change.suite_digest for x in pairs):
            violations.append("evaluation suite digest differs from frozen suite")
        if any(x.phase != "holdout" for x in pairs):
            violations.append("promotion measurements must come from held-out cases")
        if any(x.case_id in {c.case_id for c in controls} for x in pairs):
            violations.append("controls and held-out measurements overlap")
        if self.admit_pair is not None:
            for x in pairs:
                try:
                    accepted = self.admit_pair(x)
                except Exception:
                    accepted = False
                if accepted is not True:
                    violations.append(f"evaluator did not authenticate paired trial {x.case_id}")
                    break
        if self.admit_control is not None:
            for x in controls:
                try:
                    accepted = self.admit_control(x)
                except Exception:
                    accepted = False
                if accepted is not True:
                    violations.append(f"evaluator did not authenticate control {x.case_id}")
                    break
        types = {x.category for x in controls}
        if types != {"positive", "negative"} or not all(x.correct_detection for x in controls):
            violations.append("both positive and negative controls must be correctly detected")
        if len(pairs) < self.min_cases:
            violations.append("insufficient untouched held-out pairs")
        wins = sum(x.adapted_passed and not x.baseline_passed for x in pairs)
        losses = sum(x.baseline_passed and not x.adapted_passed for x in pairs)
        regressions = [x.case_id for x in pairs if x.critical and x.baseline_passed and not x.adapted_passed]
        if regressions:
            violations.append("critical nonregression failed: " + ", ".join(regressions[:8]))
        if wins + losses < self.min_discordant:
            violations.append("insufficient discriminating pairs")
        if wins <= losses:
            violations.append("candidate has no positive paired advantage")
        p_value = exact_sign_p(wins, losses)
        threshold = self.alpha / self.max_evaluations  # preregistered Bonferroni bound
        if p_value > threshold:
            violations.append(f"paired sign test not below preregistered threshold {threshold:.6g}")
        return {"completion_state": "blocked-escalated" if violations else "qualified-draft",
                "recommendation": "reject_or_redesign" if violations else "recommend_isolated_shadow_only",
                "candidate": change.id, "candidate_digest": change.code_digest,
                "baseline_digest": change.baseline_digest,
                "frozen_suite_digest": change.suite_digest,
                "n_pairs": len(pairs), "wins": wins, "losses": losses,
                "ties": len(pairs) - wins - losses,
                "critical_regression_cases": regressions,
                "p_one_sided": p_value, "multiplicity_corrected_threshold": threshold,
                "blocked_reasons": violations,
                "requires_owner_authorisation": True,
                "activation_authority": "none; an independent G6 approval and merge remain necessary",
                "scope": "observed held-out cases; no global guarantee of monotonic improvement"}


__all__ = ["FailureModeFinding", "MitigationHypothesis", "CompositionalFailureModel",
           "detect_failure_modes", "GuardChange", "PairedTrial", "ControlTrial",
           "AdaptationPromotionGate", "exact_sign_p", "protected_paths"]
