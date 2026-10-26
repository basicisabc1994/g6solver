# SPDX-License-Identifier: Apache-2.0
"""The candidate pool and the edits that move through it (frontier design §4.1, RC2 and RC3).

A ``Pool`` keeps every profile tried on the dev split with its per-case results. Its *frontier* is the members that
solve at least one dev case and whose solved cases are not a strict subset of another member's: the Pareto pool of
GEPA (Agrawal et al., 2025), so a candidate that is the only one to solve some case survives even if its total is low.
A parent is drawn from the frontier in proportion to the dev cases it solves.

An ``EditSpace`` holds the values a user declared for each profile field. Its *neighbours* of a profile change one
field to another declared value; its *merge* joins two profiles' changes from a common base, when they do not change
the same field differently. Both return plain data, which is validated as a profile like any other proposal.
"""
from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.metaharness.schema import Profile, profile_diff


@dataclass
class Pool:
    members: list[tuple[Profile, dict[str, bool]]] = field(default_factory=list)

    novelty_weight: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.novelty_weight) or self.novelty_weight < 0:
            raise ValueError("novelty_weight must be finite and nonnegative")

    def novelty(self, profile: Profile) -> float:
        own = self.results(profile)
        distances = [sum(own[k] != results[k] for k in own) / len(own)
                     for other, results in self.members
                     if own and other.digest() != profile.digest() and set(results) == set(own)]
        return sum(distances) / len(distances) if distances else 0.0

    def add(self, profile: Profile, results: Mapping[str, bool]) -> None:
        if not self.seen(profile):
            self.members.append((profile, dict(results)))

    def seen(self, profile: Profile) -> bool:
        digest = profile.digest()
        return any(p.digest() == digest for p, _ in self.members)

    def results(self, profile: Profile) -> dict[str, bool]:
        digest = profile.digest()
        return next((r for p, r in self.members if p.digest() == digest), {})

    def frontier(self) -> list[Profile]:
        """Members solving a case, minus any whose solved cases are a strict subset of another's (ties: the first)."""
        solved = [(p, frozenset(c for c, ok in r.items() if ok)) for p, r in self.members]
        out, kept = [], []
        for profile, cases in solved:
            if not cases or any(cases < other for _, other in solved) or cases in kept:
                continue
            kept.append(cases)
            out.append(profile)
        return out

    def parent(self, rng: random.Random) -> Profile:
        """A frontier member, weighted by the dev cases it solves; the first member when nothing is solved yet."""
        front = self.frontier()
        if not front:
            return self.members[0][0]
        weights = [sum(self.results(p).values()) + self.novelty_weight * self.novelty(p) for p in front]
        return rng.choices(front, weights=weights, k=1)[0]


@dataclass(frozen=True)
class EditSpace:
    space: Mapping[str, Sequence[Any]]

    def neighbours(self, profile: Profile) -> list[dict[str, Any]]:
        """Each profile that differs from ``profile`` in one declared field, set to another declared value."""
        data, out, seen = profile.model_dump(mode="json"), [], {profile.digest()}
        for key in sorted(self.space):
            for value in self.space[key]:
                candidate = {**data, key: value}
                try:
                    digest = Profile.model_validate(candidate).digest()
                except ValueError:
                    continue
                if digest not in seen:
                    seen.add(digest)
                    out.append(candidate)
        return out

    def merge(self, a: Profile, b: Profile, base: Profile) -> dict[str, Any] | None:
        """``base`` with both profiles' changes; ``None`` when they change the same field to different values."""
        da, db = profile_diff(base, a), profile_diff(base, b)
        if any(da[k]["to"] != db[k]["to"] for k in set(da) & set(db)):
            return None
        return {**base.model_dump(mode="json"), **{k: v["to"] for k, v in (*da.items(), *db.items())}}
