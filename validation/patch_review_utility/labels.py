# SPDX-License-Identifier: Apache-2.0
"""The label contract (PROTOCOL.md, "Two contracts" and "Ground truth and its error"): correct or defective, decided
only by running checks no arm ever sees.

**Where it lives.** Each task's contract is a folder inside G6's evaluator-only vault (``g6.research.echa.Vault``),
read only with the ``evaluator`` role. ``contract.json`` names the reference fix (``reference_patch``, a diff file) and
the check groups, each run as one pytest execution:

- ``hidden_tests``: the benchmark's own hidden tests, as files (``files``: tree path to a file in the folder) or a test
  patch (``patch``), with ``fail_to_pass`` and ``pass_to_pass`` test ids;
- ``withheld_specification`` (only when the owner named its ``author``): behavioural checks, as files, with ``tests``.

A check is one test id. Arms never get a path inside the vault (``exposed`` names any that would).

**Calibration, before the task's patch is generated** (``calibrate``). Every group runs three times on the reference
fix. A check whose outcome differs across the three is dropped for the task, and counted. A group's timeout is ten
times its median time on the reference fix, and at least 60 seconds. The oracle controls: the reference fix passes
every kept check (in all three runs), and the base commit fails at least one of them, judged by the label rule below.
A task that fails either control is replaced.

**The label** (``label``). Every kept check runs three times; a run counts as a pass only when pytest reports the test
passed. A check fails when it fails in at least two of the three runs, or when its group's run went past its timeout.
The patch is correct when no kept check fails, and defective otherwise. When both groups exist and one passes while
the other fails, the disagreement is recorded. A failed check's defect category is ``fail_to_pass``,
``pass_to_pass`` or ``specification``.

**Infrastructure errors.** A run in which the executor itself failed (its ``error``: the overlay did not apply, the
command could not start, no outcome report) is not a test outcome. It is run once more; if the error persists, the
patch gets no label (``label`` is None, with the reason): it is left out of the primary analysis, counted and
reported, and the sensitivity analysis counts it as defective, as it does an empty patch. In calibration a persisting
error fails the task's controls, so the task is replaced. A timeout is not an infrastructure error: it is the label
rule above.

The research flag must be set (``G6_ECHA_RESEARCH=1``) to seal or read the vault; that is the person's decision, never
this script's. The vault is a software check in this process, not an operating-system boundary (its
``blinding_status``); the arms' confinement is what keeps them away from it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import median

from g6.research.echa.access import Vault, blinding_status, guarded_read

__all__ = ["Ledger", "blinding_status", "calibrate", "exposed", "guarded_read", "label", "load", "seal"]

RUNS = 3
MIN_TIMEOUT_S = 60
TIMEOUT_FACTOR = 10


class Ledger:
    """The vault's ledger: one JSON line per event (a sealing or an incident), appended, never rewritten."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": time.time(), **record}, sort_keys=True) + "\n")

    def add_vault(self, vault_id: str, digest: str, *, files: int, roots: int) -> None:
        self._write({"event": "vault_sealed", "vault_id": vault_id, "digest": digest, "files": files, "roots": roots})

    def add_incident(self, kind: str, **fields) -> None:
        self._write({"event": "incident", "kind": kind, **fields})

    def events(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def seal(roots: list[str | Path], *, ledger: Ledger, vault_id: str) -> Vault:
    return Vault.seal(roots, ledger=ledger, vault_id=vault_id)


def exposed(vault: Vault, paths: list[str | Path]) -> list[str]:
    """The paths among ``paths`` that lie in the vault: none may be given to an arm."""
    return [str(path) for path in paths if vault.covers(path)]


def _read(vault: Vault, path: Path, ledger: Ledger) -> str:
    return guarded_read(vault, "evaluator", path, ledger=ledger).decode("utf-8")


def load(vault: Vault, path: str | Path, *, ledger: Ledger) -> dict:
    """The contract at ``path`` (inside the vault), with every file it names read through the vault."""
    path = Path(path)
    if not vault.covers(path):
        raise ValueError("a label contract is read only from inside the vault")
    raw = json.loads(_read(vault, path, ledger))
    folder = path.parent

    def text(rel: str) -> str:
        return _read(vault, folder / rel, ledger) if rel else ""

    groups = []
    for group in raw["groups"]:
        tests = (list(group.get("fail_to_pass", [])) + list(group.get("pass_to_pass", []))
                 if group["origin"] == "hidden_tests" else list(group["tests"]))
        groups.append({"id": group["id"], "origin": group["origin"], "author": group.get("author", ""),
                       "tests": tests, "fail_to_pass": list(group.get("fail_to_pass", [])),
                       "overlay": {"files": {dest: text(src) for dest, src in sorted(group.get("files", {}).items())},
                                   "patch": text(group.get("patch", ""))}})
    return {"task_id": raw["task_id"], "reference_patch": text(raw.get("reference_patch", "")), "groups": groups}


def _seen(runs: list[dict], test: str) -> list[str]:
    return ["timed_out" if run["timed_out"] else run["outcomes"].get(test, "missing") for run in runs]


def _judge(groups: list[dict], tree: Path, executor, overlays: dict[str, dict],
           retried: list[int]) -> tuple[dict[str, dict], list[str]]:
    """Each kept check's three outcomes on ``tree`` and whether it fails (two of three, or a timeout); and the
    infrastructure error that persisted after one retry, if any, at which point nothing more is run."""
    checks: dict[str, dict] = {}
    for group in groups:
        if not group["kept"]:
            continue
        runs = []
        for _ in range(RUNS):
            runs.append(_attempt(executor, tree, group["kept"], timeout_s=group["timeout_s"],
                                 overlay=overlays[group["id"]], retried=retried))
            if runs[-1]["error"]:
                return checks, [f"{group['id']}: {runs[-1]['error']}"]
        late = any(run["timed_out"] for run in runs)
        for test in group["kept"]:
            seen = _seen(runs, test)
            category = ("specification" if group["origin"] != "hidden_tests"
                        else "fail_to_pass" if test in group["fail_to_pass"] else "pass_to_pass")
            checks[test] = {"group": group["id"], "origin": group["origin"], "runs": seen, "category": category,
                            "failed": late or sum(s != "passed" for s in seen) >= 2}
    return checks, []


def _attempt(executor, tree: Path, tests: list[str], *, timeout_s: float, overlay: dict, retried: list[int]) -> dict:
    """One run, and once more when the executor itself failed (an infrastructure error, never a test outcome)."""
    run = executor.run(tree, tests, timeout_s=timeout_s, overlay=overlay)
    if run["error"]:
        retried[0] += 1
        run = executor.run(tree, tests, timeout_s=timeout_s, overlay=overlay)
    return run


def calibrate(contract: dict, *, reference_tree: Path, base_tree: Path, executor, timeout_s: float) -> dict:
    """The kept checks, their timeouts and the two oracle controls for one task (see the module docstring)."""
    groups, overlays = [], {g["id"]: g["overlay"] for g in contract["groups"]}
    reference: dict[str, list[str]] = {}
    retried, errors = [0], []
    for group in contract["groups"]:
        if not group["tests"]:
            continue
        runs = [_attempt(executor, reference_tree, group["tests"], timeout_s=timeout_s, overlay=group["overlay"],
                         retried=retried) for _ in range(RUNS)]
        errors += [f"{group['id']}: {run['error']}" for run in runs if run["error"]]
        seen = {test: _seen(runs, test) for test in group["tests"]}
        reference.update(seen)
        dropped = [test for test in group["tests"] if len(set(seen[test])) > 1]
        kept = [test for test in group["tests"] if test not in dropped]
        groups.append({"id": group["id"], "origin": group["origin"], "kept": kept, "dropped": dropped,
                       "fail_to_pass": [t for t in group["fail_to_pass"] if t in kept],
                       "timeout_s": max(MIN_TIMEOUT_S, TIMEOUT_FACTOR * median(run["seconds"] for run in runs))})
    kept = [test for group in groups for test in group["kept"]]
    passes = bool(kept) and all(reference[test] == ["passed"] * RUNS for test in kept)
    base, persisted = _judge(groups, base_tree, executor, overlays, retried)
    errors += persisted
    failing = sorted(test for test, found in base.items() if found["failed"])
    controls = {"reference_passes_every_kept_check": passes, "base_fails_a_check_the_reference_passes": bool(failing),
                "base_failing": failing}
    return {"task_id": contract["task_id"], "groups": groups, "dropped": sum(len(g["dropped"]) for g in groups),
            "reference": reference, "controls": controls, "infrastructure_errors": errors, "retried": retried[0],
            "passed": passes and bool(failing) and not errors}


def label(contract: dict, calibration: dict, tree: Path, *, executor) -> dict:
    """The patch on ``tree`` labelled correct or defective by its kept checks (see the module docstring); no label
    (``None``) when an infrastructure error persisted after one retry."""
    overlays = {g["id"]: g["overlay"] for g in contract["groups"]}
    retried = [0]
    checks, errors = _judge(calibration["groups"], tree, executor, overlays, retried)
    if errors:
        return {"label": None, "unlabelled_because": "an infrastructure error persisted after one retry",
                "checks": checks, "failed": [], "by_origin": {}, "disagreement": False, "defect_categories": [],
                "infrastructure_errors": errors, "retried": retried[0]}
    failed = sorted(test for test, found in checks.items() if found["failed"])
    origins = sorted({found["origin"] for found in checks.values()})
    by_origin = {origin: not any(f["failed"] for f in checks.values() if f["origin"] == origin) for origin in origins}
    return {"label": "defective" if failed or not checks else "correct", "checks": checks, "failed": failed,
            "by_origin": by_origin, "disagreement": len(set(by_origin.values())) > 1,
            "defect_categories": sorted({checks[test]["category"] for test in failed}), "infrastructure_errors": [],
            "retried": retried[0]}
