# SPDX-License-Identifier: Apache-2.0
"""Harness training (PLAN.md, stage 3): G6 proposes a change to the loop from its own failure records, and paired runs decide.

G6's ``cegis`` runs the outer loop, as in ``loop.py``. ``FailureTraceStrategy`` writes each candidate, one for each of PR #4's
operators, a model picks which part of the harness it adds to, and PR #4's ``assess`` compares it with its parent bug by bug. No
text is written in session. Each one is committed before it runs. What the proposer reads comes from training and development
bugs only, and ``records`` refuses any bug a harness is judged on.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import agentic
import compare
import distill
import heuristics
import loop
import run as we
import wecommon as c
from g6.cegis import InputDomain, SearchResult, cegis

#: What each operator asks the proposer to do. They are ways of working, so none can carry a fact about a bug.
WAYS = {"regression": "Write a way to keep what already works: what to check about behaviour outside the issue's scope.",
        "boundary": "Write a way to find the inputs at the edges of the rule the issue implies, not only the issue's example.",
        "counterexample": "Write a way to generalise from one failing case to the rule behind it, before repairing anything.",
        "metamorphic": "Write a way to check the rule without knowing exact outputs: relations that must hold between related inputs.",
        "decompose": "Write a way to split what the issue requires into separately testable behaviours, so that none is left out.",
        "prompt_revision": "Write whatever one change would most have prevented these failures."}
FIELDS = {"fixer": "instructions the fixing agent reads before it edits the code",
          "scout": "method for the scouts, who work out what a complete fix must handle before any patch exists",
          "repair": "instructions the fixing agent reads when the runner sends back what failed",
          "critic": "an added step for the reviewer who reads the finished patch and writes a test"}
#: The fallback when no model chooses the field.
DEFAULT_FIELD = {"regression": "fixer", "boundary": "scout", "counterexample": "repair", "metamorphic": "scout",
                 "decompose": "scout", "prompt_revision": "fixer"}
FIELD_SYSTEM = ("A coding agent fixes bugs inside a harness with several parts. From the failures summarised here, choose the "
                "one part where the given way of working would prevent them soonest.")
ADD_ONLY = ("Your text is added to that part's existing instructions, which stay. Write only what to add, at most 120 words, "
            "as instructions. It cannot change how results are scored.")
NEAR_COPY = 0.8
MAX_CASES, MAX_RECORDS = 6, 8


def judged_on() -> set[str]:
    """The bugs a harness is judged on: nothing about them reaches the proposer."""
    return {*we.R3_HELD, *(t.id for path in (we.TASKS_FRESH, we.TASKS_UNSEEN) for t in we.load_tasks(path))}


def verdicts(root: Path, arm: str, tasks: Sequence[str], repeats: Sequence[int] = (1, 2)) -> list[dict[str, Any]]:
    """The saved runs of ``arm`` on ``tasks``, each with ``pass`` on the amended tests, which are the maintainers' on most bugs."""
    rows, found = compare.saved(root), []
    for task in tasks:
        for repeat in repeats:
            got = rows.get((arm, task, repeat))
            if got is None:
                continue
            if got["amended"] is None:
                raise ValueError(f"no verdict for {arm} {task} {repeat}: rescore first")
            row = json.loads((root / "loop" / f"{arm}-{task}-{repeat}.json").read_text("utf-8"))
            found.append({**row, "pass": got["amended"]})
    return found


def near_miss(folder: Path) -> str:
    """What the runner's own checks caught on the first patch, and what became of it: the critic's rule and reasons, then for each
    set how many failed, how many of those failed before the patch too, and how many still fail. That last pair is how a set of
    mistaken probes shows on a run that ended well. It needs no maintainers' test, so a passing run can still teach something."""
    rounds: dict[str, dict[int, list[str]]] = {}
    for path in folder.glob("repair_check_*_*.json"):
        _, _, n, label = path.stem.split("_", 3)
        results = json.loads(path.read_text("utf-8")).get("results") or {}
        rounds.setdefault(label, {})[int(n)] = [case for case, got in results.items() if got in ("failed", "error")]
    if not any(seen.get(1) for seen in rounds.values()):
        return ""
    at_base = json.loads((folder / "probes_at_base.json").read_text("utf-8")) if (folder / "probes_at_base.json").is_file() else {}
    lines = []
    for label, seen in sorted(rounds.items()):
        first = seen.get(1) or []
        if not first:
            continue
        results = (at_base.get(label) or {}).get("results")
        before = f"{sum(results.get(c) in ('failed', 'error') for c in first)} of them failing before it" if results else "unknown before it"
        lines.append(f"{label}: {len(first)} failing on the first patch, {before}, {len(seen[max(seen)])} at the end")
        lines += [f"  {case}" for case in first[:MAX_CASES]] + ([f"  (and {len(first) - MAX_CASES} more)"] if len(first) > MAX_CASES else [])
    reply = folder / "critic_reply.txt"
    critic = we.critic_report(reply.read_text("utf-8", errors="replace")) if reply.is_file() else {}
    return "\n".join(text for text in (critic.get("rule"), critic.get("why"), *lines) if text)


def diverse(rows: Sequence[Mapping[str, Any]], limit: int = MAX_RECORDS) -> list[Mapping[str, Any]]:
    """Up to ``limit`` records, each bug in turn and failed runs first: the proposer reads only the first eight it is given, and
    runs on one bug are not independent."""
    queues: dict[str, list[Mapping[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: r["kind"] != "failed"):
        queues.setdefault(row["task"], []).append(row)
    picked: list[Mapping[str, Any]] = []
    while len(picked) < limit and any(queues.values()):
        picked += [queue.pop(0) for queue in queues.values() if queue][:limit - len(picked)]
    return picked


def records(root: Path, arm: str, tasks: Sequence[str]) -> list[dict[str, Any]]:
    """The failure records of ``arm``: runs that failed, and near misses."""
    refused = sorted(set(tasks) & judged_on())
    if refused:
        raise ValueError(f"these bugs are judged on, and never trained on: {refused}")
    found = []
    for row in verdicts(root, arm, tasks):
        if not row["pass"] and row.get("run"):
            found.append({**row, "kind": "failed"})
        elif row.get("run"):
            caught = near_miss(root / row["run"])
            if caught:
                found.append({**row, "kind": "near_miss", "caught": caught, "tests": []})
    return found


def paired(heuristic_id: str, operator: str, base: Sequence[Mapping[str, Any]], cand: Sequence[Mapping[str, Any]],
           digest: str) -> dict[str, Any]:
    """The candidate against its parent, bug by bug, on the runs both made. Runs are noisy, so a bug counts as solved if any run
    passed: one slipped run is no veto, and losing a bug outright is. It must also pass more runs: being cheaper is not a gain."""
    keys = {(r["task"], r["repeat"]) for r in cand}
    missing = sorted(keys - {(r["task"], r["repeat"]) for r in base})
    if missing:
        raise ValueError(f"missing from the parent: {missing}")
    base = [r for r in base if (r["task"], r["repeat"]) in keys]
    bugs = tuple(sorted({task for task, _ in keys}))

    def evaluator(heuristic: heuristics.Heuristic, bug: str) -> heuristics.Trial:
        old, new = ([r for r in rows if r["task"] == bug] for rows in (base, cand))
        return heuristics.Trial(heuristic.id, bug, any(r["pass"] for r in old), any(r["pass"] for r in new),
                                round(sum(float(r.get("cost_usd") or 0) for r in old) * 100),
                                round(sum(float(r.get("cost_usd") or 0) for r in new) * 100), digest)

    said = heuristics.assess(heuristics.Heuristic(heuristic_id, operator), bugs, evaluator, digest)
    gained = sum(bool(r["pass"]) for r in cand) - sum(bool(r["pass"]) for r in base)
    return {"trials": [asdict(t) for t in said["trials"]], "gained": gained, "recommended": bool(said["recommended"]) and gained > 0}


def similar(one: str, two: str) -> float:
    a, b = (set(re.findall(r"\w+", text.lower())) for text in (one, two))
    return len(a & b) / len(a | b) if a | b else 1.0


def tracked(path: Path) -> bool:
    return subprocess.run(["git", "-C", str(path.parent), "ls-files", "--error-unmatch", path.name], capture_output=True).returncode == 0


def projects() -> tuple[str, ...]:
    return (*distill.PROJECTS, *(t.project.split("/", 1)[1] for t in we.load_tasks(we.TASKS_DEV)))


class Training:
    """Stands where ``cegis`` has a runner: ``search`` is G6's proposer, ``evaluate`` a cheap screen on what the parent failed
    and then the paired check on every bug. It stops at the first candidate recommended: each full check costs real money."""

    def __init__(self, root: Path, g6: str, tasks: Sequence[str], parent: str, caller: Any, first: int, candidates: int,
                 committed: Callable[[Path], bool]) -> None:
        self.root, self.g6, self.tasks, self.parent, self.caller = root, g6, list(tasks), parent, caller
        self.committed, self.n = committed, first - 1
        self.rows = records(root, parent, tasks)
        self.failed = sorted({r["task"] for r in self.rows if r["kind"] == "failed"})
        self.ahead = list(heuristics.mutate(heuristics.Heuristic("root", "prompt_revision"), max_children=candidates))
        self.text, self.digest = we.trained(parent), c.sha256_file(Path(we.__file__))
        self.best, self.stopped, self.made, self.seen = parent, False, [], []

    def search(self, sketch: Any, examples: Any, *, max_exhaustive: int = 0) -> SearchResult:
        while self.ahead and self.best == self.parent:
            if loop.spent(self.root) >= loop.STOP_USD:
                self.stopped = True
                break
            self.n += 1
            self.made.append(self.proposed(self.ahead.pop(0), f"L{self.n}"))
            if self.made[-1]["outcome"] == "to run":
                return SearchResult({0: self.made[-1]["arm"]}, "proposed", False, self.n)
        return SearchResult(None, "stopped", False, self.n)

    def proposed(self, child: heuristics.Heuristic, arm: str) -> dict[str, Any]:
        path, spec = we.HARNESS / f"{arm}.json", self.root / "loop" / f"evolve_{arm}.json"
        entry: dict[str, Any] = {"arm": arm, "id": child.id, "operator": child.operator}
        if path.is_file() and spec.is_file() and self.committed(path):    # a restart: this text was proposed and committed
            entry.update(json.loads(spec.read_text("utf-8")), outcome="to run")
            self.seen.append(entry["text"])
            return entry
        way = WAYS[child.operator]
        rows = diverse(self.rows)
        summary = [{"kind": r["kind"], "caught": str(r.get("caught") or "")[:600]} for r in rows]
        field, decision = agentic.choice(self.caller, "field", FIELD_SYSTEM, {"way": way, "parts": FIELDS, "failures": summary},
                                         tuple(FIELDS), DEFAULT_FIELD[child.operator])
        try:
            text, bad = distill.distill(self.text.get(field, ""), rows, self.root, self.caller,
                                        [way, f"Your text is the {FIELDS[field]}.", ADD_ONLY], projects=projects())
        except distill.llm.LLMError as exc:
            text, bad = "", [f"the model call failed: {exc}"]
        entry.update(field=field, decision=decision.as_dict(), text=text)
        folder = c.Archive(self.root).start("distill", arm, parent=self.parent)
        folder.write("text.txt", text)
        if not text:
            folder.write("reply.txt", str(getattr(self.caller, "last", "")))
        folder.finish({"cost_usd": getattr(self.caller, "cost_usd", 0.0), "named": bad, "parent": self.parent,
                       "operator": child.operator, "field": field}, "finished" if text else "error")
        self.caller.cost_usd = 0.0
        if not text:
            return {**entry, "outcome": f"refused: no text {bad}".rstrip(" []")}
        if bad:
            return {**entry, "outcome": f"refused: named {bad}"}
        if any(similar(text, old) >= NEAR_COPY for old in self.seen):
            return {**entry, "outcome": "refused: a near copy of an earlier candidate"}
        self.seen.append(text)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({**self.text, field: text}, indent=1) + "\n", "utf-8", newline="\n")
        loop.commit(path, f"worked examples round 3: harness candidate {arm}, proposed by G6")
        spec.write_text(json.dumps({k: entry[k] for k in ("operator", "field", "text", "decision")}, indent=1), "utf-8")
        return {**entry, "outcome": "to run"}

    def evaluate(self, sketch: Any, controls: Any, inputs: Any) -> list[list[dict[str, bool]]]:
        entry, none = self.made[-1], [[{"ok": False} for _ in inputs]]
        arm = entry["arm"]
        try:
            loop.round_of(self.root, self.g6, arm, self.failed, (1,))
            loop.rescore(self.root, self.g6)
            if not any(r["pass"] for r in verdicts(self.root, arm, self.failed, (1,))):
                entry["outcome"] = "dropped at the screen"
                return none
            loop.round_of(self.root, self.g6, arm, self.tasks)
            loop.rescore(self.root, self.g6)
        except SystemExit as exc:     # the round's stop, or a refusal: nothing here counts as a failed candidate
            entry["outcome"], self.stopped = f"stopped: {exc}", True
            return none
        cand = verdicts(self.root, arm, self.tasks)
        entry["paired"] = paired(entry["id"], entry["operator"], verdicts(self.root, self.parent, self.tasks), cand, self.digest)
        entry["outcome"] = "recommended" if entry["paired"]["recommended"] else "not better"
        if entry["paired"]["recommended"]:
            self.best = arm
        passes = {(r["task"], r["repeat"]): bool(r["pass"]) for r in cand}
        return [[{"ok": passes.get((i["task"], i["repeat"]), False)} for i in inputs]]


def train(root: Path, g6: str, tasks: Sequence[str], parent: str, caller: Any, *, first: int, candidates: int = 4,
          committed: Callable[[Path], bool] = tracked) -> dict[str, Any]:
    """Candidates from ``parent``'s failure records, tried in turn: the best arm, and everything that happened to each."""
    runner = Training(root, g6, tasks, parent, caller, first, candidates, committed)
    said: dict[str, Any] = {"status": "not run", "reason": "the parent failed nothing, so training could show no gain"}
    if runner.failed:
        got = cegis(loop.Harness(), lambda **_: True, domain=InputDomain.of({"task": list(tasks), "repeat": [1, 2]}), runner=runner,
                    max_iterations=candidates, timeout_s=7 * 86400.0)
        said = {"status": got.status, "reason": got.reason}
    out = {"parent": parent, "best": runner.best, "stopped": runner.stopped, "cegis": said, "candidates": runner.made,
           "records": {"failed": sum(r["kind"] == "failed" for r in runner.rows),
                       "near_miss": sum(r["kind"] == "near_miss" for r in runner.rows)}}
    (root / "loop" / "evolve.json").write_text(json.dumps(out, indent=1, default=str), "utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6)
    ap.add_argument("--parent", default="L9")
    ap.add_argument("--first", type=int, default=10)
    ap.add_argument("--candidates", type=int, default=4)
    ap.add_argument("--tasks", nargs="+", required=True)
    args = ap.parse_args()
    caller = distill.Metered(distill.llm.AnthropicCaller(distill.MODEL, api_key=we.read_key()))
    got = train(c.archive_root() / "round3", args.g6, args.tasks, args.parent, caller, first=args.first, candidates=args.candidates)
    print(json.dumps({k: got[k] for k in ("parent", "best", "stopped", "cegis", "records")}, indent=1), flush=True)
    print(json.dumps([{k: e.get(k) for k in ("arm", "operator", "field", "outcome")} for e in got["candidates"]], indent=1), flush=True)


if __name__ == "__main__":
    main()
