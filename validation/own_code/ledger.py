# SPDX-License-Identifier: Apache-2.0
"""The record of what the Codex reviews of G6's own code found, and the root-cause analysis read from it.

Every review of a branch writes ``.review/report.md`` in its own throwaway checkout, one ``## Finding <n>`` section a finding,
each reproduced by a test. ``findings`` reads them into one row a finding, by plain code. A model then proposes each row's
root-cause class (``CLASSES``) and its effect (``EFFECTS``); it never counts. ``blamed`` asks git, for each finding after a
loop's first review, whether the lines it cites were last written by the commit that review read, which is the previous
round's repair. ``analysis`` counts: classes, which came back in a later round of the same loop, the effects by round, and
how many later findings sit in the last repair's own lines. It prints counts and class names, never a finding's text, since
the rows can quote local paths.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

#: The review folders' prefixes and the branch each loop reviewed, longest prefix first.
LOOPS = (("rpf", "claude/replays-followup"), ("rp", "claude/replays"), ("rca", "claude/tick-rca"),
         ("kfu", "claude/k-followup"), ("kf", "claude/k-fixes"), ("k0", "claude/runtime-k (first)"), ("rk", "claude/runtime-k"),
         ("k", "claude/k-loop"), ("fx", "claude/loop-followup-2"), ("fu", "claude/loop-followup"),
         ("lr", "claude/reflexive-step"), ("ll", "claude/loop-learn"), ("l", "claude/loop-fixes"),
         ("c", "claude/improve-control"), ("b1", "claude/loop-batch-1"))
#: What a finding's design error was, the closed list the labeller chooses from ("other" carries its own name).
CLASSES = ("identity_from_rendering", "one_fact_two_rules", "own_parser_for_foreign_format", "failure_read_as_success",
           "check_then_act_race", "state_not_durable", "untrusted_input_given_authority", "boundary_case",
           "guard_covers_less_than_it_guards", "other")
#: Each class as a lesson for code never seen: the check a review walks and a repair meets, so a repair removes the class
#: rather than one instance of it. Data, so the loop can add, measure and drop checks by whether their class comes back.
CHECKS = {
    "identity_from_rendering": "Take an identity from the bytes, never from a rendering of them (text, repr, a diff's display); "
                               "test two inputs that render the same.",
    "one_fact_two_rules": "Decide each fact in one place that every consumer imports; test that every consumer gives the same "
                          "answer on the same cases.",
    "own_parser_for_foreign_format": "Let the format's own producer read it (git for a diff, the library for its file); test what "
                                     "the producer reads, not what a copy of its rules reads.",
    "failure_read_as_success": "Read every status a call returns, and treat anything not recognised as a success as a failure; "
                               "test each failing status and one never seen.",
    "check_then_act_race": "Check and act in one step (a lock, a transaction), or read the identity again after acting and "
                           "refuse on a change; test a change between the two.",
    "state_not_durable": "Write what must survive a restart to the store before acting on it; test a stop and a restart between "
                         "every two steps.",
    "untrusted_input_given_authority": "Never let what a model or a caller writes decide an outcome alone; test the case where it "
                                       "lies.",
    "boundary_case": "Name the edges of every range and order; test at empty, one, the limit, past it, and the first and last.",
    "guard_covers_less_than_it_guards": "List what a guard protects from the producer's own listing, not the guard's pattern; "
                                        "test the guard on each member and on one its pattern misses.",
}
#: What it would do: change an outcome without a word, fail with one, or need an unusual or adversarial input.
EFFECTS = ("changes_outcome", "fails_loudly", "latent")


def loop_of(folder: str) -> tuple[str | None, int | None, str]:
    """The branch, round and rerun mark of a review folder named ``review-<prefix><round><mark>``: (None, None, "") if unknown."""
    tail = folder.removeprefix("review-")
    for prefix, branch in LOOPS:
        found = re.fullmatch(rf"{prefix}(\d*)([a-z]?)", tail)
        if found:
            return branch, int(found[1] or 1), found[2]
    return None, None, ""


def _field(body: str, name: str) -> str:
    found = re.search(rf"^{name}:\s*(.*?)(?=^\w+:|\Z)", body, re.M | re.S)
    return found[1].strip() if found else ""


def findings(roots: Iterable[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every finding in every review report under ``roots``, and the reports that held none."""
    rows, clean = [], []
    for root in roots:
        for folder in sorted(root.glob("review-*")):
            report = folder / ".review" / "report.md"
            if not report.is_file():
                continue
            branch, number, mark = loop_of(folder.name)
            text = report.read_text("utf-8", "replace")
            parts = re.split(r"^## Finding (\d+)\s*$", text, flags=re.M)
            if len(parts) == 1:
                clean.append({"folder": folder.name, "branch": branch, "round": number})
                continue
            for at in range(1, len(parts), 2):
                body = parts[at + 1].split("\n## ")[0]
                rows.append({"folder": folder.name, "branch": branch, "round": number, "mark": mark, "finding": int(parts[at]),
                             "rule": _field(body, "Rule"), "why": _field(body, "Why"), "test": _field(body, "Test"),
                             "cites": sorted(set(re.findall(r"[\w./-]+\.py:\d+", body)))})
    return rows, clean


def _git(folder: Path, *args: str) -> str | None:
    done = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, timeout=60)
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


#: Where a later finding's newest cited line came from, newest first. "earlier" is older than the last repair, used when the
#: loop's first review cannot be found to tell an earlier repair from code round 1 already read.
LINES = ("last repair", "earlier repair", "earlier", "before the loop")


def _ancestor(folder: Path, older: str, newer: str) -> bool | None:
    done = subprocess.run(["git", "-C", str(folder), "merge-base", "--is-ancestor", older, newer], capture_output=True, timeout=60)
    return {0: True, 1: False}.get(done.returncode)


def blamed(rows: Sequence[Mapping[str, Any]], roots: Iterable[Path]) -> dict[tuple[str, int], str]:
    """For each finding: "round 1"; for a later one, where its newest cited line came from (``LINES``): the commit its review
    read (the last repair), an earlier repair of the same loop, or code its first review already read; "unresolved" or
    "no cite" otherwise."""
    folders = {f.name: f for root in roots for f in root.glob("review-*")}
    firsts: dict[str | None, str] = {}
    for name in sorted(folders):
        branch, number, _ = loop_of(name)
        if number == 1 and branch not in firsts:
            firsts[branch] = (_git(folders[name], "rev-parse", "HEAD") or "").strip()
    head: dict[str, tuple[str, list[str], str]] = {}
    found = {}
    for row in rows:
        if row["round"] == 1:
            found[row["folder"], row["finding"]] = "round 1"
            continue
        folder = folders[row["folder"]]
        if row["folder"] not in head:
            this = (_git(folder, "rev-parse", "HEAD") or "").strip()
            first = firsts.get(loop_of(row["folder"])[0], "")
            head[row["folder"]] = (this, (_git(folder, "ls-tree", "-r", "--name-only", "HEAD") or "").splitlines(),
                                   first if first and _ancestor(folder, first, this) else "")
        this, files, first = head[row["folder"]]
        marks = []
        for cite in row["cites"]:
            path, _, line = cite.rpartition(":")
            hits = [p for p in files if p == path or p.endswith("/" + path)]
            said = _git(folder, "blame", "-L", f"{line},{line}", "--porcelain", "HEAD", "--", hits[0]) if len(hits) == 1 else None
            commit = said.split(" ", 1)[0] if said else ""
            if not commit:
                continue
            older = _ancestor(folder, commit, first) if first and commit != this else None
            marks.append("last repair" if commit == this else "earlier" if not first else
                         {True: "before the loop", False: "earlier repair"}.get(older, "earlier"))
        kind = "no cite" if not row["cites"] else next((k for k in LINES if k in marks), "unresolved")
        found[row["folder"], row["finding"]] = kind
    return found


def _class(label: Mapping[str, str]) -> str:
    return label["mechanism"] if label["mechanism"] != "other" else f"other: {label.get('other', '')}"


def _met_again(pairs: Iterable[tuple[int, str]]) -> Counter[str]:
    """For one loop's (round, class) pairs: per class, its findings in a round after the first round it appeared in."""
    first: dict[str, int] = {}
    again: Counter[str] = Counter()
    for number, cls in sorted(pairs):
        if cls in first and number > first[cls]:
            again[cls] += 1
        first.setdefault(cls, number)
    return again


def by_chance(rows: Sequence[Mapping[str, Any]], labels: Mapping[tuple[str, int], Mapping[str, str]],
              draws: int = 5000, seed: int = 0) -> dict[str, float]:
    """Whether classes came back after a repair less often than chance would give: each loop's class labels shuffled
    across its own findings, rounds kept, ``draws`` times. A repair that removed its class shows as an observed count
    below most draws; one that patched an instance, as a count among them."""
    loops: dict[Any, list[tuple[int, str]]] = defaultdict(list)
    for row in rows:
        loops[row["branch"]].append((row["round"], _class(labels[row["folder"], row["finding"]])))
    observed = sum(sum(_met_again(pairs).values()) for pairs in loops.values())
    rng = random.Random(seed)  # noqa: S311 - a seeded shuffle for a count, not a secret
    sims = []
    for _ in range(draws):
        total = 0
        for pairs in loops.values():
            classes = [cls for _, cls in pairs]
            rng.shuffle(classes)
            total += sum(_met_again(zip((k for k, _ in pairs), classes)).values())
        sims.append(total)
    sims.sort()
    return {"observed": observed, "mean": round(sum(sims) / draws, 1), "low": sims[int(0.025 * draws)],
            "high": sims[int(0.975 * draws) - 1], "share_at_or_below": round(sum(s <= observed for s in sims) / draws, 3)}


def analysis(rows: Sequence[Mapping[str, Any]], labels: Mapping[tuple[str, int], Mapping[str, str]],
             lines: Mapping[tuple[str, int], str]) -> dict[str, Any]:
    """The counts: by class, by effect and round, the classes a loop met again after its first repair, the later findings in
    the last repair's own lines, and each loop's findings by round."""
    joined = []
    for row in rows:
        label = labels[row["folder"], row["finding"]]
        joined.append({**row, "class": _class(label), "effect": label["effect"],
                       "lines": lines.get((row["folder"], row["finding"]), "")})
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        by_class[row["class"]].append(row)
    again: Counter[str] = Counter()
    for branch in {row["branch"] for row in joined}:
        again += _met_again((r["round"], r["class"]) for r in joined if r["branch"] == branch)
    later = [row for row in joined if row["lines"] in LINES]
    loops = {}
    for branch in sorted({row["branch"] for row in joined}):
        mine = [row for row in joined if row["branch"] == branch]
        rounds = sorted({row["round"] for row in mine})
        loops[branch] = {"by_round": [sum(row["round"] == k for row in mine) for k in rounds],
                         "last_round": dict(Counter(row["effect"] for row in mine if row["round"] == rounds[-1])),
                         "later_lines": dict(Counter(row["lines"] for row in mine if row["lines"] in LINES))}
    return {
        "findings": len(joined), "loops": len(loops),
        "classes": {cls: {"findings": len(rs), "silent": sum(r["effect"] == "changes_outcome" for r in rs),
                          "loops": len({r["branch"] for r in rs}), "came_back": again[cls],
                          "in_last_repair": sum(r["lines"] == "last repair" for r in rs)}
                    for cls, rs in sorted(by_class.items(), key=lambda kv: -len(kv[1]))},
        "effect_by_round": {k: dict(Counter(r["effect"] for r in joined if r["round"] == k))
                            for k in sorted({r["round"] for r in joined})},
        "met_again": sum(again.values()), "later_rounds": sum(r["round"] > 1 for r in joined),
        "later_in_last_repair": sum(r["lines"] == "last repair" for r in later), "later_resolved": len(later),
        "later_by_lines": dict(Counter(r["lines"] for r in later)), "by_loop": loops,
    }


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", type=Path, help="a private folder for the rows: findings.jsonl, lines.jsonl, and labels.jsonl when made")
    ap.add_argument("roots", type=Path, nargs="*", help="folders holding review-* checkouts; none to count what out holds")
    args = ap.parse_args(argv)
    if args.roots:
        rows, clean = findings(args.roots)
        (args.out / "findings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
        lines = blamed(rows, args.roots)
        (args.out / "lines.jsonl").write_text("".join(json.dumps({"folder": f, "finding": n, "lines": k}) + "\n"
                                                      for (f, n), k in lines.items()), "utf-8")
        print(json.dumps({"findings": len(rows), "reports_with_none": [c["folder"] for c in clean]}))
        return
    rows = _jsonl(args.out / "findings.jsonl")
    labels = {(r["folder"], r["finding"]): r for r in _jsonl(args.out / "labels.jsonl")}
    lines = {(r["folder"], r["finding"]): r["lines"] for r in _jsonl(args.out / "lines.jsonl")}
    print(json.dumps({**analysis(rows, labels, lines), "came_back_by_chance": by_chance(rows, labels)}, indent=1))


if __name__ == "__main__":
    main()
