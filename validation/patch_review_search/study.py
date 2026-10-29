# SPDX-License-Identifier: Apache-2.0
"""The entry point for V9's feasibility level and V10 together (V10's protocol, version 2: "Data", "Arms",
"Safeguards"; V9's protocol, version 2). It never starts a model or an agent session: V9's patches, arm 2's sessions
and arm R's sessions are run by the owner or an approved launcher, from inputs this script prepares, and judged here
from the records they leave.

    python validation/patch_review_search/study.py draw --freeze freeze.json --output order.json
    python validation/patch_review_search/study.py calibrate --freeze freeze.json --output <vault>/calibration.json
    python validation/patch_review_search/study.py prepare --freeze freeze.json --order order.json --output-dir in/
    python validation/patch_review_search/study.py arms --freeze freeze.json --order order.json \\
        --calibration <vault>/calibration.json --prepared in/ --work work/ --record record.jsonl --output arms.json
    python validation/patch_review_search/study.py label --freeze freeze.json --arms arms.json --record record.jsonl \\
        --committed <the record's final hash> --calibration <vault>/calibration.json --work work/ --output-dir out/

**The freeze file** (``load_freeze``) holds every frozen parameter, and the entry point refuses to start without one
naming G6's commit (40 lower-case hexadecimal digits): ``study`` (``STUDY``); ``level`` ("development" or
"feasibility"); ``g6_commit``; ``pool`` (``file`` and its ``sha256``: rows ``{instance_id, repo}``); ``seen`` (every
pool task opened while building or tuning verification search); ``replaced`` (each task replaced before its patch was
generated, with why: oracle controls, the task screen); ``v9``, V9's manifest without its ``study``, ``level`` and
``g6_commit``, which the file gives once (``run.py``: limits, executor or environment, suites, vault, audit,
broken_tasks, and the tasks in draw order); and ``v10``: ``cap_cents`` and ``shares`` (the spending cap and each arm's
share), ``limit_s``, ``grace_s`` and ``call_cap`` (arms C, T and E), ``rerun_timeout_s``, ``arm_e`` (``runs``, n, and
``run_s``, from G6's frozen commit), ``arm_r`` (``sessions``, the folder the launcher writes R's session records to,
and ``prices``, the frozen price list) and ``bindings`` (arms C, T and E as ``module:function``, ``bindings.py``; a
study level refuses a stand-in). At the feasibility level, V9's own checks apply (``run.load_manifest``), and the arms
run only from G6's frozen commit, as this checkout's clean ``HEAD``.

**The order** is the protocol's. ``draw``: the pool, less every ID V7's and V8's splits list, the tasks seen while
building, and a published list's IDs, in V9's draw order (``draw.order``, seeded from G6's commit at the feasibility
level). ``calibrate``: V9's calibration, inside the vault. ``prepare``: once the agent's patches exist, arm 2's
session inputs (V9's) and arm R's (``arm_r.prepare``), with R's copies (``arm_r.make_copy``: the base commit only,
the patch applied). ``arms``: V9's trees built from the draw (each task's base and patched trees), V9's arms on every
task with V9's gaming scan, then arms C, T, E and R on every non-empty patch with the reruns before the labels
(``driver.study``), every output hashed into the append-only record (V9's rows first), whose final hash is written
beside it (``<record>.final``). No label is read. ``label``: only for a record that verifies against the hash the
owner committed to G6's public repository: V9's labels, its audit and its results, then the reruns on the reference
fix and V10's analysis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATION = HERE.parent
REPOSITORY = VALIDATION.parent
if sys.path and Path(sys.path[0] or ".").resolve() == HERE:
    sys.path.pop(0)   # run as a script: this folder's module names must not shadow anything
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from patch_review_search import analysis, arm_r, bindings, driver, record, reruns, runner  # noqa: E402
from patch_review_utility import broken, labels, trees  # noqa: E402
from patch_review_utility import draw as v9draw  # noqa: E402
from patch_review_utility import run as v9run  # noqa: E402

STUDY = "V9 feasibility and V10"
LEVELS = ("development", "feasibility")
#: Every ID these list anywhere is excluded before any draw (V9's protocol, "Data").
EXCLUSION_FILES = (REPOSITORY / "docs" / "validation" / "results" / "V7" / "splits.json",
                   REPOSITORY / "docs" / "validation" / "results" / "V8" / "splits.draft.json")
#: V9's feasibility level: 30 non-empty patches, or 45 tasks attempted, whichever comes first.
FEASIBILITY_PATCHES, FEASIBILITY_TASKS = 30, 45


class Refused(ValueError):
    """The freeze file or the request cannot be run as the protocols require; nothing was run."""


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def folder_name(index: int, task_id: str) -> str:
    return f"{index:03d}-" + re.sub(r"[^A-Za-z0-9._-]", "_", task_id)[:60]


# --- the freeze file ------------------------------------------------------------------------------------------------------

def _whole(value, name: str, *, at_least: float = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < at_least:
        raise Refused(f"the freeze file's {name} is a number of at least {at_least}")


def load_freeze(path: str | Path) -> dict:
    """The freeze file, checked (the module docstring)."""
    path = Path(path).resolve()
    if not path.is_file():
        raise Refused(f"there is no freeze file at {path.name}: the study does not start without one")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("study") != STUDY:
        raise Refused(f"this is not the freeze file of {STUDY}")
    commit = data.get("g6_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Refused("the freeze file names no G6 commit (its full 40-character hash, in lower case)")
    level = data.get("level")
    if level not in LEVELS:
        raise Refused(f"the freeze file's level is one of {LEVELS}")
    section = {**(data.get("v9") or {}), "study": "V9", "level": level, "g6_commit": commit}
    try:
        manifest = v9run.load_manifest(path, data=section)
    except v9run.Refused as exc:
        raise Refused(f"V9's manifest: {exc}") from exc
    v10 = data.get("v10") or {}
    for name in ("cap_cents", "limit_s", "grace_s", "call_cap", "rerun_timeout_s"):
        _whole(v10.get(name), name, at_least=0 if name == "grace_s" else 1)
    for name in ("runs", "run_s"):
        _whole((v10.get("arm_e") or {}).get(name), f"arm_e.{name}")
    try:
        driver.Spending(v10["cap_cents"], v10.get("shares"))
    except ValueError as exc:
        raise Refused(f"the spending cap and its shares: {exc}") from exc
    if not (v10.get("arm_r") or {}).get("sessions") or not isinstance(v10["arm_r"].get("prices"), dict):
        raise Refused("the freeze file names arm R's sessions folder and the frozen price list")
    bound = v10.get("bindings") or {}
    if set(bound) != {"C", "T", "E"}:
        raise Refused("the freeze file binds arms C, T and E, each as module:function")
    if level == "feasibility" and any(bound[arm] == bindings.STAND_INS[arm] for arm in bound):
        raise Refused("a study level binds arms C, T and E to G6's verification search, never to a stand-in")
    pool = data.get("pool") or {}
    if not pool.get("file") or not re.fullmatch(r"[0-9a-f]{64}", str(pool.get("sha256", ""))):
        raise Refused("the freeze file names the pool's file and its SHA-256")
    return {"path": path, "folder": path.parent, "level": level, "g6_commit": commit, "v9": manifest, "v10": v10,
            "pool": pool, "seen": list(data.get("seen") or []), "replaced": dict(data.get("replaced") or {}),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def at(freeze: dict, rel: str) -> Path:
    return (freeze["folder"] / rel).resolve()


# --- the draw ---------------------------------------------------------------------------------------------------------------

def draw(freeze: dict, *, exclusion_files=EXCLUSION_FILES) -> dict:
    """The draw order after the exclusions (the module docstring), with each source's excluded IDs."""
    data = at(freeze, freeze["pool"]["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != freeze["pool"]["sha256"]:
        raise Refused("the pool's file does not have the SHA-256 the freeze file records")
    rows = json.loads(data)
    ids = [row["instance_id"] for row in rows]
    excluded = {str(path): sorted(broken.whole_ids(Path(path).read_text(encoding="utf-8"), ids))
                for path in exclusion_files}
    excluded["seen while building verification search"] = sorted(set(freeze["seen"]) & set(ids))
    listed = ((freeze["v9"].get("broken_tasks") or {}).get("published_list") or {})
    if listed.get("found"):
        excluded["the published list of broken tasks"] = sorted(broken.published_ids(
            at(freeze, listed["file"]), listed["sha256"], ids))
    leave_out = set().union(*map(set, excluded.values()))
    seed = freeze["v9"]["seed"]
    return {"study": STUDY, "seed": seed, "pool_sha256": freeze["pool"]["sha256"], "excluded": excluded,
            "order": v9draw.order(rows, seed=seed, excluded=leave_out)}


def follows_draw(freeze: dict, order: dict) -> None:
    """Refused unless V9's tasks are the draw's first tasks, in its order, less the tasks replaced before their
    patches were generated."""
    if order.get("seed") != freeze["v9"]["seed"]:
        raise Refused("the order was drawn with another seed than the freeze file's")
    expected = v9draw.replacements(list(order["order"]), freeze["replaced"])["tasks"]
    tasks = [task["task_id"] for task in freeze["v9"]["tasks"]]
    if tasks != expected[:len(tasks)]:
        raise Refused("V9's tasks are not the first tasks of the draw order, less the replaced ones")


def _stopping(freeze: dict, order: dict, states: list[str]) -> None:
    """At the feasibility level, the tasks end where V9's stopping rule ends them: at the 30th non-empty patch, at the
    45th task, or where the draw order runs out, whichever comes first."""
    if freeze["level"] != "feasibility":
        return
    available = len(v9draw.replacements(list(order["order"]), freeze["replaced"])["tasks"])
    non_empty = [index for index, state in enumerate(states) if state == "non_empty"]
    end = min(non_empty[FEASIBILITY_PATCHES - 1] + 1 if len(non_empty) >= FEASIBILITY_PATCHES else available,
              FEASIBILITY_TASKS, available)
    if len(states) != end:
        raise Refused(f"feasibility takes tasks until {FEASIBILITY_PATCHES} non-empty patches or "
                      f"{FEASIBILITY_TASKS} tasks attempted, whichever comes first")


# --- V9's trees and the sessions ----------------------------------------------------------------------------------------

def _patch_text(manifest: dict, task: dict) -> str:
    return v9run._at(manifest, task["patch"]).read_text(encoding="utf-8")


def build_trees(freeze: dict, work: Path) -> dict:
    """Each task's base tree and patched tree, under ``work``, with the patch's state (``trees.apply``)."""
    manifest, found = freeze["v9"], {}
    for index, task in enumerate(manifest["tasks"]):
        source = v9run._at(manifest, task["source"])
        folder = Path(work) / folder_name(index, task["task_id"])
        base = trees.checkout(source, task["base_commit"], folder / "base")
        patched = trees.checkout(source, task["base_commit"], folder / "patched")
        found[task["task_id"]] = {"state": trees.apply(patched, _patch_text(manifest, task)), "base": base,
                                  "patched": patched}
    return found


def prepare(freeze: dict, order: dict, output_dir: str | Path) -> list[Path]:
    """Arm 2's session inputs (V9's) and arm R's, with R's copies, for every non-empty patch (arm R reviews only
    those); nothing is launched."""
    follows_draw(freeze, order)
    manifest, out = freeze["v9"], Path(output_dir).resolve()
    written = v9run.prepare_inputs(manifest, out / "arm_2")
    limits = manifest["limits"]
    for index, task in enumerate(manifest["tasks"]):
        patch = _patch_text(manifest, task)
        try:
            copy = arm_r.make_copy(v9run._at(manifest, task["source"]), task["base_commit"], patch,
                                   out / "copies" / folder_name(index, task["task_id"]))
        except ValueError:
            continue     # an empty or non-applying patch: no review to prepare
        prepared = arm_r.prepare(task["task_id"], v9run._requirements(task), patch=patch,
                                 time_limit_s=limits["arm_seconds"], token_cap=limits["token_cap"])
        path = out / "arm_r" / bindings.session_file(task["task_id"])
        write_json(path, {**prepared, "copy": str(copy)})
        written.append(path)
    return written


# --- the arms -------------------------------------------------------------------------------------------------------------

def _arm(freeze: dict, arm: str):
    spec = freeze["v10"]["bindings"][arm]
    if freeze["level"] == "feasibility" and arm in "CT":
        return driver.ChildArm(spec, limit_s=float(freeze["v10"]["limit_s"]), grace_s=float(freeze["v10"]["grace_s"]))
    return bindings.load(spec)


def arms(freeze: dict, order: dict, *, calibration, prepared, work, record_path, output) -> dict:
    """V9's arms, then arms C, T, E and R, then the reruns, into the record; its final hash beside it; no label read."""
    follows_draw(freeze, order)
    record_path, work, prepared = Path(record_path), Path(work), Path(prepared)
    if record_path.exists():
        raise Refused("the record exists already: each arm runs once per patch, and the study supports one run")
    manifest, v10 = freeze["v9"], freeze["v10"]
    built = build_trees(freeze, work / "v10")
    _stopping(freeze, order, [built[task["task_id"]]["state"] for task in manifest["tasks"]])
    reviewed = v9run.review(manifest, calibration, work / "v9")
    rows = reviewed["rows"]
    kept = record.Record(record_path)
    for row in rows:
        kept.add("v9_arms", row)
    kept.add("v9_gaming", reviewed["gaming"])
    patches, excluded = runner.driver_inputs({"seed": manifest["seed"], "tasks": rows},
                                             lambda task_id: built[task_id]["patched"])
    by_id = {task["task_id"]: task for task in manifest["tasks"]}
    rows_by_id = {row["task_id"]: row for row in rows}
    for patch in patches:
        task = by_id[patch["id"]]
        patch.update(patch_text=_patch_text(manifest, task), requirements=v9run._requirements(task),
                     arm_1=rows_by_id[patch["id"]]["arms"]["tests"],
                     checks_cited=list(rows_by_id[patch["id"]].get("checks_cited") or []),
                     trees={"patched": str(built[patch["id"]]["patched"]), "base": str(built[patch["id"]]["base"])})

    def executor_for(task_id: str):
        return v9run.task_executor(manifest, by_id[task_id])

    ready = {}
    for patch in patches:
        path = prepared / "arm_r" / bindings.session_file(patch["id"])
        if path.is_file():
            ready[patch["id"]] = json.loads(path.read_text(encoding="utf-8"))
    sessions = bindings.Sessions(
        folder=at(freeze, v10["arm_r"]["sessions"]),
        prepared={task_id: {key: value for key, value in found.items() if key != "copy"}
                  for task_id, found in ready.items()},
        copies={task_id: Path(found["copy"]) for task_id, found in ready.items()},
        trees={task_id: (built[task_id]["patched"], built[task_id]["base"]) for task_id in ready},
        executor_for=executor_for, runs=int(v10["arm_e"]["runs"]), run_s=float(v10["arm_e"]["run_s"]),
        prices=v10["arm_r"]["prices"])

    def arm_r_run(patch, *, share_left, meter):
        if patch["id"] not in ready:
            raise RuntimeError("arm R's session was not prepared for this patch")
        return sessions(patch, share_left=share_left, meter=meter)

    def rerun(patch, arm, reproduction):
        return reruns.before_labels(executor_for(patch["id"]), built[patch["id"]]["patched"],
                                    built[patch["id"]]["base"], reproduction, timeout_s=v10["rerun_timeout_s"])

    found = driver.study(patches, arm_c=_arm(freeze, "C"), arm_t=_arm(freeze, "T"), arm_e=_arm(freeze, "E"),
                         arm_r=arm_r_run, spending=driver.Spending(v10["cap_cents"], v10.get("shares")), record=kept,
                         rerun=rerun, excluded=excluded, children_only=freeze["level"] == "feasibility",
                         vault_markers=tuple(reviewed["markers"]))
    final = kept.final
    record_path.with_name(record_path.name + ".final").write_text(final + "\n", encoding="utf-8")
    vault = reviewed["vault"]
    result = {"study": STUDY, "level": freeze["level"], "g6_commit": freeze["g6_commit"], "freeze": freeze["sha256"],
              "record": {"file": record_path.name, "final": final, "entries": len(kept.entries)},
              "v9": {"checkout": reviewed["checkout"], "environment": reviewed["environment"], "rows": rows,
                     "a5": reviewed["a5"], "gaming": reviewed["gaming"],
                     "vault": {"id": vault.vault_id, "digest": vault.digest},
                     "calibration_sha256": hashlib.sha256(reviewed["calibration_bytes"]).hexdigest()},
              "v10": found, "labels": "not read"}
    write_json(Path(output), result)
    return result


# --- the labels -------------------------------------------------------------------------------------------------------

def label(freeze: dict, *, arms_path, record_path, committed: str, calibration, work, output_dir) -> dict:
    """V9's labels and results, then the reruns on the reference fix and V10's analysis, only for a record that
    verifies against ``committed``, the hash the owner committed to G6's public repository."""
    entries = record.verify(record_path, committed)
    found = json.loads(Path(arms_path).read_text(encoding="utf-8"))
    if found["record"]["final"] != committed:
        raise Refused("the arms' output is not the one whose record ends at the committed hash")
    manifest, v10, work = freeze["v9"], freeze["v10"], Path(work)
    vault, ledger, _ = v9run.vault_of(manifest)
    if vault.digest != found["v9"]["vault"]["digest"]:
        raise Refused("the vault changed after the arms ran")
    tasks_calibrated, calibration_bytes = v9run.calibration_of(manifest, calibration, vault, ledger)
    if hashlib.sha256(calibration_bytes).hexdigest() != found["v9"]["calibration_sha256"]:
        raise Refused("the calibration is not the one the arms ran with")
    rows = found["v9"]["rows"]
    v9run.label(manifest, rows, tasks_calibrated, vault, ledger, work / "v9")
    v9 = v9run.results(manifest, checkout=found["v9"]["checkout"], vault=vault,
                       environment=found["v9"]["environment"], calibration_bytes=calibration_bytes, rows=rows,
                       a5=found["v9"]["a5"], gaming_found=found["v9"]["gaming"])
    out = Path(output_dir)
    write_json(out / "v9-results.json", v9)
    reference = _on_reference_fix(freeze, entries, rows, vault, ledger, work / "reference")
    limits = {key: v10[key] for key in ("cap_cents", "call_cap", "limit_s", "grace_s")} | {"shares": v10.get("shares")}
    arm_a_gaming = [item for item in v9["gaming"]["findings"] if str(item.get("source", "")).startswith("g6:")]
    v10_found = analysis.analyse(record_path, committed, v9=v9, reference=reference, limits=limits,
                                 gaming=arm_a_gaming)
    write_json(out / "v10-results.json", v10_found)
    return {"v9": v9, "v10": v10_found}


def _on_reference_fix(freeze: dict, entries: list[dict], rows: list[dict], vault, ledger, work: Path) -> list[dict]:
    """Every reproducible finding rerun on its task's reference fix, after the labels."""
    manifest = freeze["v9"]
    by_id = {task["task_id"]: (index, task) for index, task in enumerate(manifest["tasks"])}
    reproductions = {}
    for entry in entries:
        if entry["kind"] == "arm":
            for item in entry["data"]["output"].get("findings") or []:
                reproductions[(entry["data"]["patch"], entry["data"]["arm"], item.get("node"))] = item
    found, built = [], {}
    for entry in entries:
        data = entry["data"]
        if entry["kind"] != "rerun" or (data.get("result") or {}).get("reproducible") is not True:
            continue
        index, task = by_id[data["patch"]]
        if data["patch"] not in built:
            contract = labels.load(vault, v9run._at(manifest, task["label_contract"]), ledger=ledger)
            tree = trees.checkout(v9run._at(manifest, task["source"]), task["base_commit"],
                                  work / folder_name(index, task["task_id"]))
            trees.apply(tree, contract["reference_patch"])
            built[data["patch"]] = tree
        reproduction = reproductions.get((data["patch"], data["arm"], data["node"]))
        if reproduction is None and data["arm"] == "A":
            row = next(r for r in rows if r["task_id"] == data["patch"])
            reproduction = next((item for item in runner.arm_a_findings(row, built[data["patch"]])
                                 if item["node"] == data["node"]), None)
        if reproduction is None:
            continue
        found.append({"patch": data["patch"], "arm": data["arm"], "node": data["node"],
                      "result": reruns.on_reference_fix(v9run.task_executor(manifest, task), built[data["patch"]],
                                                        reproduction, timeout_s=freeze["v10"]["rerun_timeout_s"])})
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("draw", "calibrate", "prepare", "arms", "label"):
        command = commands.add_parser(name)
        command.add_argument("--freeze", required=True)
        if name in ("prepare", "arms"):
            command.add_argument("--order", required=True)
        if name in ("arms", "label"):
            command.add_argument("--calibration", required=True)
            command.add_argument("--work", required=True)
            command.add_argument("--record", required=True)
        if name == "arms":
            command.add_argument("--prepared", required=True)
        if name == "label":
            command.add_argument("--arms", required=True)
            command.add_argument("--committed", required=True)
        if name in ("prepare", "label"):
            command.add_argument("--output-dir", required=True)
        else:
            command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    freeze = load_freeze(args.freeze)
    if args.command == "draw":
        write_json(Path(args.output), draw(freeze))
    elif args.command == "calibrate":
        v9run.calibrate(freeze["v9"], args.output)
    else:
        order = json.loads(Path(args.order).read_text(encoding="utf-8")) if args.command != "label" else None
        if args.command == "prepare":
            prepare(freeze, order, args.output_dir)
        elif args.command == "arms":
            arms(freeze, order, calibration=args.calibration, prepared=args.prepared, work=args.work,
                 record_path=args.record, output=args.output)
        else:
            label(freeze, arms_path=args.arms, record_path=args.record, committed=args.committed,
                  calibration=args.calibration, work=args.work, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
