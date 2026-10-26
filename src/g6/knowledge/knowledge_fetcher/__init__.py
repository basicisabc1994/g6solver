# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.knowledge_fetcher — fetch pages and searches (arXiv, OAI-PMH, OpenAlex, Semantic Scholar) through G6's checked HTTP, and say exactly why a fetch failed.

An extension in the knowledge family. Every request goes through ``g6.core.http``: each redirect hop is checked
before it is requested, private, loopback, link-local and reserved addresses are refused, the connection is pinned to
the address checked, the body is capped, requests to one host are paced (arXiv's three seconds) and a page's
``robots.txt`` is obeyed. The outcome is typed: ``ok``, ``empty`` (the source answered with nothing), or the failure
it was, each with its own code (a 403 is ``blocked``, a timeout ``unreachable``, an Atom feed that does not parse
``parse_failed``), so a blocked or failed fetch is never read as "there is nothing". A search keeps the source's own
count of matches, so a list cut short says it is not the whole of the literature. An HTML page is read as the text a
reader sees (scripts, styles and hidden text dropped; merged in from the archive's ctx_scrapling), in a document that
says it came from HTML. Fetched text is untrusted data.

    from g6.knowledge.knowledge_fetcher import FetchRequest, fetch
    found = fetch(FetchRequest(source="arxiv", query="retrieval augmented generation", categories=("cs.CL",)))
    found.outcome, found.total, found.complete, [d.title for d in found.documents]

Ops: ``fetch`` (sensitive: it reaches the network, so over MCP only when the person who runs the server lists it in
``G6_MCP_SENSITIVE``) and ``sources``. Sources are registered by name (``SOURCES.register``): ``http``, ``arxiv``
(merged in from the archive's research scanner), ``oai_pmh``, ``openalex`` and ``semantic_scholar``.

From ctx_scrapling, only the checked fetch of a page and its HTML read as text are carried. Its browser and
anti-bot fetchers (which skipped the address check), the spoofed browser identity, the crawler, the parsers that ran a
caller's regular expression, the selectors and extraction that pasted a page into a model's prompt, the store and
its owner metadata are not.

Not ported from the archive's knowledge_fetcher and research scanner, and why: the personal contact address sent to
OpenAlex with every request; the address check that a redirect walked past; adapters that turned every failure into an
empty list; the process-wide patch of the HTTP library that watched for 429; the Relace adapter, which returned a paid
model's answer as a fetched record; video download; the YouTube transcripts (yt-dlp and youtube-transcript-api do
their own HTTP, outside G6's checked fetch); the Wikimedia and README adapters; the hard-coded catalogue of the former
service's crawl; and from the research scanner, its stand-in source and every result it labelled verified, the
custom-URL link scraper, the novelty and gap scores measured against G6's own component names, the store and sessions.
"""
from g6.knowledge.knowledge_fetcher import adapters, fetcher
from g6.knowledge.knowledge_fetcher.block import SENSITIVE_OPS, KnowledgeFetcherBlock, KnowledgeFetcherInput
from g6.knowledge.knowledge_fetcher.fetcher import (CODES, SOURCES, FetchRefused, FetchRequest, FetchResult,
                                                    SourceAdapter, fetch)

__g6_component__ = True
__g6_block__ = "KnowledgeFetcherBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # fetch reaches the network
__g6_requires__: tuple[str, ...] = ("grounding",)  # through g6.knowledge._types (its passages and tripwire)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a fetched page
# can carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("failure_read_as_empty", "incomplete_literature", "unreadable_source")
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["CODES", "SENSITIVE_OPS", "SOURCES", "FetchRefused", "FetchRequest", "FetchResult", "KnowledgeFetcherBlock",
           "KnowledgeFetcherInput", "SourceAdapter", "adapters", "fetch", "fetcher"]