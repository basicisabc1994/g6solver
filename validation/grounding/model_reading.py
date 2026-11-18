# SPDX-License-Identifier: Apache-2.0
"""Calibrate advisory readings on the V1 sets with an explicitly injected caller.

    from validation.grounding.model_reading import run
    run(pairs_path, output_path, llm=caller)

Or call main(["--out", output_path], llm=caller) for the frozen V1 pairs.
No caller is resolved by this script. The owner supplies one through G6's normal
consent path; local tests use FakeLLM. Outputs describe this run only.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from g6.core import semantic
from g6.grounding import GroundingBlock, GroundingInput

V1_PAIRS = Path(__file__).resolve().parents[2] / "docs/validation/results/V1/v1_test_pairs.CC-BY-SA-3.0.csv"
SETS = {"paraphrase": ("paraphrased",), "near_verbatim": ("quoted",),
        "contradiction": ("negated", "wrong_figure")}


def evaluate(rows: list[dict], *, llm) -> dict:
    """Agreement is with the V1 label; unclear is an abstention, never agreement.

    On unsupported contradiction pairs only 'contradicts' agrees. All rates use
    the set's full n (including abstentions); empty sets have null rates.
    """
    sets = {name: {"n": 0, "agreement": 0, "false_supports": 0, "abstentions": 0} for name in SETS}
    records = []
    for row in rows:
        group = next((name for name, strata in SETS.items() if row["stratum"] in strata), None)
        if group is None:
            continue
        if row["label"] not in ("supported", "unsupported"):
            raise ValueError("V1 labels must be supported or unsupported")
        # Each pair is an independent public operation, with its own bounded request.
        with semantic.request(llm=llm):
            result = GroundingBlock().infer(GroundingInput(
                claim=row["claim"], sources={"source": row["source"]}, judge="model"))
        if result.is_fail():
            status, reading, reason = None, "unclear", "input refused"
        else:
            check = result.value["check"]
            status, reading = check["status"], check["model_reading"]["reading"]
            reason = check["model_reading"]["reason"]
        counts = sets[group]
        counts["n"] += 1
        counts["agreement"] += reading == ("supports" if row["label"] == "supported" else "contradicts")
        counts["false_supports"] += reading == "supports" and row["label"] == "unsupported"
        counts["abstentions"] += reading == "unclear"
        records.append({"pair_id": row["pair_id"], "set": group, "label": row["label"], "status": status,
                        "reading": reading, "reason": reason})
    for counts in sets.values():
        for count, rate in (("agreement", "agreement_rate"), ("false_supports", "false_support_rate"),
                            ("abstentions", "abstention_rate")):
            counts[rate] = counts[count] / counts["n"] if counts["n"] else None
    return {"sets": sets, "rows": records,
            "scope": "Injected-caller calibration on supplied V1 pairs; no transfer or gate change established"}


def run(pairs: Path, out: Path, *, llm) -> dict:
    with Path(pairs).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    report = evaluate(rows, llm=llm)
    Path(out).write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def main(argv=None, *, llm=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pairs", type=Path, nargs="?", default=V1_PAIRS)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if llm is None:
        parser.error("inject a caller with main(argv, llm=caller) or run(pairs, out, llm=caller)")
    run(args.pairs, args.out, llm=llm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
