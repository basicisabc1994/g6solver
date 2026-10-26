# SPDX-License-Identifier: Apache-2.0
"""CtxSearchBlock: the registry and MCP door (``g6_invoke``) to web search.

Two ops. ``search`` asks one backend (the SearXNG you run, by default; Brave, paid, when its key is in your key file
and the action gate lets the request through) and returns a typed outcome with the results; it reaches the network,
so it is sensitive. ``backends`` lists the registered backends and whether each is configured, and reaches nothing.

An answered search is ``qualified-draft`` (results, results with engines that failed said to be partial, or an
engine that answered with nothing); every failure is ``blocked-escalated`` with its own code, an engine that failed
never stands in as "no results", and no other engine answers in its place.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.knowledge import _reply
from g6.knowledge.ctx_search import web as stage
from g6.knowledge.ctx_search.web import CODES, SEARCHERS, SearchRefused

COMPONENT = "ctx_search"
LISTED = "G6_E_KNOWLEDGE_SEARCH_BACKENDS"
#: search reaches the network (and, with brave, spends the person's money).
SENSITIVE_OPS: tuple[str, ...] = ("search",)
_OPS: dict[str, str] = {
    "search": "search(query, backend, n): ask one web search backend (searxng, the server you run, by default; "
              "brave, paid, through the action gate) and get a typed outcome: ok, partial (engines that failed are "
              "named), empty, or the failure it was; results are the engine's words about each address; sensitive",
    "backends": "backends(): the registered search backends, whether each is paid and whether it is configured; "
                "nothing is asked",
}
_ACTIONS: dict[str, str] = {
    "ok": "Each result is the engine's title and snippet, not the page: fetch the page (knowledge_fetcher) before "
          "relying on it, and check claims against it with g6.grounding.",
    "partial": "Some engines did not answer, so these are not all the results: say so, or search again later.",
    "empty": "The engine answered with nothing: not evidence that nothing exists. Try other words or another "
             "backend, and say what was searched.",
    "engines_failed": "Every engine that could have answered failed: this is not an empty result. Try again later, or "
                      "check your SearXNG's engines.",
    "blocked": "The server refused the search: for SearXNG, turn on its json format (settings.yml, search: formats: "
               "[html, json]); this is not an empty result.",
    "auth_failed": "The key was refused: check it in your key file. This is not an empty result.",
    "rate_limited": "The engine asked G6 to slow down, or the quota is spent: wait. This is not an empty result.",
    "not_found": "Nothing answers at that path: is the service at that address SearXNG?",
    "server_error": "The engine failed: try again later. This is not an empty result.",
    "http_error": "The engine answered with an error: read the reason.",
    "refused": "G6 follows no redirect and reaches only public addresses (and your SearXNG on this machine).",
    "unreachable": "Nothing answered: start your SearXNG (or set G6_SEARXNG_URL to where it listens). G6 never "
                   "installs or starts it.",
    "timed_out": "The engine did not finish in time: try again. This is not an empty result.",
    "too_large": "The reply was over the cap: ask for fewer results.",
    "parse_failed": "The reply was not the engine's format, so nothing in it was used.",
    "rejected": "The engine refused the query as written: read the reason.",
    "not_allowed": f"Nothing was sent: put {stage.BRAVE_KEY.name} in your key file to approve spending it, or use "
                   "searxng.",
    LISTED: "Choose a configured backend; a paid one spends the person's money once its key is in their key file.",
    stage.UNKNOWN_BACKEND: "Use one of the registered backends (the backends op lists them).",
    stage.SEARCH_INPUT: "Give a query, and n from 1 to 20.",
}


class CtxSearchInput(BaseModel):
    """One op's parameters; fields no op has are refused (there is no key, address or approval to pass)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["search", "backends", "ops", "help"] = "help"
    query: str = Field("", max_length=500)
    backend: str = Field(stage.DEFAULT, min_length=1, max_length=32)
    n: int = Field(10, ge=1, le=20)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(data: CtxSearchInput) -> dict[str, str]:
    return {"request_id": data.request_id, "run_id": data.run_id}


def _search(data: CtxSearchInput) -> dict[str, Any]:
    try:
        found = stage.search(data.query, data.backend, data.n)
    except SearchRefused as exc:
        return _reply.refuse(COMPONENT, "search", exc.code, exc.reason, action=_ACTIONS[exc.code], reason=exc.reason,
                             backend=data.backend, **_ids(data))
    dates = found.page_dates + [""] * (len(found.results) - len(found.page_dates))
    results = [{"rank": n, "title": d.title, "url": d.locator, "page_date": date, "document": d.model_dump()}
               for n, (d, date) in enumerate(zip(found.results, dates), start=1)]
    fields = {"outcome": found.outcome, "backend": found.backend, "query": found.query, "results": results,
              "total": found.total, "complete": found.complete, "all_engines_answered": found.all_engines_answered,
              "engines_failed": found.engines_failed, "reason": found.reason}
    evidence = [{"kind": "web_search", "backend": found.backend, "outcome": found.outcome,
                 "results": len(found.results), "engines_failed": found.engines_failed,
                 **{k: v for k, v in found.evidence.items() if k != "gate"}},
                *([found.evidence["gate"]] if "gate" in found.evidence else [])]
    code = CODES[found.outcome]
    if not found.answered:
        return _reply.refuse(COMPONENT, "search", code, found.reason or found.outcome,
                             action=_ACTIONS[found.outcome], evidence=evidence, **fields, **_ids(data))
    if found.outcome == "empty":
        message = (f"{found.backend} answered and found nothing for the query: that is not evidence that nothing "
                   "exists elsewhere, or under other words.")
    else:
        message = (f"{len(found.results)} results from {found.backend}: each is the engine's title and snippet about "
                   "an address, not the page." + (f" Partial: {found.reason}." if found.outcome == "partial" else ""))
    return _reply.answer(COMPONENT, "search", code, message, action=_ACTIONS[found.outcome], evidence=evidence,
                         texts=[d.text for d in found.results], **fields, **_ids(data))


def _backends(data: CtxSearchInput) -> dict[str, Any]:
    listed = SEARCHERS.describe()
    return _reply.answer(COMPONENT, "backends", LISTED, f"{len(listed)} search backends are registered; none was "
                         "asked anything.", action=_ACTIONS[LISTED], backends=listed, default=stage.DEFAULT,
                         **_ids(data))


@block_contract(
    purpose="ask one web search backend and say exactly what happened, so an engine that failed or was refused is "
            "never read as no results, and no other engine answers in its place",
    mitigates_failure_modes=("failure_read_as_empty",),
    verification_method="the reply's status, the transport's error and the parse mapped to their own outcome; "
                        "SearXNG's own list of engines that did not answer read back, so no results with a failed "
                        "engine is engines_failed and results with one are partial",
)
@component_maturity(
    "beta",
    reason="SearXNG through g6.core.outside and Brave through g6.core.http and the action gate, each tested on a "
           "transport double, not live engines; a result is an engine's snippet and a qualified draft at best",
)
class CtxSearchBlock(AIBlock[CtxSearchInput, dict, dict]):
    """Search the web through the backend you run or pay for. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: CtxSearchInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_search(data) if data.op == "search" else _backends(data))


__all__ = ["COMPONENT", "LISTED", "SENSITIVE_OPS", "CtxSearchBlock", "CtxSearchInput"]
