# SPDX-License-Identifier: Apache-2.0
"""SWI-Prolog out of process: the only module of g6.prolog that starts a program.

SWI-Prolog is yours, never installed by G6: ``G6_SWIPL`` names the ``swipl`` program (a path, or a name on PATH), or
``swipl`` is found on PATH. Caller code never runs in G6's process: there is no in-process bridge, and every program
runs in a fresh ``swipl``.

Each run gets a fresh temporary directory. G6 writes its fixed driver there as ``driver.pl``, with the caller's
program (``program.pl``), the caller's goal (``goal.pl``) and its own request (``request.json``), and runs
``swipl -f none -F none --no-packs --no-tty --no-pce -q --stack-limit=<n>m driver.pl`` from inside that directory: the
program and the goal never reach a command line. ``-f none`` and ``-F none`` load no user or site start-up file,
``--no-packs`` attaches no add-on, ``--no-pce`` leaves out the graphics toolkit, and the database is the fresh one of a
new process. SWI-Prolog gets a minimal environment (none of your settings), with its temporary directory and home
inside the run directory. It runs under :mod:`g6.core.component_process`: a time limit (the op's own, at most 600
seconds, plus a start-up allowance), an output cap (4 MiB by default) and the whole process tree killed when a limit is
passed; the directory is removed afterwards. Memory: SWI-Prolog's stacks are capped on every system by
``--stack-limit``, half of ``memory_mb`` and at least 64 MB (:func:`stack_limit_mb`); the whole process is capped at
``memory_mb`` only on Windows, through the Job Object.

This is a resource bound, not a sandbox, and the driver's restricted syntax is not a security boundary either:
SWI-Prolog runs as you. Run programs you do not trust inside your own sandbox, such as a container or a virtual machine
(owner, 2026-10-21). That is also why the ops that run it are sensitive: callable from Python, and over MCP only when
the person who runs the server lists them in ``G6_MCP_SENSITIVE`` (owner, 2026-10-20).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from g6.core import component_process
from g6.core.outside import find_program
from g6.core.process_tree import ProcessTreeError
from g6.prolog.envelope import OUTPUT_TOO_LARGE, RUN_FAILED, SWIPL_ENV, SWIPL_MISSING, TIME_LIMIT, Refusal

DEFAULT_TIME_LIMIT_S = 60.0
MAX_TIME_LIMIT_S = 600.0
#: Added to the op's time limit for the process: SWI-Prolog's start-up and the driver's own reading and writing.
STARTUP_ALLOWANCE_S = 10.0
DEFAULT_MEMORY_MB, MIN_MEMORY_MB, MAX_MEMORY_MB = 2048, 256, 32768
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
OUTPUT_CAP_LIMIT = component_process.MAX_MESSAGE_BYTES
DRIVER_SOURCE = Path(__file__).with_name("driver.pl")
DRIVER_FILE = "driver.pl"
#: Every flag G6 passes besides the stack limit; nothing else ever reaches swipl's command line.
FLAGS = ("-f", "none", "-F", "none", "--no-packs", "--no-tty", "--no-pce", "-q")
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
_TAIL = 8000


def find_swipl() -> str:
    """The ``swipl`` program: ``G6_SWIPL`` when it is set, else ``swipl`` on PATH. Refused when there is none."""
    named = os.environ.get(SWIPL_ENV, "").strip()
    if named:
        found = find_program(named) or (named if Path(named).is_file() else None)
        if not found:
            raise Refusal(SWIPL_MISSING, f"{SWIPL_ENV} names {named}, which is not a program here; G6 never installs "
                                         "SWI-Prolog")
        return found
    found = find_program("swipl")
    if not found:
        raise Refusal(SWIPL_MISSING, f"no swipl on PATH and {SWIPL_ENV} is not set; G6 never installs SWI-Prolog")
    return found


def stack_limit_mb(memory_mb: int) -> int:
    """SWI-Prolog's stack limit for a memory cap: half of it, at least 64 MB. It holds on every system."""
    return max(64, memory_mb // 2)


def driver_arguments(memory_mb: int) -> list[str]:
    """The arguments that run G6's driver: :data:`FLAGS`, a Prolog stack limit of half the memory cap (so a search that
    builds too much ends in SWI-Prolog's own resource error, reported as the stack limit, before a process cap is
    reached), and the driver file."""
    return [*FLAGS, f"--stack-limit={stack_limit_mb(memory_mb)}m", DRIVER_FILE]


def _environment(root: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    inside = {"TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp", "HOME": "home", "APPDATA": "home", "LOCALAPPDATA": "home",
              "USERPROFILE": "home", "XDG_CONFIG_HOME": "home", "XDG_DATA_HOME": "home"}
    return env | {key: str(root / sub) for key, sub in inside.items()}


def _execute(argv: list[str], cwd: Path, env: dict[str, str], seconds: float, cap: int, memory_mb: int,
             record: dict[str, Any]) -> tuple[int, str]:
    """Run ``argv`` under a time limit, an output cap and a memory cap, the process tree killed when one is passed."""
    with tempfile.TemporaryFile() as out:
        def too_much() -> bool:
            return os.fstat(out.fileno()).st_size > cap

        window = None
        try:
            with component_process.window(seconds, too_much) as window:
                code = component_process.run_process(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                                     stderr=subprocess.STDOUT, memory_bytes=memory_mb * 1024 * 1024)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped and too_much():
                raise Refusal(OUTPUT_TOO_LARGE, f"SWI-Prolog's output passed its cap of {cap} bytes, so the run was "
                                                "stopped and its process tree killed") from None
            raise Refusal(TIME_LIMIT, f"SWI-Prolog did not finish within {seconds:g} s, so it was stopped and its "
                                      "process tree killed") from None
        except (OSError, ProcessTreeError) as exc:
            raise Refusal(RUN_FAILED, f"SWI-Prolog could not be run to the end: {exc}"[:500]) from None
        finally:
            if window is not None:
                record["execution"] = {k: window.execution.get(k) for k in ("cleanup_confirmed", "child_exited",
                                                                             "elapsed_seconds")}
        out.seek(0)
        raw = out.read(cap + 1)
    if len(raw) > cap:
        raise Refusal(OUTPUT_TOO_LARGE, f"SWI-Prolog's output passed its cap of {cap} bytes")
    text = raw.decode("utf-8", "replace")
    record |= {"exit_code": code, "output_bytes": len(raw), "output_tail": text[-_TAIL:]}
    return code, text


def run(swipl: str, arguments: list[str], *, files: dict[str, str], seconds: float, memory_mb: int, cap: int,
        record: dict[str, Any]) -> tuple[int, str]:
    """Run ``swipl`` (from :func:`find_swipl`) with ``arguments`` (:func:`driver_arguments`, or ``["--version"]``) in a
    fresh directory holding ``files`` (name to text) and, when the arguments name it, G6's driver; remove the
    directory. Returns the exit code and the output."""
    with tempfile.TemporaryDirectory(prefix="g6_prolog_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        for sub in ("work", "tmp", "home"):
            (root / sub).mkdir()
        record["run_dir"] = str(root)
        work = root / "work"
        if DRIVER_FILE in arguments:
            shutil.copyfile(DRIVER_SOURCE, work / DRIVER_FILE)
        for name, text in files.items():
            (work / name).write_bytes(text.encode("utf-8"))
        argv = [swipl, *arguments]
        record["argv"] = list(argv)
        return _execute(argv, work, _environment(root), seconds, cap, memory_mb, record)


def version_run(record: dict[str, Any]):  # noqa: ANN201 -- g6.core.outside's Runner
    """A runner for :func:`g6.core.outside.require` that starts ``swipl --version`` the way every other run starts:
    in a fresh directory, with a minimal environment, under the time limit and an output cap."""
    def bounded(argv: list[str], timeout: float) -> tuple[int, str]:
        try:
            return run(argv[0], list(argv[1:]), files={}, seconds=timeout, memory_mb=DEFAULT_MEMORY_MB,
                       cap=64 * 1024, record=record)
        except Refusal as refusal:
            if refusal.code == TIME_LIMIT:
                raise subprocess.TimeoutExpired(argv, timeout) from None
            raise OSError(refusal.reason) from None

    return bounded


__all__ = ["DEFAULT_MEMORY_MB", "DEFAULT_TIME_LIMIT_S", "DRIVER_FILE", "DRIVER_SOURCE", "FLAGS", "MAX_MEMORY_MB",
           "MAX_OUTPUT_BYTES", "MAX_TIME_LIMIT_S", "MIN_MEMORY_MB", "OUTPUT_CAP_LIMIT", "STARTUP_ALLOWANCE_S",
           "driver_arguments", "find_swipl", "run", "stack_limit_mb", "version_run"]
