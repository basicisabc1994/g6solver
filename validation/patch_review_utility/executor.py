# SPDX-License-Identifier: Apache-2.0
"""The test executor that arm 1 and the label runner share: one pytest run on a copy of a tree, with each test's
outcome read from a small pytest plugin, never from the console.

``Executor.run`` copies the tree into a fresh temporary folder, so the tree an arm reviewed is never changed; adds the
overlay (the label contract's hidden test files, or its test patch, applied with ``git apply``); runs the command;
and reports each test id pytest reported as ``passed``, ``failed`` (a failure or error in any phase) or ``skipped``
(a skip, or an expected failure). It runs the files (or folders) of the tests asked for, those present in the copy,
as the benchmark's own procedure runs test files, so one test id that no longer exists does not stop the others; a
test asked for that pytest never reported is ``missing`` (``outcome``).

The command is a list of words with four placeholders: ``{python}`` (this interpreter), ``{tree}`` (the copy),
``{support}`` (the folder holding the plugin, ``v9_outcomes``, and its report, ``outcomes.json``) and ``{container}``
(a name new to each run); a word that is exactly ``{tests}`` becomes those files. The default runs this interpreter's
pytest in the copy, which is for development fixtures only. A study level runs the command its entry freezes, inside
a confined environment with no network, for example ``docker run --rm --name {container} --network none ...`` that
mounts ``{tree}`` and ``{support}`` and sets ``PYTHONPATH`` and ``V9_OUTCOMES`` inside the container; ``confined``
records which.

**A run past its timeout.** The command is killed. Killing a container client does not stop its container, so when the
command names ``{container}``, the container itself is stopped by that name: the words before ``run`` (the container
program) with ``kill <name>`` and then ``rm -f <name>``; ``container_stopped`` says whether the removal succeeded.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

PLUGIN = '''\
import json
import os

_TESTS, _BROKEN = {}, []


def pytest_runtest_logreport(report):
    _TESTS.setdefault(report.nodeid, {})[report.when] = "skipped" if hasattr(report, "wasxfail") else report.outcome


def pytest_collectreport(report):
    if report.failed:
        _BROKEN.append(report.nodeid or ".")


def pytest_sessionfinish(session, exitstatus):
    with open(os.environ["V9_OUTCOMES"], "w", encoding="utf-8") as handle:
        json.dump({"tests": _TESTS, "collection_errors": _BROKEN, "exit": int(exitstatus)}, handle)
'''
DEFAULT_COMMAND = ("{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "v9_outcomes", "{tests}")
#: pytest's exit codes for a run that did not get to run its tests: interrupted, internal error, usage error.
NOT_RUN_EXITS = frozenset({2, 3, 4})
#: How long stopping a container by name may take.
STOP_TIMEOUT_S = 120
_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")


def _outcome(phases: dict) -> str:
    states = set(phases.values())
    if "failed" in states:
        return "failed"
    if phases.get("call") == "passed":
        return "passed"
    return "skipped" if "skipped" in states else "missing"


def outcome(run: dict, test: str) -> str:
    return run["outcomes"].get(test, "missing")


def unrunnable(run: dict) -> bool:
    """A run that did not get to run its tests: an error, a timeout, a collection error, or pytest's own stop."""
    return bool(run["error"] or run["timed_out"] or run["collection_errors"] or run["exit"] in NOT_RUN_EXITS)


class Executor:
    """``confined`` and ``network`` declare what the frozen command does (``network`` None: not stated, which only a
    development manifest may leave)."""

    def __init__(self, command: tuple[str, ...] | list[str] = DEFAULT_COMMAND, *, confined: bool = False,
                 network: bool | None = None):
        self.command, self.confined, self.network = tuple(command), confined, network

    def describe(self) -> dict:
        return {"command": list(self.command), "confined": self.confined, "network": self.network}

    def run(self, tree: str | Path, tests: list[str], *, timeout_s: float, overlay: dict | None = None) -> dict:
        overlay = overlay or {}
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="v9-run-") as folder:
            copy, support = Path(folder) / "tree", Path(folder) / "support"
            shutil.copytree(tree, copy, symlinks=True)
            support.mkdir()
            (support / "v9_outcomes.py").write_text(PLUGIN, encoding="utf-8")
            problem = _overlay(copy, overlay)
            if problem:
                return _result(started, error=f"the overlay did not apply: {problem}")
            targets = [rel for rel in dict.fromkeys(test.partition("::")[0] for test in tests) if (copy / rel).exists()]
            if not targets:
                return _result(started, error="none of the tests named is in the tree")
            name = f"v9-{uuid.uuid4().hex[:16]}"
            words = {"{python}": sys.executable, "{tree}": str(copy), "{support}": str(support), "{container}": name}
            cmd: list[str] = []
            for word in self.command:
                if word == "{tests}":
                    cmd += targets
                else:
                    for key, value in words.items():
                        word = word.replace(key, value)
                    cmd.append(word)
            env = {**{k: v for k, v in os.environ.items() if k.upper() in _KEEP}, "PYTHONPATH": str(support),
                   "V9_OUTCOMES": str(support / "outcomes.json"), "PYTHONDONTWRITEBYTECODE": "1"}
            try:
                done = subprocess.run(cmd, cwd=copy, env=env, capture_output=True, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                stopped = _stop_container(cmd, name) if "{container}" in self.command else None
                return {**_result(started, timed_out=True), "container_stopped": stopped}
            except OSError as exc:
                return _result(started, error=f"the command could not start ({type(exc).__name__})")
            report = support / "outcomes.json"
            if not report.is_file():
                return _result(started, exit_code=done.returncode,
                               error=f"no outcome report (exit code {done.returncode})")
            found = json.loads(report.read_text(encoding="utf-8"))
            return _result(started, exit_code=found["exit"], collection_errors=found["collection_errors"],
                           outcomes={node: _outcome(phases) for node, phases in found["tests"].items()})


def _stop_container(cmd: list[str], name: str) -> bool:
    """Kill and remove the container ``name`` with the program words before ``run``; whether the removal succeeded."""
    program = cmd[:cmd.index("run")] if "run" in cmd else cmd[:1]
    try:
        subprocess.run([*program, "kill", name], capture_output=True, timeout=STOP_TIMEOUT_S)
        return subprocess.run([*program, "rm", "-f", name], capture_output=True,
                              timeout=STOP_TIMEOUT_S).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _overlay(copy: Path, overlay: dict) -> str:
    for rel, text in sorted((overlay.get("files") or {}).items()):
        target = (copy / rel).resolve()
        if not target.is_relative_to(copy.resolve()):
            return f"{rel} is outside the tree"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    patch = overlay.get("patch") or ""
    if patch.strip():
        file = copy.parent / "overlay.diff"
        file.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8", newline="\n")
        done = subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", str(file)],
                              cwd=copy, capture_output=True, text=True, errors="replace")
        if done.returncode != 0:
            return (done.stderr or "git apply failed").strip()[:300]
    return ""


def _result(started: float, *, outcomes: dict | None = None, collection_errors: list | None = None,
            exit_code: int | None = None, timed_out: bool = False, error: str = "") -> dict:
    return {"outcomes": outcomes or {}, "collection_errors": collection_errors or [], "exit": exit_code,
            "seconds": round(time.monotonic() - started, 3), "timed_out": timed_out, "error": error}
