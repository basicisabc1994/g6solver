# SPDX-License-Identifier: Apache-2.0
"""Round 3's distillation loop (PLAN.md, 2026-10-30): baseline, then generations, then one look at the held-out bugs.

Every run goes through ``run.py``, so its refusals and the round's stop apply. A result already saved is not run again.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import distill
import run as we
import wecommon as c
from g6.cegis import InputDomain, SearchResult, cegis

HERE = Path(__file__).resolve().parent
TARGET, MAX_GENERATIONS, MAX_MISSES, STOP_USD = 0.95, 8, 3, 170.0
SONNET = we.R3_MODELS[1]
TRAILERS = ("Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_016opK1M2VFbXVve6kiJtAxW")


def tool(command: str, *args: str, timeout: int = 7800) -> str:
    return subprocess.run([sys.executable, str(HERE / "run.py"), command, "--round", "3", *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout


def last_json(text: str) -> dict[str, Any]:
    """Every JSON object ``text`` prints, merged: the runner prints its record and then the ledger's line."""
    found: dict[str, Any] = {}
    decoder, at = json.JSONDecoder(), text.find("{")
    while at >= 0:
        try:
            value, end = decoder.raw_decode(text, at)
        except ValueError:
            at = text.find("{", at + 1)
            continue
        found.update(value)
        at = text.find("{", end)
    return found


def one(root: Path, g6: str, arm: str, task: str, repeat: int) -> dict[str, Any]:
    """One run and its independent check, saved under ``loop/`` once."""
    saved = root / "loop" / f"{arm}-{task}-{repeat}.json"
    if saved.is_file():
        return json.loads(saved.read_text("utf-8"))
    said = tool("run", "--task", task, "--g6", g6, "--arm", arm, "--model", SONNET, "--repeat", str(repeat))
    rec = last_json(said)
    if rec.get("status") == "refused" and "already made" in str(rec.get("why")):   # made before a crash: check it
        rec = [r for r in distill.main_row(root / "ledger.jsonl")
               if (r.get("task"), r.get("arm"), r.get("repeat")) == (task, arm, repeat)][-1]
    if rec.get("status") == "refused":   # never counted as a failed run
        raise SystemExit(f"refused: {arm} {task} {repeat}: {rec.get('why')}")
    row = {"arm": arm, "task": task, "repeat": repeat, "run": rec.get("run"), "status": rec.get("status"),
           "why": rec.get("why"), "cost_usd": rec.get("cost_usd"), "passes": False, "failing": None, "tests": []}
    if rec.get("status") == "finished":
        final = [x for x in last_json(tool("check", "--task", task, "--g6", g6, timeout=1200)).get("checks", [])
                 if x.get("patch") == "final"]
        if final:
            failing, tests = final[-1].get("failing"), list(final[-1].get("fail_to_pass_failing") or [])
            row.update(passes=bool(final[-1].get("passes")), tests=tests[:20],
                       failing=failing if isinstance(failing, int) else len(failing or []))
    saved.parent.mkdir(exist_ok=True)
    saved.write_text(json.dumps(row, indent=1), "utf-8")
    print(json.dumps({k: row[k] for k in ("arm", "task", "repeat", "status", "passes", "failing", "cost_usd")}), flush=True)
    return row


def spent(root: Path) -> float:
    return c.spent(c.Archive(root).rows())


def round_of(root: Path, g6: str, arm: str, tasks, repeats=(1, 2)) -> list[dict[str, Any]]:
    rows = []
    for task in tasks:
        for repeat in repeats:
            if spent(root) >= STOP_USD:
                raise SystemExit(f"stopped: ${spent(root):.2f} spent, the loop's stop is ${STOP_USD:.0f}")
            rows.append(one(root, g6, arm, task, repeat))
    return rows


def score(rows) -> float:
    return sum(r["passes"] for r in rows) / max(1, len(rows))


def commit(path: Path, message: str) -> None:
    git = ["git", "-C", str(HERE)]
    subprocess.run([*git, "add", str(path)], check=True)
    subprocess.run([*git, "-c", "user.name=Claude", "-c", "user.email=noreply@anthropic.com", "commit", "-q", "-m",
                    message + " [skip ci]", "-m", TRAILERS, "--", str(path)], check=True)
    subprocess.run([*git, "push", "-q", "origin", "HEAD"], check=True)


class Harness:
    """Stands where ``cegis`` has a sketch: the candidate is a generation's text, not a filled-in function."""

    @staticmethod
    def instantiate(control: dict[int, str]) -> str:
        return control[0]


class Generations:
    """G6's CEGIS loop over the harness: ``search`` distils the next text from what failed, ``evaluate`` runs it on every
    training bug. The oracle's answer is the maintainers' tests, seen only as each run's independent check."""

    def __init__(self, root: Path, g6: str, train: list[str], arm: str, text: str, rows: list[dict[str, Any]],
                 caller: Any) -> None:
        self.root, self.g6, self.train, self.caller = root, g6, train, caller
        self.best_arm, self.best_text, self.best = arm, text, rows
        self.n, self.misses, self.notes = 0, 0, []

    def search(self, sketch: Any, examples: Any, *, max_exhaustive: int = 0) -> SearchResult:
        while self.n < MAX_GENERATIONS and self.misses < MAX_MISSES and score(self.best) < TARGET:
            self.n += 1
            path = we.HARNESS / f"G{self.n}.txt"
            if path.is_file() or self.distilled(path):
                return SearchResult({0: f"G{self.n}"}, "distilled", False, self.n)
        return SearchResult(None, "stopped", False, self.n)

    def distilled(self, path: Path) -> bool:
        if spent(self.root) >= STOP_USD:
            self.misses = MAX_MISSES
            return False
        failed = [r for r in self.best if not r["passes"] and r["run"]]
        try:
            text, bad = distill.distill(self.best_text, failed, self.root, self.caller, self.notes)
        except distill.llm.LLMError as exc:
            text, bad = "", [f"the model call failed: {exc}"]
        folder = c.Archive(self.root).start("distill", path.stem, parent=self.best_arm)
        folder.write("text.txt", text)
        cost = getattr(self.caller, "cost_usd", 0.0)
        folder.finish({"cost_usd": cost, "named": bad, "parent": self.best_arm}, "finished" if text else "error")
        if self.caller is not None:
            self.caller.cost_usd = 0.0
        if bad or not text:
            print(f"== {path.stem}: refused, it names {bad}", flush=True)
            self.notes.append(f"An earlier attempt was refused for naming {bad}; name nothing of the kind.")
            self.misses += 1
            return False
        we.HARNESS.mkdir(exist_ok=True)
        path.write_text(text + "\n", "utf-8", newline="\n")
        commit(path, f"worked examples round 3: harness generation {self.n}")
        return True

    def evaluate(self, sketch: Any, controls: Any, inputs: Any) -> list[list[dict[str, bool]]]:
        arm, path = controls[0][0], we.HARNESS / f"{controls[0][0]}.txt"
        rows = round_of(self.root, self.g6, arm, self.train)
        print(f"== {arm}: train {score(rows):.2f} against {self.best_arm}'s {score(self.best):.2f}", flush=True)
        if score(rows) > score(self.best):
            self.best_arm, self.best_text, self.best, self.misses = arm, path.read_text("utf-8").strip(), rows, 0
            self.notes = []
        else:
            self.misses += 1
            self.notes.append(f"An attempt that scored {score(rows):.2f} did no better than {score(self.best):.2f}: "
                              f"{path.read_text('utf-8')[:1500]}")
        passes = {(r["task"], r["repeat"]): r["passes"] for r in rows}
        return [[{"ok": passes[(i["task"], i["repeat"])]} for i in inputs]]


def generations(root: Path, g6: str, train: list[str], arm: str, text: str, rows: list[dict[str, Any]],
                caller: Any) -> tuple[Any, str, list[dict[str, Any]]]:
    """``cegis`` over harness text, with the training bugs and repeats as its domain: its result, and the best arm."""
    runner = Generations(root, g6, train, arm, text, rows, caller)
    domain = InputDomain.of({"task": train, "repeat": [1, 2]})
    result = cegis(Harness(), lambda **_: True, domain=domain, runner=runner, max_iterations=MAX_GENERATIONS,
                   timeout_s=7 * 86400.0)
    return result, runner.best_arm, runner.best


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6)
    args = ap.parse_args()
    root = c.archive_root() / "round3"
    tasks = [t.id for t in we.load_tasks()]
    train = [t for t in tasks if t not in we.R3_HELD]
    for arm in ("A", "E"):
        rows = round_of(root, args.g6, arm, tasks)
        print(f"== {arm}: all {score(rows):.2f}, train {score([r for r in rows if r['task'] in train]):.2f}", flush=True)

    best = [one(root, args.g6, "E", t, r) for t in train for r in (1, 2)]
    caller = distill.Metered(distill.llm.AnthropicCaller(distill.MODEL, api_key=we.read_key()))
    result, best_arm, best = generations(root, args.g6, train, "E", we.ADDENDUM_D, best, caller)
    (root / "loop" / "cegis.json").write_text(json.dumps(
        {"status": result.status, "iterations": result.iterations, "counterexamples": result.counterexamples,
         "boundary": result.boundary, "reason": result.reason, "best": best_arm}, indent=1, default=str), "utf-8")
    print(f"== best {best_arm}: train {score(best):.2f}", flush=True)
    if best_arm != "E":
        repeats = range(1, we.R3_HELD_REPEATS + 1)
        base, held = (round_of(root, args.g6, arm, we.R3_HELD, repeats) for arm in ("A", "H" + best_arm[1:]))
        print(f"== held-out: {score(held):.2f} against arm A's {score(base):.2f}", flush=True)
    print(f"== spent ${spent(root):.2f}", flush=True)


if __name__ == "__main__":
    main()
