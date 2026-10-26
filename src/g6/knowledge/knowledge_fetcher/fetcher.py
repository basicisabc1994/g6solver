# SPDX-License-Identifier: Apache-2.0
"""The fetch stage: one request type, one result type with a typed outcome, sources registered by name, and every
request through ``g6.core.http``.

:func:`fetch` sends a :class:`FetchRequest` to the source adapter it names (:data:`SOURCES`), through a
``g6.core.http.Client`` made for that adapter: every hop checked before it is requested (no private, loopback,
link-local or reserved address, a redirect checked like the first request, the connection pinned to the address
checked), a timeout, a cap on the body, a pause between requests to one host (three seconds for arXiv, which asks for
one request every three seconds), and the site's ``robots.txt`` for pages. A client is kept per adapter, so the pause
holds across calls in one process.

A :class:`FetchResult`'s ``outcome`` names what happened. ``ok`` and ``empty`` mean the source answered: ``empty``
is a source that answered with nothing, never a failure. Every failure is its own outcome: ``refused`` (an address
that is not public), ``robots_disallowed``, ``blocked`` (401, 403, 407, 451), ``not_found`` (404, 410),
``rate_limited`` (429), ``server_error`` (5xx), ``http_error`` (another error status, or too many redirects),
``too_large``, ``unreachable`` (a name that does not resolve, a connection, TLS or a timeout), ``parse_failed`` (a
reply that is not the source's format) and ``rejected`` (the source said the request is malformed). ``total`` is how
many matches a search source reported, so a list it cut short says so.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from collections.abc import Callable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from g6.core import http
from g6.knowledge import _html, _store
from g6.knowledge._types import Document, Source, now, sha256_hex

Outcome = Literal["ok", "empty", "refused", "robots_disallowed", "blocked", "not_found", "rate_limited",
                  "server_error", "http_error", "too_large", "unreachable", "parse_failed", "rejected"]
CODES: dict[str, str] = {
    "ok": "G6_E_KNOWLEDGE_FETCHED", "empty": "G6_E_KNOWLEDGE_FETCH_EMPTY",
    "refused": "G6_E_KNOWLEDGE_FETCH_REFUSED", "robots_disallowed": "G6_E_KNOWLEDGE_FETCH_ROBOTS",
    "blocked": "G6_E_KNOWLEDGE_FETCH_BLOCKED", "not_found": "G6_E_KNOWLEDGE_FETCH_NOT_FOUND",
    "rate_limited": "G6_E_KNOWLEDGE_FETCH_RATE_LIMITED", "server_error": "G6_E_KNOWLEDGE_FETCH_SERVER_ERROR",
    "http_error": "G6_E_KNOWLEDGE_FETCH_HTTP_ERROR", "too_large": "G6_E_KNOWLEDGE_FETCH_TOO_LARGE",
    "unreachable": "G6_E_KNOWLEDGE_FETCH_UNREACHABLE", "parse_failed": "G6_E_KNOWLEDGE_FETCH_PARSE_FAILED",
    "rejected": "G6_E_KNOWLEDGE_FETCH_REJECTED",
}
#: The outcomes in which the source answered.
ANSWERED = frozenset({"ok", "empty"})
UNKNOWN_SOURCE = "G6_E_KNOWLEDGE_UNKNOWN_SOURCE"
FETCH_INPUT = "G6_E_KNOWLEDGE_FETCH_INPUT"
ADAPTER_FAILED = "G6_E_KNOWLEDGE_FETCH_ADAPTER"
#: How G6 names itself to the sites it fetches from: honestly, never as a browser.
USER_AGENT = "g6-knowledge-fetcher"
#: Media types a page is read as text in; any other body is kept for knowledge_converter.
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/json"})
_BLOCKED = frozenset({401, 403, 407, 451})
_GONE = frozenset({404, 410})


class FetchRequest(BaseModel):
    """What to fetch: the source's name and its parameters (a page's ``url``; a search's ``query``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field("http", min_length=1, max_length=40)
    url: str = Field("", max_length=4096)
    query: str = Field("", max_length=2000)
    categories: tuple[str, ...] = Field((), max_length=20)
    set_spec: str = Field("", max_length=200)
    since: str = Field("", max_length=40)
    max_results: int = Field(10, ge=1, le=100)
    mailto: str = Field("", max_length=320)  # OpenAlex's contact address: sent only when the caller gives one


class FetchResult(BaseModel):
    """What a fetch found; see the module docstring for ``outcome``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    outcome: Outcome
    reason: str = ""
    url: str = ""
    status: int | None = None
    media_type: str = ""
    documents: list[Document] = Field(default_factory=list)
    total: int | None = None
    kept: str = ""
    bytes_read: int = 0

    @property
    def complete(self) -> bool | None:
        """Whether every match the source reported came back; ``None`` when it reported no count."""
        return None if self.total is None else len(self.documents) >= self.total


class FetchRefused(ValueError):
    """A request that cannot be sent, or an adapter that broke, with its stable code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason = code, reason


@runtime_checkable
class SourceAdapter(Protocol):
    """A source: its name, what it is, what it needs (``url`` or ``query``), the pause it asks for between requests,
    whether ``robots.txt`` applies, and :meth:`fetch`, which reaches the network only through the client it is
    given."""

    name: str
    summary: str
    needs: str
    min_interval: float
    obey_robots: bool

    def fetch(self, request: FetchRequest, client: http.Client) -> FetchResult: ...


_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")


class SourceRegistry:
    """The sources registered by name; a name is taken once, unless :meth:`register` is told to ``replace`` it."""

    def __init__(self) -> None:
        self._sources: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter, *, replace: bool = False) -> None:
        if not isinstance(adapter, SourceAdapter):
            raise TypeError(f"{adapter!r} is not a source (name, summary, needs, min_interval, obey_robots, fetch)")
        if not _NAME.fullmatch(str(adapter.name)) or adapter.needs not in ("url", "query"):
            raise ValueError(f"{adapter.name!r} needs a lower-case name and needs 'url' or 'query'")
        if adapter.name in self._sources and not replace:
            raise ValueError(f"a source named {adapter.name!r} is already registered")
        self._sources[adapter.name] = adapter
        _CLIENTS.pop(adapter.name, None)

    def unregister(self, name: str) -> None:
        self._sources.pop(name, None)
        _CLIENTS.pop(name, None)

    def get(self, name: str) -> SourceAdapter | None:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return sorted(self._sources)

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": a.name, "summary": a.summary, "needs": a.needs, "pause_seconds": a.min_interval,
                 "robots_txt": a.obey_robots} for a in (self._sources[n] for n in self.names())]


#: The sources the family knows; the adapters module registers the five it ships.
SOURCES = SourceRegistry()
_CLIENTS: dict[str, http.Client] = {}
Clients = Callable[[SourceAdapter], http.Client]


def client_for(adapter: SourceAdapter) -> http.Client:
    """The one checked client kept for ``adapter``, with its pause and its ``robots.txt`` rule."""
    if adapter.name not in _CLIENTS:
        _CLIENTS[adapter.name] = http.Client(min_interval=adapter.min_interval, obey_robots=adapter.obey_robots,
                                             user_agent=USER_AGENT)
    return _CLIENTS[adapter.name]


def outcome_of(exc: http.HTTPError) -> Outcome:
    """The outcome a ``g6.core.http`` error stands for."""
    if exc.code == http.REFUSED:
        said = str(exc)
        return "unreachable" if ("could not be resolved" in said or "resolved to no address" in said) else "refused"
    if exc.code == http.ROBOTS:
        return "robots_disallowed"
    if exc.code == http.TOO_LARGE:
        return "too_large"
    if exc.code == http.FAILED:
        return "unreachable"
    status = exc.status or 0
    if status in _BLOCKED:
        return "blocked"
    if status in _GONE:
        return "not_found"
    if status == 429:
        return "rate_limited"
    return "server_error" if status >= 500 else "http_error"


def fetch(request: FetchRequest, *, clients: Clients | None = None) -> FetchResult:
    """``request`` sent to its source; see the module docstring. Raises :class:`FetchRefused` for a request that
    cannot be sent or an adapter that broke; every network failure is a result, never an exception."""
    adapter = SOURCES.get(request.source)
    if adapter is None:
        raise FetchRefused(UNKNOWN_SOURCE, f"no source named {request.source!r} (registered: "
                                           f"{', '.join(SOURCES.names()) or 'none'})")
    if adapter.needs == "url" and not request.url.strip():
        raise FetchRefused(FETCH_INPUT, f"the {adapter.name} source needs a url")
    if adapter.needs == "query" and not request.query.strip():
        raise FetchRefused(FETCH_INPUT, f"the {adapter.name} source needs a query")
    client = (clients or client_for)(adapter)
    try:
        result = adapter.fetch(request, client)
    except http.HTTPError as exc:
        return FetchResult(source=adapter.name, outcome=outcome_of(exc), reason=str(exc)[:500],
                           url=exc.url or request.url, status=exc.status)
    except FetchRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - an adapter's bug is a refusal, never an empty result
        raise FetchRefused(ADAPTER_FAILED, f"the {adapter.name} source failed: {type(exc).__name__}: {exc}"[:500]) \
            from None
    if not isinstance(result, FetchResult):
        raise FetchRefused(ADAPTER_FAILED, f"the {adapter.name} source returned {type(result).__name__}, not a "
                                           "fetch result")
    return result


# -- helpers the adapters share ---------------------------------------------------------------------------------------

def media_type(headers: dict[str, str]) -> tuple[str, str]:
    """``(media type, charset)`` from a reply's ``Content-Type``; ``("application/octet-stream", "")`` without one."""
    raw = headers.get("content-type", "")
    kind, _, rest = raw.partition(";")
    found = re.search(r"charset\s*=\s*\"?([A-Za-z0-9._-]+)", rest)
    return (kind.strip().lower() or "application/octet-stream"), (found.group(1) if found else "")


def decode(body: bytes, charset: str = "") -> str:
    """``body`` as text in its declared charset (UTF-8 when none is declared or the declared one is unknown)."""
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def source_of(reply: http.Response, *, uri: str = "", published_at: str = "") -> Source:
    """The source of a reply's bytes: the address they came from (after redirects) unless ``uri`` names it."""
    return Source(uri=uri or reply.url, sha256=sha256_hex(reply.body), retrieved_at=now(), provenance="fetched",
                  published_at=published_at)


def result(source: str, requested: str, reply: http.Response, documents: list[Document], *, total: int | None = None,
           kind: str = "", reason: str = "") -> FetchResult:
    """An answered fetch: ``ok`` with documents, else ``empty``."""
    return FetchResult(source=source, outcome="ok" if documents else "empty", reason=reason, url=requested or reply.url,
                       status=reply.status, media_type=kind or media_type(reply.headers)[0], documents=documents,
                       total=total, bytes_read=len(reply.body))


def text_result(source: str, requested: str, reply: http.Response) -> FetchResult:
    """A reply read as one text document (``empty`` when it holds no text)."""
    kind, charset = media_type(reply.headers)
    text = decode(reply.body, charset)
    documents = [Document(text=text, source=source_of(reply), media_type=kind)] if text.strip() else []
    return result(source, requested, reply, documents, kind=kind)


def html_result(source: str, requested: str, reply: http.Response) -> FetchResult:
    """An HTML page read as a reader sees it (scripts, styles and hidden text dropped, the title kept apart), as one
    document that says it came from HTML; ``empty`` when no text is left without running the page's scripts."""
    kind, charset = media_type(reply.headers)
    title, text, dropped = _html.read_html(decode(reply.body, charset))
    if not text.strip():
        return FetchResult(source=source, outcome="empty", url=requested or reply.url, status=reply.status,
                           media_type=kind, bytes_read=len(reply.body),
                           reason="the page has no text a reader sees without running its scripts (G6 runs none), "
                                  "or it is blank" + "".join(f"; {why}" for why in dropped))
    document = Document(text=text, source=source_of(reply), media_type=kind, converted_by=_html.CONVERTED_BY,
                        title=title, unread=dropped)
    return result(source, requested, reply, [document], kind=kind)


def kept_result(source: str, requested: str, reply: http.Response) -> FetchResult:
    """A reply that is not text, kept by its hash for knowledge_converter."""
    kind = media_type(reply.headers)[0]
    if not reply.body.strip():
        return result(source, requested, reply, [], kind=kind)
    kept = _store.keep(reply.body, url=reply.url, requested=requested or reply.url, retrieved_at=now())
    return FetchResult(source=source, outcome="ok", url=requested or reply.url, status=reply.status, media_type=kind,
                       kept=kept, bytes_read=len(reply.body),
                       reason=f"{len(reply.body)} bytes of {kind} kept for conversion")


def failed(source: str, requested: str, reply: http.Response, outcome: Outcome, why: str) -> FetchResult:
    """A reply that answered but could not be used (``parse_failed``, ``rejected``)."""
    return FetchResult(source=source, outcome=outcome, reason=why[:500], url=requested or reply.url,
                       status=reply.status, media_type=media_type(reply.headers)[0], bytes_read=len(reply.body))


class ParseProblem(ValueError):
    """A reply that is not the format its source speaks."""


def _declares(*_: object) -> None:
    raise ParseProblem("the reply declares a document type or entities, which G6 does not parse")


def xml_root(body: bytes) -> ET.Element:
    """The root of an XML reply. The whole reply is read first by expat, in whatever encoding it declares, with a
    document type, an entity declaration and an external entity each refused, so ElementTree only ever parses a reply
    that declares none and nothing is expanded."""
    guard = expat.ParserCreate()
    guard.StartDoctypeDeclHandler = _declares
    guard.EntityDeclHandler = _declares
    guard.UnparsedEntityDeclHandler = _declares
    guard.ExternalEntityRefHandler = _declares
    try:
        guard.Parse(body, True)
        return ET.fromstring(body)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise ParseProblem(f"the reply is not well-formed XML: {exc}") from None


def json_body(body: bytes) -> Any:
    """A JSON reply's value."""
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ParseProblem(f"the reply is not JSON: {exc}") from None


__all__ = ["ADAPTER_FAILED", "ANSWERED", "CODES", "FETCH_INPUT", "SOURCES", "TEXT_TYPES", "UNKNOWN_SOURCE",
           "USER_AGENT", "Clients", "FetchRefused", "FetchRequest", "FetchResult", "Outcome", "ParseProblem",
           "SourceAdapter", "SourceRegistry", "client_for", "decode", "failed", "fetch", "json_body", "kept_result",
           "html_result", "media_type", "outcome_of", "result", "source_of", "text_result", "xml_root"]