# SPDX-License-Identifier: Apache-2.0
"""Verification search for G6's patch review, part 1: what the task asks, what it must keep, and what it assumes.

``requirements_from_intent`` maps an intent contract (``g6.intent_engine.schema.IntentContract``) that the caller
passes onto the review's requirements. Its items count as caller-stated:

- the caller's own requirements come first, unchanged (``stated``, of kind ``requirement``);
- each success criterion becomes a ``stated`` requirement, ``intent.success_criteria.<index>``, with no check, so it
  is not established until a check is stated for it (by a caller's requirement with the same text, below);
- each non-goal and each failure criterion becomes a ``stated`` obligation to keep behaviour (kind ``preserve``),
  ``intent.non_goals.<index>`` or ``intent.failure_criteria.<index>``, not established without a check;
- text that is the same as an earlier normative requirement's, up to case, spacing and a closing full stop, is merged
  into that requirement (its ``merged_from`` lists the item), never doubled; nothing is merged into a requirement no one
  stated;
- the success and failure checks are left out, each named in an unknown. The ruling: an intent's ``Check`` is a
  predicate on the text of an answer (contains, absent, a bounded regular expression, a word count). It names no
  repository test, so none is a check of the patch;
- each assumption keeps its source (``explicit``, ``inferred`` or ``fallback``) and goes into the scope as an
  assumption the assessment depends on. An ``explicit`` one is normative. None becomes a requirement or gets a status;
- the other fields that hold items (constraints, declared risks, guardrails, checkpoints, breakpoints, subtasks) are
  not mapped, and each that holds any is named in an unknown.

Ids come from the field and the index, so the same intent gives the same ids on every call. Nothing here calls a model,
runs a check or reads the task's text (``IntentElicitor`` is not used). With no intent, the patch review does not import
this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from g6.harness.patch_review import NORMATIVE, Requirement
from g6.intent_engine.schema import Assumption, IntentContract

#: The intent fields mapped to requirements, with the kind each item becomes.
MAPPED = (("success_criteria", "requirement"), ("non_goals", "preserve"), ("failure_criteria", "preserve"))
#: The intent's checks: predicates on an answer's text, never a check of a patch (the ruling above).
CHECKS = ("success_checks", "failure_checks")
#: Fields that hold items but are not part of a patch's assessment: each that holds any is named in an unknown.
UNMAPPED = ("constraints", "declared_risks", "guardrails", "checkpoints", "breakpoints", "subtasks")
ASSUMED = "an assumption the assessment depends on: it is not a requirement, gets no status and decides nothing"


@dataclass(frozen=True)
class IntentMapping:
    """The requirements (the caller's, then the intent's), the assumptions for the scope, and the unknowns."""
    requirements: list[Requirement]
    assumptions: list[dict]
    unknowns: list[str]


def intent_contract(value) -> IntentContract:
    """``value`` as an ``IntentContract``, or a ``ValueError`` that says what is wrong. A field the contract does not
    have is refused (pydantic would drop it, and a misspelt ``non_goals`` would lose an obligation unseen)."""
    if isinstance(value, IntentContract):
        return value
    if not isinstance(value, dict):
        raise ValueError("an intent is an object (an intent contract)")
    unknown = sorted(set(value) - set(IntentContract.model_fields))
    if unknown:
        raise ValueError("unknown intent field(s): " + ", ".join(map(str, unknown)))
    for index, item in enumerate(value.get("assumptions") or []):
        extra = sorted(set(item) - set(Assumption.model_fields)) if isinstance(item, dict) else []
        if extra:
            raise ValueError(f"assumptions.{index}: unknown field(s): " + ", ".join(map(str, extra)))
    try:
        return IntentContract.model_validate(value)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(".".join(str(part) for part in first["loc"]) + f": {first['msg']}") from exc


def _same(text: str) -> str:
    """Text as compared for merging: case, spacing and a closing full stop do not count."""
    return " ".join(text.split()).rstrip(". ").casefold()


def requirements_from_intent(intent: IntentContract, requirements: list[Requirement]) -> IntentMapping:
    """The review's requirements with ``intent``'s items mapped in (the module's rules); a ``ValueError`` when an item
    cannot be a requirement (empty, or too long)."""
    found = list(requirements)
    seen: dict[str, int] = {}
    for index, req in enumerate(found):
        if req.provenance in NORMATIVE:
            seen.setdefault(_same(req.text), index)
    for name, kind in MAPPED:
        for index, text in enumerate(getattr(intent, name)):
            ident, key = f"intent.{name}.{index}", _same(text)
            if not key:
                raise ValueError(f"{ident} is empty")
            if key in seen:
                earlier = found[seen[key]]
                found[seen[key]] = earlier.model_copy(update={"merged_from": [*earlier.merged_from, ident]})
                continue
            try:
                found.append(Requirement(id=ident, text=text, kind=kind))
            except ValidationError as exc:
                raise ValueError(f"{ident}: {exc.errors()[0]['msg']}") from exc
            seen[key] = len(found) - 1
    unknowns = [f"intent.{name} for {criterion!r} is a {check.kind} check on the text of an answer and names no "
                "repository test, so G6 did not use it as a check of this patch"
                for name in CHECKS for criterion, check in getattr(intent, name).items()]
    unknowns += [f"intent.{name} holds {len(items)} item(s) G6 does not assess a patch against: state any the patch "
                 "must meet as a requirement, with the checks that test it" for name in UNMAPPED
                 if (items := getattr(intent, name))]
    assumptions = [{"id": f"intent.assumptions.{index}", "statement": item.statement, "source": item.source,
                    "normative": item.source == "explicit", "note": ASSUMED}
                   for index, item in enumerate(intent.assumptions)]
    return IntentMapping(found, assumptions, unknowns)
