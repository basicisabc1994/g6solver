# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.model_checking and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused before running, a tool missing, out of time, a vacuous model, refuted by G6's own re-check) is
  ``blocked-escalated``, and the result keeps no verdict or trace;
* a violation (an invariant broken, a deadlock, an induction step that fails, or TLC's own violation) is
  ``blocked-escalated`` too, and keeps its trace: the trace is the evidence. A symbolic one is reported only after its
  trace passed G6's re-check;
* a symbolic answer that finds nothing, a k-induction proof or a witness is ``qualified-draft`` only when G6's re-check
  passed, with a card that says how far it reaches: ``G6_E_MODEL_CHECK_BOUNDED`` for a search that covers paths of up
  to its depth only, ``G6_E_MODEL_CHECK_ALL_PATHS_END`` when every path ended within it, ``G6_E_MODEL_CHECK_K_INDUCTION``
  with its k, ``G6_E_MODEL_CHECK_TRACE_FOUND`` for a witness;
* TLC's "no error" is ``qualified-draft`` as TLC's own verdict (``G6_E_MODEL_CHECK_TLC_NO_ERROR``), and SANY's clean
  parse is ``qualified-draft`` with nothing model-checked (``G6_E_MODEL_CHECK_SANY_NO_ERROR``).

It never gives ``verified``: the result is re-validated through :class:`~g6.model_checking.schema.ModelCheckResult`,
which has no such state, because a model checker's answer on your model is not a proof a person has inspected.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from g6.core.errors import ISOLATION_ACTION, ISOLATION_UNAVAILABLE
from g6.core.reliability_envelope import WarningCard
from g6.model_checking.schema import ModelCheckResult

_P = "G6_E_MODEL_CHECK_"
# qualified-draft cards
BOUNDED = _P + "BOUNDED"
ALL_PATHS_END = _P + "ALL_PATHS_END"
K_INDUCTION = _P + "K_INDUCTION"
TRACE_FOUND = _P + "TRACE_FOUND"
TLC_NO_ERROR = _P + "TLC_NO_ERROR"
SANY_NO_ERROR = _P + "SANY_NO_ERROR"
# blocked, with the trace that shows it
VIOLATION = _P + "VIOLATION"
DEADLOCK = _P + "DEADLOCK"
NOT_INDUCTIVE = _P + "NOT_INDUCTIVE"
TLC_INVARIANT_VIOLATED = _P + "TLC_INVARIANT_VIOLATED"
TLC_PROPERTY_VIOLATED = _P + "TLC_PROPERTY_VIOLATED"
TLC_DEADLOCK = _P + "TLC_DEADLOCK"
TLC_ASSERTION_FAILED = _P + "TLC_ASSERTION_FAILED"
TLC_EVALUATION_ERROR = _P + "TLC_EVALUATION_ERROR"
# blocked, with what TLC or SANY said
TLC_SPEC_ERROR = _P + "TLC_SPEC_ERROR"
TLC_FAILED = _P + "TLC_FAILED"
TLC_UNRECOGNISED = _P + "TLC_UNRECOGNISED"
SANY_ERRORS = _P + "SANY_ERRORS"
# blocked
VACUOUS_INIT = _P + "VACUOUS_INIT"
VACUOUS_TRANSITION = _P + "VACUOUS_TRANSITION"
CERTIFICATE_REFUTED = _P + "CERTIFICATE_REFUTED"
NOT_CHECKED = _P + "NOT_CHECKED"
NOT_RECHECKABLE = _P + "NOT_RECHECKABLE"
PARSE_ERROR = _P + "PARSE_ERROR"
UNKNOWN_SORT = _P + "UNKNOWN_SORT"
INPUT_INVALID = _P + "INPUT_INVALID"
INPUT_TOO_LARGE = _P + "INPUT_TOO_LARGE"
TIME_LIMIT = _P + "TIME_LIMIT"
TIME_LIMIT_TOO_LONG = _P + "TIME_LIMIT_TOO_LONG"
SOLVER_UNAVAILABLE = _P + "SOLVER_UNAVAILABLE"
SOLVER_UNKNOWN = _P + "SOLVER_UNKNOWN"
SOLVER_ERROR = _P + "SOLVER_ERROR"
ENGINE_ERROR = _P + "ENGINE_ERROR"
JAR_MISSING = _P + "JAR_MISSING"
DIGEST_MISSING = _P + "DIGEST_MISSING"
DIGEST_MISMATCH = _P + "DIGEST_MISMATCH"
JAVA_MISSING = _P + "JAVA_MISSING"
MODULE_INVALID = _P + "MODULE_INVALID"
OUTPUT_TOO_LARGE = _P + "OUTPUT_TOO_LARGE"
RUN_FAILED = _P + "RUN_FAILED"

#: Where TLC comes from: the user's own tla2tools.jar, pinned by its SHA-256, and Java. G6 never downloads either.
JAR_ENV = "G6_TLA2TOOLS_JAR"
DIGEST_ENV = "G6_TLA2TOOLS_SHA256"
JAVA_ENV = "G6_JAVA"
INSTALL_Z3 = "pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'"
SUPPLY_JAR = (f"Download tla2tools.jar yourself from the TLA+ project's releases (G6 is tested on v1.7.4, whose SHA-256 "
              "is 936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88), check it, set "
              f"{JAR_ENV} to its path and {DIGEST_ENV} to its SHA-256 (64 hex digits). G6 never downloads it, and runs "
              "only the jar whose digest you pinned.")

#: Codes whose result keeps its verdict and trace: what was found is the evidence.
TRACE_CODES = frozenset({VIOLATION, DEADLOCK, NOT_INDUCTIVE, TLC_INVARIANT_VIOLATED, TLC_PROPERTY_VIOLATED,
                         TLC_DEADLOCK, TLC_ASSERTION_FAILED, TLC_EVALUATION_ERROR})
#: Codes whose result keeps its verdict and what TLC or SANY reported, but no trace.
REPORT_CODES = frozenset({TLC_SPEC_ERROR, TLC_FAILED, TLC_UNRECOGNISED, SANY_ERRORS})

#: The symbolic verdicts, and those that block.
SYMBOLIC_FOUND = {"violated": VIOLATION, "deadlock": DEADLOCK, "not_inductive": NOT_INDUCTIVE}
SYMBOLIC_NONE = ("no_violation_within_bound", "no_deadlock_within_bound", "not_reached_within_bound")
SYMBOLIC_VERDICTS = frozenset({*SYMBOLIC_FOUND, *SYMBOLIC_NONE, "k_inductive", "reached"})
TLC_VERDICTS = {"invariant_violated": TLC_INVARIANT_VIOLATED, "property_violated": TLC_PROPERTY_VIOLATED,
                "deadlock": TLC_DEADLOCK, "assumption_violated": TLC_ASSERTION_FAILED,
                "assertion_failed": TLC_ASSERTION_FAILED, "evaluation_error": TLC_EVALUATION_ERROR,
                "spec_error": TLC_SPEC_ERROR, "failed": TLC_FAILED, "unrecognised": TLC_UNRECOGNISED}

_ACTIONS = {
    ISOLATION_UNAVAILABLE: ISOLATION_ACTION,
    VIOLATION: "Read the trace: it starts in an initial state and each step follows the transition. Fix the model or "
               "the invariant, then check again.",
    DEADLOCK: "Read the trace to the state with no successor. Add the step it is missing, or allow it to stay put "
              "(next_x = x for every variable) if stopping there is meant.",
    NOT_INDUCTIVE: "Strengthen the invariant with a fact the trace's first state breaks, or raise depth (at most 50). "
                   "The trace is an induction step, not a counterexample.",
    TLC_INVARIANT_VIOLATED: "Read TLC's trace to the state that breaks the invariant, then fix the spec or the "
                            "invariant.",
    TLC_PROPERTY_VIOLATED: "Read TLC's trace: a temporal property fails on it, often for want of a fairness condition.",
    TLC_DEADLOCK: "Read TLC's trace to the state with no successor; add the missing step, or set check_deadlock false "
                  "if stopping there is meant.",
    TLC_ASSERTION_FAILED: "An ASSUME or an Assert in the spec came out false; read TLC's message and the trace.",
    TLC_EVALUATION_ERROR: "TLC could not evaluate an expression (TLC's message names where); fix the spec there.",
    TLC_SPEC_ERROR: "Fix the spec or the config at the places TLC and SANY name, then run it again.",
    TLC_FAILED: "TLC stopped without a verdict; read its messages (memory and state-space limits are the usual "
                "causes).",
    TLC_UNRECOGNISED: "Do not use the run: TLC's output and exit code do not fit a verdict G6 reads. The evidence "
                      "holds the end of the output; report it with the spec.",
    SANY_ERRORS: "Fix the module at the lines and columns SANY names, then parse it again.",
    VACUOUS_INIT: "Correct init: as written no state satisfies it, so every claim about the model holds vacuously.",
    VACUOUS_TRANSITION: "Correct the transition: as written no state has a successor, so the model never moves.",
    CERTIFICATE_REFUTED: "Do not use the answer: G6's re-check on fresh solvers contradicts it. The evidence names the "
                         "failures; report it with the model.",
    NOT_CHECKED: "Build results with g6.model_checking's functions; this one carries no re-check by G6.",
    NOT_RECHECKABLE: "Keep Real arithmetic linear: a value in the trace is not a rational number, so G6 cannot state "
                     "it exactly to re-check the trace.",
    PARSE_ERROR: "Write init, the transition, each invariant and the goal as one SMT-LIB term over the declared "
                 "variables; only the transition may use next_x.",
    UNKNOWN_SORT: "Give each state variable the sort Int, Real, Bool or (_ BitVec n).",
    INPUT_INVALID: "Correct the input as the reason says and run it again.",
    INPUT_TOO_LARGE: "Reduce the model, the depth or the spec below the limit the reason names.",
    TIME_LIMIT: "The run did not finish within the time limit. Raise time_limit (up to its cap), lower depth, or "
                "simplify the model.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit within the cap the reason names (60 seconds for Z3, 600 for TLC and SANY).",
    SOLVER_UNAVAILABLE: f"Install Z3: {INSTALL_Z3}.",
    SOLVER_UNKNOWN: "Simplify the model (non-linear arithmetic and quantifiers are the usual causes). Unknown is never "
                    "read as an answer.",
    SOLVER_ERROR: "Z3 failed before answering; inspect the reason, and report it with the model if it recurs.",
    ENGINE_ERROR: "G6 failed before answering; report it with the input and the reason.",
    JAR_MISSING: SUPPLY_JAR,
    DIGEST_MISSING: f"Set {DIGEST_ENV} to the SHA-256 of your tla2tools.jar, 64 hex digits (for example from "
                    "certutil -hashfile or sha256sum). " + SUPPLY_JAR,
    DIGEST_MISMATCH: f"The jar is not the one you pinned. Check where it came from; if it is the one you mean, set "
                     f"{DIGEST_ENV} to its digest. G6 never downloads a replacement.",
    JAVA_MISSING: f"Install a Java runtime (11 or later) and put java on PATH, or set {JAVA_ENV} to the java "
                  "executable.",
    MODULE_INVALID: "Begin the spec with ---- MODULE Name ---- where Name starts with a letter and uses only letters, "
                    "digits and _, is not a standard module's name, and matches module when you give it.",
    OUTPUT_TOO_LARGE: "TLC's output passed its cap and the run was stopped. Check fewer things at once, or raise "
                      "max_output_bytes from Python.",
    RUN_FAILED: "The program could not be run to the end, or G6 could not confirm it stopped; inspect the reason.",
}
_NOT_VERIFIED = ("A solver's answer on your model is not a proof a person has inspected, so it is not verified; if the "
                 "model does not say what you mean, neither does the answer.")


class Refusal(Exception):
    """An op that cannot produce an answer, with its stable code, the reason in words and any fields to keep."""

    def __init__(self, code: str, reason: str, **keep: Any) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.keep = keep


def labelled(operation: str, work: Callable[[dict[str, Any]], dict[str, Any]], request_id: str,
             run_id: str) -> ModelCheckResult:
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


def finish(operation: str, *, engine: str = "", engine_version: str = "", verdict: str = "", code: str = "",
           reason: str = "", depth: int | None = None, explored_depth: int | None = None, all_paths_end: bool = False,
           k: int | None = None, violated: list[str] | None = None, trace: list[dict[str, Any]] | None = None,
           trace_kind: str = "", recheck: dict[str, Any] | None = None, model: dict[str, Any] | None = None,
           tla: dict[str, Any] | None = None, extra: dict[str, Any] | None = None, elapsed_s: float = 0.0,
           request_id: str = "", run_id: str = "") -> ModelCheckResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``."""
    violated, trace, recheck, tla = list(violated or []), list(trace or []), dict(recheck or {}), dict(tla or {})
    claimed = {"verdict": verdict, "explored_depth": explored_depth, "all_paths_end": all_paths_end, "k": k,
               "violated": violated, "trace_kind": trace_kind}
    if not code:
        code, reason = _decide(engine, verdict, recheck, reason)
    evidence: dict[str, Any] = {
        "kind": ("model_check_answer" if not code else "model_check_blocked" if engine else "model_check_refusal"),
        "operation": operation,
        "engine": engine,
        "engine_version": engine_version,
        "external_program_ran": engine in ("tlc", "sany"),
        **claimed,
        "depth": depth,
        "model": model or {},
        "recheck": recheck,
        "elapsed_s": elapsed_s,
    }
    if tla:
        evidence["tla"] = {k_: v for k_, v in tla.items() if k_ != "trace"}
    evidence |= extra or {}
    if code and code not in TRACE_CODES:
        if code == CERTIFICATE_REFUTED and trace:
            evidence["refuted_trace"] = trace
        trace, violated, trace_kind = [], [], ""
        if code not in REPORT_CODES:
            verdict, explored_depth, all_paths_end, k = "", None, False, None
    if code:
        evidence |= {"code": code, "reason": reason}
        card = WarningCard(code=code, message=reason, severity="blocked",
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    else:
        card = _card(engine, engine_version, verdict, depth, explored_depth, all_paths_end, k, model or {}, tla)
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return ModelCheckResult.model_validate({
        "operation": operation, "engine": engine, "engine_version": engine_version, "verdict": verdict,
        "depth": depth, "explored_depth": explored_depth, "all_paths_end": all_paths_end, "k": k,
        "violated": violated, "trace": trace, "trace_kind": trace_kind, "recheck": recheck, "model": model or {},
        "tla": tla, "code": code, "reason": reason, "elapsed_s": elapsed_s,
        "completion_state": "blocked-escalated" if code else "qualified-draft", "warning_card": card,
        "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _decide(engine: str, verdict: str, recheck: dict[str, Any], reason: str) -> tuple[str, str]:
    """The code a result with no refusal of its own earns from its verdict and its re-check ('' when it is a draft)."""
    if engine == "z3":
        if verdict not in SYMBOLIC_VERDICTS or not recheck:
            return NOT_CHECKED, "the answer carries no re-check by G6, so it is not reported"
        if not recheck.get("passed"):
            return CERTIFICATE_REFUTED, ("G6's re-check on fresh solvers contradicts the answer: "
                                         + "; ".join(recheck.get("failures", [])[:3]))
        if verdict in SYMBOLIC_FOUND:
            return SYMBOLIC_FOUND[verdict], reason or f"the model check found {verdict.replace('_', ' ')}"
        return "", ""
    if engine == "tlc":
        if verdict == "no_error":
            return "", ""
        return TLC_VERDICTS.get(verdict, TLC_UNRECOGNISED), reason or f"TLC reported {verdict.replace('_', ' ')}"
    if engine == "sany":
        return ("", "") if verdict == "no_error" else (SANY_ERRORS, reason or "SANY reported errors")
    return NOT_CHECKED, "nothing ran, so there is no answer to report"


def steps(n: int) -> str:
    return f"{n} step" if n == 1 else f"{n} steps"


def induction(k: int) -> tuple[str, str]:
    """The base case and the induction step of k-induction at ``k``, in words."""
    if k == 1:
        return "no initial state breaks them", "no state that keeps them steps to one that breaks them"
    return (f"no path of at most {steps(k - 1)} from the initial states breaks them",
            f"no {k} different states in a row that keep them are followed by one that breaks them")


def _names(model: dict[str, Any]) -> str:
    names = list(model.get("invariants", {}))
    return ", ".join(names) if names else "the invariants"


def _card(engine: str, version: str, verdict: str, depth: int | None, explored: int | None, ended: bool,
          k: int | None, model: dict[str, Any], tla: dict[str, Any]) -> dict[str, Any]:
    if engine == "tlc":
        return WarningCard(
            code=TLC_NO_ERROR, severity="amber",
            message=f"TLC {version} explored {tla.get('distinct_states')} distinct states ({tla.get('states_generated')}"
                    f" generated, depth {tla.get('depth')}) and reported no error for what the config asks it to "
                    "check. This is TLC's own verdict on your spec and config: what the config does not ask for was "
                    "not checked, and TLC can miss a state whose fingerprint collides with another's (its own "
                    f"estimate: {tla.get('fingerprint_collision') or 'not given'}). A model checker's run is not a "
                    "proof a person has inspected, so it is not verified.",
            suggested_action="Check that the config names every invariant and property you mean, and that the "
                             "constants are large enough to matter.",
        ).model_dump()
    if engine == "sany":
        return WarningCard(
            code=SANY_NO_ERROR, severity="amber",
            message=f"SANY {version} parsed module {tla.get('module', '')} and found no syntax, semantic or level "
                    "error. Nothing was model-checked, so this says nothing about what the spec does.",
            suggested_action="Run tlc_check to model-check it.",
        ).model_dump()
    who = f"Z3 {version}"
    if verdict == "k_inductive":
        base, step = induction(k or 1)
        return WarningCard(
            code=K_INDUCTION, severity="amber",
            message=f"{who} found {_names(model)} {k}-inductive (k-induction at k = {k}): {base}, and {step}. A second, "
                    f"fresh Z3 context decided both cases again. {_NOT_VERIFIED}",
            suggested_action="Check that the invariants and the transition say what you mean before relying on the "
                             "result.",
        ).model_dump()
    if verdict == "reached":
        length = explored if explored is not None else 0
        return WarningCard(
            code=TRACE_FOUND, severity="amber",
            message=f"{who} found a path of {steps(length)} from an initial state to a state meeting the goal "
                    f"{model.get('goal', '')}, and G6 checked it state by state on fresh solvers. {_NOT_VERIFIED}",
            suggested_action="Read the trace to confirm each step is one your system can take.",
        ).model_dump()
    what = {"no_violation_within_bound": f"found no state breaking {_names(model)}",
            "no_deadlock_within_bound": "found no state without a successor",
            "not_reached_within_bound": f"found no state meeting the goal {model.get('goal', '')}"}[verdict]
    if ended:
        return WarningCard(
            code=ALL_PATHS_END, severity="amber",
            message=f"{who} {what} on any path from the initial states, and every path from the initial states ends "
                    f"within {steps(explored or 0)} (a fresh Z3 context confirmed no path is longer and found none "
                    f"either), so the search covered every reachable state. {_NOT_VERIFIED}",
            suggested_action="Check that the paths are meant to end there: a model that stops early has few states "
                             "to check.",
        ).model_dump()
    further = {"no_violation_within_bound": "a violation further on", "no_deadlock_within_bound": "a deadlock further on",
               "not_reached_within_bound": "a longer path to the goal"}[verdict]
    return WarningCard(
        code=BOUNDED, severity="amber",
        message=f"{who} {what} on any path of up to {steps(depth or 0)} from the initial states, and a fresh Z3 "
                f"context confirmed it. That covers paths of up to {steps(depth or 0)} only: {further} is not ruled "
                f"out. {_NOT_VERIFIED}",
        suggested_action="Raise depth (at most 50), or for an invariant run prove_invariant, which covers every "
                         "reachable state when it succeeds.",
    ).model_dump()


__all__ = ["ALL_PATHS_END", "BOUNDED", "CERTIFICATE_REFUTED", "DEADLOCK", "DIGEST_ENV", "DIGEST_MISMATCH",
           "DIGEST_MISSING", "ENGINE_ERROR", "INPUT_INVALID", "INPUT_TOO_LARGE", "INSTALL_Z3", "JAR_ENV",
           "JAR_MISSING", "JAVA_ENV", "JAVA_MISSING", "K_INDUCTION", "MODULE_INVALID", "NOT_CHECKED", "NOT_INDUCTIVE",
           "NOT_RECHECKABLE", "OUTPUT_TOO_LARGE", "PARSE_ERROR", "REPORT_CODES", "RUN_FAILED", "Refusal",
           "SANY_ERRORS", "SANY_NO_ERROR", "SOLVER_ERROR", "SOLVER_UNAVAILABLE", "SOLVER_UNKNOWN", "TIME_LIMIT",
           "TIME_LIMIT_TOO_LONG", "TLC_ASSERTION_FAILED", "TLC_DEADLOCK", "TLC_EVALUATION_ERROR", "TLC_FAILED",
           "TLC_INVARIANT_VIOLATED", "TLC_NO_ERROR", "TLC_PROPERTY_VIOLATED", "TLC_SPEC_ERROR", "TLC_UNRECOGNISED",
           "TRACE_CODES", "TRACE_FOUND", "UNKNOWN_SORT", "VACUOUS_INIT", "VACUOUS_TRANSITION", "VIOLATION", "finish",
           "induction", "labelled", "steps"]
