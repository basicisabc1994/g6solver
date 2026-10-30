# SPDX-License-Identifier: Apache-2.0
"""Every archived scout's probes on every archived patch of a training bug and on the maintainers' fix (PLAN.md,
2026-10-31). No model is called, and nothing here reaches an agent: it measures the probes, not a run.

A probe set is *wrong* where it fails on the maintainers' fix, and has *power* where it fails on a patch their tests reject.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import run as we
import wecommon as c

RUN = (".venv/bin/python -m pytest {probes} -q --tb=no -rfE -p no:cacheprovider -p no:pretty --rootdir={probes} "
       "2>&1 | tail -c 400000; true")
CLEAN = "git checkout -q . && git clean -fdq"
COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|deselected)\b")


def summary(output: str) -> dict[str, Any]:
    """What one pytest run says: its counts and the ids that failed. ``ran`` is false when no test ran at all."""
    tail = next((line for line in reversed(output.strip().splitlines()) if COUNT.search(line)), "")
    if "Results (" in output:     # a plugin's table, not pytest's summary: nothing here can be read as a pass
        tail = ""
    counts = {word: int(n) for n, word in COUNT.findall(tail)}
    failed = sorted(we.failed_ids(output))
    errors = counts.get("error", 0) + counts.get("errors", 0)
    return {"ran": counts.get("passed", 0) + counts.get("failed", 0) > 0, "passed": counts.get("passed", 0),
            "failed": counts.get("failed", 0), "errors": errors, "ids": failed}


def verdict(cell: dict[str, Any]) -> str:
    """A cell of the matrix: ``X`` the probes fail, ``.`` they pass, ``?`` they could not run."""
    if not cell["ran"] or cell["errors"]:
        return "?"
    return "X" if cell["failed"] else "."


def runs_of(root: Path, task: str) -> list[dict[str, Any]]:
    rows = []
    for saved in sorted((root / "loop").glob(f"*-{task}-*.json")):
        row = json.loads(saved.read_text("utf-8"))
        folder = root / str(row.get("run"))
        if (folder / "final.patch").is_file():
            rows.append({**row, "label": f"{row['arm']}-{row['repeat']}", "folder": folder})
    return rows


def replay_task(docker: Any, root: Path, task: we.Task, g6: str) -> dict[str, Any]:
    _, oracle = we.image_names(task, g6, we.ROUND3)
    rows = runs_of(root, task.id)
    sets = [{"label": r["label"] + tag, "probes": r["folder"] / folder} for r in rows
            for tag, folder in (("", "scout_probes"), ("h", "hidden_probes"), ("i", "hidden2_probes"))
            if (r["folder"] / folder).is_dir()]
    name = f"g6we-replay-{uuid.uuid4().hex[:10]}"
    matrix: dict[str, dict[str, Any]] = {}
    try:
        we.checked(docker("run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", *we.SECURITY, "-u",
                          we.USER, "-e", f"HOME={we.HOME}", "-e", "PYTHONDONTWRITEBYTECODE=1", "-w", task.repo, oracle,
                          "tail", "-f", "/dev/null", timeout=600), "replay container")
        for row in sets:
            probes = f"/tmp/p-{row['label']}"
            we.checked(docker("cp", str(row["probes"]), f"{name}:{probes}", timeout=300), "probes")
            docker.sh(name, f"find {probes} -name __pycache__ -prune -exec rm -rf {{}} +; chmod -R a+rX {probes}",
                      user="root", timeout=60)
        for code in ("base", "fix", *[r["label"] for r in rows]):
            docker.sh(name, CLEAN, workdir=task.repo, timeout=120)
            if code == "fix":
                we.checked(docker.sh(name, "git apply --binary --whitespace=nowarn /oracle/fix.patch", workdir=task.repo),
                           "the maintainers' fix")
            elif code != "base":
                patch = next(r for r in rows if r["label"] == code)["folder"] / "final.patch"
                if not patch.stat().st_size:
                    continue
                we.checked(docker("cp", str(patch), f"{name}:/tmp/we-{code}.patch", timeout=300), "patch")
                done = docker.sh(name, f"git apply --binary --whitespace=nowarn /tmp/we-{code}.patch", workdir=task.repo)
                if done.returncode != 0:
                    matrix[code] = {"unapplied": True}
                    continue
            matrix[code] = {r["label"]: summary(we.out(docker.sh(name, RUN.format(probes=f"/tmp/p-{r['label']}"),
                                                                  workdir=task.repo, timeout=300))) for r in sets}
    finally:
        docker("rm", "-f", name, timeout=300)
    return {"task": task.id, "sets": [r["label"] for r in sets],
            "patches": {r["label"]: bool(r["passes"]) for r in rows}, "matrix": matrix}


def table(got: dict[str, Any]) -> str:
    """Rows are the code, columns the probe sets: patches the maintainers' tests accept are marked ``+``."""
    sets = got["sets"]
    lines = [f"== {got['task']}", " " * 12 + " ".join(f"{s:>6}" for s in sets)]
    for code, cells in got["matrix"].items():
        if cells.get("unapplied"):
            continue
        mark = {"base": "B", "fix": "F"}.get(code) or ("+" if got["patches"].get(code) else "-")
        lines.append(f"{mark} {code:<10}" + " ".join(f"{verdict(cells[s]):>6}" for s in sets))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tasks", nargs="+")
    ap.add_argument("--g6", default=we.R3_LOOP_G6)
    args = ap.parse_args()
    if set(args.tasks) & set(we.R3_HELD):
        raise SystemExit("held-out bugs are not replayed: their runs stay unread")
    root, docker = c.archive_root() / "round3", we.Docker()
    tasks = {t.id: t for t in we.load_tasks()}
    commit = subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", args.g6], capture_output=True,
                            text=True, check=True).stdout.strip()
    results = []
    for task in args.tasks:
        got = replay_task(docker, root, tasks[task], commit)
        results.append(got)
        print(table(got), flush=True)
    out = root / "probe_replay"
    out.mkdir(exist_ok=True)
    (out / f"{c.now().replace(':', '')}.json").write_text(json.dumps(results, indent=1), "utf-8")


if __name__ == "__main__":
    main()
