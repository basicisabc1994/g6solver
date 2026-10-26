# SPDX-License-Identifier: Apache-2.0
"""Study V6b plumbing (PROTOCOL.md §9): the whole runner and the analysis, end to end, on ``g6.core.llm.FakeLLM`` and on
pool items outside every drawn id (V6's and V6b's), so no study case is read. Not a measurement.

    python validation/v6b_metaharness/plumb.py --data $V6B_DATA --splits docs/validation/results/V6b --out <scratch>
                                               --work <scratch> [--workflows massive,...]

The fake model knows the answers of the plumbing items only. It answers right when a remedy is in its prompt (labelled
examples, a working prompt, a second look, a repair request) and otherwise about a third of the time, so the gate, the
scripted approval and the later cycles all run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze  # noqa: E402
import run as R  # noqa: E402
from workflows import ORDER, REVIEW_SYSTEM, SPECS  # noqa: E402

SIZES = {"dev": 8, "gate": 32, "eval": 12}
WRONG = {"gsm8k": "Answer: 7", "massive": "alarm_query", "scifact": "SUPPORT",
         "mbpp": "```python\ndef wrong(*args):\n    return None\n```"}


def responder(key: str, cases) -> callable:
    spec = SPECS[key]
    known = [(R.case_text(key, c.input), c) for c in cases]

    def right(case) -> str:
        if key == "mbpp":
            return f"```python\n{case.meta['reference']}\n```"
        return f"Answer: {case.expected}" if key == "gsm8k" else str(case.expected)

    def answer(system: str, user: str) -> str:
        if system == REVIEW_SYSTEM:                      # self_review: the draft back, unchanged
            return user.split("DRAFT ANSWER:\n", 1)[-1]
        found = [(user.rfind(text), c) for text, c in known if text and text in user]
        if not found:
            return "no idea"
        case = max(found, key=lambda t: t[0])[1]          # the task's own case comes after any examples
        helped = ("Reference examples" in user or "step by step" in system or system.startswith("A first reading")
                  or "It fails the example test" in user)
        roll = int(hashlib.sha256((system + user).encode()).hexdigest(), 16) % 100
        if helped or roll < 35:
            return right(case)
        return WRONG[key] if spec.key != "massive" or roll % 2 else "alarm_set"

    return answer


def main(argv: list[str] | None = None) -> int:
    from g6.core.llm import FakeLLM

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--splits", required=True, help="the folder holding V6b's splits.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--workflows", default=",".join(ORDER))
    args = ap.parse_args(argv)
    data, out, work = Path(args.data), Path(args.out), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    os.environ["G6_HOME"] = str(work / "g6home")
    study = R.load_splits(Path(args.splits))
    keys = args.workflows.split(",")
    plumbing = {}
    for key in ORDER:
        ids = R.plumbing_ids(data, key, study, sum(SIZES.values()))
        at, plumbing[key] = 0, {}
        for split, size in SIZES.items():
            plumbing[key][split] = ids[at:at + size]
            at += size
    (out / "splits.json").write_text(json.dumps({"plumbing": True, "splits": plumbing}, indent=1), encoding="utf-8")
    R.record_start(out, data, "FakeLLM", args.workflows, digest="fake")
    for key in keys:
        spec = SPECS[key]
        cases = R.cases_for(spec, data, [i for s in plumbing[key].values() for i in s])
        tap = R.Tap(FakeLLM(responder(key, cases)))
        R.log(out, f"plumbing {key}: start")
        R.run_workflow(spec, key, data, out, work / key, plumbing[key], tap)
        R.write_tap(out, tap)
    R.record_end(out, data, digest="fake")
    result = analyze.analyze(out)
    (out / "analysis.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    analyze.write_csv(out)
    print(json.dumps({"verdict": result["verdict"], "trail": result["verdict_trail"],
                      "funnel": {wf: result["funnel"][wf]["D"].get("activations_detail") for wf in keys}},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
