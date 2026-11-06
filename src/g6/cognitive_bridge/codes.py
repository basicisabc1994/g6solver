# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.cognitive_bridge, and :func:`refusal`, the one place the package makes a refusal.

A refusal is ``blocked-escalated`` and says which of these it is, in ``code`` and in its ``warning_card``; the block's
refusals reach ``g6_invoke`` with their card, so the door shows the same code. Every other result is
``qualified-draft`` (:func:`settled`). ``tests/cognitive_bridge/test_refusal_inventory.py`` parses the package and
fails on any other place that writes a state; ``tests/cognitive_bridge/test_block_refusal_codes.py`` pins each code,
and the skill names each one. :func:`only_fields` is the one rule for what a collaborator's answer may carry: its
format's fields and no claim of a state (``tests/cognitive_bridge/test_credit_inventory.py``).
"""
from __future__ import annotations

from collections.abc import Mapping
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


def claims(value: Any, *, _in_card: bool = False, _at: str = "") -> list[str]:
    """Where ``value`` claims a reliability state the bridge did not decide: a field G6 reads as a state other than a
    ``completion_state`` of one of :data:`STATES`, or G6's ``verified`` flag anywhere but a warning card, where it may
    only be false (INV-ENV-2). Each place is named by its path; none is an empty list."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            at = f"{_at}.{key}" if _at else str(key)
            if key == VERIFIED_FLAG and not (_in_card and item is False):
                found.append(at)
            elif key in STATE_FIELDS and (key != "completion_state" or item not in STATES):
                found.append(at)
            found += claims(item, _in_card=_in_card or key == "warning_card", _at=at)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found += claims(item, _in_card=_in_card, _at=f"{_at}[{index}]")
    return found


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
    found = claims(value)
    if found:
        raise ValueError(f"{where} claims a state the bridge did not decide: {', '.join(found)}")
    return value


def _no_reserved(values: Mapping[str, Any], where: str) -> None:
    taken = sorted(set(values) & set(RESERVED))
    if taken:
        raise ValueError(f"{where} may not set {', '.join(taken)}: only codes.refusal and codes.settled write them")


def _card(code: str, reason: str, card: Mapping[str, Any] | None) -> dict[str, Any]:
    """The warning card for a registered ``code``: a new one, or a supplied card for that same code that claims
    nothing (no field G6 reads as a state, ``verified`` false)."""
    if code not in CODES or not ERROR_CODE_PATTERN.match(str(code)):
        raise ValueError(f"{code!r} is not a code g6.cognitive_bridge registered")
    if card is None:
        return warning_card(code, reason)
    if (not isinstance(card, Mapping) or card.get("code") != code or set(card) & set(STATE_FIELDS)
            or card.get(VERIFIED_FLAG, False) is not False):
        raise ValueError(f"a supplied card must be a warning card for the registered code {code!r} that claims "
                         "nothing of its own")
    return WarningCard.model_validate(dict(card)).model_dump()


def refusal(code: str, reason: str, *, card: Mapping[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """A ``blocked-escalated`` answer that says why, under ``code`` and in a warning card with the same code.

    ``code`` is one of :data:`CODES`; a supplied ``card`` must be a card for that code. ``extra`` may not set any of
    :data:`RESERVED`, and this function's own fields are written last."""
    _no_reserved(extra, "a refusal's extra fields")
    reason = str(reason)[:400]
    made = _card(code, reason, card)
    return {**extra, "completion_state": "blocked-escalated", "code": code, "reason": reason, "warning_card": made}


def settled(refused: Mapping[str, Any] | None, *, draft_card: tuple[str, str] | None = None,
            **fields: Any) -> dict[str, Any]:
    """``fields`` under the state a result has: ``refused`` (a refusal :func:`refusal` made, made again here so a
    changed state, code or card is refused), or ``qualified-draft``, with a card for a registered code when
    ``draft_card`` (``(code, message)``) is given. ``fields`` may not set any of :data:`RESERVED`."""
    _no_reserved(fields, "a result's fields")
    if refused is None:
        out = {**fields, "completion_state": "qualified-draft"}
        if draft_card is not None:
            out["warning_card"] = _card(draft_card[0], draft_card[1], None)
        return out
    if (draft_card is not None or not isinstance(refused, Mapping) or refused.get("completion_state") != STATES[1]
            or set(refused) & (set(RESERVED) - set(_MADE))):
        raise ValueError("settled takes a refusal codes.refusal made, unchanged, or None")
    rest = {k: v for k, v in refused.items() if k not in RESERVED and k != "reason"}
    return {**refusal(refused.get("code", ""), str(refused.get("reason", "")), card=refused.get("warning_card"),
                      **rest), **fields}


__all__ = ["ADAPTATION_REFUSED", "APPROVAL_REQUIRED", "ASSURANCE_BLOCKED", "CODES", "COMPILE_BLOCKED",
           "DOSSIER_BLOCKED", "EVIDENCE_CONTESTED", "EVIDENCE_NOT_RECONCILED", "EVIDENCE_REFUTED",
           "EVIDENCE_UNDETERMINED", "EXECUTION_INCOMPLETE", "EXECUTION_NOT_APPROVED", "INPUT", "MADE_FIELDS",
           "MODEL_MISSING", "NOT_PROMOTED", "NO_PROBE", "OBLIGATION_NOT_PLANNED", "PLAN_TAMPERED", "PREREQUISITE_UNMET",
           "RESERVED", "SELECTION_INCOMPLETE", "STATES", "STATE_FIELDS", "STEP_FAILED", "VERIFIED_FLAG", "claims",
           "only_fields", "refusal", "settled"]
