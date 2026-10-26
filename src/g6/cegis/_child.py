# SPDX-License-Identifier: Apache-2.0
"""The child process ``SubprocessRunner`` starts (``python -I -m g6.cegis._child``): it reads one JSON request on stdin
and writes one JSON reply on stdout. It is the only place candidate code runs.

Before any candidate runs it lowers its own limits where the OS allows (POSIX: address space, CPU seconds, no file
writes); the parent also bounds wall-clock time and kills the whole process tree. Candidate source passes an AST
pre-filter (no imports, no dangerous builtins, no dunder attributes) and runs with a small builtins table. This is a
pre-filter and resource bound, not an operating-system sandbox: the child runs as the same user.

Requests: ``{"op": "evaluate", "sketch", "controls", "inputs"}`` -> ``{"outputs": [[outcome, ...], ...]}``;
``{"op": "search", "sketch", "examples", "max_exhaustive"}`` -> ``{"control", "method", "complete", "tried"}``. An
outcome is ``{"ok": value}`` or ``{"error": message}``; values are JSON.
"""
from __future__ import annotations

import builtins
import itertools
import json
import random
import sys
from typing import Any

from g6.core.source_check import SAFE_BUILTIN_NAMES, validate_source  # the one pre-filter (audit 025 HD-010)

HOLE_VAR_TPL = "_CEGIS_H{i}_"
_SAFE_BUILTINS = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}
_SEED = 42
#: The largest file the child may write: its own reply goes to a file the parent reads (no candidate can open one).
_MAX_FILE_BYTES = 32 * 1024 * 1024


def _limit(mem_mb: int, cpu_s: int) -> None:
    try:
        import resource
    except ImportError:  # Windows: the parent's wall clock and tree kill are the bound
        return
    for name, value in (("RLIMIT_AS", mem_mb * 1024 * 1024), ("RLIMIT_CPU", cpu_s),
                        ("RLIMIT_FSIZE", _MAX_FILE_BYTES)):
        kind = getattr(resource, name, None)
        if kind is not None:
            try:
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass


def _instantiate(sketch: dict, control: dict[int, Any]) -> str:
    src = sketch["source"]
    for hole in sketch["holes"]:
        src = src.replace(HOLE_VAR_TPL.format(i=hole["id"]), repr(control[hole["id"]]))
    return src


def _compiled(sketch: dict, control: dict[int, Any]):
    src = _instantiate(sketch, control)
    why = validate_source(src)
    if why:
        raise ValueError(f"the candidate is refused: {why}")
    namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    exec(compile(src, "<candidate>", "exec"), namespace)  # noqa: S102 - the one place candidate code runs
    fn = namespace.get(sketch["fn_name"])
    if not callable(fn):
        raise ValueError(f"the candidate defines no function {sketch['fn_name']!r}")
    return fn


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def same(a: Any, b: Any) -> bool:
    """Equality of JSON values that does not let ``True`` equal ``1`` (Python's ``==`` does)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def _outcome(fn, inputs: dict) -> dict:
    try:
        return {"ok": _json(fn(**inputs))}
    except Exception as exc:  # noqa: BLE001 - a candidate's failure is its outcome
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}


def _controls(sketch: dict, raw: list[dict]) -> list[dict[int, Any]]:
    return [{int(k): v for k, v in c.items()} for c in raw]


def _evaluate(request: dict) -> dict:
    sketch, out = request["sketch"], []
    for control in _controls(sketch, request["controls"]):
        try:
            fn = _compiled(sketch, control)
        except Exception as exc:  # noqa: BLE001
            out.append([{"error": f"{type(exc).__name__}: {exc}"[:300]}] * len(request["inputs"]))
            continue
        out.append([_outcome(fn, inputs) for inputs in request["inputs"]])
    return {"outputs": out}


def _values(hole: dict) -> list[Any]:
    return list(range(hole["lo"], hole["hi"] + 1)) if hole["kind"] == "int" else list(hole["choices"])


def _fits(sketch: dict, control: dict[int, Any], examples: list) -> bool:
    try:
        fn = _compiled(sketch, control)
    except Exception:  # noqa: BLE001
        return False
    for inputs, expected in examples:
        got = _outcome(fn, inputs)
        if "ok" not in got or not same(got["ok"], expected):
            return False
    return True


def _error(sketch: dict, control: dict[int, Any], examples: list) -> float:
    try:
        fn = _compiled(sketch, control)
    except Exception:  # noqa: BLE001
        return float("inf")
    total = 0.0
    for inputs, expected in examples:
        got = _outcome(fn, inputs)
        if "ok" in got and same(got["ok"], expected):
            continue
        actual = got.get("ok")
        if isinstance(actual, int | float) and isinstance(expected, int | float) and not isinstance(actual, bool):
            total += float(actual - expected) ** 2
        else:
            total += 1e12
    return total


def _search(request: dict) -> dict:
    sketch, examples = request["sketch"], [(e[0], e[1]) for e in request["examples"]]
    holes = sketch["holes"]
    pools = [_values(h) for h in holes]
    total = 1
    for pool in pools:
        total *= len(pool)
    if total <= int(request["max_exhaustive"]):
        tried = 0
        for combo in itertools.product(*pools):
            tried += 1
            control = {h["id"]: v for h, v in zip(holes, combo, strict=True)}
            if _fits(sketch, control, examples):
                return {"control": control, "method": "exhaustive", "complete": False, "tried": tried}
        # ``complete``: every control was tried and none fits, so the examples are unsatisfiable in this sketch
        return {"control": None, "method": "exhaustive", "complete": True, "tried": tried}
    # Hill-climbing on squared error (the archive's adaptive search); not finding one proves nothing
    rng, tried = random.Random(_SEED), 0
    for _restart in range(20):
        control = {h["id"]: rng.choice(pool) for h, pool in zip(holes, pools, strict=True)}
        err = _error(sketch, control, examples)
        for _step in range(1000):
            tried += 1
            if err == 0:
                return {"control": control, "method": "adaptive", "complete": False, "tried": tried}
            index = rng.randrange(len(holes))
            hole, pool = holes[index], pools[index]
            if hole["kind"] == "int" and rng.random() < 0.8:
                value = max(hole["lo"], min(hole["hi"], control[hole["id"]] + rng.choice((-2, -1, 1, 2))))
            else:
                value = rng.choice(pool)
            candidate = {**control, hole["id"]: value}
            new = _error(sketch, candidate, examples)
            if new <= err:
                control, err = candidate, new
        if err == 0:
            return {"control": control, "method": "adaptive", "complete": False, "tried": tried}
    return {"control": None, "method": "adaptive", "complete": False, "tried": tried}


def main() -> int:
    request = json.loads(sys.stdin.read())
    _limit(int(request.get("mem_mb", 512)), int(request.get("cpu_s", 30)))
    op = request.get("op")
    reply = _evaluate(request) if op == "evaluate" else _search(request) if op == "search" else {
        "refused": f"unknown op {op!r}"}
    sys.stdout.write(json.dumps(reply, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
