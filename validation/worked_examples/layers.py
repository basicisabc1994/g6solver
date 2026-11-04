# SPDX-License-Identifier: Apache-2.0
"""The Swiss cheese table on recorded patches (loop plan Part 25; PLAN.md, 2026-11-04), from saved reviews and rows only: no model
and no container. It prints counts, and never the text of a task, an issue, a test, a reply or a patch.

A layer is a reviewer whose verdicts are saved by patch digest. Each distinct patch of a bug is right, the commonest wrong change of
its bug, or another wrong patch. The commonest wrong change is the normalised source change (``change``) that most of the bug's
wrong runs share, counted by run and held by two runs at least; changes tied for most are all commonest. A layer is measured by kind
of patch, and each pair of layers by the wrong patches both judged: how often the second misses what the first misses, beside how
often it misses at all. Two layers with the same holes are one layer, whatever models they run on, so independence is read from the
errors they miss and never assumed from where they come from.
"""
from __future__ import annotations

import collections
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import compare
import replay_compare as rc
import wecommon as c
from g6.core.binomial import lower_bound, upper_bound

KINDS = ("right", "commonest wrong", "other wrong")
TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
Said = Mapping[str, Mapping[str, str]]


def change(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """A patch's normalised source change: for each Python file outside tests, its added and removed lines in order, with
    whitespace, blank lines and comment lines dropped; files sorted by path."""
    files: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            found = re.match(r"diff --git a/(.*?) b/(.*)$", line)
            current = found.group(2) if found and found.group(2).endswith(".py") and not TEST_PATH.search(found.group(2)) else None
            if current:
                files[current] = []
            continue
        if current is None or line.startswith(("+++", "---", "@@", "index ", "new file", "deleted file", "similarity", "rename ")):
            continue
        if line[:1] in "+-":
            body = re.sub(r"\s+", "", line[1:])
            if body and not body.startswith("#"):
                files[current].append(line[0] + body)
    return tuple(sorted((path, tuple(lines)) for path, lines in files.items() if lines))


def kinds_of(runs: Iterable[tuple[str, object, bool]]) -> dict[str, str]:
    """Each distinct patch's kind from its bug's runs, given as (digest, normalised change, right). A digest whose runs disagree
    on the truth has no kind."""
    runs = list(runs)
    truths: dict[str, set[bool]] = collections.defaultdict(set)
    for sha, _, right in runs:
        truths[sha].add(bool(right))
    wrong = collections.Counter(chg for sha, chg, right in runs if not right and len(truths[sha]) == 1)
    top = max(wrong.values(), default=0)
    commonest = {chg for chg, n in wrong.items() if n == top and n >= 2}
    kinds = {}
    for sha, chg, right in runs:
        if len(truths[sha]) == 1:
            kinds[sha] = "right" if right else "commonest wrong" if chg in commonest else "other wrong"
    return kinds


def mixed(runs: Mapping[str, Sequence[tuple[str, object, bool]]]) -> set[str]:
    """The bugs whose runs hold both a right and a wrong patch: the only bugs where telling patches apart can change which one
    is kept."""
    return {bug for bug, got in runs.items() if {bool(right) for _, _, right in got} == {True, False}}


def table(kinds: Mapping[str, str], said: Said) -> dict[str, dict[str, object]]:
    """Each layer's counts as (flagged, judged): by kind, and its misses on wrong patches and false alarms on right ones."""
    out = {}
    for layer, verdicts in said.items():
        cells = {kind: [0, 0] for kind in KINDS}
        for sha, verdict in verdicts.items():
            if sha in kinds:
                cells[kinds[sha]][1] += 1
                cells[kinds[sha]][0] += verdict == "reproduced"
        wrong = [cells["commonest wrong"], cells["other wrong"]]
        flagged_wrong, judged_wrong = sum(x[0] for x in wrong), sum(x[1] for x in wrong)
        out[layer] = {"flagged": {kind: tuple(cells[kind]) for kind in KINDS},
                      "misses": (judged_wrong - flagged_wrong, judged_wrong), "false alarms": tuple(cells["right"])}
    return out


def overlap(kinds: Mapping[str, str], said: Said, a: str, b: str) -> dict[str, object]:
    """On the wrong patches both layers judged: how many ``a`` misses, how often ``b`` misses those, and how often ``b`` misses."""
    both = [sha for sha in said[a] if sha in said[b] and kinds.get(sha, "right") != "right"]
    a_misses = [sha for sha in both if said[a][sha] != "reproduced"]
    return {"both judged": len(both), "a misses": len(a_misses),
            "b misses given a misses": (sum(said[b][sha] != "reproduced" for sha in a_misses), len(a_misses)),
            "b misses": (sum(said[b][sha] != "reproduced" for sha in both), len(both))}


def runs_of(root: Path, tasks: Iterable[str], score: str) -> dict[str, list[tuple[str, object, bool]]]:
    """Each bug's finished runs with a non-empty final patch and a truth on ``score``, as (digest, normalised change, right)."""
    wanted, verdicts, cache = set(tasks), compare.saved(root), {}
    out: dict[str, list[tuple[str, object, bool]]] = collections.defaultdict(list)
    for path in sorted((root / "loop").glob("*-*.json")):
        row = json.loads(path.read_text("utf-8"))
        patch = root / str(row.get("run")) / "final.patch"
        truth = verdicts.get((row.get("arm"), row.get("task"), row.get("repeat")), {}).get(score)
        if row.get("task") not in wanted or row.get("status") != "finished" or truth is None or not patch.is_file() \
                or not patch.stat().st_size:
            continue
        sha = c.sha256_file(patch)
        if sha not in cache:
            cache[sha] = change(patch.read_text("utf-8", errors="surrogateescape"))
        out[row["task"]].append((sha, cache[sha], bool(truth)))
    return out


def _share(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%}, 95% {lower_bound(k, n, 0.05):.0%} to {upper_bound(k, n, 0.05):.0%})" if n else "0/0"


def report(root: Path, tasks: Sequence[str], names: Sequence[str]) -> None:
    rows = rc.patches(root, tasks)
    for score in rc.SCORES:
        runs = runs_of(root, tasks, score)
        for where, bugs in (("all bugs", set(runs)), ("mixed bugs", mixed(runs))):
            kinds: dict[str, str] = {}
            for bug in bugs:
                kinds.update(kinds_of(runs[bug]))
            said = {name: {sha: one[name]["said"] for sha, one in rows.items() if rc.counted(one[name])} for name in names}
            print(f"[{score}, {where}: {len(bugs)}] distinct patches by kind: {dict(collections.Counter(kinds.values()))}")
            for name, got in table(kinds, said).items():
                cells = "  ".join(f"{kind} {_share(*got['flagged'][kind])}" for kind in KINDS)
                print(f"  {name:13} flagged: {cells}")
            for a in names:
                for b in names:
                    if a != b:
                        pair = overlap(kinds, said, a, b)
                        print(f"  {b} misses, of what {a} misses: {_share(*pair['b misses given a misses'])}; of all wrong both "
                              f"judged: {_share(*pair['b misses'])}")


def main() -> None:
    root = c.archive_root() / "round3"
    names = [name for name, _ in rc.REVIEWERS if name != rc.PRODUCT and name != rc.REPRODUCE]
    report(root, sorted(rc.predicted()), names)


if __name__ == "__main__":
    main()
