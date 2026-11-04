# SPDX-License-Identifier: Apache-2.0
"""Headroom (PLAN.md, 2026-11-04, evening): where a harness can still gain over one run of Claude Code, bug by bug, from
recorded patches and saved reviews only, and what would have to change where it cannot. Plain code; it prints counts and bug
ids, never the text of an issue, a patch, a test or a reply.

Each bug falls in the first of these that holds:
- ``undetermined``: the blind audit found that the issue does not state what the maintainers' tests check. No harness claim
  is read on it: the target is not in what the agent is given.
- ``solved``: every distinct patch is right. Nothing is left to gain.
- ``generation``: no distinct patch is right. No reviewer, chooser or vote can gain; the author has to write what no run wrote.
- ``separable``: some layer flagged a wrong patch and judged every right patch without flagging it. Sending that layer's
  failures back, or dropping what it flags, can gain, up to ``bound``.
- ``inseparable``: right and wrong patches exist, and no layer tells them apart: each one that flags a wrong patch flags a right
  one too, or never judged them all. Gaining needs a layer that does.

A layer's flag on a wrong patch is "reproduced", the only word that catches one; on a right patch it is any word but "none",
as ``replay_compare.right`` reads both. A layer with no counted review of a patch has no flag on it.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

KINDS = ("undetermined", "solved", "generation", "separable", "inseparable")
WHAT = {
    "undetermined": "the agent and its checks need what the maintainers' tests assume and the issue does not say: their intent, "
                    "in the issue, its discussion, docs or tests the agent can read",
    "solved": "nothing: every run that finished was right",
    "generation": "the author: a first attempt or a repair that writes what no run wrote, since nothing right exists to keep",
    "separable": "nothing new: a loop that sends the separating layer's failures back, or a chooser that drops its flags",
    "inseparable": "a check that tells the right patch from the wrong ones, which no recorded layer does",
}
Patch = Mapping[str, Any]


def classify(patches: Sequence[Patch], layers: Sequence[str], determined: bool | None) -> tuple[str, list[str]]:
    """A bug's class and, if separable, the layers that separate it. ``determined`` None is unknown: read as the patches say."""
    if determined is False:
        return "undetermined", []
    right = [x for x in patches if x["truth"]]
    wrong = [x for x in patches if not x["truth"]]
    if not wrong:
        return "solved", []
    if not right:
        return "generation", []
    by = [name for name in layers
          if any(x["flags"].get(name) is True for x in wrong) and all(x["flags"].get(name) is False for x in right)]
    return ("separable", by) if by else ("inseparable", [])


def bound(patches: Sequence[Patch], runs: Sequence[bool], by: Sequence[str]) -> float:
    """The share right a chooser reaches by dropping what ``by`` flags, over distinct patches; with no layer, the share of
    ``runs`` right, the baseline."""
    if not by:
        return sum(map(bool, runs)) / len(runs) if runs else 0.0
    kept = [x for x in patches if not any(x["flags"].get(name) is True for name in by)]
    return sum(bool(x["truth"]) for x in kept) / len(kept) if kept else 0.0


def summary(bugs: Mapping[str, tuple[Sequence[Patch], Sequence[bool], bool | None]], layers: Sequence[str]) -> dict[str, dict]:
    """Each class's bugs, the baseline's mean share right on them and the mean bound, as ``bound`` reads one bug."""
    out: dict[str, dict] = {kind: {"bugs": [], "baseline": None, "bound": None, "by": {}} for kind in KINDS}
    base: dict[str, list[float]] = {kind: [] for kind in KINDS}
    top: dict[str, list[float]] = {kind: [] for kind in KINDS}
    for bug, (patches, runs, determined) in sorted(bugs.items()):
        kind, by = classify(patches, layers, determined)
        out[kind]["bugs"].append(bug)
        out[kind]["by"][bug] = by
        if runs:
            base[kind].append(bound(patches, runs, []))
            top[kind].append(bound(patches, runs, by) if by else bound(patches, runs, []))
    for kind in KINDS:
        out[kind]["baseline"] = sum(base[kind]) / len(base[kind]) if base[kind] else None
        out[kind]["bound"] = sum(top[kind]) / len(top[kind]) if top[kind] else None
    return out


def patches_of(rows: Mapping[str, Mapping[str, Any]], score: str, layers: Sequence[str]) -> list[dict[str, Any]]:
    """Distinct patches with a truth on ``score`` from ``replay_compare.patches``, each with its layers' flags."""
    import replay_compare as rc

    out = []
    for one in rows.values():
        truth = one.get(score)
        if truth is None:
            continue
        flags = {}
        for name in layers:
            review = one.get(name)
            flags[name] = None if not rc.counted(review) else (review["said"] != "none" if truth else review["said"] == "reproduced")
        out.append({"truth": bool(truth), "flags": flags})
    return out


def main(argv: Sequence[str] | None = None) -> None:
    import compare
    import replay_compare as rc
    import wecommon as c

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tasks", nargs="*", help="bugs to read; stage 1 and 2's by default")
    ap.add_argument("--labels", help="the blind audit's labels as JSON {bug: {'determined': ...}}; stage 1 and 2's lists by default")
    ap.add_argument("--baseline", default="A", help="the arm one run of which is the baseline")
    args = ap.parse_args(argv)
    root = c.archive_root() / "round3"
    tasks = args.tasks or sorted(rc.predicted())
    if args.labels:
        labels = json.loads(Path(args.labels).read_text("utf-8"))
        determined = {t: (labels[t].get("determined") == "determined") if t in labels else None for t in tasks}
    else:
        known = set(compare.DETERMINED) | set(compare.DETERMINED_2)
        determined = {t: t in known for t in tasks}
    layers = [name for name, _ in rc.REVIEWERS if name not in (rc.PRODUCT, rc.REPRODUCE)]
    verdicts = compare.saved(root)
    for score in rc.SCORES:
        bugs = {}
        for task in tasks:
            runs = [v[score] for (arm, t, _), v in verdicts.items() if t == task and arm == args.baseline and v.get(score) is not None]
            bugs[task] = (patches_of(rc.patches(root, [task]), score, layers), runs, determined.get(task))
        got = summary(bugs, layers)
        print(f"[{score}] {len(tasks)} bugs; layers: {', '.join(layers)}; baseline: one run of arm {args.baseline}")
        for kind in KINDS:
            cell = got[kind]
            if not cell["bugs"]:
                continue
            rates = "" if cell["baseline"] is None else f"; baseline {cell['baseline']:.0%}, bound {cell['bound']:.0%}"
            seps = "".join(f" {b}({'+'.join(by)})" if by else f" {b}" for b, by in cell["by"].items())
            print(f"  {kind:12} {len(cell['bugs']):2}{rates}:{seps}")
            print(f"  {'':12}    what must change: {WHAT[kind]}")


if __name__ == "__main__":
    main()
