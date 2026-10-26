# SPDX-License-Identifier: Apache-2.0
"""Study V6b analysis (PROTOCOL.md §6-§8): per-workflow endpoints, controls and the verdict, from the runner's rows.

    python validation/v6b_metaharness/analyze.py --out docs/validation/results/V6b
    python validation/v6b_metaharness/analyze.py power          (the pre-registration's power simulation; no data)

Writes ``analysis.json`` and ``v6b_rows.csv`` beside the rows. Nothing here calls a model or reads an answer: it reads
the rows the runner wrote (success, release and oracle outcome per case and arm). The decision rule is one function,
``decision_rule``; the verdict and the planted-effect control both call it, so the control tests the rule that decides.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

from g6.reasoning.compare import sign_test_p

WORKFLOWS = ("gsm8k", "massive", "scifact", "mbpp")
TARGETS = ("massive", "scifact", "mbpp")        # the three workflows V6 left inconclusive
ARMS = ("NULL", "A", "B", "B2", "H", "D", "E", "E_tok", "F", "G")
RESAMPLES, BOOT_SEED = 10_000, 20261020
ALPHA = 0.05
PLANTED_REPLICATES = 2000
MIN_FAILURE_SHARE = 0.10           # rule 4: B must fail at least this share of the pooled held-out tasks
MAX_SELFTEST_FAILURE = 0.05        # rule 3: the oracle may reject at most this share of its own reference answers
PUBLISHED_ANSWER_ERROR = {"gsm8k": 0.02}   # GSM8K paper: "less than 2 percent of problems contain breaking errors"
APPROVER = "v6b-scripted-approval (study script, not a person)"
REMEDY_STEPS = ("second_look_support", "second_look_contradict", "second_look_not_enough_info", "repair_with_tests",
                "repair1")
COUNTS = ("calls_tap", "prompt_tokens", "output_tokens", "prompt_chars", "output_chars")


def read_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def _changed(record: dict | None) -> bool:
    """Whether a meta-harness arm ever moved off version 0."""
    return bool(record) and any(c.get("head_version", 0) != 0 for c in record.get("cycles", []))


def load(out: Path) -> tuple[dict, dict, dict]:
    """rows[workflow][arm] -> {case_id: row}; adaptation records; which arms equal another by the same-profile rule."""
    splits = json.loads((out / "splits.json").read_text(encoding="utf-8"))["splits"]
    rows: dict[str, dict[str, dict[str, dict]]] = {}
    adaptation: dict[str, dict[str, Any]] = {}
    same: dict[str, dict[str, str]] = {}
    for wf in splits:
        rows[wf] = {}
        folder = out / "rows" / wf
        for path in sorted(folder.glob("*.jsonl")) if folder.exists() else []:
            rows[wf][path.stem] = {r["case_id"]: r for r in read_rows(path)}
        rec = {}
        for kind in ("D", "F", "E", "oracle_selftest"):
            p = out / "adaptation" / f"{wf}-{kind}.json"
            rec[kind] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        adaptation[wf] = rec
        same[wf] = {}
        if "B" not in rows[wf]:
            continue
        d, f, e = rec["D"], rec["F"], rec["E"]
        if d and d.get("cycles") and "D" not in rows[wf] and not _changed(d):
            same[wf]["D"] = "B"
        if f and f.get("cycles") and "F" not in rows[wf] and not _changed(f):
            same[wf]["F"] = "B"
        if e is not None and e.get("n") == 1 and "E" not in rows[wf]:
            same[wf]["E"] = "B"
        if e is not None and "E_tok" not in rows[wf] and (e.get("n_tok") == 1 or e.get("n_tok") == e.get("n")):
            same[wf]["E_tok"] = "B" if e.get("n_tok") == 1 else "E"
        for arm, like in same[wf].items():
            source = rows[wf].get(like) or rows[wf].get(same[wf].get(like, ""), {})
            rows[wf][arm] = {k: dict(v, arm=arm, same_profile_as=like) for k, v in source.items()}
    return rows, adaptation, same


# --------------------------------------------------------------------------------------------------------------------
# Statistics.
# --------------------------------------------------------------------------------------------------------------------
def pairs(rows: dict, cand: str, base: str, measure: str = "success") -> dict[str, list[tuple[int, int]]]:
    """Per workflow, the (base, candidate) outcome pairs on the cases both arms measured."""
    out = {}
    for wf, arms in rows.items():
        if cand in arms and base in arms:
            ids = sorted(set(arms[cand]) & set(arms[base]))
            out[wf] = [(int(bool(arms[base][i][measure])), int(bool(arms[cand][i][measure]))) for i in ids]
    return out


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m, running, adjusted = len(order), 0.0, {}
    for i, key in enumerate(order):
        running = max(running, min(1.0, (m - i) * pvalues[key]))
        adjusted[key] = round(running, 6)
    return adjusted


def cluster_bootstrap(by_wf: dict[str, list[tuple[int, int]]], *, resamples: int = RESAMPLES,
                      seed: int = BOOT_SEED) -> tuple[float, float]:
    """95% percentile interval of the pooled mean paired difference: workflows resampled, then tasks within each
    (descriptive in V6b; see V6-F3)."""
    groups = [[c - b for b, c in by_wf[w]] for w in sorted(by_wf) if by_wf[w]]
    if not groups:
        return (math.nan, math.nan)
    rng, stats = random.Random(seed), []
    for _ in range(resamples):
        total, count = 0, 0
        for _ in groups:
            g = groups[rng.randrange(len(groups))]
            for _ in g:
                total += g[rng.randrange(len(g))]
            count += len(g)
        stats.append(total / count)
    stats.sort()
    return (round(stats[int(0.025 * resamples)], 4), round(stats[min(resamples - 1, int(0.975 * resamples))], 4))


def compare(by_wf: dict[str, list[tuple[int, int]]], *, boot: bool = True) -> dict[str, Any]:
    flat = [p for w in by_wf.values() for p in w]
    n = len(flat)
    wins = sum(c > b for b, c in flat)
    losses = sum(b > c for b, c in flat)
    out = {"n": n, "base": sum(b for b, _ in flat), "candidate": sum(c for _, c in flat), "wins": wins,
           "losses": losses, "ties": n - wins - losses,
           "diff_mean": round((sum(c for _, c in flat) - sum(b for b, _ in flat)) / n, 4) if n else None,
           "p_one_sided": round(sign_test_p(wins, losses), 6),
           "per_workflow": {w: {"n": len(v), "base": sum(b for b, _ in v), "candidate": sum(c for _, c in v),
                                "wins": sum(c > b for b, c in v), "losses": sum(b > c for b, c in v),
                                "p_one_sided": round(sign_test_p(sum(c > b for b, c in v), sum(b > c for b, c in v)),
                                                     6)} for w, v in by_wf.items()}}
    if boot and n:
        out["ci95_cluster_bootstrap"] = cluster_bootstrap(by_wf)
    return out


# --------------------------------------------------------------------------------------------------------------------
# The decision rule (PROTOCOL.md §6, §8): one function for the verdict and for the planted-effect control.
# --------------------------------------------------------------------------------------------------------------------
def decision_rule(success: dict[str, list[tuple[int, int]]], false_accepts: dict[str, list[tuple[int, int]]],
                  recovery_origin: dict[str, bool]) -> dict[str, Any]:
    """Per workflow: D against B by the one-sided exact sign test (gain, and separately loss), Holm across the four
    workflows at family alpha 0.05 (each direction its own family); the false-accept constraint D <= B; and which of
    the three target workflows are recovery wins (a Holm-significant gain with more wins than losses, and D's frozen
    profile holds a change activated from a RecoveryStrategy proposal)."""
    per: dict[str, dict[str, Any]] = {}
    for wf in WORKFLOWS:
        v = success.get(wf, [])
        wins, losses = sum(c > b for b, c in v), sum(b > c for b, c in v)
        fa = false_accepts.get(wf, [])
        fa_more, fa_fewer = sum(c > b for b, c in fa), sum(b > c for b, c in fa)
        per[wf] = {"n": len(v), "b": sum(b for b, _ in v), "d": sum(c for _, c in v), "wins": wins, "losses": losses,
                   "p_gain": sign_test_p(wins, losses), "p_loss": sign_test_p(losses, wins),
                   "fa_b": sum(b for b, _ in fa), "fa_d": sum(c for _, c in fa),
                   # secondary: whether a rise in false accepts is beyond noise (the constraint itself has margin 0)
                   "fa_tasks_d_more": fa_more, "fa_tasks_d_fewer": fa_fewer, "p_fa_rise": sign_test_p(fa_more, fa_fewer)}
    adj_gain = holm({wf: per[wf]["p_gain"] for wf in WORKFLOWS})
    adj_loss = holm({wf: per[wf]["p_loss"] for wf in WORKFLOWS})
    for wf in WORKFLOWS:
        p = per[wf]
        p["holm_gain"], p["holm_loss"] = adj_gain[wf], adj_loss[wf]
        p["gain"] = adj_gain[wf] <= ALPHA and p["wins"] > p["losses"]
        p["loss"] = adj_loss[wf] <= ALPHA and p["losses"] > p["wins"]
        p["fa_rose"] = p["fa_d"] > p["fa_b"]
        p["recovery_origin"] = bool(recovery_origin.get(wf))
    return {"per_workflow": per,
            "gains": [wf for wf in WORKFLOWS if per[wf]["gain"]],
            "losses": [wf for wf in WORKFLOWS if per[wf]["loss"]],
            "fa_rose": [wf for wf in WORKFLOWS if per[wf]["fa_rose"]],
            "recovery_wins": [wf for wf in TARGETS if per[wf]["gain"] and per[wf]["recovery_origin"]]}


def recovery_origin(record: dict | None) -> bool:
    """D's frozen profile still holds every field an activation from a RecoveryStrategy proposal set."""
    if not record or not record.get("cycles"):
        return False
    final = record["cycles"][-1]["head_profile"]
    for cycle in record["cycles"]:
        act = cycle.get("activation") or {}
        if act.get("code") == "G6_E_META_ACTIVATED" and act.get("strategy") == "recovery":
            diff = act.get("diff") or {}
            if diff and all(final.get(k) == v.get("to") for k, v in diff.items()):
                return True
    return False


def remedy_in_profile(profile: dict | None) -> list[str]:
    """Which recovery remedies a profile holds, whoever proposed them (secondary, descriptive)."""
    if not profile:
        return []
    found = [f"exemplars={profile['exemplars']}"] if profile.get("exemplars") else []
    return found + [s for s in profile.get("pipeline", []) if s in REMEDY_STEPS]


def _plant(rows_c: list[tuple[int, int]], rows_fa: list[tuple[int, int]], k: int,
           rng: random.Random) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Turn ``k`` of the candidate's failing tasks into successes (and so not false accepts)."""
    failing = [j for j, (_, c) in enumerate(rows_c) if c == 0]
    flip = set(rng.sample(failing, min(k, len(failing))))
    succ = [(b, 1 if j in flip else c) for j, (b, c) in enumerate(rows_c)]
    fa = [(b, 0 if j in flip else c) for j, (b, c) in enumerate(rows_fa)]
    return succ, fa


PLANT_SCENARIOS = {
    "+0.10 on each target": {"massive": 0.10, "scifact": 0.10, "mbpp": 0.10},
    "+0.15 on each target": {"massive": 0.15, "scifact": 0.15, "mbpp": 0.15},
    "pilot-sized (massive +0.20, scifact +0.15, mbpp +0.15)": {"massive": 0.20, "scifact": 0.15, "mbpp": 0.15},
    "pilot-sized on the targets and gsm8k +0.30": {"gsm8k": 0.30, "massive": 0.20, "scifact": 0.15, "mbpp": 0.15},
    "+0.15 on two targets (massive, mbpp)": {"massive": 0.15, "mbpp": 0.15},
    "+0.15 on one target (mbpp)": {"mbpp": 0.15},
}


def planted(rows: dict, *, replicates: int = PLANTED_REPLICATES) -> dict[str, Any]:
    """The survival control (PROTOCOL.md §7): the A/A rerun B2 stands in for D, with a planted gain per workflow (a
    seeded share of B2's failing tasks turned into successes), judged by ``decision_rule`` exactly as D is, with every
    target's recovery origin granted. Reported: how often rule 5's count (at least two recovery wins) is met, and how
    often rule 2's success-loss or false-accept clause would fire."""
    succ = pairs(rows, "B2", "B")
    fa = pairs(rows, "B2", "B", "false_accept")
    if not all(wf in succ for wf in WORKFLOWS):
        return {"ran": False}
    origin = dict.fromkeys(TARGETS, True)
    null = decision_rule(succ, fa, origin)
    out: dict[str, Any] = {"ran": True, "replicates": replicates,
                           "null_A/A": {"recovery_wins": null["recovery_wins"], "gains": null["gains"],
                                        "losses": null["losses"], "fa_rose": null["fa_rose"]}}
    for name, gains in PLANT_SCENARIOS.items():
        rng = random.Random(f"{BOOT_SEED}:{name}")
        fired = regress = 0
        counts = dict.fromkeys(WORKFLOWS, 0)
        for _ in range(replicates):
            s2, f2 = dict(succ), dict(fa)
            for wf, gain in gains.items():
                s2[wf], f2[wf] = _plant(succ[wf], fa[wf], round(gain * len(succ[wf])), rng)
            res = decision_rule(s2, f2, origin)
            fired += len(res["recovery_wins"]) >= 2
            regress += bool(res["losses"] or res["fa_rose"])
            for wf in res["gains"]:
                counts[wf] += 1
        out[name] = {"rule5_count_met": round(fired / replicates, 4), "rule2_would_fire": round(regress / replicates, 4),
                     "gain_detected_by_workflow": {wf: round(c / replicates, 4) for wf, c in counts.items()}}
    return out


def arm_table(rows: dict) -> dict[str, dict[str, dict[str, Any]]]:
    table: dict[str, dict[str, dict[str, Any]]] = {}
    for wf, arms in rows.items():
        table[wf] = {}
        for arm in ARMS + tuple(sorted(a for a in arms if a.startswith("D@"))):
            if arm not in arms or not arms[arm]:
                continue
            rs = list(arms[arm].values())
            n = len(rs)
            succ = sum(r["success"] for r in rs)
            calls = sum(r["model_calls"] for r in rs)
            secs = sum(r["wall_clock_s"] for r in rs)
            tokens = sum(r.get("prompt_tokens", 0) + r.get("output_tokens", 0) for r in rs)
            table[wf][arm] = {"n": n, "success": succ, "released": sum(r["released"] for r in rs),
                              "false_accepts": sum(r["false_accept"] for r in rs),
                              "blocked": sum(not r["released"] for r in rs),
                              "calls_mean": round(calls / n, 3), "seconds_mean": round(secs / n, 3),
                              "tokens_mean": round(tokens / n, 1),
                              **{f"{k}_mean": round(sum(r.get(k, 0) for r in rs) / n, 1) for k in COUNTS},
                              "calls_per_success": round(calls / succ, 3) if succ else None,
                              "tokens_per_success": round(tokens / succ, 1) if succ else None,
                              "same_profile_as": next((r.get("same_profile_as") for r in rs if r.get("same_profile_as")),
                                                      None)}
    return table


# --------------------------------------------------------------------------------------------------------------------
# The adaptation funnel, controls and the verdict.
# --------------------------------------------------------------------------------------------------------------------
def funnel(record: dict | None) -> dict[str, Any]:
    if not record or not record.get("cycles"):
        return {"ran": False, "problem": (record or {}).get("problem")}
    evals = [e for c in record["cycles"] for e in c["evaluations"]]
    recs = [e for e in evals if (e.get("decision") or {}).get("verdict") == "recommend"]
    acts = [c["activation"] for c in record["cycles"] if c.get("activation")]
    codes: dict[str, int] = {}
    for e in evals:
        code = (e.get("decision") or {}).get("code") or e.get("evaluate_code")
        codes[code] = codes.get(code, 0) + 1
    offers = [o for c in record["cycles"] for o in c.get("offers", [])]
    return {"ran": True, "cycles": len(record["cycles"]), "offers": len(offers),
            "offer_codes": [(o["strategy"], o["code"]) for o in offers], "evaluations": len(evals),
            "evaluations_by_strategy": {s: sum(e.get("strategy") == s for e in evals)
                                        for s in sorted({e.get("strategy") for e in evals})},
            "recommendations": len(recs),
            "recommended_by_strategy": {s: sum(e.get("strategy") == s for e in recs)
                                        for s in sorted({e.get("strategy") for e in recs})},
            "activations": len(acts), "activated_ok": sum(a["code"] == "G6_E_META_ACTIVATED" for a in acts),
            "activations_detail": [{"cycle": c["cycle"], "strategy": c["activation"].get("strategy"),
                                    "diff": c["activation"].get("diff"), "code": c["activation"]["code"]}
                                   for c in record["cycles"] if c.get("activation")],
            "decision_codes": codes, "final_head_version": record["cycles"][-1]["head_version"],
            "final_profile": record["cycles"][-1]["head_profile"],
            "remedies_in_final_profile": remedy_in_profile(record["cycles"][-1]["head_profile"]),
            "recovery_origin": recovery_origin(record),
            "gate_success_at_activation": [
                {"cycle": c["cycle"], "base": e["report"]["base_success"], "candidate": e["report"]["candidate_success"],
                 "n": e["report"]["n"]}
                for c in record["cycles"] if c.get("activation")
                for e in c["evaluations"] if e.get("candidate_digest") == c["activation"]["candidate_digest"]
                and "report" in e],
            "demotions": 0}


def contamination(out: Path, adaptation: dict) -> dict[str, Any]:
    tap = json.loads((out / "tap.json").read_text(encoding="utf-8")) if (out / "tap.json").exists() else {}
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8")) if (out / "run_meta.json").exists() else {}
    starts = meta.get("starts", [])

    def held(key: str) -> bool:
        """The same at every start and at every end recorded. No start, or a last start with no end record, is not
        verified (it counts as a mutation); an earlier start that was killed has no end, and the next start's pinned
        value is compared instead."""
        if not starts or f"{key}_end" not in starts[-1]:
            return False
        ends = all(s.get(key) == s.get(f"{key}_end") for s in starts if f"{key}_end" in s)
        return ends and len({json.dumps(s.get(key), sort_keys=True) for s in starts}) <= 1

    frozen_same, g6_same, data_same, judging_same = (held("frozen"), held("g6_manifest_sha256"), held("data"),
                                                     held("judging_sha256"))
    model_same = held("model_digest")
    gaming = {}
    for p in sorted((out / "adaptation").glob("*-gaming.json")):
        g = json.loads(p.read_text(encoding="utf-8"))
        gaming[p.name.split("-gaming")[0]] = {"outputs_scanned": g["outputs_scanned"],
                                              "matches": {k: len(v) for k, v in g["matches"].items()},
                                              "cases": {k: v[:20] for k, v in g["matches"].items() if v}}
    judging = {wf: (rec["D"] or {}).get("confirm", {}).get("judging_sha256") == (rec["D"] or {}).get("judging_sha256_end")
               for wf, rec in adaptation.items() if rec["D"] and rec["D"].get("confirm")}
    canary = {wf: (rec["D"] or {}).get("canary") for wf, rec in adaptation.items() if rec["D"]}
    bank = {wf: ((rec["oracle_selftest"] or {}).get("example_bank") or {}).get("examples_repeating_a_study_case")
            for wf, rec in adaptation.items() if (rec["oracle_selftest"] or {}).get("example_bank")}
    hits = [h for h in tap.get("leak_hits", []) if not h["phase"].startswith("measure")]
    leaked = (bool(hits) or any(c and (c.get("gate_snippets_in_views") or c.get("eval_snippets_in_views"))
                                for c in canary.values()) or any(v for v in bank.values()))
    mutated = not (frozen_same and g6_same and data_same and judging_same and all(judging.values()))
    return {"frozen_files_unchanged": frozen_same, "g6_source_unchanged": g6_same, "model_digest_unchanged": model_same,
            "data_files_unchanged": data_same, "judging_code_unchanged_run": judging_same,
            "judging_code_unchanged_per_adaptation": judging, "gaming_scan": gaming, "proposer_view_canary": canary,
            "example_bank_repeats": bank, "prompt_leak_hits": hits, "leakage": leaked, "evaluator_mutation": mutated,
            "starts": len(starts), "starts_with_end_record": sum("frozen_end" in s for s in starts), "calls_by_phase": tap.get("calls_by_phase", {}),
            "tokens_by_phase": tap.get("tokens_by_phase", {}), "call_errors": tap.get("errors", {})}


def protected(adaptation: dict) -> dict[str, Any]:
    problems = []
    for wf, rec in adaptation.items():
        for kind in ("D", "F"):
            r = rec.get(kind)
            if not r:
                continue
            for v in r.get("inspect_versions", []):
                if v.get("kind") == "activate" and (v.get("accepted_by") != APPROVER or
                                                    v.get("accepted_by", "").lower() == v.get("proposer", "").lower()):
                    problems.append(f"{wf}/{kind}: activation version {v.get('version')} not by the scripted approver")
            for claim in (r.get("report") or {}).get("checked", []):
                if claim.get("holds") is not True:
                    problems.append(f"{wf}/{kind}: report claim does not hold: {claim.get('claim')}")
    return {"breached": bool(problems), "problems": problems}


def oracle_check(adaptation: dict) -> dict[str, Any]:
    per = {}
    for wf, rec in adaptation.items():
        st = rec.get("oracle_selftest")
        if not st:
            continue
        ev = st["eval"]
        per[wf] = {"n": ev["n"], "reference_failed": len(ev["reference_failed"]), "empty_passed": len(ev["empty_passed"]),
                   "reference_failure_share": round(len(ev["reference_failed"]) / ev["n"], 4) if ev["n"] else None,
                   "gate_reference_failed": len(st["gate"]["reference_failed"]),
                   "gate_empty_passed": len(st["gate"]["empty_passed"]),
                   "published_answer_error": PUBLISHED_ANSWER_ERROR.get(wf)}
    n_total = sum(p["n"] for p in per.values())
    bound = sum(p["n"] / n_total * ((p["reference_failure_share"] or 0) + (p["published_answer_error"] or 0))
                for p in per.values()) if n_total else math.nan
    inadequate = any((p["reference_failure_share"] or 0) > MAX_SELFTEST_FAILURE or p["empty_passed"]
                     for p in per.values())
    return {"per_workflow": per, "pooled_known_error_bound": round(bound, 4), "inadequate": inadequate}


def verdict(ctx: dict[str, Any]) -> tuple[str, list[str]]:
    trail = []
    c = ctx["contamination"]
    if c["evaluator_mutation"] or c["leakage"]:
        return "contaminated_evaluation", ["rule 1: evaluator mutation or leakage"]
    trail.append("rule 1: no evaluator mutation or leakage found")
    rule = ctx["decision"]
    if rule["fa_rose"] or rule["losses"] or ctx["protected"]["breached"]:
        return "unsafe_or_regressive", trail + [f"rule 2: false accepts rose on {rule['fa_rose']}; Holm-significant "
                                                f"success loss on {rule['losses']}; protected breached: "
                                                f"{ctx['protected']['breached']}"]
    trail.append("rule 2: on every workflow D's false accepts <= B's, no Holm-significant loss, nothing protected "
                 "breached")
    if ctx["oracle"]["inadequate"]:
        return "oracle_inadequate", trail + ["rule 3: the oracle failed its self-test"]
    trail.append("rule 3: oracle self-test passed")
    b = ctx["b_failures"]
    if b["share"] < MIN_FAILURE_SHARE:
        return "insufficient_failure_opportunities", trail + [f"rule 4: B failed {b['failures']}/{b['n']}"]
    trail.append(f"rule 4: B failed {b['failures']}/{b['n']} pooled held-out tasks")
    if len(rule["recovery_wins"]) >= 2:
        return "mechanism_demonstrated", trail + [f"rule 5: recovery wins on {rule['recovery_wins']} (Holm-significant "
                                                  "gain over B, from a RecoveryStrategy activation)"]
    trail.append(f"rule 5 not met: recovery wins on {rule['recovery_wins']}; Holm-significant gains on "
                 f"{rule['gains']}")
    activated = [wf for wf in TARGETS if ctx["funnel"][wf]["D"].get("activated_ok")]
    if activated and not any(wf in rule["gains"] for wf in activated):
        return "component_validated_no_transfer", trail + [f"rule 6: the gate recommended and the script activated "
                                                           f"changes on {activated}; none beat B on held-out"]
    trail.append(f"rule 6 not met: activations on targets {activated}; gains among them "
                 f"{[wf for wf in activated if wf in rule['gains']]}")
    return "inconclusive", trail + ["rule 7: otherwise"]


def analyze(out: Path) -> dict[str, Any]:
    rows, adaptation, same = load(out)
    for wf in WORKFLOWS:
        rows.setdefault(wf, {})
        adaptation.setdefault(wf, {"D": None, "F": None, "E": None, "oracle_selftest": None})
    origin = {wf: recovery_origin(adaptation[wf]["D"]) for wf in WORKFLOWS}
    rule = decision_rule(pairs(rows, "D", "B"), pairs(rows, "D", "B", "false_accept"), origin)
    secondary: dict[str, Any] = {"D_vs_B_pooled": compare(pairs(rows, "D", "B"))}
    for other in ("E", "E_tok", "F", "H", "A", "G"):
        secondary[f"D_vs_{other}"] = compare(pairs(rows, "D", other))
    secondary["H_vs_B"] = compare(pairs(rows, "H", "B"))
    secondary["B_vs_A"] = compare(pairs(rows, "B", "A"))
    aa = pairs(rows, "B2", "B")
    aa_rule = decision_rule(aa, pairs(rows, "B2", "B", "false_accept"), dict.fromkeys(TARGETS, True)) \
        if all(wf in aa for wf in WORKFLOWS) else None
    b_rows = [r for arms in rows.values() for r in arms.get("B", {}).values()]
    funnels = {wf: {"D": funnel(rec["D"]), "F": funnel(rec["F"]), "E": rec["E"]} for wf, rec in adaptation.items()}
    table = arm_table(rows)
    gap = {}
    for wf in WORKFLOWS:
        acts = funnels[wf]["D"].get("gate_success_at_activation") or []
        if acts and "D" in table.get(wf, {}) and "B" in table.get(wf, {}):
            last = acts[-1]
            gap[wf] = {"gate_base": f"{last['base']}/{last['n']}", "gate_candidate": f"{last['candidate']}/{last['n']}",
                       "gate_gain": round((last["candidate"] - last["base"]) / last["n"], 4),
                       "eval_base": f"{table[wf]['B']['success']}/{table[wf]['B']['n']}",
                       "eval_d": f"{table[wf]['D']['success']}/{table[wf]['D']['n']}",
                       "eval_gain": round((table[wf]["D"]["success"] - table[wf]["B"]["success"])
                                          / max(1, table[wf]["B"]["n"]), 4)}
    ctx = {
        "decision": rule,
        "secondary": secondary,
        "arms": table,
        "same_profile": same,
        "funnel": funnels,
        "gate_vs_eval_gap": gap,
        "null_controls": {"A/A_B2_vs_B": compare(aa),
                          "A/A_decision_rule": aa_rule and {k: aa_rule[k] for k in ("gains", "losses", "fa_rose",
                                                                                    "recovery_wins")},
                          "A/A_false_accepts": {w: {"b2": sum(c for _, c in v), "b": sum(b for b, _ in v)}
                                                for w, v in pairs(rows, "B2", "B", "false_accept").items()},
                          "planted_effect_on_the_decision_rule": planted(rows),
                          "suite_negative_control": {wf: {"n": len(arms.get("NULL", {})),
                                                          "success": sum(r["success"] for r in arms.get("NULL", {}).values())}
                                                     for wf, arms in rows.items()},
                          "gate_negative_controls": {
                              wf: [e["report"]["negative_control"] for kind in ("D", "F") if rec.get(kind)
                                   for c in rec[kind].get("cycles", []) for e in c["evaluations"] if "report" in e]
                              for wf, rec in adaptation.items()}},
        "learning_gradient": {wf: {arm: round(sum(r["success"] for r in arms[arm].values()) / len(arms[arm])
                                              - sum(r["success"] for r in arms["B"].values()) / len(arms["B"]), 4)
                                   for arm in sorted(arms) if (arm.startswith("D@") or arm == "D") and arms[arm]}
                              for wf, arms in rows.items() if arms.get("B")},
        "b_failures": {"n": len(b_rows), "failures": sum(not r["success"] for r in b_rows),
                       "share": round(sum(not r["success"] for r in b_rows) / len(b_rows), 4) if b_rows else 0.0},
        "contamination": contamination(out, adaptation),
        "protected": protected(adaptation),
        "oracle": oracle_check(adaptation),
    }
    ctx["verdict"], ctx["verdict_trail"] = verdict(ctx)
    return ctx


def write_csv(out: Path) -> None:
    rows, _, _ = load(out)
    with (out / "v6b_rows.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["workflow", "case_id", "arm", "success", "released", "false_accept", "oracle", "model_calls",
                    "wall_clock_s", *COUNTS, "completion_state", "error", "same_profile_as"])
        for wf in sorted(rows):
            for arm in sorted(rows[wf]):
                for cid in sorted(rows[wf][arm]):
                    r = rows[wf][arm][cid]
                    w.writerow([wf, cid, arm, int(r["success"]), int(r["released"]), int(r["false_accept"]),
                                r["oracle"], r["model_calls"], r["wall_clock_s"], *(r.get(k, "") for k in COUNTS),
                                r["completion_state"], r["error"], r.get("same_profile_as") or ""])


# --------------------------------------------------------------------------------------------------------------------
# The pre-registration's power simulation (no data): the gate, then the decision rule on 100 eval cases.
# --------------------------------------------------------------------------------------------------------------------
PILOT = {   # per-case (win, loss) rates against B from V6's development pilot (60 seen cases), and V6's GSM8K result
    "gsm8k": (33 / 60, 1 / 60), "massive": (14 / 60, 2 / 60), "scifact": (15 / 60, 6 / 60), "mbpp": (9 / 60, 0.5 / 60)}
DEV = {     # per-case (win, loss) rates against B on V6b's own 20 dev cases (run.py devcheck): the remedy RecoveryStrategy
    # offered first (gsm8k the working prompt, massive 8 examples, scifact the second look at SUPPORT, mbpp 3 repair
    # rounds); 0.5 / 20 stands in for a loss rate of 0 of 20
    "gsm8k": (13 / 20, 0.5 / 20), "massive": (5 / 20, 2 / 20), "scifact": (6 / 20, 2 / 20), "mbpp": (1 / 20, 1 / 20)}
GATE_THRESHOLD, MIN_DISCORDANT = ALPHA / 6, 5


def simulate(effects: dict[str, tuple[float, float]], gate_sizes: dict[str, int], *, n_eval: int = 100,
             replicates: int = 4000, seed: int = BOOT_SEED, force_gate: bool = False) -> dict[str, Any]:
    """Each workflow's gate recommends the effect with the probability ``g6.metaharness.evaluate.power`` gives on its
    gate split (always, with ``force_gate``); then D's eval pairs are drawn with the effect's per-case win and loss
    rates (D = B when the gate did not recommend), and ``decision_rule`` judges, with every target's recovery origin
    granted. Every answer is taken as released, so a failure is a false accept."""
    from g6.metaharness.evaluate import power

    rng = random.Random(seed)
    gate_power = {wf: 1.0 if force_gate else power(gate_sizes[wf], w + lo, w - lo, GATE_THRESHOLD, MIN_DISCORDANT)
                  for wf, (w, lo) in effects.items()}
    met = loss = fa = no_transfer = 0
    by_wf = dict.fromkeys(WORKFLOWS, 0)
    for _ in range(replicates):
        succ: dict[str, list[tuple[int, int]]] = {}
        active: dict[str, bool] = {}
        for wf in WORKFLOWS:
            w, lo = effects.get(wf, (0.0, 0.0))
            active[wf] = rng.random() < gate_power.get(wf, 0.0)
            v = []
            for _ in range(n_eval):
                u = rng.random()
                v.append((0, 1) if active[wf] and u < w else (1, 0) if active[wf] and u < w + lo else (1, 1))
            succ[wf] = v
        fas = {wf: [(1 - b, 1 - c) for b, c in v] for wf, v in succ.items()}
        res = decision_rule(succ, fas, dict.fromkeys(TARGETS, True))
        met += len(res["recovery_wins"]) >= 2
        loss += bool(res["losses"])
        fa += bool(res["fa_rose"])
        targets_on = [wf for wf in TARGETS if active[wf]]
        no_transfer += bool(targets_on) and not any(wf in res["gains"] for wf in targets_on)
        for wf in res["gains"]:
            by_wf[wf] += 1
    return {"gate_power": gate_power, "rule5_count_met": round(met / replicates, 4),
            "loss_clause_fires": round(loss / replicates, 4), "false_accept_clause_fires": round(fa / replicates, 4),
            "rule6_condition_met": round(no_transfer / replicates, 4),
            "gain_detected_by_workflow": {wf: round(c / replicates, 4) for wf, c in by_wf.items()}}


def cmd_power() -> int:
    from workflows import GATE_SIZE

    half = {wf: (w / 2 + lo / 2, lo) for wf, (w, lo) in PILOT.items()}   # half the net gain, same loss rate
    null = {wf: (0.075, 0.075) for wf in TARGETS}                          # an activated change with no effect
    print(json.dumps({"pilot": simulate(PILOT, GATE_SIZE), "half_pilot_gain": simulate(half, GATE_SIZE),
                      "v6b_dev": simulate(DEV, GATE_SIZE),
                      "no_effect_activated_on_every_target": simulate(null, GATE_SIZE, force_gate=True),
                      "no_effect_activated_on_one_target": simulate({"mbpp": (0.075, 0.075)}, GATE_SIZE,
                                                                    force_gate=True)}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["power"]:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        return cmd_power()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    result = analyze(args.out)
    (args.out / "analysis.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    write_csv(args.out)
    print(json.dumps({"verdict": result["verdict"], "trail": result["verdict_trail"],
                      "per_workflow": {wf: {k: v for k, v in p.items()}
                                       for wf, p in result["decision"]["per_workflow"].items()}},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
