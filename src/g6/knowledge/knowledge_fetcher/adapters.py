# SPDX-License-Identifier: Apache-2.0
"""The five sources the family ships, each reaching the network only through the checked client it is given.

* ``http``: one page or file at a public address. Its ``robots.txt`` is obeyed. Text (plain, Markdown, CSV, JSON)
  becomes a document; an HTML page becomes the text a reader sees (``g6.knowledge._html``: scripts, styles and
  hidden text dropped) in a document that says it came from HTML; any other body is kept by its hash for
  ``knowledge_converter``.
* ``arxiv``: arXiv's search API (``export.arxiv.org/api/query``, one request every three seconds as arXiv asks). The
  Atom feed is parsed from the bytes fetched; each entry becomes a document, and arXiv's own count of matches is kept.
* ``oai_pmh``: any OAI-PMH repository's ``ListRecords`` in Dublin Core (arXiv's is ``export.arxiv.org/oai2``),
  following its resumption token for up to ten pages; deleted records are skipped and ``noRecordsMatch`` is ``empty``.
* ``openalex``: OpenAlex's works search; its contact address (``mailto``, for its polite pool) is sent only when the
  caller gives one, and never kept in the source's address.
* ``semantic_scholar``: Semantic Scholar's paper search, without a key (its public rate limit applies).

The documented search APIs are governed by their own terms, so ``robots.txt`` is read for ``http`` pages only.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from g6.core import http
from g6.knowledge._html import HTML_TYPES
from g6.knowledge._types import Document
from g6.knowledge.knowledge_fetcher.fetcher import (SOURCES, TEXT_TYPES, FetchRefused, FetchRequest, FetchResult,
                                                    ParseProblem, failed, html_result, json_body, kept_result,
                                                    media_type, result, source_of, text_result, xml_root)

_ATOM = "{http://www.w3.org/2005/Atom}"
_OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
_OAI = "{http://www.openarchives.org/OAI/2.0/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_OAI_DC = "{http://www.openarchives.org/OAI/2.0/oai_dc/}"
_FIELD = re.compile(r"^\s*(?:ti|au|abs|co|jr|cat|rn|id|all):")
_CATEGORY = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{0,39}")
#: The most OAI-PMH pages one fetch follows.
OAI_PAGES = 10


def _plain(text: str | None) -> str:
    return " ".join((text or "").split())


def _record(title: str, lines: list[str], body: str) -> str:
    head = "\n".join([title, *(line for line in lines if line)])
    return f"{head}\n\n{body}" if body else head


class HttpSource:
    name, needs, min_interval, obey_robots = "http", "url", 1.0, True
    summary = ("one page or file at a public address; text and HTML (as a reader sees it) become a document, any "
               "other body is kept for conversion")

    def fetch(self, request: FetchRequest, client: http.Client) -> FetchResult:
        reply = client.get(request.url, headers={"Accept": "text/html, text/plain;q=0.9, */*;q=0.5"})
        kind = media_type(reply.headers)[0]
        if kind in TEXT_TYPES:
            return text_result(self.name, request.url, reply)
        if kind in HTML_TYPES:
            return html_result(self.name, request.url, reply)
        return kept_result(self.name, request.url, reply)


class ArxivSource:
    name, needs, min_interval, obey_robots = "arxiv", "query", 3.0, False
    summary = "arXiv's search API: papers matching a query, optionally within categories, with arXiv's own count"
    base = "https://export.arxiv.org/api/query"

    def fetch(self, request: FetchRequest, client: http.Client) -> FetchResult:
        bad = [c for c in request.categories if not _CATEGORY.fullmatch(c)]
        if bad:
            raise FetchRefused("G6_E_KNOWLEDGE_FETCH_INPUT", f"{bad[0]!r} is not an arXiv category (such as cs.AI)")
        search = request.query.strip() if _FIELD.match(request.query) else f"all:{request.query.strip()}"
        if request.categories:
            search += " AND (" + " OR ".join(f"cat:{c}" for c in request.categories) + ")"
        url = f"{self.base}?" + urlencode({"search_query": search, "start": 0, "max_results": request.max_results},
                                          quote_via=quote)
        reply = client.get(url, headers={"Accept": "application/atom+xml"})
        try:
            root = xml_root(reply.body)
        except ParseProblem as exc:
            return failed(self.name, url, reply, "parse_failed", str(exc))
        entries = root.findall(f"{_ATOM}entry")
        errors = [e for e in entries if (e.findtext(f"{_ATOM}id") or "").startswith("http://arxiv.org/api/errors")]
        if errors:
            return failed(self.name, url, reply, "rejected",
                          f"arXiv refused the query: {_plain(errors[0].findtext(f'{_ATOM}summary'))}")
        if root.tag != f"{_ATOM}feed":
            return failed(self.name, url, reply, "parse_failed", "the reply is not an Atom feed")
        counted = (root.findtext(f"{_OPENSEARCH}totalResults") or "").strip()
        if not counted.isdigit():  # arXiv always counts its matches; a feed without a count is not its reply
            return failed(self.name, url, reply, "parse_failed", f"arXiv's count of matches is {counted or 'missing'!r}")
        documents = []
        for entry in entries:
            title = _plain(entry.findtext(f"{_ATOM}title"))
            published = _plain(entry.findtext(f"{_ATOM}published"))[:10]
            authors = [_plain(a.findtext(f"{_ATOM}name")) for a in entry.findall(f"{_ATOM}author")]
            categories = [c.get("term", "") for c in entry.findall(f"{_ATOM}category") if c.get("term")]
            text = _record(title, [f"Authors: {', '.join(a for a in authors if a)}" if any(authors) else "",
                                   f"Published: {published}" if published else "",
                                   f"Categories: {', '.join(categories)}" if categories else ""],
                           _plain(entry.findtext(f"{_ATOM}summary")))
            documents.append(Document(text=text, source=source_of(reply, published_at=published),
                                      media_type="application/atom+xml", converted_by="g6.knowledge arXiv Atom entry",
                                      title=title, locator=_plain(entry.findtext(f"{_ATOM}id"))))
        return result(self.name, url, reply, documents, total=int(counted), kind="application/atom+xml")


class OaiPmhSource:
    name, needs, min_interval, obey_robots = "oai_pmh", "url", 3.0, False
    summary = "an OAI-PMH repository's records in Dublin Core (arXiv's is https://export.arxiv.org/oai2)"

    def fetch(self, request: FetchRequest, client: http.Client) -> FetchResult:
        params: dict[str, str] = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
        if request.set_spec:
            params["set"] = request.set_spec
        if request.since:
            params["from"] = request.since
        documents: list[Document] = []
        total: int | None = None
        reply = None
        joiner = "&" if "?" in request.url else "?"
        for _ in range(OAI_PAGES):
            page = f"{request.url}{joiner}{urlencode(params, quote_via=quote)}"
            reply = client.get(page, headers={"Accept": "text/xml, application/xml"})
            try:
                root = xml_root(reply.body)
            except ParseProblem as exc:
                return failed(self.name, request.url, reply, "parse_failed", str(exc))
            if root.tag != f"{_OAI}OAI-PMH":
                return failed(self.name, request.url, reply, "parse_failed", "the reply is not an OAI-PMH response")
            error = root.find(f"{_OAI}error")
            if error is not None:
                if error.get("code") == "noRecordsMatch":
                    total = len(documents) if total is None else total
                    break
                return failed(self.name, request.url, reply, "rejected",
                              f"the repository refused the request ({error.get('code', '')}): {_plain(error.text)}")
            listed = root.find(f"{_OAI}ListRecords")
            if listed is None or listed.find(f"{_OAI}record") is None:  # an empty list is noRecordsMatch, never this
                return failed(self.name, request.url, reply, "parse_failed",
                              "the reply has neither records nor an error, which OAI-PMH does not allow")
            for record in listed.iter(f"{_OAI}record"):
                header = record.find(f"{_OAI}header")
                dc = record.find(f".//{_OAI_DC}dc")
                if header is None or header.get("status") == "deleted" or dc is None:
                    continue
                values = {name: [_plain(e.text) for e in dc.findall(f"{_DC}{name}") if _plain(e.text)]
                          for name in ("title", "creator", "subject", "description", "identifier", "date")}
                title = (values["title"] or [""])[0]
                date = (values["date"] or [_plain(header.findtext(f"{_OAI}datestamp"))])[0][:10]
                text = _record(title, [f"Creators: {'; '.join(values['creator'])}" if values["creator"] else "",
                                       f"Subjects: {'; '.join(values['subject'])}" if values["subject"] else "",
                                       f"Date: {date}" if date else ""], "\n\n".join(values["description"]))
                link = next((i for i in values["identifier"] if i.startswith(("http://", "https://"))),
                            _plain(header.findtext(f"{_OAI}identifier")))
                documents.append(Document(text=text, source=source_of(reply, published_at=date),
                                          media_type="text/xml", converted_by="g6.knowledge OAI-PMH Dublin Core record",
                                          title=title, locator=link))
                if len(documents) >= request.max_results:
                    break
            token = root.find(f".//{_OAI}resumptionToken")
            if token is not None and (token.get("completeListSize") or "").isdigit():
                total = int(token.get("completeListSize"))
            if len(documents) >= request.max_results or token is None or not _plain(token.text):
                break
            params = {"verb": "ListRecords", "resumptionToken": _plain(token.text)}
        return result(self.name, request.url, reply, documents, total=total, kind="text/xml")


class OpenAlexSource:
    name, needs, min_interval, obey_robots = "openalex", "query", 1.0, False
    summary = "OpenAlex's works search; its contact address is sent only when the caller gives one"
    base = "https://api.openalex.org/works"

    def fetch(self, request: FetchRequest, client: http.Client) -> FetchResult:
        params = {"search": request.query.strip(), "per-page": request.max_results}
        recorded = f"{self.base}?{urlencode(params, quote_via=quote)}"
        url = f"{recorded}&{urlencode({'mailto': request.mailto}, quote_via=quote)}" if request.mailto else recorded
        reply = client.get(url, headers={"Accept": "application/json"})
        try:
            data = json_body(reply.body)
            if isinstance(data, dict) and "error" in data and "results" not in data:
                said = _plain(str(data.get("message") or data["error"]))
                return failed(self.name, recorded, reply, "rejected", f"OpenAlex refused the request: {said}")
            works, count = data["results"], data["meta"]["count"]
            if not isinstance(works, list) or not isinstance(count, int) or isinstance(count, bool):
                raise ParseProblem("the reply has no list of results with its count of matches")
        except (ParseProblem, KeyError, TypeError, AttributeError) as exc:
            return failed(self.name, recorded, reply, "parse_failed", f"OpenAlex's reply is not a works list: {exc}")
        documents = []
        for work in works:
            if not isinstance(work, dict):
                return failed(self.name, recorded, reply, "parse_failed", "a work in OpenAlex's reply is not an object")
            words = work.get("abstract_inverted_index") or {}
            placed = sorted((p, w) for w, positions in words.items() for p in positions) if isinstance(words, dict) \
                else []
            title = _plain(work.get("display_name") or work.get("title"))
            date = _plain(work.get("publication_date"))[:10]
            text = _record(title, [f"Published: {date}" if date else ""], " ".join(w for _, w in placed))
            documents.append(Document(text=text, source=source_of(reply, uri=recorded, published_at=date),
                                      media_type="application/json", converted_by="g6.knowledge OpenAlex work",
                                      title=title, locator=_plain(work.get("doi") or work.get("id"))))
        return result(self.name, recorded, reply, documents, total=count, kind="application/json")


class SemanticScholarSource:
    name, needs, min_interval, obey_robots = "semantic_scholar", "query", 1.0, False
    summary = "Semantic Scholar's paper search, without a key (its public rate limit applies)"
    base = "https://api.semanticscholar.org/graph/v1/paper/search"

    def fetch(self, request: FetchRequest, client: http.Client) -> FetchResult:
        params = {"query": request.query.strip(), "limit": request.max_results,
                  "fields": "title,abstract,year,url,externalIds,publicationDate"}
        url = f"{self.base}?{urlencode(params, quote_via=quote)}"
        reply = client.get(url, headers={"Accept": "application/json"})
        try:
            data = json_body(reply.body)
            said = (data.get("error") or data.get("message")) if "data" not in data else None
            if said:
                return failed(self.name, url, reply, "rejected", f"Semantic Scholar refused the request: "
                                                                 f"{_plain(str(said))}")
            total = data["total"]
            if not isinstance(total, int) or isinstance(total, bool):
                raise ParseProblem("the reply has no count of matches")
            papers = data["data"] if "data" in data or total else []  # it leaves data out when nothing matches
            if not isinstance(papers, list):
                raise ParseProblem("the reply has no list of papers")
        except (ParseProblem, KeyError, TypeError, AttributeError) as exc:
            return failed(self.name, url, reply, "parse_failed", f"Semantic Scholar's reply is not a paper list: {exc}")
        documents = []
        for paper in papers:
            if not isinstance(paper, dict):
                return failed(self.name, url, reply, "parse_failed", "a paper in the reply is not an object")
            title = _plain(paper.get("title"))
            date = _plain(paper.get("publicationDate") or str(paper.get("year") or ""))[:10]
            doi = _plain((paper.get("externalIds") or {}).get("DOI")) if isinstance(paper.get("externalIds"), dict) \
                else ""
            text = _record(title, [f"Published: {date}" if date else ""], _plain(paper.get("abstract")))
            documents.append(Document(text=text, source=source_of(reply, published_at=date),
                                      media_type="application/json", converted_by="g6.knowledge Semantic Scholar paper",
                                      title=title, locator=f"https://doi.org/{doi}" if doi else _plain(paper.get("url"))))
        return result(self.name, url, reply, documents, total=total, kind="application/json")


for _adapter in (HttpSource(), ArxivSource(), OaiPmhSource(), OpenAlexSource(), SemanticScholarSource()):
    SOURCES.register(_adapter, replace=True)

__all__ = ["OAI_PAGES", "ArxivSource", "HttpSource", "OaiPmhSource", "OpenAlexSource", "SemanticScholarSource"]