# SPDX-License-Identifier: Apache-2.0
"""V9's draw order (PROTOCOL.md, "The draw, fixed now"), on made-up task ids and repository names only.

Development fixtures only: no benchmark row, id or file is read here."""
from __future__ import annotations

from collections import Counter

import pytest

from patch_review_utility import draw

POOL = ([{"instance_id": f"big-{i:02d}", "repo": "org/big"} for i in range(6)]
        + [{"instance_id": f"small-{i}", "repo": "org/small"} for i in range(3)]
        + [{"instance_id": f"tie-{i}", "repo": "org/tie"} for i in range(3)])


def test_the_order_is_fixed_by_the_seed_and_ignores_the_rows_order():
    one = draw.order(POOL, seed=17)
    assert one == draw.order(list(reversed(POOL)), seed=17) and one != draw.order(POOL, seed=18)
    assert sorted(one) == sorted(r["instance_id"] for r in POOL) and len(set(one)) == len(POOL)


def test_repositories_take_turns_in_proportion_to_their_pool_with_ties_by_name():
    got = draw.order(POOL, seed=5)
    repo = {r["instance_id"]: r["repo"] for r in POOL}
    assert repo[got[0]] == "org/big"                                   # the largest share goes first
    assert [repo[t] for t in got[1:3]] == ["org/small", "org/tie"]     # equal shares: by name
    for k in range(1, len(got) + 1):                                   # never more than one task off its share
        drawn = Counter(repo[t] for t in got[:k])
        for name, size in Counter(repo.values()).items():
            assert abs(drawn[name] - k * size / len(POOL)) < 1 + 1e-9


def test_within_a_repository_the_ids_are_sorted_then_shuffled():
    got = [t for t in draw.order(POOL, seed=5) if t.startswith("big-")]
    assert sorted(got) == [f"big-{i:02d}" for i in range(6)] and got != sorted(got)


def test_excluded_ids_never_enter_and_confirmation_continues_over_the_rest():
    excluded = {"big-00", "tie-1"}
    first = draw.order(POOL, seed=5, excluded=excluded)
    assert not excluded & set(first)
    feasibility = first[:4]
    later = draw.order(POOL, seed=99, excluded=excluded | set(feasibility))
    assert not set(later) & set(feasibility) and len(later) == len(POOL) - 2 - 4
    with pytest.raises(ValueError):
        draw.order(POOL + [POOL[0]], seed=1)                           # an id twice in the pool
