# SPDX-License-Identifier: Apache-2.0
"""g6.knowledge.ctx_colbert — rank one call's passages by meaning, say which method ranked them, and refuse word matching when meaning is required.

An extension in the knowledge family. ``search`` ranks the passages given in the call for a query: by ColBERT late
interaction (PyLate, extra ``colbert``) when it is installed and its weights are already in the person's Hugging Face
cache, else by sentence embeddings (sentence-transformers, extra ``embeddings``), else by ``g6.grounding``'s BM25; the
reply names the rung that ranked and every rung passed over with why. ``requires_dense`` refuses rather than answer
with BM25, which misses paraphrase. G6 never downloads model weights: a rung whose weights are not cached says how
to get them, and weights only in pickle form are refused (G6 loads safetensors only). Nothing is kept between calls. Each hit is the family's passage with its source and offsets, on the
rung's own score scale (``colbert_maxsim``, ``cosine`` or ``bm25``).

    from g6.knowledge.ctx_colbert import rank
    found = rank("how do I get my money back", passages, requires_dense=True)
    found.rung, found.skipped, found.searched.hits

The ranking is also the ``colbert`` backend of the family's index registry: an index held in memory by one object.

Not ported from the archive's ctx_colbert, and why: the litellm embedding rung; the model re-ranker that was on by
default and sent passage snippets to a model; ragatouille, which pulls llama-index and langchain; the weights it
downloaded on first use; the TF-IDF rung (BM25 is the floor); results that dropped their documents' ids and one score
field for numbers on different scales; the 28-op MCP block and its namespace store; the production gate and the
unused command-line door.
"""
from g6.knowledge.ctx_colbert import retrieve, rungs
from g6.knowledge.ctx_colbert.block import SENSITIVE_OPS, CtxColbertBlock, CtxColbertInput
from g6.knowledge.ctx_colbert.retrieve import ColbertIndex, Ranked, rank
from g6.knowledge.ctx_colbert.rungs import COLBERT_WEIGHTS, MINILM_WEIGHTS, RUNGS, Rung, Weights

__g6_component__ = True
__g6_block__ = "CtxColbertBlock"
__g6_family__ = "knowledge"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # it ranks on this machine what the call gives; nothing is fetched
__g6_requires__: tuple[str, ...] = ("grounding",)  # its BM25 rung, and through g6.knowledge._types
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("under_retrieval",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
# Heavy and optional; without them the BM25 rung ranks and every reply says what is not installed. The weights are the
# person's own download (colbertv2.0, MIT by its model card; all-MiniLM-L6-v2, Apache-2.0 by its model card).
__g6_extras__: dict[str, tuple[str, ...]] = {"colbert": ("pylate", "huggingface_hub", "numpy"),
                                             "embeddings": ("sentence_transformers", "huggingface_hub", "numpy")}
__g6_index_backends__: tuple[str, ...] = ("colbert",)  # the family's registry imports this package to find it

__all__ = ["COLBERT_WEIGHTS", "MINILM_WEIGHTS", "RUNGS", "SENSITIVE_OPS", "ColbertIndex", "CtxColbertBlock",
           "CtxColbertInput", "Ranked", "Rung", "Weights", "rank", "retrieve", "rungs"]
