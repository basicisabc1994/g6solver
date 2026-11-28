# SPDX-License-Identifier: Apache-2.0
"""Findings classes and recurrence statistics ported from validation/own_code/ledger.py.

Pure counting and report reads only; no git, model or tool calls.
"""
from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

#: The review folders' prefixes and the branch each loop reviewed, longest prefix first.
LOOPS = (("rpf", "claude/replays-followup"), ("rp", "claude/replays"), ("r3", "claude/replay3"), ("rca", "claude/tick-rca"),
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
                             "rule": _field(body, "Rule"), "class_said": _field(body, "Class"), "why": _field(body, "Why"),
                             "test": _field(body, "Test"),
                             "cites": sorted(set(re.findall(r"[\w./-]+\.py:\d+", body)))})
    return rows, clean


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


def met_before(rows: Sequence[Mapping[str, Any]], labels: Mapping[tuple[str, int], Mapping[str, str]],
               row: Mapping[str, Any]) -> list[tuple[str, int]]:
    """The findings of ``row``'s class in an earlier round of its own loop or in any other loop. Asked before a repair: if
    any, the repair removes the class (``CHECKS``), since patching another instance is the same mistake made twice."""
    cls = _class(labels[row["folder"], row["finding"]])
    return sorted((r["folder"], r["finding"]) for r in rows if _class(labels[r["folder"], r["finding"]]) == cls
                  and (r["branch"] != row["branch"] or r["round"] < row["round"]))


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
