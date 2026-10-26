---
name: g6-knowledge-fetcher
description: Use when an agent must read a web page or search arXiv, an OAI-PMH repository, OpenAlex or Semantic Scholar, and must know exactly what came back, so a blocked, failed or cut-short fetch is never read as "there is nothing", and fetched text is kept as untrusted data rather than instructions.
---

# G6 knowledge fetcher

Fetches a page or a search through G6's checked HTTP and says exactly what happened: what came back, with its source
and the hash of the bytes read, or the failure it was. A web page comes back as the text a reader sees. A search keeps
the source's own count of matches, so a list cut short says so.

## When to use it

- Before an agent answers from a web page or a paper search: fetch it here, so a 403, a timeout or a parse failure is
  reported as itself rather than as "no results".
- To search arXiv (`arxiv`), an OAI-PMH repository (`oai_pmh`), OpenAlex (`openalex`) or Semantic Scholar
  (`semantic_scholar`) and keep how many matches each reported.
- To fetch a PDF or another binary file: it is kept by its hash for `knowledge_converter`.

## When not to use it

- For a page behind a login, or one that needs its scripts run: G6 sends no cookies and runs no scripts, so it gets
  what the server sends a plain client.
- For general web search across engines: that is a search stage, not this.
- To decide whether what came back is true or current: `g6.grounding` judges claims, `knowledge_manifest` re-checks a
  cited passage later.

## Ops

- `fetch`: a page or a search through G6's checked HTTP (every hop checked, a size cap, a pause per host, robots.txt for pages), with a typed outcome: ok, empty, or the failure it was (refused, robots_disallowed, blocked, not_found, rate_limited, server_error, http_error, too_large, unreachable, parse_failed, rejected). Sensitive.
- `sources`: the registered sources, what each needs, the pause it keeps and whether robots.txt applies; nothing is fetched.

## Reading the result

`outcome` and its code:

- `ok` (`G6_E_KNOWLEDGE_FETCHED`, `qualified-draft`): `documents` holds the text, each with its `source` (the address
  read after redirects, the SHA-256 of the bytes, when, `fetched`), `media_type`, `title` and `locator` (a paper's own
  address). An HTML page is the text a reader sees: scripts, styles, templates and hidden elements (`hidden`,
  `aria-hidden`, an inline `display: none`) are dropped, and how much hidden text there was is listed in the
  document's `unread`; `noscript` text is kept, elements end where a browser ends them, its `title` is kept apart,
  and the document says it came from HTML (`media_type` `text/html`, `converted_by` `g6.knowledge html_to_text`).
  A body that is neither text nor
  HTML is kept instead: `kept` is its hash and `documents` is empty.
- `empty` (`G6_E_KNOWLEDGE_FETCH_EMPTY`, `qualified-draft`): the source answered with nothing, or a page has no text
  without running its scripts (the reason says which). That is not evidence that nothing exists elsewhere or under
  another query.
- Every failure is `blocked-escalated` with its own code (`G6_E_KNOWLEDGE_FETCH_` and the outcome): `refused` (not a
  public address), `robots_disallowed` (or its robots.txt could not be read), `blocked` (401, 403, 407, 451),
  `not_found` (404, 410), `rate_limited` (429), `server_error` (5xx), `http_error` (another error status or too many
  redirects), `too_large`, `unreachable` (no DNS answer, connection, TLS or a timeout), `parse_failed` (the reply is not
  the source's format, or lacks a part its API always sends, such as its count of matches) and `rejected` (the
  source said the request is malformed, or answered with its error object). Only a source's own explicit empty
  answer is `empty`.

`total` is the number of matches the source reported and `complete` whether all came back (`null` for a page).
Refusals before anything is sent: `G6_E_KNOWLEDGE_UNKNOWN_SOURCE`, `G6_E_KNOWLEDGE_FETCH_INPUT` (no url or query, or
a malformed arXiv category) and `G6_E_KNOWLEDGE_FETCH_ADAPTER` (a source adapter that broke).

## Alone and composed

Over MCP, `fetch` runs only when the person who runs the server lists it in `G6_MCP_SENSITIVE`:
`g6_invoke("knowledge_fetcher", "fetch", {"source": "arxiv", "query": "grounding", "categories": ["cs.CL"],
"max_results": 20})`, or `{"source": "http", "url": "https://..."}`, or `{"source": "oai_pmh", "url":
"https://export.arxiv.org/oai2", "set_spec": "cs", "since": "2026-01-01"}`. `openalex` takes `mailto` (its polite
pool's contact address), sent only when you give one.

From Python: `from g6.knowledge.knowledge_fetcher import FetchRequest, fetch, SOURCES`. `fetch(request)` returns a
`FetchResult`; `clients=` passes your own `g6.core.http.Client` per source (tests pass a transport double). Register a
source of your own with `SOURCES.register(adapter)`: it reaches the network only through the client it is given, so
it gets the same checks.

Composed: `knowledge_chunker` cuts a document into passages to cite; `knowledge_converter` turns kept bytes (a PDF)
into text; `knowledge_manifest` records passages and later re-checks them against a fresh fetch.

## Failure modes it catches

- `failure_read_as_empty` (a failed or blocked search taken to mean there are no results): each HTTP status class,
  transport error and parse failure is its own outcome and code; only a source that answered with nothing is `empty`.
- `incomplete_literature` (conclusions drawn from an incomplete search of the literature): a search keeps the
  source's own count of matches beside what came back, and says when the list is not the whole of what it holds.
- `unreadable_source` (source material skipped or garbled because its format could not be read): a fetched page's
  HTML is read as the text a reader sees, and a body in another format is kept for `knowledge_converter` rather than
  passed off as text.

## Blind spots

- `prompt_injection`: a fetched page can carry instructions. The text is untrusted data (its `trust` is always
  `untrusted`), instruction-like phrasing is named on the card, and nothing here acts on it; an agent that reads the
  text can still be swayed by it.
- A login or challenge page served with status 200 comes back as content.
- Text hidden by a style sheet rather than an inline style, and text a page's scripts would write, are not seen.
- A source's own count can be wrong, and a search can miss what its index does not hold; `complete` speaks only of
  what that source reported.
- Parsers are tested on fixtures of each format, not against the live services.
- The documented APIs are governed by their own terms, so robots.txt is read for `http` pages only.

## What it needs

Network access to the sources you name; no key, no outside program, no extra. Every request goes through
`g6.core.http` (standard library). Kept bytes go to `$G6_HOME/knowledge/fetched/`. No model is called.

## Examples

```g6-example op=sources
{}
```

The reply lists `arxiv`, `http`, `oai_pmh`, `openalex` and `semantic_scholar`, with arXiv's three-second pause.

```g6-example op=fetch
{"source": "arxiv", "query": "citation grounding", "categories": ["cs.CL"], "max_results": 5}
```

Sensitive: over MCP it runs only when the server's owner allows it. The reply's `total` is arXiv's count of
matches.

```python
from g6.core import http
from g6.knowledge.knowledge_fetcher import FetchRequest, fetch

def answers(request):  # the network, in this example: no robots.txt, and the page refuses access
    if request.url.endswith("/robots.txt"):
        return http.Response(404, {}, b"", request.url)
    return http.Response(403, {"content-type": "text/html"}, b"<h1>Access denied</h1>", request.url)

def clients(adapter):
    return http.Client(transport=answers, resolver=lambda host, port: ["93.184.216.34"], obey_robots=adapter.obey_robots)

found = fetch(FetchRequest(source="http", url="https://example.org/report"), clients=clients)
assert found.outcome == "blocked" and found.documents == []  # a 403 is a failure, never an empty page
```

## The four open problems

- **Learning signals**: each reply records the outcome, the status, the address, the pause kept and whether
  robots.txt applied, per source. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: the outcome is set from the status, the transport's error and the parse
  in fixed code; no model is called and the page's words cannot change it.
- **Transfer by failure mode**: the outcomes are bound to `failure_read_as_empty` and `incomplete_literature` and
  apply to every source registered, including your own.
- **Enough held-out cases**: Not yet: the tests are fixed fixtures and transport doubles; there is no frozen corpus
  of real 403, redirect and changed-page replies.