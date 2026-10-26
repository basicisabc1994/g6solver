---
name: g6-ctx-colbert
description: Use when an agent must rank a set of passages it holds by meaning rather than shared words (a question worded differently from the text that answers it), and must know which method ranked them, so word matching never stands in silently for meaning, model weights are never downloaded behind its back, and nothing from one call's passages is kept for the next.
---

# G6 ColBERT ranking

Ranks the passages you give in one call for a query. ColBERT's late interaction ranks them when it is installed and
its weights are already on this machine; otherwise sentence embeddings; otherwise BM25. The reply names the method
that ranked and every one passed over, with why. With `requires_dense`, it refuses rather than fall back to BM25.

## When to use it

- The query and the text that answers it share few words ("how do I get my money back" and "refunds are issued
  within 14 days"): a dense rung finds what word matching misses.
- You hold the passages already (fetched and cut by `knowledge_chunker`) and want them ranked for one question.
- You must know whether meaning or words ranked them: `rung` says, and `requires_dense` makes word matching a
  refusal rather than a quiet stand-in.

## When not to use it

- To keep passages searchable between calls: `knowledge_indexer` keeps them under a budget.
- For an Elasticsearch server you run: `ctx_elastic`.
- For a corpus larger than one call carries (2,000 passages, 5 million characters): index it instead.
- To decide whether a hit is true: `g6.grounding` checks claims against it.

## Ops

- `search`: search(query, passages or texts, top_k, rung, requires_dense): rank this call's passages by ColBERT, else sentence embeddings, else BM25, naming each rung passed over and why; requires_dense refuses rather than fall back to BM25; nothing is kept between calls.
- `rungs`: rungs(): which rungs can run, the extra each needs, its score scale, and the weights it uses with their licence and how to get them; nothing is downloaded.

## Reading the result

- `G6_E_KNOWLEDGE_RANKED` (`qualified-draft`): the rung asked for ranked them (`rung`, `score_scale`, `hits` with
  `rank`, `score` and the passage with its source and offsets). `G6_E_KNOWLEDGE_RANKED_FALLBACK`: with `rung`
  `best`, a lower rung ranked them; `skipped` lists each rung passed over and why. `G6_E_KNOWLEDGE_RANK_NO_MATCH`:
  BM25 ranked and no passage holds a word of the query.
- Scores: `colbert_maxsim` (each query token's best cosine with any passage token, summed), `cosine` (-1 to 1) or
  `bm25`; each is comparable only within one search. A dense rung always ranks every passage, so its top hit can
  still be irrelevant.
- Refusals (`blocked-escalated`): `G6_E_KNOWLEDGE_DENSE_REQUIRED`, `G6_E_KNOWLEDGE_RUNG_UNAVAILABLE` (not
  installed, weights not cached, or no such rung), `G6_E_KNOWLEDGE_RUNG_FAILED`, `G6_E_KNOWLEDGE_NO_PASSAGES`,
  `G6_E_KNOWLEDGE_CORPUS_TOO_LARGE`, `G6_E_KNOWLEDGE_RANK_INPUT`.

## Alone and composed

Over MCP: `g6_invoke("ctx_colbert", "search", {"query": "how do I get my money back", "passages": [...],
"requires_dense": true})`, or `{"texts": {"policy.md": "..."}}` for plain texts by label. `rung` is `best`,
`colbert`, `embeddings`, `bm25`, or a rung you registered.

From Python: `from g6.knowledge.ctx_colbert import rank, RUNGS, ColbertIndex`. `rank(query, passages, rung=...,
requires_dense=...)` returns a `Ranked` (`rung`, `skipped`, `searched`). Register a rung with `RUNGS.register(...)`
(a `name`, `score_scale`, `dense`, `extra`, `ready()` and `score(query, texts)`). `ColbertIndex` is the `colbert`
backend of `g6.knowledge._index.index_backends`: a `g6.grounding` retriever over passages held in memory by one
object.

Composed: `knowledge_chunker` cuts the passages; this ranks them; `g6.grounding` checks a claim against the top hits;
`knowledge_manifest` records the cited one.

## Failure modes it catches

- `under_retrieval` (relevant passages that retrieval never finds): a dense rung ranks by meaning, so a paraphrase
  is found; when none can run, `requires_dense` refuses instead of letting BM25 miss it, and without it the reply
  names the fallback and says BM25 misses paraphrase.

## Blind spots

- A dense rung ranks every passage, relevant or not; there is no threshold, and the scores are not probabilities.
- ColBERT reads about 180 tokens of a passage, all-MiniLM-L6-v2 256: cut long documents into passages first.
- Retrieval quality is not measured here: there is no held-out set with relevance judgements, and the tests use
  encoders written for them, not the real models.
- One call's corpus only: a passage not given is never found.

## What it needs

Nothing for BM25. Both extras bring torch, which on Linux installs NVIDIA's CUDA libraries with it. For ColBERT:
`pip install "g6[colbert] @ git+https://github.com/basicisabc1994/g6solver"` (PyLate, MIT) and the
`colbert-ir/colbertv2.0` weights in your Hugging Face cache (about 440 MB; MIT by its model card; the terms of its
training data are not established). For sentence embeddings: `pip install "g6[embeddings] @ git+https://github.com/basicisabc1994/g6solver"` (sentence-transformers,
Apache-2.0) and `sentence-transformers/all-MiniLM-L6-v2` in that cache (about 90 MB; Apache-2.0 by its model card;
the terms of its training data are not established). G6 never downloads weights: get them yourself with
`hf download <repo>`. It loads weights only in safetensors form: a pickle (`pytorch_model.bin`) runs code when it is
loaded, so a cache holding only one is not ready, and the reply says how to get a safetensors copy. No network and no
model door: the ranking runs on this machine.

## Examples

```g6-example op=search
{"query": "store credit", "texts": {"policy/refunds": "Refunds are issued within 14 days of a return.", "policy/credit": "Store credit never expires."}, "top_k": 2}
```

```g6-example op=rungs
{}
```

```python
from g6.knowledge.ctx_colbert import rank
from g6.knowledge._types import Document, Source

texts = ["Refunds are issued within 14 days of a return.", "Store credit never expires."]
passages = [Document(text=t, source=Source.of(f"policy/{i}", t.encode())).passage(0, len(t)) for i, t in enumerate(texts)]
found = rank("how do I get my money back", passages, requires_dense=True)
if found.rung:  # a dense rung is installed, with its weights cached
    assert found.searched.score_scale in ("colbert_maxsim", "cosine")
else:  # refused rather than ranked by word matching; the reason says what is not installed or not cached
    reason = found.searched.reason
    assert found.code == "G6_E_KNOWLEDGE_DENSE_REQUIRED" and ("not installed" in reason or "never downloads" in reason)
```

## The four open problems

- **Learning signals**: each reply records the rung that ranked and every rung passed over with why. Not yet:
  nothing learns from them.
- **Evaluators the model cannot influence**: readiness is read from the installed packages and the files in the
  cache; the ladder and `requires_dense` are fixed code, and no passage's words change which rung runs.
- **Transfer by failure mode**: the refusal is bound to `under_retrieval` and applies to every rung registered,
  including your own.
- **Enough held-out cases**: Not yet: there is no frozen retrieval set with relevance judgements, so recall is not
  measured for any rung.
