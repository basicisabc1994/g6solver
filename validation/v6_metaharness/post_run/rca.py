# SPDX-License-Identifier: Apache-2.0
"""Root-cause the V6 failures on MASSIVE, SciFact and MBPP (development analysis; V6's verdict is final).

Reads the V6 splits, the dev-check outputs (dev split) and the measured held-out outputs (eval split, spent), classifies
each failure, and measures headroom: how often any arm got a case right that the default profile (B) got wrong.
"""
import collections
import json
import re
import sys
from pathlib import Path

OSS = Path(sys.argv[1])
DATA = Path(sys.argv[2])
WORK = Path(sys.argv[3])
sys.path.insert(0, str(OSS / "validation" / "v6_metaharness"))
import workflows as W  # noqa: E402

splits = json.loads((OSS / "docs" / "validation" / "results" / "V6" / "splits.json").read_text())["splits"]


def rows(path):
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def load(wf):
    dev = rows(WORK / ("devcheck_massive" if wf == "massive" else "devcheck") / f"devcheck-{wf}.jsonl")
    ev = rows(WORK / "run" / wf / "outputs.jsonl")
    spec = W.SPECS[wf]
    ids = splits[wf]["dev"] + splits[wf]["eval"]
    cases = {c.case_id: c for c in spec.cases(DATA, ids)}
    return spec, dev, ev, cases


def by_arm(rs):
    out = collections.defaultdict(dict)
    for r in rs:
        out[r["arm"]][r["case_id"]] = r
    return out


def headroom(arms, base="B"):
    b = arms[base]
    wrong = [cid for cid, r in b.items() if not r["success"]]
    rescued = [cid for cid in wrong if any(arms[a].get(cid, {}).get("success") for a in arms if a != base)]
    return len(b), len(wrong), len(rescued)


def label_failures(spec, arms, cases, patterns, gold_of):
    cats, confusions = collections.Counter(), collections.Counter()
    for cid, r in arms["B"].items():
        if r["success"]:
            continue
        if not r["released"]:
            cats[f"not released: {r['error'] or r['completion_state']}"] += 1
            continue
        named = W.labels_named(r["output"] or "", patterns)
        gold = gold_of(cases[cid])
        if not named:
            cats["no label named"] += 1
        elif len(named) > 1:
            cats["several labels named"] += 1
        else:
            cats["one wrong label"] += 1
            confusions[(gold, named[0])] += 1
    return cats, confusions


def report_labels(wf, patterns, gold_of, domain=None):
    spec, dev, ev, cases = load(wf)
    print(f"\n=== {wf} ===")
    for name, rs in (("dev (20)", dev), ("held-out (60, spent)", ev)):
        arms = by_arm(rs)
        n, wrong, rescued = headroom(arms)
        print(f"-- {name}: arms {sorted(arms)}; B wrong {wrong}/{n}; some other arm right on {rescued} of them")
        print("   success by arm:", {a: sum(r['success'] for r in arms[a].values()) for a in sorted(arms)})
        cats, conf = label_failures(spec, arms, cases, patterns, gold_of)
        print("   B failure kinds:", dict(cats))
        if domain:
            same = sum(c for (g, p), c in conf.items() if domain(g) == domain(p))
            print(f"   wrong label in the same domain as the gold: {same} of {sum(conf.values())}")
        print("   top confusions (gold -> said):", conf.most_common(8))
        golds = collections.Counter(gold_of(cases[c]) for c in arms["B"])
        print("   gold distribution:", golds.most_common(6))


def report_mbpp():
    spec, dev, ev, cases = load("mbpp")
    print("\n=== mbpp ===")
    for name, rs in (("dev (20)", dev), ("held-out (60, spent)", ev)):
        arms = by_arm(rs)
        n, wrong, rescued = headroom(arms)
        print(f"-- {name}: arms {sorted(arms)}; B wrong {wrong}/{n}; some other arm right on {rescued} of them")
        print("   success by arm:", {a: sum(r['success'] for r in arms[a].values()) for a in sorted(arms)})
        cats = collections.Counter()
        for cid, r in arms["B"].items():
            if r["success"]:
                continue
            out = r["output"] or ""
            if not r["released"]:
                cats[f"not released: {r['error']}"] += 1
                continue
            if not W._parses_with_def(out):
                cats["no parsable def"] += 1
                continue
            exp = cases[cid].expected
            code = W.extract_code(out)
            shown = W.run_tests(code, exp["imports"], exp["tests"][:1], timeout_s=10)
            m = re.search(r"assert\s+(\w+)\(", exp["tests"][0])
            want = m.group(1) if m else None
            named = re.findall(r"(?m)^\s*def\s+(\w+)\s*\(", code)
            if shown.status != "ok":
                err = (shown.stderr or "").strip().splitlines()[-1:] or [shown.status]
                kind = err[0].split(":")[0]
                if want and want not in named:
                    kind = "wrong function name"
                cats[f"fails the shown example: {kind}"] += 1
            else:
                cats["passes the shown example, fails a hidden test"] += 1
        print("   B failure kinds:", dict(cats))


report_labels("massive", W.MASSIVE_PATTERNS, lambda c: W.label_key(str(c.expected)), domain=lambda lab: lab.split("_")[0])
report_labels("scifact", W.SCIFACT_PATTERNS, lambda c: str(c.expected))
report_mbpp()
