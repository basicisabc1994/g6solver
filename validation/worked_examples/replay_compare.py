# SPDX-License-Identifier: Apache-2.0
"""The two replays' counts (PLAN.md, 2026-11-03, afternoon), read from saved reviews and rows only: no model and no container. It
prints counts, and never the text of a task, an issue, a test, a reply or a patch.

The patches are the distinct final patches of the bugs' finished runs, in the order of their digests, as the reviews take them.
Truth is each patch's own verdict on the amended tests, and the maintainers' verdict is reported beside it, both as
``compare.saved`` reads them; a patch whose runs' verdicts differ on a score has no truth on it. A review is right when it reproduces
a defect on a wrong patch or finds none on a right one; the product's, which runs no test, when it says defect on a wrong patch or
none on a right one. Every other review of a right patch is a false alarm, and of a wrong one a miss. A review that failed is no
review and is counted nowhere. The product's ``too long`` is counted apart: it is left out of the product's agreement, and it never
matches a verdict.

The futility check (stage 1 and 2 together, after each block of ``BLOCK`` patches by digest) asks whether, with every patch the
replay has not yet reviewed going its way, its prediction can still be reached. A block counts once each of its patches has a saved
review, failed or not.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

import compare
import final_review as fr
import product_review as pr
import run as we
import wecommon as c
from g6.core.binomial import lower_bound, upper_bound

BLOCK, ALPHA = 40, 0.05
#: The predictions, written before any run: replay 1's agreement up by at least 10 points with false alarms up by at most 5, and
#: replay 2's verdicts matching the final review's on at least 85% of patches.
RISE, MORE_FALSE_ALARMS, MATCH = Fraction(1, 10), Fraction(1, 20), Fraction(17, 20)
FROZEN, REPRODUCE, PRODUCT = "final review", "reproduce first", "product"
REVIEWERS = ((FROZEN, fr.FOLDER), (REPRODUCE, fr.REPRODUCE), (PRODUCT, pr.FOLDER))
SCORES = ("amended", "maintainers")
Rows = Mapping[str, Mapping[str, Any]]


def groups() -> list[tuple[str, list[str], bool]]:
    """The bugs each count is read on, and whether the predictions are read there: stage 1's fresh and held-out bugs and stage 2's,
    together and apart, and the training bugs apart, since they shaped the reviewer."""
    fresh = [t.id for t in we.load_tasks(we.TASKS_FRESH)] + list(we.R3_HELD)
    unseen = [t.id for t in we.load_tasks(we.TASKS_UNSEEN)]
    train = [t.id for t in we.load_tasks() if t.id not in we.R3_HELD]
    return [("stage 1 and 2", fresh + unseen, True), ("stage 1", fresh, False), ("stage 2", unseen, False), ("training", train, False)]


def patches(root: Path, tasks: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Each distinct final patch of these bugs' finished runs, by digest and in digest order: its bug, its truth on each score
    (None where its runs' verdicts differ or none was scored), the scores it is split on, and each reviewer's ``said`` and
    ``verdict`` from its saved review (None where there is no saved review)."""
    wanted, verdicts = set(tasks), compare.saved(root)
    found: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "loop").glob("*-*.json")):
        row = json.loads(path.read_text("utf-8"))
        patch = root / str(row.get("run")) / "final.patch"
        if row.get("task") not in wanted or row.get("status") != "finished" or not patch.is_file() or not patch.stat().st_size:
            continue
        one = found.setdefault(c.sha256_file(patch), {"task": row["task"], "seen": {score: set() for score in SCORES}})
        for score in SCORES:
            value = verdicts.get((row["arm"], row["task"], row["repeat"]), {}).get(score)
            if value is not None:
                one["seen"][score].add(bool(value))
    for sha, one in found.items():
        seen = one.pop("seen")
        one.update({score: next(iter(seen[score])) if len(seen[score]) == 1 else None for score in SCORES})
        one["split"] = [score for score in SCORES if len(seen[score]) > 1]
        for name, folder in REVIEWERS:
            path = root / "loop" / folder / f"{sha}.json"
            kept = json.loads(path.read_text("utf-8")) if path.is_file() else None
            one[name] = {"said": kept.get("said"), "verdict": kept.get("verdict")} if kept else None
    return dict(sorted(found.items()))


def counted(review: Mapping[str, Any] | None) -> bool:
    return review is not None and review.get("said") != "failed"


def right(name: str, said: str, truth: bool) -> bool | None:
    """Whether a review agrees with the truth; None where it is left out, the product's ``too long``."""
    if name == PRODUCT:
        return None if said == "too long" else (said == "defect") != truth
    return said == ("none" if truth else "reproduced")


def tally(rows: Rows, name: str, score: str) -> dict[str, int]:
    """One reviewer's counts on a score: its agreement, its false alarms on right patches and its misses on wrong ones."""
    cells = [(one[score], right(name, one[name]["said"], one[score])) for one in rows.values()
             if one[score] is not None and counted(one[name])]
    cells = [(truth, ok) for truth, ok in cells if ok is not None]
    good = [ok for truth, ok in cells if truth]
    bad = [ok for truth, ok in cells if not truth]
    return {"n": len(cells), "right": sum(ok for _, ok in cells), "right_patches": len(good),
            "false_alarms": sum(not ok for ok in good), "wrong_patches": len(bad), "misses": sum(not ok for ok in bad),
            "too_long": sum(one[name]["said"] == "too long" for one in rows.values() if counted(one[name]))}


def paired(rows: Rows, score: str) -> dict[str, Any]:
    """Replay 1 against the final review on the patches both reviewed: the exact one-sided sign test on those where exactly one is
    right, and the bugs on which replay 1 is right on more of them, fewer or as many."""
    both = [one for one in rows.values() if one[score] is not None and counted(one[FROZEN]) and counted(one[REPRODUCE])]
    marks = [(one["task"], right(FROZEN, one[FROZEN]["said"], one[score]), right(REPRODUCE, one[REPRODUCE]["said"], one[score]))
             for one in both]
    wins, losses = sum(ours and not theirs for _, theirs, ours in marks), sum(theirs and not ours for _, theirs, ours in marks)
    bugs = {"better": 0, "worse": 0, "same": 0}
    for task in sorted({task for task, _, _ in marks}):
        ahead = sum(ours for t, _, ours in marks if t == task) - sum(theirs for t, theirs, _ in marks if t == task)
        bugs["better" if ahead > 0 else "worse" if ahead < 0 else "same"] += 1
    return {"n": len(marks), "frozen": sum(theirs for _, theirs, _ in marks), "reproduce": sum(ours for _, _, ours in marks),
            "wins": wins, "losses": losses, "p": compare.sign_test(wins, losses), **bugs}


def _matches(one: Mapping[str, Any]) -> bool:
    return one[PRODUCT]["said"] == one[FROZEN]["verdict"]


def matched(rows: Rows) -> dict[str, int]:
    """Replay 2 against the final review on the patches both reviewed: how often the product's verdict is the final review's."""
    both = [one for one in rows.values() if counted(one[FROZEN]) and counted(one[PRODUCT])]
    return {"n": len(both), "matches": sum(map(_matches, both)), "too_long": sum(one[PRODUCT]["said"] == "too long" for one in both)}


def _points(value: Fraction, unit: str = " points") -> str:
    return f"{float(value * 100):+.1f}{unit}"


def futility(rows: Rows, *, block: int = BLOCK) -> list[str]:
    """The registered futility check of each replay after each block of ``block`` patches by digest that it has run: whether, with
    every patch it has not reviewed in those blocks or after them going its way, its prediction can still be reached. Replay 1's
    is read on the amended tests against the final review on the same patches: a remaining patch going its way is one it is right
    on, so it adds no false alarm. Replay 2's: a remaining patch going its way is one whose verdict matches."""
    return [line for name in (REPRODUCE, PRODUCT) for _, line in _blocks(rows, name, block)]


def futile(root: Path, tasks: Iterable[str], name: str, *, block: int = BLOCK) -> str:
    """The registered stop for replay ``name`` as it runs: the line of the last block it has run whole, when that block says its
    prediction cannot be reached; else "". It never stops for success."""
    rows = patches(root, tasks)
    if not any(one[name] is None for one in rows.values()):       # nothing left to review: there is nothing to stop
        return ""
    found = list(_blocks(rows, name, block))
    return found[-1][1] if found and not found[-1][0] else ""


def _blocks(rows: Rows, name: str, block: int = BLOCK) -> Iterator[tuple[bool, str]]:
    """``futility``'s reading of one replay, block by block: whether its prediction can still be reached, and the line saying so."""
    order = list(rows)
    pool = [sha for sha, one in rows.items() if one["amended"] is not None and counted(one[FROZEN])]
    truths = sum(rows[sha]["amended"] for sha in pool)

    def standing(name: str, shas: Iterable[str]) -> tuple[int, int]:
        marks = [(rows[sha]["amended"], right(name, rows[sha][name]["said"], rows[sha]["amended"])) for sha in shas]
        return sum(ok for _, ok in marks), sum(truth and not ok for truth, ok in marks)

    frozen_right, frozen_alarms = standing(FROZEN, pool)
    matching = [sha for sha, one in rows.items() if counted(one[FROZEN])]
    for number, end in enumerate(range(block, len(order) + block, block), 1):
        if any(rows[sha][name] is None for sha in order[:end]):
            break
        done = set(order[:end])
        place = f"futility {name}, block {number} (patches {end - block + 1}-{min(end, len(order))} by digest)"
        if name == REPRODUCE:
            kept = [sha for sha in pool if sha in done and counted(rows[sha][name])]
            ours, alarms = standing(name, kept)
            rise = Fraction(ours + len(pool) - len(kept) - frozen_right, len(pool)) if pool else Fraction(0)
            more = Fraction(alarms - frozen_alarms, truths) if truths else Fraction(0)
            reached = bool(pool) and rise >= RISE and more <= MORE_FALSE_ALARMS
            line = (f"{place}: {len(kept)} reviewed, {len(pool) - len(kept)} remaining of {len(pool)}; at best agreement "
                         f"{_points(rise)} (needs {_points(RISE, '')}), false alarms {_points(more)} (at most "
                         f"{_points(MORE_FALSE_ALARMS, '')}): {'can still be reached' if reached else 'cannot be reached'}")
            yield reached, line
        else:
            kept = [sha for sha in matching if sha in done and counted(rows[sha][name])]
            best = Fraction(sum(_matches(rows[sha]) for sha in kept) + len(matching) - len(kept), len(matching) or 1)
            reached = bool(matching) and best >= MATCH
            line = (f"{place}: {len(kept)} reviewed, {len(matching) - len(kept)} remaining of {len(matching)}; at best "
                         f"{float(best):.1%} of verdicts match (needs {float(MATCH):.1%}): "
                         f"{'can still be reached' if reached else 'cannot be reached'}")
            yield reached, line


def _share(k: int, n: int, *, upper: bool) -> str:
    if not n:
        return f"{k}/{n} (n/a)"
    bound = (upper_bound if upper else lower_bound)(k, n, ALPHA)
    return f"{k}/{n} ({k / n:.1%}, 95% {'upper' if upper else 'lower'} {bound:.1%})"


def report(root: Path, chosen: Sequence[tuple[str, Sequence[str], bool]] | None = None, *, block: int = BLOCK) -> None:
    """Every count, group by group: each reviewer's agreement, false alarms and misses on each score, replay 1 paired against the
    final review, replay 2's matching verdicts, and, where the predictions are read, the futility checks."""
    for group, tasks, futile in chosen or groups():
        rows = patches(root, tasks)
        reviewed = ", ".join(f"{name} {sum(counted(one[name]) for one in rows.values())} "
                             f"({sum(one[name] is not None and not counted(one[name]) for one in rows.values())})"
                             for name, _ in REVIEWERS)
        print(f"{group}: {len(rows)} distinct patches; truth on the amended tests "
              f"{sum(one['amended'] is not None for one in rows.values())}, on the maintainers' "
              f"{sum(one['maintainers'] is not None for one in rows.values())}; split amended "
              f"{sum('amended' in one['split'] for one in rows.values())}, maintainers "
              f"{sum('maintainers' in one['split'] for one in rows.values())}; reviewed (failed, not counted): {reviewed}")
        for score in SCORES:
            for name, _ in REVIEWERS:
                got = tally(rows, name, score)
                print(f"{group:13} {score:11} {name:15} agree {_share(got['right'], got['n'], upper=False)}  false alarms "
                      f"{_share(got['false_alarms'], got['right_patches'], upper=True)}  misses "
                      f"{_share(got['misses'], got['wrong_patches'], upper=True)}"
                      + (f"  too long {got['too_long']}" if name == PRODUCT else ""))
            got = paired(rows, score)
            print(f"{group:13} {score:11} {REPRODUCE} against {FROZEN}: {got['n']} paired, {FROZEN} right {got['frozen']}, "
                  f"{REPRODUCE} right {got['reproduce']}; only {REPRODUCE} right {got['wins']}, only {FROZEN} right "
                  f"{got['losses']}, one-sided p {got['p']:.4f}; bugs better {got['better']} worse {got['worse']} same {got['same']}")
        got = matched(rows)
        share = f"{got['matches'] / got['n']:.1%}" if got["n"] else "n/a"
        print(f"{group:13} {PRODUCT} against {FROZEN}: verdicts match on {got['matches']} of {got['n']} ({share}), "
              f"too long {got['too_long']}")
        if futile:
            for line in futility(rows, block=block):
                print(line)


def main() -> None:
    report(c.archive_root() / "round3")


if __name__ == "__main__":
    main()
