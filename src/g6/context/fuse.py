# SPDX-License-Identifier: Apache-2.0
"""Reciprocal-rank fusion, near-duplicate provenance and the existing budget floor."""
import re
from dataclasses import dataclass, field

from g6.context.budget import select_within_budget
from g6.context.packet import ContextItem

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NEGATION = re.compile(r"\b(?:not|no|never|none|neither|nor|cannot|without)\b|n['’]t\b")


@dataclass
class _Ranked:
    item: ContextItem
    score: float = 0.0
    sources: list[str] = field(default_factory=list)


def fuse(ranked_lists: list[list[ContextItem]], budget_tokens: int) -> list[ContextItem]:
    """Fuse ranks (1 / (60 + rank)), never raw scores, then deduplicate and budget.

    The rank offset 60 and word-set overlap > 0.7 are declared heuristics, not
    measured relevance. Identity includes source and text, so changed text is never
    silently combined under a reused key. Near-duplicates merge only when they state
    the same numbers and the same negation, so two sources that disagree both stay.
    Inputs are retrieval data, never authority. Candidates of which none fits the
    budget are refused with ``G6_E_CONTEXT_FUSION_BUDGET``, never an empty success.
    """
    if type(budget_tokens) is not int or budget_tokens < 0 or sum(map(len, ranked_lists)) > 2000:
        raise ValueError("[G6_E_CONTEXT_FUSION_INPUT] nonnegative budget and at most 2000 candidates required")
    ranked: dict[tuple[str, str], _Ranked] = {}
    for listing in ranked_lists:
        seen = set()
        for rank, item in enumerate(listing, 1):
            identity = (item.source, item.value)
            if identity in seen:
                continue
            seen.add(identity)
            ranked.setdefault(identity, _Ranked(item)).score += 1 / (60 + rank)
    ordered = sorted(ranked.values(), key=lambda row: -row.score)
    kept: list[tuple[_Ranked, set[str], tuple[frozenset[str], bool]]] = []
    for row in ordered:
        text = row.item.value.casefold()
        words = set(re.findall(r"\w+", text))
        facts = (frozenset(_NUMBER.findall(text)), _NEGATION.search(text) is not None)
        duplicate = next((other for other, terms, said in kept if row.item.value == other.item.value or
                          said == facts and words and terms and len(words & terms) / len(words | terms) > 0.7), None)
        if duplicate is not None:
            duplicate.sources.append(row.item.source)
        else:
            kept.append((row, words, facts))
    items = [ContextItem.model_validate({**row.item.model_dump(), "kind": "retrieved_excerpt", "trust": "untrusted",
             "authority": "operational", "metadata": {**row.item.metadata, "fusion_method": "reciprocal_rank",
             "rank_offset": 60, "fusion_score": row.score, "dropped_sources": row.sources}}) for row, _, _ in kept]
    selected = select_within_budget(items, budget_tokens)
    if items and not selected:
        raise ValueError("[G6_E_CONTEXT_FUSION_BUDGET] the top-ranked passage alone exceeds the budget; increase it")
    return selected
