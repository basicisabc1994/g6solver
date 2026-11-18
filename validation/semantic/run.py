# SPDX-License-Identifier: Apache-2.0
"""Compare floor and model decisions on a supplied JSON set, without an accuracy claim.

Run from the repository root: python -m validation.semantic.run --kind risk
--cases cases.json --out readings.json [--model]. Only --model resolves a real
caller. Held-out sets and real-model runs belong to the lead.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from g6.core import semantic


def _floor(kind, text):
    if kind == "risk":
        from g6.core.risk_signals import _floor_classify
        return _floor_classify(text)[0], ("R0", "R1", "R2", "R3")
    if kind == "effort":
        from g6.solver.complexity_classifier import _floor_classify_task
        return _floor_classify_task(text).value, ("simple", "moderate", "complex")
    if kind == "refusal":
        from g6.solver.red_flag_detector import _floor_is_refusal
        return _floor_is_refusal(text), ()
    raise ValueError("this kind needs explicit case floor and candidates, or an injected floor function")


def measure(kind, cases, *, llm=None, floor=None, schema=None):
    if kind not in semantic.KINDS:
        raise ValueError("unregistered semantic kind")
    rows = []
    for n, case in enumerate(cases):
        text = case["text"]
        if not isinstance(text, str):
            raise ValueError("case text must be a string")
        if "floor" in case:
            value, choices = case["floor"], case.get("candidates", ())
        else:
            value, choices = (floor or (lambda text: _floor(kind, text)))(text)
        candidates = [semantic.Candidate(**c) if isinstance(c, dict) else c for c in choices]
        with semantic.request(llm=llm, budget=1):
            if schema is not None:
                offered = tuple(c if isinstance(c, semantic.Candidate) else semantic.Candidate(c, c) for c in candidates)
                reading = semantic.read(semantic.Question(kind, text, schema, lambda: value,
                                                         semantic.KINDS[kind].combine, offered))
            else:
                if not isinstance(value, (str, bool, list)):
                    raise ValueError("structured kinds need an explicit pydantic reply schema")
                reading = semantic.decide(kind, text, value, candidates)
        rows.append({"id": case.get("id", str(n)), **reading.mechanism()})
    changed = sum(row["value"] != row["floor_value"] for row in rows)
    return {"kind": kind, "cases": len(rows), "agreement": len(rows) - changed, "changed_decisions": changed,
            "model_readings": sum(row["by"] != "floor" for row in rows), "readings": rows,
            "limits": "Agreement measures decisions, not correctness. No accuracy or generalisation is established."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=tuple(semantic.KINDS))
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", action="store_true", help="explicitly use G6's configured model through consent")
    args = parser.parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        parser.error("cases must be a JSON list")
    result = measure(args.kind, cases, llm=semantic.resolve_caller() if args.model else None)
    args.out.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
