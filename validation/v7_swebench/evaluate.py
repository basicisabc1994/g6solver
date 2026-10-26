# SPDX-License-Identifier: Apache-2.0
"""V7's oracle (PROTOCOL.md §6): the benchmark's own verifier on a saved patch, in a pristine container.

It does what the official locked protocol's re-grader does (``v2/tooling/patch_replay.py`` under Harbor 0.22's
verifier): a fresh container from the task's image; the patch applied with the re-grader's command (nothing when the
patch is empty or missing); the task's ``tests/`` folder copied to ``/tests``; ``/tests/test.sh`` run as root with its
output to ``/logs/verifier/test-stdout.txt``; the reward read from ``/logs/verifier/reward.json`` or ``reward.txt``.
The task's tests are copied by path and never opened here; the verifier's logs are saved beside the record, unread.
No agent ever sees this container. Standard library only.

``run_arm.grade_run`` calls ``verify`` for every graded patch, each in its own archived run. This file's command is
the oracle self-test on the scored tasks that completed, after every arm:

    python -I validation/v7_swebench/evaluate.py selftest --harness <v2 folder> [--archive <archive>]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import v7common as c  # noqa: E402

APPLY = ("cd /app 2>/dev/null || cd /testbed; git apply --verbose /tmp/replay.patch "
         "|| git apply --3way /tmp/replay.patch || patch --fuzz=3 -p1 -i /tmp/replay.patch")   # patch_replay.py
VERIFY = "(/tests/test.sh) > /logs/verifier/test-stdout.txt 2>&1"                               # Harbor's command


def _out(cp: subprocess.CompletedProcess) -> str:
    return (cp.stdout or b"").decode("utf-8", errors="replace").strip()


def _docker(runner: Callable[..., Any] = subprocess.run) -> Callable[..., subprocess.CompletedProcess]:
    def call(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        return runner(["docker", *args], capture_output=True, timeout=timeout)
    return call


def task_settings(harness: Path, instance_id: str) -> dict[str, Any]:
    folder = harness / "tasks" / instance_id
    toml = tomllib.loads((folder / "task.toml").read_text(encoding="utf-8"))
    env = toml["environment"]
    if (folder / "environment" / "Dockerfile").read_text(encoding="utf-8").strip() != f"FROM {env['docker_image']}":
        raise SystemExit(f"{instance_id}: the Dockerfile is more than FROM <image>")
    return {"image": env["docker_image"], "cpus": float(env.get("cpus") or 1), "memory_mb": int(env.get("memory_mb")
            or 4096), "timeout_s": float(toml["verifier"]["timeout_sec"]), "tests": folder / "tests"}


def parse_reward(folder: Path) -> float | None:
    """reward.json (its "reward") first, then reward.txt, as Harbor reads them."""
    json_path, text_path = folder / "reward.json", folder / "reward.txt"
    try:
        if json_path.is_file() and json_path.stat().st_size:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return float(data["reward"]) if isinstance(data, dict) else float(data)
        if text_path.is_file() and text_path.stat().st_size:
            return float(text_path.read_text(encoding="utf-8").strip())
    except (ValueError, KeyError, TypeError):
        return None
    return None


def verify(docker: Callable[..., subprocess.CompletedProcess], harness: Path, instance_id: str, patch: Path | None,
           out_dir: Path) -> dict[str, Any]:
    """The verifier's verdict on ``patch`` (None or an empty file: nothing applied)."""
    t = task_settings(harness, instance_id)
    name = f"v7ev-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    record: dict[str, Any] = {"instance_id": instance_id, "harness_commit": c.HARNESS["commit"], "image": t["image"]}
    patch_bytes = patch.stat().st_size if patch is not None and patch.is_file() else 0
    record["patch_sha256"] = c.sha256_file(patch) if patch_bytes else ""
    record["patch_bytes"] = patch_bytes
    try:
        made = docker("run", "-d", "--name", name, "--label", "v7=1", "--cpus", f"{t['cpus']:g}", "--memory",
                      f"{t['memory_mb']}m", "--entrypoint", "tail", t["image"], "-f", "/dev/null", timeout=1800)
        if made.returncode != 0:
            raise RuntimeError(f"verifier container: {_out(made)[-300:]}")
        if patch_bytes:
            docker("cp", str(patch), f"{name}:/tmp/replay.patch", timeout=600)
            applied = docker("exec", name, "bash", "-c", APPLY, timeout=300)
            record["apply_rc"] = applied.returncode
        else:
            record["apply_rc"] = None          # nothing applied, as the re-grader does for a missing or empty patch
        docker("exec", name, "bash", "-c", "mkdir -p /tests /logs/verifier /logs/agent", timeout=60)
        copied = docker("cp", f"{t['tests']}{os_sep()}.", f"{name}:/tests", timeout=600)
        if copied.returncode != 0:
            raise RuntimeError("could not copy the task's tests into the verifier container")
        docker("exec", "-u", "root", name, "bash", "-c", "chmod +x /tests/test.sh", timeout=60)
        ran = docker("exec", name, "bash", "-c", VERIFY, timeout=t["timeout_s"] + 120)
        record["verifier_rc"] = ran.returncode
        logs = out_dir / "verifier"
        logs.mkdir(parents=True, exist_ok=True)
        docker("cp", f"{name}:/logs/verifier/.", str(logs), timeout=600)
        record["reward"] = parse_reward(logs)
    except subprocess.TimeoutExpired:
        record["reward"], record["error"] = None, "the verifier timed out"
    except Exception as exc:  # noqa: BLE001 - an infrastructure failure is recorded, never read as a verdict
        record["reward"], record["error"] = None, f"{type(exc).__name__}: {exc}"[:600]
    finally:
        docker("rm", "-f", name, timeout=300)
    record["resolved"] = None if record.get("reward") is None else record["reward"] >= 1.0
    record["seconds"] = round(time.monotonic() - started, 3)
    return record


def os_sep() -> str:
    import os

    return os.sep


def selftest(docker: Callable[..., subprocess.CompletedProcess], harness: Path, archive: c.Archive,
             ids: list[str]) -> dict[str, Any]:
    """The oracle self-test (PROTOCOL.md §8), run after every arm: each task's reference patch must resolve and an
    empty patch must not. Each check is an archived run (``selftest/<task>/gold|empty``); the reference patch is copied
    by path into the verifier and never opened here."""
    rows = []
    for task in ids:
        out = {}
        for kind, patch in (("gold", harness / "tasks" / task / "solution" / "gold_patch.diff"), ("empty", None)):
            run = archive.start("selftest", task, kind)
            found = verify(docker, harness, task, patch, run.path)
            out[kind] = run.finish(found, "finished" if found.get("resolved") is not None else "error")
        rows.append({"instance_id": task, "gold_resolved": out["gold"].get("resolved"),
                     "empty_resolved": out["empty"].get("resolved"),
                     "errors": [e for e in (out["gold"].get("error"), out["empty"].get("error")) if e]})
    return {"tasks": len(rows), "gold_unresolved": sum(r["gold_resolved"] is not True for r in rows),
            "empty_resolved": sum(r["empty_resolved"] is True for r in rows), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("selftest",))
    ap.add_argument("--archive", type=Path, default=None, help="default: V7_ARCHIVE, else " + str(c.DEFAULT_ARCHIVE))
    ap.add_argument("--harness", required=True, type=Path, help="the harness tree's v2 folder")
    ap.add_argument("--task", default="")
    ap.add_argument("--splits", type=Path, default=HERE.parents[1] / "docs/validation/results/V7/splits.json")
    args = ap.parse_args(argv)
    archive = c.Archive(args.archive or c.archive_root())
    done = [t for t in c.load_splits(args.splits)["scored"]
            if (archive.latest("eval", t, "task") or {}).get("record", {}).get("stage2_done")]
    result = selftest(_docker(), args.harness, archive, [t for t in done if not args.task or t == args.task])
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
