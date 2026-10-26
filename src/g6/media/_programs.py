# SPDX-License-Identifier: Apache-2.0
"""The outside programs the media family runs over their command line (ffmpeg, ffprobe, eSpeak NG, Blender): found at
call time, run as a bounded child process. ``_``-prefixed, so it never registers as a component.

* :func:`find` asks ``g6.core.outside`` where the program is (the path its environment variable names, or the command
  on PATH) and whether it answers; a missing one is :class:`~g6.media._types.MediaError` with
  ``G6_E_OUTSIDE_PROGRAM_MISSING`` and the install steps. G6 never installs, bundles or stands in for it.
* :func:`run` starts it with an argument list (never a shell and never a command line of words), through
  ``g6.core.component_process``: a deadline after which its whole process tree is killed, a cap on what it writes to
  its output (it is stopped past the cap), and a clean environment: no setting of yours reaches it but the few the
  operating system needs, with its home and temporary folders inside a fresh folder of the call's own, removed
  afterwards. A deadline passed is ``G6_E_MEDIA_TIME_LIMIT``; a program that could not be run, or whose process tree
  could not be confirmed ended, is ``G6_E_MEDIA_BACKEND_FAILED``.

This is a resource bound, not a sandbox: the program runs as you, which is why every op that runs one is sensitive.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from g6.core import component_process, outside
from g6.core.process_tree import ProcessTreeError
from g6.media._types import BACKEND_FAILED, OUTSIDE_MISSING, TIME_LIMIT, MediaError

DEFAULT_SECONDS = 120.0
MAX_SECONDS = 600.0
DEFAULT_OUTPUT_CAP = 16 * 1024 * 1024
DEFAULT_MEMORY_MB = 4096
#: The settings of yours a program is given: only what the operating system needs to start one.
_KEEP = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL")


@dataclass(frozen=True)
class Ran:
    """What a program did: its exit code, what it wrote to its output and its error stream, and how long it took."""

    code: int
    out: bytes
    err: bytes
    seconds: float

    def text(self, limit: int = 4000) -> str:
        """Its error stream (or, when that is empty, its output) as text, the last ``limit`` characters."""
        said = (self.err or self.out).decode("utf-8", "replace").strip()
        return said[-limit:]


def find(program: outside.OutsideProgram) -> outside.Detected:
    """Where ``program`` is and the version it reports, found now; refused with the install steps when it is not there
    or does not answer."""
    found = outside.detect(program)
    if not found.found or not found.where:
        raise MediaError(OUTSIDE_MISSING, f"{program.name} ({program.purpose}) is not ready: {found.detail}. G6 never "
                                          f"installs it; install it yourself: {program.install}")
    return found


def environment(where: str, home: Path) -> dict[str, str]:
    """The clean environment a program runs in: the few settings the operating system needs, a ``PATH`` of the
    program's own folder and the system's, and its home and temporary folders inside ``home``."""
    env = {k: os.environ[k] for k in _KEEP if k in os.environ}
    system = (os.path.join(env.get("SYSTEMROOT", r"C:\Windows"), "System32") if os.name == "nt" else "/usr/bin:/bin")
    env["PATH"] = os.pathsep.join([str(Path(where).resolve().parent), system])
    for name in ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                 "XDG_CACHE_HOME"):
        env[name] = str(home)
    return env


def run(where: str, args: list[str], *, seconds: float = DEFAULT_SECONDS, cwd: Path | str | None = None,
        stdin: bytes | None = None, output_cap: int = DEFAULT_OUTPUT_CAP, memory_mb: int = DEFAULT_MEMORY_MB,
        extra_env: dict[str, str] | None = None, name: str = "") -> Ran:
    """Run the program at ``where`` (:func:`find`'s ``where``) with ``args``; see the module docstring."""
    said = name or Path(where).stem
    if not all(isinstance(a, str) for a in args):
        raise TypeError("a program's arguments are a list of strings")
    seconds = max(0.1, min(float(seconds), MAX_SECONDS))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="g6-media-run-", ignore_cleanup_errors=True) as scratch, \
            tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as given:
        if stdin is not None:
            given.write(stdin)
            given.seek(0)
        env = environment(where, Path(scratch)) | dict(extra_env or {})

        def too_much() -> bool:
            return os.fstat(out.fileno()).st_size > output_cap or os.fstat(err.fileno()).st_size > output_cap

        try:
            with component_process.window(seconds, too_much):
                code = component_process.run_process([where, *args], env=env, cwd=cwd or scratch,
                                                     stdin=given if stdin is not None else subprocess.DEVNULL,
                                                     stdout=out, stderr=err, memory_bytes=memory_mb * 1024 * 1024)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped and too_much():
                raise MediaError(BACKEND_FAILED, f"{said} wrote more than {output_cap} bytes of output, so it was "
                                                 "stopped and its process tree killed") from None
            raise MediaError(TIME_LIMIT, f"{said} did not finish within {seconds:g} s, so it was stopped and its "
                                         "process tree killed") from None
        except component_process.ContainmentFailure:
            raise MediaError(BACKEND_FAILED, f"G6 could not confirm that {said}'s process tree ended") from None
        except (OSError, ProcessTreeError) as exc:
            raise MediaError(BACKEND_FAILED, f"{said} could not be run: {exc}"[:500]) from None
        out.seek(0)
        err.seek(0)
        return Ran(code, out.read(output_cap + 1), err.read(output_cap + 1), round(time.monotonic() - started, 3))



__all__ = ["DEFAULT_MEMORY_MB", "DEFAULT_OUTPUT_CAP", "DEFAULT_SECONDS", "MAX_SECONDS", "Ran", "environment", "find",
           "run"]
