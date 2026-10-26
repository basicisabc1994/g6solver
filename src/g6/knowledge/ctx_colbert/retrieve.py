# SPDX-License-Identifier: Apache-2.0
"""Ranking one call's passages down the ladder of rungs, and the in-memory ``colbert`` index backend.

:func:`rank` ranks the passages given, and only those: nothing derived from one call's passages (their vectors, a
vocabulary) outlives it. With ``rung="best"`` it tries ``colbert``, then ``embeddings``, then ``bm25``, and the result
names the rung that ranked and every rung passed over with why (not installed, weights not cached, it failed), so a
fallback is never silent. ``requires_dense`` passes over the lexical rung: when no dense rung can run, the search is
refused rather than answered by BM25, which misses paraphrase. A named rung runs alone, or is refused. Each hit is
the family's passage with its source and its offsets, scored on the rung's own scale.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from g6.knowledge._index import Added, IndexBackend, Removed, Retrieves, Searched, index_backends, ranked
from g6.knowledge._types import Passage
from g6.knowledge.ctx_colbert.rungs import LADDER, RUNGS, Rungs, finite

DENSE_REQUIRED = "G6_E_KNOWLEDGE_DENSE_REQUIRED"
RUNG_UNAVAILABLE = "G6_E_KNOWLEDGE_RUNG_UNAVAILABLE"
RUNG_FAILED = "G6_E_KNOWLEDGE_RUNG_FAILED"


@dataclass(frozen=True)
class Ranked:
    """What :func:`rank` found: the search, the rung that ranked (``""`` when none could), every rung passed over and
    why, and the refusal's code when none ranked."""

    searched: Searched
    rung: str
    skipped: tuple[dict[str, str], ...]
    code: str = ""


def rank(query: str, passages: Sequence[Passage], *, rung: str = "best", requires_dense: bool = False,
         top_k: int = 10, rungs: Rungs = RUNGS) -> Ranked:
    """``passages`` ranked for ``query``; see the module docstring."""
    names = LADDER if rung == "best" else (rung,)
    skipped: list[dict[str, str]] = []
    said = {"backend": "colbert", "query": query, "searched": len(passages)}
    for name in names:
        found = rungs.get(name)
        if found is None:
            skipped.append({"rung": name, "why": "no rung of that name is registered"})
            continue
        if requires_dense and not found.dense:
            skipped.append({"rung": name, "why": "it is lexical, and dense retrieval is required"})
            continue
        why = found.ready()
        if why:
            skipped.append({"rung": name, "why": why})
            continue
        try:
            scores = found.score(query, [p.text for p in passages])
            if len(scores) != len(passages) or not finite(scores):
                raise ValueError(f"it gave {len(scores)} scores for {len(passages)} passages, or a score that is "
                                 "not a finite number")
        except Exception as exc:  # noqa: BLE001 - a rung that breaks is named and passed over, never hidden
            skipped.append({"rung": name, "why": f"it failed: {type(exc).__name__}: {exc}"[:300]})
            if rung != "best":
                return Ranked(Searched(**said, outcome="failed", score_scale=found.score_scale,
                                       reason=skipped[-1]["why"], evidence={"skipped": skipped}),
                              "", tuple(skipped), RUNG_FAILED)
            continue
        pairs = list(zip(passages, scores))
        scored = pairs if found.dense else [(p, s) for p, s in pairs if s > 0]
        hits = ranked(scored, found.score_scale, top_k)
        reason = "" if hits else "no passage holds a word of the query"
        return Ranked(Searched(**said, outcome="ok" if hits else "empty", score_scale=found.score_scale, hits=hits,
                               reason=reason, evidence={"rung": name, "skipped": skipped}),
                      name, tuple(skipped))
    code = DENSE_REQUIRED if requires_dense else RUNG_UNAVAILABLE
    reason = "; ".join(f"{s['rung']}: {s['why']}" for s in skipped) or "no rung was tried"
    scale = getattr(rungs.get(names[0]), "score_scale", "colbert_maxsim")
    return Ranked(Searched(**said, outcome="unavailable", score_scale=scale, reason=reason,
                           evidence={"skipped": skipped}), "", tuple(skipped), code)


class ColbertIndex(Retrieves):
    """Passages held in memory by this one object, ranked by :func:`rank`: a ``g6.grounding`` retriever over one
    caller's corpus. Nothing is shared between objects and nothing is kept on disk."""

    backend = "colbert"
    score_scale = "colbert_maxsim"

    def __init__(self, *, rung: str = "best", requires_dense: bool = False, rungs: Rungs = RUNGS, **_: Any) -> None:
        self.rung, self.requires_dense, self.rungs = rung, requires_dense, rungs
        self._passages: dict[str, Passage] = {}
        self.last: Ranked | None = None

    def add(self, passages: Sequence[Passage]) -> Added:
        new = [p for p in passages if p.id not in self._passages]
        self._passages.update({p.id: p for p in new})
        return Added(added=len(new), known=len(passages) - len(new), evidence={"held": len(self._passages)})

    def find(self, query: str, top_k: int = 10) -> Searched:
        if not self._passages:
            return Searched(backend="colbert", query=query, outcome="empty", score_scale=self.score_scale, searched=0,
                            reason="this index holds no passages")
        self.last = rank(query, list(self._passages.values()), rung=self.rung, requires_dense=self.requires_dense,
                         top_k=top_k, rungs=self.rungs)
        return self.last.searched

    def remove(self, ids: Sequence[str]) -> Removed:
        removed = [i for i in dict.fromkeys(ids) if self._passages.pop(i, None) is not None]
        return Removed(removed=removed, missing=[i for i in dict.fromkeys(ids) if i not in removed])

    def status(self) -> dict[str, Any]:
        return {"backend": "colbert", "passages": len(self._passages), "rung": self.rung,
                "requires_dense": self.requires_dense, "rungs": self.rungs.describe()}


BACKEND = IndexBackend(name="colbert", summary="one caller's passages in memory, ranked by ColBERT, then sentence "
                       "embeddings, then BM25, each fallback named; nothing is kept", score_scale="colbert_maxsim",
                       open=ColbertIndex, persistent=False)
index_backends.register(BACKEND, replace=True)

__all__ = ["BACKEND", "DENSE_REQUIRED", "RUNG_FAILED", "RUNG_UNAVAILABLE", "ColbertIndex", "Ranked", "rank"]
