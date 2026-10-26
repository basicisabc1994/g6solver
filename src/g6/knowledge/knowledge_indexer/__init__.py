# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.knowledge_indexer — keep passages in a searchable index under a storage budget you set, and search them with each hit's source and a score on a stated scale.

An extension in the knowledge family. ``add`` keeps passages (as the chunker cut them, each with its source) in a
SQLite file with an FTS5 full-text index under G6's state folder (``$G6_HOME/knowledge/index/<name>.db``); an index
is named, never placed by a path. Its budget is the ``max_bytes`` its first write sets, kept with the index (a later
call cannot raise it): each write is projected before anything is written and measured again before it commits, and a write over the budget is refused with both numbers, the index
left as it was. ``search`` ranks the passages by BM25 (``fts5_bm25``, higher is better, comparable only within one
search) and returns each as the family's passage with its source; an index that holds nothing, a backend that is not
ready and a search that failed are each refused with their own code, never read as "no match". Every index is a
``g6.grounding`` retriever (``index.search(query, top_k)``), so the grounding checks run on what it finds.

    from g6.knowledge.knowledge_indexer import SqliteIndex
    index = SqliteIndex("notes", max_bytes=50_000_000)
    index.add(passages)                    # passages from knowledge_chunker
    found = index.find("refund window")    # outcome, hits (passage, score, score_scale, rank), searched

Backends register by name (``g6.knowledge._index.index_backends``): ``sqlite`` here, others (``elastic``) from their
own members, which this package never imports. Search results are untrusted text.

Ops: ``add``, ``search``, ``remove``, ``status`` and ``backends``. None reaches the network or runs anything; the
writes are G6's own state. ``remove`` deletes passages, so it is sensitive: over MCP it runs only when the server's
owner allows it.

Not ported from the archive's knowledge_indexer, and why: its MCP server, which labelled its own ingests and prunes
verified and let a remote caller re-point the store to any path or declare a domain ready; the budget checked on the
current size only against a fixed 400 GB; a ColBERT build whose failure was swallowed and that rebuilt the whole index
on every call; and the CLIP and CLAP vectors.
"""
from g6.knowledge.knowledge_indexer import index
from g6.knowledge.knowledge_indexer.block import SENSITIVE_OPS, KnowledgeIndexerBlock, KnowledgeIndexerInput
from g6.knowledge.knowledge_indexer.index import DEFAULT_MAX_BYTES, SqliteIndex

__g6_component__ = True
__g6_block__ = "KnowledgeIndexerBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # remove deletes passages; add writes G6's own state
__g6_requires__: tuple[str, ...] = ("grounding",)  # through g6.knowledge._types (its passages and tripwire)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unbounded_index",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}
__g6_index_backends__: tuple[str, ...] = ("sqlite",)  # the family's registry imports this package to find it

__all__ = ["DEFAULT_MAX_BYTES", "SENSITIVE_OPS", "KnowledgeIndexerBlock", "KnowledgeIndexerInput", "SqliteIndex",
           "index"]
