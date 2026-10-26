# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.knowledge_manifest — keep the sources and passages an agent cites, and check a passage against its source as read again.

An extension in the knowledge family. ``record`` keeps a document and the passages cut from it in a SQLite file under
G6's state folder (``$G6_HOME/knowledge/manifest.db``), content-addressed, so the same text from a second source is a
second record with its own source; only passages that are exact spans of their document are kept, and a record
edited by hand afterwards is reported, not served. ``verify_passage`` re-reads the source (from Python, a function
that re-reads it; over MCP, the document as the caller read it again), recomputes the text's hash and compares the
words at the passage's offsets: ``matches`` (``qualified-draft``), or ``changed`` with where the words are now,
``source_unavailable`` or ``unverifiable`` (each ``blocked-escalated``). Nothing unchecked passes.

    from g6.knowledge.knowledge_manifest import verify_passage
    verdict = verify_passage(passage, reread)   # reread: a Document, or a function from a Source to one
    verdict.outcome, verdict.span_intact, verdict.found_at

Ops: ``record``, ``get`` and ``verify_passage``. None reaches the network or runs anything; ``record`` writes only the
manifest in G6's own state folder.

Not ported from the archive's knowledge_manifest, and why: the check that answered "provenance ok" for a passage with
no hash, having checked nothing; a hash that bound stored text to a source id but was never compared with the source
again; passage ids that were the text's hash, which kept the first source of identical text and lost the second;
concept edges, the confidence and authority columns with their fixed 0.5, epistemic status, its own full-text search
(the index stage's job) and the corpus in the home folder.
"""
from g6.knowledge.knowledge_manifest import manifest
from g6.knowledge.knowledge_manifest.block import SENSITIVE_OPS, KnowledgeManifestBlock, KnowledgeManifestInput
from g6.knowledge.knowledge_manifest.manifest import Store, StoreError, Verdict, verify_passage

__g6_component__ = True
__g6_block__ = "KnowledgeManifestBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # record writes only the manifest in G6's own state folder
__g6_requires__: tuple[str, ...] = ("grounding",)  # through g6.knowledge._types (its passages and tripwire)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("stale_citation",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "KnowledgeManifestBlock", "KnowledgeManifestInput", "Store", "StoreError", "Verdict",
           "manifest", "verify_passage"]