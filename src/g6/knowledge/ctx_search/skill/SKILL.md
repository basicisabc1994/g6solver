---
name: g6-ctx-search
description: Use when an agent must search the web and know exactly what came back, so an engine that failed, was blocked or ran out of quota is never read as "there are no results", no other engine silently answers in its place, and a paid search engine is used only with the person's own key.
---

# G6 web search

Asks one web search backend and says exactly what happened. `searxng`, the default, is the SearXNG server you run on
this machine; it names the engines that did not answer. `brave` is the paid Brave Search API, used only with the key
in your key file and only when the action gate lets the request through. Each result is the engine's title and
snippet about an address, not the page.

## When to use it

- An agent needs candidate sources for a question and must not mistake a failed or blocked search for "nothing
  exists".
- You run SearXNG and want its engines' failures (a CAPTCHA, a time-out) reported, not hidden.
- You pay for Brave Search and want each request to pass G6's action gate.

## When not to use it

- To read a page: `knowledge_fetcher` fetches it (results here are snippets, which can differ from the page).
- For papers: `knowledge_fetcher`'s `arxiv`, `openalex` and `semantic_scholar` sources keep their own counts.
- To answer a question from the results: fetch the pages and check claims with `g6.grounding`; there is no research
  loop here.

## Ops

- `search`: search(query, backend, n): ask one web search backend (searxng, the server you run, by default; brave, paid, through the action gate) and get a typed outcome: ok, partial (engines that failed are named), empty, or the failure it was; results are the engine's words about each address. Sensitive.
- `backends`: backends(): the registered search backends, whether each is paid and whether it is configured; nothing is asked.

## Reading the result

- `G6_E_KNOWLEDGE_SEARCHED_WEB` (`qualified-draft`): `results`, each with `rank`, `title`, `url`, `page_date` (the
  date the engine gives for the page, its word, or `""`) and `document` (the title and snippet, `locator` the
  address, `source` the engine's reply that G6 read). `complete` is `null`: only the first page is asked for, so
  whether more exist is not known; `all_engines_answered` says whether every engine answered.
  `G6_E_KNOWLEDGE_SEARCH_PARTIAL`: some engines did not answer (`engines_failed`), so `complete` is false.
  `G6_E_KNOWLEDGE_SEARCH_EMPTY`: the engine answered with nothing.
- Every failure is `blocked-escalated` with its own code (`G6_E_KNOWLEDGE_SEARCH_` and the outcome):
  `ENGINES_FAILED` (no results, and engines failed), `BLOCKED` (403: for SearXNG, its json format is off),
  `AUTH_FAILED`, `RATE_LIMITED`, `NOT_FOUND`, `SERVER_ERROR`, `HTTP_ERROR`, `REFUSED` (a redirect or a private
  address), `TIMEOUT`, `TOO_LARGE`, `PARSE_FAILED` (a reply that is not the engine's structure, such as a Brave
  result with no title or address; only a results list that is there and empty is `EMPTY`), `REJECTED` and
  `NOT_ALLOWED` (the gate held a paid request, or the key is not in your key file); `G6_E_OUTSIDE_PROGRAM_MISSING`
  when your SearXNG does not answer.
  `G6_E_KNOWLEDGE_SEARCH_BACKEND` names a backend that is not registered.

## Alone and composed

Over MCP, `search` runs only when the person who runs the server lists it in `G6_MCP_SENSITIVE`:
`g6_invoke("ctx_search", "search", {"query": "retrieval augmented generation evaluation", "n": 10})`, or with
`"backend": "brave"`. There is no field for a key, an address or an approval.

From Python: `from g6.knowledge.ctx_search import search, SEARCHERS`; `search(query, backend, n)` returns a
`SearchResult` (`outcome`, `results`, `page_dates`, `engines_failed`, `all_engines_answered`, `complete`). Register a backend with `SEARCHERS.register(...)`
(a `name`, `summary`, `network`, `paid`, `configured()` and `search(query, n)`, answering for itself).

Composed: search here, fetch each page with `knowledge_fetcher`, cut it with `knowledge_chunker`, and check what it
supports with `g6.grounding`.

## Failure modes it catches

- `failure_read_as_empty` (a failed or blocked search taken to mean there are no results): each status, transport
  error and parse failure is its own outcome; SearXNG's list of engines that did not answer turns no results into
  `engines_failed` and some results into `partial`; only an engine that answered with nothing is `empty`, and no
  other engine answers in a failed one's place.

## Blind spots

- `prompt_injection`: a snippet is outside text and can carry instructions. It is untrusted data, instruction-like
  phrasing is named on the card, and nothing here acts on it.
- Engines rank by their own measures: engine bias and pages written to rank are in the results.
- A snippet can differ from the page, and a result's date is the engine's.
- Only the first page of results is asked for; `total` is the engine's own estimate where it gives one.

## What it needs

SearXNG, which you run yourself on this machine (AGPL-3.0, its own licence; G6 talks to it over HTTP and never
imports, bundles or starts it), at `http://127.0.0.1:8888` or the local address in `G6_SEARXNG_URL`, with its json
format turned on. For Brave: your key in your key file, `~/.g6/.env`, as `BRAVE_SEARCH_API_KEY=<key>`, which approves
spending it; each request still asks the action gate. A Brave request follows no redirect (the key travels in a
header of its own, which a redirect would carry to another host: a redirect is `REFUSED`), and requests are a second
apart. No extra and no model.

## Examples

```g6-example op=backends
{}
```

The reply lists `brave` and `searxng`, whether each is paid, and whether Brave's key is in your key file.

```g6-example op=search
{"query": "retrieval augmented generation evaluation", "n": 5}
```

Sensitive: over MCP it runs only when the server's owner allows it.

```python
from g6.core import outside
from g6.knowledge.ctx_search.web import SearxngSearcher

def server(program, method, path, **_):  # your SearXNG, in this example: every engine it asked failed
    body = b'{"results": [], "number_of_results": 0, "unresponsive_engines": [["google", "CAPTCHA"]]}'
    return outside.ServiceReply(200, {}, body)

found = SearxngSearcher(caller=server).search("rag evaluation", 5)
assert found.outcome == "engines_failed" and found.results == []  # a failure, never "no results"
```

## The four open problems

- **Learning signals**: each reply records the backend, the outcome, the engines that failed and, for Brave, the
  gate's decision. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: the outcome is set from the status, the transport's error, the parse and
  SearXNG's own list of failed engines in fixed code; no model is called, and a snippet's words change nothing.
- **Transfer by failure mode**: the outcomes are bound to `failure_read_as_empty` and apply to every backend
  registered, including your own.
- **Enough held-out cases**: Not yet: the tests use a local server and transport doubles, not a frozen set of real
  engine replies, 429s and block pages.
