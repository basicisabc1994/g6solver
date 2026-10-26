# SPDX-License-Identifier: Apache-2.0
"""The stable codes of the jobs family's duty-of-care code and the one labelled result every op returns.

An answer (a scan that found no listed words, a score computed, the directory, the list of instruments) is
``qualified-draft`` with a card that says what it rests on. A crisis finding, a check that could not run, a crisis
response withheld because it broke a rule, a missing or out-of-range input and a refused instrument are
``blocked-escalated`` with their own code. Nothing is ``verified``:
:class:`CareResult` has no such state.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core.reliability_envelope import WarningCard

CRISIS = "G6_E_DUTY_OF_CARE_CRISIS"
CHECK_FAILED = "G6_E_DUTY_OF_CARE_CHECK_FAILED"
RESPONSE_REFUSED = "G6_E_DUTY_OF_CARE_RESPONSE_REFUSED"
MISSING = "G6_E_DUTY_OF_CARE_MISSING_INPUT"
OUT_OF_RANGE = "G6_E_DUTY_OF_CARE_OUT_OF_RANGE"
LICENCE_UNSETTLED = "G6_E_DUTY_OF_CARE_LICENCE_UNSETTLED"
UNKNOWN = "G6_E_DUTY_OF_CARE_UNKNOWN_INSTRUMENT"
INSTRUMENT_UNAVAILABLE = "G6_E_DUTY_OF_CARE_INSTRUMENT_UNAVAILABLE"
DIRECTORY_UNAVAILABLE = "G6_E_DUTY_OF_CARE_DIRECTORY_UNAVAILABLE"
REGION_NOT_COVERED = "G6_E_DUTY_OF_CARE_REGION_NOT_COVERED"
NO_LISTED_WORDS = "G6_E_DUTY_OF_CARE_NO_LISTED_WORDS"
SCORE_COMPUTED = "G6_E_DUTY_OF_CARE_SCORE_COMPUTED"
DIRECTORY = "G6_E_DUTY_OF_CARE_DIRECTORY"
DIRECTORY_STALE = "G6_E_DUTY_OF_CARE_DIRECTORY_STALE"
INSTRUMENT_LIST = "G6_E_DUTY_OF_CARE_INSTRUMENT_LIST"

_ACTIONS = {
    CRISIS: "Stop and hand over to a person now: show the response and the numbers to the person, and pass the "
            "conversation to someone who can help. Nothing else is produced for this subject in this run.",
    CHECK_FAILED: "Treat this as a possible crisis: show the numbers and pass the conversation to a person, then fix "
                  "the cause the reason names.",
    RESPONSE_REFUSED: "Show the numbers in the directory and pass the conversation to a person now. The words composed "
                      "for the person broke a rule the reason names and are not shown; report it so it can be fixed.",
    MISSING: "Supply what the reason names; nothing is assumed in its place.",
    OUT_OF_RANGE: "Give one answer per item, each one of the values the form prints.",
    LICENCE_UNSETTLED: "Use an instrument whose licence is settled, or settle this one's licence entry first.",
    UNKNOWN: "Use one of the instruments the instruments op lists.",
    INSTRUMENT_UNAVAILABLE: "The instrument's data could not be read: reinstall G6 or report it. No score is given in "
                            "its place.",
    DIRECTORY_UNAVAILABLE: "The directory could not be read: if anyone is in danger, call the local emergency number. "
                           "Reinstall G6 or report it.",
    REGION_NOT_COVERED: "If anyone is in danger, call the local emergency number. This directory holds no numbers for "
                        "that region.",
    NO_LISTED_WORDS: "Keep listening: a person can be in crisis without using any of these words. Pass anything that "
                     "worries you to a person.",
    SCORE_COMPUTED: "A qualified person interprets the score; G6 only adds it up.",
    DIRECTORY: "Check the retrieval date beside each number. If anyone is in danger, call the local emergency number.",
    DIRECTORY_STALE: "The numbers may have changed: check each one with its service before relying on it.",
    INSTRUMENT_LIST: "Score only the instruments whose licence is settled.",
}

State = Literal["qualified-draft", "blocked-escalated"]


class CareResult(BaseModel):
    """One op's labelled outcome. Op-specific fields sit beside the envelope's."""

    model_config = ConfigDict(extra="allow", frozen=True)

    operation: str
    code: str = ""
    reason: str = ""
    completion_state: State
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""


def answer(operation: str, card: str, reason: str, *, evidence: list[dict[str, Any]], request_id: str = "",
           run_id: str = "", **fields: Any) -> CareResult:
    """A ``qualified-draft`` answer whose card (``card``) says what it rests on."""
    warning = WarningCard(code=card, message=reason, severity="amber", suggested_action=_ACTIONS[card]).model_dump()
    return CareResult.model_validate({**fields, "operation": operation, "code": card, "reason": reason,
                                      "completion_state": "qualified-draft", "warning_card": warning,
                                      "evidence": evidence, "request_id": request_id, "run_id": run_id})


def refuse(operation: str, code: str, reason: str, *, evidence: list[dict[str, Any]] | None = None,
           request_id: str = "", run_id: str = "", **fields: Any) -> CareResult:
    """A ``blocked-escalated`` result with its stable code and the reason in words."""
    warning = WarningCard(code=code, message=reason, severity="blocked", suggested_action=_ACTIONS[code]).model_dump()
    return CareResult.model_validate({**fields, "operation": operation, "code": code, "reason": reason,
                                      "completion_state": "blocked-escalated", "warning_card": warning,
                                      "evidence": [*(evidence or []), {"code": code, "reason": reason}],
                                      "request_id": request_id, "run_id": run_id})


__all__ = ["CHECK_FAILED", "CRISIS", "DIRECTORY", "DIRECTORY_STALE", "DIRECTORY_UNAVAILABLE", "INSTRUMENT_LIST",
           "INSTRUMENT_UNAVAILABLE", "LICENCE_UNSETTLED", "MISSING", "NO_LISTED_WORDS", "OUT_OF_RANGE",
           "REGION_NOT_COVERED", "RESPONSE_REFUSED", "SCORE_COMPUTED", "UNKNOWN", "CareResult", "answer", "refuse"]
