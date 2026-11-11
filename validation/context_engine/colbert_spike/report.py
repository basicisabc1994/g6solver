# SPDX-License-Identifier: Apache-2.0
"""The persistent-meaning spike's table and decision (protocol 05).

    python validation/context_engine/colbert_spike/report.py results/*.json

Every cell of the declared grid (candidate x size x platform x measure) stays in the denominator: a cell with no
result, an unavailable or failed run, or a missing measure is listed and fails the envelope for its candidate. The
decision is the cheapest candidate inside the whole envelope on every platform, or ``none: repair and rerun``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [p for p in (str(HERE),) if p not in sys.path]

import candidates  # noqa: E402

PROTOCOL = HERE.parents[2] / "docs" / "measurements" / "context-engine" / "05-scale-and-meaning.protocol.md"


def thresholds() -> dict:
    text = PROTOCOL.read_text(encoding="utf-8")
    return json.loads(text[text.index("```json") + 7:text.rindex("```")])["thresholds"]


def cell_problems(result: dict | None, size: int, limits: dict) -> list[str]:
    """Why one (candidate, size, platform) result is outside the envelope; ``[]`` when it is inside."""
    if result is None:
        return ["no result"]
    if result.get("state") != "ran":
        return [f"{result.get('state', 'unknown')}: {result.get('reason', '')}"[:200]]
    m, out = result.get("measures", {}), []
    out += [f"missing measure {name}" for name in candidates.MEASURES if name not in m]
    checks = (("peak_rss_bytes", lambda v: v <= limits["peak_rss_bytes_max"]),
              ("disk_bytes", lambda v: v <= limits["disk_bytes_per_100000_passages_max"] * size / 100_000),
              ("warm_p95_s", lambda v: v <= limits["warm_p95_seconds_max"]),
              ("re_encoded_after_restart", lambda v: v == limits["passages_re_encoded_after_restart"]),
              ("partial_after_kill", lambda v: v == limits["partial_passages_after_kill"]))
    out += [f"{name} outside the envelope" for name, ok in checks if name in m and not ok(m[name])]
    if size == candidates.SIZES[0] and "recall_at_10" in m and m["recall_at_10"] < limits["recall_at_10_vs_exhaustive_maxsim_min"]:
        out.append("recall_at_10 outside the envelope")
    return out


def decide(results: list[dict], platforms: tuple[str, ...] = candidates.PLATFORMS) -> dict:
    limits = thresholds()
    by_cell = {(r.get("candidate"), r.get("size"), r.get("platform")): r for r in results}
    table, inside = [], {}
    for cid in sorted(candidates.CANDIDATES):
        problems = {}
        for size in candidates.SIZES:
            for platform in platforms:
                found = cell_problems(by_cell.get((cid, size, platform)), size, limits)
                if found:
                    problems[f"{size}/{platform}"] = found
                table.append({"candidate": cid, "size": size, "platform": platform,
                              "state": (by_cell.get((cid, size, platform)) or {}).get("state", "missing"),
                              "problems": found})
        if not problems:
            worst = [by_cell[(cid, s, p)]["measures"] for s in candidates.SIZES for p in platforms]
            inside[cid] = (max(m["peak_rss_bytes"] for m in worst), max(m["disk_bytes"] for m in worst),
                           max(m["warm_p95_s"] for m in worst))
    cells = len(candidates.CANDIDATES) * len(candidates.SIZES) * len(platforms)
    missing = sum(1 for row in table if row["state"] != "ran")
    decision = min(inside, key=lambda c: (*inside[c], c)) if inside else "none: repair and rerun"
    return {"cells": cells, "cells_missing_or_failed": missing, "table": table, "decision": decision}


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (sys.argv[1:] if argv is None else argv)]
    found = decide([json.loads(p.read_text(encoding="utf-8")) for p in paths])
    for row in found["table"]:
        print(f"{row['candidate']}  {row['size']:>7}  {row['platform']:<8} {row['state']:<12} "
              f"{'; '.join(row['problems']) or 'inside the envelope'}")
    print(f"cells: {found['cells']}, missing or failed: {found['cells_missing_or_failed']}")
    print(f"decision: {found['decision']}")
    return 0 if not found["decision"].startswith("none") else 1


if __name__ == "__main__":
    raise SystemExit(main())