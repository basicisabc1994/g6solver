# SPDX-License-Identifier: Apache-2.0
"""Failure-mode composition and conservative, frozen-evaluator adaptation gating.

This module can recommend an isolated shadow trial, not activate a guard,
change protected evaluators, mint G6 `verified`, or bypass owner merge.
Real separation requires evaluator-owned records plus isolated execution.

The gate's statistic is the meta-harness gate's own (``g6.reasoning.sign_test_p`` at alpha / max_evaluations, the
rule ``g6.metaharness.schema.EvidencePolicy`` states). What a change may write is an allow-list: only the files this
component declares adaptable (:data:`ADAPTABLE`), compared by file identity however a path is spelled or linked, and
never one G6's own policy protects (``g6.core.adaptation_gate.protected_hits``); everything else is refused. Its other
conditions are stricter additions on top.
"""
from __future__ import annotations

import inspect
import os
import re
import stat
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Sequence

from g6.core.adaptation_gate import protected_hits
from g6.reasoning import sign_test_p

from .assurance import CandidateDossier
from .codes import ADAPTATION_REFUSED, affirmed, refusal, settled
from .snapshot import publish, record_data, fresh, number, record, snapshot

HEX = re.compile(r"^[0-9a-f]{64}$")


@record
class FailureModeFinding:
    code: str
    criterion_id: str
    artifact_digest: str
    evidence_ref: str
    failure_detail: str
    severity: str
    status: str = "observed_failure"  # Not an independently established root cause


def detect_failure_modes(dossier: CandidateDossier) -> tuple[FailureModeFinding, ...]:
    dossier = snapshot(dossier, where="dossier")
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
    return publish(tuple(out), where="detect_failure_modes")


@record
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
        findings = snapshot(findings, where="findings")
        proposals = snapshot(proposals, where="proposals")
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
        return publish(settled(None, observed=[record_data(f) for f in findings],
                       candidate_guards=[record_data(p) for p in proposals],
                       edges=edges, unmatched_proposals=unresolved,
                       warning="A failure signature is not a confirmed root cause; an LLM's guard is not an established "
                               "mitigation."), where="CompositionalFailureModel.map")


@record
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


@record
class PairedTrial:
    case_id: str
    suite_digest: str
    baseline_passed: bool
    adapted_passed: bool
    critical: bool
    phase: str
    evaluator_receipt: str
    #: What was evaluated: the candidate patch's and the baseline's digests (``GuardChange.code_digest`` and
    #: ``baseline_digest``). A trial credits only the change with both; a trial that names neither credits nothing.
    candidate_digest: str = ""
    baseline_digest: str = ""

    def __post_init__(self) -> None:
        if not self.case_id or not self.evaluator_receipt or not HEX.fullmatch(self.suite_digest):
            raise ValueError("paired trial must bind suite and evaluator receipt")
        if any(x and not HEX.fullmatch(x) for x in (self.candidate_digest, self.baseline_digest)):
            raise ValueError("a paired trial's candidate and baseline are SHA-256 pins")
        if self.phase not in ("holdout", "shadow"):
            raise ValueError("paired trial must be separately held out")
        if any(type(x) is not bool for x in (self.baseline_passed, self.adapted_passed, self.critical)):
            raise ValueError("paired outcomes must be booleans")


@record
class ControlTrial:
    case_id: str
    correct_detection: bool
    category: str  # negative | positive
    evaluator_receipt: str
    #: What the control evaluated: the frozen suite's digest (``GuardChange.suite_digest``). A control credits only the
    #: change whose suite it names; a control that names none credits nothing.
    suite_digest: str = ""

    def __post_init__(self) -> None:
        if self.category not in ("negative", "positive"):
            raise ValueError("control must be known-positive or planted-negative")
        if type(self.correct_detection) is not bool or not self.case_id or not self.evaluator_receipt:
            raise ValueError("control outcome invalid")
        if self.suite_digest and not HEX.fullmatch(self.suite_digest):
            raise ValueError("a control's suite is a SHA-256 pin")


#: The one-sided exact sign test the meta-harness gate decides by (``g6.metaharness.evaluate`` uses it): one rule for
#: "the change won on paired cases", whichever gate asks.
exact_sign_p = sign_test_p


#: The checkout: a changed path is named relative to it, as a change names it.
_ROOT = Path(__file__).resolve().parents[3]


def judged_files() -> frozenset[Path]:
    """The source files that define the code judging a bridge result or its evidence, listed from the functions and
    classes themselves (never from file names): this gate and its path guard, the assurance evaluator and its checker
    type, the selector that credits trials, the conflict gate, the orchestrator's assessment, the executor, the one maker
    of refusals, the sign test, ``g6.epistemic_integrity``'s adjudication, G6's protected-path policy and G6's door.
    The gate admits none of them whatever :data:`ADAPTABLE` says (a declaration naming one admits nothing)."""
    from g6.core import door, reliability_envelope
    from g6.epistemic_integrity import adjudicator
    from g6.epistemic_integrity.markers import markers

    from . import assurance, codes, epistemic, metacognition, mixed_methods, orchestration

    judges = (AdaptationPromotionGate.assess, protected_paths, assurance.InProcessAssuranceEvaluator.evaluate,
              assurance.FrozenVerifier, metacognition.MetacognitiveMethodSelector,
              epistemic.CrossMethodConflictGate.reconcile, orchestration.CognitiveAssuranceOrchestrator.assess,
              mixed_methods.MixedMethodExecutor.execute, codes.refusal, sign_test_p, adjudicator.adjudicate_claim,
              adjudicator.detect_contested, adjudicator.collapse_independence, markers, protected_hits,
              door.invoke_checked,
              door.door_reply, reliability_envelope.door_witness)
    return frozenset(Path(inspect.getsourcefile(judge)).resolve() for judge in judges)


#: The only files a guard change to this component may write, relative to the checkout: the bridge's guard modules,
#: which it does not ship yet (a change creates them). Every other path is refused, the judges and G6's core among
#: them; a candidate is compared with these by file identity (:func:`_identity`), never by its spelling.
ADAPTABLE: tuple[str, ...] = ("src/g6/cognitive_bridge/guard.py", "src/g6/cognitive_bridge/method_adapter.py")


def _absolute(path: str) -> bool:
    return Path(path).is_absolute() or PureWindowsPath(path).is_absolute() or path.startswith(("/", "\\"))


def _identity(path: Path) -> tuple[Any, ...]:
    """What ``path`` names, however it is spelled or linked: an existing file's (device, inode), or, for a file not
    made yet, the location the file system resolves it to (links followed, case folded where the file system folds
    it). Raises for anything a change may not write by name: not a regular file, or a file with another name too (a
    hard link). The lookup itself decides: only "no such file" in a folder that can be listed is a file not made yet;
    any other lookup error raises, and so refuses the path."""
    resolved = os.path.normcase(os.path.realpath(path))
    try:
        found = os.lstat(resolved)
    except FileNotFoundError:
        os.listdir(os.path.dirname(resolved))  # the folder it would be made in must be there and listable
        return ("new", resolved)
    if not stat.S_ISREG(found.st_mode) or found.st_nlink != 1:
        raise ValueError(f"{path} is not a regular file with one name")
    return ("file", found.st_dev, found.st_ino)


def _declared() -> frozenset[tuple[Any, ...]]:
    """The identities of :data:`ADAPTABLE`. Raises when a declared file is reached through a link or is one of the
    judges (:func:`judged_files`): then the declaration itself is wrong and nothing is admitted."""
    judges = {("file", found.st_dev, found.st_ino) for found in map(os.stat, judged_files())}
    found = set()
    for name in ADAPTABLE:
        path = _ROOT / name
        if os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.abspath(path)):
            raise ValueError(f"the adaptable file {name} is reached through a link")
        found.add(_identity(path))
    if found & judges:
        raise ValueError("an adaptable file is one of the files that judge the bridge")
    return frozenset(found)


def _admitted(path: str, declared: frozenset[tuple[Any, ...]]) -> bool:
    """``path`` (relative to the checkout) is one of the declared adaptable files; any error refuses it."""
    if _absolute(path):
        return False
    try:
        return _identity(_ROOT / path) in declared
    except Exception:
        return False


def protected_paths(paths: Sequence[str]) -> list[str]:
    """The changed paths this gate refuses: every path that is not one of the files this component declares adaptable
    (:data:`ADAPTABLE`, compared by file identity), and every path G6's own policy protects
    (``g6.core.adaptation_gate.protected_hits``). An allow-list, so a judge nobody listed is refused too. Any error
    resolving a path refuses that path; an error in the declaration or in G6's policy refuses every path."""
    paths = snapshot(paths, where="changed paths")
    try:
        declared = _declared()
        upstream = set(protected_hits(tuple(paths)))
    except Exception:
        return sorted(set(paths))
    return sorted(p for p in set(paths) if p in upstream or not _admitted(p, declared))


class AdaptationPromotionGate:
    def __init__(self, *, admit_pair: Callable[[PairedTrial], bool] | None = None,
                 admit_control: Callable[[ControlTrial], bool] | None = None,
                 min_cases: int = 20, min_discordant: int = 6,
                 alpha: float = 0.05, max_evaluations: int = 5):
        self.admit_pair, self.admit_control = admit_pair, admit_control
        self.min_cases = snapshot(min_cases, where="min_cases")
        self.min_discordant = snapshot(min_discordant, where="min_discordant")
        self.alpha = snapshot(alpha, where="alpha")
        self.max_evaluations = snapshot(max_evaluations, where="max_evaluations")
        for name in ("min_cases", "min_discordant", "alpha", "max_evaluations"):  # numbers, by the one number rule
            number(getattr(self, name), where=name)
        if not 1 <= self.min_cases <= 100000 or not 1 <= self.min_discordant <= self.min_cases:
            raise ValueError("invalid minimum sample sizes")
        if not 0 < self.alpha < 1 or not 1 <= self.max_evaluations <= 100:
            raise ValueError("invalid multiplicity policy")

    def assess(self, change: GuardChange, pairs: Sequence[PairedTrial],
               controls: Sequence[ControlTrial], *, detected_failures: Sequence[str]) -> dict[str, Any]:
        # Everything is taken where it enters, before anything is checked: what is admitted is what is counted.
        change = snapshot(change, where="change")
        pairs = snapshot(pairs, where="pairs")
        controls = snapshot(controls, where="controls")
        detected_failures = snapshot(detected_failures, where="detected_failures")
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
        if any((x.candidate_digest, x.baseline_digest) != (change.code_digest, change.baseline_digest) for x in pairs):
            violations.append("a paired trial was not measured on this change's candidate and baseline")
        if any(x.suite_digest != change.suite_digest for x in controls):
            violations.append("a control was not run on this change's frozen suite")
        if any(x.phase != "holdout" for x in pairs):
            violations.append("promotion measurements must come from held-out cases")
        if any(x.case_id in {c.case_id for c in controls} for x in pairs):
            violations.append("controls and held-out measurements overlap")
        # Each admission hook is handed a copy, so what it admits is what is counted, whatever it does to its copy.
        if self.admit_pair is not None:
            for x in pairs:
                try:
                    accepted = affirmed(self.admit_pair(fresh(x)))
                except Exception:
                    accepted = False
                if not accepted:
                    violations.append(f"evaluator did not authenticate paired trial {x.case_id}")
                    break
        if self.admit_control is not None:
            for x in controls:
                try:
                    accepted = affirmed(self.admit_control(fresh(x)))
                except Exception:
                    accepted = False
                if not accepted:
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
        p_value = sign_test_p(wins, losses)
        threshold = self.alpha / self.max_evaluations  # preregistered Bonferroni bound, as EvidencePolicy.threshold
        if p_value > threshold:
            violations.append(f"paired sign test not below preregistered threshold {threshold:.6g}")
        return publish(settled(refusal(ADAPTATION_REFUSED, "; ".join(violations)) if violations else None,
                       recommendation="reject_or_redesign" if violations else "recommend_isolated_shadow_only",
                       candidate=change.id, candidate_digest=change.code_digest,
                       baseline_digest=change.baseline_digest,
                       frozen_suite_digest=change.suite_digest,
                       n_pairs=len(pairs), wins=wins, losses=losses,
                       ties=len(pairs) - wins - losses,
                       critical_regression_cases=regressions,
                       p_one_sided=p_value, multiplicity_corrected_threshold=threshold,
                       blocked_reasons=violations,
                       requires_owner_authorisation=True,
                       activation_authority="none; an independent G6 approval and merge remain necessary",
                       scope="observed held-out cases; no global guarantee of monotonic improvement"), where="AdaptationPromotionGate.assess")


__all__ = ["FailureModeFinding", "MitigationHypothesis", "CompositionalFailureModel",
           "detect_failure_modes", "GuardChange", "PairedTrial", "ControlTrial",
           "AdaptationPromotionGate", "ADAPTABLE", "exact_sign_p", "judged_files", "protected_paths"]
