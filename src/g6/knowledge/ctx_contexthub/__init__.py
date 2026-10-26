# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.ctx_contexthub — keep a documentation registry you name, each document with when it was read and a time to live, and refuse a copy past it.

An extension in the knowledge family. ``sync`` reads a registry in context-hub's format (``registry.json`` and each
entry's ``DOC.md``) from the address or folder the person names (there is no default host), an address only through
``knowledge_fetcher``'s guarded fetch, invoked through the component registry: every redirect hop checked, no private
address, a cap on the body. Each document is kept under G6's state folder with its own ``retrieved_at``, the version
the registry names and a time to live; one not read again keeps its earlier date and goes stale on it. ``search``
ranks the kept entries by BM25 over name, tags, description and content; ``get`` returns a document and, apart from
it, the notes kept on its entry, and refuses one past its time to live. Notes are plainly a caller's unchecked
records and are never written into a document. All of it is untrusted text: a registry vouches only for itself.

    from g6.knowledge.ctx_contexthub import hub
    hub.sync("vendor-docs", "https://docs.example.org/hub")   # through knowledge_fetcher's fetch
    hub.search("vendor-docs", "rate limits")                    # entries with their documents' dates
    doc, stale, age = hub.get("vendor-docs", "acme/api")

Not ported from the archive's ctx_contexthub, and why: the default third-party host and the fallback to it; the
fetch whose redirects went unchecked, with no size cap; the cache served first and never expired, and freshness
measured on the registry's sync time; agent notes appended to the documents; the bundled registry of the former
service's own launch documents; local folders an MCP caller could register; and the 25-op MCP analytics (usage,
popular documents, feedback, quality reports), the re-ranker and the production gate.
"""
from g6.knowledge.ctx_contexthub import hub
from g6.knowledge.ctx_contexthub.block import SENSITIVE_OPS, CtxContexthubBlock, CtxContexthubInput
from g6.knowledge.ctx_contexthub.hub import HubError

__g6_component__ = True
__g6_block__ = "CtxContexthubBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # sync reaches the network or reads the machine's files
__g6_requires__: tuple[str, ...] = ("grounding",)  # its BM25 ranking, and through g6.knowledge._types
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a document and a
# note are outside text that can carry instructions).
__g6_mitigates__: tuple[str, ...] = ("stale_context_used",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "CtxContexthubBlock", "CtxContexthubInput", "HubError", "hub"]
