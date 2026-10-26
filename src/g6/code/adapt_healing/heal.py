# SPDX-License-Identifier: Apache-2.0
"""heal: classify a failing step, have the injected caller propose a patch, check it on the shown tests, and judge it
once on held-back tests the caller never saw.

1. **Reproduce and classify.** Run the original on shown tests; classify the observed exception, not the claim. ``g6.triage.classify`` names the error's class from its type (the last ``Type: message`` line of the
   error or traceback) and its posture. Only ``auto_heal`` is repaired without a person: an error that touches the
   world outside the code (a file, the network, memory), an unknown class, or a message that points elsewhere is
   refused (``G6_E_HEAL_NEEDS_REVIEW``), and no model is asked.
2. **Propose.** The caller gets the code, the error, the triage hint and the shown tests, and, after a failed round,
   the shown tests' failures. It never gets a held-back test, its expression or its answer. At most three rounds.
3. **Check.** Each proposal must parse (it is parsed here, never run here), then runs with the shown tests in
   ``g6.core.sandbox``. A round whose shown tests all pass ends the loop.
4. **Judge.** The proposal that passed the shown tests is judged once on the held-back tests (:func:`.cases.judge`):
   every one must pass, or the repair is ``blocked-escalated`` (``G6_E_HEAL_HELD_BACK_FAILED``) and nothing is fed
   back. A repair that passes is a ``qualified-draft``: held-back tests the caller wrote are evidence, not proof.

A proposal that spells out a literal the shown tests expect, which the original code did not hold, is named on the
card: it may have been fitted to the examples.
"""
from __future__ import annotations

import ast
import re
from typing import Any

from g6.code._types import CodeRefused
from g6.code.adapt_healing import cases
from g6.core.llm import LLMError
from g6.triage import classify

NO_MODEL = "G6_E_HEAL_NO_MODEL"
NEEDS_REVIEW = "G6_E_HEAL_NEEDS_REVIEW"
NO_HELD_BACK = "G6_E_HEAL_NO_HELD_BACK"
LEAKED = "G6_E_HEAL_HELD_BACK_SHOWN"
SHOWN_FAILED = "G6_E_HEAL_SHOWN_TESTS_FAIL"
HELD_BACK_FAILED = "G6_E_HEAL_HELD_BACK_FAILED"
MODEL_FAILED = "G6_E_HEAL_MODEL_FAILED"
MAX_ROUNDS = 3
MAX_PATCH_CHARS = 200_000
SYSTEM = ("You repair one piece of Python code. Reply with the complete corrected code in one ```python block and "
          "nothing else. Fix the cause of the error; do not special-case the inputs the tests use.")
_FENCE = re.compile(r"```[ \t]*(?:python|py)?[ \t]*\r?\n(.*?)```", re.DOTALL)
_ERROR = re.compile(r"^(?:\w+\.)*(\w+(?:Error|Exception|Exit|Interrupt|Warning)|\w+)\s*:\s*(.*)$")
#: Literals too common to say anything when a patch spells them.
_PLAIN = {0, 1, -1, True, False, None, "", 2}


def error_parts(error: str) -> tuple[str, str]:
    """The exception's type and message: the last ``Type: message`` line, or a bare type name."""
    lines = [line.strip() for line in error.strip().splitlines() if line.strip()]
    for line in reversed(lines):
        found = _ERROR.match(line)
        if found and not line.startswith(("File ", "Traceback")):
            return found.group(1), found.group(2)
    return (lines[-1].split()[0], "") if lines else ("", "")


def proposed_code(reply: str) -> str:
    """The first fenced Python block of a reply, else the whole reply."""
    blocks = _FENCE.findall(reply or "")
    return (blocks[0] if blocks else reply or "").strip() + "\n"


def _who(caller: Any) -> str:
    model = getattr(caller, "model", None)
    return f"{type(caller).__name__}({model})" if model else type(caller).__name__


def _constants(code: str) -> list[Any]:
    try:
        return [n.value for n in ast.walk(ast.parse(code)) if isinstance(n, ast.Constant)]
    except (SyntaxError, ValueError):
        return []


def _spelled(patch: str, original: str, expected: list[Any]) -> list[str]:
    """Shown answers the patch spells out that the original did not."""
    before = {repr(v) for v in _constants(original)}
    wanted = {repr(v) for v in expected if not (isinstance(v, (int, float, str, bool)) and v in _PLAIN)}
    return sorted((wanted & {repr(v) for v in _constants(patch)}) - before)


def heal(code: str, error: str, tests_shown: tuple[str, ...], tests_held_back: tuple[str, ...], caller: Any, *,
         rounds: int, timeout_s: float, max_tokens: int = 4096) -> dict[str, Any]:
    """The repair, its rounds and the judgement (see the module docstring); raises :class:`CodeRefused` for every
    outcome that is not a repair to report."""
    cases.check_bounds(tests_shown, tests_held_back)
    shown = cases.shown(tests_shown)
    held = cases.held_back(tests_held_back)
    if not held:
        raise CodeRefused(NO_HELD_BACK, "give tests_held_back: without tests the caller never sees, a repair could be "
                                        "judged only on tests it was fitted to")
    leaked = [t.index for t in held if t.shape in cases.shapes_shown(shown)]
    if leaked:
        raise CodeRefused(LEAKED, f"held-back test(s) {', '.join(map(str, leaked))} compare an expression a shown test "
                                  "also uses, so the caller would have seen it")
    original = cases.run_shown(code, shown, timeout_s)
    reproduction = {"kind": "original_run", "run": original["run"], "loaded": original["loaded"],
                    "failures": original["failures"], "error": original["error"],
                    "caller_error": {"kind": "claim", "text": error[:4000]}}
    errors = ([original["error"]] if original["error"] else []) + [f["said"] for f in original["failures"]]
    observed = []
    for text in errors:
        kind, message = error_parts(text)
        if kind:
            observed.append((text, kind, classify(kind, message, text)))
    if not observed or not shown:
        raise CodeRefused(NEEDS_REVIEW, "the claimed error was not reproduced on the shown tests; a person must "
                                        "supply a reproducing case", [reproduction])
    error, kind, triage = next((entry for entry in observed if entry[2].posture != "auto_heal"), observed[0])
    seen = {"kind": "triage", "by": "g6.triage.classify", "source": "original_run",
            "error_type": kind, "category": triage.category, "reproduction": reproduction,
            "posture": triage.posture, "reasons": list(triage.reasons), "hint": triage.hint}
    if triage.posture != "auto_heal":
        why = ", ".join(r.replace("_", " ") for r in triage.reasons) or f"posture {triage.posture}"
        raise CodeRefused(NEEDS_REVIEW, f"the error is {triage.category} ({why}): a person looks at it before any fix "
                                        "is tried", [seen], triage=seen)
    if caller is None:
        raise CodeRefused(NO_MODEL, "no model was given to the block (over MCP: no model is configured), so no patch "
                                    "was proposed", [seen], triage=seen)
    history: list[dict[str, Any]] = []
    feedback = ""
    candidate = None
    for round_number in range(1, rounds + 1):
        user = (f"CODE:\n{code}\n\nERROR:\n{error.strip()[:4000]}\n\nHINT: {triage.hint}\n\nTESTS THE FIX MUST PASS:\n"
                + "\n".join(shown) + (f"\n\nYOUR LAST PROPOSAL FAILED:\n{feedback}" if feedback else ""))
        try:
            reply = str(caller.complete(SYSTEM, user, max_tokens=max_tokens))
        except LLMError as exc:
            raise CodeRefused(MODEL_FAILED, f"{_who(caller)} did not answer in round {round_number}: "
                                            f"{str(exc)[:300]}", [seen, *history], triage=seen, rounds=history) from None
        patch = proposed_code(reply)
        entry: dict[str, Any] = {"round": round_number, "by": _who(caller), "chars": len(patch)}
        if len(patch) > MAX_PATCH_CHARS:
            entry["outcome"], feedback = "too_large", f"the proposal is over {MAX_PATCH_CHARS} characters"
            history.append(entry)
            continue
        try:
            ast.parse(patch)
        except (SyntaxError, ValueError) as exc:
            entry["outcome"], feedback = "does_not_parse", f"SyntaxError: {exc}"
            history.append(entry)
            continue
        ran = cases.run_shown(patch, shown, timeout_s) if shown else {"loaded": True, "failures": [], "passed": 0,
                                                                       "error": "", "run": {}}
        entry.update(shown_passed=ran["passed"], shown_failed=len(ran["failures"]), run=ran["run"])
        if ran["loaded"] and not ran["failures"]:
            entry["outcome"] = "shown_tests_pass"
            history.append(entry)
            candidate = patch
            break
        entry["outcome"] = "shown_tests_fail"
        history.append(entry)
        feedback = (ran["error"] + "\n" if not ran["loaded"] else "") + "\n".join(
            f"{f['test']}  ->  {f['said']}" for f in ran["failures"][:10])
    if candidate is None:
        raise CodeRefused(SHOWN_FAILED, f"no proposal passed the shown tests in {len(history)} round(s); the held-back "
                                        "tests were not run", [seen, *history], triage=seen, rounds=history)
    verdict = cases.judge(candidate, held, timeout_s)
    judged = {"kind": "held_back_judgement", "by": "G6, comparing in its own process values the code computed in "
                                                   "g6.core.sandbox", "tests": len(held),
              "passed": sum(v["passed"] for v in verdict["verdicts"]), "seen_by_caller": False,
              "run": verdict["run"]}
    failed = [v for v in verdict["verdicts"] if not v["passed"]]
    if not verdict["loaded"] or failed or len(verdict["verdicts"]) != len(held):
        why = verdict["error"] or (f"held-back test {failed[0]['held_back']}: {failed[0]['why']}" if failed
                                   else "not every held-back test reported")
        raise CodeRefused(HELD_BACK_FAILED, f"the proposal passed the shown tests but not the held-back ones ({why}); "
                                            "it is not a repair", [seen, *history, judged], triage=seen,
                          rounds=history, held_back=verdict["verdicts"], patched=candidate)
    spelled = _spelled(candidate, code, cases.expected_literals(shown))
    return {"patched": candidate, "triage": seen, "rounds": history, "held_back": verdict["verdicts"],
            "spelled_answers": spelled, "evidence": [seen, *history, judged]}


__all__ = ["HELD_BACK_FAILED", "LEAKED", "MAX_ROUNDS", "MODEL_FAILED", "NEEDS_REVIEW", "NO_HELD_BACK", "NO_MODEL",
           "SHOWN_FAILED", "SYSTEM", "error_parts", "heal", "proposed_code"]
