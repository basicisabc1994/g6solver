# SPDX-License-Identifier: Apache-2.0
"""The draw order (PROTOCOL.md, "The draw, fixed now"), over ``{instance_id, repo}`` rows the owner supplies. It reads
no dataset itself.

The excluded ids are removed first. Within each repository the ids are sorted and then shuffled, by one
``random.Random(seed)`` used for the repositories in name order. Repositories then take turns in proportion to their
pool: each next task comes from the repository whose share of the tasks drawn so far is furthest below its share of
the pool (before the first task, every drawn share is zero), ties broken by repository name, a repository that has
none left being skipped. Shares are exact fractions, so a tie is a tie. ``stats.seed`` gives a level's seed;
confirmation calls ``order`` again with its own seed and every id feasibility drew added to the excluded ids. The IDs
on a published list of broken tasks are among the excluded ids (``broken.published_ids``). A task replaced before any
patch is generated, including one the task screen marks label-invalid, is skipped and logged (``replacements``).
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


def replacements(ordered: list[str], replaced: dict[str, str]) -> dict:
    """The draw order without the tasks that are replaced before any patch is generated (``replaced``: each task's
    reason, an environment that does not build, the oracle controls, or the task screen), and the log of each
    replacement: the task, why, and the next task in order that takes its place (None when none is left)."""
    tasks = [task for task in ordered if task not in replaced]
    log = []
    for index, task in enumerate(ordered):
        if task in replaced:
            later = [other for other in ordered[index + 1:] if other not in replaced and other not in
                     {item["by"] for item in log}]
            log.append({"replaced": task, "why": replaced[task], "by": later[0] if later else None})
    return {"tasks": tasks, "log": log}
