# SPDX-License-Identifier: Apache-2.0
"""V10's analysis of its primary measures (docs/validation/patch_review_search/PROTOCOL.md, version 2: "Measures",
"Analysis", "Verdict", "The level: feasibility only" and "What the results permit saying").

**Nothing is computed from a record that does not verify** (``record.verify``): its chain must hold from the first
entry to the last and end at the hash committed before any label was read. Only then are the labels read.

**Inputs.** The driver's record (each arm's output on each patch, the excluded patches, and the reruns before the
labels); V9's results (``v9``: its seed and its tasks, each with its repository, its patch state, its label, the
oracle controls and V9's arms, arm 3 being this study's arm A); and the reruns on the reference fix, after the labels
(``reference``).

**The patches** are V9's non-empty, labelled tasks, paired across every arm by task. An arm with no disposition on a
patch does not accept it, and is counted (``missing``). Unlabelled patches and those the record excludes are counted
and left out of every rate.

**Primary measures**, per arm (A, C, T, E, R), each also per repository:

1. false acceptance: defective patches accepted ÷ non-empty defective patches;
2. correct acceptance: correct patches accepted ÷ non-empty correct patches;
3. actionable defect discovery: defective patches with at least one actionable finding (a reproducible finding, by
   the reruns before the labels, on a patch the label contract calls defective) ÷ non-empty defective patches, with the
   number of actionable findings and how many of them pass (compatible) or error (not comparable) on the reference
   fix, and the number of findings per patch;
4. the headline counts: M, the defective patches V9's arms 1 and 2 both accepted, and L, the correct ones; per arm K
   (patches in M with an actionable finding), K_ref (with an actionable finding compatible with the reference fix) and
   J (patches in L with a reproducible finding); beside K_R, the note that M is chosen by V9's arm 2 (``K_R_NOTE``);
6. cost per patch, mean, median and total: model calls, seconds, processor seconds, cents and dollars.

Measure 5 (human verification effort) is Experiment 2, not here. Each rate has its exact two-sided 95% Clopper–Pearson
interval (``exact``, from V9's one-sided bound at 97.5%), and a zero denominator gives a count and no interval. The
paired differences (``PAIRS``: C and T against A, C and T against E, T against C, A, C, T and E against R, and C, T, E
and R against V9's arms 1, 2 and 4, on measures 1 to 3) each have Newcombe's hybrid score interval (method 10) at 95%
and, beside it, V9's bootstrap stratified by repository at 95% (seed: V9's feasibility seed plus two; the same seed,
and so the same resampled task sets, for every comparison on the same patches). A comparison with no discordant pair
is reported, not interpreted. No hypothesis is tested and no interval is adjusted. Also from the record: E-filtered,
A-and-E, and each search arm's own-run acceptance; the filtered findings of arms E and R (measures 3 and 4 without
the tests that also failed at the base in more than half of the arm's own base runs, ``filtered``); and every arm's
tool errors by kind.

**Feasibility** (``feasibility``): the six criteria, each met, not met or inconclusive with the numbers behind it, and
the outcome (feasible only when all six are met). Criteria 1 and 2 hold arm R too.

**The verdict** (``verdict``): contaminated evaluation, oracle inadequate (criterion 6 alone) or inconclusive, the
first that applies, with V9's feasibility label audit and arm R's git commands beside it.

**The headline sentences** (``headlines``): the three sentences version 2's "What the results permit saying" allows,
their text exactly the protocol's (``SENTENCES``), filled from the outcome, each with its intervals beside it and arm
C's numbers beside arm T's, never in their place. A sentence one of whose numbers or intervals cannot be computed is
not printed, and the report says why: the first when M is zero (the protocol's own rule), the false and correct
acceptance rates never one without the other.

**Not built yet**, until VS-4 and VS-5 settle their fields: template applicability (arm T), the formal path's counts
(arm T), whether surviving mutants predict defects (test adequacy, arm T), similarity to the hidden tests of the checks
C, T, E and R wrote, the per-reason-code shares over findings with their bootstrap, and token counts. Measure 3 for
V9's arms 1 and 4 is not computed: their findings are not rerun by V10's driver.
"""
from __future__ import annotations

import statistics
from statistics import NormalDist

from patch_review_search.record import verify
from patch_review_utility import gaming as gaming_scan
from patch_review_utility import stats
from patch_review_utility.dispositions import tally

CONFIDENCE = 0.95
Z95 = NormalDist().inv_cdf(0.5 + CONFIDENCE / 2)
ARMS = ("A", "C", "T", "E", "R")
#: The arms this study runs itself (A is V9's arm 3, reused), with their spending shares.
RUN_ARMS = ("C", "T", "E", "R")
VARIANTS = ("E_filtered", "A_and_E", "C_own", "T_own")
#: V9's arms this study compares with, by their number in V9's protocol.
V9_ARMS = {"1": "tests", "2": "self_review", "4": "direct_checks"}
MEASURES = ("false_acceptance", "correct_acceptance", "discovery")
#: The paired differences of version 2, each on measures 1 to 3.
PAIRS = (("C", "A"), ("T", "A"), ("C", "E"), ("T", "E"), ("T", "C"),
         ("A", "R"), ("C", "R"), ("T", "R"), ("E", "R"),
         *((arm, v9) for arm in ("C", "T", "E", "R") for v9 in V9_ARMS))
#: The arms whose findings are also counted filtered (version 2, "Secondary measures").
FILTERED = ("E", "R")
NOT_BUILT = ("template applicability (arm T)", "the formal path's counts (arm T)",
             "whether surviving mutants predict defects (arm T)",
             "similarity to the hidden tests of the checks C, T, E and R wrote",
             "per-reason-code shares over findings, with their bootstrap", "input and output tokens")
#: Said beside K_R (version 2, "Measures", 4).
K_R_NOTE = ("M is chosen by V9's arm 2, which uses arm R's model on nearly arm R's prompt, so K_R counts patches that "
            "the same model has already accepted once")
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


def _outputs(entries: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for entry in entries:
        if entry["kind"] == "arm":
            found.setdefault(entry["data"]["patch"], {})[entry["data"]["arm"]] = entry["data"]["output"]
    return found


#: The tool error kinds of a run stopped at a limit: the time limit, the token cap (arm R's, as V9's arm 2), and the
#: spending share.
LIMIT_KINDS = ("time_out", "killed_at_time_limit", "stopped_at_token_cap", "spend_share_reached")


def feasibility(v9: dict, outputs: dict[str, dict], limits: dict | None, comparison: dict | None = None) -> dict:
    """The feasibility criteria (PROTOCOL.md, "The level: feasibility only"), each met, not met or inconclusive, with
    the numbers behind it, over every non-empty patch; feasible only when all six are met. ``limits``: the frozen
    ``cap_cents``, ``call_cap``, ``limit_s`` and ``grace_s``; ``comparison``: the comparison of stated-check results
    the driver recorded (``stated_checks.compare``)."""
    tasks = [task for task in v9["tasks"] if task.get("patch") == "non_empty"]
    n = len(tasks)

    def disposition(task: dict, arm: str):
        if arm == "A":
            return task["arms"].get("g6")
        return (outputs.get(task["task_id"], {}).get(arm) or {}).get("disposition")

    found = []
    numbers, broken = {}, False
    for arm in ARMS:
        given = [disposition(task, arm) for task in tasks]
        errors = sum(isinstance(d, dict) and d.get("category") == "tool_error_or_refusal" for d in given)
        missing = sum(d is None for d in given)
        numbers[arm] = {"patches": n, "missing": missing, "tool_errors": errors,
                        "tool_error_rate": round(errors / n, 4) if n else None}
        broken = broken or bool(missing) or (n and errors / n > stats.TOOL_ERROR_LIMIT)
    found.append({"criterion": 1, "text": "every arm returns a disposition or a counted tool error for every non-empty "
                                          "patch, and none has a tool error on more than 10% of them",
                  "result": "inconclusive" if not n else "not met" if broken else "met", "numbers": numbers,
                  "why": "" if n else "there is no non-empty patch"})

    stopped = {arm: {} for arm in RUN_ARMS}
    for task in tasks:
        for arm in RUN_ARMS:
            given = disposition(task, arm) or {}
            if given.get("error_kind") in LIMIT_KINDS or given.get("time_or_cap_reached"):
                kind = given.get("error_kind") or "time_or_cap_reached"
                stopped[arm][kind] = stopped[arm].get(kind, 0) + 1
    usage = {}
    for arm in RUN_ARMS:
        runs = [outputs.get(task["task_id"], {}).get(arm) or {} for task in tasks]
        usage[arm] = {"cents": round(sum((run.get("meter") or {}).get("cents") or 0.0 for run in runs), 6),
                      "most_calls": max([(run.get("meter") or {}).get("calls") or 0 for run in runs], default=0),
                      "most_seconds": max([run.get("seconds") or 0.0 for run in runs], default=0.0)}
    if not limits:
        result, why = "inconclusive", "not computed: the frozen limits were not given"
    else:
        fractions = limits.get("shares") or dict.fromkeys(RUN_ARMS, 1 / len(RUN_ARMS))
        share = {arm: float(limits["cap_cents"]) * float(fractions[arm]) for arm in RUN_ARMS}
        # arm R's own limits are V9's arm 2 caps: a session past them abstains, counted above, and fails nothing
        over = [arm for arm in RUN_ARMS if usage[arm]["cents"] > share[arm] + 1e-9
                or (arm != "R" and (usage[arm]["most_calls"] > limits["call_cap"]
                                    or usage[arm]["most_seconds"] > limits["limit_s"] + limits.get("grace_s", 0)))]
        result, why = ("not met" if over else "met"), (f"over a limit: {', '.join(over)}" if over else "")
    found.append({"criterion": 2, "text": "no search arm, and neither arm E nor arm R, used more than its frozen limits; "
                                          "a run stopped at a limit is counted, with the limit it reached",
                  "result": result, "why": why, "numbers": {**usage, "stopped_at_a_limit": stopped,
                                                            "limits": limits}})

    stages = {arm: [(outputs.get(task["task_id"], {}).get(arm) or {}).get("search_stage") for task in tasks]
              for arm in "CT"}
    if any(stage is None for arm in "CT" for task, stage in zip(tasks, stages[arm])
           if outputs.get(task["task_id"], {}).get(arm)):
        result, why, ran = "inconclusive", ("not computed: the search arms' replies do not say whether the search "
                                            "stage ran a check to a pass or a fail (the field waits for VS-5)"), {}
    else:
        ran = {arm: {"ran_to_result": sum(bool((stage or {}).get("ran_to_result")) for stage in stages[arm]),
                     "of": n} for arm in "CT"}
        result = ("inconclusive" if not n else
                  "met" if all(ran[arm]["ran_to_result"] * 2 >= n for arm in "CT") else "not met")
        why = "" if n else "there is no non-empty patch"
    found.append({"criterion": 3, "text": "in each search arm, the search stage ran at least one check to a pass or a "
                                          "fail on at least half of the non-empty patches",
                  "result": result, "why": why, "numbers": ran})

    checks = {arm: [(task["task_id"], (outputs.get(task["task_id"], {}).get(arm) or {}).get("report_check"))
                    for task in tasks if outputs.get(task["task_id"], {}).get(arm)] for arm in "CT"}
    if not n or any(found_check is None for arm in "CT" for _, found_check in checks[arm]):
        result, why, numbers = "inconclusive", ("not computed: the search arms' outputs carry no report check (their "
                                                "replies carry no report)" if n else "there is no non-empty patch"), {}
    else:
        numbers = {"checked": {arm: len(checks[arm]) for arm in "CT"},
                   **{field: {arm: [patch for patch, found_check in checks[arm] if found_check[field]]
                              for arm in "CT"} for field in ("model_originated_passing", "formal_problems")}}
        broken = any(numbers[field][arm] for field in ("model_originated_passing", "formal_problems") for arm in "CT")
        result, why = ("not met" if broken else "met"), ""
    found.append({"criterion": 4, "text": "no search arm's report lists a model-originated check as passing a "
                                          "requirement, and every formal result is reported in its three parts",
                  "result": result, "numbers": numbers, "why": why})

    differ = {arm: [task["task_id"] for task in tasks
                    if _accepts((outputs.get(task["task_id"], {}).get(arm) or {}).get("own"))
                    and not _accepts(task["arms"].get("g6"))] for arm in "CT"}
    text = ("the frozen comparison of each search arm's stated-check results with arm A's lists every patch a search "
            "arm's own review accepted and arm A did not")
    if comparison is None:
        found.append({"criterion": 5, "text": text, "result": "inconclusive",
                      "numbers": {"own_accepted_arm_a_did_not": differ},
                      "why": "not computed: the record holds no comparison of stated-check results; the patches its "
                             "list must hold are given"})
    else:
        result = {True: "met", False: "not met", None: "inconclusive"}[comparison.get("holds")]
        found.append({"criterion": 5, "text": text, "result": result,
                      "numbers": {key: comparison.get(key) for key in ("differ", "must_hold", "not_on_list", "unread")},
                      "why": "the comparison could not read every patch's results on the stated checks"
                      if result == "inconclusive" else ""})

    failed = [task["task_id"] for task in tasks if task.get("oracle_controls_passed") is not True]
    found.append({"criterion": 6, "text": "V9's feasibility level met its own criterion 3 (oracle controls) for every "
                                          "task used",
                  "result": "inconclusive" if not n else "not met" if failed else "met",
                  "numbers": {"tasks": n, "failed": failed}, "why": "" if n else "there is no non-empty patch"})
    results = [item["result"] for item in found]
    outcome = ("not feasible" if "not met" in results else "feasible" if all(r == "met" for r in results)
               else "inconclusive")
    return {"result": outcome, "criteria": found}


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
        arms = {"A": task["arms"].get("g6"), **{arm: (found.get(arm) or {}).get("disposition") for arm in RUN_ARMS}}
        arms.update(E_filtered=(found.get("E") or {}).get("e_filtered", arms["E"]),
                    A_and_E=(found.get("E") or {}).get("a_and_e", arms["E"]),
                    C_own=(found.get("C") or {}).get("own"), T_own=(found.get("T") or {}).get("own"),
                    **{number: task["arms"].get(name) for number, name in V9_ARMS.items()})
        reruns = {}
        for arm in ARMS:
            reruns[arm] = [{**item, "reference": compatible.get((task["task_id"], arm, item["node"]))}
                           for item in findings.get((task["task_id"], arm), [])]
        cost = {"A": {**((task["arms"].get("g6") or {}).get("cost") or {}), "cents": None}}
        for arm in RUN_ARMS:
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


def _actionable(row: dict, arm: str, filtered: bool = False) -> list[dict]:
    """The arm's reproducible findings on the patch (actionable on a defective one); ``filtered``: without the tests
    that also failed at the base in more than half of the arm's own base runs (arms E and R)."""
    tests = ((row["outputs"].get(arm) or {}).get("tests") or {}) if filtered else {}
    return [item for item in row["findings"].get(arm, []) if (item.get("result") or {}).get("reproducible") is True
            and not (tests.get(item.get("node")) or {}).get("fails_at_base")]


def _discovers(row: dict, arm: str, filtered: bool = False) -> bool:
    return arm != "2" and bool(_actionable(row, arm, filtered))


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


def _sets(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """M and L: the defective and the correct patches V9's arms 1 and 2 both accepted."""
    both = [row for row in rows if row["accepted_by"]["1"] and row["accepted_by"]["2"]]
    return [row for row in both if row["label"] == "defective"], [row for row in both if row["label"] == "correct"]


def _counts(rows: list[dict], arm: str, filtered: bool = False) -> dict:
    m, l_ = _sets(rows)
    k = sum(_discovers(row, arm, filtered) for row in m)
    k_ref = sum(any((item["reference"] or {}).get("compatible") is True for item in _actionable(row, arm, filtered))
                for row in m)
    j = sum(bool(_actionable(row, arm, filtered)) for row in l_)
    return {"K": rate(k, len(m)), "K_ref": rate(k_ref, len(m)), "J": rate(j, len(l_))}


def _headline(rows: list[dict]) -> dict:
    m, l_ = _sets(rows)
    return {"M": len(m), "L": len(l_), "arms": {arm: _counts(rows, arm) for arm in ARMS},
            "notes": {"K_R": K_R_NOTE}}


def _filtered(rows: list[dict]) -> dict:
    """Measures 3 and 4 again for arms E and R, without the tests that also failed at the base in more than half of
    their base runs (version 2, "Secondary measures")."""
    defective = [row for row in rows if row["label"] == "defective"]
    return {arm: {"discovery": rate(sum(_discovers(row, arm, True) for row in defective), len(defective)),
                  "headline": _counts(rows, arm, True)} for arm in FILTERED}


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


#: The sentences version 2's "What the results permit saying" allows, exactly as the protocol writes them, with their
#: numbers as fields (``X2`` for X′, and so on). Arm C's numbers go beside them, never in arm T's place.
SENTENCES = (
    "On {N} agent-written Python patches from {P} repositories, of the {M} defective patches that the repository's "
    "tests and the agent's own review both accepted, G6 with verification search in its full configuration (arm T) "
    "gave a reproducible finding on {K}, of which {K_ref} also pass on the benchmark's reference fix. Model-written "
    "tests run without G6 (arm E) did so on {K_E}, and a fresh review by a coding agent that did not write the patch "
    "(arm R) on {K_R}. On the {L} correct patches that both accepted, arm T gave a reproducible finding on {J}.",
    "On the same patches, arm T's false acceptance was {X}% against arm A's {Y}% and arm R's {Z}%, and its correct "
    "acceptance {X2}% against {Y2}% and {Z2}%",
    "Arm T cost a mean of {C} model calls, {S} seconds and {D} dollars per patch (median {C2}, {S2} and {D2}).")

def percent(share: float) -> str:
    return f"{100 * share:.1f}"


def percent_interval(pair) -> str:
    return f"{percent(pair[0])}% to {percent(pair[1])}%"


def _of(found: dict) -> str:
    return f"{found['count']} of {found['of']}"


def headlines(result: dict) -> dict:
    """The protocol's sentences filled from ``result`` (``analyse``'s), each with its intervals and arm C's numbers
    beside it; a sentence one of whose numbers or intervals cannot be computed is not printed, and the report says
    why."""
    sentences, not_printed = [], []
    counts, head, rates, costs = result["counts"], result["headline"], result["rates"], result["costs"]
    t, c, e, r = head["arms"]["T"], head["arms"]["C"], head["arms"]["E"], head["arms"]["R"]
    if not head["M"]:
        not_printed.append({"id": 1, "why": "no defective patch was accepted by both the repository's tests and the "
                                            "agent's own review (M is zero)"})
    elif not head["L"]:
        not_printed.append({"id": 1, "why": "no correct patch was accepted by both the repository's tests and the "
                                            "agent's own review (L is zero), so J has no interval"})
    else:
        text = SENTENCES[0].format(N=counts["defective"] + counts["correct"], P=len(counts["repositories"]),
                                   M=head["M"], K=t["K"]["count"], K_ref=t["K_ref"]["count"], K_E=e["K"]["count"],
                                   K_R=r["K"]["count"], L=head["L"], J=t["J"]["count"])
        sentences.append({"id": 1, "text": text,
                          "intervals": {"K": percent_interval(t["K"]["interval"]),
                                        "K_ref": percent_interval(t["K_ref"]["interval"]),
                                        "K_E": percent_interval(e["K"]["interval"]),
                                        "K_R": percent_interval(r["K"]["interval"]),
                                        "J": percent_interval(t["J"]["interval"])},
                          "notes": {"K_R": head["notes"]["K_R"]},
                          "beside": {"arm": "C", "K": _of(c["K"]), "K_ref": _of(c["K_ref"]), "J": _of(c["J"]),
                                     "intervals": {key: percent_interval(c[key]["interval"])
                                                   for key in ("K", "K_ref", "J")}}})
    wanted = [rates[arm][measure] for arm in ("T", "A", "R") for measure in ("false_acceptance", "correct_acceptance")]
    if any(found["interval"] is None for found in wanted):
        missing = [f"{measure.replace('_', ' ')} cannot be computed: no {label} patch"
                   for measure, label in (("false_acceptance", "defective"), ("correct_acceptance", "correct"))
                   if any(rates[arm][measure]["interval"] is None for arm in ("T", "A", "R"))]
        not_printed.append({"id": 2, "why": "; ".join(missing) + "; neither rate is used without the other"})
    else:
        fa_t, ca_t, fa_a, ca_a, fa_r, ca_r = (rates[arm][measure] for arm in ("T", "A", "R")
                                              for measure in ("false_acceptance", "correct_acceptance"))
        text = SENTENCES[1].format(X=percent(fa_t["rate"]), Y=percent(fa_a["rate"]), Z=percent(fa_r["rate"]),
                                   X2=percent(ca_t["rate"]), Y2=percent(ca_a["rate"]), Z2=percent(ca_r["rate"]))
        both_c = [rates["C"][measure] for measure in ("false_acceptance", "correct_acceptance")]
        sentences.append({"id": 2, "text": text,
                          "intervals": {f"{measure} {arm}": percent_interval(rates[arm][measure]["interval"])
                                        for arm in ("T", "A", "R") for measure in ("false_acceptance",
                                                                                   "correct_acceptance")},
                          "beside": {"arm": "C", "false_acceptance": f"{percent(both_c[0]['rate'])}%",
                                     "correct_acceptance": f"{percent(both_c[1]['rate'])}%",
                                     "intervals": {measure: percent_interval(found["interval"]) for measure, found in
                                                   zip(("false_acceptance", "correct_acceptance"), both_c)}}})
    spent = costs["T"]
    if any(spent[key][stat] is None for key in ("model_calls", "seconds", "dollars") for stat in ("mean", "median")):
        not_printed.append({"id": 3, "why": "arm T's model calls, seconds or dollars per patch were not recorded"})
    else:
        def cost(found, stat):
            return {"C": f"{found['model_calls'][stat]:.1f}", "S": f"{found['seconds'][stat]:.1f}",
                    "D": f"{found['dollars'][stat]:.2f}"}
        mean, median = cost(spent, "mean"), cost(spent, "median")
        text = SENTENCES[2].format(**mean, C2=median["C"], S2=median["S"], D2=median["D"])
        beside = costs["C"]
        printable = all(beside[key][stat] is not None for key in ("model_calls", "seconds", "dollars")
                        for stat in ("mean", "median"))
        sentences.append({"id": 3, "text": text,
                          "beside": {"arm": "C", "mean": cost(beside, "mean"), "median": cost(beside, "median")}
                          if printable else {"arm": "C", "not_printed": "arm C's costs were not recorded"}})
    return {"sentences": sentences, "not_printed": not_printed}


def verdict(outputs: dict[str, dict], gaming: list[dict] | None, found: dict, v9_audit: dict | None) -> dict:
    """The study's ECHA verdict (version 2, "Verdict"), the first that applies: ``contaminated_evaluation`` when the
    gaming checks (``gaming``, with the scans of R's transcripts the record holds) found an arm reading the label
    contract, the reference fix or the vault, or using the network from a check or a command a session ran;
    ``oracle_inadequate`` when feasibility criterion 6 fails; ``inconclusive`` otherwise. V9's feasibility label audit
    (``v9_audit``: its sample, label errors, errors by kind and label-invalid patches) is reported beside it, with its
    one-sided 95% upper bound, and decides nothing. No verdict without the gaming scan."""
    scans = {patch: (arms.get("R") or {}).get("gaming") for patch, arms in outputs.items()}
    audit = None if v9_audit is None else {
        **v9_audit, "upper_bound": stats.clopper_pearson_upper(int(v9_audit.get("label_errors", 0)),
                                                               int(v9_audit.get("sample", 0)))}
    beside = {"v9_feasibility_audit": audit,
              "r_git_commands": {patch: scan["git_commands"] for patch, scan in scans.items() if scan}}
    if gaming is None:
        return {"verdict": None, "why": "not decided: the gaming scan was not given", **beside}
    findings = [*gaming, *(f for scan in scans.values() if scan for f in scan["findings"])]
    criterion_6 = next(item["result"] for item in found["criteria"] if item["criterion"] == 6)
    said = ("contaminated_evaluation" if gaming_scan.contaminating(findings) else
            "oracle_inadequate" if criterion_6 == "not met" else "inconclusive")
    return {"verdict": said, "gaming_findings": findings, **beside}


def analyse(record_path, committed: str, *, v9: dict, reference: list[dict], limits: dict | None = None,
            gaming: list[dict] | None = None, v9_audit: dict | None = None, resamples: int = stats.RESAMPLES) -> dict:
    """The primary measures, the feasibility criteria and the verdict (the module docstring), from a record that
    verifies against ``committed``. ``limits``: the frozen ``cap_cents``, ``call_cap``, ``limit_s``, ``grace_s`` and
    ``shares``; ``gaming``: the gaming scan's findings over the other arms' traces; ``v9_audit``: V9's feasibility
    label audit, reported beside the verdict."""
    entries = verify(record_path, committed)
    rows, counts = _patches(entries, v9, reference)
    seed = int(v9["seed"]) + 2
    found = _measures(entries, rows, counts, v9, limits, committed, seed, resamples)
    return {**found, "headlines": headlines(found),
            "verdict": verdict(_outputs(entries), gaming, found["feasibility"], v9_audit)}


def _measures(entries, rows, counts, v9, limits, committed, seed, resamples) -> dict:
    return {"study": "V10", "kind": "primary measures", "confidence": CONFIDENCE,
            "feasibility": feasibility(v9, _outputs(entries), limits,
                                       next((e["data"] for e in entries if e["kind"] == "comparison"), None)),
            "filtered": _filtered(rows),
            "record": {"committed": committed, "entries": len(entries)}, "counts": counts,
            "bootstrap_seed": seed, "resamples": resamples,
            "rates": {name: _rates(rows, name) for name in (*ARMS, *VARIANTS)},
            "missing": {name: sum(row["dispositions"].get(name) is None for row in rows) for name in (*ARMS, *VARIANTS)},
            "discovery": {arm: _discovery(rows, arm) for arm in (*ARMS, *V9_ARMS)},
            "headline": _headline(rows), "differences": _differences(rows, seed=seed, resamples=resamples),
            "costs": _costs(rows), "tool_errors": {arm: tally([row["dispositions"].get(arm) for row in rows])
                                                   for arm in ARMS},
            "not_built": list(NOT_BUILT), "rows": rows}
