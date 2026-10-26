# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Stage 0 task intake and SMART acceptance check.

Library cut (audit 009 §11): feasibility is the deterministic assessor in ``g6.reliability_pipeline.feasibility`` unless
the caller injects one (``assessor=``); there is no LLM triage and no block-guidance wording. The closed-book
answer-emission flag of the archive's solver pipeline is not ported (it was default-off), so its soft-block relaxation
never applies here.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from g6.intent_engine import IntentElicitor
from g6.intent_engine.schema import IntentContract, IntentQuestion
from g6.reliability_pipeline import failure_modes
from g6.reliability_pipeline.feasibility import (
    FeasibilityAssessor,
    at_least,
    deterministic_assess,
    names_context,
)
from g6.reliability_pipeline.stages import Stage, StageResult
from g6.reliability_pipeline.task_contract import (
    SuccessCriterion,
    TaskMode,
    coerce_task_mode,
    derive_criteria,
)


@dataclass(frozen=True)
class IntakeContract:
    task: str
    acceptance_criteria: tuple[str, ...]
    resource_bound: str
    intent: IntentContract | None = None
    # TaskContract v1 (K-P0-2) — the fit-for-purpose vocabulary. All additive and
    # defaulted so the single construction site (run_intake) and every caller
    # remain byte-identical; NOTHING consumes these yet (a later, flag-gated gate
    # does), so they change no reliability state today.
    mode: TaskMode = TaskMode.EVIDENTIARY
    #: Structured success criteria (criticality + verifier). Legacy callers get a
    #: non-critical, verifier-less criterion per acceptance-criteria string, which
    #: can never trip the verifier-required rule.
    criteria: tuple[SuccessCriterion, ...] = ()
    evidence_standard: str = ""
    forbidden_claims: tuple[str, ...] = ()
    escalation_triggers: tuple[str, ...] = ()


REPLAY_ANSWER, REPLAY_REQUIREMENT = failure_modes.REPLAY_ANSWER, failure_modes.REPLAY_REQUIREMENT


def replay_question(task: str) -> IntentQuestion | None:
    """One bounded supported-phrasing rule; not general ambiguity detection.

    Recognize an underspecified event-handler request, not arbitrary prose or
    broader concurrency/durability promises. Never infer the answer from tests.
    """
    if not re.fullmatch(
            r"make (?:this|the) [a-z ]{0,80}event handler "
            r"(?:idempotent|safe for retries|ignore duplicate events)\.?",
            " ".join(task.lower().split())):
        return None
    return IntentQuestion(
        field="replay_semantics",
        question=("Must an identical retry with the same stable event ID leave state unchanged even after a "
                  "different event, within the same sequential in-memory state?"),
        reason=("Adjacent-duplicate tests can miss this replay. The available constructed check covers sequential "
                "in-memory state only, not concurrent deliveries, restarts or external effects. Specify a different "
                "requirement if those guarantees are needed; they cannot be silently excluded."))


_FILLERS = frozenset({"tbd", "todo", "na", "n a", "none", "x", "xx", "xxx", "tba", "later"})


def _filler(text: str) -> bool:
    """No word of two or more letters or digits, or a known filler."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return not any(len(w) >= 2 for w in words) or " ".join(words) in _FILLERS


def _real_criteria(raw: Any) -> list[str]:
    """The criteria in an answer (a list, or lines of text), without fillers: a "TBD" answer is no criterion."""
    items = raw.splitlines() if isinstance(raw, str) else raw if isinstance(raw, (list, tuple)) else [raw]
    return [text for text in (str(item).strip() for item in items if item is not None) if text and not _filler(text)]


# COPY: what an accepted risky intent is: the caller's word, recorded as such.
RISK_ACCEPTED_BY_CALLER = ("accepted_risky_intent is the caller's word: G6 cannot tell whether a person set it, so the "
                           "task proceeds as a qualified draft at most.")


def _extract_acceptance_criteria(request: Any) -> tuple[str, ...]:
    raw = getattr(request, "acceptance_criteria", None)
    if raw is None and isinstance(getattr(request, "context", None), dict):
        raw = request.context.get("acceptance_criteria")
    criteria: tuple[str, ...] = ()
    if isinstance(raw, str):
        criteria = tuple(item.strip() for item in raw.splitlines() if item.strip())
    elif isinstance(raw, (list, tuple)):
        criteria = tuple(str(item).strip() for item in raw if str(item).strip())
    if criteria:
        return criteria
    raw = getattr(request, "constraints", None)
    if isinstance(raw, str):
        return tuple(item.strip() for item in raw.splitlines() if item.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def derive_escalation_triggers(
    *,
    mode: TaskMode,
    criteria: tuple[SuccessCriterion, ...],
    feasibility_classification: str,
    intent: IntentContract | None,
) -> tuple[str, ...]:
    """Deterministic restatement of already-DECLARED intake facts as stop/escalation
    conditions — never generated content, only facts this request (or the
    deterministic feasibility/criteria machinery) already declared. Always derived
    and carried on ``IntakeContract.escalation_triggers`` for the caller to obey;
    the archive's ``G6_METHOD_IR`` flag and its MethodIR consumer are not ported
    (audit 009 RP-011).

    Pure + deterministic; NEVER RAISES — the whole body is guarded, so any
    internal fault (a malformed criterion, a duck-typed ``intent``, a raising
    attribute) yields one escalate trigger, never a partial or empty list
    (audit 009 RP-014: a stop condition must not fail open).

    Order (stable): exploratory-mode trigger, then feasibility-classification
    trigger, then one per critical criterion lacking an available verifier, then
    one per ``intent.non_goals`` item, then one per ``intent.failure_criteria`` item.
    """
    try:
        triggers: list[str] = []
        if mode == TaskMode.EXPLORATORY:
            triggers.append("exploratory mode: stop at qualified-draft; do not certify.")
        if feasibility_classification in {"risky", "too_large", "underspecified"}:
            triggers.append(
                f"feasibility flagged {feasibility_classification}: escalate before certifying."
            )
        for criterion in criteria or ():
            if criterion.critical and not criterion.has_available_verifier():
                triggers.append(
                    f"critical criterion '{criterion.text}' has no available verifier: escalate."
                )
        if intent is not None:
            for item in tuple(getattr(intent, "non_goals", ()) or ()) + tuple(
                getattr(intent, "failure_criteria", ()) or ()
            ):
                text = str(item).strip()
                if text:
                    triggers.append(f"stop if the output would assert or pursue '{text}'.")
        return tuple(triggers)
    except Exception:  # noqa: BLE001 - never raise; any internal fault escalates
        return ("escalation triggers could not be derived: escalate before certifying.",)


def _clarification_default_on() -> bool:
    """Resolve the default-on flag for the intake clarify gate (WS-1.1).

    OFF only when ``G6_PIPELINE_CLARIFICATION`` is one of ``{0,false,no,off}``;
    unset or any other value keeps the blocking gate ON (a typo must not turn a gate off).
    """
    raw = os.environ.get("G6_PIPELINE_CLARIFICATION")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def run_intake(
    request: Any,
    llm: Any = None,
    *,
    clarification: bool | None = None,
    assessor: Any = None,
) -> StageResult:
    """``llm`` is accepted for call-signature compatibility and ignored; ``assessor`` (anything with
    ``assess(IntentContract) -> FeasibilityReport``) may tighten the deterministic feasibility verdict, never relax it."""
    task = str(getattr(request, "goal", "") or getattr(request, "task", "") or "").strip()
    # a filler ("x", ".", "TBD", "n/a") is no criterion (audit 005 IE-002)
    criteria = tuple(c for c in _extract_acceptance_criteria(request) if not _filler(c))
    if not task:
        return StageResult.block(
            Stage.INTAKE,
            "G6_E_PIPELINE_NO_TASK",
            "Provide a concrete task or goal.",
        )
    answers = dict(getattr(request, "intent_answers", {}) or {})
    said = _real_criteria(answers.pop("success_criteria")) if "success_criteria" in answers else []
    if said:
        answers["success_criteria"] = said
    if criteria and "success_criteria" not in answers:
        answers["success_criteria"] = list(criteria)
    elicitor = IntentElicitor()
    session = elicitor.elicit(
        task,
        answers=answers,
        user_declined=bool(getattr(request, "declined_clarification", False)),
    )
    intent = session.contract
    if not criteria:
        criteria = tuple(intent.success_criteria)
    if not criteria:
        return StageResult.block(
            Stage.INTAKE,
            "G6_E_PIPELINE_NO_ACCEPTANCE",
            "Add measurable acceptance criteria before execution.",
            task=task,
            intent_session=session,
            intent_questions=session.questions,
            # C5: honest self-report — only the elicitor ran before this block
            # (feasibility never started). The pipeline ledger sweeps this key.
            components_used=("intent_engine",),
        )
    # Attached sources resolve a context gap the request names; they relax nothing else (audit 030 FEA-001).
    _has_context_sources = bool(getattr(request, "context_sources", None))
    if assessor is None:
        feasibility = FeasibilityAssessor().assess(intent, context_attached=_has_context_sources)
    else:
        feasibility = at_least(assessor.assess(intent),
                               deterministic_assess(intent, context_attached=_has_context_sources),
                               context_attached=_has_context_sources)
    _context_resolved = _has_context_sources and names_context(intent)
    # C5: honest stage self-report for the pipeline's component ledger —
    # intent_engine (IntentElicitor) + feasibility (FeasibilityAssessor)
    # genuinely ran on every path below this line.
    _COMPONENTS_USED = ("intent_engine", "feasibility")
    # TaskContract v1 (K-P0-2): derive the mode + structured criteria additively.
    # Both default to today's behavior — evidentiary mode, and one non-critical
    # verifier-less criterion per acceptance string — unless the caller opts in via
    # request.task_mode / request.success_criteria_structured. Nothing consumes
    # these yet, so this changes no reliability state (Slice 1).
    _mode = coerce_task_mode(getattr(request, "task_mode", ""))
    _structured = getattr(request, "success_criteria_structured", None) or None
    _criteria = derive_criteria(criteria, structured=_structured)
    contract = IntakeContract(
        task=task,
        acceptance_criteria=criteria,
        resource_bound="current_request",
        intent=intent,
        mode=_mode,
        criteria=_criteria,
        escalation_triggers=derive_escalation_triggers(
            mode=_mode, criteria=_criteria,
            feasibility_classification=feasibility.classification, intent=intent
        ),
    )
    # D-G: with request.context_sources attached, a context gap the request names is resolved
    # inside the assessment (it is classified as if the context were present, so it cannot mask
    # an access, infeasible or risky verdict), and the task proceeds as a qualified draft below.
    _hard_block = {"infeasible", "blocked_missing_access", "blocked_unclear_success", "blocked_missing_context"}
    if feasibility.classification in _hard_block:
        return StageResult.block(
            Stage.INTAKE,
            f"G6_E_PIPELINE_FEASIBILITY_{feasibility.classification.upper()}",
            feasibility.recommended_next_step,
            task_contract=contract,
            feasibility_report=feasibility,
            components_used=_COMPONENTS_USED,
        )
    risky = feasibility.classification == "risky"
    if risky and not bool(getattr(request, "accepted_risky_intent", False)):
        return StageResult.block(
            Stage.INTAKE,
            "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED",
            # audit 030 FEA-008: the questions are offered, and nothing reads the answers
            "A person decides whether to accept the risk; set accepted_risky_intent only on their say. The flag is "
            "the caller's word: the task then proceeds as a qualified draft at most, recorded as accepted by the "
            "caller. The reduction-to-practice questions are advisory: G6 does not check the answers.",
            task_contract=contract,
            feasibility_report=feasibility,
            intent_session=session,
            reduction_to_practice_questions=feasibility.reduction_to_practice_questions,
            components_used=_COMPONENTS_USED,
        )
    # WS-1.1 clarify gate (ask-before-answer): the elicitor already computed the
    # exact questions a vague request needs — turn them into a real block instead
    # of discarding them. Two honest unlocks: `intent_answers` (raises confidence)
    # or `declined_clarification=True` (today's path, recorded as a declined
    # assumption). Sits AFTER the hard/risky blocks (stricter gates win) and
    # BEFORE the downgrade branch, so it covers both downgrade and advance paths.
    if clarification is None:
        clarification = _clarification_default_on()
    # Proceed-and-offer (RELIABILITY.md §0 intake): when the goal is unclear, the spec
    # says to "ask targeted questions OR propose a reasonable task contract with
    # assumptions clearly labelled" — not to dead-end-block. A feasibility verdict of
    # "clear" means the goal is self-contained and answerable now (the genuinely
    # ambiguous "underspecified"/"too_large" verdicts downgrade-and-offer below, and the
    # "risky"/hard-block verdicts already returned above). So a clear task must NOT be
    # hard-blocked merely because the IntentContract lacks why/non_goals/success metadata
    # (intent-completeness confidence, not answerability). It proceeds; the questions are
    # still surfaced as a non-blocking "you can refine" offer (see warning-card note), and
    # any real high-stakes risk is gated downstream by risk-tiering + the quality gate.
    _clear_proceeds = feasibility.classification == "clear"
    if (
        clarification
        and session.questions
        and session.contract.confidence < elicitor.confidence_threshold
        and not bool(getattr(request, "declined_clarification", False))
        and not _clear_proceeds
    ):
        return StageResult.block(
            Stage.INTAKE,
            "G6_E_PIPELINE_CLARIFICATION_REQUIRED",
            "Answer the clarification questions (intent_answers) or explicitly "
            "proceed without them (declined_clarification=true).",
            task_contract=contract,
            feasibility_report=feasibility,
            intent_session=session,
            intent_questions=session.questions,
            components_used=_COMPONENTS_USED,
        )
    if feasibility.classification in {"risky", "too_large", "underspecified"} or _context_resolved:
        # D-G: a source-provided task whose named context was resolved proceeds, but honestly
        # as a qualified draft rather than a clean advance. A risky one proceeds only on the caller's word.
        accepted = {"risk_acceptance": {"accepted_by": "caller", "authenticated": False,
                                        "said": RISK_ACCEPTED_BY_CALLER}} if risky else {}
        return StageResult.downgrade(
            Stage.INTAKE,
            f"feasibility={feasibility.classification}" + ("; context attached" if _context_resolved else "")
            + (f"; {RISK_ACCEPTED_BY_CALLER}" if risky else ""),
            task_contract=contract,
            feasibility_report=feasibility,
            reduction_to_practice_questions=feasibility.reduction_to_practice_questions,
            components_used=_COMPONENTS_USED,
            **accepted,
        )
    return StageResult.advance(
        Stage.INTAKE,
        task_contract=contract,
        feasibility_report=feasibility,
        components_used=_COMPONENTS_USED,
    )


__all__ = ["RISK_ACCEPTED_BY_CALLER", "IntakeContract", "replay_question", "run_intake"]
