# SPDX-License-Identifier: Apache-2.0
"""The frozen comparison (PLAN.md, 2026-10-31): the loop against plain Claude Code on bugs it was not tuned on.

Reads saved rows only, so it uses no model and no container. Uplift is the loop's pass rate minus the other arm's on the
same bugs and repeats. Every comparison is printed on the maintainers' tests and on the amended tests.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from math import comb
from pathlib import Path
from typing import Any

import run as we
import wecommon as c

SCORES = ("maintainers", "amended")
DETERMINED = ("pydantic-13243", "black-5148", "click-3434", "click-3404", "starlette-3189", "fastapi-14616", "fastapi-13920")
TARGET = 0.95
Rows = Mapping[tuple[str, str, int], Mapping[str, bool | None]]


def sign_test(wins: int, losses: int) -> float:
    """The exact one-sided p of at least ``wins`` of the pairs that differ going the loop's way, at even odds."""
    n = wins + losses
    return sum(comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def saved(root: Path) -> dict[tuple[str, str, int], dict[str, bool | None]]:
    """Each saved run's two verdicts; None is missing, and a missing verdict is neither a pass nor a failure. A finished
    run with no independent verdict has none on either score. A bug with no amendment has one score, and an amended bug
    has an amended verdict only if it was scored for this run on the diff as it stands."""
    rows: dict[tuple[str, str, int], dict[str, bool | None]] = {}
    for path in (root / "loop").glob("*-*.json"):
        row, again = json.loads(path.read_text("utf-8")), root / "loop" / "amended" / path.name
        passes = None if row.get("status") == "finished" and row.get("failing") is None else bool(row["passes"])
        amended = passes
        diff = we.amendment(row["task"])
        if diff:
            kept = json.loads(again.read_text("utf-8")) if again.is_file() else {}
            same = (kept.get("amendment_sha256"), kept.get("run")) == (c.sha256_file(diff), row.get("run"))
            amended = kept.get("amended_passes") if same else None
        rows[(row["arm"], row["task"], row["repeat"])] = {"maintainers": passes, "amended": amended}
    return rows


def compare(rows: Rows, loop_arm: str, other: str, tasks: Iterable[str], repeats: Iterable[int], score: str) -> dict[str, Any]:
    """One arm against another on the same bugs and repeats; a pair with a run missing counts neither way."""
    pairs = [(rows.get((loop_arm, t, r), {}).get(score), rows.get((other, t, r), {}).get(score))
             for t in tasks for r in repeats]
    seen = [(a, b) for a, b in pairs if a is not None and b is not None]
    wins, losses = sum(a and not b for a, b in seen), sum(b and not a for a, b in seen)
    passed, base = sum(bool(a) for a, _ in seen), sum(bool(b) for _, b in seen)
    return {"loop": loop_arm, "other": other, "score": score, "pairs": len(seen), "missing": len(pairs) - len(seen),
            "loop_passed": passed, "other_passed": base, "uplift": (passed - base) / len(seen) if seen else None,
            "wins": wins, "losses": losses, "p": sign_test(wins, losses)}


def judged(results: Sequence[Mapping[str, Any]]) -> bool:
    """The frozen rule: with no pair missing, more passes than every other arm, and p < 0.05 against each."""
    return bool(results) and all(r["missing"] == 0 and r["loop_passed"] > r["other_passed"] and r["p"] < 0.05 for r in results)


def report(rows: Rows, name: str, tasks: Sequence[str], repeats: Sequence[int], loop_arm: str = "M9",
           others: Sequence[str] = ("A", "T")) -> None:
    for score in SCORES:
        results = [compare(rows, loop_arm, other, tasks, repeats, score) for other in others]
        for r in results:
            uplift = "n/a" if r["uplift"] is None else f"{r['uplift']:+.1%}"
            print(f"{name:22} {score:11} {loop_arm} {r['loop_passed']:3}/{r['pairs']:<3} {r['other']:2} {r['other_passed']:3}/{r['pairs']:<3}"
                  f" uplift {uplift:>7}  wins {r['wins']:2} losses {r['losses']:2}  p {r['p']:.4f}"
                  + (f"  MISSING {r['missing']}" if r["missing"] else ""))
        rate = results[0]["loop_passed"] / results[0]["pairs"] if results[0]["pairs"] else 0.0
        print(f"{name:22} {score:11} frozen rule: {'met' if judged(results) else 'not shown'}; "
              f"95% target: {'met' if rate >= TARGET and not results[0]['missing'] else 'not met'} ({rate:.1%})")


def main() -> None:
    root = c.archive_root() / "round3"
    rows = saved(root)
    fresh = [t.id for t in we.load_tasks(we.TASKS_FRESH)]
    train = [t.id for t in we.load_tasks() if t.id not in we.R3_HELD]
    report(rows, "fresh, all eleven", fresh, (1, 2))
    report(rows, "fresh, determined 7", [t for t in fresh if t in DETERMINED], (1, 2))
    report(rows, "held-out, second look", list(we.R3_HELD), (1, 2, 3, 4, 5))
    report(rows, "training, tuned", train, (1, 2), loop_arm="L9", others=("A",))


if __name__ == "__main__":
    main()
