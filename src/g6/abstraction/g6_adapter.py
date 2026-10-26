# SPDX-License-Identifier: Apache-2.0
"""g6_adapter — inline G6 wire-envelope adapter for abstraction-mcp verdicts.

L3 of the loop-closure substream (plan addendum 2026-07-18, task #66).

This module's internal status lattice {checked, provisional, degenerate,
rejected} (decided solely in ``library.py::Library.evaluate``) is a
STATISTICAL check: "checked" here means robustly sound with a positive
selection-adjusted lower confidence bound on the observed trace.
That is strictly weaker than an executed task oracle, so the campaign
crosswalk (frozen 2026-07-18) maps it DOWN-ONLY onto the G6 states:

    module checked      -> qualified-draft  (oracle strength: executable
                                             trace replay - never task-level)
    module provisional  -> qualified-draft  (separated_judgment)
    module degenerate   -> blocked-escalated
    module rejected     -> blocked-escalated
    anything else       -> blocked-escalated (fail-closed: an unknown status,
                           the old literal ``verified`` included, is drift or
                           tampering, never trusted)

NO input can produce a ``verified`` state or a green badge through this
adapter, and ``human_review_required`` is always True. The warning card's
code is ``G6_E_ABSTRACTION_REVIEW_REQUIRED``, and the envelope carries
``request_id`` and ``run_id``. Side channels per the crosswalk: gate
counterexamples + replay regret_events -> warning_card; gate calibration +
robustness + MDL -> evidence.

Deliberately dependency-free towards G6: this file imports nothing from
``mvp.*`` (no orchestrator, no reliability components). The adapter is a
total, deterministic function from a ``LibraryEntry`` (or its ``to_dict``
mapping) to a plain envelope-shaped dict; when merged over a caller
payload, the adapter's fields win (forged completion states cannot
survive the merge).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

WARNING_CODE = "G6_E_ABSTRACTION_REVIEW_REQUIRED"

STATE_QUALIFIED_DRAFT = "qualified-draft"
STATE_BLOCKED_ESCALATED = "blocked-escalated"

# module status -> (wire completion_state, badge, claim-ledger oracle strength)
_STATUS_TO_WIRE: dict[str, tuple[str, str, str]] = {
    "checked": (STATE_QUALIFIED_DRAFT, "amber", "executable"),
    "provisional": (STATE_QUALIFIED_DRAFT, "amber", "separated_judgment"),
    "degenerate": (STATE_BLOCKED_ESCALATED, "red", ""),
    "rejected": (STATE_BLOCKED_ESCALATED, "red", ""),
}

_CARD_MESSAGES: dict[str, str] = {
    "checked": (
        "A statistical check only: the abstraction is robustly sound "
        "with positive selection-adjusted value on the observed trace. This "
        "is NOT an executed task oracle - treat as a qualified draft and "
        "review before any reliance."
    ),
    "provisional": (
        "The abstraction is robustly sound but its benefit is not "
        "established (selection-adjusted lower confidence bound <= 0). "
        "Usable as advice with caveats only."
    ),
    "degenerate": (
        "The abstraction is structurally trivial (a single cell) - it "
        "cannot carry information about the domain and must not be relied "
        "upon."
    ),
    "rejected": (
        "The abstraction is unsound: the property gate or bootstrap replay "
        "produced counterexamples. Do not rely on it; see the "
        "counterexamples in this card."
    ),
}

_MAX_CARD_ITEMS = 10


def _card_message(status_key: str, safe_status: str) -> str:
    known = _CARD_MESSAGES.get(status_key)
    if known is not None:
        return known
    return (
        f"Unrecognized abstraction-mcp status '{safe_status}' - failing "
        "closed. The verdict cannot be interpreted and must not be relied "
        "upon."
    )


def _status_key(status: object) -> str:
    """Normalize a status for table lookup; ANY failure (including a
    hostile str subclass whose strip/lower raises) reads as unknown."""
    if not isinstance(status, str):
        return ""
    try:
        return status.strip().lower()
    except Exception:  # noqa: BLE001 - hostile str subclass: fail closed
        return ""


def _safe_str(value: object) -> str:
    """str() that cannot raise: a hostile __str__ reads as a marker."""
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - hostile __str__: fail closed
        return "<unrepresentable>"


def surface_state(status: object) -> tuple[str, str, str]:
    """Total, deterministic, DOWN-ONLY map from a module status to
    ``(completion_state, badge, oracle_strength)``.

    Unknown / non-string / tampered statuses fail closed to
    ``blocked-escalated`` - including hostile objects whose own methods
    raise (L3-check hardening). Structurally, no branch of this function
    can return a ``verified`` state or a green badge.
    """
    key = _status_key(status)
    if key in _STATUS_TO_WIRE:
        return _STATUS_TO_WIRE[key]
    return (STATE_BLOCKED_ESCALATED, "red", "")


def _entry_data(entry: Any) -> dict[str, Any]:
    """Normalize a LibraryEntry / mapping into a plain dict; garbage -> {}
    (which downstream reads as an unknown status and fails closed). The
    try covers EVERY probe of the caller's object - a raising to_dict
    property getter, a hostile __getattr__, a detonating __class__ used
    by the ABC isinstance check (D1+L3 check finds) - not just the
    to_dict() call itself."""
    try:
        to_dict = getattr(entry, "to_dict", None)
        if callable(to_dict):
            data = to_dict()
            return dict(data) if isinstance(data, Mapping) else {}
        if isinstance(entry, Mapping):
            return dict(entry)
    except Exception:  # noqa: BLE001 - hostile object: fail closed
        return {}
    return {}


_MAX_SANITIZE_DEPTH = 6


def _json_safe(value: Any, depth: int = _MAX_SANITIZE_DEPTH) -> Any:
    """Deep-sanitize envelope payload data: JSON-native scalars, dicts and
    lists survive (bounded depth); everything else - including bomb
    objects smuggled inside entry fields that would detonate on a later
    json.dumps (D1+L3 check find N2) - becomes its _safe_str."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth <= 0:
        return _safe_str(value)
    try:
        if isinstance(value, Mapping):
            return {_safe_str(k): _json_safe(v, depth - 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v, depth - 1) for v in value]
    except Exception:  # noqa: BLE001 - hostile container: fail closed
        return _safe_str(value)
    return _safe_str(value)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        try:
            return dict(value)
        except Exception:  # noqa: BLE001 - hostile mapping: fail closed
            return {}
    return {}


def _clip(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        try:
            return list(value)[:_MAX_CARD_ITEMS]
        except Exception:  # noqa: BLE001 - hostile sequence: fail closed
            return []
    return []


def _hard_blocked_envelope(operation: str, request_id: str = "", run_id: str = "") -> dict[str, Any]:
    """The last-resort envelope: fully blocked, serialization-safe, no
    caller data but the ids. Emitted only if envelope construction itself
    detonates - totality is a hard law, not a docstring claim."""
    return {
        "request_id": request_id,
        "run_id": run_id,
        "completion_state": STATE_BLOCKED_ESCALATED,
        "reliability_status": "red",
        "warning_card": {
            "code": WARNING_CODE,
            "message": (
                "Adapter internal failure while interpreting an "
                "abstraction-mcp verdict - failing closed."
            ),
            "module_status": "",
            "counterexamples": [],
            "regret_events": [],
            "bootstrap_regret_events": None,
        },
        "evidence": {
            "source": "g6.abstraction.library",
            "module_status": "",
            "oracle_strength": "",
        },
        "verification_report": {
            "component": "abstraction",
            "operation": operation,
            "deterministic": True,
            "module_status": "",
        },
        "human_review_required": True,
        "claims_ledger": {},
        "grounding_quality": {},
    }


def attach_abstraction_envelope(
    operation: str,
    entry: Any,
    payload: Mapping[str, Any] | None = None,
    *,
    request_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Wrap an abstraction-mcp verdict in an honest G6 envelope.

    ``entry`` is a ``LibraryEntry`` or its ``to_dict`` mapping. ``payload``
    is an optional caller dict to merge under the envelope - the adapter's
    fields always win, so a forged ``completion_state`` in the payload
    cannot survive. ``request_id`` and ``run_id`` are carried as given.
    Total by construction: any hostility the specific guards miss lands in
    the belt below, which emits a hard-blocked envelope rather than
    propagating an exception.
    """
    safe_operation = _safe_str(operation)
    ids = {"request_id": _safe_str(request_id), "run_id": _safe_str(run_id)}
    try:
        return _build_envelope(safe_operation, entry, payload, **ids)
    except Exception:  # noqa: BLE001 - belt: totality is a hard law
        return _hard_blocked_envelope(safe_operation, **ids)


def _build_envelope(
    operation: str,
    entry: Any,
    payload: Mapping[str, Any] | None,
    request_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    data = _entry_data(entry)
    raw_status = data.get("status", "")
    status_key = _status_key(raw_status)
    safe_status = _safe_str(raw_status)
    state, badge, oracle = surface_state(raw_status)

    gate = _as_mapping(data.get("gate"))
    replay_d = _as_mapping(data.get("replay"))
    robust = _as_mapping(data.get("robust"))
    mdl = _as_mapping(data.get("mdl"))

    envelope: dict[str, Any] = {
        "request_id": request_id,
        "run_id": run_id,
        "completion_state": state,
        "reliability_status": badge,
        "warning_card": _json_safe({
            "code": WARNING_CODE,
            "message": _card_message(status_key, safe_status),
            "module_status": safe_status,
            "counterexamples": _clip(gate.get("counterexamples")),
            "regret_events": _clip(replay_d.get("regret_events")),
            "bootstrap_regret_events": robust.get("total_regret_events"),
        }),
        "evidence": _json_safe({
            "source": "g6.abstraction.library",
            "module_status": safe_status,
            "oracle_strength": oracle,
            "entry_id": data.get("entry_id"),
            "spec_hash": data.get("spec_hash"),
            "domain": data.get("domain_name"),
            "trace_len": data.get("trace_len"),
            "blp_score": data.get("blp_score"),
            "calibration": _as_mapping(gate.get("calibration")),
            "robust": {
                k: robust.get(k)
                for k in (
                    "lcb", "lcb_level", "mean_benefit", "sound_robust",
                    "valuable", "total_regret_events", "n_permutations",
                    "n_candidates_tried",
                )
            },
            "mdl": {
                k: mdl.get(k)
                for k in ("raw_bits", "coded_bits", "mechanism_bits")
            },
        }),
        "verification_report": {
            "component": "abstraction",
            "operation": operation,
            "deterministic": True,
            "module_status": safe_status,
        },
        "human_review_required": True,
        "claims_ledger": {},
        "grounding_quality": {},
    }
    # `payload is not None` (never bare truthiness): a hostile __len__
    # must not detonate here (D1+L3 check find N8). A payload whose
    # conversion raises is dropped - the envelope's own fields never are.
    base: dict[str, Any] = {}
    if payload is not None and isinstance(payload, Mapping):
        try:
            base = dict(payload)
        except Exception:  # noqa: BLE001 - hostile payload: fail closed
            base = {}
    return {**base, **envelope}
