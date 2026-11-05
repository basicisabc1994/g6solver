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

import final_review
import run as we
import wecommon as c
from g6.metaharness.method_rca import compare_methods

SCORES =("maintainers", "amended")
DETERMINED = ("pydantic-13243", "black-5148", "click-3434", "click-3404", "starlette-3189", "fastapi-14616", "fastapi-13920")
DETERMINED_2 = ("urllib3-5347", "urllib3-5254", "charset-normalizer-772", "pygments-3204", "pygments-3158", "pluggy-809", "botocore-3852", "anyio-1424",
                "anyio-1354", "s3transfer-423", "python-dotenv-700", "pyjwt-1201", "platformdirs-666")      # the blind audit (PLAN.md, 2026-11-02)
TARGET = 0.95
TWO_PRIMARIES = 0.025       # the bar with M9 and K1 both counted (PLAN.md, 2026-11-01): beside the rule, never in place of it
EXPLORATORY = ("K0",)       # chosen after a result was read (PLAN.md, 2026-11-02): a report, never a verdict of the rule
STAGE_1_PRIMARIES = ("M9", "K1")    # the arms stage 1 tested: for any later arm its bugs are development (PLAN.md, 2026-11-02)
CLEAN = "no_counterexample_in_domain"
Rows = Mapping[tuple[str, str, int], Mapping[str, bool | None]]


def sign_test(wins: int, losses: int) -> float:
    """The exact one-sided p of at least ``wins`` of the pairs that differ going the loop's way, at even odds."""
    n = wins + losses
    return sum(comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def saved(root: Path) -> dict[tuple[str, str, int], dict[str, bool | None]]:
    """Each saved run's two verdicts; None is missing, and a missing verdict is neither a pass nor a failure. A finished
    run with no independent verdict has none on either score. A bug with no amendment has one score, and an amended bug
    has an amended verdict only if it was scored for this run on the diff as it stands. Each is read by one rule, the run's ending
    first (``wecommon.verdict``): a verdict saved under an ending the run no longer has is void."""
    rows: dict[tuple[str, str, int], dict[str, bool | None]] = {}
    archive = c.Archive(root)
    for path in (root / "loop").glob("*-*.json"):
        row, again = json.loads(path.read_text("utf-8")), root / "loop" / "amended" / path.name
        admitted = we.scored_run(archive, str(row.get("run") or ""))   # the one admission rule, from the archive alone
        passes = c.verdict(root, row, row) if admitted else None      # none for a run that may not be scored, on either score
        amended = passes
        diff = we.amendment(row["task"]) if admitted else None
        if diff:
            kept = json.loads(again.read_text("utf-8")) if again.is_file() else None
            amended = c.verdict(root, row, kept, amendment=c.sha256_file(diff))
        rows[(row["arm"], row["task"], row["repeat"])] = {"maintainers": passes, "amended": amended, "said": said_of(root, row)}
        review, end = final_review.said_of(root, row), end_of(root, row)
        if review:
            rows[(row["arm"], row["task"], row["repeat"])]["review"] = review
        if end:
            rows[(row["arm"], row["task"], row["repeat"])]["end"] = end
    return rows


def record(path: Path) -> dict[str, Any]:
    """A saved run's record, or nothing when it is missing or a killed run cut it short."""
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def said_of(root: Path, row: Mapping[str, Any]) -> str:
    """What the system said of its own run: "done" for an arm with no loop, "clean" for a loop that ended with nothing failing, and
    "unsure" for any other end, a missing record included. It never decides the rule; it is how the report asks if "done" can be
    trusted."""
    if row.get("arm", "")[:1] not in ("K", "L", "M", "R", "V"):
        return "done"
    status = (record(root / str(row.get("run")) / "run.json").get("repairs") or {}).get("status")
    return "clean" if status == CLEAN else "unsure"


def end_of(root: Path, row: Mapping[str, Any]) -> str | None:
    """How arm K's last review ended; None for any other arm. A review that says ``defect`` with no test standing behind it stops the
    loop as a clean one does (PLAN.md, 2026-11-01), so the report counts those apart on each score. It never decides the rule."""
    if row.get("arm", "")[:1] != "K":
        return None
    run = root / str(row.get("run"))
    repairs = record(run / "run.json").get("repairs") or {}
    if repairs.get("status") != CLEAN:
        return str(repairs.get("reason") or "no record")
    if (repairs.get("critic") or {}).get("verdicts", [""])[-1] != "defect":
        return "nothing found"
    rounds = repairs.get("rounds")
    check = run / f"repair_check_{rounds}_critic.json"
    if not check.is_file():
        return "a defect with no test"
    got = record(check)
    if not got:
        return "no record"
    own = "test_critic" if rounds == 1 else f"test_critic_{rounds}"     # earlier reviews' tests stay in the folder and run again
    cases = {case: said for case, said in (got.get("results") or {}).items() if case.split("::")[0].split(".")[0] == own}
    return "a defect whose test found nothing" if we.ProbeRun(got.get("exit"), cases, {}).usable \
        else "a defect whose test would not run"


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


def trust(rows: Rows, arm: str, tasks: Iterable[str], repeats: Iterable[int], score: str, key: str = "said") -> dict[str, dict[str, int]]:
    """For each thing the arm said of its runs, how often that run was right: the owner's own question, when it says done, is it?
    ``key``: whose word, the arm's or the final review's; a run the review has not spoken on is left out of its table."""
    table: dict[str, dict[str, int]] = {}
    repeats = list(repeats)
    for task in tasks:
        for repeat in repeats:
            row = rows.get((arm, task, repeat), {})
            if row.get(score) is not None and (key == "said" or key in row):
                cell = table.setdefault(str(row.get(key, "done")), {"right": 0, "wrong": 0})
                cell["right" if row[score] else "wrong"] += 1
    return table


def finished(rows: Rows, tasks: Sequence[str], arms: Sequence[str], repeats: Sequence[int]) -> list[str]:
    """The bugs that finished, in the frozen order: every arm ran every repeat, and the list stops at the first bug that did not. So
    how far the runs got decides which bugs the rule reads, and how a bug came out never can (PLAN.md, 2026-11-01)."""
    done: list[str] = []
    for task in tasks:
        if not all((arm, task, repeat) in rows for arm in arms for repeat in repeats):
            break
        done.append(task)
    return done


def judged(results: Sequence[Mapping[str, Any]], bar: float = 0.05) -> bool:
    """The frozen rule: with no pair missing, more passes than every other arm, and p < 0.05 against each. ``bar``: the same rule at
    another p, for the line beside it."""
    return bool(results) and all(r["missing"] == 0 and r["loop_passed"] > r["other_passed"] and r["p"] < bar for r in results)


def failure_rich(rows: Rows, tasks: Iterable[str], score: str) -> list[str]:
    """The bugs on which at least one of arm S's own runs failed on this score. A run with no verdict is not a failure."""
    return [t for t in tasks if any(rows.get(("S", t, r), {}).get(score) is False for r in (1, 2, 3))]


def report(rows: Rows, name: str, tasks: Sequence[str], repeats: Sequence[int], loop_arm: str = "M9",
           others: Sequence[str] = ("A", "T"), control: Sequence[str] = ("R",), tuned: bool = False,
           scores: Sequence[str] = SCORES) -> None:
    """Every comparison on both scores. ``control``: arms reported beside the rule, where they have runs, which never enter it.
    ``tuned``: bugs this arm was tuned on, which are development, so they get no verdict of the rule, however they read. An arm in
    ``EXPLORATORY`` gets none either, however it is called."""
    shown =[arm for arm in control if arm != loop_arm and any(key[0] == arm for key in rows)]
    for score in scores:
        results = [compare(rows, loop_arm, other, tasks, repeats, score) for other in others]
        for r in [*results, *(compare(rows, loop_arm, other, tasks, repeats, score) for other in shown)]:
            uplift = "n/a" if r["uplift"] is None else f"{r['uplift']:+.1%}"
            bugs = by_bug(rows, loop_arm, r["other"], tasks, repeats, score)
            span = interval(rows, loop_arm, r["other"], tasks, repeats, score)
            print(f"{name:22} {score:11} {loop_arm} {r['loop_passed']:3}/{r['pairs']:<3} {r['other']:2} {r['other_passed']:3}/{r['pairs']:<3}"
                  f" uplift {uplift:>7}  wins {r['wins']:2} losses {r['losses']:2}  p {r['p']:.4f}"
                  f"  bugs better {bugs['better']} worse {bugs['worse']} same {bugs['same']}"
                  f"  95% by bug {f'{span[0]:+.0%} to {span[1]:+.0%}' if span else 'n/a'}"
                  + (f"  MISSING {r['missing']}" if r["missing"] else "") + ("  report only" if r["other"] in shown else ""))
        for arm in (loop_arm, *others, *shown):
            for said, cell in sorted(trust(rows, arm, tasks, repeats, score).items()):
                total = cell["right"] + cell["wrong"]
                print(f"{name:22} {score:11} {arm} said {said}: right {cell['right']} wrong {cell['wrong']} ({cell['right'] / total:.0%})")
            for key, whose in (("review", "final review"), ("end", "last review")):
                for said, cell in sorted(trust(rows, arm, tasks, repeats, score, key=key).items()):
                    total = cell["right"] + cell["wrong"]
                    print(f"{name:22} {score:11} {arm} {whose} {said}: right {cell['right']} wrong {cell['wrong']} ({cell['right'] / total:.0%})")
        rate = results[0]["loop_passed"] / results[0]["pairs"] if results[0]["pairs"] else 0.0
        verdict = "not applied, tuned on these bugs" if tuned else "not applied, exploratory" if loop_arm in EXPLORATORY else (
            f"{'met' if judged(results) else 'not shown'}; at {TWO_PRIMARIES}: {'cleared' if judged(results, TWO_PRIMARIES) else 'not cleared'}")
        print(f"{name:22} {score:11} frozen rule: {verdict}; "
              f"95% target: {'met' if rate >= TARGET and not results[0]['missing'] else 'not met'} ({rate:.1%})")


def rich(rows: Rows, name: str, tasks: Sequence[str], repeats: Sequence[int], loop_arm: str, control: Sequence[str] = ("R",)) -> None:
    """The same rule on the arms' own runs of the failure-rich bugs alone, score by score (PLAN.md, 2026-11-01), printed beside the
    result on all the bugs and never in place of it."""
    for score in SCORES:
        kept = failure_rich(rows, tasks, score)
        print(f"{name:22} {score:11} failure-rich: {len(kept)} of {len(tasks)} bugs")
        report(rows, name, kept, repeats, loop_arm=loop_arm, control=control, scores=(score,))


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loop", default="M9", help="the frozen loop's arm")
    ap.add_argument("--second", default="K1", help="the second primary, judged apart from --loop and never pooled with it")
    ap.add_argument("--trained", help="stage 3: the arm the loop's own tick produced, judged against --second, its parent, as well")
    args = ap.parse_args(argv)
    if {args.loop, args.second, args.trained} & set(EXPLORATORY):
        ap.error("an exploratory arm has its own section and is never a primary")
    root = c.archive_root() / "round3"
    rows = saved(root)
    fresh = [t.id for t in we.load_tasks(we.TASKS_FRESH)]
    unseen = [t.id for t in we.load_tasks(we.TASKS_UNSEEN)]
    train = [t.id for t in we.load_tasks() if t.id not in we.R3_HELD]
    later, later_second = args.loop not in STAGE_1_PRIMARIES, args.second not in STAGE_1_PRIMARIES
    report(rows, "stage 1, all eleven", fresh, (1, 2), loop_arm=args.loop, tuned=later)
    report(rows, "stage 1, determined 7", [t for t in fresh if t in DETERMINED], (1, 2), loop_arm=args.loop, tuned=later)
    stage_2 = finished(rows, unseen, (args.loop, args.second, "A", "T", "R"), (1, 2))
    print(f"stage 2: {len(stage_2)} of {len(unseen)} bugs finished, in the frozen order")
    report(rows, "stage 2, new repos", stage_2, (1, 2), loop_arm=args.loop)
    report(rows, "stage 2, determined", [t for t in stage_2 if t in DETERMINED_2], (1, 2), loop_arm=args.loop)
    report(rows, "held-out, second look", list(we.R3_HELD), (1, 2, 3, 4, 5), loop_arm=args.loop, tuned=later)
    report(rows, "training, tuned", train, (1, 2), loop_arm=f"L{args.loop[1:]}", others=("A",), tuned=True)
    rich(rows, "stage 2, failure-rich", stage_2, (1, 2), args.loop)
    second, shown = f"{args.second}, ", ("R", args.loop)
    report(rows, f"{second}stage 1, all eleven", fresh, (1, 2), loop_arm=args.second, control=shown, tuned=later_second)
    report(rows, f"{second}stage 1, determined 7", [t for t in fresh if t in DETERMINED], (1, 2), loop_arm=args.second, control=shown, tuned=later_second)
    report(rows, f"{second}stage 2, new repos", stage_2, (1, 2), loop_arm=args.second, control=shown)
    report(rows, f"{second}stage 2, determined", [t for t in stage_2 if t in DETERMINED_2], (1, 2), loop_arm=args.second, control=shown)
    rich(rows, f"{second}stage 2, failure-rich", stage_2, (1, 2), args.second, control=shown)
    report(rows, f"{second}held-out, second look", list(we.R3_HELD), (1, 2, 3, 4, 5), loop_arm=args.second, control=shown, tuned=later_second)
    report(rows, f"{second}training, tuned", train, (1, 2), loop_arm=args.second, others=("A",), tuned=True)
    if any(key[0] == "K0" for key in rows):
        report(rows, "K0, stage 2, exploratory", stage_2, (1, 2), loop_arm="K0", control=("R", args.loop, args.second))
    if args.trained:
        others = (args.second, "A", "T")
        stage_3 = finished(rows, stage_2, (args.trained,), (1, 2))
        print(f"stage 3: {len(stage_3)} of {len(stage_2)} bugs finished, in the frozen order")
        report(rows, "stage 3, new repos", stage_3, (1, 2), loop_arm=args.trained, others=others)
        report(rows, "stage 3, stage 1 bugs", fresh, (1, 2), loop_arm=args.trained, others=others, tuned=True)


if __name__ == "__main__":
    main()
