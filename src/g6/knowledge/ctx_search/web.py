# SPDX-License-Identifier: Apache-2.0
"""The search stage: ``search(query, backend, n)`` asks one web search backend and returns a typed outcome and the
results, each the engine's own words about one address, never a page G6 has read.

Backends register by name (:data:`SEARCHERS`); two ship:

* ``searxng`` (the default): the SearXNG metasearch server the person runs on this machine (a declared outside
  service, ``http://127.0.0.1:8888`` or the local address ``G6_SEARXNG_URL`` names), asked through
  ``g6.core.outside.call_service`` for its JSON format. G6 never chooses a public instance. SearXNG reports the
  engines that did not answer (``unresponsive_engines``): no results with an engine that failed is
  ``engines_failed``, never ``empty``, and results with one that failed are ``partial``.
* ``brave``: the Brave Search API, through ``g6.core.http`` (the address checked, a body cap, a time limit, a pause
  between requests). It is paid: its key comes only from the person's key file (``BRAVE_SEARCH_API_KEY`` in
  ``~/.g6/.env``, their approval to spend it), and each request first asks ``g6.action_gating``, which holds it unless
  that key is there. It follows no redirect: the key travels in a header of its own, which would go with the request
  to whatever host a redirect names, so a redirect is a refusal.

An outcome says what happened: ``ok``, ``partial``, ``empty`` (the engine answered with nothing), or the failure
(``engines_failed``, ``blocked``, ``auth_failed``, ``rate_limited``, ``not_found``, ``server_error``, ``http_error``,
``refused``, ``unreachable``, ``timed_out``, ``too_large``, ``parse_failed``, ``rejected``, ``not_allowed``). A
failure is never answered by another engine in its place. Each result is a document: the result's title and snippet
as the engine wrote them, its ``locator`` the result's address, and its ``source`` the engine's reply (the bytes G6
read, so its ``published_at`` stays ``""``). The date the engine gives for a page is its word about that page, kept
apart in ``page_dates``. Only the engine's first page is asked for, so a search is never called complete: whether
more results exist is not known (``complete`` is ``None``, or ``False`` when an engine failed). Fetch the page itself
with ``knowledge_fetcher`` before relying on it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit

from g6.core import http, keys, outside
from g6.knowledge._types import Document, Source, now, sha256_hex

Outcome = Literal["ok", "partial", "empty", "engines_failed", "blocked", "auth_failed", "rate_limited", "not_found",
                  "server_error", "http_error", "refused", "unreachable", "timed_out", "too_large", "parse_failed",
                  "rejected", "not_allowed"]
CODES: dict[str, str] = {
    "ok": "G6_E_KNOWLEDGE_SEARCHED_WEB", "partial": "G6_E_KNOWLEDGE_SEARCH_PARTIAL",
    "empty": "G6_E_KNOWLEDGE_SEARCH_EMPTY", "engines_failed": "G6_E_KNOWLEDGE_SEARCH_ENGINES_FAILED",
    "blocked": "G6_E_KNOWLEDGE_SEARCH_BLOCKED", "auth_failed": "G6_E_KNOWLEDGE_SEARCH_AUTH_FAILED",
    "rate_limited": "G6_E_KNOWLEDGE_SEARCH_RATE_LIMITED", "not_found": "G6_E_KNOWLEDGE_SEARCH_NOT_FOUND",
    "server_error": "G6_E_KNOWLEDGE_SEARCH_SERVER_ERROR", "http_error": "G6_E_KNOWLEDGE_SEARCH_HTTP_ERROR",
    "refused": "G6_E_KNOWLEDGE_SEARCH_REFUSED", "unreachable": outside.OUTSIDE_PROGRAM_MISSING,
    "timed_out": "G6_E_KNOWLEDGE_SEARCH_TIMEOUT", "too_large": "G6_E_KNOWLEDGE_SEARCH_TOO_LARGE",
    "parse_failed": "G6_E_KNOWLEDGE_SEARCH_PARSE_FAILED", "rejected": "G6_E_KNOWLEDGE_SEARCH_REJECTED",
    "not_allowed": "G6_E_KNOWLEDGE_SEARCH_NOT_ALLOWED",
}
#: The outcomes in which the engine answered.
ANSWERED = frozenset({"ok", "partial", "empty"})
UNKNOWN_BACKEND = "G6_E_KNOWLEDGE_SEARCH_BACKEND"
SEARCH_INPUT = "G6_E_KNOWLEDGE_SEARCH_INPUT"
#: How long one search may take, and the most of a reply read.
TIMEOUT = 20.0
MAX_REPLY = 4 * 1024 * 1024

SEARXNG = outside.OutsideProgram(
    name="SearXNG",
    purpose="the metasearch server you run, which ctx_search asks",
    install="run SearXNG yourself on this machine (https://docs.searxng.org, for example its container image) with "
            "the json format turned on in its settings.yml (search: formats: [html, json]), at 127.0.0.1:8888; set "
            "G6_SEARXNG_URL for another local port",
    licence="AGPL-3.0 (SearXNG's own): a separate program G6 talks to over HTTP, never imports or bundles",
    url="http://127.0.0.1:8888",
    env="G6_SEARXNG_URL",
)
BRAVE_KEY = keys.Key("BRAVE_SEARCH_API_KEY", "the Brave Search API (ctx_search's brave backend); putting it in your "
                     "key file approves spending it", where="https://api-dashboard.search.brave.com", paid=True)
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True)
class SearchResult:
    """What one search found; see the module docstring for ``outcome``."""

    backend: str
    query: str
    outcome: Outcome
    results: list[Document] = field(default_factory=list)
    reason: str = ""
    total: int | None = None
    engines_failed: list[list[str]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    page_dates: list[str] = field(default_factory=list)  # the engine's date for each result's page, "" for none

    @property
    def answered(self) -> bool:
        return self.outcome in ANSWERED

    @property
    def all_engines_answered(self) -> bool:
        """Whether every engine asked answered."""
        return self.outcome in ("ok", "empty")

    @property
    def complete(self) -> bool | None:
        """Whether every result came back: ``False`` when an engine failed, otherwise ``None`` (not known: only the
        engine's first page is asked for)."""
        return None if self.all_engines_answered else False


class SearchRefused(ValueError):
    """A search that cannot be sent, with its stable code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason = code, reason


@runtime_checkable
class Searcher(Protocol):
    """A web search backend: its name and what it is, whether it reaches the network and spends money, whether the
    person has configured it (``""`` when so; it never reaches the network to find out), and :meth:`search`."""

    name: str
    summary: str
    network: bool
    paid: bool

    def configured(self) -> str: ...

    def search(self, query: str, n: int) -> SearchResult: ...


def _result(backend: str, query: str, reply_bytes: bytes, address: str, items: list[tuple[str, str, str, str]],
            **fields: Any) -> SearchResult:
    """The results of one reply: each ``(title, url, snippet, page date)`` as a document sourced to the reply, the
    page dates (the engine's word about each page) kept apart."""
    source = Source(uri=address, sha256=sha256_hex(reply_bytes), retrieved_at=now(), provenance="fetched")
    documents, dates = [], []
    for title, url, snippet, page_date in items:
        text = "\n\n".join(part for part in (title, snippet) if part)
        if not text.strip() or not url:
            continue
        documents.append(Document(text=text, source=source, media_type="application/json",
                                  converted_by=f"g6.knowledge {backend} result", title=title[:2000],
                                  locator=url[:4096]))
        dates.append(page_date[:40])
    outcome = fields.pop("outcome", None) or ("ok" if documents else "empty")
    return SearchResult(backend, query, outcome, documents, page_dates=dates, **fields)


def _plain(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _web_address(value: Any) -> str:
    text = str(value or "").strip()
    return text if urlsplit(text).scheme in ("http", "https") else ""


class SearxngSearcher:
    """The SearXNG the person runs on this machine, through ``g6.core.outside.call_service``."""

    name, network, paid = "searxng", True, False
    summary = "the SearXNG metasearch server you run on this machine (its json format), which names engines that failed"

    def __init__(self, *, caller: Callable[..., outside.ServiceReply] | None = None) -> None:
        self.caller = caller

    def configured(self) -> str:
        return ""  # a declared local service: whether it answers is found at call time, with how to run it

    def search(self, query: str, n: int) -> SearchResult:
        path = "/search?" + urlencode({"q": query, "format": "json", "pageno": 1})
        address = f"{outside.service_url(SEARXNG).rstrip('/')}{path}"
        try:
            reply = (self.caller or outside.call_service)(SEARXNG, "GET", path, headers={"Accept": "application/json"},
                                                          timeout=TIMEOUT, max_bytes=MAX_REPLY)
        except outside.ServiceError as exc:
            return SearchResult(self.name, query, _service_outcome(exc), reason=exc.message[:500],
                                evidence={"status": exc.status})
        try:
            data = json.loads(reply.body)
            raw = data["results"]
            if not isinstance(raw, list):
                raise TypeError("results is not a list")
        except (ValueError, KeyError, TypeError) as exc:
            return SearchResult(self.name, query, "parse_failed", reason=f"SearXNG's reply is not its JSON: {exc}")
        failed = [[_plain(e[0], 80), _plain(e[1], 200)] for e in data.get("unresponsive_engines") or []
                  if isinstance(e, (list, tuple)) and len(e) >= 2]
        items = [(_plain(r.get("title"), 500), _web_address(r.get("url")), _plain(r.get("content")),
                  _plain(r.get("publishedDate"), 40)) for r in raw[:n] if isinstance(r, dict)]
        total = data.get("number_of_results")
        said = {"total": int(total) if isinstance(total, (int, float)) and total > 0 else None,
                "engines_failed": failed, "evidence": {"engines": sorted({e for r in raw if isinstance(r, dict)
                                                                          for e in (r.get("engines") or [])})}}
        result = _result(self.name, query, reply.body, address, items, **said)
        if failed and not result.results:
            return SearchResult(self.name, query, "engines_failed", reason="no results, and these engines did not "
                                "answer: " + "; ".join(f"{e} ({why})" for e, why in failed), **said)
        if failed:
            return SearchResult(self.name, query, "partial", result.results, reason="these engines did not answer: "
                                + "; ".join(f"{e} ({why})" for e, why in failed), page_dates=result.page_dates,
                                **said)
        return result


def _service_outcome(exc: outside.ServiceError) -> Outcome:
    if exc.code == outside.OUTSIDE_PROGRAM_MISSING:
        return "unreachable"
    if exc.code == outside.SERVICE_TIMEOUT:
        return "timed_out"
    if exc.code == outside.SERVICE_TOO_LARGE:
        return "too_large"
    if exc.code in (outside.SERVICE_NOT_LOCAL,):
        return "refused"
    return _status_outcome(exc.status)


def _status_outcome(status: int | None) -> Outcome:
    if status is None:
        return "server_error"
    if 300 <= status < 400:
        return "refused"  # a redirect, which G6 does not follow
    if status in (401, 407):
        return "auth_failed"
    if status in (403, 451):
        return "blocked"
    if status in (404, 410):
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status in (400, 422):
        return "rejected"
    return "server_error" if status >= 500 else "http_error"


Gate = Callable[[dict[str, Any], str], dict[str, Any]]


def ask_gate(action: dict[str, Any], key_approval: str) -> dict[str, Any]:
    """``g6.action_gating``'s decision on ``action``, as evidence; a gate that cannot decide holds the request."""
    from g6.action_gating import evaluate_action

    found: dict[str, Any] = {"kind": "action_gate", "gate": "g6.action_gating", "action_type": action["action_type"]}
    try:
        evaluation = evaluate_action(action, key_approval=key_approval)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot decide never lets money be spent
        return {**found, "decision": "block", "reason_codes": [],
                "required_user_message": f"the action gate could not decide: {type(exc).__name__}: {exc}"[:300]}
    decided = evaluation.gate_decision
    return {**found, "decision": decided.decision, "reason_codes": list(decided.reason_codes),
            "required_user_message": decided.required_user_message, "audit_id": decided.audit_id}


def lets_through(gate: dict[str, Any]) -> bool:
    """The gate's ``allow``, or its ``allow_with_notice`` because the person's key file holds the key
    (``G6_E_KEY_FILE_APPROVED``); a notice for any other reason sends nothing."""
    from g6.action_gating.decision_engine import E_KEY_FILE_APPROVED

    return gate["decision"] == "allow" or (gate["decision"] == "allow_with_notice"
                                           and E_KEY_FILE_APPROVED in gate.get("reason_codes", []))


def brave_request(query: str, n: int) -> dict[str, Any]:
    """The action the gate decides: one paid request to the Brave Search API, with what G6 checks afterwards."""
    plan = {"expected_result": "a JSON reply from api.search.brave.com whose web results each have a title and an "
                               "http or https address",
            "observation_window": "until the reply arrives, within the search's time limit",
            "leading_indicators": ["the reply's status", "the reply's size, under the cap"],
            "lagging_indicators": ["the parse of web.results"],
            "monitor": "g6.knowledge.ctx_search (the reply's status and shape)",
            "verification_time": "when the reply arrives, before any result is returned",
            "stop_threshold": "any status other than 200, or a reply that does not parse: nothing is returned as a "
                              "result",
            "rollback_procedure": "a query spent cannot be returned; a failed request is reported, never retried "
                                  "unasked",
            "model_update_rule": "none: no model is involved"}
    return {"action_type": "call_api", "target_backend": "brave", "target_resource": "Brave Search API (paid web "
            "search)", "description": "send one web search to the Brave Search API, spending the person's quota",
            "action_params": {"query": query, "count": n}, "requested_by": "ctx_search.search", "actor_type": "agent",
            "external_side_effect": True, "irreversible_or_costly_to_reverse": True, "blast_radius": "external_party",
            "safety_domains": ["financial"], "verification_contract": plan}


def brave_results(data: Any) -> list[dict[str, Any]]:
    """The web results of a Brave reply, each checked: the reply is a search (``type`` ``search``) with a ``web`` part
    holding a ``results`` list, and each result has a title and an http or https ``url``, with ``description`` and
    ``page_age`` text when given. ``[]`` only for a results list that is there and empty; :class:`ValueError` for
    anything else, so a reply that is not Brave's is never read as no results."""
    if not isinstance(data, dict) or data.get("type") != "search":
        raise ValueError("it is not a search reply (no type 'search')")
    web = data.get("web")
    if not isinstance(web, dict) or not isinstance(web.get("results"), list):
        raise ValueError("it has no web.results list")
    for at, result in enumerate(web["results"]):
        if not isinstance(result, dict):
            raise ValueError(f"web result {at} is not an object")
        if not isinstance(result.get("title"), str) or not result["title"].strip():
            raise ValueError(f"web result {at} has no title")
        if not isinstance(result.get("url"), str) or not _web_address(result["url"]):
            raise ValueError(f"web result {at} has no http or https url")
        for name in ("description", "page_age"):
            if result.get(name) is not None and not isinstance(result[name], str):
                raise ValueError(f"web result {at}'s {name} is not text")
    return web["results"]


class BraveSearcher:
    """The Brave Search API through ``g6.core.http``, its key from the person's key file, each request let through
    by ``g6.action_gating``. One client is kept, so its pause between requests holds across searches, and it follows
    no redirect. ``client`` and ``gate`` replace that client and the gate (tests use doubles); a client that follows
    redirects is refused, since the key would go with it."""

    name, network, paid = "brave", True, True
    summary = "the Brave Search API (paid): its key only from your key file, each request through the action gate"

    def __init__(self, *, client: http.Client | None = None, gate: Gate | None = None) -> None:
        if client is not None and client.max_redirects:
            raise ValueError("a client for Brave must follow no redirect (max_redirects=0): the key would go with it")
        self.client = client or http.Client(timeout=TIMEOUT, max_bytes=MAX_REPLY, max_redirects=0, min_interval=1.0)
        self.gate = gate

    def configured(self) -> str:
        return "" if keys.present(BRAVE_KEY.name) else keys.KeyMissing(BRAVE_KEY).message

    def search(self, query: str, n: int) -> SearchResult:
        decided = (self.gate or ask_gate)(brave_request(query, n), BRAVE_KEY.name)
        if not lets_through(decided):
            codes = ", ".join(decided.get("reason_codes") or []) or "no reason code"
            why = (keys.KeyMissing(BRAVE_KEY).message if not keys.present(BRAVE_KEY.name)
                   else str(decided.get("required_user_message", "")))
            return SearchResult(self.name, query, "not_allowed", reason=f"nothing was sent to Brave, which spends the "
                                f"person's money: the action gate decided {decided['decision']} ({codes}). "
                                f"{why}"[:1000], evidence={"gate": decided})
        try:
            key = keys.require(BRAVE_KEY)
        except keys.KeyMissing as exc:
            return SearchResult(self.name, query, "not_allowed", reason=exc.message, evidence={"gate": decided})
        address = f"{BRAVE_URL}?" + urlencode({"q": query, "count": n})
        try:
            reply = self.client.get(address, headers={"Accept": "application/json", "X-Subscription-Token": key})
        except http.HTTPError as exc:
            redirected = exc.code == http.STATUS and exc.status is None  # a redirect the client did not follow
            outcome: Outcome = ("refused" if exc.code == http.REFUSED or redirected else "too_large"
                                if exc.code == http.TOO_LARGE else "unreachable" if exc.code == http.FAILED
                                else _status_outcome(exc.status))
            why = ("Brave answered with a redirect, which G6 does not follow: the key would go with it. "
                   if redirected or (exc.status is not None and 300 <= exc.status < 400) else "")
            return SearchResult(self.name, query, outcome, reason=(why + str(exc)).replace(key, "***")[:500],
                                evidence={"gate": decided, "status": exc.status})
        try:
            raw = brave_results(json.loads(reply.body))
        except (ValueError, TypeError) as exc:
            return SearchResult(self.name, query, "parse_failed", reason=f"Brave's reply is not its JSON: {exc}",
                                evidence={"gate": decided})
        items = [(_plain(re.sub(r"<[^>]+>", "", r["title"]), 500), _web_address(r["url"]),
                  _plain(re.sub(r"<[^>]+>", "", r.get("description") or "")), _plain(r.get("page_age"), 40))
                 for r in raw[:n]]
        return _result(self.name, query, reply.body, address, items,
                       evidence={"gate": decided, "spent": "one Brave Search request; its cost is on your Brave "
                                                           "account, not known to G6"})


class Searchers:
    """The search backends registered by name; a name is taken once, unless :meth:`register` is told to
    ``replace`` it."""

    def __init__(self) -> None:
        self._searchers: dict[str, Searcher] = {}

    def register(self, searcher: Searcher, *, replace: bool = False) -> None:
        if not isinstance(searcher, Searcher):
            raise TypeError(f"{searcher!r} is not a searcher (name, summary, network, paid, configured, search)")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(searcher.name)):
            raise ValueError(f"{searcher.name!r} is not a backend name")
        if searcher.name in self._searchers and not replace:
            raise ValueError(f"a search backend named {searcher.name!r} is already registered")
        self._searchers[searcher.name] = searcher

    def unregister(self, name: str) -> None:
        self._searchers.pop(name, None)

    def get(self, name: str) -> Searcher | None:
        return self._searchers.get(name)

    def names(self) -> list[str]:
        return sorted(self._searchers)

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "summary": s.summary, "network": s.network, "paid": s.paid,
                 "configured": s.configured() or "configured"} for s in (self._searchers[n] for n in self.names())]


SEARCHERS = Searchers()
SEARCHERS.register(SearxngSearcher())
SEARCHERS.register(BraveSearcher())
DEFAULT = "searxng"


def search(query: str, backend: str = DEFAULT, n: int = 10, *, searchers: Searchers = SEARCHERS) -> SearchResult:
    """``query`` asked of one backend; see the module docstring. :class:`SearchRefused` for a request that cannot be
    sent; every network failure is a result, never an exception, and never answered by another backend."""
    if not query.strip():
        raise SearchRefused(SEARCH_INPUT, "there is no query")
    if not 1 <= n <= 20:
        raise SearchRefused(SEARCH_INPUT, "n is 1 to 20 results")
    found = searchers.get(backend)
    if found is None:
        raise SearchRefused(UNKNOWN_BACKEND, f"no search backend named {backend!r} (registered: "
                                             f"{', '.join(searchers.names()) or 'none'})")
    try:
        result = found.search(query.strip(), n)
    except Exception as exc:  # noqa: BLE001 - a backend's bug is a refusal, never an empty result
        raise SearchRefused(UNKNOWN_BACKEND, f"the {backend} backend failed: {type(exc).__name__}: {exc}"[:500]) \
            from None
    if not isinstance(result, SearchResult) or result.backend != backend:
        raise SearchRefused(UNKNOWN_BACKEND, f"the {backend} backend did not answer for itself")
    return result


__all__ = ["ANSWERED", "BRAVE_KEY", "BRAVE_URL", "CODES", "DEFAULT", "SEARCHERS", "SEARCH_INPUT", "SEARXNG",
           "UNKNOWN_BACKEND", "BraveSearcher", "Outcome", "SearchRefused", "SearchResult", "Searcher", "Searchers",
           "SearxngSearcher", "ask_gate", "brave_request", "brave_results", "lets_through", "search"]
