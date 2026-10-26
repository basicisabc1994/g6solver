# SPDX-License-Identifier: Apache-2.0
"""``check_proof`` and ``lean_version``: the two ops of g6.lean that run Lean.

``check_proof`` takes the Lean source, the full name of the theorem that must be proved and, optionally, the statement
you expect it to have. In order, and stopping at the first that fails:

1. the limits and the name (a dotted Lean name of at most 200 characters, without ``_root_``);
2. the source screen (:mod:`g6.lean.screen`): every refused construct with its own code, line and column;
3. the expected statement's screen: it is written into G6's own ``example`` line, so it must be one term;
4. the declaration check: the source must declare a ``theorem`` by that full name (a file that declares none proves
   nothing, which is the earlier version's FM-001);
5. Lean, out of process (:mod:`g6.lean.runner`), on the file :func:`g6.lean.lean_output.build_file` builds;
6. the judgement on what Lean reported (:func:`g6.lean.lean_output.judge`): no error; the theorem unknown before the
   source and a ``theorem`` after it; no ``sorry``; only Lean's standard axioms; the expected statement matching as
   printed and accepted by Lean read on its own.

A pass is ``qualified-draft``: Lean's kernel accepted a proof of exactly this statement, using only the axioms listed,
and whether the statement says what you mean is for a person to inspect.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any

from g6.lean import runner
from g6.lean.envelope import (
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NOT_A_THEOREM,
    OUTPUT_UNRECOGNISED,
    RUN_FAILED,
    STATEMENT_REFUSED,
    THEOREM_MISSING,
    TIME_LIMIT_TOO_LONG,
    Refusal,
    labelled,
)
from g6.lean.lean_output import FILE_NAME, build_file, judge, parse_version_flag
from g6.lean.schema import LeanResult
from g6.lean.screen import declarations, screen, screen_statement

MAX_SOURCE_CHARS = 200_000
MAX_SOURCE_LINES = 20_000
MAX_STATEMENT_CHARS = 10_000
MAX_NAME_CHARS = 200
VERSION_TIME_LIMIT_S = 30.0
_NAME = re.compile(r"[^\W\d][\w'!?]*(?:\.[^\W\d][\w'!?]*)*")
_KEEP_TAIL = frozenset({RUN_FAILED, OUTPUT_UNRECOGNISED})


def _seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < float("inf"):
        raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {value!r}")
    if value > runner.MAX_TIME_LIMIT_S:
        raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {value:g} s is above the cap of {runner.MAX_TIME_LIMIT_S:g} s")
    return float(value)


def _whole(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise Refusal(INPUT_INVALID, f"{what} must be a whole number of at least {low}, not {value!r}")
    if value > high:
        raise Refusal(INPUT_TOO_LARGE, f"{what} {value} is above the limit of {high}")
    return value


def _source(source: Any) -> str:
    if not isinstance(source, str):
        raise Refusal(INPUT_INVALID, "source is the text of one Lean 4 file")
    if len(source) > MAX_SOURCE_CHARS:
        raise Refusal(INPUT_TOO_LARGE, f"the source is {len(source)} characters; the limit is {MAX_SOURCE_CHARS}")
    if source.count("\n") > MAX_SOURCE_LINES:
        raise Refusal(INPUT_TOO_LARGE, f"the source has more than {MAX_SOURCE_LINES} lines")
    return source


def _theorem(name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise Refusal(INPUT_INVALID, "theorem names the theorem that must be proved, such as two_add or A.foo")
    if len(name) > MAX_NAME_CHARS or not _NAME.fullmatch(name):
        raise Refusal(INPUT_INVALID, f"{name[:80]!r} is not a theorem name G6 reads: a Lean name, its parts joined "
                                     f"by dots, at most {MAX_NAME_CHARS} characters, no spaces or guillemets")
    if "_root_" in name.split("."):
        raise Refusal(INPUT_INVALID, "give the theorem's full name without _root_; G6 adds it itself")
    return name


def _statement(text: Any) -> str:
    if not isinstance(text, str):
        raise Refusal(INPUT_INVALID, "expected_statement is the statement as Lean prints it, such as 2 + 2 = 4")
    if len(text) > MAX_STATEMENT_CHARS:
        raise Refusal(INPUT_TOO_LARGE, f"the expected statement is {len(text)} characters; the limit is "
                                       f"{MAX_STATEMENT_CHARS}")
    return text.strip()


def _declared(source: str, name: str) -> None:
    """The source must declare a theorem with this full name; a file that declares none proves nothing (FM-001)."""
    found = [d for d in declarations(source) if d["name"] == name]
    if not found:
        raise Refusal(THEOREM_MISSING, f"the source declares no theorem named {name} (namespaces count: give the full "
                                       "name), so it proves nothing about it")
    if not any(d["kind"] == "theorem" for d in found):
        first = found[0]
        article = "an" if first["kind"][0] in "aeiou" else "a"
        raise Refusal(NOT_A_THEOREM, f"the source declares {name} as {article} {first['kind']} at line "
                                     f"{first['line']}, not a theorem: only a theorem is a proof of its statement")


def check_proof(source: str, theorem: str, *, expected_statement: str = "",
                time_limit: float = runner.DEFAULT_TIME_LIMIT_S, memory_mb: int = runner.DEFAULT_MEMORY_MB,
                max_output_bytes: int = runner.MAX_OUTPUT_BYTES, request_id: str = "", run_id: str = "") -> LeanResult:
    """Have Lean check that ``source`` proves the theorem named ``theorem`` (its full name), with no ``sorry`` and no
    axiom beyond ``propext``, ``Classical.choice`` and ``Quot.sound``; when ``expected_statement`` is given, the
    theorem's statement must be it. ``qualified-draft`` at best."""
    return _check_proof(source, theorem, expected_statement=expected_statement, time_limit=time_limit,
                        memory_mb=memory_mb, max_output_bytes=max_output_bytes, request_id=request_id, run_id=run_id)


def _check_proof(source: str, theorem: str, *, expected_statement: str = "",
                 time_limit: float = runner.DEFAULT_TIME_LIMIT_S, memory_mb: int = runner.DEFAULT_MEMORY_MB,
                 max_output_bytes: int = runner.MAX_OUTPUT_BYTES, request_id: str = "", run_id: str = "",
                 screened: bool = True) -> LeanResult:
    """:func:`check_proof`, with the source screen and the declaration check switched off when ``screened`` is false:
    for tests that show Lean's own report still blocks. The evidence says which."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {"screened": screened})
        seconds = _seconds(time_limit)
        memory = _whole(memory_mb, "memory_mb", runner.MIN_MEMORY_MB, runner.MAX_MEMORY_MB)
        cap = _whole(max_output_bytes, "max_output_bytes", 1, runner.OUTPUT_CAP_LIMIT)
        text = _source(source)
        expected = _statement(expected_statement)
        name = _theorem(theorem)
        state |= {"theorem": name, "expected_statement": expected}
        extra |= {"source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "source_chars": len(text),
                  "source_lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1)}
        if screened and (found := screen(text)):
            more = f"; {len(found) - 1} more listed in refusals" if len(found) > 1 else ""
            raise Refusal(found[0]["code"], f"G6 refuses the source before Lean runs: {found[0]['reason']}{more}",
                          refusals=found)
        if expected and (found := screen_statement(expected)):
            raise Refusal(STATEMENT_REFUSED, f"the expected statement cannot be written as one term: "
                                             f"{found[0]['reason']}", refusals=found)
        if screened:
            _declared(text, name)
        file_text, layout = build_file(text, name, expected, secrets.token_hex(16))
        lean = runner.find_lean()
        state |= {"executable": lean, "lean_ran": True}
        code, output = runner.run(lean, ["--json", FILE_NAME], file_text=file_text, seconds=seconds, memory_mb=memory,
                                  cap=cap, record=extra)
        verdict = judge(output, code, layout)
        state |= {k: verdict[k] for k in ("lean_version", "lean_target", "kind", "statement", "statement_explicit",
                                          "statement_matched", "axioms", "messages")}
        if verdict["code"] not in _KEEP_TAIL:
            extra.pop("output_tail", None)
        if verdict["code"]:
            raise Refusal(verdict["code"], verdict["reason"])
        return state

    return labelled("check_proof", work, request_id, run_id)


def lean_version(*, time_limit: float = VERSION_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> LeanResult:
    """Which Lean would check a proof: ``lean --version``, read. Nothing is checked."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        seconds = _seconds(time_limit)
        lean = runner.find_lean()
        state |= {"executable": lean, "lean_ran": True}
        code, output = runner.run(lean, ["--version"], file_text=None, seconds=seconds,
                                  memory_mb=runner.DEFAULT_MEMORY_MB, cap=64 * 1024, record=extra)
        read = parse_version_flag(output)
        if code != 0 or not read:
            raise Refusal(OUTPUT_UNRECOGNISED, f"lean --version exited with code {code} and printed "
                                               f"{output.strip()[:200]!r}, which is not a version G6 reads")
        extra.pop("output_tail", None)
        extra |= {"commit": read["commit"], "build": read["build"]}
        state |= {"lean_version": read["version"], "lean_target": read["target"]}
        return state

    return labelled("lean_version", work, request_id, run_id)


__all__ = ["MAX_NAME_CHARS", "MAX_SOURCE_CHARS", "MAX_SOURCE_LINES", "MAX_STATEMENT_CHARS", "check_proof",
           "lean_version"]
