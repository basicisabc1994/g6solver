# SPDX-License-Identifier: Apache-2.0
"""g6.core.door — the rule every door applies to what a component says about its own result (plan A1).

``verified`` stands only when G6's own code re-runs a check at the door and reproduces it; a witness the producer
hands over is ignored. The door picks G6's re-checker by where the code that produced the result lives (a registry
entry's built-in package and block class, a pipeline stage's class), never by a name; the re-checker takes what to
compare against from the caller's own input, never from the output it judges, and declines a result that carries any
claim it does not examine. The promotion law re-runs it (``g6.core.reliability_envelope.door_witness``). Anything else
a component calls ``verified`` is held at ``qualified-draft`` with a ``G6_E_PROMOTION_LAW`` card, and
``claimed_completion_state`` keeps what it said; a re-check that finds a mismatch blocks it. A door never raises a
state, and every word in a held result that reads as ``verified`` is corrected in the copy it hands on.

G6 re-checks artifacts with its own code; it does not defend against malicious code in its own process.

The doors: the MCP server's ``g6_invoke``, the ``g6 invoke`` command, an agent's tool calls
(``g6.agents._runtime.tools``) and a navigator plan's steps reply through :func:`door_reply`; a pipeline of blocks, a
bundle's result and the harness build their envelopes with :func:`door_envelope`. ``get_registry().invoke`` is the raw
library call beneath them: it returns a component's own answer, unchecked.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from g6.core.reliability_envelope import PROMOTION_LAW, door_witness, envelope_fields, recheck_evidence
from g6.core.reliability_envelope import warning_card as new_card

_RANK = {"verified": 0, "qualified-draft": 1, "blocked-escalated": 2}
#: What G6 does and does not defend against, said wherever the door rule is read (plan A1).
THREAT_STATEMENT = ("G6 re-checks artifacts with its own code; it does not defend against malicious code in its own "
                    "process.")
#: What a held result's card tells its reader to do.
HELD_NEXT_STEP = "Treat it as a qualified draft: check it yourself, or use a component whose results G6 re-checks."
_CODE = re.compile(r"^\[(G6_E_[A-Z0-9_]+)\]\s*")
#: How deep the result copy is corrected; anything nested deeper is replaced by a note, never handed on unread.
_MAX_DEPTH = 32


def card(found: Any, code: str, message: str) -> dict[str, Any]:
    """``found`` when it is a warning card with a stable ``G6_E_*`` code, else a new card with ``code``."""
    from g6.invariants import ERROR_CODE_PATTERN

    if isinstance(found, Mapping) and ERROR_CODE_PATTERN.match(str(found.get("code", ""))):
        return dict(found)
    return new_card(code, message)


def state_of(word: Any) -> str | None:
    """The state a reliability word reads as, the way the product's own readers take it (``workforce.contracts``):
    any ``blocked*`` (or ``failed`` / ``error``) is a block, ``verified`` or ``verified_ready*`` is verified, any
    ``qualified*`` is a draft; None for anything else, or a word that is not a string."""
    if not isinstance(word, str):
        return None
    norm = word.strip().lower().replace("-", "_")
    if norm.startswith("blocked") or norm in {"failed", "error"}:
        return "blocked-escalated"
    if norm == "verified" or norm.startswith("verified_ready"):
        return "verified"
    if norm.startswith("qualified"):
        return "qualified-draft"
    return None


def said_state(value: Any) -> str | None:
    """The worse of the two state fields a result reports (``completion_state``, ``reliability_label``), or None when
    it reports neither. A word outside the vocabulary is a ``qualified-draft``; a block is never raised."""
    if not isinstance(value, Mapping):
        return None
    words = [value.get(key) for key in ("completion_state", "reliability_label") if value.get(key) is not None]
    if not words:
        return None
    return max((state_of(word) or "qualified-draft" for word in words), key=_RANK.__getitem__)


def held_copy(value: Any, state: str, *, _depth: int = 0) -> Any:
    """A copy of ``value`` that claims no more than ``state``: every string anywhere in it that reads as
    ``verified`` (:func:`state_of`) becomes ``state``, and a ``verified: true`` flag becomes false. Nothing changes
    when ``state`` is ``verified``."""
    if state == "verified":
        return value
    if _depth > _MAX_DEPTH:
        return "(nested too deep for the door to read)"
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if key == "verified" and item is True:
                out[key] = False
            else:
                out[key] = held_copy(item, state, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [held_copy(item, state, _depth=_depth + 1) for item in value]
    if state_of(value) == "verified":
        return state
    return value


def corrected_result(value: Any, state: str) -> Any:
    """The result copy a door hands on: its top-level state fields say no more than ``state`` (``claimed_completion_
    state`` keeps what it said when that was stronger), and nothing inside it reads as ``verified`` unless the door
    kept a ``verified``."""
    if not isinstance(value, Mapping):
        return held_copy(value, state)
    said = value.get("completion_state")
    out = dict(held_copy(value, state))
    for key in ("completion_state", "reliability_label"):
        if key in out and _RANK.get(state_of(value.get(key)) or "qualified-draft", 1) < _RANK[state]:
            out[key] = state
    if state_of(said) is not None and _RANK[state_of(said)] < _RANK[state]:
        out["claimed_completion_state"] = said
    return out


def door_envelope(claimed: Any, *, who: str = "", producer: Any = None, value: Any = None,
                  caller: Mapping[str, Any] | None = None, evidence: list[Any] | None = None,
                  warning_card: dict[str, Any] | None = None, request_id: str = "", run_id: str = "",
                  task_id: str = "") -> dict[str, Any]:
    """The library envelope a door returns for a result whose producer (``who``) said ``claimed``.

    A word outside the three states is a ``qualified-draft``. ``verified`` is kept only when G6 has a re-checker for
    the code ``producer`` (a class or function, located by :func:`g6.core.reliability_envelope.g6_module`), that
    re-checker accepts ``value`` as its own record with what to compare it against from ``caller``, and the re-check
    reproduces; the envelope's evidence is then the re-check itself. Otherwise the claim is held at ``qualified-draft``
    (``blocked-escalated`` when the re-check found a mismatch) with a ``G6_E_PROMOTION_LAW`` card, and
    ``claimed_completion_state`` keeps ``verified``.
    """
    state = claimed if claimed in _RANK else "qualified-draft"
    witness = (door_witness(producer, value, caller=caller, task_id=task_id, run_id=run_id)
               if state == "verified" else None)
    if witness is not None:
        evidence, warning_card = [recheck_evidence(witness)], None
    envelope = envelope_fields(reliability_label=state, warning_card=warning_card, evidence=list(evidence or []),
                               request_id=request_id, task_id=task_id, run_id=run_id, promotion_witness=witness)
    if state != "verified" or envelope["completion_state"] == "verified":
        return envelope
    reason = envelope.get("degradation_reason") or "G6 did not re-run a check that reproduces it"
    outcome = ("G6 re-ran the check and the artifact does not match it, so it is blocked"
               if envelope["completion_state"] == "blocked-escalated" else
               "G6 did not re-run a check that reproduces it, so it is held at qualified-draft")
    envelope["warning_card"] = new_card(PROMOTION_LAW, f"{who or 'the producer'} reported verified, but {outcome} "
                                                       f"({reason})", HELD_NEXT_STEP)
    envelope["claimed_completion_state"] = "verified"
    return envelope


def blocked_reply(error: str, *, request_id: str = "") -> dict[str, Any]:
    """A door's reply when there is no result: ``blocked-escalated``, with the error's own ``G6_E_*`` code."""
    match = _CODE.match(error)
    found = card(None, match.group(1) if match else "G6_E_INVOKE_FAILED", error[match.end():] if match else error)
    return {"ok": False, "error": error, **envelope_fields(reliability_label="blocked-escalated", warning_card=found,
                                                         request_id=request_id, degradation_reason=error[:300])}


def producer_of(component: str, registry: Any = None) -> Any:
    """The code behind a registry entry, for :func:`door_envelope`: the block class of a built-in component, or None
    (an extension, an unknown name, or a registry that does not say). A name never stands for code."""
    if registry is None:
        from g6.core.registry import get_registry

        registry = get_registry()
    refs = getattr(registry, "refs", None)
    ref = refs.get(component) if isinstance(refs, Mapping) else None
    if ref is None or getattr(ref, "source", "") != "builtin":
        return None
    try:
        block = registry.get_class(component)
    except Exception:  # noqa: BLE001 - code the door cannot load is code it cannot vouch for
        return None
    from g6.core.reliability_envelope import g6_module

    package = str(getattr(ref, "module", "")).split(":", 1)[0]
    module = g6_module(block)
    return block if package and (module == package or module.startswith(package + ".")) else None


def door_reply(component: str, op: str, outcome: Any, *, caller: Mapping[str, Any] | None = None,
               registry: Any = None, request_id: str = "") -> dict[str, Any]:
    """The reply for ``component.op``'s registry ``outcome`` (a ``Success`` or a ``Failure``): ``ok``, the library
    envelope, and ``result``, a copy of the component's answer that claims no more than the door decided. ``caller``
    is the caller's own input to the op (what a re-check compares against); ``registry`` says which code answered.

    ``ok`` says whether the reply may be used: it is false when there is no result (``error`` says why) and when the
    door's state for the result is ``blocked-escalated`` (the card says why, and ``result`` keeps the answer)."""
    request_id = request_id or uuid.uuid4().hex[:12]
    if outcome.__class__.__name__ != "Success":
        return blocked_reply(str(outcome.failure()), request_id=request_id)
    value = outcome.unwrap()
    mapping = isinstance(value, Mapping)
    said = said_state(value)
    evidence = value.get("evidence") if mapping and isinstance(value.get("evidence"), list) else []
    found_card = value.get("warning_card") if mapping else None
    run_id = value.get("run_id") if mapping and isinstance(value.get("run_id"), str) else ""
    who = f"{component}.{op}"
    if said == "verified":
        found = None
    elif said == "blocked-escalated":
        found = card(found_card, "G6_E_BLOCKED", f"{who} reported blocked-escalated")
    else:
        why = ("it reported a qualified draft: usable with caution" if said == "qualified-draft"
               else "it reported no reliability state, so treat the result as a draft")
        found = card(found_card if said == "qualified-draft" else None, "G6_E_QUALIFIED_DRAFT", f"{who}: {why}")
    producer = producer_of(component, registry) if said == "verified" else None
    envelope = door_envelope(said, who=who, producer=producer, value=value, caller=caller, evidence=evidence,
                             warning_card=found, request_id=request_id, run_id=run_id)
    state = envelope["completion_state"]
    return {"ok": state != "blocked-escalated", "result": corrected_result(value, state), **envelope}


#: The person's own setting: the sensitive components or ops (``component.op``) they allow, comma-separated, no
#: wildcard. G6 reads it from the environment it was started in; an agent's call cannot set it.
SENSITIVE_ENV = "G6_MCP_SENSITIVE"
_DENIED = "[G6_E_SENSITIVE_COMPONENT_DENIED]"


def allowed_sensitive(setting: str | None = None) -> frozenset[str]:
    """The names listed in ``G6_MCP_SENSITIVE`` (or ``setting``): components or ``component.op``, no wildcard."""
    import os

    raw = os.environ.get(SENSITIVE_ENV, "") if setting is None else setting
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def is_allowed(component: str, op: str, allow: frozenset[str]) -> bool:
    return component in allow or f"{component}.{op}" in allow


def person_model() -> Any:
    """The model the person configured for G6 (``g6.core.llm.provider_from_env``: ``G6_LLM_PROVIDER`` in their key
    file), or ``None`` when they configured none. A setting G6 cannot use gives a caller that says why when it is
    asked, so only an op that needs a model hears of it."""
    from g6.core import llm

    try:
        caller = llm.provider_from_env()
    except llm.LLMError as exc:
        return llm.UnusableModel(str(exc))
    return None if isinstance(caller, llm.NoModel) else caller


def invoke_checked(component: str, op: str, params: Any, *, allow: frozenset[str], how_to_allow: str,
                   registry: Any = None, request_id: str = "") -> dict[str, Any]:
    """The one checked call the MCP server, ``g6 invoke`` and ``g6.invoke`` share: ``component.op`` on ``params``
    (a mapping), replied through :func:`door_reply`. A sensitive op runs only when ``allow`` (what the person granted
    on that surface) lists it or its component; the refusal ends with ``how_to_allow``. A component whose block takes
    a model (``__g6_model__``) is given the one the person configured, built once for the call."""
    request_id = request_id or uuid.uuid4().hex[:12]
    if not isinstance(params, Mapping):
        return blocked_reply("[G6_E_INPUT_VALIDATION] the op's parameters must be a JSON object (a mapping)",
                             request_id=request_id)
    params = dict(params)
    if registry is None:
        from g6.core.registry import get_registry

        registry = get_registry()
    model = person_model()
    result = registry.invoke(component, op, params, block_sensitive=not is_allowed(component, op, allow),
                             **({"llm": model} if model is not None else {}))
    if result.__class__.__name__ != "Success":
        error = str(result.failure())
        if error.startswith(_DENIED):
            from g6.core.confine import UNCONFINED_WARNING

            error += f": it runs a program on your machine, reaches the network or changes it. {how_to_allow} " + (
                UNCONFINED_WARNING)
        return blocked_reply(error, request_id=request_id)
    return door_reply(component, op, result, caller=params, registry=registry, request_id=request_id)


__all__ = ["HELD_NEXT_STEP", "SENSITIVE_ENV", "THREAT_STATEMENT", "allowed_sensitive", "blocked_reply", "card",
           "corrected_result", "door_envelope", "door_reply", "held_copy", "invoke_checked", "is_allowed",
           "person_model", "producer_of", "said_state", "state_of"]
