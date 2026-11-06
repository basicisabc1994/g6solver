# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.cognitive_bridge, and :func:`refusal`, the one place the package makes a refusal.

A refusal is ``blocked-escalated`` and says which of these it is, in ``code`` and in its ``warning_card``; the block's
refusals reach ``g6_invoke`` with their card, so the door shows the same code. Every other result is
``qualified-draft`` (:func:`settled`). ``tests/cognitive_bridge/test_refusal_inventory.py`` parses the package and
fails on any other place that writes a state; ``tests/cognitive_bridge/test_block_refusal_codes.py`` pins each code,
and the skill names each one.

:func:`claims` is the one rule for a claim of a state, at any depth of a value: no ``reliability_label``, no
``verified`` but false directly inside a warning card, and every nested result in the makers' format
(:func:`result_faults`, the one check of that format). It is applied to every collaborator's answer as it returns
(``snapshot.returned``), to every tool's answer the door hands on, and by both makers to what they return
(``tests/cognitive_bridge/test_claim_inventory.py``). :func:`only_fields` adds a format's fields to it. :func:`affirmed`
is the one reading of a yes: ``True`` and nothing else (``tests/cognitive_bridge/test_approval_inventory.py``).
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Set
from typing import Any

from g6.core.door import said_state
from g6.core.reliability_envelope import ReliabilityEnvelope, ReliabilityLabel, WarningCard, warning_card
from g6.invariants import ERROR_CODE_PATTERN

#: The block refused its input: a field it does not take is ``G6_E_INPUT_VALIDATION`` at the door; this is the rest
#: (priors that do not sum to one, a probe that does not predict every hypothesis, a plan with no claim).
INPUT = "G6_E_COGNITIVE_BRIDGE_INPUT"
#: An executor asked to run a plan without the caller's approval: nothing ran.
APPROVAL_REQUIRED = "G6_E_COGNITIVE_APPROVAL_REQUIRED"
#: A plan's steps differ from the canonical routing of its claims, or a compiled model no longer matches its pin:
#: nothing ran.
PLAN_TAMPERED = "G6_E_COGNITIVE_PLAN_TAMPERED"
#: The orchestrator was asked to run a plan that is not approved, not fully specified, or has no registry: nothing ran.
EXECUTION_NOT_APPROVED = "G6_E_COGNITIVE_EXECUTION_NOT_APPROVED"
#: An assessment blocked: a compiled step not executed by this orchestrator, a critical check, a documentary section,
#: a review or a conflict failed.
ASSURANCE_BLOCKED = "G6_E_COGNITIVE_ASSURANCE_BLOCKED"
#: An assessment passed the checks it ran in this process; no G6 door re-ran them and no outside custody held them.
NOT_PROMOTED = "G6_E_COGNITIVE_NOT_PROMOTED"
#: The selector found no affordable variant for a mandatory method, or no affordable portfolio for all of them.
SELECTION_INCOMPLETE = "G6_E_COGNITIVE_SELECTION_INCOMPLETE"
#: A required (professional) method is not among the claims' evidence needs.
OBLIGATION_NOT_PLANNED = "G6_E_COGNITIVE_OBLIGATION_NOT_PLANNED"
#: An evidence need has no typed model, so no step can be planned for it.
MODEL_MISSING = "G6_E_COGNITIVE_MODEL_MISSING"
#: A step's prerequisite was not established (a causal estimate without identification): it did not run.
PREREQUISITE_UNMET = "G6_E_COGNITIVE_PREREQUISITE_UNMET"
#: A step's call raised, G6's door refused it or did not pass its answer (the door's own card is kept under ``door``),
#: or it answered with something that is not an answer: it is not counted as run.
STEP_FAILED = "G6_E_COGNITIVE_STEP_FAILED"
#: An executed plan had a blocked step or a missing model: the run as a whole is not complete.
EXECUTION_INCOMPLETE = "G6_E_COGNITIVE_EXECUTION_INCOMPLETE"
#: The compiled plan is not complete: a mandatory method has no selected variant or no typed model.
COMPILE_BLOCKED = "G6_E_COGNITIVE_COMPILE_BLOCKED"
#: An assurance dossier has a blocker: a critical check, a control, a documentary section or a review.
DOSSIER_BLOCKED = "G6_E_COGNITIVE_DOSSIER_BLOCKED"
#: The adaptation gate refused to recommend a guard change.
ADAPTATION_REFUSED = "G6_E_COGNITIVE_ADAPTATION_REFUSED"
#: Admitted assertions support and refute the same proposition and scope.
EVIDENCE_CONTESTED = "G6_E_COGNITIVE_EVIDENCE_CONTESTED"
#: Admitted assertions only refute the proposition (``g6.epistemic_integrity``'s REFUTED).
EVIDENCE_REFUTED = "G6_E_COGNITIVE_EVIDENCE_REFUTED"
#: Admitted assertions neither support nor refute the proposition.
EVIDENCE_UNDETERMINED = "G6_E_COGNITIVE_EVIDENCE_UNDETERMINED"
#: A reconciliation as a whole: an assertion was not admitted, or a proposition is contested, refuted or undetermined.
EVIDENCE_NOT_RECONCILED = "G6_E_COGNITIVE_EVIDENCE_NOT_RECONCILED"
#: The research agent found no affordable, executable, discriminating probe.
NO_PROBE = "G6_E_COGNITIVE_NO_PROBE"

CODES: tuple[str, ...] = (INPUT, APPROVAL_REQUIRED, PLAN_TAMPERED, EXECUTION_NOT_APPROVED, ASSURANCE_BLOCKED,
                          NOT_PROMOTED, SELECTION_INCOMPLETE, OBLIGATION_NOT_PLANNED, MODEL_MISSING, PREREQUISITE_UNMET,
                          STEP_FAILED, EXECUTION_INCOMPLETE, COMPILE_BLOCKED, DOSSIER_BLOCKED, ADAPTATION_REFUSED,
                          EVIDENCE_CONTESTED, EVIDENCE_REFUTED, EVIDENCE_UNDETERMINED, EVIDENCE_NOT_RECONCILED,
                          NO_PROBE)
#: The two states the bridge gives a result (it never gives ``verified``).
STATES: tuple[str, ...] = ("qualified-draft", "blocked-escalated")
#: The name of G6's own flag, which only G6's warning card carries (INV-ENV-2); the one place the package names it.
VERIFIED_FLAG = "verified"


def _state_fields() -> tuple[str, ...]:
    """The fields G6 reads as a result's reliability state, taken from G6's own definitions, never a copied list: the
    fields the door's reader asks a result for (``g6.core.door.said_state``, asked here which ones it reads) and the
    envelope's fields typed as a reliability label (``ReliabilityEnvelope`` fields of type ``ReliabilityLabel``)."""
    asked: list[str] = []

    class _Asked(dict):
        def get(self, key: Any, default: Any = None) -> Any:
            asked.append(key)
            return default

    said_state(_Asked())
    labelled = [name for name, field in ReliabilityEnvelope.model_fields.items() if field.annotation == ReliabilityLabel]
    return tuple(dict.fromkeys([*asked, *labelled]))


#: Every field G6's door or envelope reads as a reliability state (``completion_state``, ``reliability_label``, ...).
STATE_FIELDS: tuple[str, ...] = _state_fields()
#: The fields only :func:`refusal` and :func:`settled` write: their own state, code and card, every field G6 reads as a
#: state, and G6's flag. A caller's value for any of them is refused, never taken.
RESERVED: tuple[str, ...] = tuple(dict.fromkeys(("completion_state", "code", "warning_card", *STATE_FIELDS,
                                                 VERIFIED_FLAG)))
#: What :func:`refusal` itself writes; any other reserved field in a refusal handed back is a forgery.
_MADE = ("completion_state", "code", "warning_card")
#: The fields :func:`refusal` and :func:`settled` write beside a format's own: a format's answer may carry these too.
MADE_FIELDS = (*_MADE, "reason")


#: How deep :func:`claims` reads a value; anything nested deeper is a place it did not read, so it is refused.
DEPTH = 64


def _claims_nothing(card: Mapping[str, Any] | None) -> bool:
    """Whether ``card`` is a G6 warning card that claims nothing of its own: a mapping with a stable ``G6_E_*`` code
    that reads as a ``WarningCard``, no field G6 reads as a state, and ``verified`` false (INV-ENV-2)."""
    if (not isinstance(card, Mapping) or not ERROR_CODE_PATTERN.match(str(card.get("code", "")))
            or set(card) & set(STATE_FIELDS) or card.get(VERIFIED_FLAG, False) is not False):
        return False
    try:
        WarningCard.model_validate(dict(card))
    except ValueError:
        return False
    return True


def result_faults(result: Mapping[str, Any]) -> list[str]:
    """Why ``result`` (a mapping that carries ``completion_state``) is not a result in the format :func:`refusal` and
    :func:`settled` make, or ``[]`` when it is: the one check of that format, which the makers and :func:`claims` both
    read. A ``blocked-escalated`` result carries a code g6.cognitive_bridge registered (:data:`CODES`) and a warning
    card for that code that claims nothing; a ``qualified-draft`` one claims nothing more: a card, if it has one,
    claims nothing. Any other ``completion_state`` is not one of the bridge's states."""
    state, card = result.get("completion_state"), result.get("warning_card")
    if state not in STATES:
        return [f"completion_state {state!r} is not one of the bridge's states"]
    if state == STATES[1]:
        code = result.get("code")
        faults = [] if code in CODES and ERROR_CODE_PATTERN.match(str(code)) else [
            f"{code!r} is not a code g6.cognitive_bridge registered"]
        if not _claims_nothing(card) or card.get("code") != code:
            faults.append(f"it has no warning card for its code {code!r} that claims nothing")
        return faults
    return [] if card is None or _claims_nothing(card) else ["its warning card claims more than a qualified draft"]


def claims(value: Any, *, _card: bool = False, _at: str = "", _depth: int = 0) -> list[str]:
    """Where ``value`` claims a reliability state the bridge did not decide, each place named by its path; none is an
    empty list. Every mapping, sequence, set and record (field by field) in it is read: a field G6 reads as a state
    other than ``completion_state`` (``reliability_label``) anywhere; G6's ``verified`` flag anywhere but directly
    inside a warning card, where it may only be false (INV-ENV-2); and a mapping that carries ``completion_state``
    that is not a result in the makers' format (:func:`result_faults`). A value nested deeper than :data:`DEPTH` is
    not read, and is named as a claim."""
    where = _at or "the value"
    if _depth > DEPTH:
        return [f"{where} (nested deeper than {DEPTH} levels)"]
    if getattr(type(value), "__snapshot_record__", False):
        value = {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
    found: list[str] = []
    if isinstance(value, Mapping):
        if "completion_state" in value:
            found += [f"{where}: {fault}" for fault in result_faults(value)]
        for key, item in value.items():
            at = f"{_at}.{key}" if _at else str(key)
            if key == VERIFIED_FLAG and not (_card and item is False):
                found.append(at)
            elif key in STATE_FIELDS and key != "completion_state":
                found.append(at)
            found += claims(item, _card=key == "warning_card", _at=at, _depth=_depth + 1)
    elif isinstance(value, (list, tuple, Set)):
        for index, item in enumerate(value):
            found += claims(item, _at=f"{_at}[{index}]", _depth=_depth + 1)
    return found


def unclaimed(value: Any, *, where: str) -> Any:
    """``value`` when it claims no state (:func:`claims`), else ``ValueError`` naming each place it does."""
    found = claims(value)
    if found:
        raise ValueError(f"{where} claims a state the bridge did not decide: {'; '.join(found)[:600]}")
    return value


def affirmed(value: Any) -> bool:
    """Whether a yes the bridge reads (a person's approval, an admission or authentication hook's verdict, a door
    reply's ``ok``, a confirmed prerequisite) says yes: ``True``, and nothing else. A ``"false"``, a ``1``, a ``"yes"``
    or any other value that is merely truthy is not a yes."""
    return value is True


def only_fields(value: Any, *, allowed: tuple[str, ...], required: tuple[str, ...] = (),
                where: str) -> Mapping[str, Any]:
    """``value`` when it is a mapping with every ``required`` field, no field but ``allowed`` and no claim anywhere in it
    (:func:`claims`), else ``ValueError``: what the bridge credits from a collaborator, and republishes, carries only
    its format's fields, and none of them claims a state the bridge did not decide."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} is a mapping of its format's fields")
    missing, extra = sorted(set(required) - set(value)), sorted(set(value) - set(allowed), key=str)
    if missing or extra:
        raise ValueError(f"{where} has only its format's fields: missing {missing}, not in the format {extra}")
    return unclaimed(value, where=where)


def _no_reserved(values: Mapping[str, Any], where: str) -> None:
    taken = sorted(set(values) & set(RESERVED))
    if taken:
        raise ValueError(f"{where} may not set {', '.join(taken)}: only codes.refusal and codes.settled write them")


def _card(code: str, reason: str, card: Mapping[str, Any] | None) -> dict[str, Any]:
    """The warning card a refusal carries: a new one for ``code``, or a supplied card read as a ``WarningCard``. Whether
    it is a card for a registered code that claims nothing is :func:`result_faults`' to say."""
    if card is None:
        return warning_card(str(code), reason)
    if not isinstance(card, Mapping):
        raise ValueError("a supplied card is a G6 warning card, a mapping")
    return WarningCard.model_validate(dict(card)).model_dump()


def refusal(code: str, reason: str, *, card: Mapping[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """A ``blocked-escalated`` answer that says why, under ``code`` and in a warning card with the same code.

    ``code`` is one of :data:`CODES`; a supplied ``card`` must be a card for that code (:func:`result_faults`).
    ``extra`` may not set any of :data:`RESERVED`, and this function's own fields are written last. What it returns
    claims no state anywhere (:func:`unclaimed`)."""
    _no_reserved(extra, "a refusal's extra fields")
    reason = str(reason)[:400]
    out = {**extra, "completion_state": "blocked-escalated", "code": code, "reason": reason,
           "warning_card": _card(code, reason, card)}
    return unclaimed(out, where=f"a refusal under {code!r}")


def settled(refused: Mapping[str, Any] | None, *, draft_card: tuple[str, str] | None = None,
            **fields: Any) -> dict[str, Any]:
    """``fields`` under the state a result has: ``refused`` (a refusal :func:`refusal` made, in its format and
    unchanged, made again here so a changed state, code or card is refused), or ``qualified-draft``, with a card for a
    registered code when ``draft_card`` (``(code, message)``) is given. ``fields`` may not set any of :data:`RESERVED`.
    What it returns claims no state anywhere (:func:`unclaimed`)."""
    _no_reserved(fields, "a result's fields")
    if refused is None:
        out = {**fields, "completion_state": "qualified-draft"}
        if draft_card is not None:
            if draft_card[0] not in CODES:
                raise ValueError(f"{draft_card[0]!r} is not a code g6.cognitive_bridge registered")
            out["warning_card"] = _card(draft_card[0], draft_card[1], None)
        return unclaimed(out, where="a result")
    if (draft_card is not None or not isinstance(refused, Mapping) or refused.get("completion_state") != STATES[1]
            or set(refused) & (set(RESERVED) - set(_MADE)) or result_faults(refused)):
        raise ValueError("settled takes a refusal codes.refusal made, unchanged, or None")
    rest = {k: v for k, v in refused.items() if k not in RESERVED and k != "reason"}
    return unclaimed({**refusal(refused["code"], str(refused.get("reason", "")), card=refused["warning_card"], **rest),
                      **fields}, where="a result")


__all__ = ["ADAPTATION_REFUSED", "APPROVAL_REQUIRED", "ASSURANCE_BLOCKED", "CODES", "COMPILE_BLOCKED", "DEPTH",
           "DOSSIER_BLOCKED", "EVIDENCE_CONTESTED", "EVIDENCE_NOT_RECONCILED", "EVIDENCE_REFUTED",
           "EVIDENCE_UNDETERMINED", "EXECUTION_INCOMPLETE", "EXECUTION_NOT_APPROVED", "INPUT", "MADE_FIELDS",
           "MODEL_MISSING", "NOT_PROMOTED", "NO_PROBE", "OBLIGATION_NOT_PLANNED", "PLAN_TAMPERED", "PREREQUISITE_UNMET",
           "RESERVED", "SELECTION_INCOMPLETE", "STATES", "STATE_FIELDS", "STEP_FAILED", "VERIFIED_FLAG", "affirmed",
           "claims", "only_fields", "refusal", "result_faults", "settled", "unclaimed"]
