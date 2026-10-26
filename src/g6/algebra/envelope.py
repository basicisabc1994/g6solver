# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.algebra and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused by the parser or a limit, SymPy or SageMath missing, out of time, no answer, an answer G6's
  re-check refuted) is ``blocked-escalated``. The result keeps no answer; a refuted answer is kept in the evidence;
* a SymPy answer is ``qualified-draft`` only with G6's re-check record: ``G6_E_ALGEBRA_RECHECKED`` when the re-check
  passed, ``G6_E_ALGEBRA_NOT_RECHECKED`` when it could not decide (near a singular point, a numeric check with too few
  usable points, a series G6 does not re-check, the time limit reached during the re-check), which is never reported as
  passed. A failed re-check is ``G6_E_ALGEBRA_CERTIFICATE_REFUTED``; an answer with no record is
  ``G6_E_ALGEBRA_NOT_CHECKED``, whoever built it;
* a SageMath answer is ``qualified-draft`` (``G6_E_ALGEBRA_SAGE_ANSWER``), and its card says G6 did not re-check it;
  ``sage_version`` is ``qualified-draft`` (``G6_E_ALGEBRA_SAGE_VERSION_READ``) when SageMath answered with a version.

It never gives ``verified``: the result is re-validated through :class:`~g6.algebra.schema.AlgebraResult`, which has no
such state, because a computer algebra system's answer on a caller's expression is not a proof a person has inspected.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from g6.algebra.schema import AlgebraResult
from g6.core.reliability_envelope import WarningCard

_P = "G6_E_ALGEBRA_"
# qualified-draft cards
RECHECKED = _P + "RECHECKED"
NOT_RECHECKED = _P + "NOT_RECHECKED"
SAGE_ANSWER = _P + "SAGE_ANSWER"
SAGE_VERSION_READ = _P + "SAGE_VERSION_READ"
# refused by G6's parser, before anything is built
PARSE_ERROR = _P + "PARSE_ERROR"
CONSTRUCT_REFUSED = _P + "CONSTRUCT_REFUSED"
UNKNOWN_FUNCTION = _P + "UNKNOWN_FUNCTION"
UNKNOWN_SYMBOL = _P + "UNKNOWN_SYMBOL"
WRONG_ARGUMENTS = _P + "WRONG_ARGUMENTS"
SYMBOL_INVALID = _P + "SYMBOL_INVALID"
EXPONENT_TOO_LARGE = _P + "EXPONENT_TOO_LARGE"
FACTORIAL_TOO_LARGE = _P + "FACTORIAL_TOO_LARGE"
NUMBER_TOO_LARGE = _P + "NUMBER_TOO_LARGE"
# refused before anything runs
INPUT_INVALID = _P + "INPUT_INVALID"
INPUT_TOO_LARGE = _P + "INPUT_TOO_LARGE"
TIME_LIMIT_TOO_LONG = _P + "TIME_LIMIT_TOO_LONG"
CAS_UNAVAILABLE = _P + "CAS_UNAVAILABLE"
SAGE_MISSING = _P + "SAGE_MISSING"
# blocked on what the engine answered
NO_ANSWER = _P + "NO_ANSWER"
NOT_INVERTIBLE = _P + "NOT_INVERTIBLE"
ANSWER_TOO_LARGE = _P + "ANSWER_TOO_LARGE"
CERTIFICATE_REFUTED = _P + "CERTIFICATE_REFUTED"
# blocked on the run itself
TIME_LIMIT = _P + "TIME_LIMIT"
OUTPUT_TOO_LARGE = _P + "OUTPUT_TOO_LARGE"
RUN_FAILED = _P + "RUN_FAILED"
OUTPUT_UNRECOGNISED = _P + "OUTPUT_UNRECOGNISED"
SAGE_FAILED = _P + "SAGE_FAILED"
NOT_CHECKED = _P + "NOT_CHECKED"
ENGINE_ERROR = _P + "ENGINE_ERROR"

#: The parser's codes: each refused construct or limit has its own (see :mod:`g6.algebra.language`).
PARSER_CODES = (PARSE_ERROR, CONSTRUCT_REFUSED, UNKNOWN_FUNCTION, UNKNOWN_SYMBOL, WRONG_ARGUMENTS, SYMBOL_INVALID,
                EXPONENT_TOO_LARGE, FACTORIAL_TOO_LARGE, NUMBER_TOO_LARGE, INPUT_INVALID, INPUT_TOO_LARGE)

INSTALL_CAS = "pip install 'g6[cas] @ git+https://github.com/basicisabc1994/g6solver'"
#: Where SageMath comes from: ``G6_SAGE`` names the ``sage`` program, or ``sage`` is found on PATH.
SAGE_ENV = "G6_SAGE"
INSTALL_SAGE = (f"SageMath is a separate program under the GPL, so G6 never installs, bundles or imports it. Install it "
                f"yourself as sagemath.org describes, then put sage on PATH or set {SAGE_ENV} to the sage program.")

_ACTIONS = {
    PARSE_ERROR: "Correct the expression at the line and column given. Write every product with * (2*x, not 2x), "
                 "powers with ^ or **, and call functions with parentheses, such as sin(x).",
    CONSTRUCT_REFUSED: "Remove what the reason names: the language has numbers, declared symbols, + - * / ^, "
                       "parentheses, the listed functions and pi, E and I, and nothing else (no strings, attributes, "
                       "brackets, keywords or comments).",
    UNKNOWN_FUNCTION: "Use one of the functions help lists, such as sin, exp, log, sqrt, abs, factorial or binomial.",
    UNKNOWN_SYMBOL: "Declare every symbol the expression uses in symbols, such as {\"x\": [\"real\"]}, or check the "
                    "spelling.",
    WRONG_ARGUMENTS: "Give the function the number of arguments help lists, such as log(x) or log(x, 2).",
    SYMBOL_INVALID: "Name each symbol with letters and digits (single underscores between them allowed), not a "
                    "function or constant name, and give assumptions only from real, positive and integer.",
    EXPONENT_TOO_LARGE: "Use exponents of at most 1000 in size, written so G6 can bound them (numbers, pi, E, I and "
                        "+ - * / ^); a power that size grows without limit.",
    FACTORIAL_TOO_LARGE: "Give factorial and binomial arguments of at most 1000.",
    NUMBER_TOO_LARGE: "Use numbers of at most 10,000 digits.",
    INPUT_INVALID: "Correct the input as the reason says and run it again.",
    INPUT_TOO_LARGE: "Reduce the input below the limit the reason names.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit within the cap the reason names.",
    CAS_UNAVAILABLE: f"Install SymPy: {INSTALL_CAS}.",
    SAGE_MISSING: INSTALL_SAGE,
    NO_ANSWER: "SymPy gave no answer of the kind asked for (the reason names what it returned). Rewrite or simplify "
               "the input, or try another op.",
    NOT_INVERTIBLE: "The matrix has determinant zero, so it has no inverse.",
    ANSWER_TOO_LARGE: "The answer is too long to report. Ask for a smaller one: lower the order, or simplify the "
                      "input first.",
    CERTIFICATE_REFUTED: "Do not use the answer: G6's independent re-check contradicts it. The evidence keeps the "
                         "refuted answer and where the re-check failed; report it with the input.",
    TIME_LIMIT: "Raise time_limit within its cap, or simplify the input: no answer came within the time limit, so "
                "the work was stopped and its process tree killed.",
    OUTPUT_TOO_LARGE: "The program's output passed its cap, so the run was stopped. Ask for a smaller answer.",
    RUN_FAILED: "The program stopped before it answered (a crash, or the memory cap); inspect the reason and the end "
                "of its output.",
    OUTPUT_UNRECOGNISED: "Do not use the run: the program's output does not fit what G6 reads. The evidence holds the "
                         "end of the output; report it with the input.",
    SAGE_FAILED: "SageMath reported an error on G6's reading of your expression; the evidence holds the end of its "
                 "output. Correct the expression and run it again.",
    NOT_CHECKED: "Build results with g6.algebra's functions; this one carries no re-check record from G6.",
    ENGINE_ERROR: "G6 failed before answering; report it with the input and the reason.",
}
_NOT_VERIFIED = ("A computer algebra system's answer on your input is not a proof a person has inspected, so it is not "
                 "verified.")


class Refusal(Exception):
    """An op that cannot produce an answer, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


def labelled(operation: str, work: Callable[[dict[str, Any]], dict[str, Any]], request_id: str,
             run_id: str) -> AlgebraResult:
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


def finish(operation: str, *, engine: str = "SymPy", engine_version: str = "", engine_ran: bool = False,
           executable: str = "", input: dict[str, Any] | None = None, answer: str = "", latex: str = "",
           answers: list[dict[str, Any]] | None = None, complete: bool | None = None, completeness: str = "",
           details: dict[str, Any] | None = None, recheck: dict[str, Any] | None = None,
           refusals: list[dict[str, Any]] | None = None, code: str = "", reason: str = "",
           extra: dict[str, Any] | None = None, elapsed_s: float = 0.0, request_id: str = "",
           run_id: str = "") -> AlgebraResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``."""
    answers, refusals, details = list(answers or []), list(refusals or []), dict(details or {})
    recheck = dict(recheck) if recheck else None
    if not code:
        code, reason = _decide(operation, engine_ran, engine_version, answer, answers, recheck)
    evidence: dict[str, Any] = {
        "kind": ("algebra_blocked" if engine_ran else "algebra_refusal") if code else
                ("algebra_sage_version" if operation == "sage_version" else "algebra_answer"),
        "operation": operation,
        "engine": engine,
        "engine_version": engine_version,
        "engine_ran": engine_ran,
        "external_program_ran": engine_ran and engine == "SageMath",
        "executable": executable,
        "input": input or {},
        "recheck": recheck or {},
        "complete": complete,
        "completeness": completeness,
        "refusals": refusals,
        "elapsed_s": elapsed_s,
    }
    evidence |= extra or {}
    if code:
        evidence |= {"code": code, "reason": reason}
        if answer or answers:
            evidence["refuted_answer" if code == CERTIFICATE_REFUTED else "unreported_answer"] = {
                "answer": answer, "latex": latex, "answers": answers, "details": details}
        answer, latex, answers, details, complete, completeness = "", "", [], {}, None, ""
        card = WarningCard(code=code, message=reason, severity="blocked",
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    elif operation == "sage_version":
        card = WarningCard(code=SAGE_VERSION_READ, severity="amber",
                           message=f"SageMath {engine_version} answered from {executable}. This says which SageMath "
                                   "would compute; it computed nothing.",
                           suggested_action="Run sage_evaluate to have this SageMath compute.").model_dump()
    elif operation == "sage_evaluate":
        card = _sage_card(engine_version, (input or {}).get("expression", ""))
    else:
        card = _answer_card(engine, engine_version, recheck or {}, complete, completeness, input or {})
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return AlgebraResult.model_validate({
        "operation": operation, "engine": engine, "engine_version": engine_version, "input": input or {},
        "answer": answer, "latex": latex, "answers": answers, "complete": complete, "completeness": completeness,
        "details": details, "recheck": recheck or {}, "refusals": refusals, "code": code, "reason": reason,
        "elapsed_s": elapsed_s, "completion_state": "blocked-escalated" if code else "qualified-draft",
        "warning_card": card, "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _decide(operation: str, ran: bool, version: str, answer: str, answers: list[dict[str, Any]],
            recheck: dict[str, Any] | None) -> tuple[str, str]:
    """The code a result with no refusal of its own earns ('' when it is a draft)."""
    if not ran:
        return NOT_CHECKED, "no engine ran, so there is no answer to label"
    if operation == "sage_version":
        return ("", "") if version else (NOT_CHECKED, "SageMath ran but no version was read from it")
    if not (answer or answers):
        return NOT_CHECKED, "the result carries no answer"
    if operation == "sage_evaluate":
        return "", ""
    status = (recheck or {}).get("status")
    if status == "refuted":
        failures = "; ".join(str(f) for f in recheck.get("failures", [])[:3]) or "no detail recorded"
        return CERTIFICATE_REFUTED, f"G6's independent re-check contradicts SymPy's answer: {failures}"
    if status in ("passed", "undecided"):
        return "", ""
    return NOT_CHECKED, "the answer carries no re-check record from G6, so it is not reported"


def _complete_note(completeness: str) -> str:
    return f" {completeness}" if completeness else ""


def _answer_card(engine: str, version: str, recheck: dict[str, Any], complete: bool | None, completeness: str,
                 given: dict[str, Any]) -> dict[str, Any]:
    who = f"{engine} {version}".strip()
    methods = "; ".join(str(m) for m in recheck.get("methods", [])) or "no method recorded"
    read = given.get("expression") or given.get("equations") or given.get("matrix") or ""
    reading = f" (G6 read the input as {read})" if isinstance(read, str) and read else ""
    if recheck.get("status") == "passed":
        return WarningCard(
            code=RECHECKED, severity="amber",
            message=f"{who} computed this answer, and G6 re-checked it a second, independent way: {methods}."
                    f"{_complete_note(completeness)} {_NOT_VERIFIED}",
            suggested_action=f"Check that the input says what you mean{reading} before relying on the answer.",
        ).model_dump()
    why = recheck.get("why") or "the re-check could not decide"
    return WarningCard(
        code=NOT_RECHECKED, severity="amber",
        message=f"{who} computed this answer, but G6 could not re-check it: {why}. It is reported as not re-checked, "
                f"never as passed.{_complete_note(completeness)} {_NOT_VERIFIED}",
        suggested_action="Treat the answer with care: check it another way, or simplify the input and run it again.",
    ).model_dump()


def _sage_card(version: str, read: str) -> dict[str, Any]:
    return WarningCard(
        code=SAGE_ANSWER, severity="amber",
        message=f"SageMath {version} computed this from G6's reading of your expression ({read}). G6 did not re-check "
                f"it: SageMath's answers are reported as SageMath gave them. {_NOT_VERIFIED}",
        suggested_action="Check the answer another way, for example with one of g6.algebra's SymPy ops, which G6 "
                         "re-checks, before relying on it.",
    ).model_dump()


__all__ = ["ANSWER_TOO_LARGE", "CAS_UNAVAILABLE", "CERTIFICATE_REFUTED", "CONSTRUCT_REFUSED", "ENGINE_ERROR",
           "EXPONENT_TOO_LARGE", "FACTORIAL_TOO_LARGE", "INPUT_INVALID", "INPUT_TOO_LARGE", "INSTALL_CAS",
           "INSTALL_SAGE", "NOT_CHECKED", "NOT_INVERTIBLE", "NOT_RECHECKED", "NO_ANSWER", "NUMBER_TOO_LARGE",
           "OUTPUT_TOO_LARGE", "OUTPUT_UNRECOGNISED", "PARSER_CODES", "PARSE_ERROR", "RECHECKED", "RUN_FAILED",
           "Refusal", "SAGE_ANSWER", "SAGE_ENV", "SAGE_FAILED", "SAGE_MISSING", "SAGE_VERSION_READ", "SYMBOL_INVALID",
           "TIME_LIMIT", "TIME_LIMIT_TOO_LONG", "UNKNOWN_FUNCTION", "UNKNOWN_SYMBOL", "WRONG_ARGUMENTS", "finish",
           "labelled"]
