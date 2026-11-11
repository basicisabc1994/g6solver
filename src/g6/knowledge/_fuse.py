# SPDX-License-Identifier: Apache-2.0
"""G6's own fusion (design 12.3): reciprocal rank fusion keyed by canonical passage id, every contribution kept.

``_``-prefixed, so it never registers as a component. Ranks are combined, never raw scores: a passage's fused weight
is the sum over the backends that returned it of ``1 / (RRF_K + rank)``, with the rank each backend declared. Ties
are broken by passage id, so the order is the same on every run. The rank constant, the budget loop and the
number and negation patterns come from ``g6.context.fuse``; its source/text identity and its word-overlap merge do
not: two different canonical passages are never merged, however alike. Passages that say nearly the same thing are
listed together in ``groups`` for presentation only, each still an item with its own citation, and a group whose
members differ in a number or in negation gets a caution that names both; a caution is not agreement and not a
contradiction finding.

Every backend's report (answered, empty, unavailable, failed, partial, unsupported, refused) passes through
unchanged. A backend whose reported rung is ``bm25`` while a lexical backend also answered is counted once: its
contributions are kept and recorded with weight zero, so a lexical fallback is never a second, independent vote.
More than :data:`MAX_CANDIDATES` candidates is refused, not truncated; so is a budget the best item alone exceeds.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from g6.knowledge._envelope import BackendReport, Contribution, PassageItem
from g6.knowledge._index import Hit

RRF_K = 60
MAX_CANDIDATES = 2_000
TOO_MANY = "G6_E_KNOWLEDGE_FUSION_CANDIDATES"
OVER_BUDGET = "G6_E_KNOWLEDGE_FUSION_BUDGET"
#: Backends whose ranking is lexical (BM25 over words).
LEXICAL = frozenset({"sqlite", "elastic_managed"})
LEADS = "leads"   # the route name of model-proposed relations (g6.knowledge._relations)
#: Word-set overlap above which two passages are shown as one presentation group. A declared heuristic.
GROUP_OVERLAP = 0.7
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NEGATION = re.compile(r"\b(?:not|no|never|none|neither|nor|cannot|without)\b|n['’]t\b")
Render = Callable[[Hit, int, tuple[Contribution, ...]], PassageItem | None]


class Fused(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[PassageItem, ...] = ()
    ranked: tuple[str, ...] = ()                       # every fused passage id, best first
    contributions: tuple[Contribution, ...] = ()       # one per (backend, passage), as each backend returned it
    backends: tuple[BackendReport, ...] = ()
    groups: tuple[tuple[str, ...], ...] = ()           # presentation only: near-duplicates, each still its own item
    cautions: tuple[dict, ...] = ()
    omitted: int = 0                                   # ranked passages left out because the budget was reached
    tokens: int = 0
    refused: str = ""


def tokens_of(item: PassageItem) -> int:
    """What ``item`` costs a model: every rendered field, its provenance and the JSON around them."""
    from g6.context.packet import estimate_tokens

    return estimate_tokens(json.dumps(item.model_dump(), sort_keys=True, default=str))


def _facts(text: str) -> tuple[frozenset[str], bool, frozenset[str]]:
    folded = text.casefold()
    return frozenset(_NUMBER.findall(folded)), _NEGATION.search(folded) is not None, frozenset(re.findall(r"\w+", folded))


def fuse(results: Sequence[tuple[BackendReport, Sequence[Hit], int]], *, top_k: int,
         budget_tokens: int | None = None, render: Render | None = None,
         query: str = "") -> Fused:
    """Fuse each backend's hits (its report, its hits in its own order, the generation they came from).

    ``render`` builds the model-bound item for a hit (the engine's renderer re-reads the passage and returns
    ``None`` for one that may no longer be used); without it only the order and the contributions are returned.
    """
    reports = tuple(report for report, _, _ in results)
    total = sum(len(hits) for _, hits, _ in results)
    if total > MAX_CANDIDATES:
        return Fused(backends=reports, refused=f"[{TOO_MANY}] {total} candidates is over the bound of "
                                               f"{MAX_CANDIDATES}; narrow the query or ask for fewer per route")
    if budget_tokens is not None and (type(budget_tokens) is not int or budget_tokens < 0):
        return Fused(backends=reports, refused=f"[{OVER_BUDGET}] the budget is a whole number of tokens, 0 or more")
    lexical_answered = any(report.backend in LEXICAL and report.state in ("answered", "partial")
                           for report, _, _ in results)
    weight: dict[str, float] = {}
    first: dict[str, Hit] = {}
    by_passage: dict[str, list[Contribution]] = {}
    contributions: list[Contribution] = []
    for report, hits, generation in results:
        fallback = report.rung == "bm25" and report.backend not in LEXICAL
        lead = report.route == LEADS   # a model's proposed relation led here: it is kept, with no evidence weight
        counted_once = (fallback and lexical_answered) or lead
        seen: set[str] = set()
        for hit in hits:
            pid = hit.passage.id
            if pid in seen:
                continue
            seen.add(pid)
            reason = ("a model-proposed lead: a way to find the passage, no evidence weight" if lead
                      else "bm25 fallback: lexical, counted once with the lexical backend" if counted_once
                      else "bm25 fallback: lexical, not semantic" if fallback else f"{report.route} match")
            made = Contribution(backend=report.backend, route=report.route, query=query, rank=hit.rank,
                                score_scale=hit.score_scale, reason=reason, passage_id=pid, generation=generation)
            contributions.append(made)
            by_passage.setdefault(pid, []).append(made)
            first.setdefault(pid, hit)
            weight[pid] = weight.get(pid, 0.0) + (0.0 if counted_once else 1.0 / (RRF_K + hit.rank))
    ranked = sorted(weight, key=lambda pid: (-weight[pid], pid))
    facts = {pid: _facts(first[pid].passage.text) for pid in ranked}
    groups: list[list[str]] = []
    for pid in ranked:
        words = facts[pid][2]
        home = next((g for g in groups if words and facts[g[0]][2]
                     and len(words & facts[g[0]][2]) / len(words | facts[g[0]][2]) > GROUP_OVERLAP), None)
        if home is None:
            groups.append([pid])
        else:
            home.append(pid)
    cautions = []
    for group in (g for g in groups if len(g) > 1):
        if len({facts[p][1] for p in group}) > 1:
            cautions.append({"kind": "negation", "passages": list(group),
                             "note": "these passages read alike and differ in negation; both are cited"})
        if len({facts[p][0] for p in group}) > 1:
            cautions.append({"kind": "number", "passages": list(group),
                             "note": "these passages read alike and state different numbers; both are cited"})
    items: list[PassageItem] = []
    spent = omitted = 0
    refused = ""
    if render is not None:
        for pid in ranked:
            if len(items) >= top_k:
                break
            item = render(first[pid], len(items) + 1, tuple(by_passage[pid]))
            if item is None:
                continue
            cost = tokens_of(item)
            if budget_tokens is not None and spent + cost > budget_tokens:
                if not items:
                    refused = (f"[{OVER_BUDGET}] the best passage alone needs {cost} tokens, over the budget of "
                               f"{budget_tokens}; raise the budget")
                omitted = len(ranked) - ranked.index(pid)
                break
            items.append(item)
            spent += cost
    return Fused(items=tuple(items), ranked=tuple(ranked), contributions=tuple(contributions), backends=reports,
                 groups=tuple(tuple(g) for g in groups if len(g) > 1), cautions=tuple(cautions), omitted=omitted,
                 tokens=spent, refused=refused)


__all__ = ["GROUP_OVERLAP", "LEXICAL", "MAX_CANDIDATES", "OVER_BUDGET", "RRF_K", "TOO_MANY", "Fused", "fuse",
           "tokens_of"]
