# SPDX-License-Identifier: Apache-2.0
"""The pool ledger: which case ids are already spent.

Every ``splits.json`` under ``docs/validation/results/`` (registered studies: V6, V6b, later ones) and
``docs/validation/rounds/`` (development rounds) lists the ids it drew, as ``{"splits": {key: {split: [ids]}}}``. A
new draw removes all of them, so no case is ever used by two studies. Development splits are drawn once (round 1) and
reused by later development rounds; a registration always draws fresh.
"""
from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path

OSS = Path(__file__).resolve().parents[2]
RESULTS = OSS / "docs" / "validation" / "results"
ROUNDS = OSS / "docs" / "validation" / "rounds"


def used_ids(key: str) -> set[str]:
    """Every id any registered study or development round drew for workflow ``key``, in any split."""
    used: set[str] = set()
    for folder in (RESULTS, ROUNDS):
        for path in sorted(folder.glob("*/splits.json")) if folder.is_dir() else ():
            splits = json.loads(path.read_text(encoding="utf-8")).get("splits", {})
            used |= {i for ids in (splits.get(key) or {}).values() for i in ids}
    return used


def draw(key: str, pool: Sequence[str], sizes: Mapping[str, int], *, seed: int, label: str) -> dict[str, list[str]]:
    """The pool minus every used id, sorted, shuffled by a seed named for ``label``, then cut in order into ``sizes``.
    Raises ``ValueError`` when too few fresh ids remain."""
    fresh = sorted(set(pool) - used_ids(key))
    need = sum(sizes.values())
    if len(fresh) < need:
        raise ValueError(f"{key}: {len(fresh)} fresh ids, {need} needed for {dict(sizes)}")
    random.Random(f"frontier:{seed}:{label}:{key}").shuffle(fresh)
    out, at = {}, 0
    for split, size in sizes.items():
        out[split] = fresh[at:at + size]
        at += size
    return out
