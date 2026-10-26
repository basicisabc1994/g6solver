# SPDX-License-Identifier: Apache-2.0
"""Racing: rank candidate profiles on the selection split, so the held-out split is spent once, on the winner
(frontier design §4.2, RC1).

The selection cases are run in blocks, in order. The base runs once per case; each live candidate is paired against it.
After each block the candidates with the fewest net wins (wins − losses against the base) are dropped, until one is
left or the split ends. A block the budget cut short is discarded, so every live candidate is compared on the same
cases. The winner is the leader, if it beat the base on net; a candidate equal to the base never wins. Racing reads
only the selection split, and its result decides nothing: the winner still goes to the gate on the held-out split.
"""
from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.learning import TheoryStore
from g6.metaharness.evaluate import _arm
from g6.metaharness.executor import ExecutorFactory
from g6.metaharness.schema import Profile, Workflow


@dataclass
class Race:
    ranking: list[dict[str, Any]]                        # best first: candidate_digest, wins, losses, n, dropped_at_block
    winner: Profile | None
    cases_used: int
    calls_used: int
    stopped: str = ""
    blocks: list[int] = field(default_factory=list)      # the live candidates after each completed block


def race(workflow: Workflow, factory: ExecutorFactory, base: Profile, candidates: Sequence[Profile], *, block: int = 10,
         keep: float = 0.5, budget: int, theories: TheoryStore | None = None,
         clock: Callable[[], float] = time.monotonic, artifacts: Any = None) -> Race:
    """Successive halving of ``candidates`` against ``base`` on ``workflow.suite.selection``; see the module docstring."""
    if block < 1 or not 0 < keep <= 1:
        raise ValueError("block must be >= 1 and keep in (0, 1]")
    base_digest = base.digest()
    stats = [{"candidate_digest": c.digest(), "wins": 0, "losses": 0, "n": 0, "dropped_at_block": None}
             for c in candidates]
    live = [i for i, c in enumerate(candidates) if c.digest() != base_digest]
    base_ok: dict[str, bool] = {}
    calls, used, completed, stopped, sizes = 0, 0, 0, "", []
    cases = workflow.suite.selection

    def run(profile: Profile, case: Any, arm: str) -> bool:
        nonlocal calls
        record = _arm(workflow, factory, profile, case, arm, theories=theories, clock=clock, artifacts=artifacts)
        calls += record.model_calls
        return record.success

    for start in range(0, len(cases), block):
        if not live:                                     # a last survivor races on to the end of the split (round 1)
            break
        snapshot, chunk = copy.deepcopy(stats), cases[start:start + block]
        for case in chunk:
            for profile_index in (None, *live):
                if calls >= budget:
                    stopped = f"the selection budget is spent ({calls} of {budget} model calls)"
                    break
                if profile_index is None:
                    if case.case_id not in base_ok:
                        base_ok[case.case_id] = run(base, case, "base")
                    continue
                ok, row = run(candidates[profile_index], case, "candidate"), stats[profile_index]
                row["n"] += 1
                row["wins"] += ok and not base_ok[case.case_id]
                row["losses"] += base_ok[case.case_id] and not ok
            if stopped:
                break
        if stopped:
            stats = snapshot                             # a cut-short block is discarded: equal cases for every one
            break
        completed += 1
        used += len(chunk)
        live.sort(key=lambda i: (stats[i]["wins"] - stats[i]["losses"], -i), reverse=True)
        kept = max(1, math.ceil(keep * len(live)))
        for i in live[kept:]:
            stats[i]["dropped_at_block"] = completed
        live = live[:kept]
        sizes.append(len(live))
    order = sorted(range(len(candidates)), key=lambda i: (stats[i]["dropped_at_block"] is None,
                                                          stats[i]["dropped_at_block"] or 0,
                                                          stats[i]["wins"] - stats[i]["losses"], -i), reverse=True)
    # A race the budget cut short while candidates were still live has no winner: a leader on fewer cases than the
    # race was set to use is not confirmed (it would spend the held-out split on a half-raced choice).
    leader = live[0] if live and completed and not (stopped and len(live) > 1) else None
    winner = (candidates[leader] if leader is not None and stats[leader]["wins"] > stats[leader]["losses"] else None)
    return Race([stats[i] for i in order], winner, used, calls, stopped, sizes)
