# SPDX-License-Identifier: Apache-2.0
"""Programs out of process: the only module of g6.algebra that starts one.

Two programs run, each under :mod:`g6.core.component_process` in a fresh temporary directory that is removed
afterwards, with a minimal environment (its temporary directory and home inside the run directory), a time limit, an
output cap, a memory cap on each process (the Job Object on Windows; on POSIX the program lowers its own limits or the
time limit is the bound) and its whole process tree killed when a limit is passed:

* G6's own SymPy worker, ``python -I -m g6.algebra.worker`` (:mod:`g6.algebra.worker`), for every SymPy op. It is this
  interpreter in isolated mode (no ``PYTHONPATH``, no user site, no current directory on the path). The request goes
  to it as a file on standard input, never on a command line; caller text in it reaches nothing but G6's parser. The
  worker is not "running caller code", so the SymPy ops are not sensitive. Its environment keeps only ``SYSTEMROOT``,
  ``WINDIR``, ``LANG`` and ``LC_ALL``;
* SageMath, ``sage``, which the user installs (:mod:`g6.algebra.sage`). It reads one file G6 wrote from its own parse
  of the caller's expression, and is run as ``sage <file>`` or ``sage --version``, no other flag. Its environment also
  keeps ``PATH`` (the ``sage`` launcher is a script that finds its own programs there) and ``SAGE_ROOT``,
  ``SAGE_LOCAL`` and ``SAGE_VENV`` when they are set, with ``DOT_SAGE``, ``IPYTHONDIR`` and ``MPLCONFIGDIR`` inside the
  run directory.

SageMath on the file built from the caller's expression goes through :mod:`g6.core.confine`, like every run of
untrusted code: where a backend can confine it (bubblewrap on Linux, an AppContainer on Windows), it reads only its own
installation (``confine.program_paths``, with ``SAGE_ROOT``, ``SAGE_LOCAL`` and ``SAGE_VENV``) and writes only the run
directory, with no network; the run's ``isolation`` says how it ran, and where isolation is required and no backend
can give it the run is refused before SageMath starts (``G6_E_ISOLATION_UNAVAILABLE``). ``sage --version`` and G6's own
worker are not planned. Elsewhere this is a resource bound, not a sandbox: both run as you. That is why the SageMath
ops are sensitive.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.algebra.envelope import OUTPUT_UNRECOGNISED, RUN_FAILED, Refusal
from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.process_tree import ProcessTreeError

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
OUTPUT_CAP_LIMIT = component_process.MAX_MESSAGE_BYTES
_WORKER_ENV = ("SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
_SAGE_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "SAGE_ROOT", "SAGE_LOCAL", "SAGE_VENV")
_TAIL = 8000


@dataclass(frozen=True)
class Run:
    """How a program ended: its exit code (``None`` when it was stopped), its output and error text, and ``stopped``
    (``"time"`` or ``"output"`` when a limit stopped it, else empty)."""

    code: int | None
    output: str
    errors: str
    stopped: str


def _execute(argv: list[str], cwd: Path, env: dict[str, str], seconds: float, cap: int, memory_mb: int,
             record: dict[str, Any], stdin_path: Path | None = None, merge: bool = False,
             confined: confine.Plan | None = None) -> Run:
    """Run ``argv`` under a time limit, an output cap and a memory cap, the process tree killed when one is passed."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        def too_much() -> bool:
            return os.fstat(out.fileno()).st_size > cap

        stopped, code, window = "", None, None
        source = stdin_path.open("rb") if stdin_path is not None else None
        try:
            with component_process.window(seconds, too_much) as window:
                code = component_process.run_process(argv, env=env, cwd=cwd,
                                                     stdin=source if source is not None else subprocess.DEVNULL,
                                                     stdout=out, stderr=subprocess.STDOUT if merge else err,
                                                     memory_bytes=memory_mb * 1024 * 1024, confined=confined)
        except component_process.ExecutionInterrupted as exc:
            stopped = "output" if exc.stopped and too_much() else "time"
        except (OSError, ProcessTreeError) as exc:
            if isinstance(exc, confine.NotStarted) and "isolation" in record:
                record["isolation"] = confine.not_started(record["isolation"], exc)
            raise Refusal(RUN_FAILED, f"the program could not be run to the end: {exc}"[:500]) from None
        finally:
            if source is not None:
                source.close()
            if window is not None:
                record["execution"] = {k: window.execution.get(k) for k in ("cleanup_confirmed", "child_exited",
                                                                             "elapsed_seconds")}
        out.seek(0)
        raw = out.read(cap + 1)
        err.seek(0)
        errors = err.read(64 * 1024).decode("utf-8", "replace")
    if len(raw) > cap:
        stopped = "output"
    text = raw[:cap].decode("utf-8", "replace")
    record |= {"exit_code": code, "output_bytes": len(raw), "stopped_by": stopped or None}
    return Run(code, text, errors, stopped)


def _environment(root: Path, keep: tuple[str, ...]) -> dict[str, str]:
    env = {k: os.environ[k] for k in keep if k in os.environ}
    return env | {"TEMP": str(root / "tmp"), "TMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
                  "HOME": str(root / "home")}


def call_worker(request: dict[str, Any], *, seconds: float, memory_mb: int, cap: int,
                record: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Run G6's SymPy worker on ``request``; its JSON lines, and how it was stopped (``""``, ``"time"``, ``"output"``).
    Refused when it could not run or ended without finishing for any other reason."""
    if not sys.executable:
        raise Refusal(RUN_FAILED, "this Python has no interpreter path to start G6's worker from")
    argv = [sys.executable, "-I", "-m", "g6.algebra.worker"]
    with tempfile.TemporaryDirectory(prefix="g6_algebra_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        for sub in ("work", "tmp", "home"):
            (root / sub).mkdir()
        sent = root / "request.json"
        sent.write_bytes(json.dumps(request).encode("utf-8"))
        record["argv"] = list(argv)
        run = _execute(argv, root / "work", _environment(root, _WORKER_ENV), seconds, cap, memory_mb, record,
                       stdin_path=sent)
    lines: list[dict[str, Any]] = []
    rows = run.output.splitlines()
    for number, row in enumerate(rows, start=1):
        if not row.strip():
            continue
        try:
            line = json.loads(row)
        except json.JSONDecodeError:
            if run.stopped and number == len(rows):
                break                       # the last line, cut off when the run was stopped
            record["output_tail"] = run.output[-_TAIL:]
            raise Refusal(OUTPUT_UNRECOGNISED, "G6's worker wrote a line that is not JSON") from None
        if not isinstance(line, dict) or not isinstance(line.get("stage"), str):
            record["output_tail"] = run.output[-_TAIL:]
            raise Refusal(OUTPUT_UNRECOGNISED, "G6's worker wrote a line G6 does not read")
        lines.append(line)
    if not run.stopped and not any(line["stage"] == "done" for line in lines):
        record["worker_errors"] = run.errors[-_TAIL:]
        said = " ".join(run.errors.split())[-400:] or "no message"
        raise Refusal(RUN_FAILED, f"G6's worker stopped with exit code {run.code} before it finished (a crash, or the "
                                  f"memory cap): {said}")
    return lines, run.stopped


def run_sage(sage: str, arguments: list[str], *, file_name: str = "", file_text: str | None = None, seconds: float,
             memory_mb: int, cap: int, record: dict[str, Any]) -> Run:
    """Run ``sage`` (from :func:`g6.algebra.sage.find_sage`) with ``arguments`` in a fresh directory, with
    ``file_text`` written there as ``file_name``, and remove the directory."""
    with confine.run_folder(prefix="g6_sage_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        for sub in ("work", "tmp", "home", "dot_sage", "ipython", "matplotlib"):
            (root / sub).mkdir()
        if file_text is not None:
            (root / "work" / file_name).write_bytes(file_text.encode("utf-8"))
        argv = [sage, *arguments]
        record["argv"] = list(argv)
        env = _environment(root, _SAGE_ENV) | {"DOT_SAGE": str(root / "dot_sage"), "IPYTHONDIR": str(root / "ipython"),
                                               "MPLCONFIGDIR": str(root / "matplotlib")}
        confined = None
        if file_text is not None:   # built from the caller's expression: untrusted, so planned (a version run is not)
            reads = confine.program_paths(sage, *(env.get(key, "") for key in ("SAGE_ROOT", "SAGE_LOCAL", "SAGE_VENV")))
            confined = confine.plan(confine.Confinement(read_write=(tmp,), read_only=reads))
            record["isolation"] = confined.record()
            said = confine.refused(confined)
            if said:
                raise Refusal(ISOLATION_UNAVAILABLE, said)
        try:
            return _execute(argv, root / "work", env, seconds, cap, memory_mb, record, merge=True, confined=confined)
        finally:
            left = confined.release() if confined is not None else ""
            if left:
                record["isolation"] = {**record["isolation"], "cleanup": left}


__all__ = ["MAX_OUTPUT_BYTES", "OUTPUT_CAP_LIMIT", "Run", "call_worker", "run_sage"]
