# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.ctx_search — search the web through the SearXNG you run, or Brave when you approve it, and tell an engine that failed from one that found nothing.

An extension in the knowledge family: the family's search stage. ``search`` asks one backend and returns a typed
outcome and the results. ``searxng`` (the default) is the SearXNG the person runs on this machine, a declared outside
service reached through ``g6.core.outside``; G6 never picks a public instance. Its own list of engines that did not
answer is read back: no results with a failed engine is ``engines_failed``, never empty, and results with one are
``partial``. ``brave`` is the paid Brave Search API, through ``g6.core.http``; its key comes only from the person's
key file, which is their approval to spend it, and each request asks ``g6.action_gating`` first. A failure is its
own outcome and code, and no other engine answers in its place. Each result is the engine's title and snippet about
an address, as a document sourced to the engine's reply; fetch the page with ``knowledge_fetcher`` before relying on
it.

    from g6.knowledge.ctx_search import search
    found = search("retrieval augmented generation evaluation", "searxng", 10)
    found.outcome, found.engines_failed, [(d.title, d.locator) for d in found.results]

Not ported from the archive's ctx_search, and why: the research loop, which summarised invented results and labelled
them verified (an agent runs its own loop: search, fetch, then g6.grounding); the stand-in engine and the results it
made up when the network was off; the DuckDuckGo path and its silent fall back to the Instant Answers API (ddgs
scrapes search engines' pages over its own networking, outside G6's checked fetch, under terms not considered); the
key read from the environment and the hosted entitlement check; the model planner, classifier and re-ranker; the
result store, curation, PubMed and Reddit ops; and the owner metadata with a personal address.
"""
from g6.knowledge.ctx_search import web
from g6.knowledge.ctx_search.block import SENSITIVE_OPS, CtxSearchBlock, CtxSearchInput
from g6.knowledge.ctx_search.web import (BRAVE_KEY, SEARCHERS, SEARXNG, BraveSearcher, SearchRefused, SearchResult,
                                            SearxngSearcher, search)

__g6_component__ = True
__g6_block__ = "CtxSearchBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # search reaches the network, and brave spends money
__g6_requires__: tuple[str, ...] = ("action_gating", "grounding")  # the gate before a paid request; _types
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a result's
# snippet is outside text that can carry instructions).
__g6_mitigates__: tuple[str, ...] = ("failure_read_as_empty",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = (SEARXNG,)
__g6_keys__ = (BRAVE_KEY,)  # from the person's key file only; a key there approves spending it
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["BRAVE_KEY", "SEARCHERS", "SEARXNG", "SENSITIVE_OPS", "BraveSearcher", "CtxSearchBlock", "CtxSearchInput",
           "SearchRefused", "SearchResult", "SearxngSearcher", "search", "web"]
