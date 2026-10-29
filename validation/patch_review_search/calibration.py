# SPDX-License-Identifier: Apache-2.0
"""V10's cost calibration: development calibration, never study data. It measures what arms C, T, E and R cost per
patch, to inform the owner's spending cap and nothing else.

    python validation/patch_review_search/calibration.py prepare --freeze freeze.json --tasks tasks.json \\
        --archive <V7's archive> --output-dir prepared/
    python validation/patch_review_search/calibration.py run --freeze freeze.json --tasks tasks.json \\
        --archive <V7's archive> --cap-dollars 10 --prepared prepared/ --work work/ --output cost.json

- **V7's archived pilot patches only.** Each task's patch is a file V7's archive manifest lists
  (``docs/validation/results/V7/archive_manifest.json``), named ``model.patch`` in the task's own folder, with the
  SHA-256 the manifest records, read from the archive's folder (``--archive``). The tasks file gives each task's
  repository, its source (a git repository holding the base commit), its base commit and its statement: this script
  reads no dataset.
- **Refused before anything runs:** a task in the study's pool (the freeze file's, its hash checked), a task V8's draft
  split scores, a task whose ID holds ``qutebrowser-96b997802e94`` or ``ec2dcfce9eee``, a patch not in V7's archive, a
  dollar cap above zero and at most ``MAX_DOLLARS``, and a checkout that is not G6's frozen commit (the freeze file's,
  as this checkout's clean ``HEAD``).
- **A hard dollar stop** on the provider's usage, as the meters count it: patch by patch, C, T, E and R on each, no
  model call begins once what has been spent reaches the cap (each arm's ``share_left`` says so before every call),
  and once it is reached no further arm run starts. A call that begins below the cap may end above it. Arm R's
  sessions are launched outside this script; their cost, priced from their records, counts when they are judged.
- **The cost record** gives, per patch and arm, the model calls, the tokens by kind where the provider reports them,
  the cents and dollars, and the wall-clock and processor seconds; per arm, the totals and means. It is labelled
  development calibration (``KIND``, ``LABEL``, ``study_data: false``) and says it informs the owner's spending cap
  only (``INFORMS``). Nothing here writes G6's configuration, prompts or limits, or the freeze file.

The arms are bound as the study binds them (``bindings.py``); arm R is judged from the session records an approved
launcher leaves (``bindings.Sessions``), and this script never starts a model call of its own or a session.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATION = HERE.parent
REPOSITORY = VALIDATION.parent
if sys.path and Path(sys.path[0] or ".").resolve() == HERE:
    sys.path.pop(0)   # run as a script: this folder's module names must not shadow anything
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from patch_review_search import arm_r, bindings, driver, study  # noqa: E402
from patch_review_search.metering import Meter  # noqa: E402
from patch_review_utility import contract, trees  # noqa: E402
from patch_review_utility import run as v9run  # noqa: E402

KIND = "development calibration"
LABEL = "development calibration, never study data: it measures cost to inform the owner's spending cap"
INFORMS = "the owner's spending cap only; nothing here may change G6's configuration, prompts or limits"
MAX_DOLLARS = 50
NEVER = ("qutebrowser-96b997802e94", "ec2dcfce9eee")
ARCHIVE_MANIFEST = REPOSITORY / "docs" / "validation" / "results" / "V7" / "archive_manifest.json"
V8_DRAFT = REPOSITORY / "docs" / "validation" / "results" / "V8" / "splits.draft.json"


class Refused(ValueError):
    """The calibration cannot run as asked; nothing was run."""


def this_checkout() -> dict:
    """This checkout's commit and whether its tracked files are unchanged."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPOSITORY), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    try:
        return {"commit": git("rev-parse", "HEAD"), "clean": git("status", "--porcelain", "--untracked-files=no") == ""}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "clean": False}


def _tasks(freeze: dict, tasks_path: Path, archive: Path, archive_manifest: Path, v8_draft: Path) -> list[dict]:
    """The calibration's tasks, each with its archived patch's text; refused as the module docstring says."""
    tasks = json.loads(Path(tasks_path).read_text(encoding="utf-8"))["tasks"]
    pool_bytes = study.at(freeze, freeze["pool"]["file"]).read_bytes()
    if hashlib.sha256(pool_bytes).hexdigest() != freeze["pool"]["sha256"]:
        raise Refused("the pool's file does not have the SHA-256 the freeze file records")
    pool = {row["instance_id"] for row in json.loads(pool_bytes)}
    scored = set(json.loads(Path(v8_draft).read_text(encoding="utf-8")).get("scored") or [])
    listed = json.loads(Path(archive_manifest).read_text(encoding="utf-8"))["files"]
    for task in tasks:
        task_id = task["task_id"]
        if task_id in pool:
            raise Refused(f"{task_id} is in the study's pool: calibration never runs on a pool task")
        if task_id in scored:
            raise Refused(f"{task_id} is one of V8's scored tasks")
        if any(text in task_id for text in NEVER):
            raise Refused(f"{task_id} is never run")
        rel = str(task.get("patch") or "")
        parts = rel.split("/")
        if rel not in listed or parts[-1] != "model.patch" or len(parts) < 2 or parts[-2] != task_id:
            raise Refused(f"{task_id}'s patch is not its model.patch in V7's archive manifest")
        data = (Path(archive) / rel).read_bytes()
        if hashlib.sha256(data).hexdigest() != listed[rel]["sha256"]:
            raise Refused(f"{task_id}'s archived patch does not have the SHA-256 V7's archive manifest records")
        task["patch_text"] = data.decode("utf-8")
    return tasks


def _source(tasks_path: Path, task: dict) -> Path:
    return (Path(tasks_path).resolve().parent / task["source"]).resolve()


def _requirements(task: dict) -> list[dict]:
    said = task["statement"]
    return contract.split(said.get("problem_statement", ""), said.get("requirements", ""), said.get("interface", ""))


def prepare(freeze: dict, *, tasks_path, archive, output_dir, archive_manifest=ARCHIVE_MANIFEST,
            v8_draft=V8_DRAFT) -> list[Path]:
    """Arm R's session inputs and copies for the calibration's patches; nothing is launched."""
    out, written = Path(output_dir).resolve(), []
    limits = freeze["v9"]["limits"]
    for index, task in enumerate(_tasks(freeze, Path(tasks_path), Path(archive), Path(archive_manifest),
                                        Path(v8_draft))):
        try:
            copy = arm_r.make_copy(_source(tasks_path, task), task["base_commit"], task["patch_text"],
                                   out / "copies" / study.folder_name(index, task["task_id"]))
        except ValueError:
            continue     # an empty patch: no review to prepare
        prepared = arm_r.prepare(task["task_id"], _requirements(task), patch=task["patch_text"],
                                 time_limit_s=limits["arm_seconds"], token_cap=limits["token_cap"])
        path = out / "arm_r" / bindings.session_file(task["task_id"])
        study.write_json(path, {**prepared, "copy": str(copy)})
        written.append(path)
    return written


def _tokens(arm: str, reply: dict) -> dict | None:
    """The tokens by kind the provider reported for the run, where it reported them."""
    found: dict[str, int] = {}
    usages = ([exchange.get("usage") for exchange in reply.get("exchanges") or []] if arm == "E" else
              [(reply.get("session") or {}).get("usage")] if arm == "R" else [reply.get("usage")])
    for usage in usages:
        for kind, count in (usage or {}).items():
            if isinstance(count, int):
                found[kind] = found.get(kind, 0) + count
    return found or None


class DollarStop:
    """The hard dollar stop: what has been spent, against the cap."""

    def __init__(self, cap_cents: float):
        self.cap, self.spent = float(cap_cents), 0.0

    def reached(self) -> bool:
        return self.spent >= self.cap

    def allows(self, meter) -> bool:
        return self.spent + meter.reading()["cents"] < self.cap


def calibrate(freeze: dict, *, tasks_path, archive, cap_dollars: float, prepared, work, output,
              archive_manifest=ARCHIVE_MANIFEST, v8_draft=V8_DRAFT, checkout: dict | None = None) -> dict:
    """Arms C, T, E and R on each calibration patch under the dollar stop, into the cost record (the module
    docstring)."""
    found_checkout = checkout if checkout is not None else this_checkout()
    if found_checkout.get("commit") != freeze["g6_commit"] or not found_checkout.get("clean"):
        raise Refused("calibration runs only from the frozen G6 commit the freeze file names, with no tracked file "
                      "changed")
    if isinstance(cap_dollars, bool) or not 0 < float(cap_dollars) <= MAX_DOLLARS:
        raise Refused(f"the dollar cap is above zero and at most {MAX_DOLLARS} dollars, the development spend the "
                      "owner approved")
    tasks = _tasks(freeze, Path(tasks_path), Path(archive), Path(archive_manifest), Path(v8_draft))
    v10, work, prepared = freeze["v10"], Path(work), Path(prepared)
    built, ready = {}, {}
    for index, task in enumerate(tasks):
        folder = work / study.folder_name(index, task["task_id"])
        base = trees.checkout(_source(tasks_path, task), task["base_commit"], folder / "base")
        patched = trees.checkout(_source(tasks_path, task), task["base_commit"], folder / "patched")
        built[task["task_id"]] = (trees.apply(patched, task["patch_text"]), patched, base)
        path = prepared / "arm_r" / bindings.session_file(task["task_id"])
        if path.is_file():
            ready[task["task_id"]] = json.loads(path.read_text(encoding="utf-8"))
    by_id = {task["task_id"]: task for task in tasks}
    sessions = bindings.Sessions(
        folder=study.at(freeze, v10["arm_r"]["sessions"]),
        prepared={task_id: {k: v for k, v in found.items() if k != "copy"} for task_id, found in ready.items()},
        copies={task_id: Path(found["copy"]) for task_id, found in ready.items()},
        trees={task_id: (built[task_id][1], built[task_id][2]) for task_id in ready},
        executor_for=lambda task_id: v9run.task_executor(freeze["v9"], by_id[task_id]),
        runs=int(v10["arm_e"]["runs"]), run_s=float(v10["arm_e"]["run_s"]), prices=v10["arm_r"]["prices"])
    search = {arm: bindings.load(v10["bindings"][arm]) for arm in "CT"}
    arm_e = bindings.load(v10["bindings"]["E"])
    stop = DollarStop(float(cap_dollars) * 100)
    rows = []
    for task in tasks:
        state, patched, base = built[task["task_id"]]
        if state != "non_empty":
            rows += [{"task_id": task["task_id"], "arm": arm, "not_run": f"the patch is {state}"} for arm in "CTER"]
            continue
        patch = {"id": task["task_id"], "repository": task.get("repository"), "patch_text": task["patch_text"],
                 "requirements": _requirements(task), "arm_1": None, "arm_a": None,
                 "trees": {"patched": str(patched), "base": str(base)}}
        calls = {}
        for arm in "CTER":
            if arm != "R" and stop.reached():
                rows.append({"task_id": task["task_id"], "arm": arm, "not_run": "the dollar stop was reached"})
                continue
            meter = Meter()

            def share_left(meter=meter) -> bool:
                return stop.allows(meter)

            def call(arm=arm, meter=meter, share_left=share_left):
                if arm in "CT":
                    return search[arm](patch, search=dict(driver.SEARCH[arm]), share_left=share_left, meter=meter)
                if arm == "E":
                    return arm_e(patch, c_calls=calls.get("C", 0), t_calls=calls.get("T", 0),
                                 share_left=share_left, meter=meter)
                if task["task_id"] not in ready:
                    raise RuntimeError("arm R's session was not prepared for this patch")
                return sessions(patch, share_left=share_left, meter=meter)

            started, cpu = time.monotonic(), os.times()
            reply, error = {}, ""
            try:
                reply = call() or {}
            except Exception as exc:  # a failed run is counted with what it spent, never dropped
                error = f"{type(exc).__name__}: {exc}"
            finally:
                reading = meter.reading()
                stop.spent += reading["cents"]
            now = os.times()
            if not error and arm == "R" and reply.get("session") is None:
                error = str((reply.get("disposition") or {}).get("detail") or "no session record")
            calls[arm] = reading["calls"]
            rows.append({"task_id": task["task_id"], "arm": arm, "calls": reading["calls"],
                         "tokens": _tokens(arm, reply), "cents": round(reading["cents"], 6),
                         "dollars": round(reading["cents"] / 100, 6), "seconds": round(time.monotonic() - started, 3),
                         "processor_seconds": round(sum(now[:4]) - sum(cpu[:4]), 3), "error": error,
                         "not_run": ""})
    per_arm = {}
    for arm in "CTER":
        ran = [row for row in rows if row["arm"] == arm and not row.get("not_run")]
        cents = sum(row["cents"] for row in ran)
        per_arm[arm] = {"patches": len(ran), "calls": sum(row["calls"] for row in ran), "cents": round(cents, 6),
                        "dollars": round(cents / 100, 6),
                        "mean_dollars_per_patch": round(cents / 100 / len(ran), 6) if ran else None,
                        "mean_seconds_per_patch": round(sum(row["seconds"] for row in ran) / len(ran), 3)
                        if ran else None}
    result = {"kind": KIND, "label": LABEL, "study_data": False, "informs": INFORMS,
              "g6_commit": freeze["g6_commit"], "freeze": freeze["sha256"], "cap_cents": stop.cap,
              "spent_cents": round(stop.spent, 6), "stopped": stop.reached(), "rows": rows, "per_arm": per_arm}
    study.write_json(Path(output), result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run"):
        command = commands.add_parser(name)
        command.add_argument("--freeze", required=True)
        command.add_argument("--tasks", required=True)
        command.add_argument("--archive", required=True)
        if name == "prepare":
            command.add_argument("--output-dir", required=True)
        else:
            command.add_argument("--cap-dollars", required=True, type=float)
            command.add_argument("--prepared", required=True)
            command.add_argument("--work", required=True)
            command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    freeze = study.load_freeze(args.freeze)
    if args.command == "prepare":
        prepare(freeze, tasks_path=args.tasks, archive=args.archive, output_dir=args.output_dir)
    else:
        calibrate(freeze, tasks_path=args.tasks, archive=args.archive, cap_dollars=args.cap_dollars,
                  prepared=args.prepared, work=args.work, output=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
