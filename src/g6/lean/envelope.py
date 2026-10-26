# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.lean and :func:`finish`, the one place a result is labelled.

``finish`` decides the label from what the result records:

* a ``code`` (refused by the screen or the limits, Lean missing, out of time, a failed elaboration, a missing theorem,
  a ``def``, a statement that does not match, an axiom beyond Lean's standard three) is ``blocked-escalated``, and the
  result keeps what Lean reported, which is the evidence for the block;
* a proof check is ``qualified-draft`` (``G6_E_LEAN_PROOF_CHECKED``) only when Lean ran and reported a theorem by that
  name, its statement, and axioms that are all among ``propext``, ``Classical.choice`` and ``Quot.sound``, and, when
  an expected statement was given, that it matched; anything less is ``G6_E_LEAN_NOT_CHECKED``, whoever built it;
* ``lean_version`` is ``qualified-draft`` (``G6_E_LEAN_VERSION_READ``) when Lean answered with its version.

It never gives ``verified``: the result is re-validated through :class:`~g6.lean.schema.LeanResult`, which has no such
state, because a kernel-checked proof of a statement nobody has inspected says nothing about what was meant.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from g6.core.reliability_envelope import WarningCard
from g6.lean.schema import LeanResult

_P = "G6_E_LEAN_"
# qualified-draft cards
PROOF_CHECKED = _P + "PROOF_CHECKED"
VERSION_READ = _P + "VERSION_READ"
# refused before Lean runs
LEAN_MISSING = _P + "LEAN_MISSING"
SOURCE_REFUSED = _P + "SOURCE_REFUSED"
SORRY = _P + "SORRY"
CODE_EXECUTION = _P + "CODE_EXECUTION"
AXIOM_DECLARED = _P + "AXIOM_DECLARED"
SYNTAX_EXTENSION = _P + "SYNTAX_EXTENSION"
ATTRIBUTE_REFUSED = _P + "ATTRIBUTE_REFUSED"
OPTION_REFUSED = _P + "OPTION_REFUSED"
IMPORT_REFUSED = _P + "IMPORT_REFUSED"
COMMAND_REFUSED = _P + "COMMAND_REFUSED"
MARKER_IN_SOURCE = _P + "MARKER_IN_SOURCE"
STATEMENT_REFUSED = _P + "STATEMENT_REFUSED"
INPUT_INVALID = _P + "INPUT_INVALID"
INPUT_TOO_LARGE = _P + "INPUT_TOO_LARGE"
TIME_LIMIT_TOO_LONG = _P + "TIME_LIMIT_TOO_LONG"
# blocked on what Lean reported
ELABORATION_FAILED = _P + "ELABORATION_FAILED"
THEOREM_MISSING = _P + "THEOREM_MISSING"
NOT_A_THEOREM = _P + "NOT_A_THEOREM"
STATEMENT_MISMATCH = _P + "STATEMENT_MISMATCH"
NONSTANDARD_AXIOM = _P + "NONSTANDARD_AXIOM"
# blocked on the run itself
TIME_LIMIT = _P + "TIME_LIMIT"
OUTPUT_TOO_LARGE = _P + "OUTPUT_TOO_LARGE"
RUN_FAILED = _P + "RUN_FAILED"
OUTPUT_UNRECOGNISED = _P + "OUTPUT_UNRECOGNISED"
NOT_CHECKED = _P + "NOT_CHECKED"
ENGINE_ERROR = _P + "ENGINE_ERROR"

#: The screen's codes: each refused construct has its own (see :mod:`g6.lean.screen`).
SCREEN_CODES = (SOURCE_REFUSED, SORRY, CODE_EXECUTION, AXIOM_DECLARED, SYNTAX_EXTENSION, ATTRIBUTE_REFUSED,
                OPTION_REFUSED, IMPORT_REFUSED, COMMAND_REFUSED, MARKER_IN_SOURCE)

#: Lean's standard axioms: the only ones a passing proof may depend on.
STANDARD_AXIOMS = ("propext", "Classical.choice", "Quot.sound")

#: Where Lean comes from: ``G6_LEAN`` names the ``lean`` executable, or ``lean`` is found on PATH. G6 never installs it.
LEAN_ENV = "G6_LEAN"
INSTALL_LEAN = (f"Install Lean 4 with elan, Lean's toolchain manager, as the Lean project describes at lean-lang.org; "
                f"elan puts lean on PATH. Or set {LEAN_ENV} to a lean executable. G6 never installs Lean, a toolchain, "
                "Lake or Mathlib.")

_ACTIONS = {
    LEAN_MISSING: INSTALL_LEAN,
    SOURCE_REFUSED: "Remove the control and invisible characters at the places named: a person reading the proof must "
                    "see what Lean reads.",
    SORRY: "Finish the proof: sorry, admit and stop leave a goal unproved, and Lean accepts them with only a warning.",
    CODE_EXECUTION: "Remove what runs code (#eval, run_cmd, run_tac, IO, unsafe, implemented_by, extern, initialize, "
                    "native_decide); a proof needs none of it.",
    AXIOM_DECLARED: "Prove the fact instead of declaring it: an axiom, or an opaque constant, lets a file assume "
                    "anything, False included.",
    SYNTAX_EXTENSION: "Write the proof without new notation, macros or syntax: they change how Lean reads the file and "
                      "how it prints the statement you inspect.",
    ATTRIBUTE_REFUSED: "Use only the attributes the reason lists; the others change how Lean elaborates, compiles or "
                       "prints.",
    OPTION_REFUSED: "Use set_option only for the options the reason lists, within their caps; the others change what "
                    "Lean prints or checks.",
    IMPORT_REFUSED: "Import only Init and Std, which ship with Lean, one import line per module list; G6 never fetches "
                    "Mathlib or any other library.",
    COMMAND_REFUSED: "Remove the commands named: only #check, #check_failure, #reduce and #synth are allowed, and G6 "
                     "adds its own #print commands after your source.",
    MARKER_IN_SOURCE: "Remove the text G6_LEAN_CHECK from the source: G6 reserves it to mark its own checks.",
    STATEMENT_REFUSED: "Give expected_statement as one Lean term: no comments, strings, commands, sorry or unbalanced "
                       "brackets.",
    INPUT_INVALID: "Correct the input as the reason says and check again.",
    INPUT_TOO_LARGE: "Reduce the source, the statement or the limit below what the reason names.",
    TIME_LIMIT_TOO_LONG: "Give a time_limit of at most 600 seconds.",
    ELABORATION_FAILED: "Fix the errors at the lines and columns Lean names (the messages hold Lean's own text), then "
                        "check again.",
    THEOREM_MISSING: "Declare the theorem in your source with the theorem keyword, and give its full name, namespaces "
                     "included, as theorem. A theorem that comes from an import was not proved by your file.",
    NOT_A_THEOREM: "Declare it with theorem, not def, abbrev, instance or another keyword: only a theorem is a proof "
                   "of its statement.",
    STATEMENT_MISMATCH: "Compare the statement Lean printed with the one you expected and correct whichever is wrong. "
                        "They are compared with runs of whitespace collapsed, and Lean must also accept the theorem as a "
                        "proof of your expected statement read on its own.",
    NONSTANDARD_AXIOM: "Remove what brings in the axioms named (an axiom declaration, native_decide or a shortcut); a "
                       "passing proof may use only propext, Classical.choice and Quot.sound.",
    TIME_LIMIT: "Raise time_limit (at most 600 seconds) or simplify the proof.",
    OUTPUT_TOO_LARGE: "Lean's output passed its cap, so the run was stopped. Remove #check and #reduce commands that "
                      "print a lot, or raise max_output_bytes from Python.",
    RUN_FAILED: "Lean stopped before G6's checks ran (a crash, the memory cap, or a toolchain it could not start); "
                "inspect the reason and the end of its output.",
    OUTPUT_UNRECOGNISED: "Do not use the run: Lean's output does not fit what G6 reads, so it is not reported as a "
                         "check. The evidence holds the end of the output; report it with the source.",
    NOT_CHECKED: "Build results with g6.lean's functions; this one carries no report from Lean that G6 read.",
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
             run_id: str) -> LeanResult:
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


def finish(operation: str, *, lean_ran: bool = False, lean_version: str = "", lean_target: str = "",
           executable: str = "", theorem: str = "", kind: str = "", statement: str = "", statement_explicit: str = "",
           expected_statement: str = "", statement_matched: bool | None = None, axioms: list[str] | None = None,
           messages: list[dict[str, Any]] | None = None, refusals: list[dict[str, Any]] | None = None,
           code: str = "", reason: str = "", extra: dict[str, Any] | None = None, elapsed_s: float = 0.0,
           request_id: str = "", run_id: str = "") -> LeanResult:
    """``operation``'s outcome labelled: state, warning card, evidence and ids. Never ``verified``."""
    axioms = None if axioms is None else list(axioms)
    messages, refusals = list(messages or []), list(refusals or [])
    if not code:
        code, reason = _decide(operation, lean_ran, lean_version, kind, statement, axioms, expected_statement,
                               statement_matched)
    evidence: dict[str, Any] = {
        "kind": ("lean_blocked" if lean_ran else "lean_refusal") if code else
                ("lean_version" if operation == "lean_version" else "lean_proof_checked"),
        "operation": operation,
        "external_program_ran": lean_ran,
        "lean_version": lean_version,
        "lean_target": lean_target,
        "executable": executable,
        "theorem": theorem,
        "declaration_kind": kind,
        "statement": statement,
        "statement_explicit": statement_explicit,
        "expected_statement": expected_statement,
        "statement_matched": statement_matched,
        "axioms": axioms,
        "standard_axioms": list(STANDARD_AXIOMS),
        "refusals": refusals,
        "messages": messages,
        "elapsed_s": elapsed_s,
    }
    evidence |= extra or {}
    if code:
        evidence |= {"code": code, "reason": reason}
        card = WarningCard(code=code, message=reason, severity="blocked",
                           suggested_action=_ACTIONS.get(code, _ACTIONS[INPUT_INVALID])).model_dump()
    elif operation == "lean_version":
        card = WarningCard(
            code=VERSION_READ, severity="amber",
            message=f"Lean {lean_version} ({lean_target or 'target not given'}) answered from {executable}. This says "
                    "which Lean would check a proof; it checked nothing.",
            suggested_action="Run check_proof to have this Lean check a proof.",
        ).model_dump()
    else:
        card = _proof_card(lean_version, theorem, statement, statement_explicit, axioms or [], expected_statement)
    # Re-validated, not model_copy'd: the state passes through the schema, which has no ``verified``.
    return LeanResult.model_validate({
        "operation": operation, "lean_version": lean_version, "lean_target": lean_target, "executable": executable,
        "theorem": theorem, "kind": kind, "statement": statement, "statement_explicit": statement_explicit,
        "expected_statement": expected_statement, "statement_matched": statement_matched, "axioms": axioms,
        "messages": messages, "refusals": refusals, "code": code, "reason": reason, "elapsed_s": elapsed_s,
        "completion_state": "blocked-escalated" if code else "qualified-draft", "warning_card": card,
        "evidence": [evidence], "request_id": request_id, "run_id": run_id,
    })


def _decide(operation: str, lean_ran: bool, version: str, kind: str, statement: str, axioms: list[str] | None,
            expected: str, matched: bool | None) -> tuple[str, str]:
    """The code a result with no refusal of its own earns ('' when it is a draft)."""
    if not lean_ran:
        return NOT_CHECKED, "Lean did not run, so there is nothing it reported to label"
    if operation == "lean_version":
        return ("", "") if version else (NOT_CHECKED, "Lean ran but no version was read from it")
    if kind != "theorem" or not statement or axioms is None:
        return NOT_CHECKED, "the result carries no report from Lean of a theorem, its statement and its axioms"
    beyond = [a for a in axioms if a not in STANDARD_AXIOMS]
    if beyond:
        return NOT_CHECKED, f"the result names axioms beyond Lean's standard ones: {', '.join(beyond)}"
    if expected and matched is not True:
        return NOT_CHECKED, "an expected statement was given, but the result does not show that it matched"
    return "", ""


def _proof_card(version: str, theorem: str, statement: str, explicit: str, axioms: list[str],
                expected: str) -> dict[str, Any]:
    used = "no axioms at all" if not axioms else "only the axioms " + ", ".join(axioms)
    pinned = (" The expected statement you gave matched it as Lean prints it, and Lean accepted the theorem as a "
              "proof of your expected statement read on its own." if expected else
              " No expected statement was given, so nothing pinned the statement you meant: it is the theorem's own.")
    shown = f" With every name in full and every numeral's type shown, it reads: {explicit}." if explicit else ""
    return WarningCard(
        code=PROOF_CHECKED, severity="amber",
        message=f"Lean {version}'s kernel accepted a proof of {theorem} : {statement}, exactly this statement as Lean "
                f"reads it in your file, using {used} (Lean's standard axioms are propext, Classical.choice and "
                f"Quot.sound).{pinned}{shown} Whether the statement says what you mean, and what the definitions and "
                "instances it uses from your file say, is for a person to inspect; until then this is a qualified "
                "draft, not verified.",
        suggested_action="Have a person read the statement, and the definitions and instances it uses from your file, "
                         "before relying on the proof; give expected_statement to pin the statement you mean.",
    ).model_dump()


__all__ = ["ATTRIBUTE_REFUSED", "AXIOM_DECLARED", "CODE_EXECUTION", "COMMAND_REFUSED", "ELABORATION_FAILED",
           "ENGINE_ERROR", "IMPORT_REFUSED", "INPUT_INVALID", "INPUT_TOO_LARGE", "INSTALL_LEAN", "LEAN_ENV",
           "LEAN_MISSING", "MARKER_IN_SOURCE", "NONSTANDARD_AXIOM", "NOT_A_THEOREM", "NOT_CHECKED", "OPTION_REFUSED",
           "OUTPUT_TOO_LARGE", "OUTPUT_UNRECOGNISED", "PROOF_CHECKED", "RUN_FAILED", "Refusal", "SCREEN_CODES",
           "SORRY", "SOURCE_REFUSED", "STANDARD_AXIOMS", "STATEMENT_MISMATCH", "STATEMENT_REFUSED", "SYNTAX_EXTENSION",
           "THEOREM_MISSING", "TIME_LIMIT", "TIME_LIMIT_TOO_LONG", "VERSION_READ", "finish", "labelled"]
