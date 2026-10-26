# SPDX-License-Identifier: Apache-2.0
"""Run a small generated function out of process, bounded (audit 025 §11, HD-010).

``check_source`` is the shared AST pre-filter (``g6.core.source_check``) plus a size bound. ``run_batch`` runs
``solve(x)`` for each input in one child started by ``g6.core.sandbox.run_script``:

- a fresh temporary directory and an environment with no secret in it;
- a wall-clock allowance, then a kill of the whole process tree;
- a memory cap (POSIX rlimit, a Windows Job Object), and on POSIX CPU and file-size limits;
- network connections to anywhere but this machine refused.

The parent checks the source before the child starts; inside the child the candidate runs with the small builtins
table and no import. Inputs and outputs are JSON: a value that is not JSON is an error, never a repr.

It is a resource bound and a pre-filter, not an operating-system sandbox: the child runs as the same user.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from g6.core import sandbox
from g6.core.source_check import SAFE_BUILTIN_NAMES, validate_source

MAX_SOURCE_CHARS = 50_000
# The child: reads the candidate and its inputs, runs solve(x) on each, writes one outcome per input.
_HARNESS = """import builtins, json
SAFE = {names!r}
src = open("candidate.py", encoding="utf-8").read()
values = json.load(open("inputs.json", encoding="utf-8"))
outcomes = []
namespace = {{"__builtins__": {{name: getattr(builtins, name) for name in SAFE}}}}
try:
    exec(compile(src, "candidate.py", "exec"), namespace)
    solve = namespace.get("solve")
    if not callable(solve):
        raise ValueError("the source defines no solve(x)")
except Exception as exc:
    outcomes = [{{"error": f"{{type(exc).__name__}}: {{exc}}"[:300]}} for _ in values]
else:
    for value in values:
        try:
            out = solve(value)
            json.dumps(out)
            outcomes.append({{"ok": out}})
        except Exception as exc:
            outcomes.append({{"error": f"{{type(exc).__name__}}: {{exc}}"[:300]}})
json.dump(outcomes, open("outcomes.json", "w", encoding="utf-8"))
"""


@dataclass(frozen=True)
class Run:
    """One input's outcome: ``ok`` with its ``value``, ``error``, ``timeout`` or ``refused`` with ``reason``."""

    status: str
    value: Any = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def check_source(source: str, *, max_chars: int = MAX_SOURCE_CHARS) -> list[str]:
    """Why ``source`` may not run; empty when it may."""
    if len(source) > max_chars:
        return [f"the source is longer than {max_chars} characters"]
    why = validate_source(source)
    return [why] if why else []


def run_batch(source: str, values: Sequence[Any], *, timeout_s: float = 10.0, memory_mb: int = 256) -> list[Run]:
    """``solve(value)`` for each of ``values`` in one bounded child; one ``Run`` per value, in order."""
    problems = check_source(source)
    if problems:
        return [Run("refused", reason=problems[0]) for _ in values]
    try:
        inputs = json.dumps(list(values))
    except (TypeError, ValueError) as exc:
        return [Run("refused", reason=f"the inputs are not JSON: {exc}") for _ in values]
    done = sandbox.run_script(_HARNESS.format(names=SAFE_BUILTIN_NAMES),
                              {"candidate.py": source, "inputs.json": inputs},
                              timeout_s=timeout_s, mem_mb=memory_mb, file_mb=8)
    if done.status == "timeout":
        return [Run("timeout", reason=done.stderr[:300]) for _ in values]
    try:
        outcomes = json.loads(done.outputs["outcomes.json"].decode("utf-8"))
    except (KeyError, ValueError):
        why = (done.stderr or done.stdout or f"the child ended with {done.status}").strip().splitlines()
        return [Run("error", reason=(why[-1] if why else done.status)[:300]) for _ in values]
    if not isinstance(outcomes, list) or len(outcomes) != len(values):
        return [Run("error", reason="the child's outcomes do not match its inputs") for _ in values]
    return [Run("ok", value=o["ok"]) if isinstance(o, dict) and "ok" in o
            else Run("error", reason=str(o.get("error", "no outcome") if isinstance(o, dict) else o)[:300])
            for o in outcomes]


def run_sandboxed(source: str, value: Any, *, timeout_s: float = 2.0, memory_mb: int = 256) -> Run:
    """``solve(value)`` in a bounded child."""
    return run_batch(source, [value], timeout_s=timeout_s, memory_mb=memory_mb)[0]


__all__ = ["MAX_SOURCE_CHARS", "Run", "check_source", "run_batch", "run_sandboxed"]
