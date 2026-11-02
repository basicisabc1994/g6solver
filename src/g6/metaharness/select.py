# SPDX-License-Identifier: Apache-2.0
"""Racing: rank candidate profiles on the selection split, so the held-out split is spent once, on the winner
(frontier design §4.2, RC1).

The selection cases are run in blocks, one case of each group in turn: cases of one group share their source and rise or
fall together, so a block of one group says little and on a real pool is often all ties. The base runs once per case; each
live candidate is paired against it. After each block the candidates with the fewest net wins (wins − losses against the
base) are dropped, until one is left or the split ends. A tie is no evidence, so a candidate level with the last one kept
stays, and a race of ties runs every candidate on every case. A block the budget cut short is discarded, so every live
candidate is compared on the same cases. The winner is the leader, if it beat the base on net; a candidate equal to the
base never wins. Racing reads only the selection split, and its result decides nothing: the winner still goes to the gate
on the held-out split.
"""
from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any

from g6.learning import TheoryStore
from g6.metaharness.evaluate import _arm
from g6.metaharness.executor import ExecutorFactory
from g6.metaharness.schema import Profile, SuiteCase, Workflow


@dataclass
class Race:
    ranking: list[dict[str, Any]]                        # best first: candidate_digest, wins, losses, n, dropped_at_block
    winner: Profile | None
    cases_used: int
    calls_used: int
    stopped: str = ""
    blocks: list[int] = field(default_factory=list)      # the live candidates after each completed block


def race_calls(cases: int, candidates: int) -> int:
    """The most runs a race of ``candidates`` on ``cases`` can take: the base once per case, and every candidate on each if
    they all tie."""
    return cases * (1 + candidates)


def _in_turns(cases: Sequence[SuiteCase]) -> list[SuiteCase]:
    """One case of each group in turn. A case with no group is its own, so cases with none keep their order."""
    groups: dict[Any, list[SuiteCase]] = {}
    for case in cases:
        groups.setdefault(case.meta.get("group") or case.case_id, []).append(case)
    return [case for turn in zip_longest(*groups.values()) for case in turn if case is not None]


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
    cases = _in_turns(workflow.suite.selection)

    def run(profile: Profile, case: Any, arm: str) -> bool:
        nonlocal calls
        record = _arm(workflow, factory, profile, case, arm, theories=theories, clock=clock, artifacts=artifacts)
        calls += record.model_calls
        return record.success

    def net(i: int) -> int:
        return stats[i]["wins"] - stats[i]["losses"]

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
        live.sort(key=lambda i: (net(i), -i), reverse=True)
        last_kept = net(live[max(1, math.ceil(keep * len(live))) - 1])
        for i in live:
            if net(i) < last_kept:
                stats[i]["dropped_at_block"] = completed
        live = [i for i in live if net(i) >= last_kept]
        sizes.append(len(live))
    order = sorted(range(len(candidates)), key=lambda i: (stats[i]["dropped_at_block"] is None,
                                                          stats[i]["dropped_at_block"] or 0, net(i), -i), reverse=True)
    # A race the budget cut short while candidates were still live has no winner: a leader on fewer cases than the
    # race was set to use is not confirmed (it would spend the held-out split on a half-raced choice).
    leader = live[0] if live and completed and not (stopped and len(live) > 1) else None
    winner = (candidates[leader] if leader is not None and stats[leader]["wins"] > stats[leader]["losses"] else None)
    return Race([stats[i] for i in order], winner, used, calls, stopped, sizes)
