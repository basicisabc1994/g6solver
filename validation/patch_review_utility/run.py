# SPDX-License-Identifier: Apache-2.0
"""V9's single entry point: given a frozen manifest, calibrate the label contracts, prepare arm 2's session inputs,
and run every arm, the A5 check, the gaming scan, the labels and the statistics into one results file with the digest
of every input. No model call, no agent session, no dataset download: patches and arm 2's session records are files
the owner's sessions wrote.

    python validation/patch_review_utility/run.py calibrate --manifest m.json --output <vault>/calibration.json
    python validation/patch_review_utility/run.py prepare-self-review --manifest m.json --output-dir inputs/
    python validation/patch_review_utility/run.py run --manifest m.json --calibration <vault>/calibration.json \\
        --output results.json

``calibrate`` runs before any patch is generated (the oracle controls; a task that fails one is listed under
``replace``). ``run`` takes the arms first, then the gaming scan and the vault's check that nothing changed, and only
then the labels, which no arm ever sees. The research flag (``G6_ECHA_RESEARCH=1``) is the person's to set.

**The manifest** (JSON; relative paths are read from its folder): ``study`` ("V9"); ``level`` ("development",
"feasibility" or "confirmatory"); ``g6_commit`` (a study level: the frozen commit, which must be this checkout's clean
``HEAD``, and gives the seed) or ``seed`` (development); ``limits`` (``arm_seconds`` for arms 2 to 4, at most G6's
1,800; ``suite_run_seconds`` per arm-1 run; ``calibration_seconds``; ``token_cap``); ``executor`` (``command``,
``confined``, ``network``: a study level needs a confined executor with the network closed), or instead
``environment`` (``kind`` "containers"; ``toolchain`` ``{volume, lock}``, G6's toolchain checked against its lock
before anything runs; ``python``, each repository's interpreter in its image; optionally ``env``, each repository's
test environment; and an ``image`` for every task: ``containers.py`` says how each arm runs there); optionally
``import_roots`` (per repository, the folders that make the patched code importable: ``PYTHONPATH`` for arm 1 and
the labels, ``import_roots`` for G6's arms); ``suites`` (test paths
per repository, for arm 1); ``vault`` (``roots``, ``id``, ``ledger``); ``audit`` (``auditor``, ``audited``,
``label_errors`` and ``errors``, each label error's ``task_id``, ``kind`` and ``test``; or null); ``broken_tasks``
(required at a study level: the published-list check and the task screen, ``broken.py``); ``registered_size`` (or
null); optional ``setup_effort`` and ``deviations``, copied into the results; and
``tasks``, each ``{task_id, repository, source (a git repository holding the base commit), base_commit, statement
{problem_statement, requirements, interface}, patch, label_contract (inside the vault), self_review (arm 2's
session record), transcripts}``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATION = HERE.parent
if sys.path and Path(sys.path[0] or ".").resolve() == HERE:
    sys.path.pop(0)   # run as a script: this folder's module names must not shadow anything
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from patch_review_utility import (  # noqa: E402
    arms, broken, containers, contract, executor, gaming, labels, self_review, stats, trees)
from patch_review_utility.environment import toolchain  # noqa: E402

LEVELS = ("development", "feasibility", "confirmatory")
STUDY_LEVELS = ("feasibility", "confirmatory")
MAX_ARM_SECONDS = 1800
CONTAINER_PROGRAMS = frozenset({"docker", "podman"})
PROTOCOL = VALIDATION.parent / "docs" / "validation" / "patch_review_utility" / "PROTOCOL.md"
TRIAL = VALIDATION / "patch_review" / "run.py"
INTERPRETATION = {
    "development": "Development fixtures only: no claim of any kind.",
    "feasibility": "Feasibility estimates only: no claim about superiority or safety (PROTOCOL.md, \"Two levels\").",
    "confirmatory": "Only the registered hypotheses; nothing is generalised beyond the repositories in the pool.",
}


class Refused(ValueError):
    """The manifest or the request cannot be run as the protocol requires; nothing was run."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checkout() -> dict:
    """This checkout's commit and whether its tracked files are unchanged."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(VALIDATION.parent), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    try:
        return {"commit": git("rev-parse", "HEAD"), "clean": git("status", "--porcelain", "--untracked-files=no") == ""}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "clean": False}


def load_manifest(path: str | Path, *, data: dict | None = None) -> dict:
    """The manifest at ``path``, checked; ``data``, when given, is the manifest itself, held in the file at ``path``
    (V10's freeze file holds V9's manifest as one of its sections), whose folder relative paths are read from."""
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8")) if data is None else dict(data)
    level = manifest.get("level")
    if manifest.get("study") != "V9" or level not in LEVELS:
        raise Refused(f"the manifest is V9's, at one of the levels {LEVELS}")
    if level in STUDY_LEVELS:
        try:
            manifest["seed"] = stats.seed(level, manifest.get("g6_commit", ""))
        except ValueError as exc:
            raise Refused(str(exc)) from exc
        if _containers(manifest) is None:
            described = manifest["executor"]
            if described.get("confined") is not True or described.get("network") is not False:
                raise Refused("a study level runs arm 1 and the labels only in a confined executor with the network "
                              "closed")
            command = [str(word) for word in described.get("command", [])]
            if command and Path(command[0]).stem.lower() in CONTAINER_PROGRAMS and "{container}" not in command:
                raise Refused("a container command names each run's container ({container}), so a run past its "
                              "timeout can be stopped by name")
    elif not isinstance(manifest.get("seed"), int):
        raise Refused("a development manifest gives its own integer seed")
    _broken_tasks(manifest, path.parent)
    seconds = manifest["limits"]["arm_seconds"]
    if not isinstance(seconds, int) or not 1 <= seconds <= MAX_ARM_SECONDS:
        raise Refused(f"arm_seconds is a whole number from 1 to {MAX_ARM_SECONDS}, G6's own limit")
    for repository, roots in (manifest.get("import_roots") or {}).items():
        problems = containers.root_problems(roots)
        if problems:
            raise Refused(f"{repository}'s import roots: " + "; ".join(problems))
    environment = _containers(manifest)
    if environment is not None:
        if not {"volume", "lock"} <= set(environment.get("toolchain") or {}):
            raise Refused("a container environment names G6's toolchain volume and its lock")
        for task in manifest["tasks"]:
            if not task.get("image"):
                raise Refused(f"task {task['task_id']} names no image: each task runs in its own benchmark image")
            if not containers.pinned(task["image"]):
                raise Refused(f"task {task['task_id']} names its image by a tag: an image is named by its digest "
                              "(name@sha256:...)")
            if task["repository"] not in (environment.get("python") or {}):
                raise Refused(f"the environment names no Python of the image for {task['repository']}")
        for repository, env in (environment.get("env") or {}).items():
            problems = containers.test_env_problems(env)
            if problems:
                raise Refused(f"{repository}'s test environment: " + "; ".join(problems))
    manifest["_path"], manifest["_folder"] = path, path.parent
    return manifest


def _broken_tasks(manifest: dict, folder: Path) -> None:
    """The label audit's record and, at a study level (or whenever given), the freeze entry's published-list check and
    task screen (PROTOCOL.md version 2, "Broken tasks"); no task the published list names is run."""
    problems = broken.audit_problems(manifest.get("audit"))
    if problems:
        raise Refused("the label audit: " + "; ".join(problems))
    entry = manifest.get("broken_tasks")
    if entry is None and manifest["level"] not in STUDY_LEVELS:
        return
    problems = broken.frozen_problems(entry, auditor=(manifest.get("audit") or {}).get("auditor"))
    if problems:
        raise Refused("broken tasks: " + "; ".join(problems))
    listed = entry["published_list"]
    if listed["found"]:
        try:
            on_list = broken.published_ids((folder / listed["file"]).resolve(), listed["sha256"],
                                           [task["task_id"] for task in manifest["tasks"]])
        except (OSError, ValueError) as exc:
            raise Refused(f"broken tasks: {exc}") from exc
        if on_list:
            raise Refused("tasks on the published list of broken tasks are excluded: " + ", ".join(sorted(on_list)))


def _containers(manifest: dict) -> dict | None:
    """The manifest's container environment, or None when the arms run on this computer (development)."""
    environment = manifest.get("environment") or {"kind": "local"}
    if environment.get("kind") not in ("local", "containers"):
        raise Refused("the environment's kind is local or containers")
    return environment if environment["kind"] == "containers" else None


def _at(manifest: dict, rel: str) -> Path:
    return (manifest["_folder"] / rel).resolve()


def _frozen(manifest: dict) -> dict:
    found = _checkout()
    if manifest["level"] in STUDY_LEVELS and (found["commit"] != manifest["g6_commit"] or not found["clean"]):
        raise Refused("a study level runs only from G6's frozen commit, with no tracked file changed")
    return found


def _vault(manifest: dict):
    ledger = labels.Ledger(_at(manifest, manifest["vault"]["ledger"]))
    roots = [_at(manifest, root) for root in manifest["vault"]["roots"]]
    return labels.seal(roots, ledger=ledger, vault_id=manifest["vault"]["id"]), ledger, roots


#: The vault, its ledger and its roots, for an entry point that runs V9's steps apart (V10's ``study.py``).
vault_of = _vault


def task_executor(manifest: dict, task: dict) -> executor.Executor:
    """Arm 1's and the labels' executor for ``task``: its own pristine image in a container environment, else the
    manifest's executor."""
    environment = _containers(manifest)
    if environment is not None:
        python, env = environment["python"][task["repository"]], (environment.get("env") or {}).get(task["repository"])
        command = containers.tests_command(task["image"], python=python, env=env,
                                           import_roots=import_roots(manifest, task))
        return executor.Executor(command, confined=True, network=False)
    described = manifest["executor"]
    return executor.Executor(described["command"], confined=bool(described.get("confined")),
                             network=described.get("network"))


def import_roots(manifest: dict, task: dict) -> list[str]:
    """The folders that make the repository's patched code importable (``lib`` for a ``lib/`` layout whose package
    the image also installs): ``PYTHONPATH`` in arm 1's and the labels' containers, G6's ``import_roots``."""
    return list((manifest.get("import_roots") or {}).get(task["repository"]) or [])


def g6_command(manifest: dict, task: dict) -> list[str] | None:
    """The command arms 3 and 4 run in for ``task``: its image with G6's toolchain; None runs them on this computer."""
    environment = _containers(manifest)
    if environment is None:
        return None
    return containers.g6_command(task["image"], toolchain=environment["toolchain"]["volume"], study=VALIDATION,
                                 repo_python=environment["python"][task["repository"]],
                                 env=(environment.get("env") or {}).get(task["repository"]))


def _toolchain_ready(manifest: dict) -> dict | None:
    """In a container environment, the toolchain volume checked against its lock (a refusal when they differ)."""
    environment = _containers(manifest)
    if environment is None:
        return None
    lock = _at(manifest, environment["toolchain"]["lock"])
    problems = toolchain.check(toolchain.load(), volume=environment["toolchain"]["volume"], lock=lock)
    if problems:
        raise Refused("G6's toolchain does not match its lock: " + "; ".join(problems))
    return json.loads(lock.read_text(encoding="utf-8"))


def _folder(index: int, task_id: str) -> str:
    return f"{index:03d}-" + re.sub(r"[^A-Za-z0-9._-]", "_", task_id)[:60]


def _digests(manifest: dict, extra: dict[str, bytes]) -> dict[str, str]:
    found = {f"scripts/{p.relative_to(HERE).as_posix()}": _sha256(p.read_bytes())
             for p in sorted([*HERE.glob("*.py"), *HERE.glob("*.md"), *(HERE / "environment").glob("*.*")])
             if p.suffix in (".py", ".md", ".json", ".sh")}
    for key, path in (("validation/patch_review/run.py", TRIAL),
                      ("docs/validation/patch_review_utility/PROTOCOL.md", PROTOCOL)):
        found[key] = _sha256(path.read_bytes()) if path.is_file() else "missing"
    found["manifest"] = _sha256(manifest["_path"].read_bytes())
    found.update({key: _sha256(data) for key, data in extra.items()})
    for task in manifest["tasks"]:
        for rel in [task["patch"], task.get("self_review", ""), *task.get("transcripts", [])]:
            if rel:
                path = _at(manifest, rel)
                found[Path(rel).as_posix()] = _sha256(path.read_bytes()) if path.is_file() else "missing"
    return found


def _finite(value):
    """``value`` with every infinite or undefined number spelled out, so the file is plain JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return "not a number" if math.isnan(value) else ("infinity" if value > 0 else "-infinity")
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def _write(path: str | Path, data: dict) -> None:
    Path(path).write_text(json.dumps(_finite(data), indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")


def _requirements(task: dict) -> list[dict]:
    said = task["statement"]
    return contract.split(said.get("problem_statement", ""), said.get("requirements", ""), said.get("interface", ""))


# --- calibrate ----------------------------------------------------------------------------------------------------------

def calibrate(manifest: dict, output: str | Path) -> dict:
    checkout = _frozen(manifest)
    vault, ledger, _ = _vault(manifest)
    if manifest["level"] in STUDY_LEVELS and not vault.covers(Path(output).resolve().parent):
        raise Refused("a study level keeps its calibration inside the vault: it names the hidden tests")
    environment = _toolchain_ready(manifest)
    found, executors = {}, {}
    with tempfile.TemporaryDirectory(prefix="v9-calibrate-") as work:
        for index, task in enumerate(manifest["tasks"]):
            label_contract = labels.load(vault, _at(manifest, task["label_contract"]), ledger=ledger)
            folder = Path(work) / _folder(index, task["task_id"])
            source = _at(manifest, task["source"])
            base = trees.checkout(source, task["base_commit"], folder / "base")
            reference = trees.checkout(source, task["base_commit"], folder / "reference")
            if trees.apply(reference, label_contract["reference_patch"]) != "non_empty":
                found[task["task_id"]] = {"task_id": task["task_id"], "passed": False,
                                          "why": "the reference fix does not apply to the base commit"}
                continue
            run_with = executors.setdefault(task["task_id"], task_executor(manifest, task))
            found[task["task_id"]] = labels.calibrate(label_contract, reference_tree=reference, base_tree=base,
                                                      executor=run_with,
                                                      timeout_s=manifest["limits"]["calibration_seconds"])
    screen, screen_bytes = _screen(manifest, vault, ledger)
    why: dict[str, list[str]] = {t: ["the oracle controls"] for t, c in found.items() if not c.get("passed")}
    for task in manifest["tasks"]:
        said = (screen or {}).get("label_invalid", {}).get(task["task_id"])
        if said:
            why.setdefault(task["task_id"], []).append(f"the task screen: {said['test']}: {said['sentence']}")
    result = {"study": "V9", "level": manifest["level"], "g6_checkout": checkout,
              "executors": {task: ran.describe() for task, ran in executors.items()}, "toolchain": environment,
              "vault": {"id": vault.vault_id, "digest": vault.digest},
              "input_sha256": _digests(manifest, {"screen": screen_bytes} if screen_bytes is not None else {}),
              "tasks": found, "replace": sorted(why), "replaced_why": {t: "; ".join(why[t]) for t in sorted(why)},
              "screen": screen}
    _write(output, result)
    return result


def _screen(manifest: dict, vault, ledger) -> tuple[dict | None, bytes | None]:
    """The task screen's findings, when the freeze entry switches it on (PROTOCOL.md version 2, "Broken tasks"), with
    the record's bytes; its record names hidden tests, so a study level reads it only from inside the vault."""
    screen = (manifest.get("broken_tasks") or {}).get("screen") or {}
    if not screen.get("on"):
        return None, None
    path = _at(manifest, screen["record"])
    if vault.covers(path):
        data = labels.guarded_read(vault, "evaluator", path, ledger=ledger)
    elif manifest["level"] in STUDY_LEVELS:
        raise Refused("a study level keeps the task screen's record inside the vault: it names the hidden tests")
    else:
        data = path.read_bytes()
    try:
        found = broken.screened(json.loads(data))
    except ValueError as exc:
        raise Refused(f"the task screen: {exc}") from exc
    if found["screener"] != screen["screener"]:
        raise Refused("the task screen's record is not by the screener its freeze entry names")
    return found, data


# --- arm 2's session inputs ---------------------------------------------------------------------------------------------

def prepare_inputs(manifest: dict, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for task in manifest["tasks"]:
        patch = _at(manifest, task["patch"]).read_text(encoding="utf-8")
        prepared = self_review.prepare(task["task_id"], _requirements(task), patch=patch,
                                       time_limit_s=manifest["limits"]["arm_seconds"],
                                       token_cap=manifest["limits"]["token_cap"])
        path = out / (re.sub(r"[^A-Za-z0-9._-]", "_", task["task_id"]) + ".json")
        _write(path, prepared)
        written.append(path)
    return written


# --- run ------------------------------------------------------------------------------------------------------------------

def _arm_inputs(manifest: dict) -> list[Path]:
    paths = []
    for task in manifest["tasks"]:
        paths += [_at(manifest, task["source"]), _at(manifest, task["patch"])]
        paths += [_at(manifest, rel) for rel in [task.get("self_review", ""), *task.get("transcripts", [])] if rel]
    return paths


def cited_checks(tree: Path) -> list[str]:
    """The checks every requirement cites: the test files the patch adds or changes that are on the patched tree (a
    test file the patch deletes is no check of it)."""
    return [rel for rel in contract.patch_tests(trees.changed_files(tree)) if (Path(tree) / rel).is_file()]


def _review_task(manifest, task, index, work, calibration, markers, findings, a5) -> dict:
    task_id, repository = task["task_id"], task["repository"]
    run_with, in_image = task_executor(manifest, task), g6_command(manifest, task)
    patch = _at(manifest, task["patch"]).read_text(encoding="utf-8")
    requirements = _requirements(task)
    folder = Path(work) / _folder(index, task_id)
    source, limits = _at(manifest, task["source"]), manifest["limits"]
    found = calibration.get(task_id) or {}
    row = {"task_id": task_id, "repository": repository, "patch": "", "label": None, "defect_categories": [],
           "oracle_controls_passed": found.get("passed") is True, "contract": requirements, "checks_cited": [],
           "arms": {}, "details": {}}
    findings += gaming.scan_patch(patch, source=f"patch:{task_id}")
    for rel in task.get("transcripts", []):
        path = _at(manifest, rel)
        if path.is_file():
            findings += gaming.scan_transcript(path.read_text(encoding="utf-8", errors="replace"),
                                               source=f"transcript:{task_id}:{Path(rel).name}",
                                               vault_markers=markers, patch_files=trees.patch_paths(patch))
    probe = trees.checkout(source, task["base_commit"], folder / "probe")
    row["patch"] = trees.apply(probe, patch)
    if row["patch"] != "non_empty":
        return row
    checks = cited_checks(probe)
    row["checks_cited"] = checks

    def fresh(name: str) -> Path:
        tree = trees.checkout(source, task["base_commit"], folder / name)
        trees.apply(tree, patch)
        return tree

    ci = arms.tests_and_ci(trees.checkout(source, task["base_commit"], folder / "base"), fresh("tests"),
                           suite=manifest["suites"][repository], patch_tests=checks, executor=run_with,
                           timeout_s=limits["suite_run_seconds"])
    session = _at(manifest, task["self_review"]) if task.get("self_review") else None
    record = json.loads(session.read_text(encoding="utf-8")) if session and session.is_file() else None
    prepared = self_review.prepare(task_id, requirements, patch=patch, time_limit_s=limits["arm_seconds"],
                                   token_cap=limits["token_cap"])
    own = self_review.disposition(prepared, record)
    roots = import_roots(manifest, task)
    reviewed = arms.in_child("g6", fresh("g6"), requirements, checks, timeout_s=limits["arm_seconds"],
                             command=in_image, import_roots=roots)
    direct = arms.in_child("direct_checks", fresh("direct"), requirements, checks, timeout_s=limits["arm_seconds"],
                           command=in_image, import_roots=roots)
    if reviewed.get("a5") is not None:
        a5["checked"] += 1
        if not reviewed["a5"]["complete"]:
            a5["incomplete"].append({"task_id": task_id, "missing": reviewed["a5"]["missing"]})
    for arm, traced in (("tests", ci), ("g6", reviewed), ("direct_checks", direct)):
        findings += gaming.scan_trace(traced.get("trace") or {}, source=f"{arm}:{task_id}", vault_markers=markers)
    row["arms"] = {"tests": ci["disposition"], "self_review": own, "g6": reviewed["disposition"],
                   "direct_checks": direct["disposition"]}
    row["details"] = {"tests": {key: ci.get(key) for key in ("base_passing", "base_flaky", "regressions",
                                                             "added_not_passing", "unrunnable", "killed")},
                      "g6": {"row": reviewed.get("row"), "a5": reviewed.get("a5"),
                             "model_calls": reviewed.get("model_calls"),
                             # for V10: arm 3's stated-check results and the defects it reproduced
                             "stated_checks": reviewed.get("stated_checks"),
                             "defects_reproduced": reviewed.get("defects_reproduced")},
                      "direct_checks": {"checks": direct.get("checks")}}
    return row


def calibration_of(manifest: dict, calibration_path: str | Path, vault, ledger) -> tuple[dict, bytes]:
    """The calibration's tasks and bytes, read through the vault (a study level reads it only from inside)."""
    calibration_path = Path(calibration_path).resolve()
    if vault.covers(calibration_path):
        calibration_bytes = labels.guarded_read(vault, "evaluator", calibration_path, ledger=ledger)
    elif manifest["level"] in STUDY_LEVELS:
        raise Refused("a study level reads its calibration only from inside the vault")
    else:
        calibration_bytes = calibration_path.read_bytes()
    return json.loads(calibration_bytes)["tasks"], calibration_bytes


def review(manifest: dict, calibration_path: str | Path, work: str | Path) -> dict:
    """Every arm on every task, then the gaming scan and the vault's check that nothing changed; no label is read.
    The trees are made under ``work``."""
    checkout = _frozen(manifest)
    vault, ledger, roots = _vault(manifest)
    leaks = labels.exposed(vault, _arm_inputs(manifest))
    if leaks:
        raise Refused("arm inputs lie inside the vault: " + ", ".join(leaks[:5]))
    calibration, calibration_bytes = calibration_of(manifest, calibration_path, vault, ledger)
    environment = _toolchain_ready(manifest)
    markers = [*vault.roots, *(str(root) for root in roots), manifest["vault"]["id"]]
    incidents_before = sum(e["event"] == "incident" for e in ledger.events())
    rows, findings, a5 = [], [], {"checked": 0, "incomplete": []}
    for index, task in enumerate(manifest["tasks"]):
        rows.append(_review_task(manifest, task, index, work, calibration, markers, findings, a5))
    vault_changes = list(vault.verify_unchanged(ledger=ledger))
    incidents = [e for e in ledger.events() if e["event"] == "incident"][incidents_before:]
    contaminated = gaming.contaminating(findings) or bool(vault_changes) or bool(incidents)
    return {"checkout": checkout, "vault": vault, "ledger": ledger, "markers": markers, "calibration": calibration,
            "calibration_bytes": calibration_bytes, "environment": environment, "rows": rows, "a5": a5,
            "gaming": {"findings": findings, "contaminated": contaminated, "vault_changes": vault_changes,
                       "vault_incidents": incidents}}


def label(manifest: dict, rows: list[dict], calibration: dict, vault, ledger, work: str | Path) -> None:
    """Each non-empty, calibrated patch's label, into its row, on a fresh tree under ``work``: only once every arm
    has run and the scan is done."""
    for index, (row, task) in enumerate(zip(rows, manifest["tasks"])):
        found = calibration.get(task["task_id"])
        if row["patch"] != "non_empty" or not found or "groups" not in found:
            continue
        label_contract = labels.load(vault, _at(manifest, task["label_contract"]), ledger=ledger)
        tree = trees.checkout(_at(manifest, task["source"]), task["base_commit"],
                              Path(work) / _folder(index, task["task_id"]) / "label")
        trees.apply(tree, _at(manifest, task["patch"]).read_text(encoding="utf-8"))
        labelled = labels.label(label_contract, found, tree, executor=task_executor(manifest, task))
        row["label"], row["defect_categories"] = labelled["label"], labelled["defect_categories"]
        row["label_detail"] = {key: labelled.get(key) for key in ("failed", "by_origin", "disagreement",
                                                                   "infrastructure_errors", "retried",
                                                                   "unlabelled_because")}


def results(manifest: dict, *, checkout: dict, vault, environment, calibration_bytes: bytes, rows: list[dict],
            a5: dict, gaming_found: dict) -> dict:
    """The results file's content from the labelled rows: the audit's exclusions, the statistics and the verdict."""
    # the label audit's kinds, recorded before this run: a label-invalid patch leaves the primary analysis
    audited = broken.audit(manifest.get("audit"), {row["task_id"]: row["label"] for row in rows})
    for row in rows:
        row["label_invalid"] = audited is not None and row["task_id"] in audited["label_invalid"]
    analysis = stats.analyse(rows, seed=manifest["seed"])
    counts = analysis["counts"]
    return {
        "study": "V9", "level": manifest["level"], "g6_commit": manifest.get("g6_commit"), "g6_checkout": checkout,
        "seed": manifest["seed"], "limits": manifest["limits"], "toolchain": environment,
        "executors": {task["task_id"]: task_executor(manifest, task).describe() for task in manifest["tasks"]},
        "model_calls_by_g6": sum(((r["arms"].get("g6") or {}).get("cost") or {}).get("model_calls", 0) for r in rows),
        "model_calls_by_g6_source": sorted({((r["details"].get("g6") or {}).get("model_calls") or {}).get("source", "")
                                            for r in rows if r["details"].get("g6")} - {""}),
        "vault": {"id": vault.vault_id, "digest": vault.digest, "blinding": labels.blinding_status(vault)},
        "input_sha256": _digests(manifest, {"calibration": calibration_bytes}),
        "tasks": rows, "a5": a5, "gaming": gaming_found,
        "broken_tasks": {**(manifest.get("broken_tasks") or {}), "audit": audited,
                         "note": "" if audited is not None else broken.NOT_CHECKED},
        "experiment_1": analysis, "feasibility": stats.feasibility(rows),
        "verdict": stats.verdict(level=manifest["level"], contaminated=gaming_found["contaminated"],
                                 audit=manifest.get("audit"), registered_size=manifest.get("registered_size"),
                                 defective=counts["defective"], correct=counts["correct"],
                                 hypotheses=analysis["hypotheses"]),
        "setup_effort": manifest.get("setup_effort", {}), "deviations": manifest.get("deviations", []),
        "interpretation": INTERPRETATION[manifest["level"]]}


def run(manifest: dict, calibration_path: str | Path, output: str | Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="v9-study-") as work:
        reviewed = review(manifest, calibration_path, work)
        # the labels, only now that every arm has run and the scan is done
        label(manifest, reviewed["rows"], reviewed["calibration"], reviewed["vault"], reviewed["ledger"], work)
    result = results(manifest, checkout=reviewed["checkout"], vault=reviewed["vault"],
                     environment=reviewed["environment"], calibration_bytes=reviewed["calibration_bytes"],
                     rows=reviewed["rows"], a5=reviewed["a5"], gaming_found=reviewed["gaming"])
    _write(output, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("calibrate")
    one.add_argument("--manifest", required=True)
    one.add_argument("--output", required=True)
    two = commands.add_parser("prepare-self-review")
    two.add_argument("--manifest", required=True)
    two.add_argument("--output-dir", required=True)
    three = commands.add_parser("run")
    three.add_argument("--manifest", required=True)
    three.add_argument("--calibration", required=True)
    three.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.command == "calibrate":
        calibrate(manifest, args.output)
    elif args.command == "prepare-self-review":
        prepare_inputs(manifest, args.output_dir)
    else:
        run(manifest, args.calibration, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
