# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.prolog and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused by the limits, SWI-Prolog missing, a program or goal that does not read, a directive, a refused
  clause, a goal outside the restricted syntax or refused by SWI-Prolog's sandbox library, an error in the goal, a
  limit reached, a re-check that disagrees) is ``blocked-escalated``, and the result keeps what SWI-Prolog reported,
  which is the evidence for the block;
* a ``query`` or ``prove`` result is ``qualified-draft`` only when SWI-Prolog ran it and a second, fresh SWI-Prolog
  process re-checked it and agreed: each answer, put back into the goal, held again as given and, when the search
  finished, the search run again gave the same answers and no more (with no answer, the goal failed again). Anything
  less is ``G6_E_PROLOG_NOT_CHECKED``, whoever built it;
* ``check_program`` is ``qualified-draft`` (``G6_E_PROLOG_PROGRAM_READ``) when SWI-Prolog read every clause, and
  ``swipl_version`` (``G6_E_PROLOG_VERSION_READ``) when SWI-Prolog answered with its version.

It never gives ``verified``: the result is re-validated through :class:`~g6.prolog.schema.PrologResult`, which has no
such state, because an answer derived from a program nobody has inspected says nothing about what was meant. And under
Prolog's closed world, a goal that fails is not derivable from this program, which is not the same as false.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from g6.core.outside import OutsideProgram
from g6.core.reliability_envelope import WarningCard
from g6.prolog.schema import PrologResult

_P = "G6_E_PROLOG_"
# qualified-draft cards
ANSWERS = _P + "ANSWERS"
NO_ANSWERS = _P + "NO_ANSWERS"
GOAL_DERIVABLE = _P + "GOAL_DERIVABLE"
GOAL_NOT_DERIVABLE = _P + "GOAL_NOT_DERIVABLE"
PROGRAM_READ = _P + "PROGRAM_READ"
VERSION_READ = _P + "VERSION_READ"
# refused before SWI-Prolog runs
SWIPL_MISSING = _P + "SWIPL_MISSING"
INPUT_INVALID = _P + "INPUT_INVALID"
INPUT_TOO_LARGE = _P + "INPUT_TOO_LARGE"
TIME_LIMIT_TOO_LONG = _P + "TIME_LIMIT_TOO_LONG"
# blocked on what SWI-Prolog reported
SYNTAX_ERROR = _P + "SYNTAX_ERROR"
DIRECTIVE_REFUSED = _P + "DIRECTIVE_REFUSED"
CLAUSE_REFUSED = _P + "CLAUSE_REFUSED"
UNSAFE_GOAL = _P + "UNSAFE_GOAL"
GOAL_NOT_ALLOWED = _P + "GOAL_NOT_ALLOWED"
UNDEFINED_PREDICATE = _P + "UNDEFINED_PREDICATE"
GOAL_ERROR = _P + "GOAL_ERROR"
INFERENCE_LIMIT = _P + "INFERENCE_LIMIT"
TIME_LIMIT = _P + "TIME_LIMIT"
MEMORY_LIMIT = _P + "MEMORY_LIMIT"
CERTIFICATE_REFUTED = _P + "CERTIFICATE_REFUTED"
RECHECK_UNFINISHED = _P + "RECHECK_UNFINISHED"
# blocked on the run itself
OUTPUT_TOO_LARGE = _P + "OUTPUT_TOO_LARGE"
RUN_FAILED = _P + "RUN_FAILED"
OUTPUT_UNRECOGNISED = _P + "OUTPUT_UNRECOGNISED"
NOT_CHECKED = _P + "NOT_CHECKED"
ENGINE_ERROR = _P + "ENGINE_ERROR"

#: Where SWI-Prolog comes from: ``G6_SWIPL`` names the ``swipl`` program, or ``swipl`` is found on PATH.
SWIPL_ENV = "G6_SWIPL"
INSTALL_SWIPL = (f"Install SWI-Prolog from swi-prolog.org (its installer, or your system's package manager), so swipl "
                 f"is on PATH, or set {SWIPL_ENV} to the swipl program. G6 never installs it.")
#: The outside program, as g6_describe lists it and :func:`g6.core.outside.require` finds it.
SWIPL = OutsideProgram(
    name="SWI-Prolog", purpose="query, prove, check_program and swipl_version run SWI-Prolog (swipl) on your program",
    install=INSTALL_SWIPL, licence="BSD-2-Clause", command="swipl", env=SWIPL_ENV)
#: What a failure means, said on every card that reports one.
CLOSED_WORLD = ("Under Prolog's closed world, a goal that fails is not derivable from this program, which is not the "
                "same as false: a fact or rule the program leaves out makes a true goal fail.")

_ACTIONS = {
    SWIPL_MISSING: INSTALL_SWIPL,
    INPUT_INVALID: "Correct the input as the reason says and run it again.",
    INPUT_TOO_LARGE: "Reduce the program, the goal or the limit below what the reason names.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit of at most 600 seconds.",
    SYNTAX_ERROR: "Fix the syntax at the lines and columns named (SWI-Prolog's own messages, columns counted from 0), "
                  "then run it again.",
    DIRECTIVE_REFUSED: "Remove the directives (:- ... and ?- ...): G6 reads the program and never runs one. Put the "
                       "query in goal instead, and leave out operator, flag and module declarations.",
    CLAUSE_REFUSED: "Rename the predicate named: a program defines only its own predicates, never a built-in, library "
                    "or hook predicate (name your own append/3 my_append/3, or use the library's) or one in another "
                    "module.",
    GOAL_NOT_ALLOWED: "Keep to the restricted syntax: call the program's own predicates and those on the allow-list "
                      "(see the skill), never a variable or Module:Goal. Run anything else in your own SWI-Prolog, "
                      "inside your own sandbox.",
    UNSAFE_GOAL: "Remove what SWI-Prolog's sandbox library named: the goal kept to the restricted syntax, but the "
                 "library's own check refused it.",
    UNDEFINED_PREDICATE: "Define the predicate named in the program, or correct its name or its number of arguments.",
    GOAL_ERROR: "The goal raised an error in SWI-Prolog (its message is the reason); correct the program or the goal.",
    INFERENCE_LIMIT: "Raise inference_limit, or give the program a terminating search: the answers found before the "
                     "limit are kept and were not re-checked.",
    TIME_LIMIT: "Raise time_limit (at most 600 seconds) or simplify the search: the answers found before the limit "
                "are kept and were not re-checked.",
    MEMORY_LIMIT: "Raise memory_mb (SWI-Prolog's stacks get half of it) or give the program a search that builds "
                  "smaller terms: the answers found before the limit are kept and were not re-checked.",
    CERTIFICATE_REFUTED: "Do not use these answers: a second SWI-Prolog process did not reproduce them. The re-check "
                         "in the evidence names the answer and why; report it with the program and the goal.",
    RECHECK_UNFINISHED: "The re-check reached its limit before it agreed, so the answers are not reported as checked; "
                        "raise the limits or simplify the goal.",
    OUTPUT_TOO_LARGE: "SWI-Prolog's output passed its cap, so the run was stopped. Ask for fewer answers "
                      "(max_solutions) or print less, or raise max_output_bytes from Python.",
    RUN_FAILED: "SWI-Prolog stopped before G6's driver finished (a crash, the memory cap, or a program it could not "
                "start); inspect the reason and the end of its output.",
    OUTPUT_UNRECOGNISED: "Do not use the run: the driver's output does not fit what G6 reads, so it is not reported. "
                         "The evidence holds the end of the output; report it with the program and the goal.",
    NOT_CHECKED: "Build results with g6.prolog's functions; this one carries no re-checked report from SWI-Prolog.",
    ENGINE_ERROR: "G6 failed before answering; report it with the input and the reason.",
}


class Refusal(Exception):
    """An op that cannot produce an answer, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


def labelled(operation: str, work: Callable[[dict[str, Any]], dict[str, Any]], request_id: str,
             run_id: str) -> PrologResult:
    """``work``'s outcome labelled by :func:`finish`. ``work`` fills ``state`` as it goes, so a refusal keeps what had
    run. Nothing raises through the surface."""
    state: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        outcome = work(state)
    except Refusal as refusal:
        outcome = {**state, "code": refusal.code, "reason": refusal.reason, **refusal.keep}
    except Exception as exc:  # noqa: BLE001 -- reported with its type, never raised through g6_invoke
        outcome = {**state, "code": ENGINE_ERROR, "reason": f"{type(exc).__name__}: {exc}"[:500]}
    return finish(operation, elapsed_s=round(time.perf_counter() - started, 6), request_id=request_id, run_id=run_id,
                  **outcome)


def finish(operation: str, *, swipl_ran: bool = False, swipl_version: str = "", executable: str = "",
           goal: str = "", answers: list[dict[str, Any]] | None = None, exhausted: bool | None = None,
           succeeded: bool | None = None, stopped_by: str = "", predicates: list[dict[str, Any]] | None = None,
           problems: list[dict[str, Any]] | None = None, recheck: dict[str, Any] | None = None, code: str = "",
           reason: str = "", extra: dict[str, Any] | None = None, elapsed_s: float = 0.0, request_id: str = "",
           run_id: str = "") -> PrologResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``."""
    answers = None if answers is None else list(answers)
    predicates = None if predicates is None else list(predicates)
    problems = list(problems or [])
    if not code:
        code, reason = _decide(operation, swipl_ran, swipl_version, answers, exhausted, succeeded, predicates, problems,
                               recheck)
    evidence: dict[str, Any] = {
        "kind": ("prolog_blocked" if swipl_ran else "prolog_refusal") if code else f"prolog_{operation}",
        "operation": operation,
        "external_program_ran": swipl_ran,
        "swipl_version": swipl_version,
        "executable": executable,
        "goal": goal,
        "answer_count": None if answers is None else len(answers),
        "exhausted": exhausted,
        "succeeded": succeeded,
        "stopped_by": stopped_by,
        "predicates": predicates,
        "problems": problems,
        "recheck": recheck,
        "closed_world": CLOSED_WORLD,
        "elapsed_s": elapsed_s,
    }
    evidence |= extra or {}
    if code:
        evidence |= {"code": code, "reason": reason}
        card = WarningCard(code=code, message=reason, severity="blocked",
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    else:
        card = _card(operation, swipl_version, executable, answers or [], exhausted, succeeded, predicates or [])
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return PrologResult.model_validate({
        "operation": operation, "swipl_version": swipl_version, "executable": executable, "goal": goal,
        "answers": answers, "exhausted": exhausted, "succeeded": succeeded, "stopped_by": stopped_by,
        "predicates": predicates, "problems": problems, "recheck": recheck, "code": code, "reason": reason,
        "elapsed_s": elapsed_s, "completion_state": "blocked-escalated" if code else "qualified-draft",
        "warning_card": card, "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _decide(operation: str, ran: bool, version: str, answers: list[dict[str, Any]] | None, exhausted: bool | None,
            succeeded: bool | None, predicates: list[dict[str, Any]] | None, problems: list[dict[str, Any]],
            recheck: dict[str, Any] | None) -> tuple[str, str]:
    """The code a result with no refusal of its own earns ('' when it is a draft)."""
    if not ran:
        return NOT_CHECKED, "SWI-Prolog did not run, so there is nothing it reported to label"
    if operation == "swipl_version":
        return ("", "") if version else (NOT_CHECKED, "SWI-Prolog ran but no version was read from it")
    if operation == "check_program":
        if predicates is None or problems:
            return NOT_CHECKED, "the result carries no clean read of the program by SWI-Prolog"
        return "", ""
    if answers is None or not isinstance(recheck, dict) or recheck.get("passed") is not True:
        return NOT_CHECKED, "the result carries no answers that a second SWI-Prolog process re-checked and agreed with"
    if not answers and exhausted is not True:
        return NOT_CHECKED, "the result has no answers and does not say the search finished"
    if recheck.get("checked") != len(answers) + (1 if exhausted is True else 0):
        return NOT_CHECKED, "the re-check does not cover every answer and, when the search finished, its end"
    if operation == "prove" and succeeded is not bool(answers):
        return NOT_CHECKED, "the result's success does not match its answers"
    return "", ""


def _card(operation: str, version: str, executable: str, answers: list[dict[str, Any]], exhausted: bool | None,
          succeeded: bool | None, predicates: list[dict[str, Any]]) -> dict[str, Any]:
    checked = (" A second, fresh SWI-Prolog process re-checked it under the same restricted syntax and limits and "
               "agreed.")
    inspect = (" Whether the program says what you mean is for a person to inspect; until then this is a qualified "
               "draft, not verified.")
    if operation == "swipl_version":
        return WarningCard(
            code=VERSION_READ, severity="amber",
            message=f"SWI-Prolog {version} answered from {executable}. This says which SWI-Prolog would run a program; "
                    "it ran nothing.",
            suggested_action="Run query, prove or check_program to have this SWI-Prolog read or run a program.",
        ).model_dump()
    if operation == "check_program":
        count = sum(int(p.get("clauses", 0)) for p in predicates)
        return WarningCard(
            code=PROGRAM_READ, severity="amber",
            message=f"SWI-Prolog {version} read every clause of the program ({count} clauses, {len(predicates)} "
                    "predicates) without consulting it: no directive ran and nothing was queried. A program that "
                    "reads can still be wrong.",
            suggested_action="Run query or prove to see what follows from it.",
        ).model_dump()
    if operation == "prove":
        if succeeded:
            return WarningCard(
                code=GOAL_DERIVABLE, severity="amber",
                message=f"SWI-Prolog {version} derived the goal from your program (the first answer is kept)."
                        + checked + inspect,
                suggested_action="Read the program's facts and rules behind the goal before relying on it.",
            ).model_dump()
        return WarningCard(
            code=GOAL_NOT_DERIVABLE, severity="amber",
            message=f"SWI-Prolog {version} found the goal not derivable from your program: its search finished with no "
                    "answer." + checked + " " + CLOSED_WORLD + inspect,
            suggested_action="Before saying the goal is false, check that the program states every fact and rule that "
                             "bears on it.",
        ).model_dump()
    if not answers:
        return WarningCard(
            code=NO_ANSWERS, severity="amber",
            message=f"SWI-Prolog {version} found no answer: its search finished below the cap." + checked + " "
                    + CLOSED_WORLD + inspect,
            suggested_action="Before saying nothing satisfies the goal, check that the program states every fact and "
                             "rule that bears on it.",
        ).model_dump()
    complete = ("its search finished below the cap, so these are all the answers your program gives" if exhausted else
                "the search stopped at max_solutions, so there may be more")
    return WarningCard(
        code=ANSWERS, severity="amber",
        message=f"SWI-Prolog {version} found {len(answers)} answer{'' if len(answers) == 1 else 's'}; {complete}. "
                "Each one, put back into the goal, held again as given in a second, fresh SWI-Prolog process under the "
                "same restricted syntax and limits" + (", where the search, run again, gave the same answers and no "
                                                       "more." if exhausted else ".") + inspect,
        suggested_action="Read the answers against the program's facts and rules before relying on them; raise "
                         "max_solutions when exhausted is false and you need them all.",
    ).model_dump()


__all__ = ["ANSWERS", "CERTIFICATE_REFUTED", "CLAUSE_REFUSED", "CLOSED_WORLD", "DIRECTIVE_REFUSED", "ENGINE_ERROR",
           "GOAL_DERIVABLE", "GOAL_ERROR", "GOAL_NOT_ALLOWED", "GOAL_NOT_DERIVABLE", "INFERENCE_LIMIT", "INPUT_INVALID",
           "INPUT_TOO_LARGE", "INSTALL_SWIPL", "MEMORY_LIMIT", "NOT_CHECKED", "NO_ANSWERS", "OUTPUT_TOO_LARGE", "OUTPUT_UNRECOGNISED", "PROGRAM_READ",
           "RECHECK_UNFINISHED", "RUN_FAILED", "Refusal", "SWIPL", "SWIPL_ENV", "SWIPL_MISSING", "SYNTAX_ERROR",
           "TIME_LIMIT", "TIME_LIMIT_TOO_LONG", "UNDEFINED_PREDICATE", "UNSAFE_GOAL", "VERSION_READ", "finish",
           "labelled"]
