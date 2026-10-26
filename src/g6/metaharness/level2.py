# SPDX-License-Identifier: Apache-2.0
"""Level 2: learn which proposer strategy to use (META_HARNESS_PLAN.md Phase 7; CR section C5, Level 2).

A bandit over the strategies a user gives it, credited only by promotions a person approved: a strategy's credit is the
number of its proposals that became an ``activate`` row in the profile store, out of the number it proposed. A
recommendation nobody approved earns nothing, and neither does a demoted version. There is no level 3: nothing here
learns how this credit is computed, and the strategies stay data the user wrote.

The choice is an upper-confidence bound (UCB1) on the approval rate, with untried strategies first; it is deterministic,
so a run can be replayed.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from g6.metaharness.propose import Strategy
from g6.metaharness.store import _resolve


def credit(mh: Any) -> dict[str, dict[str, int]]:
    """Per strategy: proposals made, and proposals a person activated (and the monitor has not demoted since)."""
    proposals = mh.ledger.rows("proposal")
    decisions = {r["_id"]: r for r in mh.ledger.rows("decision")}
    versions = mh.store.versions()
    # a demote row names where it moved to; the demoted version is the one the head stood for just before it
    demoted = {_resolve(versions, i - 1)["version"] for i, v in enumerate(versions) if v["kind"] == "demote" and i}
    activated_digests = set()
    for v in versions:
        if v["kind"] == "activate" and v["version"] not in demoted:
            decision = decisions.get(v["decision_digest"])
            if decision:
                activated_digests.add(decision["proposal_digest"])
    out: dict[str, dict[str, int]] = {}
    for row in proposals:
        entry = out.setdefault(row.get("strategy", "?"), {"proposed": 0, "approved": 0})
        entry["proposed"] += 1
        entry["approved"] += row["_id"] in activated_digests
    return out


def choose(strategies: Sequence[Strategy], mh: Any) -> Strategy:
    """The strategy to try next: an untried one first, else the highest UCB1 bound on its approval rate."""
    table = credit(mh)
    total = sum(v["proposed"] for v in table.values()) or 1
    best, best_score = strategies[0], -math.inf
    for strategy in strategies:
        row = table.get(strategy.name, {"proposed": 0, "approved": 0})
        if row["proposed"] == 0:
            return strategy
        score = row["approved"] / row["proposed"] + math.sqrt(2 * math.log(total) / row["proposed"])
        if score > best_score:
            best, best_score = strategy, score
    return best
