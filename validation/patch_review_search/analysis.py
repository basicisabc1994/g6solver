# SPDX-License-Identifier: Apache-2.0
"""V10's analysis of its primary measures (docs/validation/patch_review_search/PROTOCOL.md: "Measures", "Analysis",
"The level: feasibility only" and "What the results permit saying").

**Nothing is computed from a record that does not verify** (``record.verify``): its chain must hold from the first
entry to the last and end at the hash committed before any label was read. Only then are the labels read.

**Inputs.** The driver's record (each arm's output on each patch, the excluded patches, and the reruns before the
labels); V9's results (``v9``: its seed and its tasks, each with its repository, its patch state, its label, the
oracle controls and V9's arms, arm 3 being this study's arm A); and the reruns on the reference fix, after the labels
(``reference``).

**The patches** are V9's non-empty, labelled tasks, paired across every arm by task. An arm with no disposition on a
patch does not accept it, and is counted (``missing``). Unlabelled patches and those the record excludes are counted
and left out of every rate.

**Primary measures**, per arm (A, C, T, E), each also per repository:

1. false acceptance: defective patches accepted ÷ non-empty defective patches;
2. correct acceptance: correct patches accepted ÷ non-empty correct patches;
3. actionable defect discovery: defective patches with at least one actionable finding (a reproducible finding, by
   the reruns before the labels, on a patch the label contract calls defective) ÷ non-empty defective patches, with the
   number of actionable findings and how many of them pass (compatible) or error (not comparable) on the reference
   fix, and the number of findings per patch;
4. the headline counts: M, the defective patches V9's arms 1 and 2 both accepted, and L, the correct ones; per arm K
   (patches in M with an actionable finding), K_ref (with an actionable finding compatible with the reference fix) and
   J (patches in L with a reproducible finding);
6. cost per patch, mean, median and total: model calls, seconds, processor seconds, cents and dollars.

Measure 5 (human verification effort) is Experiment 2, not here. Each rate has its exact two-sided 95% Clopper–Pearson
interval (``exact``, from V9's one-sided bound at 97.5%), and a zero denominator gives a count and no interval. The
paired differences (C and T against A, C and T against E, T against C, and C, T and E against V9's arms 1, 2 and 4, on
measures 1 to 3) each have Newcombe's hybrid score interval (method 10) at 95% and, beside it, V9's bootstrap stratified
by repository at 95% (seed: V9's feasibility seed plus two; the same seed, and so the same resampled task sets, for
every comparison on the same patches). A comparison with no discordant pair is reported, not interpreted. No hypothesis
is tested and no interval is adjusted. Also from the record: E-filtered, A-and-E, and each search arm's own-run
acceptance; and every arm's tool errors by kind.

**Not built yet**, until VS-4 and VS-5 settle their fields: template applicability (arm T), the formal path's counts
(arm T), whether surviving mutants predict defects (test adequacy, arm T), similarity to the hidden tests, the
per-reason-code shares over findings with their bootstrap, and token counts. Measure 3 for V9's arms 1 and 4 is not
computed: their findings are not rerun by V10's driver.
"""
from __future__ import annotations

import statistics
from statistics import NormalDist

from patch_review_search.record import verify
from patch_review_utility import stats
from patch_review_utility.dispositions import tally

CONFIDENCE = 0.95
Z95 = NormalDist().inv_cdf(0.5 + CONFIDENCE / 2)
ARMS = ("A", "C", "T", "E")
VARIANTS = ("E_filtered", "A_and_E", "C_own", "T_own")
#: V9's arms this study compares with, by their number in V9's protocol.
V9_ARMS = {"1": "tests", "2": "self_review", "4": "direct_checks"}
MEASURES = ("false_acceptance", "correct_acceptance", "discovery")
PAIRS = (("C", "A"), ("T", "A"), ("C", "E"), ("T", "E"), ("T", "C"),
         *((arm, v9) for arm in ("C", "T", "E") for v9 in V9_ARMS))
NOT_BUILT = ("template applicability (arm T)", "the formal path's counts (arm T)",
             "whether surviving mutants predict defects (arm T)", "similarity to the hidden tests",
             "per-reason-code shares over findings, with their bootstrap", "input and output tokens")
NO_FINDING = "the agent's own review gives no finding (PROTOCOL.md, Findings)"
NOT_RERUN = "not computed: V10's driver does not rerun this arm's findings"


def exact(count: int, of: int) -> tuple[float, float]:
    """The exact two-sided 95% Clopper–Pearson interval of ``count`` in ``of`` (``of`` above zero)."""
    tail = 0.5 + CONFIDENCE / 2
    high = stats.clopper_pearson_upper(count, of, tail)
    low = 0.0 if count == 0 else 1.0 - stats.clopper_pearson_upper(of - count, of, tail)
    return low, high


def rate(count: int, of: int) -> dict:
    return {"count": count, "of": of, "rate": count / of if of else None,
            "interval": list(exact(count, of)) if of else None}


def paired(rows: list[dict], x_of, y_of, *, seed: int, resamples: int = stats.RESAMPLES) -> dict:
    """``x_of(row)`` against ``y_of(row)`` on ``rows``, paired by row: the difference of the two rates, Newcombe's
    interval at 95%, and the bootstrap stratified by each row's ``repository`` at 95%."""
    pairs = [(row["repository"], bool(x_of(row)), bool(y_of(row))) for row in rows]
    both = sum(x and y for _, x, y in pairs)
    only_x = sum(x and not y for _, x, y in pairs)
    only_y = sum(y and not x for _, x, y in pairs)
    neither = len(pairs) - both - only_x - only_y
    boot = stats.stratified_bootstrap(pairs, seed=seed, resamples=resamples, confidence=CONFIDENCE)
    return {"n": len(pairs), "difference": (only_x - only_y) / len(pairs) if pairs else None,
            "newcombe": list(stats.newcombe_paired(both, only_x, only_y, neither, z=Z95)) if pairs else None,
            "bootstrap": list(boot) if boot else None, "discordant": {"x_only": only_x, "y_only": only_y},
            "interpreted": bool(only_x or only_y)}


def _accepts(disposition) -> bool:
    return bool(isinstance(disposition, dict) and disposition.get("accepted") is True)


def _summary(values: list) -> dict:
    found = [value for value in values if isinstance(value, (int, float))]
    if not found:
        return {"mean": None, "median": None, "total": None, "missing": len(values)}
    return {"mean": round(sum(found) / len(found), 6), "median": statistics.median(found),
            "total": round(sum(found), 6) if any(isinstance(v, float) for v in found) else sum(found),
            "missing": len(values) - len(found)}


def _patches(entries: list[dict], v9: dict, reference: list[dict]) -> tuple[list[dict], dict]:
    """The analysed patches, each with every arm's disposition, findings and cost; and the counts."""
    outputs: dict[str, dict] = {}
    findings: dict[tuple[str, str], list[dict]] = {}
    excluded = {"empty": 0, "not_applying": 0}
    for entry in entries:
        data = entry["data"]
        if entry["kind"] == "arm":
            outputs.setdefault(data["patch"], {})[data["arm"]] = data["output"]
        elif entry["kind"] == "rerun":
            findings.setdefault((data["patch"], data["arm"]), []).append(data)
        elif entry["kind"] == "excluded":
            excluded[data["reason"]] = excluded.get(data["reason"], 0) + 1
    compatible = {(item["patch"], item["arm"], item["node"]): item["result"] for item in reference}
    tasks = v9["tasks"]
    non_empty = [task for task in tasks if task.get("patch") == "non_empty"]
    labelled = [task for task in non_empty if task.get("label") in ("correct", "defective")]
    rows = []
    for task in labelled:
        found = outputs.get(task["task_id"], {})
        arms = {"A": task["arms"].get("g6"), **{arm: (found.get(arm) or {}).get("disposition") for arm in "CTE"}}
        arms.update(E_filtered=(found.get("E") or {}).get("e_filtered", arms["E"]),
                    A_and_E=(found.get("E") or {}).get("a_and_e", arms["E"]),
                    C_own=(found.get("C") or {}).get("own"), T_own=(found.get("T") or {}).get("own"),
                    **{number: task["arms"].get(name) for number, name in V9_ARMS.items()})
        reruns = {}
        for arm in ARMS:
            reruns[arm] = [{**item, "reference": compatible.get((task["task_id"], arm, item["node"]))}
                           for item in findings.get((task["task_id"], arm), [])]
        cost = {"A": {**((task["arms"].get("g6") or {}).get("cost") or {}), "cents": None}}
        for arm in "CTE":
            output = found.get(arm) or {}
            meter = output.get("meter") or {}
            cost[arm] = {"model_calls": meter.get("calls"), "seconds": output.get("seconds"),
                         "processor_seconds": output.get("processor_seconds"), "cents": meter.get("cents")}
        rows.append({"id": task["task_id"], "repository": task["repository"], "label": task["label"],
                     "accepted_by": {"1": _accepts(arms["1"]), "2": _accepts(arms["2"])},
                     "dispositions": arms, "findings": reruns, "cost": cost, "outputs": found})
    counts = {"tasks": len(tasks), "non_empty": len(non_empty), "excluded": excluded,
              "unlabelled": [task["task_id"] for task in non_empty if task not in labelled],
              "defective": sum(row["label"] == "defective" for row in rows),
              "correct": sum(row["label"] == "correct" for row in rows),
              "repositories": sorted({row["repository"] for row in rows})}
    return rows, counts


def _actionable(row: dict, arm: str) -> list[dict]:
    """The arm's reproducible findings on the patch (actionable on a defective one)."""
    return [item for item in row["findings"].get(arm, []) if (item.get("result") or {}).get("reproducible") is True]


def _discovers(row: dict, arm: str) -> bool:
    return arm != "2" and bool(_actionable(row, arm))


def _rates(rows: list[dict], name: str) -> dict:
    def two(subset):
        return {measure: rate(sum(_accepts(r["dispositions"].get(name)) for r in members), len(members))
                for measure, members in (("false_acceptance", [r for r in subset if r["label"] == "defective"]),
                                         ("correct_acceptance", [r for r in subset if r["label"] == "correct"]))}
    repositories = sorted({row["repository"] for row in rows})
    return {**two(rows), "per_repository": {repo: two([r for r in rows if r["repository"] == repo])
                                            for repo in repositories}}


def _discovery(rows: list[dict], arm: str) -> dict:
    defective = [row for row in rows if row["label"] == "defective"]
    if arm == "2":
        return {**rate(0, len(defective)), "note": NO_FINDING}
    if arm in ("1", "4"):
        return {"note": NOT_RERUN}
    actionable = [item for row in defective for item in _actionable(row, arm)]
    every = [len(row["findings"].get(arm, [])) for row in rows]
    return {**rate(sum(_discovers(row, arm) for row in defective), len(defective)),
            "actionable_findings": len(actionable),
            "compatible_with_reference": sum((item["reference"] or {}).get("compatible") is True
                                             for item in actionable),
            "not_comparable": sum((item["reference"] or {}).get("not_comparable") is True for item in actionable),
            "per_repository": {repo: rate(sum(_discovers(r, arm) for r in defective if r["repository"] == repo),
                                          sum(r["repository"] == repo for r in defective))
                               for repo in sorted({row["repository"] for row in defective})},
            "findings_per_patch": {"patches_with_findings": sum(n > 0 for n in every),
                                   "mean": round(sum(every) / len(every), 3) if every else None,
                                   "most": max(every) if every else None}}


def _headline(rows: list[dict]) -> dict:
    both = [row for row in rows if row["accepted_by"]["1"] and row["accepted_by"]["2"]]
    m = [row for row in both if row["label"] == "defective"]
    l_ = [row for row in both if row["label"] == "correct"]
    arms = {}
    for arm in ARMS:
        k = sum(_discovers(row, arm) for row in m)
        k_ref = sum(any((item["reference"] or {}).get("compatible") is True for item in _actionable(row, arm))
                    for row in m)
        j = sum(bool(_actionable(row, arm)) for row in l_)
        arms[arm] = {"K": rate(k, len(m)), "K_ref": rate(k_ref, len(m)), "J": rate(j, len(l_))}
    return {"M": len(m), "L": len(l_), "arms": arms}


def _differences(rows: list[dict], *, seed: int, resamples: int) -> list[dict]:
    found = []
    for x, y in PAIRS:
        for measure in MEASURES:
            if measure == "discovery":
                if "1" in (x, y) or "4" in (x, y):
                    continue
                members = [row for row in rows if row["label"] == "defective"]
                result = paired(members, lambda r, a=x: _discovers(r, a), lambda r, a=y: _discovers(r, a), seed=seed,
                                resamples=resamples)
            else:
                label = "defective" if measure == "false_acceptance" else "correct"
                members = [row for row in rows if row["label"] == label]
                result = paired(members, lambda r, a=x: _accepts(r["dispositions"].get(a)),
                                lambda r, a=y: _accepts(r["dispositions"].get(a)), seed=seed, resamples=resamples)
            found.append({"x": x, "y": y, "measure": measure, **result})
    return found


def _costs(rows: list[dict]) -> dict:
    found = {}
    for arm in ARMS:
        per = [row["cost"][arm] for row in rows]
        cents = [item.get("cents") for item in per]
        found[arm] = {key: _summary([item.get(key) for item in per])
                      for key in ("model_calls", "seconds", "processor_seconds")}
        found[arm]["cents"] = _summary(cents)
        found[arm]["dollars"] = _summary([value / 100 if isinstance(value, (int, float)) else None
                                          for value in cents])
    return found


def analyse(record_path, committed: str, *, v9: dict, reference: list[dict],
            resamples: int = stats.RESAMPLES) -> dict:
    """The primary measures (the module docstring), from a record that verifies against ``committed``."""
    entries = verify(record_path, committed)
    rows, counts = _patches(entries, v9, reference)
    seed = int(v9["seed"]) + 2
    return {"study": "V10", "kind": "primary measures", "confidence": CONFIDENCE,
            "record": {"committed": committed, "entries": len(entries)}, "counts": counts,
            "bootstrap_seed": seed, "resamples": resamples,
            "rates": {name: _rates(rows, name) for name in (*ARMS, *VARIANTS)},
            "missing": {name: sum(row["dispositions"].get(name) is None for row in rows) for name in (*ARMS, *VARIANTS)},
            "discovery": {arm: _discovery(rows, arm) for arm in (*ARMS, *V9_ARMS)},
            "headline": _headline(rows), "differences": _differences(rows, seed=seed, resamples=resamples),
            "costs": _costs(rows), "tool_errors": {arm: tally([row["dispositions"].get(arm) for row in rows])
                                                   for arm in ARMS},
            "not_built": list(NOT_BUILT), "rows": rows}
