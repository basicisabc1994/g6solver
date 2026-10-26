# SPDX-License-Identifier: Apache-2.0
"""SageMath as an optional external program: ``sage_version`` and ``sage_evaluate``.

SageMath is licensed under the GPL, so G6 never imports, bundles, vendors or installs it, and never reaches it through a
container image: you install it yourself, as sagemath.org describes, and G6 runs the ``sage`` program that
``G6_SAGE`` names (a path, or a name on PATH), or else ``sage`` on PATH. A missing SageMath is refused with
``G6_E_ALGEBRA_SAGE_MISSING`` and how to install it.

``sage_evaluate`` takes one expression in SageMath's syntax and the symbols it uses, and before SageMath is even looked
for:

1. G6's own parser reads it (:mod:`g6.algebra.language`) with :data:`~g6.algebra.language.SAGE_FUNCTIONS`, a fixed
   table of SageMath functions that compute on their arguments (``factor``, ``expand``, ``simplify``, ``diff``,
   ``integrate``, ``taylor``, ``solve``, ``is_prime``, ``gcd`` and the like). Strings, attributes, keyword arguments,
   brackets, statements and every other function are refused with their codes, the line and the column, and the size
   limits apply as for SymPy;
2. G6 renders its parse tree back as SageMath text, fully bracketed, with exact rationals for decimals and ``exp(1)``
   for ``E`` (:func:`to_sage`), so SageMath never reads a character of the caller's own text;
3. G6 writes a file (:func:`build_file`) that declares each symbol with ``var`` (and its domain), evaluates that one
   expression, and prints SageMath's version, the answer and its LaTeX between marks carrying a nonce the expression
   cannot know; SageMath runs it as ``sage g6_expression.sage`` in a fresh directory (:mod:`g6.algebra.runner`), under a
   time limit (120 seconds by default, at most 600), an output cap and a memory cap, its process tree killed when one
   is passed.

A SageMath answer is ``qualified-draft`` (``G6_E_ALGEBRA_SAGE_ANSWER``) and its card says G6 did not re-check it. Both
ops are sensitive: SageMath is a program that runs on your machine, as you. They run from Python, or over MCP only
when the person running the server lists them in ``G6_MCP_SENSITIVE``.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

from g6.algebra import language as L
from g6.algebra import runner
from g6.algebra.envelope import (
    CONSTRUCT_REFUSED,
    OUTPUT_TOO_LARGE,
    OUTPUT_UNRECOGNISED,
    SAGE_ENV,
    SAGE_FAILED,
    SAGE_MISSING,
    TIME_LIMIT,
    Refusal,
    labelled,
)
from g6.algebra.ops import seconds, whole
from g6.algebra.schema import AlgebraResult
from g6.core.outside import find_program

DEFAULT_TIME_LIMIT_S = 120.0
MAX_TIME_LIMIT_S = 600.0
VERSION_TIME_LIMIT_S = 60.0
DEFAULT_MEMORY_MB, MIN_MEMORY_MB, MAX_MEMORY_MB = 4096, 512, 32768
FILE_NAME = "g6_expression.sage"
MARK = "G6_SAGE_"
_VERSION = re.compile(r"SageMath version ([0-9][0-9A-Za-z.]*)")
_TAIL = 8000


def find_sage() -> str:
    """The ``sage`` program: ``G6_SAGE`` when it is set, else ``sage`` on PATH. Refused when there is none."""
    named = os.environ.get(SAGE_ENV, "").strip()
    if named:
        found = find_program(named) or (named if Path(named).is_file() else None)
        if not found:
            raise Refusal(SAGE_MISSING, f"{SAGE_ENV} names {named}, which is not a program here; G6 never installs "
                                        "SageMath")
        return found
    found = find_program("sage")
    if not found:
        raise Refusal(SAGE_MISSING, f"no sage on PATH and {SAGE_ENV} is not set; G6 never installs SageMath")
    return found


def screen(expression: Any, symbols: Any) -> tuple[L.Node, dict[str, tuple[str, ...]]]:
    """``expression`` read by G6's parser with SageMath's function table, and the symbols declared for it."""
    declared = L.declare(symbols)
    return L.parse(expression, declared, functions=L.SAGE_FUNCTIONS, infinity=True), declared


def to_sage(node: L.Node) -> str:
    """G6's parse tree as SageMath text: every operation bracketed, numbers exact, nothing of the caller's text."""
    if isinstance(node, L.Num):
        v = node.value
        return str(v.numerator) if v.denominator == 1 else f"({v.numerator}/{v.denominator})"
    if isinstance(node, L.Sym):
        return node.name
    if isinstance(node, L.Const):
        return {"pi": "pi", "E": "exp(1)", "I": "I", "oo": "oo"}[node.name]
    if isinstance(node, L.Neg):
        return f"(-{to_sage(node.operand)})"
    if isinstance(node, L.Call):
        return f"{node.name}({', '.join(to_sage(a) for a in node.args)})"
    if isinstance(node, L.BinOp):
        op = "**" if node.op == "^" else node.op
        return f"({to_sage(node.left)}{op}{to_sage(node.right)})"
    raise Refusal(CONSTRUCT_REFUSED, "an equation cannot be sent to SageMath; write left - right")


def _declaration(name: str, assumed: tuple[str, ...]) -> str:
    domain = "integer" if "integer" in assumed else "positive" if "positive" in assumed else \
        "real" if "real" in assumed else ""
    line = f"{name} = var('{name}', domain='{domain}')" if domain else f"{name} = var('{name}')"
    if domain == "integer" and "positive" in assumed:
        line += f"\nassume({name} > 0)"
    return line


def build_file(node: L.Node, declared: dict[str, tuple[str, ...]], nonce: str) -> str:
    """The file SageMath runs: the symbols declared, the one expression, and its answer printed between marks."""
    mark = f"{MARK}{nonce}_"
    lines = ["# Written by G6 from its own parse of your expression; SageMath runs nothing else from you."]
    lines += [_declaration(name, declared[name]) for name in sorted(declared)]
    lines += [f"_g6_value = ({to_sage(node)})",
              f"print('{mark}VERSION')", "print(version())",
              f"print('{mark}TEXT')", "print(str(_g6_value))",
              f"print('{mark}LATEX')", "print(latex(_g6_value))",
              f"print('{mark}END')"]
    return "\n".join(lines) + "\n"


def parse_version(text: str) -> str:
    found = _VERSION.search(text or "")
    return found.group(1) if found else ""


def read_output(text: str, nonce: str) -> dict[str, str] | None:
    """SageMath's version, answer and LaTeX from between G6's marks, or ``None`` when they are not all there."""
    mark = f"{MARK}{nonce}_"
    lines = (text or "").splitlines()
    where = {name: lines.index(mark + name) for name in ("VERSION", "TEXT", "LATEX", "END")
             if mark + name in lines}
    if len(where) < 4 or not where["VERSION"] < where["TEXT"] < where["LATEX"] < where["END"]:
        return None
    between = lambda a, b: "\n".join(lines[where[a] + 1:where[b]]).strip()  # noqa: E731
    return {"version": parse_version(between("VERSION", "TEXT")), "text": between("TEXT", "LATEX"),
            "latex": between("LATEX", "END")}


def _stopped(run: runner.Run, limit: float) -> None:
    if run.stopped == "time":
        raise Refusal(TIME_LIMIT, f"SageMath did not finish within {limit:g} s, so it was stopped and its process tree "
                                  "killed")
    if run.stopped == "output":
        raise Refusal(OUTPUT_TOO_LARGE, "SageMath's output passed its cap, so it was stopped and its process tree "
                                        "killed")


def sage_version(*, time_limit: float = VERSION_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> AlgebraResult:
    """Which SageMath would compute: ``sage --version``, read. Nothing is computed."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        state["engine"] = "SageMath"
        limit = seconds(time_limit, MAX_TIME_LIMIT_S)
        executable = find_sage()
        state |= {"executable": executable, "engine_ran": True}
        run = runner.run_sage(executable, ["--version"], seconds=limit, memory_mb=DEFAULT_MEMORY_MB, cap=64 * 1024,
                              record=extra)
        _stopped(run, limit)
        version = parse_version(run.output)
        if run.code != 0 or not version:
            extra["output_tail"] = run.output[-_TAIL:]
            raise Refusal(OUTPUT_UNRECOGNISED, f"sage --version exited with code {run.code} and printed "
                                               f"{run.output.strip()[:200]!r}, which is not a version G6 reads")
        state["engine_version"] = version
        return state

    return labelled("sage_version", work, request_id, run_id)


def sage_evaluate(expression: str, *, symbols: Any = None, time_limit: float = DEFAULT_TIME_LIMIT_S,
                  memory_mb: int = DEFAULT_MEMORY_MB, max_output_bytes: int = runner.MAX_OUTPUT_BYTES,
                  request_id: str = "", run_id: str = "") -> AlgebraResult:
    """One expression in SageMath's syntax, screened by G6 and evaluated by SageMath. Not re-checked by G6."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        state["engine"] = "SageMath"
        limit = seconds(time_limit, MAX_TIME_LIMIT_S)
        memory = whole(memory_mb, "memory_mb", MIN_MEMORY_MB, MAX_MEMORY_MB)
        cap = whole(max_output_bytes, "max_output_bytes", 1024, runner.OUTPUT_CAP_LIMIT)
        node, declared = screen(expression, symbols)
        sent = to_sage(node)
        state["input"] = {"op": "sage_evaluate", "expression": L.render(node), "sent_to_sage": sent,
                          "symbols": {name: list(a) for name, a in declared.items()}}
        executable = find_sage()
        state |= {"executable": executable, "engine_ran": True}
        nonce = secrets.token_hex(16)
        run = runner.run_sage(executable, [FILE_NAME], file_name=FILE_NAME,
                              file_text=build_file(node, declared, nonce), seconds=limit, memory_mb=memory, cap=cap,
                              record=extra)
        _stopped(run, limit)
        read = read_output(run.output, nonce)
        if run.code != 0 or read is None:
            extra["output_tail"] = run.output[-_TAIL:]
            raise Refusal(SAGE_FAILED, f"SageMath exited with code {run.code} without the answer G6 asked it to print; "
                                       "the end of its output is in the evidence")
        state |= {"engine_version": read["version"], "answer": read["text"], "latex": read["latex"]}
        return state

    return labelled("sage_evaluate", work, request_id, run_id)


__all__ = ["DEFAULT_MEMORY_MB", "DEFAULT_TIME_LIMIT_S", "FILE_NAME", "MAX_MEMORY_MB", "MAX_TIME_LIMIT_S",
           "MIN_MEMORY_MB", "VERSION_TIME_LIMIT_S", "build_file", "find_sage", "parse_version", "read_output",
           "sage_evaluate", "sage_version", "screen", "to_sage"]
