# SPDX-License-Identifier: Apache-2.0
"""The persistent-meaning spike's fixed workload and its candidates (protocol 05).

The corpus and the queries are generated from a fixed vocabulary with a fixed seed. A candidate says whether it can
run here (``ready``: the library is importable and the weights are in the local cache) without loading anything, and
a candidate that cannot run is reported ``unavailable`` with the reason, never as a zero. Candidates c and d run on
G6's own rungs (``g6.knowledge.ctx_colbert``). The adapters for a (PyLate's own index) and b (a PLAID build) are
written with the run that needs them, on the machine that has the libraries: until then they report ``unavailable``.
"""
from __future__ import annotations

import importlib.util
import random
from collections.abc import Callable
from dataclasses import dataclass

SIZES = (2_000, 20_000, 100_000)
PLATFORMS = ("windows", "wsl", "linux")
MEASURES = ("cold_p50_s", "cold_p95_s", "warm_p50_s", "warm_p95_s", "peak_rss_bytes", "disk_bytes", "recall_at_10",
            "ingest_per_s", "update_per_s", "delete_per_s", "re_encoded_after_restart", "partial_after_kill")
WORDS_PER_PASSAGE = 200
QUERIES = 200
_COMMON = ("the service keeps a record of each request and its reply so that a later reader can follow what happened "
           "when a deployment changes the limit the team reviews the change before it ships").split()


def corpus(size: int, seed: int = 12) -> list[str]:
    """``size`` passages of 200 words; each holds four words that occur in no other passage."""
    rng = random.Random(seed)
    out = []
    for n in range(size):
        words = [rng.choice(_COMMON) for _ in range(WORDS_PER_PASSAGE - 4)]
        for k in range(4):
            words.insert(rng.randrange(len(words)), f"w{n}x{k}")
        out.append(" ".join(words))
    return out


def queries(size: int, seed: int = 13, count: int = QUERIES) -> list[tuple[str, int]]:
    """``count`` queries, each with the index of its one eligible passage."""
    rng = random.Random(seed)
    picks = rng.sample(range(size), min(count, size))
    return [(f"w{n}x{rng.randrange(4)} w{n}x{rng.randrange(4)} {rng.choice(_COMMON)} {rng.choice(_COMMON)}", n)
            for n in picks]


def _missing(*modules: str) -> str:
    absent = [m for m in modules if importlib.util.find_spec(m) is None]
    return f"not installed: {', '.join(absent)}" if absent else ""


def _rung_ready(name: str) -> str:
    try:
        from g6.knowledge.ctx_colbert import rungs
    except ImportError as exc:
        return f"g6.knowledge.ctx_colbert is not importable ({type(exc).__name__})"
    rung = {"colbert": rungs.ColbertRung, "embeddings": rungs.EmbeddingsRung}[name]()
    return rung.ready()


@dataclass(frozen=True)
class Candidate:
    id: str
    summary: str
    ready: Callable[[], str]
    adapter: str      # "g6" when run.py can run it on G6's rungs; "owed" when its adapter is written with the run


CANDIDATES = {
    "a": Candidate("a", "PyLate's own index", lambda: _missing("pylate") or "the adapter is owed with the run", "owed"),
    "b": Candidate("b", "a PLAID build", lambda: _missing("pylate") or "the adapter is owed with the run", "owed"),
    "c": Candidate("c", "FTS5 candidates reranked by ColBERT, token vectors stored per passage id",
                   lambda: _rung_ready("colbert"), "g6"),
    "d": Candidate("d", "FTS5 with cached sentence embeddings", lambda: _rung_ready("embeddings"), "g6"),
}