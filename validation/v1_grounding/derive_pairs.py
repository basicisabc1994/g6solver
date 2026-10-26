# SPDX-License-Identifier: Apache-2.0
"""Study V1 population (VALIDATION_PLAN.md §0.1): VitaminC pairs assigned to the five strata by a frozen rule.

    python validation/v1_grounding/derive_pairs.py VITAMINC.jsonl --out PAIRS.csv [--seed 20261018] [--per-stratum 100]

The dataset's authors wrote every claim and evidence passage and set every label; this script only assigns a
stratum and samples. It is developed on the ``dev`` split and frozen (its SHA-256 in ``docs/validation/REGISTRY.md``)
before the ``test`` split is read. Rule, in order (the first that applies wins):

- ``near_topic``: label NOT ENOUGH INFO (evidence from the same article that does not settle the claim);
- ``negated``: label REFUTES, the claim contains a negation word, the evidence contains none, and the claim has no
  number the evidence lacks (on ``dev``, "either side negated" caught long passages' incidental negations and
  figure contrasts, so it was narrowed before the ``test`` split was read);
- ``wrong_figure``: label REFUTES, both contain a number, and the claim has a number the evidence lacks;
- ``quoted``: label SUPPORTS, and at least 90% of the claim's word tokens occur in the evidence;
- ``paraphrased``: label SUPPORTS otherwise.

Other REFUTES pairs are in no stratum. Within a stratum at most one pair per ``case_id`` is kept (the first by
``unique_id``), then up to ``--per-stratum`` pairs are drawn with ``random.Random(seed)``; a smaller stratum is used
whole and its size reported. Label: SUPPORTS is ``supported``; everything else ``unsupported``.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

STRATA = ("quoted", "paraphrased", "negated", "wrong_figure", "near_topic")
QUOTED_OVERLAP = 0.9
NEGATIONS = frozenset({"not", "never", "no", "none", "nobody", "nothing", "neither", "nor", "cannot", "without"})
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
#: VitaminC's tokenised contractions: "was n't", "isn ’ t", "don't".
_CONTRACTED = re.compile(r"n\s*['’]\s*t\b")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def negated(text: str) -> bool:
    return any(w in NEGATIONS for w in words(text)) or bool(_CONTRACTED.search(text.lower()))


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def stratum(row: dict) -> str | None:
    label, claim, evidence = row["label"], row["claim"], row["evidence"]
    if label == "NOT ENOUGH INFO":
        return "near_topic"
    if label == "REFUTES":
        new_figure = bool(numbers(claim) - numbers(evidence))
        if negated(claim) and not negated(evidence) and not new_figure:
            return "negated"
        if numbers(claim) and numbers(evidence) and new_figure:
            return "wrong_figure"
        return None
    if label == "SUPPORTS":
        tokens, present = words(claim), set(words(evidence))
        share = sum(t in present for t in tokens) / len(tokens) if tokens else 0.0
        return "quoted" if share >= QUOTED_OVERLAP else "paraphrased"
    return None


def derive(rows: list[dict], *, seed: int, per_stratum: int) -> tuple[list[dict], dict]:
    pools: dict[str, dict[str, dict]] = {s: {} for s in STRATA}
    for row in sorted(rows, key=lambda r: r["unique_id"]):
        found = stratum(row)
        if found is not None:
            pools[found].setdefault(row["case_id"], row)
    rng = random.Random(seed)
    chosen, sizes = [], {}
    for s in STRATA:
        pool = list(pools[s].values())
        picked = pool if len(pool) <= per_stratum else rng.sample(pool, per_stratum)
        sizes[s] = {"qualifying_cases": len(pool), "drawn": len(picked)}
        chosen += [{"pair_id": r["unique_id"], "stratum": s, "claim": r["claim"], "source": r["evidence"],
                    "label": "supported" if r["label"] == "SUPPORTS" else "unsupported",
                    "revision_type": r.get("revision_type", ""), "case_id": r["case_id"]} for r in picked]
    return chosen, sizes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("vitaminc", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--per-stratum", type=int, default=100)
    args = ap.parse_args(argv)
    rows = [json.loads(line) for line in args.vitaminc.open(encoding="utf-8") if line.strip()]
    chosen, sizes = derive(rows, seed=args.seed, per_stratum=args.per_stratum)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(chosen[0]))
        writer.writeheader()
        writer.writerows(chosen)
    print(json.dumps({"rows_read": len(rows), "seed": args.seed, "strata": sizes,
                      "revision_types": Counter((c["stratum"], c["revision_type"]) for c in chosen).most_common()},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
