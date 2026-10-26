# SPDX-License-Identifier: Apache-2.0
"""Weighted voting over candidate answers. Pure: no model calls, no terminal states."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field


def normalise(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".!?")


@dataclass(frozen=True)
class Candidate:
    answer: str
    weight: float = 1.0
    verified: bool = False       # True only when an independent check accepted this answer


@dataclass
class VoteResult:
    winner: str | None
    weight: float
    total: float
    has_majority: bool           # winner holds more than half the considered weight, abstentions included
    verified_exist: bool         # if True, only verified candidates were counted
    groups: list[tuple[str, float, int]] = field(default_factory=list)   # (answer, weight, count)
    abstained: float = 0.0       # weight of candidates with no answer (a failed or unparseable call)


def weighted_vote(candidates: list[Candidate], *,
                  equivalent: Callable[[str, str], bool] | None = None) -> VoteResult:
    """Verified candidates dominate: when any exists, unverified ones are not counted at all.

    Non-finite or negative weights are ignored. An empty answer is an abstention: it wins nothing but
    counts against a majority of unverified answers, so one survivor of n calls is no majority (audit
    023 RH-004). Without a strict majority the caller must treat the winner as a plurality, not a consensus.
    """
    same = equivalent or (lambda a, b: normalise(a) == normalise(b))
    weighed = [c for c in candidates if math.isfinite(c.weight) and c.weight >= 0]
    usable = [c for c in weighed if c.answer.strip()]
    abstained = sum(c.weight for c in weighed if not c.answer.strip())
    verified_exist = any(c.verified for c in usable)
    considered = [c for c in usable if c.verified] if verified_exist else usable
    groups: list[list] = []
    for c in considered:
        for g in groups:
            if same(g[0], c.answer):
                g[1] += c.weight
                g[2] += 1
                break
        else:
            groups.append([c.answer, c.weight, 1])
    total = sum(g[1] for g in groups)
    if not groups:
        return VoteResult(None, 0.0, 0.0, False, verified_exist, abstained=abstained)
    best = max(groups, key=lambda g: (g[1], g[2]))
    ties = [g for g in groups if (g[1], g[2]) == (best[1], best[2])]
    winner = best[0] if len(ties) == 1 else None
    against = total + (0.0 if verified_exist else abstained)
    return VoteResult(winner, best[1], total, winner is not None and total > 0 and best[1] * 2 > against,
                      verified_exist, [tuple(g) for g in groups], abstained)
