---
name: g6-ctx-library-mapper
description: Use when an agent is about to write code for a method that may already exist as a library, or is choosing a dependency, and must find candidate libraries in PyPI, GitHub and Hugging Face with the licence each declares, and look those licences up against the project's own; a source that could not be asked is never read as "no library exists".
---

# G6 ctx_library_mapper

Finds candidate libraries in package and model registries and says what each source did, so a blocked or failed
search is never mistaken for "nothing exists". Each candidate carries the licence its registry declares, read into
SPDX; `licence_check` looks those licences up against the project's licence in a fixed table.

## When to use it

- Before implementing a known method (BM25, a tokenizer, an optimiser): look for libraries that already do it.
- Before adding a dependency: check its declared licence against the licence you release under.
- When a search came back empty and you need to know whether the source was really asked.

## When not to use it

- To decide that a library implements a method correctly: it finds candidates, it never reads their code.
- For legal advice: the licence verdicts are a table lookup.
- For private package indexes: register a source of your own from Python.

## Ops

- `search_candidates`: candidates from the sources named (`pypi` for an exact project name, `github`, `huggingface`), each with the licence it declares and a typed outcome per source. Sensitive: it reaches the network.
- `licence_check`: each candidate's licence looked up against `project_licence` in an SPDX table: compatible, conditions, incompatible or unknown.
- `sources`: the registered sources, their hosts and pauses; nothing is fetched.

## Reading the result

`search_candidates` keeps one entry per source in `per_source`: `outcome` is `ok`, `empty` (the source answered with
nothing), or the failure it was: `blocked` (401 or 403; GitHub's unauthenticated search allows 10 requests a
minute, and a 403 is often that), `rate_limited` (429), `not_found`, `rejected`, `server_error`, `http_error`,
`too_large`, `unreachable`, `refused` (not a public address) or `parse_failed` (the reply was not the source's
format). `total` is GitHub's own count of matches. `complete` is false when any source failed.

The reply's code: `G6_E_LIBRARY_CANDIDATES` (every source answered), `G6_E_LIBRARY_PARTIAL` (some failed: the list is
not the whole of what exists), `G6_E_LIBRARY_NONE_FOUND` (they answered with nothing: not evidence that nothing
exists), all `qualified-draft`; `G6_E_LIBRARY_SEARCH_FAILED` (no source could be asked) is `blocked-escalated`, and so
are `G6_E_LIBRARY_INPUT` and `G6_E_LIBRARY_UNKNOWN_SOURCE`. No source is searched unless named.

A candidate has `name`, `source`, `url`, `description` (the registry's own words: untrusted data), `declared_licence`
(as the registry gives it), `licence` (SPDX, or `unknown`), and the registry's own `stars`, `downloads`, `version`,
`updated` and `archived`. There is no relevance or quality score.

`licence_check` answers per candidate `verdict` and `why` (the table row). `A OR B` takes the better side, `A AND B`
the worse; an expression is read in full or not at all, so one with a stray word or unmatched parentheses is
`unknown`. Anything not in the table is `unknown`, and so is a name that does not say which licence: "BSD" alone, or
the classifier "Apache Software License", which names no version. Fields you add to a candidate (a verdict, an
approval) are ignored.

## Alone and composed

Over MCP, `licence_check` and `sources` always run; `search_candidates` runs only when the person who runs the server
lists it in `G6_MCP_SENSITIVE`: `g6_invoke("ctx_library_mapper", "search_candidates", {"query": "bm25", "sources":
["github", "huggingface"]})`, then pass its `candidates` to `licence_check` with your `project_licence`.

From Python: `CtxLibraryMapperBlock(clients=...)` takes a function from a source to a `g6.core.http.Client` (tests pass
a transport double). Register a source with `SOURCES.register(source)`: it gets the same checked client.

Composed: `knowledge_fetcher` reads a candidate's README or paper; `g6.grounding` checks a claim that a library
implements the method against what was read.

## Failure modes it catches

- `reinvented_method` (reimplementing a method that already exists): candidates from three registries, and every
  source that could not be asked named as such, so an agent does not conclude that nothing exists and write its own.

## Blind spots

- `prompt_injection`: a description can carry instructions. It is untrusted data (instruction-like phrasing is named
  on the card), and nothing here acts on it.
- PyPI is asked only for exact names (the query, and the query with hyphens): a project under another name is missed.
- A registry's declared licence can be wrong or out of date, and a repository's files may carry other licences.
- The table covers common licences only, and names no edition of any licence's text.
- Rankings are the registries' own (stars, downloads), not fitness for your method.

## What it needs

Network access to pypi.org, api.github.com and huggingface.co for `search_candidates`; no key, no outside program, no
extra. Every request goes through `g6.core.http` (standard library). No model is called.

## Examples

```g6-example op=licence_check
{"candidates": [{"name": "rank_bm25", "declared_licence": "Apache-2.0"}, {"name": "gpl-lib", "declared_licence": "GPL-3.0-only"}], "project_licence": "MIT"}
```

The first is compatible (keep its notice), the second incompatible: GPL-3.0 needs the combined work under the GPL.

```g6-example op=sources
{}
```

Lists `github`, `huggingface` and `pypi` with their pauses; nothing is fetched.

```g6-example op=search_candidates
{"query": "bm25", "sources": ["github", "huggingface"], "max_results": 5}
```

Sensitive: over MCP it runs only when the server's owner allows it.

```python
from g6.core import http
from g6.sources.ctx_library_mapper import CtxLibraryMapperBlock, CtxLibraryMapperInput

def answers(request):  # the network, in this example: GitHub refuses the search
    return http.Response(403, {}, b"API rate limit exceeded", request.url)

block = CtxLibraryMapperBlock(clients=lambda source: http.Client(
    transport=answers, resolver=lambda host, port: ["140.82.112.3"], min_interval=0))
out = block.infer(CtxLibraryMapperInput(op="search_candidates", query="bm25", sources=["github"])).unwrap()
assert out["per_source"][0]["outcome"] == "blocked" and out["completion_state"] == "blocked-escalated"
```

## The four open problems

- **Learning signals**: each reply records every source's outcome and what was read (address, hash, time). Not yet:
  which candidates a person kept is not recorded, and nothing learns from it.
- **Evaluators the model cannot influence**: the registries' own metadata and a fixed licence table; no model is
  called, and a description's words change no outcome or verdict.
- **Transfer by failure mode**: bound to `reinvented_method`; the licence table also serves a dependency chosen any
  other way (a licence-incompatible dependency).
- **Enough held-out cases**: Not yet: the tests use fixed replies; there is no frozen set of papers with known
  implementations.
