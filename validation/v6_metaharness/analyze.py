# SPDX-License-Identifier: Apache-2.0
"""Study V6 analysis (PROTOCOL.md §5-§7): endpoints, null controls and the verdict, from the runner's rows.

    python validation/v6_metaharness/analyze.py --out docs/validation/results/V6

Writes ``analysis.json`` and ``v6_rows.csv`` beside the rows. Nothing here calls a model or reads an answer: it reads the
rows the runner wrote (success, release and oracle outcome per case and arm).
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

ARMS = ("NULL", "A", "B", "B2", "C", "D", "E", "F", "G")
RESAMPLES, BOOT_SEED = 10_000, 20261019
ALPHA = 0.05
PLANTED_GAIN, PLANTED_REPLICATES = 0.10, 2000
MIN_FAILURE_SHARE = 0.10           # rule 4: B must fail at least this share of the pooled held-out tasks
MAX_SELFTEST_FAILURE = 0.05        # rule 3: the oracle may reject at most this share of its own reference answers
PUBLISHED_ANSWER_ERROR = {"gsm8k": 0.02}   # GSM8K paper: "less than 2 percent of problems contain breaking errors"
APPROVER = "v6-scripted-approval (study script, not a person)"


def read_rows(folder: Path) -> list[dict]:
    return [json.loads(x) for x in folder.read_text(encoding="utf-8").splitlines() if x.strip()] if folder.exists() \
        else []


def load(out: Path) -> tuple[dict, dict, dict]:
    """rows[workflow][arm] -> {case_id: row}; adaptation records; which arms equal B by the same-profile rule."""
    splits = json.loads((out / "splits.json").read_text(encoding="utf-8"))["splits"]
    rows: dict[str, dict[str, dict[str, dict]]] = {}
    adaptation: dict[str, dict[str, Any]] = {}
    same_as_b: dict[str, list[str]] = {}
    for wf in splits:
        rows[wf] = {}
        for path in sorted((out / "rows" / wf).glob("*.jsonl")) if (out / "rows" / wf).exists() else []:
            rows[wf][path.stem] = {r["case_id"]: r for r in read_rows(path)}
        rec = {}
        for kind in ("D", "F", "C", "E", "oracle_selftest"):
            p = out / "adaptation" / f"{wf}-{kind}.json"
            rec[kind] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        adaptation[wf] = rec
        same: list[str] = []
        base = "B" in rows[wf]
        if base:
            d, f, c, e = rec["D"], rec["F"], rec["C"], rec["E"]
            if d and d.get("cycles") and "D" not in rows[wf] and not _changed(d):
                same.append("D")
            if f and f.get("cycles") and "F" not in rows[wf] and not _changed(f):
                same.append("F")
            if c is not None and not c.get("hints") and "C" not in rows[wf]:
                same.append("C")
            if e is not None and e.get("n") == 1 and "E" not in rows[wf]:
                same.append("E")
            for arm in same:
                rows[wf][arm] = {k: dict(v, arm=arm, same_profile_as="B") for k, v in rows[wf]["B"].items()}
        same_as_b[wf] = same
    return rows, adaptation, same_as_b


def _changed(record: dict) -> bool:
    """Whether a meta-harness arm ever moved off version 0."""
    return any(c.get("head_version", 0) != 0 for c in record.get("cycles", []))


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


def cluster_bootstrap(by_wf: dict[str, list[tuple[int, int]]], *, resamples: int = RESAMPLES,
                      seed: int = BOOT_SEED) -> tuple[float, float]:
    """95% percentile interval of the pooled mean paired difference: workflows resampled, then tasks within each."""
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
    if boot:
        out["ci95_cluster_bootstrap"] = cluster_bootstrap(by_wf)
    return out


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m, running, adjusted = len(order), 0.0, {}
    for i, key in enumerate(order):
        running = max(running, min(1.0, (m - i) * pvalues[key]))
        adjusted[key] = round(running, 6)
    return adjusted


def planted(rows: dict, *, gain: float = PLANTED_GAIN, replicates: int = PLANTED_REPLICATES,
            level: float = ALPHA / 4) -> dict[str, Any]:
    """The survival control: the A/A rerun B2 with a planted gain (a seeded share of its failing tasks turned into
    successes) compared with B by the primary test. Detection = one-sided sign test p <= ``level``."""
    base = pairs(rows, "B2", "B")
    flat = [(w, i, b, c) for w, v in base.items() for i, (b, c) in enumerate(v)]
    n = len(flat)
    if not n:
        return {"n": 0}
    k = round(gain * n)
    failing = [j for j, (_, _, _, c) in enumerate(flat) if c == 0]
    rng, detected, detected_05 = random.Random(BOOT_SEED), 0, 0
    for _ in range(replicates):
        flip = set(rng.sample(failing, min(k, len(failing))))
        wins = losses = 0
        for j, (_, _, b, c) in enumerate(flat):
            c2 = 1 if j in flip else c
            wins += c2 > b
            losses += b > c2
        p = sign_test_p(wins, losses)
        detected += p <= level
        detected_05 += p <= ALPHA
    return {"n": n, "planted_tasks": min(k, len(failing)), "gain": gain, "replicates": replicates,
            "detected_at_holm_first_step": round(detected / replicates, 4), "level": level,
            "detected_at_0.05": round(detected_05 / replicates, 4)}


def arm_table(rows: dict) -> dict[str, dict[str, dict[str, Any]]]:
    table: dict[str, dict[str, dict[str, Any]]] = {}
    for wf, arms in rows.items():
        table[wf] = {}
        for arm in ARMS + tuple(sorted(a for a in arms if a.startswith("D@"))):
            if arm not in arms:
                continue
            rs = list(arms[arm].values())
            succ = sum(r["success"] for r in rs)
            calls = sum(r["model_calls"] for r in rs)
            secs = sum(r["wall_clock_s"] for r in rs)
            table[wf][arm] = {"n": len(rs), "success": succ, "released": sum(r["released"] for r in rs),
                              "false_accepts": sum(r["false_accept"] for r in rs),
                              "calls_mean": round(calls / len(rs), 3) if rs else None,
                              "seconds_mean": round(secs / len(rs), 3) if rs else None,
                              "calls_per_success": round(calls / succ, 3) if succ else None,
                              "seconds_per_success": round(secs / succ, 3) if succ else None,
                              "blocked": sum(not r["released"] for r in rs),
                              "same_profile_as_B": any(r.get("same_profile_as") == "B" for r in rs)}
    return table


# --------------------------------------------------------------------------------------------------------------------
# The adaptation funnel, controls and the verdict.
# --------------------------------------------------------------------------------------------------------------------
def funnel(record: dict | None) -> dict[str, Any]:
    if not record or not record.get("cycles"):
        return {"ran": False}
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
            "recommendations": len(recs), "activations": len(acts),
            "activated_ok": sum(a["code"] == "G6_E_META_ACTIVATED" for a in acts), "decision_codes": codes,
            "final_head_version": record["cycles"][-1]["head_version"],
            "final_profile": record["cycles"][-1]["head_profile"],
            "gate_success_at_activation": [
                {"cycle": c["cycle"], "base": e["report"]["base_success"], "candidate": e["report"]["candidate_success"],
                 "n": e["report"]["n"]}
                for c in record["cycles"] if c.get("activation")
                for e in c["evaluations"] if e.get("candidate_digest") == c["activation"]["candidate_digest"]],
            "demotions": 0}


def contamination(out: Path, adaptation: dict) -> dict[str, Any]:
    tap = json.loads((out / "tap.json").read_text(encoding="utf-8")) if (out / "tap.json").exists() else {}
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8")) if (out / "run_meta.json").exists() else {}
    starts = meta.get("starts", [])
    frozen_same = all(s.get("frozen") == s.get("frozen_end") for s in starts if "frozen_end" in s)
    frozen_same = frozen_same and len({json.dumps(s.get("frozen"), sort_keys=True) for s in starts}) <= 1
    g6_same = all(s.get("g6_manifest_sha256") == s.get("g6_manifest_sha256_end") for s in starts
                  if "g6_manifest_sha256_end" in s)
    model_same = all(s.get("model_digest") == s.get("model_digest_end") for s in starts if "model_digest_end" in s)
    data_same = all(s.get("data") == s.get("data_end") for s in starts if "data_end" in s)
    data_same = data_same and len({json.dumps(s.get("data"), sort_keys=True) for s in starts}) <= 1
    gaming = {}
    for p in sorted((out / "adaptation").glob("*-gaming.json")):
        g = json.loads(p.read_text(encoding="utf-8"))
        gaming[p.name.split("-gaming")[0]] = {"outputs_scanned": g["outputs_scanned"],
                                              "matches": {k: len(v) for k, v in g["matches"].items()},
                                              "cases": {k: v[:20] for k, v in g["matches"].items() if v}}
    judging = {wf: (rec["D"] or {}).get("confirm", {}).get("judging_sha256") == (rec["D"] or {}).get("judging_sha256_end")
               for wf, rec in adaptation.items() if rec["D"]}
    canary = {wf: (rec["D"] or {}).get("canary") for wf, rec in adaptation.items() if rec["D"]}
    hits = [h for h in tap.get("leak_hits", []) if not h["phase"].startswith("measure")]
    leaked = bool(hits) or any(c and (c.get("gate_snippets_in_views") or c.get("eval_snippets_in_views"))
                               for c in canary.values())
    mutated = not (frozen_same and g6_same and data_same and all(judging.values()))
    return {"frozen_files_unchanged": frozen_same, "g6_source_unchanged": g6_same, "model_digest_unchanged": model_same,
            "data_files_unchanged": data_same, "gaming_scan": gaming,
            "judging_code_unchanged": judging, "proposer_view_canary": canary, "prompt_leak_hits": hits,
            "leakage": leaked, "evaluator_mutation": mutated, "calls_by_phase": tap.get("calls_by_phase", {}),
            "call_errors": tap.get("errors", {})}


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


def oracle_check(adaptation: dict, rows: dict) -> dict[str, Any]:
    per = {}
    for wf, rec in adaptation.items():
        st = rec.get("oracle_selftest")
        if not st:
            continue
        ev = st["eval"]
        per[wf] = {"n": ev["n"], "reference_failed": len(ev["reference_failed"]), "empty_passed": len(ev["empty_passed"]),
                   "reference_failure_share": round(len(ev["reference_failed"]) / ev["n"], 4) if ev["n"] else None,
                   "gate_reference_failed": len(st["gate"]["reference_failed"]),
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
    e2 = ctx["endpoints"]["E2_false_accepts"]
    if e2.get("rose") or ctx["protected"]["breached"]:
        return "unsafe_or_regressive", trail + [f"rule 2: false accepts D {e2.get('d')} vs B {e2.get('b')}; "
                                                f"protected breached: {ctx['protected']['breached']}"]
    trail.append(f"rule 2: false accepts did not rise (D {e2.get('d')} <= B {e2.get('b')}); nothing protected breached")
    if ctx["oracle"]["inadequate"]:
        return "oracle_inadequate", trail + ["rule 3: the oracle failed its self-test"]
    trail.append(f"rule 3: oracle self-test passed; pooled known error bound {ctx['oracle']['pooled_known_error_bound']}")
    b = ctx["b_failures"]
    if b["share"] < MIN_FAILURE_SHARE:
        return "insufficient_failure_opportunities", trail + [f"rule 4: B failed {b['failures']}/{b['n']}"]
    trail.append(f"rule 4: B failed {b['failures']}/{b['n']} pooled held-out tasks")
    e = ctx["endpoints"]
    adj = e["holm_adjusted"]
    e1 = e["E1_success_D_vs_B"]
    beats = {k: (adj.get(k, 1.0) <= ALPHA and e[name]["wins"] > e[name]["losses"])
             for k, name in (("D>B", "E1_success_D_vs_B"), ("D>E", "D_vs_E"), ("D>F", "D_vs_F")) if name in e}
    lower = (e1.get("ci95_cluster_bootstrap") or (math.nan, math.nan))[0]
    bound = ctx["oracle"]["pooled_known_error_bound"]
    if len(beats) == 3 and all(beats.values()) and lower > bound:
        return "mechanism_demonstrated", trail + [f"rule 5: {beats}; lower bound {lower} > {bound}"]
    trail.append(f"rule 5 not met: {beats}; gain lower bound {lower} vs oracle bound {bound}")
    held_out_gain = beats.get("D>B", False)
    if ctx["dev_gain"] and not held_out_gain:
        return "component_validated_no_transfer", trail + ["rule 6: the gate recommended and the script activated at "
                                                           "least one change on the dev side; E1 shows no held-out "
                                                           "gain"]
    trail.append(f"rule 6 not met: dev-side activation {ctx['dev_gain']}, held-out gain over B {held_out_gain}")
    return "inconclusive", trail + ["rule 7: otherwise"]


def analyze(out: Path) -> dict[str, Any]:
    rows, adaptation, same = load(out)
    endpoints: dict[str, Any] = {}
    endpoints["E1_success_D_vs_B"] = compare(pairs(rows, "D", "B"))
    fa = pairs(rows, "D", "B", "false_accept")
    flat = [p for v in fa.values() for p in v]
    fa_d, fa_b = sum(c for _, c in flat), sum(b for b, _ in flat)
    fewer = sum(b > c for b, c in flat)
    more = sum(c > b for b, c in flat)
    endpoints["E2_false_accepts"] = {"n": len(flat), "d": fa_d, "b": fa_b, "rose": fa_d > fa_b,
                                     "tasks_d_fewer": fewer, "tasks_d_more": more,
                                     "p_one_sided_fewer": round(sign_test_p(fewer, more), 6),
                                     "p_one_sided_rise": round(sign_test_p(more, fewer), 6),
                                     "per_workflow": {w: {"d": sum(c for _, c in v), "b": sum(b for b, _ in v)}
                                                      for w, v in fa.items()}}
    for other in ("E", "F", "C", "G", "A"):
        endpoints[f"D_vs_{other}"] = compare(pairs(rows, "D", other))
    endpoints["B_vs_A"] = compare(pairs(rows, "B", "A"))
    tests = {"D>B": endpoints["E1_success_D_vs_B"]["p_one_sided"],
             "FA": endpoints["E2_false_accepts"]["p_one_sided_fewer"]}
    if endpoints["D_vs_E"]["n"]:
        tests["D>E"] = endpoints["D_vs_E"]["p_one_sided"]
    if endpoints["D_vs_F"]["n"]:
        tests["D>F"] = endpoints["D_vs_F"]["p_one_sided"]
    endpoints["holm_family"] = tests
    endpoints["holm_adjusted"] = holm(tests)
    b_rows = [r for arms in rows.values() for r in arms.get("B", {}).values()]
    ctx = {
        "endpoints": endpoints,
        "arms": arm_table(rows),
        "same_profile_as_B": same,
        "funnel": {wf: {"D": funnel(rec["D"]), "F": funnel(rec["F"]),
                        "C": {"hints": (rec["C"] or {}).get("hints"), "code": (rec["C"] or {}).get("code")},
                        "E": rec["E"]} for wf, rec in adaptation.items()},
        "null_controls": {"A/A_B2_vs_B": compare(pairs(rows, "B2", "B")),
                          "A/A_false_accepts": {w: {"b2": sum(c for _, c in v), "b": sum(b for b, _ in v)}
                                                for w, v in pairs(rows, "B2", "B", "false_accept").items()},
                          "planted_improvement": planted(rows),
                          "suite_negative_control": {wf: {"n": len(arms.get("NULL", {})),
                                                          "success": sum(r["success"] for r in arms.get("NULL", {}).values())}
                                                     for wf, arms in rows.items()},
                          "gate_negative_controls": {
                              wf: [e["report"]["negative_control"] for kind in ("D", "F") if rec.get(kind)
                                   for c in rec[kind].get("cycles", []) for e in c["evaluations"] if "report" in e]
                              for wf, rec in adaptation.items()}},
        "learning_gradient": {wf: {arm: arms[arm] and round(sum(r["success"] for r in arms[arm].values())
                                                            / len(arms[arm]) - sum(r["success"] for r in arms["B"].values())
                                                            / len(arms["B"]), 4)
                                   for arm in sorted(arms) if arm.startswith("D@") or arm == "D"}
                              for wf, arms in rows.items() if "B" in arms},
        "b_failures": {"n": len(b_rows), "failures": sum(not r["success"] for r in b_rows),
                       "share": round(sum(not r["success"] for r in b_rows) / len(b_rows), 4) if b_rows else 0.0},
        "contamination": contamination(out, adaptation),
        "protected": protected(adaptation),
        "oracle": oracle_check(adaptation, rows),
        "dev_gain": any(funnel(rec["D"]).get("activated_ok") for rec in adaptation.values()),
    }
    ctx["verdict"], ctx["verdict_trail"] = verdict(ctx)
    return ctx


def write_csv(out: Path) -> None:
    rows, _, _ = load(out)
    with (out / "v6_rows.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["workflow", "case_id", "arm", "success", "released", "false_accept", "oracle", "model_calls",
                    "wall_clock_s", "completion_state", "error", "same_profile_as_B"])
        for wf in sorted(rows):
            for arm in sorted(rows[wf]):
                for cid in sorted(rows[wf][arm]):
                    r = rows[wf][arm][cid]
                    w.writerow([wf, cid, arm, int(r["success"]), int(r["released"]), int(r["false_accept"]),
                                r["oracle"], r["model_calls"], r["wall_clock_s"], r["completion_state"], r["error"],
                                int(r.get("same_profile_as") == "B")])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    result = analyze(args.out)
    (args.out / "analysis.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    write_csv(args.out)
    print(json.dumps({"verdict": result["verdict"], "trail": result["verdict_trail"],
                      "E1": {k: v for k, v in result["endpoints"]["E1_success_D_vs_B"].items() if k != "per_workflow"},
                      "E2": result["endpoints"]["E2_false_accepts"],
                      "holm_adjusted": result["endpoints"]["holm_adjusted"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
