# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.cognitive_bridge, and :func:`refusal`, the one place the package makes a refusal.

A refusal is ``blocked-escalated`` and says which of these it is, in ``code`` and in its ``warning_card``; the block's
refusals reach ``g6_invoke`` with their card, so the door shows the same code. Every other result is
``qualified-draft`` (:func:`settled`). ``tests/cognitive_bridge/test_refusal_inventory.py`` parses the package and
fails on any other place that writes a state; ``tests/cognitive_bridge/test_block_refusal_codes.py`` pins each code,
and the skill names each one.

:func:`claims` is the one rule for a claim of a state, at any depth of a value: no ``reliability_label``, no
``verified`` but false directly inside a warning card, and every nested result in the makers' format
(:func:`result_faults`, the one check of that format). Everything the bridge holds is plain JSON data, taken at the
boundary (``snapshot.plain``), so the rule walks JSON alone, reading each value's kind by the one leaf test the boundary
uses too (:func:`_read_as`): a leaf (:data:`LEAVES`, a number only when the bridge's encoder writes it), an object with
text keys (:data:`OBJECTS`) and an array (:data:`ARRAYS`); anything else is refused, its path named
(``tests/cognitive_bridge/test_claim_kinds.py``, ``test_json_rule_inventory.py``). It is applied to every
collaborator's answer as it returns (``snapshot.returned``), to every tool's answer the door hands on, and by both
makers to what they return (``tests/cognitive_bridge/test_claim_inventory.py``); a value that holds what is not JSON data
is refused as the boundary refuses it (:class:`NotPlainData`), and each maker returns what the boundary made of its
fields (``snapshot.plain``), sharing nothing with them. :func:`only_fields` adds a format's fields to it.
:func:`affirmed` is the one reading of a yes: ``True`` and nothing else
(``tests/cognitive_bridge/test_approval_inventory.py``).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from g6.core.door import said_state
from g6.core.reliability_envelope import ReliabilityEnvelope, ReliabilityLabel, WarningCard, warning_card
from g6.invariants import ERROR_CODE_PATTERN

#: The block refused its input: a field it does not take is ``G6_E_INPUT_VALIDATION`` at the door; this is the rest
#: (priors that do not sum to one, a probe that does not predict every hypothesis, a plan with no claim). From
#: Python, a value that is not plain JSON data where it enters the bridge (``snapshot.plain``), or an identity that is
#: not text, is refused under this code with its path.
INPUT = "G6_E_COGNITIVE_BRIDGE_INPUT"
#: An executor asked to run a plan without the caller's approval: nothing ran.
APPROVAL_REQUIRED = "G6_E_COGNITIVE_APPROVAL_REQUIRED"
#: A plan's steps differ from the canonical routing of its claims, or a compiled model no longer matches its pin:
#: nothing ran.
PLAN_TAMPERED = "G6_E_COGNITIVE_PLAN_TAMPERED"
#: The orchestrator was asked to run a plan that is not approved, not fully specified, or has no registry, or a
#: compilation it did not issue: nothing ran.
EXECUTION_NOT_APPROVED = "G6_E_COGNITIVE_EXECUTION_NOT_APPROVED"
#: An assessment blocked: a compilation, dossier or report the instance that makes it did not issue, a compiled step
#: not executed by this orchestrator, a critical check, a documentary section, a review or a conflict failed.
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


class FrozenMap(dict):
    """A read-only JSON object: a ``dict`` (so JSON, ``asdict`` and equality read it as one) on which every write
    raises. Only the boundary makes one (``snapshot``), from data it took."""
    __slots__ = ()

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("a snapshot cannot be changed; make a new value instead")

    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _refuse

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(frozenset(self.items()))

    def __copy__(self) -> FrozenMap:
        return self

    def __deepcopy__(self, memo: dict) -> FrozenMap:
        return self

    def __reduce__(self) -> tuple:
        return FrozenMap, (dict(self),)

    def __repr__(self) -> str:
        return f"FrozenMap({dict.__repr__(self)})"


#: How deep :func:`claims` reads a value, and how deep the boundary takes one, counted from where the value enters the
#: bridge: every walk takes its depth from its caller (``tests/cognitive_bridge/test_depth_inventory.py``), and
#: anything deeper is refused.
DEPTH = 64
#: The JSON leaves, by the value's exact type (never a subclass): text, a number, a flag and null.
LEAVES: tuple[type, ...] = (str, int, float, bool, type(None))
#: A JSON object, as the bridge holds it: a ``dict``, or the read-only :class:`FrozenMap` a record holds. Its keys are
#: text.
OBJECTS: tuple[type, ...] = (dict, FrozenMap)
#: A JSON array, as the bridge holds it: a ``list``, or the ``tuple`` a record holds.
ARRAYS: tuple[type, ...] = (list, tuple)


class NotPlainData(TypeError, ValueError):
    """A value refused where it enters the bridge: not plain JSON data, or not of the kind its place declares. It
    carries a registered code (:data:`INPUT`) and the path of what was refused. The boundary (``snapshot.plain``)
    raises it, and so does :func:`unclaimed` for a value that holds what is not JSON data: one refusal for one rule."""

    code = INPUT

    def __init__(self, path: str, why: str) -> None:
        super().__init__(f"[{INPUT}] {path}: {why}", path)

    @property
    def path(self) -> str:
        return self.args[1]

    def __str__(self) -> str:
        return self.args[0]


def _read_as(value: Any) -> str:
    """How a value is read as JSON data, by its exact type: the one leaf test, which :func:`claims` and the boundary's
    walk (``snapshot.plain``) both ask. ``"text"`` (a leaf, and the only kind an object's key may be), ``"leaf"`` (the
    rest of :data:`LEAVES`: a number only when the bridge's encoder writes it, so finite, and an integer no longer than
    the encoder writes out), ``"object"`` (:data:`OBJECTS`), ``"array"`` (:data:`ARRAYS`), or ``""``: anything else,
    which is not JSON data, so it is refused."""
    kind = type(value)
    if kind in LEAVES:
        if kind is LEAVES[0]:
            return "text"
        if kind is float and not math.isfinite(value):
            return ""
        if kind is int:
            try:
                int.__repr__(value)  # what the encoder writes an integer by; past Python's digit limit it raises
            except ValueError:
                return ""
        return "leaf"
    return "object" if kind in OBJECTS else "array" if kind in ARRAYS else ""


def _named(value: Any) -> str:
    """The type of ``value``, as a refusal names it."""
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _unread(value: Any) -> str:
    """What a value :func:`_read_as` does not read is, as a refusal names it."""
    kind = type(value)
    if kind is float:
        return "a number that is not finite"
    if kind is int:
        return "an integer longer than the bridge's encoder writes"
    return f"a {_named(value)}"


def _read_card(card: Any) -> dict[str, Any]:
    """A warning card as the bridge reads one, the one reading :func:`refusal` and :func:`result_faults` share: taken
    by the boundary first, in its raw form (``snapshot.plain``: a set, bytes or any other object is refused there,
    never converted), then read as a ``WarningCard`` in strict mode (no field converted from another kind). The card
    that reading makes, or ``ValueError`` (:class:`NotPlainData` for what is not JSON data)."""
    taken = _plain(card, where="a warning card")
    if type(taken) is not dict:
        raise ValueError("a warning card is a G6 warning card, a mapping")
    return WarningCard.model_validate(taken, strict=True).model_dump()


def _claims_nothing(card: Mapping[str, Any] | None) -> bool:
    """Whether ``card`` is a G6 warning card that claims nothing of its own: a mapping with no field G6 reads as a
    state and ``verified`` false (INV-ENV-2), that reads as a ``WarningCard`` (:func:`_read_card`) with a stable
    ``G6_E_*`` code."""
    if type(card) not in OBJECTS or set(card) & set(STATE_FIELDS) or card.get(VERIFIED_FLAG, False) is not False:
        return False
    try:
        read = _read_card(card)
    except ValueError:
        return False
    return bool(ERROR_CODE_PATTERN.match(read["code"]))


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
        faults = [] if type(code) is str and code in CODES and ERROR_CODE_PATTERN.match(code) else [
            f"{code!r} is not a code g6.cognitive_bridge registered"]
        if not _claims_nothing(card) or card.get("code") != code:
            faults.append(f"it has no warning card for its code {code!r} that claims nothing")
        return faults
    return [] if card is None or _claims_nothing(card) else ["its warning card claims more than a qualified draft"]


def claims(value: Any, *, _card: bool = False, _at: str = "", _depth: int = 0,
           _not_data: list[str] | None = None) -> list[str]:
    """Where ``value`` claims a reliability state the bridge did not decide, or holds what is not JSON data, each place
    named by its path; none is an empty list. It walks JSON alone (:func:`_read_as`, the boundary's own leaf test,
    decides how): a leaf (:data:`LEAVES`) is read as itself, an object (:data:`OBJECTS`, whose every key is text) and
    an array (:data:`ARRAYS`) item by item; anything else (a number the encoder does not write, a key that is not text
    among them) is named with what it is. What it finds: a field G6 reads as a state other than ``completion_state``
    (``reliability_label``) anywhere; G6's ``verified`` flag anywhere but directly inside a warning card, where it may
    only be false (INV-ENV-2); and an object that carries ``completion_state`` that is not a result in the makers'
    format (:func:`result_faults`). A value nested deeper than :data:`DEPTH`, counted from the depth its caller gives
    (``_depth``), is not read, and is named as a claim. Each place that is not JSON data is also added to
    ``_not_data`` when one is given."""
    where = _at or "the value"
    odd = _not_data if _not_data is not None else []
    if _depth > DEPTH:
        odd.append(where)
        return [f"{where} (nested deeper than {DEPTH} levels)"]
    how = _read_as(value)
    if how in ("text", "leaf"):
        return []
    if not how:
        odd.append(where)
        return [f"{where} ({_unread(value)}, which is not JSON data)"]
    found: list[str] = []
    if how == "object":
        if "completion_state" in value:
            found += [f"{where}: {fault}" for fault in result_faults(value)]
        for key, item in value.items():
            if _read_as(key) != "text":
                odd.append(where)
                found.append(f"{_at}.<{_named(key)} key>".lstrip(".") + " (the key, which is not text)")
                continue
            at = f"{_at}.{key}" if _at else key
            if key == VERIFIED_FLAG and not (_card and item is False):
                found.append(at)
            elif key in STATE_FIELDS and key != "completion_state":
                found.append(at)
            found += claims(item, _card=key == "warning_card", _at=at, _depth=_depth + 1, _not_data=odd)
    else:
        for index, item in enumerate(value):
            found += claims(item, _at=f"{_at}[{index}]", _depth=_depth + 1, _not_data=odd)
    return found


def unclaimed(value: Any, *, where: str, _depth: int = 0) -> Any:
    """``value`` when it claims no state and holds nothing but JSON data (:func:`claims`, counted from ``_depth``,
    where ``value`` stands), else an error naming each place: :class:`NotPlainData`, the boundary's refusal (its code,
    and ``where``), when it holds what is not JSON data, and ``ValueError`` when it only claims a state."""
    odd: list[str] = []
    found = claims(value, _depth=_depth, _not_data=odd)
    if found:
        why = f"claims a state the bridge did not decide, or holds what is not JSON data: {'; '.join(found)[:600]}"
        if odd:
            raise NotPlainData(where, why)
        raise ValueError(f"{where} {why}")
    return value


def affirmed(value: Any) -> bool:
    """Whether a yes the bridge reads (a person's approval, an admission or authentication hook's verdict, a door
    reply's ``ok``, a confirmed prerequisite) says yes: ``True``, and nothing else. A ``"false"``, a ``1``, a ``"yes"``
    or any other value that is merely truthy is not a yes."""
    return value is True


def _plain(value: Any, *, where: str, frozen: bool = False) -> Any:
    """``value`` as the boundary makes it, the one conversion (``snapshot.plain``, or read-only ``snapshot.snapshot``
    when ``frozen``): a new value that shares nothing with ``value``. The boundary imports this module, so it is
    imported where it is used."""
    from . import snapshot as boundary

    return boundary.snapshot(value, where=where) if frozen else boundary.plain(value, where=where)


def _text(value: Any, *, where: str) -> str:
    """``value`` as the boundary takes text (``snapshot.text``): text, and nothing else, never converted. The boundary
    imports this module, so it is imported where it is used."""
    from . import snapshot as boundary

    return boundary.text(value, where=where)


def only_fields(value: Any, *, allowed: tuple[str, ...], required: tuple[str, ...] = (),
                where: str) -> Mapping[str, Any]:
    """``value`` when it is a mapping with every ``required`` field, no field but ``allowed`` and no claim anywhere in it
    (:func:`claims`), else ``ValueError`` (:class:`NotPlainData` for what is not JSON data): what the bridge credits
    from a collaborator, and republishes, carries only its format's fields, and none of them claims a state the bridge
    did not decide. What it returns is what the boundary made of it, read-only."""
    if not isinstance(value, dict):
        raise ValueError(f"{where} is a mapping of its format's fields")
    missing, extra = sorted(set(required) - set(value)), sorted(set(value) - set(allowed), key=str)
    if missing or extra:
        raise ValueError(f"{where} has only its format's fields: missing {missing}, not in the format {extra}")
    return _plain(unclaimed(value, where=where), where=where, frozen=True)


def _no_reserved(values: Mapping[str, Any], where: str) -> None:
    taken = sorted(set(values) & set(RESERVED))
    if taken:
        raise ValueError(f"{where} may not set {', '.join(taken)}: only codes.refusal and codes.settled write them")


def _card(code: str, reason: str, card: Mapping[str, Any] | None) -> dict[str, Any]:
    """The warning card a refusal carries: a new one for ``code``, or a supplied card as the bridge reads one
    (:func:`_read_card`: the boundary first, then a strict ``WarningCard``). Whether it is a card for a registered code
    that claims nothing is :func:`result_faults`' to say."""
    return warning_card(code, reason) if card is None else _read_card(card)


def refusal(code: str, reason: str, *, card: Mapping[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """A ``blocked-escalated`` answer that says why, under ``code`` and in a warning card with the same code.

    ``code`` is one of :data:`CODES`; ``reason`` is text, taken as the boundary takes text (``snapshot.text``: a number,
    a set or bytes is refused, never converted); a supplied ``card`` is read by the boundary, then as a strict
    ``WarningCard`` (:func:`_read_card`), and must be a card for that code (:func:`result_faults`). ``extra`` may not
    set any of :data:`RESERVED`, and this function's own fields are written last. What it returns claims no state
    anywhere (:func:`unclaimed`) and is what the boundary made of it (``snapshot.plain``)."""
    _no_reserved(extra, "a refusal's extra fields")
    code = _text(code, where="a refusal's code")
    reason = _text(reason, where="a refusal's reason")[:400]
    out = {**extra, "completion_state": "blocked-escalated", "code": code, "reason": reason,
           "warning_card": _card(code, reason, card)}
    where = f"a refusal under {code!r}"
    return _plain(unclaimed(out, where=where), where=where)


def settled(refused: Mapping[str, Any] | None, *, draft_card: tuple[str, str] | None = None,
            **fields: Any) -> dict[str, Any]:
    """``fields`` under the state a result has: ``refused`` (a refusal :func:`refusal` made, in its format and
    unchanged, made again here so a changed state, code or card is refused), or ``qualified-draft``, with a card for a
    registered code when ``draft_card`` (``(code, message)``) is given. ``fields`` may not set any of :data:`RESERVED`.
    What it returns claims no state anywhere (:func:`unclaimed`) and is what the boundary made of it
    (``snapshot.plain``)."""
    _no_reserved(fields, "a result's fields")
    if refused is None:
        out = {**fields, "completion_state": "qualified-draft"}
        if draft_card is not None:
            if draft_card[0] not in CODES:
                raise ValueError(f"{draft_card[0]!r} is not a code g6.cognitive_bridge registered")
            out["warning_card"] = _card(draft_card[0], draft_card[1], None)
        return _plain(unclaimed(out, where="a result"), where="a result")
    if (draft_card is not None or not isinstance(refused, dict) or refused.get("completion_state") != STATES[1]
            or set(refused) & (set(RESERVED) - set(_MADE)) or result_faults(refused)):
        raise ValueError("settled takes a refusal codes.refusal made, unchanged, or None")
    rest = {k: v for k, v in refused.items() if k not in RESERVED and k != "reason"}
    return _plain(unclaimed({**refusal(refused["code"], refused.get("reason", ""), card=refused["warning_card"],
                                       **rest), **fields}, where="a result"), where="a result")


__all__ = ["ADAPTATION_REFUSED", "APPROVAL_REQUIRED", "ARRAYS", "ASSURANCE_BLOCKED", "CODES", "COMPILE_BLOCKED",
           "DEPTH", "DOSSIER_BLOCKED", "EVIDENCE_CONTESTED", "EVIDENCE_NOT_RECONCILED", "EVIDENCE_REFUTED",
           "EVIDENCE_UNDETERMINED", "EXECUTION_INCOMPLETE", "EXECUTION_NOT_APPROVED", "FrozenMap", "INPUT", "LEAVES",
           "MADE_FIELDS", "MODEL_MISSING", "NOT_PROMOTED", "NO_PROBE", "NotPlainData", "OBJECTS", "OBLIGATION_NOT_PLANNED",
           "PLAN_TAMPERED", "PREREQUISITE_UNMET",
           "RESERVED", "SELECTION_INCOMPLETE", "STATES", "STATE_FIELDS", "STEP_FAILED", "VERIFIED_FLAG", "affirmed",
           "claims", "only_fields", "refusal", "result_faults", "settled", "unclaimed"]
