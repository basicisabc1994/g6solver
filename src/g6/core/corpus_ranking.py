# SPDX-License-Identifier: Apache-2.0
"""One corpus ranker for discovery and selection; optional reading never expands the candidate set."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import field_validator

from g6.core import semantic
from g6.core.discovery import _tokens

# Absolute query coverage, not a fraction of the best hit. Calibrated on development examples.
FIND_FLOOR = 0.20
MODEL_CANDIDATES = 24
_KIND_ORDER = {"tool": 0, "route": 1, "skill": 2, "op": 3}


class OrderReply(semantic.ChoicesReply):
    @field_validator("value")
    @classmethod
    def distinct(cls, value):
        if len(value) != len(set(value)) or not value:
            raise ValueError("an order needs distinct offered ids")
        return value


@dataclass
class Ranking:
    order: list[str]
    lexical: dict[str, float]
    scores: dict[str, float]
    ranked_by: str = "lexical"
    rerank_code: str = ""


def documents(data, entries):
    return {key: e.get("when") or " ".join([e["text"], e["establishes"],
                                            data["components"].get(e.get("component", ""), {}).get("text", "")])
            for key, e in entries.items()}


def _words(text):
    """Conservative English plurals and adverbs; the same rule applies to query and corpus."""
    def root(word):
        if len(word) > 5 and word.endswith("ally"):
            word = word[:-4]
        if len(word) > 4 and word.endswith("ies"):
            return word[:-3] + "y"
        if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            return word[:-1]
        return word

    return [root(word) for word in _tokens(text)]


def lexical(task: str, documents: dict[str, str], *, coverage=False) -> dict[str, float]:
    """IDF overlap. Absolute coverage includes absent words for the discovery admission floor."""
    query = Counter(_words(task))
    docs = {key: Counter(_words(text)) for key, text in documents.items()}
    df = Counter(t for doc in docs.values() for t in set(doc))
    n = max(1, len(docs))
    ideal = sum(count * math.log(1 + n / max(1, df[t])) for t, count in query.items() if coverage or df.get(t))
    return {key: score / ideal for key, doc in docs.items()
            if (score := sum(min(query[t], doc[t]) * math.log(1 + n / df[t]) for t in query if doc.get(t))) > 0}


def rank(task: str, data: dict[str, Any], *, scores=None, acting=None, model=False, floor=0.0) -> Ranking:
    """Rank the shipped corpus once; selector policy may supply its accumulated scores and outward-act demotion.

    A model may choose or reorder up to 24 floor-admitted ids. Unchosen ids retain lexical order. Failure preserves
    the complete deterministic order. Default caller resolution and consent remain entirely in semantic.read().
    """
    from g6.skills.corpus import _expanded

    entries = {entry["id"]: entry for entry in _expanded(data)}
    docs = documents(data, entries)
    overlaps = lexical(task, docs)
    covered = lexical(task, docs, coverage=True) if floor else overlaps
    values = dict(overlaps if scores is None else scores)
    values = {key: value for key, value in values.items() if covered.get(key, 0) >= floor}
    acts = acting or {}
    order = sorted(values, key=lambda key: (bool(acts.get(key)), -values[key],
                                            _KIND_ORDER[entries[key]["kind"]], key))
    result = Ranking(order, overlaps, values)
    if not model or not order:
        return result
    offered = order[:MODEL_CANDIDATES]
    reading = semantic.read(semantic.Question(
        kind="workflow_ranking", text=task, schema=OrderReply, floor=lambda: offered, combine="choose",
        candidates=tuple(semantic.Candidate(key, docs[key][:1800]) for key in offered)))
    result.rerank_code = reading.code
    if reading.by == "model":
        # Policy demotion remains authoritative even if a model reorders outward actions.
        chosen = list(reading.value)
        reordered = [*chosen, *(key for key in order if key not in chosen)]
        result.order = sorted(reordered, key=lambda key: bool(acts.get(key)))
        result.ranked_by = "model"
    return result
