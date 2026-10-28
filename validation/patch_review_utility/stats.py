# SPDX-License-Identifier: Apache-2.0
"""V9's statistics, as PROTOCOL.md specifies them. Standard library only.

**Experiment 1** ("Analysis and hypotheses for Experiment 1"). The unit is the task, one patch per task, paired by
patch. Each difference gets a two-sided 90% interval from two methods, and the decision uses the larger upper limit
and the smaller lower limit of the two:

- Newcombe's hybrid score interval for a difference of paired proportions (Newcombe 1998, method 10): Wilson score
  limits for each arm's rate, combined with the phi coefficient of the paired table (zero when a margin of the table is
  empty);
- a bootstrap that resamples the tasks within each repository, 10,000 times, with percentile limits (linear
  interpolation between order statistics), seeded with the level's seed plus one.

The three hypotheses, "worse for G6" positive: H1 = G6's false acceptance rate minus arm 1's (margin 5 points), H2 =
G6's false acceptance rate minus arm 4's (5 points), H3 = arm 1's correct acceptance rate minus G6's (15 points). Each
is met when the upper limit is at most its margin, not met when the lower limit is above it, and inconclusive
otherwise; the comparisons are exact, with no tolerance. Experiment 1 is met only when all three are met, not met when
any is not met, and inconclusive otherwise. McNemar's exact test on the discordant pairs is reported for each as a
secondary result. Arm 2 is compared with G6 with no verdict in two ways: the three differences with arm 2 in G6's place,
and G6 against arm 2 directly (``DIRECT_SELF``).

Patches left out of the primary analysis (empty, not applying, or with no label because an infrastructure error
persisted) are counted, and the sensitivity analysis counts each as defective: an empty or non-applying patch as
accepted by no arm, since none reviewed it; an unlabelled patch with the dispositions the arms gave it.

**Verdicts** ("Verdicts"): the first that applies, with the label audit's one-sided 95% Clopper-Pearson upper bound.

**Experiment 2** ("Design of Experiment 2"): time per correct disposition, the median of each reviewer's ratio, a
bootstrap over reviewers, and the safety constraint.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from statistics import NormalDist, median

from patch_review_utility.dispositions import ARMS, ERROR, NOT_ACCEPTED, tally

LEVELS = ("feasibility", "confirmatory")
#: The normal quantile of a two-sided 90% interval (its upper limit is the one-sided 95% bound).
Z90 = NormalDist().inv_cdf(0.95)
RESAMPLES = 10_000
HYPOTHESES = (
    ("H1", "false acceptance against tests and continuous integration", "defective", "g6", "tests", 0.05),
    ("H2", "false acceptance against the same checks without G6", "defective", "g6", "direct_checks", 0.05),
    ("H3", "correct acceptance against tests and continuous integration", "correct", "tests", "g6", 0.15),
)
#: G6 compared with the agent's own review directly, with no verdict: H1's form (false acceptance, G6 minus arm 2) and
#: H3's form (correct acceptance, arm 2 minus G6). H2's form would compare G6 with arm 2 on false acceptance again,
#: the same difference as the first, so it is not computed.
DIRECT_SELF = (
    ("H1", "false acceptance: G6 minus the agent's own review", "defective", "g6", "self_review"),
    ("H3", "correct acceptance: the agent's own review minus G6", "correct", "self_review", "g6"),
)
LABEL_ERROR_LIMIT = 0.15
TOOL_ERROR_LIMIT = 0.10
TARGET_RATIO = 0.75
CAP_MINUTES = 45


def seed(level: str, commit: str) -> int:
    """The integer value of the first 16 hexadecimal digits of SHA-256 of ``V9:<level>:<commit>``."""
    if level not in LEVELS:
        raise ValueError(f"the level is one of {LEVELS}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise ValueError("the commit is G6's full 40-character frozen commit hash, in lower case")
    return int(hashlib.sha256(f"V9:{level}:{commit}".encode("utf-8")).hexdigest()[:16], 16)


# --- intervals and tests ------------------------------------------------------------------------------------------------

def wilson(x: int, n: int, z: float = Z90) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p, square = x / n, z * z
    scale = 1 + square / n
    centre = (p + square / (2 * n)) / scale
    half = z * math.sqrt(p * (1 - p) / n + square / (4 * n * n)) / scale
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_paired(a: int, b: int, c: int, d: int, z: float = Z90) -> tuple[float, float]:
    """The interval for p1 - p2, where p1 = (a + b) / n and p2 = (a + c) / n: ``a`` pairs where both arms accept,
    ``b`` only the first, ``c`` only the second, ``d`` neither."""
    n = a + b + c + d
    if n == 0:
        return -1.0, 1.0
    p1, p2 = (a + b) / n, (a + c) / n
    (l1, u1), (l2, u2) = wilson(a + b, n, z), wilson(a + c, n, z)
    margins = (a + b) * (c + d) * (a + c) * (b + d)
    phi = (a * d - b * c) / math.sqrt(margins) if margins else 0.0
    dl, du, el, eu = p1 - l1, u1 - p1, p2 - l2, u2 - p2
    low = (p1 - p2) - math.sqrt(max(0.0, dl * dl - 2 * phi * dl * eu + eu * eu))
    high = (p1 - p2) + math.sqrt(max(0.0, du * du - 2 * phi * du * el + el * el))
    return max(-1.0, low), min(1.0, high)


def _quantile(ordered: list[float], q: float) -> float:
    """The ``q`` quantile of sorted values, interpolating linearly between order statistics."""
    h = (len(ordered) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(ordered) - 1)
    weight = h - lo
    if weight == 0 or ordered[lo] == ordered[hi]:
        return ordered[lo]
    return ordered[lo] + weight * (ordered[hi] - ordered[lo])


def stratified_bootstrap(pairs: list[tuple[str, bool, bool]], *, seed: int,
                         resamples: int = RESAMPLES) -> tuple[float, float] | None:
    """The 90% percentile interval of mean(x) - mean(y), resampling ``(repository, x, y)`` within each repository
    (repositories in name order, each keeping its size); None when there is nothing to resample."""
    if not pairs:
        return None
    strata: dict[str, list[int]] = {}
    for repository, x, y in pairs:
        strata.setdefault(repository, []).append(int(x) - int(y))
    groups = [strata[name] for name in sorted(strata)]
    rng, n = random.Random(seed), len(pairs)
    values = sorted(sum(sum(rng.choices(group, k=len(group))) for group in groups) / n for _ in range(resamples))
    return _quantile(values, 0.05), _quantile(values, 0.95)


def mcnemar_exact(b: int, c: int) -> float:
    """The two-sided exact sign test on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def binomial_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson_upper(k: int, n: int, confidence: float = 0.95) -> float:
    """The one-sided upper bound on a rate from ``k`` events in ``n``: the rate at which seeing ``k`` or fewer has
    probability ``1 - confidence``."""
    if n <= 0 or k >= n:
        return 1.0
    low, high = k / n, 1.0
    for _ in range(200):
        middle = (low + high) / 2
        low, high = (middle, high) if binomial_cdf(k, n, middle) > 1 - confidence else (low, middle)
    return high


def decide(low: float, high: float, margin: float) -> str:
    if high <= margin:
        return "met"
    if low > margin:
        return "not met"
    return "inconclusive"


def experiment_1(results: list[str]) -> str:
    if results and all(r == "met" for r in results):
        return "met"
    return "not met" if "not met" in results else "inconclusive"


# --- Experiment 1 ---------------------------------------------------------------------------------------------------

def _accepted(row: dict, arm: str) -> bool:
    found = (row.get("arms") or {}).get(arm)
    return bool(found and found["accepted"])


def _rate(rows: list[dict], arm: str) -> dict:
    accepted = sum(_accepted(r, arm) for r in rows)
    return {"accepted": accepted, "of": len(rows), "rate": accepted / len(rows) if rows else None}


def difference(name: str, description: str, rows: list[dict], x: str, y: str, *, seed: int,
               margin: float | None = None) -> dict:
    """Rate of arm ``x`` minus rate of arm ``y`` on ``rows``, paired by patch, with both intervals; with ``margin``,
    the hypothesis's result."""
    pairs = [(_accepted(r, x), _accepted(r, y)) for r in rows]
    both = sum(ax and ay for ax, ay in pairs)
    only_x = sum(ax and not ay for ax, ay in pairs)
    only_y = sum(ay and not ax for ax, ay in pairs)
    neither = len(pairs) - both - only_x - only_y
    score = newcombe_paired(both, only_x, only_y, neither)
    boot = stratified_bootstrap([(r["repository"], _accepted(r, x), _accepted(r, y)) for r in rows], seed=seed)
    resampled = boot or (-1.0, 1.0)
    interval = [min(score[0], resampled[0]), max(score[1], resampled[1])]
    found = {"name": name, "description": description, "x": x, "y": y, "n": len(rows),
             "difference": (only_x - only_y) / len(rows) if rows else None, "newcombe": list(score),
             "bootstrap": list(boot) if boot else None, "interval": interval,
             "discordant": {"x_only": only_x, "y_only": only_y}, "mcnemar_exact_p": mcnemar_exact(only_x, only_y)}
    if margin is not None:
        found.update(margin=margin, result=decide(interval[0], interval[1], margin))
    return found


def analyse(rows: list[dict], *, seed: int) -> dict:
    """Experiment 1 at one level. ``rows``: one per task attempted, ``{task_id, repository, patch ("non_empty",
    "empty" or "not_applying"), label ("correct", "defective" or None), defect_categories, arms: {arm: disposition}}``.
    ``seed`` is the level's seed; the bootstrap uses ``seed + 1``."""
    boot_seed = seed + 1
    non_empty = [r for r in rows if r["patch"] == "non_empty"]
    classes = {label: [r for r in non_empty if r.get("label") == label] for label in ("correct", "defective")}
    rates = {arm: {"false_acceptance": _rate(classes["defective"], arm),
                   "correct_acceptance": _rate(classes["correct"], arm)} for arm in ARMS}
    hypotheses = [difference(name, text, classes[label], x, y, seed=boot_seed, margin=margin)
                  for name, text, label, x, y, margin in HYPOTHESES]
    secondary = [difference(name, text + " (the agent's own review in G6's place)", classes[label],
                            "self_review" if x == "g6" else x, "self_review" if y == "g6" else y, seed=boot_seed)
                 for name, text, label, x, y, _ in HYPOTHESES]
    direct = [difference(name, text, classes[label], x, y, seed=boot_seed) for name, text, label, x, y in DIRECT_SELF]
    repositories = sorted({r["repository"] for r in non_empty})
    per_repository = {
        repo: {arm: {"false_acceptance": _rate([r for r in classes["defective"] if r["repository"] == repo], arm),
                     "correct_acceptance": _rate([r for r in classes["correct"] if r["repository"] == repo], arm)}
               for arm in ARMS} for repo in repositories}
    detection = {}
    for category in sorted({c for r in classes["defective"] for c in r.get("defect_categories", [])}):
        members = [r for r in classes["defective"] if category in r.get("defect_categories", [])]
        detection[category] = {arm: {"detected": sum(not _accepted(r, arm) for r in members), "of": len(members)}
                               for arm in ARMS}
    correct_not_accepted = {arm: {} for arm in ARMS}
    for row in classes["correct"]:
        for arm in ARMS:
            found = row["arms"].get(arm)
            if found and found["category"] in NOT_ACCEPTED:
                correct_not_accepted[arm][found["category"]] = correct_not_accepted[arm].get(found["category"], 0) + 1
    costs = {arm: {key: round(sum(((r["arms"].get(arm) or {}).get("cost") or {}).get(key, 0) for r in non_empty), 3)
                   for key in ("seconds", "processor_seconds", "model_calls")} for arm in ARMS}
    unlabelled = [r for r in non_empty if r.get("label") not in ("correct", "defective")]
    flagged = [r for r in rows if r["patch"] != "non_empty"]
    widened = classes["defective"] + [dict(r, arms={}) for r in flagged] + unlabelled
    sensitivity = {"counted_as_defective": len(flagged) + len(unlabelled),
                   "rates": {arm: {"false_acceptance": _rate(widened, arm)} for arm in ARMS},
                   "hypotheses": [difference(name, text, widened, x, y, seed=boot_seed)
                                  for name, text, label, x, y, _ in HYPOTHESES if label == "defective"]}
    return {"counts": {"tasks": len(rows), "non_empty": len(non_empty),
                       "empty": sum(r["patch"] == "empty" for r in rows),
                       "not_applying": sum(r["patch"] == "not_applying" for r in rows),
                       "unlabelled": len(unlabelled),
                       "correct": len(classes["correct"]), "defective": len(classes["defective"])},
            "unlabelled": [r["task_id"] for r in unlabelled],
            "rates": rates, "hypotheses": hypotheses,
            "experiment_1": experiment_1([h["result"] for h in hypotheses]),
            "secondary_self_review": secondary, "secondary_g6_minus_self_review": direct,
            "per_repository": per_repository,
            "tallies": {arm: tally([r["arms"].get(arm) for r in non_empty]) for arm in ARMS},
            "correct_not_accepted": correct_not_accepted, "detection_by_category": detection, "costs": costs,
            "sensitivity_empty_as_defective": sensitivity, "bootstrap_seed": boot_seed}


def feasibility(rows: list[dict]) -> dict:
    """The three feasibility criteria (PROTOCOL.md, "Two levels")."""
    non_empty = [r for r in rows if r["patch"] == "non_empty"]
    dispositions = {}
    for arm in ARMS:
        found = [r["arms"].get(arm) for r in non_empty]
        errors = sum(1 for d in found if d and d["category"] == ERROR)
        rate = errors / len(non_empty) if non_empty else None
        dispositions[arm] = {"missing": sum(d is None for d in found), "tool_errors": errors, "tool_error_rate": rate,
                             "ok": bool(non_empty) and not any(d is None for d in found) and rate <= TOOL_ERROR_LIMIT}
    both_ways = (any(_accepted(r, "g6") for r in non_empty if r.get("label") == "correct")
                 and any(not _accepted(r, "g6") for r in non_empty if r.get("label") == "defective"))
    oracle = bool(rows) and all(r.get("oracle_controls_passed") is True for r in rows)
    criteria = {"dispositions": dispositions, "g6_both_ways": both_ways, "oracle_controls": oracle}
    ok = all(d["ok"] for d in dispositions.values()) and both_ways and oracle
    return {"criteria": criteria, "size": {"non_empty": len(non_empty), "attempted": len(rows)},
            "result": "feasible" if ok else "not feasible"}


def verdict(*, level: str, contaminated: bool, audit: dict | None, registered_size: int | None, defective: int,
            correct: int, hypotheses: list[dict]) -> dict:
    """The study's ECHA verdict: the first of the protocol's six that applies."""
    bound = (clopper_pearson_upper(audit["label_errors"], audit["audited"]) if audit else None)

    def said(name: str, **more) -> dict:
        return {"verdict": name, "label_error_bound": bound, **more}

    if contaminated:
        return said("contaminated_evaluation")
    if level != "confirmatory":
        if bound is not None and bound > LABEL_ERROR_LIMIT:
            return said("oracle_inadequate")
        return said("inconclusive", reason="a feasibility level tests no hypothesis")
    if bound is None or bound > LABEL_ERROR_LIMIT:
        return said("oracle_inadequate")
    if registered_size is None or defective < registered_size:
        return said("insufficient_failure_opportunities")
    results = {h["name"]: h["result"] for h in hypotheses}
    which = (["unsafe"] if "not met" in (results.get("H1"), results.get("H2")) else []) + (
        ["regressive"] if results.get("H3") == "not met" else [])
    if which:
        return said("unsafe_or_regressive", which=which)
    if correct < registered_size:
        return said("inconclusive", reason="fewer non-empty correct patches than the registered size")
    if experiment_1(list(results.values())) == "met":
        return said("component_validated_no_transfer")
    return said("inconclusive")


# --- Experiment 2 -----------------------------------------------------------------------------------------------------

def _percentiles(values: list[float], *, seed: int, resamples: int, statistic) -> list[float]:
    rng = random.Random(seed)
    found = sorted(statistic(rng.choices(values, k=len(values))) for _ in range(resamples))
    return [_quantile(found, 0.05), _quantile(found, 0.95)]


def experiment_2(reviews: list[dict], *, seed: int, resamples: int = RESAMPLES) -> dict:
    """``reviews``: ``{reviewer, method ("g6" or "tests"), patch_id, label, decision ("merge", "do_not_merge", or None
    when no decision was recorded by the cap), minutes}``."""
    at_cap = 0
    by: dict[str, dict[str, dict]] = {}
    for review in reviews:
        decision, minutes = review["decision"], review["minutes"]
        if decision is None:
            at_cap += 1
            decision, minutes = "do_not_merge", CAP_MINUTES
        merged = decision == "merge"
        totals = by.setdefault(review["reviewer"], {}).setdefault(
            review["method"], {"minutes": 0.0, "correct": 0, "merged_defective": 0})
        totals["minutes"] += minutes
        totals["correct"] += merged == (review["label"] == "correct")
        totals["merged_defective"] += merged and review["label"] == "defective"
    ratios, left_out = {}, []
    for reviewer in sorted(by):
        if set(by[reviewer]) != {"g6", "tests"}:
            left_out.append(reviewer)
            continue
        effort = {m: (t["minutes"] / t["correct"] if t["correct"] else math.inf) for m, t in by[reviewer].items()}
        if math.isinf(effort["g6"]) and math.isinf(effort["tests"]):
            left_out.append(reviewer)
            continue
        ratios[reviewer] = effort["g6"] / effort["tests"]
    merged = {m: sum(t.get(m, {}).get("merged_defective", 0) for t in by.values()) for m in ("g6", "tests")}
    differences = [t["g6"]["merged_defective"] - t["tests"]["merged_defective"] for t in by.values()
                   if set(t) == {"g6", "tests"}]
    safety = {"holds": merged["g6"] <= merged["tests"], "merged_defective": merged,
              "per_reviewer_difference": {
                  "mean": sum(differences) / len(differences) if differences else None,
                  "interval": _percentiles(differences, seed=seed, resamples=resamples,
                                           statistic=lambda s: sum(s) / len(s)) if differences else None}}
    values = list(ratios.values())
    if not values:
        return {"ratios": ratios, "left_out": left_out, "decisions_at_cap": at_cap, "median_ratio": None,
                "interval": None, "safety": safety, "target": TARGET_RATIO, "result": "inconclusive"}
    low, high = _percentiles(values, seed=seed, resamples=resamples, statistic=median)
    if high <= TARGET_RATIO and safety["holds"]:
        result = "met"
    elif low > TARGET_RATIO or not safety["holds"]:
        result = "not met"
    else:
        result = "inconclusive"
    return {"ratios": ratios, "left_out": left_out, "decisions_at_cap": at_cap, "median_ratio": median(values),
            "interval": [low, high], "safety": safety, "target": TARGET_RATIO, "result": result}
