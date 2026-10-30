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

HERE = Path(__file__).resolve().parent
TARGET, MAX_GENERATIONS, MAX_MISSES, STOP_USD = 0.95, 8, 3, 170.0
SONNET = we.R3_MODELS[1]
TRAILERS = ("Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_016opK1M2VFbXVve6kiJtAxW")


def tool(command: str, *args: str, timeout: int = 2400) -> str:
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
    if rec.get("status") == "refused":   # never counted as a failed run
        raise SystemExit(f"refused: {arm} {task} {repeat}: {rec.get('why')}")
    row = {"arm": arm, "task": task, "repeat": repeat, "run": rec.get("run"), "status": rec.get("status"),
           "why": rec.get("why"), "cost_usd": rec.get("cost_usd"), "passes": False, "failing": None, "tests": []}
    if rec.get("status") == "finished":
        final = [x for x in last_json(tool("check", "--task", task, "--g6", g6, timeout=1200)).get("checks", [])
                 if x.get("patch") == "final"]
        if final:
            failing = list(final[-1].get("failing") or [])
            row.update(passes=bool(final[-1].get("passes")), failing=len(failing), tests=failing[:20])
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
                    message + " [skip ci]", "-m", TRAILERS], check=True)
    subprocess.run([*git, "push", "-q", "origin", "HEAD"], check=True)


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

    best_arm, best_text = "E", we.ADDENDUM_D
    best = [one(root, args.g6, "E", t, r) for t in train for r in (1, 2)]
    misses, notes = 0, []
    caller = distill.Metered(distill.llm.AnthropicCaller(distill.MODEL, api_key=we.read_key()))
    for n in range(1, MAX_GENERATIONS + 1):
        if score(best) >= TARGET or misses >= MAX_MISSES:
            break
        path = we.HARNESS / f"G{n}.txt"
        if not path.is_file():
            failed = [{**r, "run": r["run"]} for r in best if not r["passes"] and r["run"]]
            text, bad = distill.distill(best_text, failed, root, caller, notes)
            folder = c.Archive(root).start("distill", f"G{n}", parent=best_arm)
            folder.write("text.txt", text)
            folder.finish({"cost_usd": caller.cost_usd, "named": bad, "parent": best_arm}, "finished" if text else "error")
            caller.cost_usd = 0.0
            if bad or not text:
                print(f"== G{n}: refused, it names {bad}", flush=True)
                notes.append(f"An earlier attempt was refused for naming {bad}; name nothing of the kind.")
                misses += 1
                continue
            we.HARNESS.mkdir(exist_ok=True)
            path.write_text(text + "\n", "utf-8", newline="\n")
            commit(path, f"worked examples round 3: harness generation {n}")
        rows = round_of(root, args.g6, f"G{n}", train)
        print(f"== G{n}: train {score(rows):.2f} against {best_arm}'s {score(best):.2f}", flush=True)
        if score(rows) > score(best):
            best_arm, best_text, best, misses = f"G{n}", path.read_text("utf-8").strip(), rows, 0
            notes = []
        else:
            misses += 1
            notes.append(f"An attempt that scored {score(rows):.2f} did no better than {score(best):.2f}: "
                         f"{path.read_text('utf-8')[:1500]}")
    print(f"== best {best_arm}: train {score(best):.2f}", flush=True)
    if best_arm != "E":
        held = round_of(root, args.g6, "H" + best_arm[1:], we.R3_HELD)
        print(f"== held-out: {score(held):.2f}", flush=True)
    print(f"== spent ${spent(root):.2f}", flush=True)


if __name__ == "__main__":
    main()
