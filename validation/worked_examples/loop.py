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


EXECUTOR = "container"      # run.py's --executor for every call: "host" runs Claude Code on the subscription (PLAN.md, 2026-11-04)


def tool(command: str, *args: str, timeout: int = 7800) -> str:
    return subprocess.run([sys.executable, str(HERE / "run.py"), command, "--round", "3", "--executor", EXECUTOR, *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout).stdout


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
    """One run and its independent check, saved under ``loop/`` once, with the digest of the ending it was made under. A saved row
    is given only while it holds (``wecommon.holds``): a row saved under another ending is void, and is made again from the run's
    ending as it is now, its check included, only if the run may be scored now; the run itself is never made again for it."""
    saved, archive = root / "loop" / f"{arm}-{task}-{repeat}.json", c.Archive(root)
    if saved.is_file():
        rec = json.loads(saved.read_text("utf-8"))
        if we.scored_run(archive, {**rec, "arm": arm}) and c.holds(root, rec, rec):    # admitted now, and saved under this ending
            return rec
    else:
        rec = last_json(tool("run", "--task", task, "--g6", g6, "--arm", arm, "--model", SONNET, "--repeat", str(repeat)))
        if not rec:          # the runner printed no record: nothing is known of the run, so nothing is saved
            raise SystemExit(f"no record: {arm} {task} {repeat}: the runner printed none")
        if rec.get("status") == "refused" and "already made" in str(rec.get("why")):   # made before a crash: check it
            rec = [r for r in distill.main_row(root / c.NDJSON["ledger"])
                   if (r.get("task"), r.get("arm"), r.get("repeat")) == (task, arm, repeat)][-1]
    end, made = c.ending(root, rec), c.ending_sha256(root, rec)     # how the run ended, from its own folder (``wecommon.ending``)
    why = we.unscored_why(archive, {**rec, "arm": arm})   # the one admission rule: refused, limited, no ending, or K3's baseline
    if why:              # refused, never counted as a failed run; limited: made again after the reset; a K3 run with no baseline
        status = (end or {}).get("status") or "no ending"
        said = (((rec.get("limit") or {}).get("resets") or "no reset given") if status == "limited"
                else "; ".join(str(x) for x in (why, (end or {}).get("why") or rec.get("why")) if x))
        raise SystemExit(f"{status}: {arm} {task} {repeat}: {said}")
    row = {"arm": arm, "task": task, "repeat": repeat, "run": rec.get("run"), "status": end.get("status"),
           "why": rec.get("why"), "cost_usd": rec.get("cost_usd"), "passes": False, "failing": None, "tests": [],
           **({c.MADE_UNDER: made} if made else {})}
    if c.on_patch(end):      # finished, or ended by a model refusal: scored on the patch it left
        final = [x for x in last_json(tool("check", "--task", task, "--g6", g6, "--run", rec["run"], timeout=1200)).get("checks", [])
                 if x.get("patch") == "final"]     # by name: with no run named, check scores the bug's latest run, which a restart may not be
        if not final:       # no verdict is not a failed run: nothing is saved, and a restart checks it again
            raise SystemExit(f"no verdict for {arm} {task} {repeat}")
        failing, tests = final[-1].get("failing"), list(final[-1].get("fail_to_pass_failing") or [])
        row.update(passes=bool(final[-1].get("passes")), tests=tests[:20],
                   failing=failing if isinstance(failing, int) else len(failing or []))
    saved.parent.mkdir(exist_ok=True)
    saved.write_text(json.dumps(row, indent=1), "utf-8")
    print(json.dumps({k: row[k] for k in ("arm", "task", "repeat", "status", "passes", "failing", "cost_usd")}), flush=True)
    return row


def rescore(root: Path, g6: str) -> list[dict[str, Any]]:
    """Every saved run of a bug with an amendment, its final patch scored on the amended tests, saved beside the
    maintainers' verdict under ``loop/amended/`` (PLAN.md, 2026-10-31). A verdict holds for one run, one diff and the ending it was
    made under (``wecommon.holds``), and is scored again if any changes. No model; the saved rows stay as they are."""
    rows, archive = [], c.Archive(root)
    for path in sorted((root / "loop").glob("*-*.json")):
        row, saved = json.loads(path.read_text("utf-8")), root / "loop" / "amended" / path.name
        diff = we.amendment(row["task"])
        end, made = c.ending(root, row), c.ending_sha256(root, row)   # a run that may not be scored has no verdict: none is made
        if not diff or not we.scored_run(archive, row):                # the one admission rule (run.unscored_why)
            continue
        sha = c.sha256_file(diff)
        kept = json.loads(saved.read_text("utf-8")) if saved.is_file() else None
        if c.holds(root, row, kept, amendment=sha):
            rows.append(kept)
            continue
        passes = False      # a made run that did not end on its patch failed on either score, with no check
        if c.on_patch(end):
            final = [x for x in last_json(tool("check", "--task", row["task"], "--g6", g6, "--run", row["run"], "--amended",
                                               timeout=1200)).get("checks", []) if x.get("patch") == "final"]
            passes = final[-1].get("passes") if final else None
            if not isinstance(passes, bool):       # no verdict is not a failed run
                raise SystemExit(f"no amended verdict for {path.stem}")
        rows.append({**{k: row.get(k) for k in ("arm", "task", "repeat", "run", "passes")}, "amended_passes": passes,
                     "amendment_sha256": sha, **({c.MADE_UNDER: made} if made else {})})
        saved.parent.mkdir(exist_ok=True)
        saved.write_text(json.dumps(rows[-1], indent=1), "utf-8")
    return rows


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


def main(argv: list[str] | None = None) -> None:
    global EXECUTOR
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6)
    ap.add_argument("--rescore", action="store_true", help="score saved runs on the amended tests, and nothing else")
    ap.add_argument("--executor", choices=("container", "host"), default="container",
                    help="run.py's executor for every run and check: host runs Claude Code on the host, on the subscription")
    args = ap.parse_args(argv)
    EXECUTOR = args.executor
    root = c.archive_root() / "round3"
    if args.rescore:
        print(json.dumps(rescore(root, args.g6), indent=1), flush=True)
        return
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
