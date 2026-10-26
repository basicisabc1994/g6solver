# SPDX-License-Identifier: Apache-2.0
"""Proof search for g6.lean: ``prove`` and ``sketch`` (the archive's ``lean_prover``, rewritten; finding LEAN_PROVER-01
to -05).

``prove(statement)`` writes ``theorem g6_goal : <statement> := <proof>`` itself, after your optional ``preamble``, for
each candidate in order: the core tactics ``rfl``, ``decide``, ``simp`` and ``omega`` (``by <tactic>``), then proofs an
injected ``g6.core.llm`` caller proposes, each told Lean's errors on the last failed candidate. Every candidate is
judged only by :func:`g6.lean.check_proof`, with your statement as the expected statement, and the first it passes is
the answer: ``qualified-draft``, the label ``check_proof`` gave, never more. A model's reply is only ever the text after
``:=``: one that declares its own theorem is refused before any check (the archive passed such text through as the
proof), and the screen refuses what runs code or leaves a gap before Lean starts. The search stops at the budget (at
most 32 candidates, model replies included), and at once on what no candidate can change: Lean missing, a statement
Lean prints otherwise or refuses, a limit out of range. A model that fails is recorded and the search goes on with
what is left. No Pantograph, Lake, Mathlib or download: only Lean, which is yours.

``sketch(statement)`` asks the injected caller for an informal outline and returns it as a draft: Lean checked
nothing, and nothing in it counts as a proof until ``check_proof`` or ``prove`` passes one.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.llm import CONFIGURE_MODEL, LLMCaller
from g6.core.reliability_envelope import WarningCard
from g6.lean import runner
from g6.lean.envelope import _ACTIONS as _LEAN_ACTIONS
from g6.lean.envelope import (
    ENGINE_ERROR,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    LEAN_MISSING,
    OUTPUT_UNRECOGNISED,
    PROOF_CHECKED,
    STANDARD_AXIOMS,
    STATEMENT_MISMATCH,
    STATEMENT_REFUSED,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
)
from g6.lean.proof import MAX_SOURCE_CHARS, MAX_STATEMENT_CHARS, check_proof
from g6.lean.schema import LeanResult
from g6.lean.screen import declarations, screen, screen_statement

_P = "G6_E_LEAN_"
PROOF_FOUND = _P + "PROOF_FOUND"
SEARCH_EXHAUSTED = _P + "SEARCH_EXHAUSTED"
PROPOSAL_REFUSED = _P + "PROPOSAL_REFUSED"
PROPOSAL_REPEATED = _P + "PROPOSAL_REPEATED"
NO_MODEL = _P + "NO_MODEL"
MODEL_FAILED = _P + "MODEL_FAILED"
SKETCH_UNCHECKED = _P + "SKETCH_UNCHECKED"

#: The core tactics, tried in this order before any model proposal.
CORE_TACTICS = ("rfl", "decide", "simp", "omega")
#: The name G6 gives the goal it writes.
GOAL = "g6_goal"
DEFAULT_BUDGET, MAX_BUDGET = 8, 32
#: One deadline for the whole search, model calls included; each check gets at most what is left of it.
DEFAULT_DEADLINE_S, MAX_DEADLINE_S = 600.0, 3600.0
MAX_PROOF_CHARS, MAX_SKETCH_CHARS = 4000, 8000
_clock = time.monotonic
#: What no other candidate can change: the search stops at once on these.
_STOP = frozenset({LEAN_MISSING, INPUT_INVALID, INPUT_TOO_LARGE, TIME_LIMIT_TOO_LONG, STATEMENT_REFUSED,
                   STATEMENT_MISMATCH, OUTPUT_UNRECOGNISED, ENGINE_ERROR})
_DECLARES = re.compile(r"^\s*(?:@\[[^\]\n]*\]\s*)?(?:(?:private|protected|noncomputable|unsafe|partial)\s+)*"
                       r"(?:theorem|lemma|def|example|abbrev|instance|axiom|opaque|structure|class|inductive)\b",
                       re.MULTILINE)
_FENCE = re.compile(r"```[A-Za-z0-9]*[ \t]*\n(.*?)```", re.DOTALL)
_PROPOSE = ("You propose Lean 4 proofs. Reply with only the text that goes after := in the theorem shown: a term, or "
            "a tactic block starting with by. No theorem line, no explanation, no sorry. Only Init and Std are "
            "available (no Mathlib). Lean, not you, decides whether it is a proof.")
_SKETCH = ("Outline, in plain words, how one might prove this Lean 4 statement. This is a draft for a person; it is "
           "not checked.")
Check = Callable[..., LeanResult]


class SearchResult(BaseModel):
    """One ``prove`` or ``sketch`` answer. ``completion_state`` cannot be ``verified``: a proof is at best what
    ``check_proof`` said of it (``check``, unchanged), and a sketch is a model's draft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["prove", "sketch"]
    theorem: str = ""
    statement: str = ""
    proof: str = ""
    source: str = ""
    draft: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    check: dict[str, Any] | None = None
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: Literal["qualified-draft", "blocked-escalated"] = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


_ACTIONS = {
    SEARCH_EXHAUSTED: "Raise the budget, give a model caller, or add the lemmas the proof needs to the preamble; the "
                      "candidates show what Lean said of each.",
    NO_MODEL: f"To give G6 a model, {CONFIGURE_MODEL}. Or pass a g6.core.llm caller (from Python, or "
              "LeanBlock(llm=...)); G6 never picks a model for you.",
    MODEL_FAILED: "Check the model caller you gave; the reason names its error.",
    TIME_LIMIT: f"Raise deadline_s (at most {MAX_DEADLINE_S:g} seconds), lower the budget, or simplify the statement.",
}


def _card(code: str, message: str, *, blocked: bool) -> dict[str, Any]:
    action = _ACTIONS.get(code) or _LEAN_ACTIONS.get(code) or "Correct what the reason names and run again."
    return WarningCard(code=code, message=message, severity="blocked" if blocked else "amber",
                       suggested_action=action).model_dump()


def _passed(result: Any, statement: str) -> bool:
    """Whether ``result`` is check_proof's pass for this goal and this statement, by Lean's own report."""
    if not isinstance(result, LeanResult):
        return False
    evidence = result.evidence[0] if result.evidence else {}
    return (result.completion_state == "qualified-draft" and not result.code and result.operation == "check_proof"
            and result.theorem == GOAL and result.kind == "theorem" and result.expected_statement == statement
            and result.statement_matched is True and result.axioms is not None
            and set(result.axioms) <= set(STANDARD_AXIOMS) and evidence.get("external_program_ran") is True
            and (result.warning_card or {}).get("code") == PROOF_CHECKED)


def _proof_body(reply: Any) -> tuple[str, str]:
    """``(proof, "")`` from a model's reply, or ``("", why)`` when it is no proof body G6 will write."""
    text = reply if isinstance(reply, str) else ""
    fenced = _FENCE.search(text)
    body = (fenced.group(1) if fenced else text).strip()
    body = body[2:].strip() if body.startswith(":=") else body
    if not body:
        return "", "the reply is empty"
    if len(body) > MAX_PROOF_CHARS:
        return "", f"the reply is longer than {MAX_PROOF_CHARS} characters"
    if _DECLARES.search(body):
        return "", "the reply declares its own theorem or definition; only the proof of your statement is taken"
    return body, ""


def _ask(llm: LLMCaller, statement: str, preamble: str, last: dict[str, Any] | None) -> str:
    shown = f"{preamble.strip()}\n\n" if preamble.strip() else ""
    user = f"{shown}theorem {GOAL} : {statement} := ?\n"
    if last:
        errors = "\n".join(m.get("text", "") for m in last.get("messages", []))[:2000]
        user += f"\nThe last candidate was:\n{last['proof']}\nLean said: {last['reason']}\n{errors}\n"
    return llm.complete(_PROPOSE, user, max_tokens=1024)


def _blocked(operation: str, code: str, reason: str, started: float, ids: dict[str, str], **fields: Any
             ) -> SearchResult:
    evidence = {"kind": f"lean_{operation}", "code": code, "reason": reason, **fields.pop("extra", {})}
    return SearchResult(operation=operation, code=code, reason=reason, completion_state="blocked-escalated",
                        warning_card=_card(code, reason, blocked=True), evidence=[evidence],
                        elapsed_s=round(time.perf_counter() - started, 6), **ids, **fields)


def prove(statement: str, *, llm: LLMCaller | None = None, budget: int = DEFAULT_BUDGET, preamble: str = "",
          tactics: tuple[str, ...] = CORE_TACTICS, time_limit: float = runner.DEFAULT_TIME_LIMIT_S,
          deadline_s: float = DEFAULT_DEADLINE_S, memory_mb: int = runner.DEFAULT_MEMORY_MB, request_id: str = "",
          run_id: str = "") -> SearchResult:
    """Search for a proof of ``statement`` (written as Lean prints it), judged only by :func:`check_proof`, within
    ``deadline_s`` seconds in all; each check gets ``time_limit`` or what is left, whichever is less."""
    return _prove(statement, check=check_proof, llm=llm, budget=budget, preamble=preamble, tactics=tactics,
                  time_limit=time_limit, deadline_s=deadline_s, memory_mb=memory_mb, request_id=request_id,
                  run_id=run_id)


def _prove(statement: Any, *, check: Check, llm: LLMCaller | None = None, budget: Any = DEFAULT_BUDGET,
           preamble: Any = "", tactics: tuple[str, ...] = CORE_TACTICS, time_limit: float = runner.DEFAULT_TIME_LIMIT_S,
           deadline_s: Any = DEFAULT_DEADLINE_S, memory_mb: int = runner.DEFAULT_MEMORY_MB, request_id: str = "",
           run_id: str = "") -> SearchResult:
    """:func:`prove` with the checker passed in, so tests can stand a double in for Lean."""
    started, ids = time.perf_counter(), {"request_id": request_id, "run_id": run_id}
    deadline = _clock() + (deadline_s if isinstance(deadline_s, int | float) and not isinstance(deadline_s, bool)
                           else 0)
    text = statement.strip() if isinstance(statement, str) else ""
    head = {"theorem": GOAL, "statement": text}
    if not text or len(text) > MAX_STATEMENT_CHARS:
        return _blocked("prove", INPUT_INVALID, f"statement is one Lean term of 1 to {MAX_STATEMENT_CHARS} "
                                                "characters, written as Lean prints it", started, ids, **head)
    if found := screen_statement(text):
        return _blocked("prove", STATEMENT_REFUSED, f"the statement cannot be written as one term: "
                                                    f"{found[0]['reason']}", started, ids, **head)
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= MAX_BUDGET:
        return _blocked("prove", INPUT_INVALID, f"budget is a whole number of candidates from 1 to {MAX_BUDGET}",
                        started, ids, **head)
    if isinstance(deadline_s, bool) or not isinstance(deadline_s, int | float) or not 0 < deadline_s <= MAX_DEADLINE_S:
        return _blocked("prove", INPUT_INVALID, f"deadline_s is a number of seconds above 0 and at most "
                                                f"{MAX_DEADLINE_S:g}", started, ids, **head)
    if not isinstance(preamble, str) or len(preamble) > MAX_SOURCE_CHARS // 2:
        return _blocked("prove", INPUT_INVALID, "preamble is Lean source text of at most "
                                                f"{MAX_SOURCE_CHARS // 2} characters", started, ids, **head)
    if found := screen(preamble):
        return _blocked("prove", found[0]["code"], f"G6 refuses the preamble before Lean runs: {found[0]['reason']}",
                        started, ids, **head)
    if any(d["name"] == GOAL for d in declarations(preamble)) or not set(tactics) <= set(CORE_TACTICS):
        return _blocked("prove", INPUT_INVALID, f"the preamble may not declare {GOAL}, the name G6 gives the goal, "
                                                f"and tactics are among {', '.join(CORE_TACTICS)}", started, ids,
                        **head)
    lead = f"{preamble.rstrip()}\n\n" if preamble.strip() else ""
    tried: list[dict[str, Any]] = []
    seen: set[str] = set()
    model = "none given" if llm is None else type(llm).__name__

    def out_of_time(left: float | None = None) -> SearchResult | None:
        if (deadline - _clock() if left is None else left) > 0:
            return None
        return _blocked("prove", TIME_LIMIT, f"the search's deadline of {deadline_s:g} s passed after {len(tried)} "
                                             "candidate(s)", started, ids, candidates=list(tried),
                        extra={"candidates": len(tried), "model": model}, **head)

    def attempt(proof: str, origin: str) -> SearchResult | None:
        left = deadline - _clock()   # read once: a second read could leave the check no time, which it refuses
        if late := out_of_time(left):
            return late
        source = f"{lead}theorem {GOAL} : {text} := {proof}\n"
        seen.add(proof)
        result = check(source, GOAL, expected_statement=text, time_limit=min(time_limit, left), memory_mb=memory_mb,
                       **ids)
        ran = bool(result.evidence and result.evidence[0].get("external_program_ran"))
        tried.append({"origin": origin, "proof": proof, "code": result.code, "lean_ran": ran,
                      "completion_state": result.completion_state, "reason": result.reason[:300],
                      "messages": [m for m in result.messages if m.get("severity") == "error"][:3]})
        if _passed(result, text):
            message = (f"check_proof passed the {origin} proof {proof!r} of {GOAL} : {text} after {len(tried)} "
                       f"candidate(s). This is its label, a qualified draft: whether the statement says what you "
                       "mean is for a person to inspect.")
            return SearchResult(operation="prove", proof=proof, source=source, candidates=list(tried),
                                check=result.model_dump(mode="json"), completion_state="qualified-draft",
                                warning_card=_card(PROOF_FOUND, message, blocked=False),
                                evidence=[{"kind": "lean_prove", "code": PROOF_FOUND, "candidates": len(tried),
                                           "model": model, "judged_by": "g6.lean.check_proof"}],
                                elapsed_s=round(time.perf_counter() - started, 6), **ids, **head)
        if result.code in _STOP:
            return _blocked("prove", result.code, result.reason, started, ids, candidates=list(tried),
                            extra={"candidates": len(tried), "model": model}, **head)
        return None

    for tactic in tactics[:budget]:
        if done := attempt(f"by {tactic}", "core tactic"):
            return done
    while llm is not None and len(tried) < budget:
        if late := out_of_time():
            return late
        try:
            proof, why = _proof_body(_ask(llm, text, lead, tried[-1] if tried else None))
        except Exception as exc:  # noqa: BLE001 -- a model that fails is recorded; the search ends with what it has
            model = f"{MODEL_FAILED}: {type(exc).__name__}: {exc}"[:300]
            break
        if why or proof in seen:
            tried.append({"origin": "model", "proof": proof, "code": PROPOSAL_REPEATED if not why else
                          PROPOSAL_REFUSED, "lean_ran": False, "completion_state": "blocked-escalated",
                          "reason": why or "the model proposed a candidate already tried", "messages": []})
            continue
        if done := attempt(proof, "model"):
            return done
    if late := out_of_time():   # the deadline passed during the last check: that is the timeout, not exhaustion
        return late
    return _blocked("prove", SEARCH_EXHAUSTED, f"no candidate of {len(tried)} passed check_proof for {GOAL} : {text}",
                    started, ids, candidates=tried, extra={"candidates": len(tried), "model": model}, **head)


def sketch(statement: str, *, llm: LLMCaller | None = None, request_id: str = "", run_id: str = "") -> SearchResult:
    """A model's informal outline for ``statement``: a draft, never a proof."""
    started, ids = time.perf_counter(), {"request_id": request_id, "run_id": run_id}
    text = statement.strip() if isinstance(statement, str) else ""
    if not text or len(text) > MAX_STATEMENT_CHARS:
        return _blocked("sketch", INPUT_INVALID, f"statement is 1 to {MAX_STATEMENT_CHARS} characters", started, ids)
    if llm is None:
        return _blocked("sketch", NO_MODEL, "no model caller was given, so there is no draft", started, ids,
                        statement=text)
    try:
        draft = str(llm.complete(_SKETCH, text, max_tokens=1024))[:MAX_SKETCH_CHARS].strip()
    except Exception as exc:  # noqa: BLE001 -- reported, never raised through g6_invoke
        return _blocked("sketch", MODEL_FAILED, f"the model caller failed: {type(exc).__name__}: {exc}"[:300],
                        started, ids, statement=text)
    message = ("A model's draft of how the statement might be proved. Lean checked nothing; nothing in it counts as a "
               "proof until check_proof or prove passes one.")
    return SearchResult(operation="sketch", statement=text, draft=draft, completion_state="qualified-draft",
                        warning_card=_card(SKETCH_UNCHECKED, message, blocked=False),
                        evidence=[{"kind": "lean_sketch", "code": SKETCH_UNCHECKED, "model": type(llm).__name__,
                                   "lean_ran": False}],
                        elapsed_s=round(time.perf_counter() - started, 6), **ids)


__all__ = ["CORE_TACTICS", "DEFAULT_BUDGET", "GOAL", "MAX_BUDGET", "MODEL_FAILED", "NO_MODEL", "PROOF_FOUND",
           "PROPOSAL_REFUSED", "PROPOSAL_REPEATED", "SEARCH_EXHAUSTED", "SKETCH_UNCHECKED", "SearchResult", "prove",
           "sketch"]
