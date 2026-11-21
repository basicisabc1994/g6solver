# SPDX-License-Identifier: Apache-2.0
"""Diagnosis by a discriminating check (plan revision 8, phase C, item 1).

After a review reproduces a defect, or in the diagnose phase, ``g6_select`` offers a ``g6_review_patch`` template
(``TEMPLATE``) whose one proposed check carries at least two competing explanations, each predicting a different
classification of that check's run (``verification_search.classify``). The calling agent is the semantic engine: it
writes the check and may restate the explanations. G6 calls no model here.

``explanations`` validates what a proposed check states, before anything runs: 2 to ``MAX_EXPLANATIONS`` objects
``{id, said, predicts}``, each ``predicts`` a classification a check of that intent can have (``PREDICTABLE``), no two
the same; a set that does not tell its explanations apart is refused. ``check_error`` is never a prediction: it
decides nothing.

``read`` reads the observed classification against the stated predictions, deterministically, through
``g6.harness.workflow.diagnose.Explanation.predicts``: each explanation is ``consistent`` (it predicted what was
observed), ``inconsistent`` (the check ran to another result) or ``not_decided`` (the check ended in a check error,
was skipped or stopped). The words are "consistent with", never "cause": one run of a check the caller proposed
changes no outcome and no requirement, and the review stays qualified-draft.
"""
from __future__ import annotations

from typing import Any

#: The classifications ``verification_search.classify`` gives a free-form proposed check of each intent.
PREDICTABLE = {"preserve": ("counterexample", "fails_on_base_too", "not_refuted", "behaviour_changed"),
               "change": ("unconfirmed_failure", "not_refuted", "behaviour_changed")}
MAX_EXPLANATIONS = 4
MAX_SAID = 300
CONSISTENT, INCONSISTENT, NOT_DECIDED = "consistent", "inconsistent", "not_decided"
NOTE = ("Consistent with is not the cause: this is one run of a check the caller proposed, and an explanation nobody "
        "stated may predict the same result. It changes no outcome and no requirement.")
SHAPE = "explanations must be a list of 2 to 4 objects {id, said, predicts}"

#: What ``g6_select`` offers to fill and run: one ``change`` check of what the task asks for the case the review
#: reproduced, and two explanations that predict different results of it (``diagnose.WORDS``' own two for an
#: accepted check that fails on the patch).
REQUIREMENT = {"id": "diagnosis", "checks": [],
               "text": "<what the task asks of the case the reproduced failure is about>"}
CHECK = {"id": "discriminate", "requirement_id": "diagnosis", "intent": "change", "file": "tests/test_g6_discriminate.py",
         "source": "<a pytest test of what the task asks for that case, written from the task and not from the patch>",
         "test": "",
         "explanations": [
             {"id": "patch_wrong_for_case", "said": "the patch is wrong for this case",
              "predicts": "unconfirmed_failure"},
             {"id": "check_stale_or_wrong", "said": "the reproduced test expects behaviour the task changes, so the "
                                                    "test is stale or wrong", "predicts": "not_refuted"}]}
TEMPLATE = {"requirements": [REQUIREMENT], "proposed_checks": [CHECK]}
FILL = ["requirements.0.text", "proposed_checks.0.source"]
SAY = ("Before repairing, tell the explanations apart: fill requirements.0.text with what the task asks of the case the "
       "review reproduced, and proposed_checks.0.source with a pytest test of it written from the task, not the patch "
       "(name your own stated requirement in requirement_id if you have one). Each explanation predicts a different "
       "result of that check; restate them if you see others, each with a different predicts. G6 reads the result "
       "against them in assessment.explanations: consistent with, never the cause.")


def placeholder(text: str) -> bool:
    """Whether ``text`` is still a template's placeholder: one line, in angle brackets."""
    stripped = text.strip()
    return stripped.startswith("<") and stripped.endswith(">") and "\n" not in stripped


def explanations(value: Any, intent: str) -> tuple[dict[str, str], ...]:
    """``value`` as a check's stated explanations, or a ``ValueError`` in G6's words (module docstring)."""
    allowed = PREDICTABLE[intent]
    if not isinstance(value, (list, tuple)) or not 2 <= len(value) <= MAX_EXPLANATIONS:
        raise ValueError(f"{SHAPE}: competing explanations, at least two, each predicting a different result")
    found: list[dict[str, str]] = []
    for index, item in enumerate(value):
        where = f"explanations.{index}"
        if not isinstance(item, dict) or set(item) != {"id", "said", "predicts"}:
            raise ValueError(f"{where}: {SHAPE}")
        ident, said, predicts = item["id"], item["said"], item["predicts"]
        if not (isinstance(ident, str) and 1 <= len(ident) <= 100 and ident.strip() == ident):
            raise ValueError(f"{where}: id is a name of 1 to 100 characters")
        if not (isinstance(said, str) and said.strip() and len(said) <= MAX_SAID):
            raise ValueError(f"{where}: said says the explanation in at most {MAX_SAID} characters")
        if predicts not in allowed:
            raise ValueError(f"{where}: predicts is one of {', '.join(allowed)} for a check whose intent is "
                             f"{intent} (a check_error decides nothing, so it is never a prediction)")
        found.append({"id": ident, "said": said, "predicts": predicts})
    if len({one["id"] for one in found}) != len(found):
        raise ValueError("explanation ids must be unique within a check")
    seen: dict[str, str] = {}
    for one in found:
        if one["predicts"] in seen:
            raise ValueError(f"explanations {seen[one['predicts']]} and {one['id']} both predict {one['predicts']}: "
                             "this check cannot tell them apart; give each a different predicts")
        seen[one["predicts"]] = one["id"]
    return tuple(found)


def read(stated: tuple[dict[str, str], ...], observed: str, *, check: str, requirement: str,
         intent: str) -> dict[str, Any]:
    """The observed classification of one check's run, read against its stated explanations (module docstring)."""
    from g6.harness.workflow.diagnose import Explanation

    held = [(Explanation(one["id"], one["id"], one["predicts"]), one["said"]) for one in stated]
    decided = observed in PREDICTABLE[intent]
    rows = [{"id": e.id, "said": said, "predicts": e.predicts,
             "reading": NOT_DECIDED if not decided else CONSISTENT if e.predicts == observed else INCONSISTENT}
            for e, said in held]
    fits = [row["id"] for row in rows if row["reading"] == CONSISTENT]
    if not decided:
        summary = f"The check ended {observed}, which decides nothing: no explanation is read for or against."
    elif fits:
        others = [row["id"] for row in rows if row["reading"] == INCONSISTENT]
        summary = (f"The check's result, {observed}, is consistent with {', '.join(fits)}"
                   + (f" and not with {', '.join(others)}." if others else "."))
    else:
        summary = (f"The check's result, {observed}, is consistent with none of the stated explanations: one nobody "
                   "stated may hold.")
    return {"check": check, "requirement": requirement, "intent": intent, "observed": observed,
            "explanations": rows, "consistent_with": fits, "summary": summary, "note": NOTE}


__all__ = ["CHECK", "CONSISTENT", "FILL", "INCONSISTENT", "MAX_EXPLANATIONS", "NOTE", "NOT_DECIDED", "PREDICTABLE",
           "SAY", "TEMPLATE", "explanations", "placeholder", "read"]
