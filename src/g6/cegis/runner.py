# SPDX-License-Identifier: Apache-2.0
"""Running candidate code out of process (audit 027 CG-002, CG-005).

``SubprocessRunner`` starts ``g6.cegis._child`` with ``python -I`` through :mod:`g6.core.component_process` (a wall-clock
allowance and a kill of the whole process tree), in an empty run folder G6 made and with an environment that carries no
secret. The child lowers its own memory, CPU and file-size limits on POSIX before any candidate runs (macOS refuses the
address-space limit, so there its memory is not capped); on Windows the Job Object caps its memory, beside the wall
clock and the tree kill. Like every run of untrusted code it goes through :mod:`g6.core.confine`: where a backend can
confine it (bubblewrap on Linux, an AppContainer on Windows), it reads only the interpreter's folders, writes only its
run folder and reaches no network; elsewhere it is a resource bound with an AST pre-filter, and the child runs as the
same user. Each call's isolation is recorded (``confine.recording``), and a run that isolation is required for and no
backend can give is refused before anything starts (:class:`RunnerRefused`).
"""
from __future__ import annotations

import json
import math
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import g6
from g6.cegis.sketch import Control, Sketch
from g6.core import component_process, confine

#: Environment variables the child may see (the interpreter needs these on Windows); nothing else is passed.
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
#: The child's program. ``-I`` leaves out ``PYTHONPATH``, where a source checkout puts G6, so it is told where G6 is, after
#: everything else on its path; ``confine.interpreter_paths`` already lets it read that folder.
_CHILD = "import sys; sys.path.append({folder!r}); from g6.cegis._child import main; sys.exit(main())"


class RunnerError(RuntimeError):
    """The child could not give an answer (it was refused, crashed, or replied with something unreadable)."""


class RunnerTimeout(RunnerError):
    """The child's wall-clock allowance ran out; it and every process it started were killed."""


class RunnerRefused(RunnerError):
    """No candidate ran: isolation was required and no operating-system backend could confine the child. The message
    is ``confine``'s refusal (``G6_E_ISOLATION_UNAVAILABLE`` first)."""


@dataclass(frozen=True)
class SearchResult:
    """A control that fits every example (or None), how it was searched, and whether the search was exhaustive."""

    control: Control | None
    method: str
    complete: bool
    tried: int


class Runner(Protocol):
    def evaluate(self, sketch: Sketch, controls: Sequence[Control], inputs: Sequence[Mapping[str, Any]]
                 ) -> list[list[dict]]: ...

    def search(self, sketch: Sketch, examples: Sequence[tuple[Mapping[str, Any], Any]], *,
               max_exhaustive: int) -> SearchResult: ...


@dataclass(frozen=True)
class SubprocessRunner:
    """Each call is one child process, bounded by ``timeout_s`` of wall clock and ``mem_mb`` of memory (none on macOS)."""

    timeout_s: float = 30.0
    mem_mb: int = 512

    def evaluate(self, sketch: Sketch, controls: Sequence[Control], inputs: Sequence[Mapping[str, Any]]
                 ) -> list[list[dict]]:
        """For each control, each input's outcome: ``{"ok": value}`` or ``{"error": message}``."""
        reply = self._call({"op": "evaluate", "sketch": sketch.as_wire(),
                            "controls": [sketch.check(c) for c in controls], "inputs": [dict(i) for i in inputs]})
        outputs = reply.get("outputs")
        if not (isinstance(outputs, list) and len(outputs) == len(controls)
                and all(isinstance(row, list) and len(row) == len(inputs) for row in outputs)):
            raise RunnerError("the child's evaluation reply has the wrong shape")
        return outputs

    def search(self, sketch: Sketch, examples: Sequence[tuple[Mapping[str, Any], Any]], *,
               max_exhaustive: int = 50_000) -> SearchResult:
        """A control that fits every example: exhaustive when the space is at most ``max_exhaustive`` controls, else
        a seeded hill-climb (whose failure proves nothing)."""
        reply = self._call({"op": "search", "sketch": sketch.as_wire(), "max_exhaustive": int(max_exhaustive),
                            "examples": [[dict(i), out] for i, out in examples]})
        raw = reply.get("control")
        control = sketch.check({int(k): v for k, v in raw.items()}) if isinstance(raw, dict) else None
        return SearchResult(control, str(reply.get("method", "")), bool(reply.get("complete")), int(reply.get("tried", 0)))

    def _call(self, request: dict) -> dict:
        # the CPU limit sits a second past the wall clock, so the wall clock (a clean kill) normally fires first
        request = {**request, "mem_mb": self.mem_mb, "cpu_s": max(1, math.ceil(self.timeout_s)) + 1}
        env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        python = sys.executable
        argv = [python, "-I", "-c", _CHILD.format(folder=str(Path(g6.__file__).resolve().parents[1]))]
        with confine.run_folder(prefix="g6_cegis_") as cwd:
            confined = confine.plan(confine.Confinement(read_write=(cwd,), read_only=confine.interpreter_paths(python)),
                                    python)
            said = confine.refused(confined)
            if said:
                raise RunnerRefused(said)
            try:
                with component_process.window(self.timeout_s):
                    done = component_process.run_contained(argv, env=env, cwd=cwd,
                                                           input_bytes=json.dumps(request).encode("utf-8"),
                                                           memory_bytes=self.mem_mb * 1024 * 1024, confined=confined)
            except component_process.ExecutionInterrupted as exc:
                raise RunnerTimeout(f"the candidate run took longer than {self.timeout_s:g}s and was killed") from exc
            except (OSError, ConnectionResetError) as exc:
                raise RunnerError(f"the candidate run could not be completed: {exc}") from exc
            finally:
                confined.release()
        xcpu = getattr(signal, "SIGXCPU", None)  # POSIX only
        if xcpu is not None and done.returncode == -xcpu:
            raise RunnerTimeout(f"the candidate run used up its {request['cpu_s']}s of CPU and was killed")
        if done.returncode != 0:
            tail = done.stderr.decode("utf-8", "replace")[-400:]
            raise RunnerError(f"the candidate run exited with {done.returncode}: {tail}")
        try:
            reply = json.loads(done.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError(f"the candidate run's reply is not JSON: {exc}") from exc
        if not isinstance(reply, dict) or "refused" in reply:
            raise RunnerError(f"the candidate run refused the request: {reply}")
        return reply


__all__ = ["Runner", "RunnerError", "RunnerRefused", "RunnerTimeout", "SearchResult", "SubprocessRunner"]
