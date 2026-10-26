# SPDX-License-Identifier: Apache-2.0
"""G6's own SymPy worker: ``python -I -m g6.algebra.worker``, started by :mod:`g6.algebra.runner` so that a time limit,
a memory cap and an output cap hold however long SymPy takes.

It is not a place where caller code runs. It reads one JSON request on standard input (the op, its parameters exactly
as the calling process checked them, a seed and its limits), reads the parameters again through G6's own parser
(:mod:`g6.algebra.inputs`), builds SymPy objects from the parse trees through SymPy's constructors
(:mod:`g6.algebra.build`), and writes JSON lines on standard output as it goes, each flushed at once:

* ``{"stage": "start", "sympy_version", "mpmath_version"}``;
* ``{"stage": "answer", ...}``, SymPy's answer, before any re-check runs;
* ``{"stage": "partial", "recheck"}``, a re-check whose quick part is done while a slower part runs;
* ``{"stage": "recheck", "recheck"}``, the re-check's verdict;
* ``{"stage": "refusal", "code", "reason"}`` or ``{"stage": "error", "reason"}`` instead of an answer;
* ``{"stage": "done"}``.

So when the time limit stops it during a re-check, the calling process still has the answer, and reports it as not
re-checked. On POSIX it lowers its own address space, CPU time and file-size limits before importing SymPy; on Windows
the calling process's Job Object caps its memory.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from g6.algebra.envelope import CAS_UNAVAILABLE, INSTALL_CAS, Refusal

Emit = Callable[[dict[str, Any]], None]


def _limit(memory_mb: Any, cpu_s: Any, file_bytes: Any) -> None:
    try:
        import resource
    except ImportError:  # Windows: the Job Object caps memory, the parent's clock bounds time
        return
    for name, value in (("RLIMIT_AS", memory_mb and int(memory_mb) * 1024 * 1024), ("RLIMIT_CPU", cpu_s),
                        ("RLIMIT_FSIZE", file_bytes)):
        kind = getattr(resource, name, None)
        if kind is not None and isinstance(value, int) and value > 0:
            try:
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass


def _safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def handle(request: dict[str, Any], emit: Emit) -> None:
    """Answer one request, reporting each stage through ``emit``. Never raises."""
    try:
        import mpmath
        import sympy
    except ImportError:
        emit({"stage": "refusal", "code": CAS_UNAVAILABLE, "reason": f"SymPy is not installed where G6's worker runs: "
                                                                     f"{INSTALL_CAS}"})
        emit({"stage": "done"})
        return
    emit({"stage": "start", "sympy_version": sympy.__version__, "mpmath_version": mpmath.__version__})
    try:
        from g6.algebra import checks, engine, inputs

        problem = inputs.read(str(request.get("op")), request.get("params") or {})
        context = checks.Context.make(problem.declared, int(request.get("seed") or 0),
                                      progress=lambda found: emit({"stage": "partial", "recheck": found.as_dict()}))
        answer, recheck = engine.compute(problem, context)
        emit({"stage": "answer", **_safe(answer)})
    except Refusal as refusal:
        emit({"stage": "refusal", "code": refusal.code, "reason": refusal.reason, **_safe(refusal.keep)})
        emit({"stage": "done"})
        return
    except (RecursionError, MemoryError) as exc:
        emit({"stage": "error", "reason": f"SymPy ran out of {'memory' if isinstance(exc, MemoryError) else 'stack'} "
                                          "before answering"})
        emit({"stage": "done"})
        return
    except Exception as exc:  # noqa: BLE001 -- reported with its type; the parent labels it
        emit({"stage": "error", "reason": f"SymPy failed before answering: {type(exc).__name__}: {exc}"[:500]})
        emit({"stage": "done"})
        return
    try:
        found = recheck()
        emit({"stage": "recheck", "recheck": _safe(found.as_dict())})
    except Exception as exc:  # noqa: BLE001 -- a re-check that fails decides nothing
        emit({"stage": "recheck", "recheck": {"status": "undecided", "methods": [], "failures": [], "points": 0,
                                              "tolerance": None, "details": {},
                                              "why": f"G6's re-check stopped with an error: {type(exc).__name__}: "
                                                     f"{exc}"[:400]}})
    emit({"stage": "done"})


def _emit(line: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()


def main() -> None:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _emit({"stage": "error", "reason": f"the request is not JSON: {exc}"})
        return
    if not isinstance(request, dict):
        _emit({"stage": "error", "reason": "the request is not a JSON object"})
        return
    _limit(request.get("mem_mb"), request.get("cpu_s"), request.get("file_bytes"))
    handle(request, _emit)


if __name__ == "__main__":
    main()


__all__ = ["handle", "main"]
