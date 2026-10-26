# SPDX-License-Identifier: Apache-2.0
"""Guarded prune / no-prune advice for fresh candidate configurations.

Library-level home of the guards the MCP ``advise_pruning`` tool applies, so they can be used
and tested without an MCP session. Every guard can only turn ``prune`` off; none turns it on.
The caller alone decides whether to skip an evaluation.
"""
from __future__ import annotations

import math
from typing import Any

from .domain import ConcreteDomain
from .library import STALE_REFUSE, STALE_WARN, TOP_STATUS, Library

SUPPORT_GUARD = 0.25  # max normalised L-inf distance to data for pruning
# The re-evaluation that restores an entry's evidence: the Library has no ``revalidate``; the server's tool runs this.
_REEVALUATE = "re-evaluate the entry with Library.evaluate(..., force=True) (the server's revalidate tool)"


def support_distance(dom: ConcreteDomain, cfg: dict[str, Any]) -> float:
    v = dom.vector(cfg)
    best = math.inf
    for rec in dom.records:
        w = dom.vector(rec.config)
        d = max((abs(a - b) for a, b in zip(v, w)), default=math.inf)
        best = min(best, d)
    return best


def advise_pruning(library: Library, entry_id: str, dom: ConcreteDomain,
                   candidates: list[dict[str, Any]], incumbent: float | None = None) -> dict[str, Any]:
    entry = library.get(entry_id)
    notes: list[str] = []

    may_prune = entry.status == TOP_STATUS
    if entry.status == "provisional":
        notes.append("provisional entry: sound but value unconfirmed; "
                     "pruning disabled, bounds shown for information")
    elif entry.status in ("rejected", "degenerate"):
        notes.append(f"{entry.status} entry: pruning disabled; treat "
                     f"bounds as untrusted")

    if entry_id in library.loaded_unverified:
        may_prune = False
        notes.append(f"status '{entry.status}' was read from a state file, not re-established; "
                     f"{_REEVALUATE} before pruning")

    growth = entry.staleness(dom)
    if growth >= STALE_REFUSE:
        may_prune = False
        notes.append(f"stale: trace grew {growth:.0%} since evaluation; "
                     f"{_REEVALUATE} to restore pruning")
    elif growth > STALE_WARN:
        notes.append(f"staleness caveat: trace grew {growth:.0%} since "
                     f"evaluation")

    inc = incumbent if incumbent is not None else dom.score(dom.best().objective)
    fitted = library.instantiate(entry_id).fit(dom)
    out = []
    for cfg in candidates:
        missing = [p for p in dom.params if p not in cfg]
        if missing:
            out.append({"config": cfg, "error": f"missing params: {missing}"})
            continue
        oor = [p for p, s in dom.params.items() if not s.validate(cfg[p])]
        if oor:
            # Outside the calibrated domain: never pruned, and no bound is computed on it
            # (audit 001, F-06: the archive reported the range error but could still prune).
            out.append({"config": cfg, "covered": False, "bound": None, "prune": False,
                        "out_of_range": oor, "guards": [f"out_of_range: {', '.join(oor)}"]})
            continue
        b = fitted.bound(cfg)
        row: dict[str, Any] = {
            "config": cfg,
            "covered": fitted.covers(cfg),
            "bound": None if b == float("inf") else b,
            "prune": may_prune and fitted.prune(cfg, inc),
            "guards": [],
        }
        dist = support_distance(dom, cfg)
        if dist > SUPPORT_GUARD:
            row["prune"] = False
            row["guards"].append(
                f"insufficient_support: distance to nearest data "
                f"{dist:.3f} > {SUPPORT_GUARD}")
        if not may_prune and fitted.covers(cfg):
            row["guards"].append("pruning_disabled_by_entry_status_or_staleness")
        ucb = getattr(fitted, "ucb", None)
        if callable(ucb):
            u = ucb(cfg)
            row["ucb"] = None if u == float("inf") else u
        out.append(row)
    return {"entry_id": entry_id, "entry_status": entry.status, "may_prune": may_prune,
            "incumbent": inc, "notes": notes, "candidates": out}
