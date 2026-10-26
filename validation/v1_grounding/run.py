# SPDX-License-Identifier: Apache-2.0
"""Study V1 runner (docs/validation/VALIDATION_PLAN.md §V1). Reads pairs, runs the arms, reports endpoints.

    python validation/v1_grounding/run.py PAIRS.csv --out results/   [--nli]   (NLI arm uses G6_LLM_PROVIDER)

PAIRS.csv columns: pair_id, stratum, claim, source, label  (label: supported | unsupported); optional case_id
strata: quoted, paraphrased, negated, wrong_figure, near_topic

An arm that fails on a pair answers ``None``. It is counted (``errors``) and never helps the arm: on the adversarial
strata it counts as "said supported", and for recall as "not supported". Intervals: Wilson, and a cluster bootstrap
over ``case_id`` (rule 4: 10,000 resamples, fixed seed). The verdict uses the larger upper bound, because a percentile
bootstrap collapses to [0, 0] when nothing was falsely supported.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path

from g6.grounding import Passage, TextRetriever, check_claim
from g6.reasoning.compare import sign_test_p

STRATA = ("quoted", "paraphrased", "negated", "wrong_figure", "near_topic")
ADVERSARIAL = ("negated", "wrong_figure")
NLI_MAX_TOKENS = 2000    # frozen: at 200, gpt-oss:20b's reasoning truncated and changed answers on dev pairs
BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED = 10_000, 20261018


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def cluster_bootstrap(hits: dict[str, list[bool]], *, resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """95% percentile interval of the pooled rate, resampling whole clusters (``case_id``) with replacement."""
    clusters = [hits[k] for k in sorted(hits)]
    if not clusters:
        return (0.0, 1.0)
    rng, rates = random.Random(seed), []
    for _ in range(resamples):
        drawn = [rng.choice(clusters) for _ in clusters]
        total = sum(len(c) for c in drawn)
        rates.append(sum(sum(c) for c in drawn) / total if total else 0.0)
    rates.sort()
    return (rates[int(.025 * resamples)], rates[min(resamples - 1, int(.975 * resamples))])


def arm_check_claim(claim: str, source: str) -> bool:
    return check_claim(claim, [Passage(source, "source")]).status in ("quoted", "supported")


def arm_relevance(threshold: float):
    """Audit 014's criticised baseline: 'relevant enough' counts as support."""
    def run(claim: str, source: str) -> bool:
        hits = TextRetriever.from_texts({"source": source}).search(claim, 1)
        return bool(hits) and hits[0].score >= threshold
    return run


def arm_nli(llm):
    from g6.reasoning import first_json_object

    system = ('Does the SOURCE support the CLAIM? Reply with JSON {"label": "entails"|"contradicts"|"neutral"}.')

    def run(claim: str, source: str) -> bool | None:
        try:
            label = first_json_object(llm.complete(system, f"SOURCE:\n{source}\n\nCLAIM:\n{claim}",
                                                   max_tokens=NLI_MAX_TOKENS)).get("label")
        except Exception:  # noqa: BLE001 - an arm failure is an error, counted against the arm
            return None
        return None if label not in ("entails", "contradicts", "neutral") else label == "entails"
    return run


def load(path: Path) -> list[dict]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    for r in rows:
        if r["stratum"] not in STRATA or r["label"] not in ("supported", "unsupported"):
            raise SystemExit(f"bad row {r.get('pair_id')}: stratum/label")
    return rows


def evaluate(rows: list[dict], arms: dict) -> dict:
    raw = {name: {r["pair_id"]: fn(r["claim"], r["source"]) for r in rows} for name, fn in arms.items()}
    # worst case for the arm: an error is "supported" on an adversarial pair and "not supported" anywhere else
    per_pair = {name: {r["pair_id"]: (r["stratum"] in ADVERSARIAL) if said[r["pair_id"]] is None
                       else said[r["pair_id"]] for r in rows} for name, said in raw.items()}
    report: dict = {"n_pairs": len(rows), "arms": {}}
    adv = [r for r in rows if r["stratum"] in ADVERSARIAL]
    quoted = [r for r in rows if r["stratum"] == "quoted"]
    for name, said in per_pair.items():
        false_support = sum(said[r["pair_id"]] for r in adv)
        recall_q = sum(said[r["pair_id"]] for r in quoted)
        clusters: dict[str, list[bool]] = {}
        for r in adv:
            clusters.setdefault(r.get("case_id") or r["pair_id"], []).append(bool(said[r["pair_id"]]))
        strata = {}
        for s in STRATA:
            sub = [r for r in rows if r["stratum"] == s]
            strata[s] = {"n": len(sub), "said_supported": sum(said[r["pair_id"]] for r in sub),
                         "errors": sum(raw[name][r["pair_id"]] is None for r in sub)}
        report["arms"][name] = {
            "false_support_rate": round(false_support / len(adv), 4) if adv else None,
            "false_support_ci95": [round(x, 4) for x in wilson(false_support, len(adv))],
            "false_support_cluster_bootstrap_ci95": [round(x, 4) for x in cluster_bootstrap(clusters)],
            "quoted_recall": round(recall_q / len(quoted), 4) if quoted else None,
            "errors": sum(v is None for v in raw[name].values()),
            "strata": strata,
        }
    if "check_claim" in per_pair and "relevance" in per_pair:
        # paired on adversarial pairs: a "win" is check_claim correct (no support) where relevance is wrong
        wins = sum((not per_pair["check_claim"][r["pair_id"]]) and per_pair["relevance"][r["pair_id"]] for r in adv)
        losses = sum(per_pair["check_claim"][r["pair_id"]] and not per_pair["relevance"][r["pair_id"]] for r in adv)
        report["paired_vs_relevance"] = {"wins": wins, "losses": losses, "p_one_sided": round(sign_test_p(wins, losses), 6)}
        a = report["arms"]["check_claim"]
        upper = max(a["false_support_ci95"][1], a["false_support_cluster_bootstrap_ci95"][1])
        positive = (a["false_support_rate"] is not None and a["false_support_rate"] <= 0.05
                    and upper <= 0.10 and (a["quoted_recall"] or 0) >= 0.90
                    and report["paired_vs_relevance"]["p_one_sided"] < 0.05)
        report["verdict"] = "component_validated_no_transfer" if positive else "inconclusive"
    report["per_pair"] = raw   # as answered: None is an error
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--relevance-threshold", type=float, required=True, help="tuned on the development split only")
    ap.add_argument("--nli", action="store_true")
    args = ap.parse_args(argv)
    arms = {"check_claim": arm_check_claim, "relevance": arm_relevance(args.relevance_threshold)}
    if args.nli:
        from g6.core.llm import provider_from_env
        arms["nli"] = arm_nli(provider_from_env())
    report = evaluate(load(args.pairs), arms)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "v1_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_pair"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
