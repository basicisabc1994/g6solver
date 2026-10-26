# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.llm_router and the one labelled result every op returns.

An answer is ``qualified-draft``, and its warning card says what it rests on: :data:`COMMITTED_TABLE` for what the
committed table says (hand-assigned scores, dated prices), :data:`COST_ESTIMATE` for a cost estimate,
:data:`LIVE_ANSWER` for what a live server said, and :data:`RECORDED_OUTCOMES` for outcomes callers reported; an answer
whose prices are older than the stated age carries :data:`STALE_PRICE` instead. A refusal is ``blocked-escalated``
with its own code: the router's or, where g6.core refused (an address it will not reach, an outside program that is
not there), g6.core's. Nothing is ``verified``: :class:`RouterResult` has no such state, because a table of
estimates, a server's word or a caller's report is not evidence a person has inspected.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core import http, outside
from g6.core.reliability_envelope import WarningCard

COMMITTED_TABLE = "G6_E_ROUTER_COMMITTED_TABLE"
COST_ESTIMATE = "G6_E_ROUTER_COST_ESTIMATE"
LIVE_ANSWER = "G6_E_ROUTER_LIVE_ANSWER"
STALE_PRICE = "G6_E_ROUTER_STALE_PRICE"
RECORDED_OUTCOMES = "G6_E_ROUTER_RECORDED_OUTCOMES"
STORE_FAILED = "G6_E_ROUTER_STORE_FAILED"
INPUT_INVALID = "G6_E_ROUTER_INPUT_INVALID"
NO_MODEL = "G6_E_ROUTER_NO_MODEL"
UNKNOWN_MODEL = "G6_E_ROUTER_UNKNOWN_MODEL"
PRICE_UNKNOWN = "G6_E_ROUTER_PRICE_UNKNOWN"
NO_SUPERVISOR = "G6_E_ROUTER_NO_SUPERVISOR"
PROVIDER_UNREACHABLE = "G6_E_ROUTER_PROVIDER_UNREACHABLE"
PROVIDER_ERROR = "G6_E_ROUTER_PROVIDER_ERROR"

_ACTIONS = {
    INPUT_INVALID: "Correct the input as the reason says and ask again.",
    NO_MODEL: "Allow another provider, lower min_score, or name the local models you have installed.",
    UNKNOWN_MODEL: "Use a model_id from models_for_task or route; the table only knows the models it lists.",
    PRICE_UNKNOWN: "The price is variable or unknown, so no estimate is given. Check the provider's current price.",
    NO_SUPERVISOR: "Allow a provider with a dearer or more capable model, or check the worker's output yourself.",
    PROVIDER_UNREACHABLE: "Check that the server is running and the address is right (for Ollama, OLLAMA_BASE_URL).",
    PROVIDER_ERROR: "The server answered with an error; read the reason, correct the request and try again.",
    STORE_FAILED: "The outcome store under the G6 home could not be used; check that G6_HOME is a folder you can "
                  "write, and read the reason.",
    outside.OUTSIDE_PROGRAM_MISSING: "Install and start it as the reason says; G6 never installs it.",
    outside.SERVICE_NOT_LOCAL: "Ask the person who runs G6 to point OLLAMA_BASE_URL at this machine (127.0.0.1, ::1 "
                               "or localhost); these ops never reach another one.",
    http.REFUSED: "G6 does not fetch from that address. If the provider moved its catalogue, update the router.",
}
#: Every refusal code an op of the router can give.
REFUSALS: tuple[str, ...] = tuple(_ACTIONS)

State = Literal["qualified-draft", "blocked-escalated"]


class RouterResult(BaseModel):
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


def answer(operation: str, card: str, message: str, action: str, *, evidence: dict[str, Any], request_id: str = "",
           run_id: str = "", **fields: Any) -> RouterResult:
    """A ``qualified-draft`` answer whose card (``card``) says what it rests on."""
    warning = WarningCard(code=card, message=message, severity="amber", suggested_action=action).model_dump()
    return RouterResult.model_validate({**fields, "operation": operation, "completion_state": "qualified-draft",
                                        "warning_card": warning, "evidence": [evidence], "request_id": request_id,
                                        "run_id": run_id})


def refuse(operation: str, code: str, reason: str, *, evidence: dict[str, Any] | None = None, request_id: str = "",
           run_id: str = "", **fields: Any) -> RouterResult:
    """A ``blocked-escalated`` refusal with its stable code and the reason in words."""
    warning = WarningCard(code=code, message=reason, severity="blocked", suggested_action=_ACTIONS[code]).model_dump()
    return RouterResult.model_validate({**fields, "operation": operation, "code": code, "reason": reason,
                                        "completion_state": "blocked-escalated", "warning_card": warning,
                                        "evidence": [{**(evidence or {}), "code": code, "reason": reason}],
                                        "request_id": request_id, "run_id": run_id})


__all__ = ["COMMITTED_TABLE", "COST_ESTIMATE", "INPUT_INVALID", "LIVE_ANSWER", "NO_MODEL", "NO_SUPERVISOR",
           "PRICE_UNKNOWN", "PROVIDER_ERROR", "PROVIDER_UNREACHABLE", "RECORDED_OUTCOMES", "REFUSALS", "STALE_PRICE",
           "STORE_FAILED", "UNKNOWN_MODEL", "RouterResult", "answer", "refuse"]
