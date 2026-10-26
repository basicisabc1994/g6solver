# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json

from g6.intent_engine.schema import Assumption, IntentContract, IntentQuestion, IntentSession


_QUESTION_PLAN = (
    ("why", "Why is this task worth doing now?", "intent rationale is missing"),
    ("success_criteria", "What would success look like in observable terms?", "success criteria are missing"),
    ("failure_criteria", "What outcome would be unacceptable?", "failure criteria are missing"),
    ("non_goals", "What should stay out of scope?", "scope boundary is missing"),
    ("risk_tolerance", "What risk level is acceptable for this task?", "risk tolerance is missing"),
)

# Typed IntentContract literals the clarify-gate answers map onto. Answers
# arrive as FREE TEXT from real users (GUI clarify card, CLI flags) — they
# must coerce fail-open, never crash the pipeline (live regression
# 2026-06-12: "Keep it to one page; my team is just me" → pydantic
# literal_error killed the whole run).
_RISK_LEVELS = ("low", "medium", "high")
_EXPERTISE_LEVELS = ("beginner", "intermediate", "expert")
_NEGATIONS = frozenset({"not", "no", "never", "but", "except", "without", "avoid", "nothing"})
# Fill-ins that occupy a field without saying anything: an all-"x" spec is not a complete one (audit 005 IE-002).
_PLACEHOLDERS = frozenset({"tbd", "tba", "todo", "n/a", "na", "idk", "placeholder", "lorem ipsum", "asdf", "foo",
                           "bar", "whatever"})


def replacement_statement(request: str, goal: str) -> str:
    """The explicit assumption that records a goal answer replacing the request (audit 005 IE-007)."""
    return f"The goal answer replaced the request: {request.strip()!r} became {goal!r}."


def _says_something(text: object) -> bool:
    """Not empty, not a fill-in word, and not one character repeated ("x", "...", "???")."""
    lowered = " ".join(str(text or "").lower().split()).strip(".!?")
    return len({c for c in lowered if c.isalnum()}) > 1 and lowered not in _PLACEHOLDERS


class IntentElicitor:
    def __init__(self, *, max_questions: int = 5, confidence_threshold: float = 0.82) -> None:
        self.max_questions = max_questions
        self.confidence_threshold = confidence_threshold

    def elicit(
        self,
        initial_request: str,
        *,
        answers: dict[str, object] | None = None,
        prior: IntentContract | None = None,
        user_declined: bool = False,
    ) -> IntentSession:
        answers = answers or {}
        goal = str(answers.get("goal") or (prior.goal if prior else initial_request)).strip()
        success = self._list(answers.get("success_criteria") or (prior.success_criteria if prior else []))
        assumptions = list(prior.assumptions if prior else [])
        if answers.get("goal") and " ".join(goal.split()) != " ".join(initial_request.split()):
            assumptions.append(Assumption(  # audit 005 IE-007: the request is replaced in the open, never silently
                statement=replacement_statement(initial_request, goal),
                confidence=0.6,
                source="explicit",
            ))
        if user_declined and not success:
            assumptions.append(Assumption(
                statement="No success criteria were stated or inferred; the user declined clarification.",
                confidence=0.35,
                source="fallback",
            ))
        contract = IntentContract(
            goal=goal,
            success_checks=answers.get("success_checks", prior.success_checks if prior else {}),
            failure_checks=answers.get("failure_checks", prior.failure_checks if prior else {}),
            declared_risks=self._list(answers.get("declared_risks") or (prior.declared_risks if prior else [])),
            context=str(answers.get("context") or (prior.context if prior else "") or ""),
            constraints=self._list(answers.get("constraints") or (prior.constraints if prior else [])),
            success_criteria=success,
            why=str(answers.get("why") or (prior.why if prior else "") or ""),
            failure_criteria=self._list(answers.get("failure_criteria") or (prior.failure_criteria if prior else [])),
            non_goals=self._list(answers.get("non_goals") or (prior.non_goals if prior else [])),
            assumptions=assumptions,
            user_expertise_level=self._coerce_literal(  # type: ignore[arg-type]
                answers.get("user_expertise_level"),
                _EXPERTISE_LEVELS,
                str(prior.user_expertise_level) if prior else "unknown",
                "user expertise",
                assumptions,
            ),
            risk_tolerance=self._coerce_literal(  # type: ignore[arg-type]
                answers.get("risk_tolerance"),
                _RISK_LEVELS,
                str(prior.risk_tolerance) if prior else "unknown",
                "risk tolerance",
                assumptions,
            ),
            cost_constraint=str(answers.get("cost_constraint") or (prior.cost_constraint if prior else "") or ""),
            deadline=str(answers.get("deadline") or (prior.deadline if prior else "") or ""),
            confidence=0.0,
            confidence_method="field_completeness_heuristic",
            declined_clarification=user_declined,
        )
        contract = contract.model_copy(update={"confidence": self._confidence(contract, answers)})
        questions = self._questions_for(contract)
        reached = contract.confidence >= self.confidence_threshold
        ended_by = "declined" if user_declined else "confidence" if reached else "incomplete"  # IE-008
        if not user_declined and len(questions) >= self.max_questions and not reached:
            ended_by = "question_budget"
        # The id covers the whole input (the contract holds the answers, prior and decline), not only the request (IE-007)
        seed = json.dumps([initial_request, contract.model_dump(mode="json")], sort_keys=True)
        return IntentSession(
            session_id=hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12],
            initial_request=initial_request,
            questions=questions[: self.max_questions],
            contract=contract,
            ended_by=ended_by,  # type: ignore[arg-type]
        )

    @staticmethod
    def _given(contract: IntentContract) -> dict[str, bool]:
        """Which checklist fields say something: a placeholder ("x", "tbd") is not an answer (audit 005 IE-002)."""
        return {
            "why": _says_something(contract.why),
            "success_criteria": any(map(_says_something, contract.success_criteria)),
            "failure_criteria": any(map(_says_something, contract.failure_criteria)),
            "non_goals": any(map(_says_something, contract.non_goals)),
            "risk_tolerance": contract.risk_tolerance != "unknown",
        }

    def _questions_for(self, contract: IntentContract) -> list[IntentQuestion]:
        questions: list[IntentQuestion] = []
        given = self._given(contract)
        for field, question, reason in _QUESTION_PLAN:
            if not given[field]:
                questions.append(IntentQuestion(field=field, question=question, reason=reason))
        return questions

    @staticmethod
    def _coerce_literal(
        value: object,
        allowed: tuple[str, ...],
        fallback: str,
        field_label: str,
        assumptions: list[Assumption],
    ) -> str:
        """Fail-open coercion of a free-text answer onto a typed literal.

        Exact (case-insensitive) or whole-word keyword answers coerce; an
        unparseable answer keeps the prior/unknown value and is recorded as
        an EXPLICIT assumption so the user's words are never silently
        dropped. This must never raise — the clarify gate's unlock path
        feeds raw user text straight through here.
        """
        text = str(value or "").strip()
        if not text:
            return fallback
        lowered = text.lower()
        if lowered in allowed or lowered == "unknown":
            return lowered
        tokens = {token.strip(".,;:!?()") for token in lowered.split()}
        named = [level for level in allowed if level in tokens]
        negated = bool(tokens & _NEGATIONS) or any(token.endswith("n't") for token in tokens)
        # "anything but high" and "low to medium" name a level without choosing it: keep the words, not a guess
        if len(named) == 1 and not negated:
            return named[0]
        assumptions.append(Assumption(
            statement=f"User's {field_label} answer (free text): {text}",
            confidence=0.6,
            source="explicit",
        ))
        return fallback

    @staticmethod
    def _list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()]

    @staticmethod
    def _confidence(contract: IntentContract, answers: dict[str, object]) -> float:
        """Completeness heuristic only; not a semantic or verification score.

        Counted from the merged contract, so a field carried from ``prior`` counts; a placeholder does not. A free-text
        risk answer that did not coerce still counts (its words are kept as an explicit assumption), so the gate does
        not re-ask forever.
        """
        given = IntentElicitor._given(contract)
        given["risk_tolerance"] = given["risk_tolerance"] or _says_something(answers.get("risk_tolerance"))
        present = sum(given.values())
        penalty = 0.08 * len([item for item in contract.assumptions if item.source != "explicit"])
        return max(0.0, min(1.0, present / 5.0 - penalty))
