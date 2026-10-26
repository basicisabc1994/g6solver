# SPDX-License-Identifier: Apache-2.0
"""Where ctx_library_mapper looks for libraries: PyPI, GitHub and Hugging Face, each asked through G6's checked HTTP.

Each source is registered by name (:data:`SOURCES`) and asked through the ``g6.core.http`` client it is given: every
hop checked, a size cap, one time limit per request and a pause between requests to one host (GitHub's
unauthenticated search allows 10 requests a minute, so its pause is six seconds). No key is sent anywhere. Each answer
has a typed outcome: ``ok``, ``empty`` (the source answered with nothing), or the failure it was (``refused``,
``blocked``, ``not_found``, ``rate_limited``, ``server_error``, ``http_error``, ``too_large``, ``unreachable``,
``parse_failed``, ``rejected``), so a source that could not be asked is never read as "no library exists".

* ``pypi``: PyPI has no search API, so the query is looked up as an exact project name (as given, and with its spaces
  as hyphens) in PyPI's JSON API, ``https://pypi.org/pypi/<name>/json``. A name with no project is ``empty`` for that
  name, which says nothing about projects under other names.
* ``github``: repository search, ``https://api.github.com/search/repositories``, by stars, with the total count GitHub
  reports.
* ``huggingface``: model search, ``https://huggingface.co/api/models``, by downloads.

A candidate is what the registry says about itself: its name, address, description (untrusted text), the licence it
declares as the registry gives it, and the registry's own numbers (stars, downloads), labelled as the registry's.
Nothing here judges whether a candidate implements anything.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from g6.core import http
from g6.knowledge._types import Source

OUTCOMES = ("ok", "empty", "refused", "blocked", "not_found", "rate_limited", "server_error", "http_error",
            "too_large", "unreachable", "parse_failed", "rejected")
#: The outcomes in which the source answered: every other one is a failure, never "nothing found".
ANSWERED = frozenset({"ok", "empty"})
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20.0
_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class Candidate:
    """One library a registry lists, in the registry's own words."""

    name: str
    source: str
    url: str
    description: str = ""
    declared_licence: str = ""
    version: str = ""
    stars: int | None = None
    downloads: int | None = None
    updated: str = ""
    archived: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "source": self.source, "url": self.url, "description": self.description,
                "declared_licence": self.declared_licence, "version": self.version, "stars": self.stars,
                "downloads": self.downloads, "updated": self.updated, "archived": self.archived}


@dataclass
class SourceAnswer:
    """What one source said: its outcome, the candidates, the total it reported, and what was read."""

    source: str
    outcome: str
    candidates: list[Candidate] = field(default_factory=list)
    total: int | None = None
    reason: str = ""
    read: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "outcome": self.outcome, "count": len(self.candidates), "total": self.total,
                "reason": self.reason, "read": self.read}


class CandidateSource(Protocol):
    """A registry of libraries: asked only through the client it is given."""

    name: str
    host: str
    min_interval: float

    def search(self, query: str, max_results: int, client: http.Client) -> SourceAnswer: ...


class Failed(Exception):
    def __init__(self, outcome: str, reason: str) -> None:
        super().__init__(reason)
        self.outcome, self.reason = outcome, reason


def _outcome_of(exc: http.HTTPError) -> str:
    if exc.code == http.REFUSED:
        return "refused"
    if exc.code == http.TOO_LARGE:
        return "too_large"
    if exc.code == http.FAILED:
        return "unreachable"
    status = exc.status or 0
    if status in (401, 403, 407, 451):
        return "blocked"
    if status in (404, 410):
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status in (400, 422):
        return "rejected"
    if status >= 500:
        return "server_error"
    return "http_error"


def get_json(client: http.Client, url: str, read: list[dict[str, Any]]) -> Any:
    """The JSON body at ``url`` (a :class:`Source` of the bytes read is added to ``read``); :class:`Failed` with the
    outcome for any failure."""
    try:
        reply = client.get(url, headers={"Accept": "application/json"})
    except http.HTTPError as exc:
        raise Failed(_outcome_of(exc), f"{url}: {exc}"[:300]) from None
    read.append(Source.of(reply.url, reply.body, provenance="fetched").model_dump())
    try:
        return json.loads(reply.body)
    except (ValueError, UnicodeDecodeError):
        kind = reply.headers.get("content-type", "unknown")
        raise Failed("parse_failed", f"{url} answered {reply.status} with {kind}, not JSON") from None


def _text(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class PyPISource:
    """PyPI's JSON API, asked for the query as an exact project name (see the module docstring)."""

    name, host, min_interval = "pypi", "https://pypi.org", 1.0

    @staticmethod
    def names(query: str) -> list[str]:
        out = []
        for name in (query.strip(), "-".join(query.split())):
            if _NAME.fullmatch(name) and name.lower() not in [n.lower() for n in out]:
                out.append(name)
        return out

    def search(self, query: str, max_results: int, client: http.Client) -> SourceAnswer:
        names = self.names(query)[:max_results]
        if not names:
            return SourceAnswer(self.name, "rejected", reason="PyPI has no search API: give an exact project name "
                                                              "(letters, digits, '.', '_' or '-')")
        read: list[dict[str, Any]] = []
        found, missing = [], []
        for name in names:
            try:
                data = get_json(client, f"{self.host}/pypi/{quote(name, safe='')}/json", read)
            except Failed as exc:
                if exc.outcome == "not_found":
                    missing.append(name)
                    continue
                return SourceAnswer(self.name, exc.outcome, reason=exc.reason, read=read)
            info = data.get("info") if isinstance(data, dict) else None
            if not isinstance(info, dict) or not isinstance(info.get("name"), str):
                return SourceAnswer(self.name, "parse_failed", reason="PyPI's reply has no project info", read=read)
            licence = _text(info.get("license_expression")) or _text(info.get("license"), 200)
            classifiers = [c for c in info.get("classifiers") or () if isinstance(c, str) and c.startswith("License ::")]
            files = data.get("urls") if isinstance(data.get("urls"), list) else []
            uploaded = next((f.get("upload_time_iso_8601") for f in files if isinstance(f, dict)), "")
            found.append(Candidate(info["name"], self.name, f"https://pypi.org/project/{info['name']}/",
                                   _text(info.get("summary")), licence or " | ".join(classifiers),
                                   _text(info.get("version"), 100), updated=_text(uploaded, 40)))
        if not found:
            return SourceAnswer(self.name, "empty", reason=f"PyPI has no project named {' or '.join(missing)}; it has "
                                                           "no search API, so projects under other names were not "
                                                           "looked for", read=read)
        return SourceAnswer(self.name, "ok", found, total=None, read=read)


class GitHubSource:
    """GitHub's repository search, unauthenticated (10 requests a minute)."""

    name, host, min_interval = "github", "https://api.github.com", 6.0

    def search(self, query: str, max_results: int, client: http.Client) -> SourceAnswer:
        read: list[dict[str, Any]] = []
        url = f"{self.host}/search/repositories?" + urlencode({"q": query, "sort": "stars", "order": "desc",
                                                               "per_page": max_results})
        try:
            data = get_json(client, url, read)
        except Failed as exc:
            why = (" (GitHub's unauthenticated search allows 10 requests a minute, and a 403 is often that limit)"
                   if exc.outcome == "blocked" else "")
            return SourceAnswer(self.name, exc.outcome, reason=exc.reason + why, read=read)
        items, total = (data.get("items"), data.get("total_count")) if isinstance(data, dict) else (None, None)
        if not isinstance(items, list) or _int(total) is None:
            return SourceAnswer(self.name, "parse_failed", reason="GitHub's reply has no items or total_count",
                                read=read)
        found = []
        for item in items[:max_results]:
            if not isinstance(item, dict) or not isinstance(item.get("full_name"), str):
                continue
            licence = item.get("license") if isinstance(item.get("license"), dict) else {}
            found.append(Candidate(item["full_name"], self.name, _text(item.get("html_url"), 500),
                                   _text(item.get("description")), _text(licence.get("spdx_id"), 100),
                                   stars=_int(item.get("stargazers_count")), updated=_text(item.get("pushed_at"), 40),
                                   archived=item.get("archived") if isinstance(item.get("archived"), bool) else None))
        return SourceAnswer(self.name, "ok" if found else "empty", found, total=total, read=read,
                            reason="" if found else "GitHub's search answered with no repository")


class HuggingFaceSource:
    """Hugging Face's model search, by downloads."""

    name, host, min_interval = "huggingface", "https://huggingface.co", 1.0

    def search(self, query: str, max_results: int, client: http.Client) -> SourceAnswer:
        read: list[dict[str, Any]] = []
        url = f"{self.host}/api/models?" + urlencode({"search": query, "sort": "downloads", "direction": "-1",
                                                      "limit": max_results})
        try:
            data = get_json(client, url, read)
        except Failed as exc:
            return SourceAnswer(self.name, exc.outcome, reason=exc.reason, read=read)
        if not isinstance(data, list):
            return SourceAnswer(self.name, "parse_failed", reason="Hugging Face's reply is not a list of models",
                                read=read)
        found = []
        for item in data[:max_results]:
            ident = item.get("id") or item.get("modelId") if isinstance(item, dict) else None
            if not isinstance(ident, str):
                continue
            tags = [t for t in item.get("tags") or () if isinstance(t, str)]
            licence = next((t.partition(":")[2] for t in tags if t.startswith("license:")), "")
            found.append(Candidate(ident, self.name, f"https://huggingface.co/{ident}", _text(item.get("pipeline_tag")),
                                   licence, downloads=_int(item.get("downloads")),
                                   updated=_text(item.get("lastModified") or item.get("createdAt"), 40)))
        return SourceAnswer(self.name, "ok" if found else "empty", found, read=read,
                            reason="" if found else "Hugging Face's search answered with no model")


class CandidateSources:
    """The registered sources, by name."""

    def __init__(self, sources: Iterable[CandidateSource] = ()) -> None:
        self._sources: dict[str, CandidateSource] = {}
        for source in sources:
            self.register(source)

    def register(self, source: CandidateSource) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", getattr(source, "name", "")):
            raise ValueError("a source's name is a lower-case word")
        self._sources[source.name] = source

    def get(self, name: str) -> CandidateSource | None:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return sorted(self._sources)

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "host": s.host, "pause_seconds": s.min_interval, "key": "none"}
                for s in (self._sources[n] for n in self.names())]


SOURCES = CandidateSources([PyPISource(), GitHubSource(), HuggingFaceSource()])
Clients = Callable[[CandidateSource], http.Client]
_CLIENTS: dict[str, http.Client] = {}


def default_client(source: CandidateSource) -> http.Client:
    """One checked client per source for the life of the process, so its pause holds across calls."""
    if source.name not in _CLIENTS:
        _CLIENTS[source.name] = http.Client(timeout=TIMEOUT, max_bytes=MAX_BYTES, min_interval=source.min_interval,
                                            user_agent="g6-ctx-library-mapper")
    return _CLIENTS[source.name]


def ask(source: CandidateSource, query: str, max_results: int, clients: Clients | None = None) -> SourceAnswer:
    """``source``'s answer; a source that raises is ``parse_failed`` with the reason, never an empty answer."""
    client = (clients or default_client)(source)
    try:
        answer = source.search(query, max_results, client)
    except Exception as exc:  # noqa: BLE001 - a broken source is a failure, never "nothing found"
        return SourceAnswer(source.name, "parse_failed", reason=f"the {source.name} source broke: "
                                                                f"{type(exc).__name__}: {exc}"[:300])
    if answer.outcome not in OUTCOMES:
        return SourceAnswer(source.name, "parse_failed", reason=f"the {source.name} source gave the unknown outcome "
                                                                f"{answer.outcome!r}")
    return answer


__all__ = ["ANSWERED", "OUTCOMES", "SOURCES", "Candidate", "CandidateSource", "CandidateSources", "Clients",
           "GitHubSource", "HuggingFaceSource", "PyPISource", "SourceAnswer", "ask", "default_client", "get_json"]
