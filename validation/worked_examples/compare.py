# SPDX-License-Identifier: Apache-2.0
"""The frozen comparison (PLAN.md, 2026-10-31): the loop against plain Claude Code on bugs it was not tuned on.

Reads saved rows only, so it uses no model and no container. Uplift is the loop's pass rate minus the other arm's on the
same bugs and repeats. Every comparison is printed on the maintainers' tests and on the amended tests.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from math import comb
from pathlib import Path
from typing import Any

import run as we
import wecommon as c
from g6.metaharness.method_rca import compare_methods

SCORES =("maintainers", "amended")
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
        rows[(row["arm"], row["task"], row["repeat"])] = {"maintainers": passes, "amended": amended, "said": said_of(root, row)}
    return rows


def said_of(root: Path, row: Mapping[str, Any]) -> str:
    """What the system said of its own run: "done" for an arm with no loop, "clean" for a loop that ended with nothing failing, and
    "unsure" for any other end, a missing record included. It never decides the rule; it is how the report asks if "done" can be
    trusted."""
    if row.get("arm", "")[:1] not in ("L", "M", "R"):
        return "done"
    path = root / str(row.get("run")) / "run.json"
    status = (json.loads(path.read_text("utf-8")).get("repairs") or {}).get("status") if path.is_file() else None
    return "clean" if status == "no_counterexample_in_domain" else "unsure"


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


def by_bug(rows: Rows, loop_arm: str, other: str, tasks: Iterable[str], repeats: Iterable[int], score: str) -> dict[str, int]:
    """The bugs on which the loop passed more runs, fewer or the same: repeats of one bug are not independent, so a p-value
    overstates the evidence. A bug with a run missing counts neither way."""
    repeats = list(repeats)
    counts = {"better": 0, "worse": 0, "same": 0}
    for task in tasks:
        ours, theirs = ([rows.get((arm, task, r), {}).get(score) for r in repeats] for arm in (loop_arm, other))
        if None in ours or None in theirs:
            continue
        ahead = sum(map(bool, ours)) - sum(map(bool, theirs))
        counts["better" if ahead > 0 else "worse" if ahead < 0 else "same"] += 1
    return counts


def interval(rows: Rows, loop_arm: str, other: str, tasks: Iterable[str], repeats: Iterable[int],
             score: str) -> tuple[float, float] | None:
    """The 95% interval of the uplift from G6's ``compare_methods``, resampling whole bugs. It is read beside the rule, not by it."""
    pairs, clusters = [], []
    for task in tasks:
        for repeat in repeats:
            ours, theirs = (rows.get((arm, task, repeat), {}).get(score) for arm in (loop_arm, other))
            if ours is not None and theirs is not None:
                pairs.append((float(theirs), float(ours)))
                clusters.append(task)
    return tuple(compare_methods(pairs, clusters=clusters)["ci95"]) if pairs else None


def trust(rows: Rows, arm: str, tasks: Iterable[str], repeats: Iterable[int], score: str) -> dict[str, dict[str, int]]:
    """For each thing the arm said of its runs, how often that run was right: the owner's own question, when it says done, is it?"""
    table: dict[str, dict[str, int]] = {}
    repeats = list(repeats)
    for task in tasks:
        for repeat in repeats:
            row = rows.get((arm, task, repeat), {})
            if row.get(score) is not None:
                cell = table.setdefault(str(row.get("said", "done")), {"right": 0, "wrong": 0})
                cell["right" if row[score] else "wrong"] += 1
    return table


def judged(results: Sequence[Mapping[str, Any]]) -> bool:
    """The frozen rule: with no pair missing, more passes than every other arm, and p < 0.05 against each."""
    return bool(results) and all(r["missing"] == 0 and r["loop_passed"] > r["other_passed"] and r["p"] < 0.05 for r in results)


def report(rows: Rows, name: str, tasks: Sequence[str], repeats: Sequence[int], loop_arm: str = "M9",
           others: Sequence[str] = ("A", "T")) -> None:
    for score in SCORES:
        results = [compare(rows, loop_arm, other, tasks, repeats, score) for other in others]
        for r in results:
            uplift = "n/a" if r["uplift"] is None else f"{r['uplift']:+.1%}"
            bugs = by_bug(rows, loop_arm, r["other"], tasks, repeats, score)
            span = interval(rows, loop_arm, r["other"], tasks, repeats, score)
            print(f"{name:22} {score:11} {loop_arm} {r['loop_passed']:3}/{r['pairs']:<3} {r['other']:2} {r['other_passed']:3}/{r['pairs']:<3}"
                  f" uplift {uplift:>7}  wins {r['wins']:2} losses {r['losses']:2}  p {r['p']:.4f}"
                  f"  bugs better {bugs['better']} worse {bugs['worse']} same {bugs['same']}"
                  f"  95% by bug {f'{span[0]:+.0%} to {span[1]:+.0%}' if span else 'n/a'}"
                  + (f"  MISSING {r['missing']}" if r["missing"] else ""))
        for arm in (loop_arm, *others):
            for said, cell in sorted(trust(rows, arm, tasks, repeats, score).items()):
                total = cell["right"] + cell["wrong"]
                print(f"{name:22} {score:11} {arm} said {said}: right {cell['right']} wrong {cell['wrong']} ({cell['right'] / total:.0%})")
        rate = results[0]["loop_passed"] / results[0]["pairs"] if results[0]["pairs"] else 0.0
        print(f"{name:22} {score:11} frozen rule: {'met' if judged(results) else 'not shown'}; "
              f"95% target: {'met' if rate >= TARGET and not results[0]['missing'] else 'not met'} ({rate:.1%})")


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loop", default="M9", help="the frozen loop's arm")
    ap.add_argument("--trained", help="stage 3: the arm G6's training produced, as M<n>, judged against --loop as well")
    args = ap.parse_args(argv)
    root = c.archive_root() / "round3"
    rows = saved(root)
    fresh = [t.id for t in we.load_tasks(we.TASKS_FRESH)]
    unseen = [t.id for t in we.load_tasks(we.TASKS_UNSEEN)]
    train = [t.id for t in we.load_tasks() if t.id not in we.R3_HELD]
    report(rows, "stage 1, all eleven", fresh, (1, 2), loop_arm=args.loop)
    report(rows, "stage 1, determined 7", [t for t in fresh if t in DETERMINED], (1, 2), loop_arm=args.loop)
    report(rows, "stage 2, new repos", unseen, (1, 2), loop_arm=args.loop)
    report(rows, "held-out, second look", list(we.R3_HELD), (1, 2, 3, 4, 5), loop_arm=args.loop)
    report(rows, "training, tuned", train, (1, 2), loop_arm=f"L{args.loop[1:]}", others=("A",))
    if args.trained:
        others = (args.loop, "A", "T")
        report(rows, "stage 3, new repos", unseen, (1, 2), loop_arm=args.trained, others=others)
        report(rows, "stage 3, stage 1 bugs", fresh, (1, 2), loop_arm=args.trained, others=others)


if __name__ == "__main__":
    main()
