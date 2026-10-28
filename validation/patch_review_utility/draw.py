# SPDX-License-Identifier: Apache-2.0
"""The draw order (PROTOCOL.md, "The draw, fixed now"), over ``{instance_id, repo}`` rows the owner supplies. It reads
no dataset itself.

The excluded ids are removed first. Within each repository the ids are sorted and then shuffled, by one
``random.Random(seed)`` used for the repositories in name order. Repositories then take turns in proportion to their
pool: each next task comes from the repository whose share of the tasks drawn so far is furthest below its share of
the pool (before the first task, every drawn share is zero), ties broken by repository name, a repository that has
none left being skipped. Shares are exact fractions, so a tie is a tie. ``stats.seed`` gives a level's seed;
confirmation calls ``order`` again with its own seed and every id feasibility drew added to the excluded ids.
"""
from __future__ import annotations

import random
from collections import Counter
from fractions import Fraction


def order(rows: list[dict], *, seed: int, excluded: set[str] | frozenset[str] = frozenset()) -> list[str]:
    seen: set[str] = set()
    by: dict[str, list[str]] = {}
    for row in rows:
        task = row["instance_id"]
        if task in seen:
            raise ValueError(f"{task} appears twice in the pool")
        seen.add(task)
        if task not in excluded:
            by.setdefault(row["repo"], []).append(task)
    rng = random.Random(seed)
    queues = {}
    for name in sorted(by):
        ids = sorted(by[name])
        rng.shuffle(ids)
        queues[name] = ids
    total = sum(len(ids) for ids in queues.values())
    share = {name: Fraction(len(ids), total) for name, ids in queues.items()}
    drawn: Counter[str] = Counter()
    found: list[str] = []
    while len(found) < total:
        k = len(found)
        best = min((name for name in sorted(queues) if drawn[name] < len(queues[name])),
                   key=lambda name: (-(share[name] - (Fraction(drawn[name], k) if k else 0)), name))
        found.append(queues[best][drawn[best]])
        drawn[best] += 1
    return found
