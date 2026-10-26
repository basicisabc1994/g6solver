# SPDX-License-Identifier: Apache-2.0
"""The request G6 writes for its driver (``driver.pl``), and the reader of the records the driver prints.

The driver is a fixed file in this package, never built from caller text (see its header). G6 tells it what to do in
``request.json``: the mode (``check``, ``query`` or ``recheck``), the limits, the answers to re-check and whether the
first search finished, and a nonce drawn for the run. Each record the driver prints is one line, the nonce, a space and a JSON object; the caller's code never
sees the nonce, so a line it prints cannot pass for a record. :func:`read_output` reads only those lines and refuses
any output that does not hold one complete, well-formed run: a version first, ``end`` last, nothing unknown between.
"""
from __future__ import annotations

import json
from typing import Any

from g6.prolog.envelope import OUTPUT_UNRECOGNISED, RUN_FAILED, Refusal

_KINDS = frozenset({"version", "predicate", "problem", "unsafe", "answer", "goal_error", "stopped", "done", "recheck",
                    "driver_error", "end"})
_STAGES = frozenset({"program", "goal"})
_PROBLEMS = frozenset({"syntax", "directive", "clause", "not_allowed", "undefined"})
_STOPS = frozenset({"max_solutions", "inference_limit", "time_limit", "memory_limit"})
_VERDICTS = frozenset({"agreed", "disagreed", "error", "unfinished"})


def request(mode: str, nonce: str, *, max_solutions: int = 1, time_limit: float = 1.0, inference_limit: int = 1,
            answers: list[str] | None = None, exhausted: bool = False) -> str:
    """``request.json`` for one run of the driver."""
    return json.dumps({"mode": mode, "nonce": nonce, "max_solutions": max_solutions, "time_limit": time_limit,
                       "inference_limit": inference_limit, "answers": list(answers or []), "exhausted": exhausted})


def end_record(nonce: str) -> bytes:
    """The driver's last record for ``nonce``, as it prints it (``g6.prolog.runner`` watches for it)."""
    return f'{nonce} {{"kind":"end"}}'.encode()


def _unrecognised(why: str) -> Refusal:
    return Refusal(OUTPUT_UNRECOGNISED, f"the driver's output is not a run G6 reads: {why}")


def _field(record: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    value = record.get(key)
    if isinstance(value, bool) and kind is not bool or not isinstance(value, kind):
        raise _unrecognised(f"a {record.get('kind')} record has no {key} of the expected type")
    return value


def records(text: str, nonce: str) -> list[dict[str, Any]]:
    """The driver's records in ``text``: the lines that start with ``nonce`` and a space, each one JSON object."""
    mark = nonce + " "
    found = []
    for line in text.splitlines():
        if not line.startswith(mark):
            continue
        try:
            record = json.loads(line[len(mark):])
        except ValueError:
            raise _unrecognised("a marked line is not JSON") from None
        if not isinstance(record, dict) or record.get("kind") not in _KINDS:
            raise _unrecognised("a marked line is not a record the driver writes")
        found.append(record)
    return found


def read_output(text: str, nonce: str, exit_code: int) -> dict[str, Any]:
    """What one run of the driver reported, read from its output; refused unless it is one complete run."""
    found = records(text, nonce)
    kinds = [r["kind"] for r in found]
    failed = next((r for r in found if r["kind"] == "driver_error"), None)
    if failed is not None:
        raise Refusal(RUN_FAILED, f"G6's driver failed inside SWI-Prolog: {str(failed.get('message'))[:300]}")
    if exit_code != 0 or not found or kinds[0] != "version" or kinds[-1] != "end" or kinds.count("end") != 1:
        raise Refusal(RUN_FAILED, f"SWI-Prolog exited with code {exit_code} before G6's driver finished its run")
    if kinds.count("version") != 1 or kinds.count("unsafe") > 1 or kinds.count("goal_error") > 1 \
            or kinds.count("done") + kinds.count("stopped") > 1:
        raise _unrecognised("a record that appears once in a run appears more than once")
    read: dict[str, Any] = {"version": _field(found[0], "version", str), "predicates": [], "problems": [],
                            "unsafe": None, "goal_error": None, "answers": [], "stopped": "", "exhausted": None,
                            "rechecks": []}
    for record in found[1:-1]:
        kind = record["kind"]
        if kind == "predicate":
            read["predicates"].append({"name": _field(record, "name", str), "arity": _field(record, "arity", int),
                                       "clauses": _field(record, "clauses", int), "line": _field(record, "line", int)})
        elif kind == "problem":
            stage, problem = _field(record, "stage", str), _field(record, "problem", str)
            if stage not in _STAGES or problem not in _PROBLEMS:
                raise _unrecognised("a problem record names an unknown stage or problem")
            read["problems"].append({"stage": stage, "problem": problem, "line": _field(record, "line", int),
                                     "column": _field(record, "column", int),
                                     "message": _field(record, "message", str)})
        elif kind == "unsafe":
            read["unsafe"] = {"message": _field(record, "message", str), "culprit": _field(record, "culprit", str),
                              "error": _field(record, "error", str)}
        elif kind == "goal_error":
            read["goal_error"] = _field(record, "message", str)
        elif kind == "answer":
            pairs = _field(record, "bindings", list)
            if not all(isinstance(p, list) and len(p) == 2 and all(isinstance(s, str) for s in p) for p in pairs):
                raise _unrecognised("an answer's bindings are not name and value pairs")
            names = [p[0] for p in pairs]
            if len(set(names)) != len(names):
                raise _unrecognised("an answer binds a variable twice")
            read["answers"].append({"bindings": dict(pairs), "text": _field(record, "text", str)})
        elif kind == "stopped":
            by = _field(record, "by", str)
            if by not in _STOPS:
                raise _unrecognised("a stopped record names an unknown limit")
            read["stopped"] = by
            read["exhausted"] = False
        elif kind == "done":
            if _field(record, "exhausted", bool) is not True:
                raise _unrecognised("a done record does not say the search finished")
            read["exhausted"] = True
        elif kind == "recheck":
            verdict = _field(record, "verdict", str)
            if verdict not in _VERDICTS:
                raise _unrecognised("a re-check record names an unknown verdict")
            read["rechecks"].append({"index": _field(record, "index", int), "verdict": verdict,
                                     "why": _field(record, "why", str)})
        else:
            raise _unrecognised(f"a {kind} record appears where it cannot")
    return read


__all__ = ["read_output", "records", "request"]
