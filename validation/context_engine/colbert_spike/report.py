# SPDX-License-Identifier: Apache-2.0
"""The persistent-meaning spike's table and decision (protocol 05).

    python validation/context_engine/colbert_spike/report.py results/*.json

Every cell of the declared grid (candidate x size x platform x measure) stays in the denominator: a cell with no
result, an unavailable or failed run, a missing measure or a measure that is not a finite number is listed and fails
the envelope for its candidate. The decision is the cheapest candidate inside the whole envelope on every platform,
or ``none: repair and rerun``: always that when the protocol's thresholds are not the frozen ones (their digest is
pinned here), when a run records no pin, an incomplete one or another one than the rest (a stand-in encoder's result
cannot otherwise be told from a pinned model's), or when more cells are missing or failed than the frozen bar allows.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [p for p in (str(HERE), str(HERE.parent)) if p not in sys.path]

import candidates  # noqa: E402
import frozen  # noqa: E402

PROTOCOL = HERE.parents[2] / "docs" / "measurements" / "context-engine" / "05-scale-and-meaning.protocol.md"
#: The digest of row 05's frozen thresholds. A protocol file whose thresholds differ decides nothing.
THRESHOLDS_SHA256 = "f9fd3ebd4bd593bcf0ee4e1ed350e05f1aeb98ec5f95b800812bb0935f88116d"
NONE = "none: repair and rerun"


def thresholds() -> dict:
    return frozen.block(PROTOCOL)["thresholds"]


def cell_problems(result: dict | None, size: int, limits: dict) -> list[str]:
    """Why one (candidate, size, platform) result is outside the envelope; ``[]`` when it is inside. Every measure
    is a finite number before a bar is applied to it."""
    if result is None:
        return ["no result"]
    if result.get("state") != "ran":
        return [f"{result.get('state', 'unknown')}: {result.get('reason', '')}"[:200]]
    m, out = result.get("measures") or {}, []
    out += [f"missing measure {name}" for name in candidates.MEASURES if name not in m]
    out += [f"{name} is not a finite number" for name in candidates.MEASURES if name in m and not frozen.finite(m[name])]
    checks = (("peak_rss_bytes", lambda v: v <= limits["peak_rss_bytes_max"]),
              ("disk_bytes", lambda v: v <= limits["disk_bytes_per_100000_passages_max"] * size / 100_000),
              ("warm_p95_s", lambda v: v <= limits["warm_p95_seconds_max"]),
              ("re_encoded_after_restart", lambda v: v == limits["passages_re_encoded_after_restart"]),
              ("partial_after_kill", lambda v: v == limits["partial_passages_after_kill"]))
    out += [f"{name} outside the envelope" for name, ok in checks if frozen.finite(m.get(name)) and not ok(m[name])]
    if size == candidates.SIZES[0] and frozen.finite(m.get("recall_at_10")) \
            and m["recall_at_10"] < limits["recall_at_10_vs_exhaustive_maxsim_min"]:
        out.append("recall_at_10 outside the envelope")
    return out


def pin_problems(results: list[dict]) -> list[str]:
    """Why the runs may not be compared: a run that records no pin or lacks a required one, runs under different
    pins, or a run that does not record the frozen thresholds it ran under."""
    ran = [r for r in results if r.get("state") == "ran"]
    out = []
    lacking = sum(1 for r in ran if not isinstance(r.get("pins"), dict)
                  or any(not r["pins"].get(name) for name in candidates.REQUIRED_PINS))
    if lacking:
        out.append(f"{lacking} run(s) do not record every required pin ({', '.join(candidates.REQUIRED_PINS)})")
    if len({frozen.digest(r.get("pins")) for r in ran}) > 1:
        out.append("the runs were made under different pins")
    if any(r.get("thresholds_sha256") != THRESHOLDS_SHA256 for r in ran):
        out.append("a run does not record the frozen thresholds' digest it was made under")
    return out


def decide(results: list[dict], platforms: tuple[str, ...] = candidates.PLATFORMS) -> dict:
    """The table and the decision. No candidate is named unless the protocol's thresholds are the frozen ones, every
    run records the same complete pins, and every frozen threshold is met, the one on missing or failed cells
    included."""
    refused = []
    limits = thresholds()
    if frozen.digest(limits) != THRESHOLDS_SHA256:
        refused.append("the thresholds in the protocol file are not the frozen ones; a changed bar retires the protocol")
    refused += pin_problems(results)
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
    if missing > limits.get("cells_missing_or_failed", 0):
        refused.append(f"{missing} of {cells} cells are missing or failed, over the frozen bar of "
                       f"{limits.get('cells_missing_or_failed', 0)}")
    decision = min(inside, key=lambda c: (*inside[c], c)) if inside and not refused else NONE
    return {"cells": cells, "cells_missing_or_failed": missing, "table": table, "decision": decision,
            "refused_because": refused, "thresholds_sha256": frozen.digest(limits),
            "frozen_thresholds_sha256": THRESHOLDS_SHA256, "inside_the_envelope": sorted(inside)}


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (sys.argv[1:] if argv is None else argv)]
    found = decide([json.loads(p.read_text(encoding="utf-8")) for p in paths])
    for row in found["table"]:
        print(f"{row['candidate']}  {row['size']:>7}  {row['platform']:<8} {row['state']:<12} "
              f"{'; '.join(row['problems']) or 'inside the envelope'}")
    print(f"cells: {found['cells']}, missing or failed: {found['cells_missing_or_failed']}")
    print(f"thresholds: {found['thresholds_sha256']} (frozen: {found['frozen_thresholds_sha256']})")
    for why in found["refused_because"]:
        print(f"no decision: {why}")
    print(f"decision: {found['decision']}")
    return 0 if not found["decision"].startswith("none") else 1


if __name__ == "__main__":
    raise SystemExit(main())