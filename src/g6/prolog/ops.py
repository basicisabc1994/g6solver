# SPDX-License-Identifier: Apache-2.0
"""``query``, ``prove``, ``check_program`` and ``swipl_version``: the ops of g6.prolog, each of which runs SWI-Prolog.

``query`` and ``prove`` take a program and a goal. In order, and stopping at the first that fails:

1. the limits and the sizes (the program at most 200,000 characters and 20,000 lines, the goal at most 10,000
   characters, ``max_solutions`` at most 10,000, ``time_limit`` at most 600 seconds);
2. SWI-Prolog, out of process (:mod:`g6.prolog.runner`), on G6's fixed driver (``driver.pl``): it reads the program
   clause by clause without consulting it and the goal as one term, and accepts only the restricted syntax (no
   directive; a program defines only its own predicates; every goal calls the program's own predicates or the
   allow-list, never a variable or ``Module:Goal``), each refusal with the predicate it names and its line and column;
   SWI-Prolog's sandbox library checks the goal as well, an extra layer; the goal runs under an inference limit, a time
   limit and the stack limit, collecting at most ``max_solutions`` answers (``prove``: one);
3. the re-check, in a second, fresh SWI-Prolog process under the same restricted syntax and limits: each answer, read
   back and put into the goal, must hold again, run once, with its free variables left free; and when the search
   finished (``exhausted``, or no answer at all), the search run again with a cap one higher must give the same
   answers in the same order and no more. A re-check that disagrees is ``G6_E_PROLOG_CERTIFICATE_REFUTED``.

None of this is a security boundary: SWI-Prolog runs as you, so run programs you do not trust inside your own sandbox,
such as a container or a virtual machine.

``qualified-draft`` is the best any op reaches: the answers follow from the program as SWI-Prolog runs it, and whether
the program says what you mean is for a person to inspect. A goal that fails is not derivable from this program, which,
under Prolog's closed world, is not the same as false.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any

from g6.core import outside
from g6.prolog import driver, runner
from g6.prolog.envelope import (
    CERTIFICATE_REFUTED,
    CLAUSE_REFUSED,
    DIRECTIVE_REFUSED,
    GOAL_ERROR,
    GOAL_NOT_ALLOWED,
    INFERENCE_LIMIT,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    MEMORY_LIMIT,
    OUTPUT_UNRECOGNISED,
    RECHECK_UNFINISHED,
    SWIPL,
    SWIPL_MISSING,
    SYNTAX_ERROR,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    UNDEFINED_PREDICATE,
    UNSAFE_GOAL,
    Refusal,
    labelled,
)
from g6.prolog.schema import PrologResult

MAX_PROGRAM_CHARS = 200_000
MAX_PROGRAM_LINES = 20_000
MAX_GOAL_CHARS = 10_000
DEFAULT_MAX_SOLUTIONS, MAX_SOLUTIONS = 100, 10_000
DEFAULT_INFERENCE_LIMIT, MAX_INFERENCE_LIMIT = 10_000_000, 10_000_000_000
VERSION_TIME_LIMIT_S = 30.0
_PROBLEM_CODES = {"syntax": SYNTAX_ERROR, "directive": DIRECTIVE_REFUSED, "clause": CLAUSE_REFUSED,
                  "not_allowed": GOAL_NOT_ALLOWED, "undefined": UNDEFINED_PREDICATE}
#: What stopped a search before it finished, and its code.
_STOP_CODES = {"inference_limit": (INFERENCE_LIMIT, "the inference limit"), "time_limit": (TIME_LIMIT, "the time limit"),
               "memory_limit": (MEMORY_LIMIT, "the stack limit")}
_VERSION = re.compile(r"SWI-Prolog version (\S+)(?: for (\S+))?")


# --- the limits ----------------------------------------------------------------------------------------------------

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


def _program(program: Any) -> str:
    if not isinstance(program, str):
        raise Refusal(INPUT_INVALID, "program is the text of a Prolog program: facts and rules, each ending in a full "
                                     "stop")
    if len(program) > MAX_PROGRAM_CHARS:
        raise Refusal(INPUT_TOO_LARGE, f"the program is {len(program)} characters; the limit is {MAX_PROGRAM_CHARS}")
    if program.count("\n") > MAX_PROGRAM_LINES:
        raise Refusal(INPUT_TOO_LARGE, f"the program has more than {MAX_PROGRAM_LINES} lines")
    return program


def _goal(goal: Any) -> str:
    if not isinstance(goal, str) or not goal.strip():
        raise Refusal(INPUT_INVALID, "goal is one Prolog goal, such as ancestor(tom, X), with or without its full "
                                     "stop")
    if len(goal) > MAX_GOAL_CHARS:
        raise Refusal(INPUT_TOO_LARGE, f"the goal is {len(goal)} characters; the limit is {MAX_GOAL_CHARS}")
    return goal


def _limits(time_limit: Any, inference_limit: Any, memory_mb: Any, max_output_bytes: Any) -> dict[str, Any]:
    return {"seconds": _seconds(time_limit),
            "inferences": _whole(inference_limit, "inference_limit", 1, MAX_INFERENCE_LIMIT),
            "memory_mb": _whole(memory_mb, "memory_mb", runner.MIN_MEMORY_MB, runner.MAX_MEMORY_MB),
            "cap": _whole(max_output_bytes, "max_output_bytes", 1, runner.OUTPUT_CAP_LIMIT)}


def _describe_program(text: str) -> dict[str, Any]:
    return {"program_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "program_chars": len(text),
            "program_lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1)}


# --- one run of the driver -----------------------------------------------------------------------------------------

def _run_driver(swipl: str, mode: str, program: str, goal: str | None, limits: dict[str, Any],
                record: dict[str, Any], *, max_solutions: int = 1, answers: list[str] | None = None,
                exhausted: bool = False) -> dict[str, Any]:
    """One fresh SWI-Prolog process on G6's driver, its records read; a refusal keeps the end of its output."""
    nonce = secrets.token_hex(16)
    files = {"program.pl": program,
             "request.json": driver.request(mode, nonce, max_solutions=max_solutions, time_limit=limits["seconds"],
                                            inference_limit=limits["inferences"], answers=answers,
                                            exhausted=exhausted)}
    if goal is not None:
        files["goal.pl"] = goal
    code, output = runner.run(swipl, runner.driver_arguments(limits["memory_mb"]), files=files,
                              seconds=limits["seconds"] + runner.STARTUP_ALLOWANCE_S, memory_mb=limits["memory_mb"],
                              cap=limits["cap"], record=record, end=driver.end_record(nonce))
    read = driver.read_output(output, nonce, code)
    record.pop("output_tail", None)
    return read


def _refuse_problems(read: dict[str, Any]) -> None:
    problems = read["problems"]
    if not problems:
        return
    first = problems[0]
    where = "in the goal" if first["stage"] == "goal" else f"at line {first['line']}, column {first['column']}"
    more = f"; {len(problems) - 1} more listed in problems" if len(problems) > 1 else ""
    raise Refusal(_PROBLEM_CODES[first["problem"]], f"{first['message']} ({where}){more}", problems=problems)


def _refuse_unsafe(read: dict[str, Any]) -> None:
    unsafe = read["unsafe"]
    if unsafe is None:
        return
    if unsafe.get("error") == "existence_error":
        raise Refusal(UNDEFINED_PREDICATE, f"SWI-Prolog's sandbox library found a call to a predicate nothing defines, "
                                           f"so the goal never ran: {unsafe['message']}")
    raise Refusal(UNSAFE_GOAL, f"SWI-Prolog's sandbox library refused the goal, so it never ran: {unsafe['message']} "
                               f"(it reported {unsafe['culprit']})")


def _search(program: str, goal: str, max_solutions: int, limits: dict[str, Any], state: dict[str, Any],
            extra: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """The first run: the program and the goal read and checked against the restricted syntax, the goal run; the
    answers, or the refusal."""
    swipl = runner.find_swipl()
    state |= {"executable": swipl, "swipl_ran": True}
    read = _run_driver(swipl, "query", program, goal, limits, extra.setdefault("search", {}),
                       max_solutions=max_solutions)
    state |= {"swipl_version": read["version"], "predicates": read["predicates"]}
    _refuse_problems(read)
    _refuse_unsafe(read)
    answers = read["answers"]
    state["answers"] = answers
    if read["goal_error"] is not None:
        raise Refusal(GOAL_ERROR, f"the goal raised an error in SWI-Prolog after {len(answers)} answers: "
                                  f"{read['goal_error']}")
    state["stopped_by"] = read["stopped"]
    if read["stopped"] in _STOP_CODES:
        state["exhausted"] = False
        code, limit = _STOP_CODES[read["stopped"]]
        if read["stopped"] == "memory_limit":
            limit += f" of {runner.stack_limit_mb(limits['memory_mb'])} MB (half of memory_mb)"
        raise Refusal(code, f"{limit} stopped the search after {len(answers)} answers, so it did not finish; the "
                            "answers found are kept and were not re-checked")
    if read["exhausted"] is None:
        raise Refusal(OUTPUT_UNRECOGNISED, "the driver did not say how the search ended")
    state["exhausted"] = read["exhausted"]
    return swipl, answers


def _recheck(swipl: str, program: str, goal: str, answers: list[dict[str, Any]], exhausted: bool,
             limits: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """The second run, in a fresh process: every answer must hold again as given and, when the search finished, the
    search run again must give the same answers and no more (or, with no answer, the failure must recur)."""
    texts = [a["text"] for a in answers]
    read = _run_driver(swipl, "recheck", program, goal, limits, extra.setdefault("recheck_run", {}), answers=texts,
                       exhausted=exhausted)
    each = "each answer, put back into the goal and run once, must hold with its free variables left free"
    end = "the search, run again with a cap one higher, must give the same answers in the same order and no more"
    summary: dict[str, Any] = {"process": "a second, fresh SWI-Prolog process, under the same restricted syntax and "
                                          "limits",
                               "of": (f"{each}; and {end}" if exhausted else each) if texts else
                                     "the failure: the goal run again must find no answer",
                               "checked": 0, "verdicts": [], "passed": False}
    if read["problems"] or read["unsafe"] is not None:
        raise Refusal(CERTIFICATE_REFUTED, "the second SWI-Prolog process did not read the program and the goal as the "
                                           "first did", recheck=summary)
    if read["stopped"] == "time_limit":
        raise Refusal(RECHECK_UNFINISHED, "the time limit stopped the re-check before it answered for every answer",
                      recheck=summary)
    verdicts = read["rechecks"]
    if [v["index"] for v in verdicts] != list(range(len(texts))) + ([-1] if exhausted else []):
        raise Refusal(OUTPUT_UNRECOGNISED, "the re-check does not answer for each answer once, in order, and for the "
                                           "search's end when it finished", recheck=summary)
    summary |= {"checked": len(verdicts), "verdicts": verdicts}
    wrong = [v for v in verdicts if v["verdict"] in ("disagreed", "error")]
    if wrong:
        which = (("the claim that the search finished" if texts else "the failure") if wrong[0]["index"] < 0
                 else f"answer {wrong[0]['index'] + 1}")
        bindings = "" if wrong[0]["index"] < 0 else f" ({texts[wrong[0]['index']]})"
        raise Refusal(CERTIFICATE_REFUTED, f"the re-check disagreed with {which}{bindings}: {wrong[0]['why']}; "
                                           f"{len(wrong)} of {len(verdicts)} disagreed", recheck=summary)
    if any(v["verdict"] == "unfinished" for v in verdicts):
        raise Refusal(RECHECK_UNFINISHED, "the inference limit stopped the re-check before it agreed",
                      recheck=summary)
    return summary | {"passed": True}


# --- the ops -------------------------------------------------------------------------------------------------------

def query(program: str, goal: str, *, max_solutions: int = DEFAULT_MAX_SOLUTIONS,
          time_limit: float = runner.DEFAULT_TIME_LIMIT_S, inference_limit: int = DEFAULT_INFERENCE_LIMIT,
          memory_mb: int = runner.DEFAULT_MEMORY_MB, max_output_bytes: int = runner.MAX_OUTPUT_BYTES,
          request_id: str = "", run_id: str = "") -> PrologResult:
    """The answers to ``goal`` from ``program``, at most ``max_solutions`` of them, each re-checked in a second process;
    ``exhausted`` is true only when the search finished below the cap. ``qualified-draft`` at best."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        limits = _limits(time_limit, inference_limit, memory_mb, max_output_bytes)
        cap = _whole(max_solutions, "max_solutions", 1, MAX_SOLUTIONS)
        text, asked = _program(program), _goal(goal)
        state["goal"] = asked
        extra |= _describe_program(text) | {"max_solutions": cap, "inference_limit": limits["inferences"],
                                            "time_limit": limits["seconds"]}
        swipl, answers = _search(text, asked, cap, limits, state, extra)
        state["recheck"] = _recheck(swipl, text, asked, answers, state["exhausted"], limits, extra)
        return state

    return labelled("query", work, request_id, run_id)


def prove(program: str, goal: str, *, time_limit: float = runner.DEFAULT_TIME_LIMIT_S,
          inference_limit: int = DEFAULT_INFERENCE_LIMIT, memory_mb: int = runner.DEFAULT_MEMORY_MB,
          max_output_bytes: int = runner.MAX_OUTPUT_BYTES, request_id: str = "", run_id: str = "") -> PrologResult:
    """Whether ``goal`` is derivable from ``program`` (``succeeded``), with its first answer, re-checked in a second
    process. A failure means not derivable from this program, not false. ``qualified-draft`` at best."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        limits = _limits(time_limit, inference_limit, memory_mb, max_output_bytes)
        text, asked = _program(program), _goal(goal)
        state["goal"] = asked
        extra |= _describe_program(text) | {"inference_limit": limits["inferences"], "time_limit": limits["seconds"]}
        swipl, answers = _search(text, asked, 1, limits, state, extra)
        state["succeeded"] = bool(answers)
        state["recheck"] = _recheck(swipl, text, asked, answers, state["exhausted"], limits, extra)
        return state

    return labelled("prove", work, request_id, run_id)


def check_program(program: str, *, time_limit: float = runner.DEFAULT_TIME_LIMIT_S,
                  memory_mb: int = runner.DEFAULT_MEMORY_MB, max_output_bytes: int = runner.MAX_OUTPUT_BYTES,
                  request_id: str = "", run_id: str = "") -> PrologResult:
    """SWI-Prolog's read of ``program``, without consulting it: the predicates it defines, and each syntax error,
    directive, refused clause and goal outside the restricted syntax with its line and column. Nothing is queried."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        limits = _limits(time_limit, 1, memory_mb, max_output_bytes)
        text = _program(program)
        extra |= _describe_program(text)
        swipl = runner.find_swipl()
        state |= {"executable": swipl, "swipl_ran": True}
        read = _run_driver(swipl, "check", text, None, limits, extra.setdefault("read", {}))
        state |= {"swipl_version": read["version"], "predicates": read["predicates"], "problems": read["problems"]}
        _refuse_problems(read)
        return state

    return labelled("check_program", work, request_id, run_id)


def swipl_version(*, time_limit: float = VERSION_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> PrologResult:
    """Which SWI-Prolog would run a program: found as :data:`g6.prolog.envelope.SWIPL` declares, through
    :func:`g6.core.outside.require`, and ``swipl --version`` read. Nothing is run on a program."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        seconds = _seconds(time_limit)
        record = extra.setdefault("run", {})
        found = outside.require(SWIPL, run=runner.version_run(record), timeout=seconds)
        if record.get("argv"):
            state |= {"executable": record["argv"][0], "swipl_ran": True}
        if not found.is_ok():
            raise Refusal(SWIPL_MISSING, found.error)
        read = _VERSION.search(found.value.version)
        if not read:
            raise Refusal(OUTPUT_UNRECOGNISED, f"swipl --version printed {found.value.version[:200]!r}, which is not a "
                                               "version G6 reads")
        record.pop("output_tail", None)
        extra["target"] = read.group(2) or ""
        state["swipl_version"] = read.group(1)
        return state

    return labelled("swipl_version", work, request_id, run_id)


__all__ = ["DEFAULT_INFERENCE_LIMIT", "DEFAULT_MAX_SOLUTIONS", "MAX_GOAL_CHARS", "MAX_INFERENCE_LIMIT",
           "MAX_PROGRAM_CHARS", "MAX_PROGRAM_LINES", "MAX_SOLUTIONS", "VERSION_TIME_LIMIT_S", "check_program", "prove",
           "query", "swipl_version"]
