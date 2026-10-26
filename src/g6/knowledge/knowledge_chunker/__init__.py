# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.knowledge_chunker — cut a document into passages with exact offsets and ids bound to their source, and check a citation against it.

An extension in the knowledge family. A passage's text is the document's text at its offsets, exactly (line endings
are never normalised), and its id is the hash of the source's address and bytes, the text's hash and the offsets: the
same words from two sources are two passages, each citing its own, and a passage whose offsets moved is another one.
Every passage is checked against the document before it is returned; ``check`` finds a citation an agent holds whose
offsets, text, id or source do not match, and says where its text really is.

    from g6.core.registry import get_registry
    out = get_registry().invoke("knowledge_chunker", "chunk", {"text": "One. Two. Three.", "unit": "sentences",
                                                               "size": 2, "overlap": 1}).unwrap()
    [(p["start"], p["end"], p["text"]) for p in out["passages"]]   # [(0, 9, "One. Two."), (5, 16, "Two. Three.")]

Ops: ``chunk`` (characters, words or sentences; tokens through a tokenizer a Python caller passes) and ``check``. Both
are pure. A result is ``qualified-draft``: it says where words are, never that they are true.

Not ported from the archive's knowledge_chunker, and why: the id that hashed the text alone and the manifest lookup
that skipped a text already indexed (so a second source's identical passage was never recorded); text rebuilt from
joined paragraphs and decoded token slices instead of sliced from the source; the eight fixed domain labels; and
tiktoken, which fetches its encoding file from a fixed address on first use, outside G6's checked fetch.
"""
from g6.knowledge.knowledge_chunker import chunker
from g6.knowledge.knowledge_chunker.block import SENSITIVE_OPS, CitedSpan, KnowledgeChunkerBlock, KnowledgeChunkerInput
from g6.knowledge.knowledge_chunker.chunker import ChunkError, chunk, found_at, span_problems, spans

__g6_component__ = True
__g6_block__ = "KnowledgeChunkerBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # nothing reaches the network, writes or runs
__g6_requires__: tuple[str, ...] = ("grounding",)  # through g6.knowledge._types (its passages and tripwire)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("provenance_missing",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "ChunkError", "CitedSpan", "KnowledgeChunkerBlock", "KnowledgeChunkerInput", "chunk",
           "chunker", "found_at", "span_problems", "spans"]