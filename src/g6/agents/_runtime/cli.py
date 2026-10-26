# SPDX-License-Identifier: Apache-2.0
"""CliAgentRunner: the one way a command-line agent (Codex first) is started.

The program is one the person installed (a ``g6.core.outside.OutsideProgram``), found when it is called, on PATH or
through the environment variable it declares, and never bundled. It runs as a bounded child process through
``g6.core.component_process``: its whole process tree is ended when the deadline passes, when G6's stop is engaged,
when its output passes the cap, and after it exits. Its prompt goes in on stdin from a file, never on the command line,
and its output goes to a file, never a pipe (the archive's ``subprocess.run(capture_output=True)`` could wedge on
Windows while a grandchild held the pipe open, AGENT_CODEX-01). It starts in the folder the adapter names, with a clean
environment: the few variables a program needs to start and to find its own sign-in (:data:`BASE_ENV`, and the ones
the adapter adds), and a temporary folder of its own inside the scratch folder G6 removes afterwards. No key, token or
setting of G6's is passed. The program still acts with your rights: this bounds it; it is not a sandbox.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from g6.agents._runtime import codes
from g6.agents._runtime.model import AgentRefusal
from g6.core import component_process, outside
from g6.core.process_tree import ProcessTreeError

#: What a command-line agent is given of G6's environment: enough to start (the search path, the system folders, the
#: shell Windows' ``.cmd`` shims need), the person's home folders, where its own sign-in lives, and the locale. Nothing
#: else: no key, token or setting of G6's or anyone else's.
BASE_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "HOME", "USERPROFILE", "HOMEDRIVE",
            "HOMEPATH", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
#: The file in the run's scratch folder that holds everything the program printed (its output and its errors).
OUTPUT_LOG = "output.log"
VERSION_TIMEOUT_S = 15.0
_TAIL = 20_000
#: The characters a path G6 itself puts on a command line may hold: a Windows ``.cmd`` shim parses its arguments again.
_PLAIN_PATH = re.compile(r"[A-Za-z0-9 _\-.:\\/~()]+")


def clean_environment(keep: Iterable[str] = (), *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables of :data:`BASE_ENV` and ``keep`` that are set, and no others."""
    source = os.environ if environ is None else environ
    return {name: source[name] for name in (*BASE_ENV, *keep) if source.get(name)}


def _stopped(stop: Callable[[], str | None] | None) -> str | None:
    if stop is None:
        return None
    try:
        why = stop()
    except Exception as exc:  # noqa: BLE001 - a stop that cannot be read counts as engaged
        return f"the stop could not be read ({type(exc).__name__})"
    return str(why) if why else None


@dataclass(frozen=True)
class CliRun:
    """One finished run of the program: what was started, how it exited, and the end of what it printed."""

    argv: tuple[str, ...]
    exit_code: int
    output_tail: str
    output_bytes: int
    elapsed_s: float
    cleanup_confirmed: bool
    env_names: tuple[str, ...]


class CliAgentRunner:
    """Finds and starts one outside program; see the module docstring."""

    def __init__(self, program: outside.OutsideProgram, *, keep_env: tuple[str, ...] = (),
                 max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> None:
        if not program.command:
            raise ValueError(f"{program.name} is a service, not a program to start")
        self.program, self.keep_env, self.max_output_bytes = program, tuple(keep_env), int(max_output_bytes)

    def _missing(self, why: str) -> AgentRefusal:
        return AgentRefusal(outside.OUTSIDE_PROGRAM_MISSING,
                            f"{self.program.name} ({self.program.purpose}) is not ready: {why}. G6 never installs it; "
                            f"install it yourself: {self.program.install}")

    def locate(self) -> str:
        """Where the program is, found as ``g6.core.outside`` finds it, without running it."""
        env = self.program.env
        named = os.environ.get(env, "").strip() if env else ""
        if named:
            where = outside.find_program(named) or (named if Path(named).is_file() else None)
            if not where:
                raise self._missing(f"{env} names {named}, which is not a program here")
            return where
        where = outside.find_program(self.program.command)
        if not where:
            raise self._missing(f"no {self.program.command} on PATH" + (f" and {env} is not set" if env else ""))
        return where

    def require(self, *, stop: Callable[[], str | None] | None = None) -> outside.Detected:
        """The program's version check, run as every run is (clean environment, bounded, its tree ended), through
        ``g6.core.outside.detect``; refused with the install steps when it does not answer."""
        def version(argv: list[str], timeout: float) -> tuple[int, str]:
            with self.session() as scratch:
                done = self.run(argv, prompt="", cwd=scratch, seconds=timeout, scratch=scratch, stop=stop)
            return done.exit_code, done.output_tail

        found = outside.detect(self.program, run=version, timeout=VERSION_TIMEOUT_S)
        if not found.found:
            raise self._missing(found.detail)
        return found

    @contextmanager
    def session(self) -> Iterator[Path]:
        """A scratch folder for one run (its output, its temporary folder, any file the program writes for G6),
        removed afterwards."""
        with tempfile.TemporaryDirectory(prefix="g6_agent_", ignore_cleanup_errors=True) as tmp:
            yield Path(tmp)

    @staticmethod
    def plain(path: Path) -> str:
        """``path`` for a command line, refused when it holds a character a ``.cmd`` shim could misread."""
        text = str(path)
        if not _PLAIN_PATH.fullmatch(text):
            raise AgentRefusal(codes.START_FAILED, f"the scratch folder {text!r} holds characters a command line may "
                                                   "misread; set TEMP to a plain folder")
        return text

    def environment(self, scratch: Path) -> dict[str, str]:
        temp = scratch / "tmp"
        temp.mkdir(exist_ok=True)
        return clean_environment(self.keep_env) | {"TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp)}

    def run(self, argv: list[str], *, prompt: str, cwd: Path, seconds: float, scratch: Path,
            stop: Callable[[], str | None] | None = None) -> CliRun:
        """Run ``argv`` in ``cwd`` with ``prompt`` on stdin, for at most ``seconds``; see the module docstring.
        A deadline, an engaged stop, too much output, a cleanup G6 cannot confirm and a program that cannot start are
        each an :class:`~g6.agents._runtime.model.AgentRefusal` with its code."""
        name = self.program.name
        env = self.environment(scratch)
        started = time.monotonic()
        with open(scratch / OUTPUT_LOG, "w+b") as out, tempfile.TemporaryFile(dir=scratch) as incoming:
            incoming.write(prompt.encode("utf-8"))
            incoming.seek(0)

            def too_much() -> bool:
                return os.fstat(out.fileno()).st_size > self.max_output_bytes

            window = None
            try:
                with component_process.window(seconds, lambda: too_much() or bool(_stopped(stop))) as window:
                    code = component_process.run_process(list(argv), env=env, cwd=cwd, stdin=incoming, stdout=out,
                                                         stderr=subprocess.STDOUT)
            except component_process.ExecutionInterrupted as exc:
                if exc.stopped and too_much():
                    raise AgentRefusal(codes.OUTPUT_TOO_LARGE, f"{name}'s output passed {self.max_output_bytes} "
                                                               "bytes, so it was stopped and its process tree ended") from None
                if exc.stopped:
                    raise AgentRefusal(codes.STOPPED, f"G6's stop was engaged ({_stopped(stop) or 'stop requested'}), "
                                                      f"so {name} was stopped and its process tree ended") from None
                raise AgentRefusal(codes.DEADLINE, f"{name} did not finish within {seconds:g} seconds, so it was "
                                                   "stopped and its process tree ended", backend_failure=True) from None
            except component_process.ContainmentFailure:
                raise AgentRefusal(codes.CLEANUP_UNCONFIRMED, f"G6 could not confirm that {name}'s processes ended",
                                   backend_failure=True) from None
            except (OSError, ProcessTreeError) as exc:
                raise AgentRefusal(codes.START_FAILED, f"{name} could not be run: {exc}"[:500],
                                   backend_failure=True) from None
            cleanup = bool(window is not None and window.execution.get("cleanup_confirmed"))
            out.seek(0)
            raw = out.read(self.max_output_bytes + 1)
        if len(raw) > self.max_output_bytes:
            raise AgentRefusal(codes.OUTPUT_TOO_LARGE, f"{name}'s output passed {self.max_output_bytes} bytes")
        text = raw.decode("utf-8", "replace")
        return CliRun(argv=tuple(argv), exit_code=code, output_tail=text[-_TAIL:], output_bytes=len(raw),
                      elapsed_s=round(time.monotonic() - started, 3), cleanup_confirmed=cleanup,
                      env_names=tuple(sorted(env)))


__all__ = ["BASE_ENV", "DEFAULT_MAX_OUTPUT_BYTES", "OUTPUT_LOG", "CliAgentRunner", "CliRun", "clean_environment"]
