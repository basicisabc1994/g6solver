# SPDX-License-Identifier: Apache-2.0
"""Lean 4 out of process: the only module of g6.lean that starts a program.

Lean is yours, never installed by G6: ``G6_LEAN`` names the ``lean`` executable (a path, or a name on PATH), or
``lean`` is found on PATH. elan, Lean's toolchain manager, puts it there. When the ``lean`` found is elan's own proxy
(its folder holds a ``toolchains`` folder beside ``bin``), G6 passes elan its home so the proxy finds your default
toolchain; it picks the toolchain as elan does. To pin one toolchain, set ``G6_LEAN`` to that toolchain's own ``lean``.

Each run gets a fresh temporary directory. The file G6 built is written there as ``Proof.lean`` and Lean is run on it
as ``lean --json Proof.lean`` from inside that directory: the source never reaches a command line, and no other flag is
ever passed (no plugin, shared library, program to run or option setting). Lean gets a minimal environment (no
``LEAN_PATH``, ``LEAN_SYSROOT`` or other settings of yours), with its temporary directory and home inside the run
directory. It runs under :mod:`g6.core.component_process`: a time limit (default 60 seconds, at most 600), an output cap
(4 MiB by default), a memory cap on each process (enforced on Windows through the Job Object; elsewhere the time limit
is the bound) and the whole process tree killed when a limit is passed; the directory is removed afterwards. Lean's own
``--memory`` flag is not used: in Lean 4.22.0 it is rejected as an unknown option.

A run on the caller's file goes through :mod:`g6.core.confine`, like every run of untrusted code: where a backend can
confine it (bubblewrap on Linux, an AppContainer on Windows), Lean reads only its toolchain (``confine.program_paths``,
with ``ELAN_HOME``) and writes only the run directory, with no network; the run's ``isolation`` says how it ran, and
where isolation is required and no backend can give it the run is refused before Lean starts
(``G6_E_ISOLATION_UNAVAILABLE``). ``lean --version`` runs nothing of the caller's and is not planned. Elsewhere this is
a resource bound, not a sandbox: Lean runs as you. That is why the ops that run it are sensitive: callable from
Python, and over MCP only when the person who runs the server lists them in ``G6_MCP_SENSITIVE`` (owner, 2026-10-20).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.outside import find_program
from g6.core.process_tree import ProcessTreeError
from g6.lean.envelope import LEAN_ENV, LEAN_MISSING, OUTPUT_TOO_LARGE, RUN_FAILED, TIME_LIMIT, Refusal
from g6.lean.lean_output import FILE_NAME

DEFAULT_TIME_LIMIT_S = 60.0
MAX_TIME_LIMIT_S = 600.0
DEFAULT_MEMORY_MB, MIN_MEMORY_MB, MAX_MEMORY_MB = 4096, 256, 32768
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
OUTPUT_CAP_LIMIT = component_process.MAX_MESSAGE_BYTES
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "ELAN_HOME", "ELAN_TOOLCHAIN")
_TAIL = 8000


def find_lean() -> str:
    """The ``lean`` executable: ``G6_LEAN`` when it is set, else ``lean`` on PATH. Refused when there is none."""
    named = os.environ.get(LEAN_ENV, "").strip()
    if named:
        found = find_program(named) or (named if Path(named).is_file() else None)
        if not found:
            raise Refusal(LEAN_MISSING, f"{LEAN_ENV} names {named}, which is not a program here; G6 never installs "
                                        "Lean")
        return found
    found = find_program("lean")
    if not found:
        raise Refusal(LEAN_MISSING, f"no lean on PATH and {LEAN_ENV} is not set; G6 never installs Lean")
    return found


def _environment(root: Path, lean: str) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    elan = Path(lean).resolve().parent.parent
    if "ELAN_HOME" not in env and Path(lean).resolve().parent.name == "bin" and (elan / "toolchains").is_dir():
        env["ELAN_HOME"] = str(elan)
    return env | {"TEMP": str(root / "tmp"), "TMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
                  "HOME": str(root / "home")}


def _execute(argv: list[str], cwd: Path, env: dict[str, str], seconds: float, cap: int, memory_mb: int,
             record: dict[str, Any], confined: confine.Plan | None = None) -> tuple[int, str]:
    """Run ``argv`` under a time limit, an output cap and a memory cap, the process tree killed when one is passed."""
    with tempfile.TemporaryFile() as out:
        def too_much() -> bool:
            return os.fstat(out.fileno()).st_size > cap

        window = None
        try:
            with component_process.window(seconds, too_much) as window:
                code = component_process.run_process(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                                     stderr=subprocess.STDOUT, memory_bytes=memory_mb * 1024 * 1024,
                                                     confined=confined)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped and too_much():
                raise Refusal(OUTPUT_TOO_LARGE, f"Lean's output passed its cap of {cap} bytes, so the run was stopped "
                                                "and its process tree killed") from None
            raise Refusal(TIME_LIMIT, f"Lean did not finish within {seconds:g} s, so it was stopped and its process "
                                      "tree killed") from None
        except (OSError, ProcessTreeError) as exc:
            if isinstance(exc, confine.NotStarted) and "isolation" in record:
                record["isolation"] = confine.not_started(record["isolation"], exc)
            raise Refusal(RUN_FAILED, f"Lean could not be run to the end: {exc}"[:500]) from None
        finally:
            if window is not None:
                record["execution"] = {k: window.execution.get(k) for k in ("cleanup_confirmed", "child_exited",
                                                                             "elapsed_seconds")}
        out.seek(0)
        raw = out.read(cap + 1)
    if len(raw) > cap:
        raise Refusal(OUTPUT_TOO_LARGE, f"Lean's output passed its cap of {cap} bytes")
    text = raw.decode("utf-8", "replace")
    record |= {"exit_code": code, "output_bytes": len(raw), "output_tail": text[-_TAIL:]}
    return code, text


def run(lean: str, arguments: list[str], *, file_text: str | None, seconds: float, memory_mb: int, cap: int,
        record: dict[str, Any]) -> tuple[int, str]:
    """Run ``lean`` (from :func:`find_lean`) with ``arguments`` (``["--json", "Proof.lean"]`` or ``["--version"]``)
    in a fresh directory, with ``file_text`` written there as ``Proof.lean``, and remove the directory. Returns the
    exit code and the output."""
    with confine.run_folder(prefix="g6_lean_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        for sub in ("work", "tmp", "home"):
            (root / sub).mkdir()
        record["run_dir"] = str(root)
        if file_text is not None:
            (root / "work" / FILE_NAME).write_bytes(file_text.encode("utf-8"))
        argv = [lean, *arguments]
        record["argv"] = list(argv)
        env = _environment(root, lean)
        confined = None
        if file_text is not None:   # the caller's file: untrusted, so planned (a version run is not)
            reads = confine.program_paths(lean, env.get("ELAN_HOME", ""))
            confined = confine.plan(confine.Confinement(read_write=(tmp,), read_only=reads))
            record["isolation"] = confined.record()
            said = confine.refused(confined)
            if said:
                raise Refusal(ISOLATION_UNAVAILABLE, said)
        try:
            return _execute(argv, root / "work", env, seconds, cap, memory_mb, record, confined)
        finally:
            left = confined.release() if confined is not None else ""
            if left:
                record["isolation"] = {**record["isolation"], "cleanup": left}


__all__ = ["DEFAULT_MEMORY_MB", "DEFAULT_TIME_LIMIT_S", "MAX_MEMORY_MB", "MAX_OUTPUT_BYTES", "MAX_TIME_LIMIT_S",
           "MIN_MEMORY_MB", "OUTPUT_CAP_LIMIT", "find_lean", "run"]
