# SPDX-License-Identifier: Apache-2.0
"""g6.core.door — the rule every door applies to what a component says about its own result (plan A1).

``verified`` stands only when G6's own code re-runs a check at the door and reproduces it; a witness the producer
hands over is ignored. The door asks G6's re-checker for the component
(``g6.core.reliability_envelope.door_witness``) to name what to re-check from the result's own content, and the
promotion law re-runs it. Anything else a component calls ``verified`` is held at ``qualified-draft`` with a
``G6_E_PROMOTION_LAW`` card, and ``claimed_completion_state`` keeps what it said. A door never raises a state.

G6 re-checks artifacts with its own code; it does not defend against malicious code in its own process.

The MCP server's ``g6_invoke`` and the ``g6 invoke`` command reply through :func:`door_reply`; a pipeline of blocks, a
bundle's result and the harness build their envelopes with :func:`door_envelope`.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from g6.core.reliability_envelope import PROMOTION_LAW, door_witness, envelope_fields, warning_card

_RANK = {"verified": 0, "qualified-draft": 1, "blocked-escalated": 2}
_CODE = re.compile(r"^\[(G6_E_[A-Z0-9_]+)\]\s*")


def card(found: Any, code: str, message: str) -> dict[str, Any]:
    """``found`` when it is a warning card with a stable ``G6_E_*`` code, else a new card with ``code``."""
    from g6.invariants import ERROR_CODE_PATTERN

    if isinstance(found, Mapping) and ERROR_CODE_PATTERN.match(str(found.get("code", ""))):
        return dict(found)
    return warning_card(code, message)


def door_envelope(claimed: Any, *, who: str = "", component: str = "", value: Any = None,
                  evidence: list[Any] | None = None, warning_card: dict[str, Any] | None = None,
                  request_id: str = "", run_id: str = "", task_id: str = "") -> dict[str, Any]:
    """The library envelope a door returns for a result whose producer (``who``) said ``claimed``.

    A word outside the three states is a ``qualified-draft``. ``verified`` is kept only when G6's re-checker for
    ``component`` re-runs the check named by ``value``'s own content and reproduces it; the re-check is then recorded
    in ``evidence``. Otherwise it is held at ``qualified-draft`` with a ``G6_E_PROMOTION_LAW`` card, and
    ``claimed_completion_state`` keeps ``verified``.
    """
    state = claimed if claimed in _RANK else "qualified-draft"
    witness = door_witness(component, value, task_id=task_id, run_id=run_id) if state == "verified" else None
    envelope = envelope_fields(reliability_label=state, warning_card=warning_card, evidence=list(evidence or []),
                               request_id=request_id, task_id=task_id, run_id=run_id, promotion_witness=witness)
    if state != "verified":
        return envelope
    if envelope["completion_state"] == "verified":
        envelope["evidence"].append({"kind": "g6_recheck", "oracle": witness["oracle"],
                                     "execution_id": witness["execution"]["execution_id"],
                                     "summary": "G6 re-ran this check at the door and it reproduced"})
        return envelope
    reason = envelope.get("degradation_reason") or "G6 did not re-run a check that reproduces it"
    envelope["warning_card"] = card(None, PROMOTION_LAW, f"{who or 'the producer'} reported verified, but G6 did not "
                                                         f"re-run a check that reproduces it, so it is held at "
                                                         f"qualified-draft ({reason})")
    envelope["claimed_completion_state"] = "verified"
    return envelope


def blocked_reply(error: str, *, request_id: str = "") -> dict[str, Any]:
    """A door's reply when there is no result: ``blocked-escalated``, with the error's own ``G6_E_*`` code."""
    match = _CODE.match(error)
    found = card(None, match.group(1) if match else "G6_E_INVOKE_FAILED", error[match.end():] if match else error)
    return {"ok": False, "error": error, **envelope_fields(reliability_label="blocked-escalated", warning_card=found,
                                                         request_id=request_id, degradation_reason=error[:300])}


def _corrected(value: Any, state: str) -> Any:
    """A copy of ``value`` whose own state fields claim no more than ``state`` (the raw-state leak, plan A1)."""
    if not isinstance(value, Mapping):
        return value
    out = dict(value)
    for key in ("completion_state", "reliability_label"):
        said = out.get(key)
        if isinstance(said, str) and _RANK.get(said, 1) < _RANK[state]:
            out[key] = state
            if key == "completion_state":
                out["claimed_completion_state"] = said
    return out


def door_reply(component: str, op: str, outcome: Any, *, request_id: str = "") -> dict[str, Any]:
    """The reply for ``component.op``'s registry ``outcome`` (a ``Success`` or a ``Failure``): ``ok``, the library
    envelope, and ``result``, a copy of the component's answer whose state fields say what the door decided."""
    request_id = request_id or uuid.uuid4().hex[:12]
    if outcome.__class__.__name__ != "Success":
        return blocked_reply(str(outcome.failure()), request_id=request_id)
    value = outcome.unwrap()
    mapping = isinstance(value, Mapping)
    said = value.get("completion_state") if mapping else None
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
    envelope = door_envelope(said, who=who, component=component, value=value, evidence=evidence,
                             warning_card=found, request_id=request_id, run_id=run_id)
    return {"ok": True, "result": _corrected(value, envelope["completion_state"]), **envelope}


__all__ = ["blocked_reply", "card", "door_envelope", "door_reply"]
