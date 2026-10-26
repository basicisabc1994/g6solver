# SPDX-License-Identifier: Apache-2.0
"""Study V1: the relevance baseline's threshold, tuned on the development pairs only (VALIDATION_PLAN.md §V1).

    python validation/v1_grounding/tune_relevance.py DEV_PAIRS.csv

Rule, frozen with the study: score every pair with the baseline's own retriever (BM25 top-1 of the claim against its
source), then take the threshold among the observed scores that maximises balanced accuracy (the mean of recall on
``supported`` and on ``unsupported`` pairs); on a tie, the lowest such threshold. The baseline is given its best
dev-set setting, so a G6 win over it is not a win over a crippled baseline.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from g6.grounding import TextRetriever


def score(claim: str, source: str) -> float:
    hits = TextRetriever.from_texts({"source": source}).search(claim, 1)
    return hits[0].score if hits else 0.0


def tune(rows: list[dict]) -> dict:
    scored = [(score(r["claim"], r["source"]), r["label"] == "supported") for r in rows]
    positives = sum(1 for _, supported in scored if supported)
    negatives = len(scored) - positives
    best = None
    for threshold in sorted({s for s, _ in scored}):
        tp = sum(1 for s, supported in scored if supported and s >= threshold)
        tn = sum(1 for s, supported in scored if not supported and s < threshold)
        balanced = (tp / positives + tn / negatives) / 2
        if best is None or balanced > best["balanced_accuracy"]:
            best = {"threshold": threshold, "balanced_accuracy": round(balanced, 4)}
    return {"pairs": len(rows), "supported": positives, "unsupported": negatives, **(best or {})}


def main(argv: list[str] | None = None) -> int:
    path = Path((argv or sys.argv[1:])[0])
    print(json.dumps(tune(list(csv.DictReader(path.open(encoding="utf-8")))), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
