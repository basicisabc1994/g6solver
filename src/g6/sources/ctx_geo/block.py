# SPDX-License-Identifier: Apache-2.0
"""CtxGeoBlock: the registry and MCP door (``g6_invoke``) to ctx_geo.

Every lookup op reaches a map service over the network and, with the ``google`` backend, spends the person's money, so
every one is sensitive: over MCP it runs only when the person who runs the server lists it in ``G6_MCP_SENSITIVE``.
``backends`` lists the registered backends and reaches nothing.

A lookup is a dry run unless ``dry_run`` is false (the caller's word, which can only hold a request back). Then a paid
backend's request is sent only when ``g6.action_gating`` lets it through: the gate reads the backend's key in the
person's key file (``~/.g6/.env``) as their approval to spend it. The key is read from that file only, through
``g6.core.keys``. Results are ``qualified-draft``: the service's answer, with the provider and the request (never
the key) in the evidence.
"""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import g6.knowledge._reply as _reply
import g6.sources._gate as _gate
from g6.core import keys
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.sources.ctx_geo.backends import ANSWERED, BACKENDS, OPS, Clients, GeoAnswer, default_client

COMPONENT = "ctx_geo"
_PAID = " (sensitive: it reaches the network and, with google, spends money)"
_OPS: dict[str, str] = {
    "geocode": "geocode(address): the places an address names, every candidate when there is more than one" + _PAID,
    "reverse_geocode": "reverse_geocode(lat, lng): the addresses at a point" + _PAID,
    "directions": "directions(origin, destination, mode): routes with distance and duration" + _PAID,
    "distance_matrix": "distance_matrix(origins, destinations, mode): distance and duration for each pair" + _PAID,
    "search_places": "search_places(query, max_results): places matching a text search" + _PAID,
    "elevation": "elevation(locations): the ground's height at each point" + _PAID,
    "timezone": "timezone(lat, lng, timestamp): the time zone and its offsets at a point and moment" + _PAID,
    "backends": "backends(): the registered map backends, what each costs and whether its key is in the key file; "
                "nothing is sent",
}
SENSITIVE_OPS: tuple[str, ...] = OPS
ANSWERED_CODE, EMPTY, DRY_RUN, HELD = "G6_E_GEO_ANSWERED", "G6_E_GEO_ZERO_RESULTS", "G6_E_GEO_DRY_RUN", "G6_E_GEO_HELD"
PARTIAL = "G6_E_GEO_PARTIAL"
LISTED, INPUT, UNKNOWN_BACKEND = "G6_E_GEO_BACKENDS", "G6_E_GEO_INPUT", "G6_E_GEO_UNKNOWN_BACKEND"
_ACTIONS: dict[str, str] = {
    ANSWERED_CODE: "The service's answer, not a check of it: a place, route or time is as the service has it.",
    EMPTY: "The service answered and found nothing: say so; do not answer from memory instead.",
    PARTIAL: "Some pairs failed, as named: their distances and durations are not known; ask again for them, never "
             "fill them in from memory.",
    DRY_RUN: "Nothing was sent. Ask with dry_run false; the action gate and the person's key file decide.",
    HELD: "Nothing was sent: the person approves spending by putting the backend's key in their key file.",
    LISTED: "Name a backend, or leave it to google.",
    INPUT: "Correct the request as the reason says.",
    UNKNOWN_BACKEND: "Use a registered backend (the backends op lists them).",
    keys.KEY_MISSING: "The person adds the line named to their key file; an agent cannot.",
    "auth_failed": "The service refused the key, or the API is not enabled for it: the person checks the key's API "
                   "access. This is not 'no results'.",
    "quota_exceeded": "The key's quota or rate is used up: wait, or the person raises it. This is not 'no results'.",
    "rejected": "The service refused the request as written: correct it.",
    "not_found": "The service could not find a place in the request (an origin or destination): check it.",
    "server_error": "The service failed: try again later. This is not 'no results'.",
    "parse_failed": "The reply could not be read as the service's format, so nothing in it was used.",
    "unreachable": "Nothing answered: try again later. This is not 'no results'.",
    "refused": "G6 reaches only public addresses.",
    "too_large": "The reply was over the size cap.",
    "http_error": "The service answered with an error: read the reason.",
    "unsupported": "This backend does not answer this op: use another backend.",
}


def _code(outcome: str) -> str:
    return f"G6_E_GEO_{outcome.upper()}"


class CtxGeoInput(BaseModel):
    """One op's parameters; fields no op has are refused (an approval or a key among them)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["geocode", "reverse_geocode", "directions", "distance_matrix", "search_places", "elevation",
                "timezone", "backends", "ops", "help"] = "help"
    backend: str = Field("google", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    address: str = Field("", max_length=500)
    lat: float | None = Field(None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(None, ge=-180, le=180, allow_inf_nan=False)
    origin: str = Field("", max_length=500)
    destination: str = Field("", max_length=500)
    origins: tuple[str, ...] = Field((), max_length=25)
    destinations: tuple[str, ...] = Field((), max_length=25)
    mode: Literal["driving", "walking", "bicycling", "transit", "two_wheeler"] = "driving"
    query: str = Field("", max_length=500)
    max_results: int = Field(10, ge=1, le=20)
    locations: tuple[tuple[float, float], ...] = Field((), max_length=100)
    timestamp: int | None = Field(None, ge=0, le=32503680000)
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @model_validator(mode="after")
    def _points_are_points(self) -> CtxGeoInput:
        for lat, lng in self.locations:
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError(f"({lat}, {lng}) is not a point: latitude -90 to 90, longitude -180 to 180")
        if len(self.origins) * len(self.destinations) > 100:
            raise ValueError("distance_matrix takes at most 100 origin and destination pairs")
        return self


def _params(d: CtxGeoInput) -> dict[str, Any]:
    """What the op sends, checked; ``ValueError`` naming what is missing."""
    need: dict[str, tuple[str, ...]] = {"geocode": ("address",), "reverse_geocode": ("lat", "lng"),
                                        "directions": ("origin", "destination"),
                                        "distance_matrix": ("origins", "destinations"), "search_places": ("query",),
                                        "elevation": ("locations",), "timezone": ("lat", "lng")}
    missing = [f for f in need[d.op] if getattr(d, f) in (None, "", ())]
    if missing:
        raise ValueError(f"{d.op} needs {' and '.join(missing)}")
    picked = {f: getattr(d, f) for f in need[d.op]}
    if d.op in ("directions", "distance_matrix"):
        picked["mode"] = d.mode
    if d.op == "search_places":
        picked["max_results"] = d.max_results
    if d.op == "timezone":
        picked["timestamp"] = d.timestamp if d.timestamp is not None else int(time.time())
    if d.op in ("distance_matrix", "elevation"):
        picked = {k: [list(v) if isinstance(v, tuple) else v for v in val] if isinstance(val, tuple) else val
                  for k, val in picked.items()}
    return picked


def _action(backend: Any, op: str, params: dict[str, Any]) -> dict[str, Any]:
    """The act the gate decides: one paid request to the backend's service, with what G6 checks afterwards."""
    service = backend.service
    return {"action_type": "call_api", "target_backend": backend.name, "target_resource": f"{service} ({op})",
            "description": f"send one {op} request to {service}, a paid service, spending the person's money",
            "action_params": {"op": op, **params}, "requested_by": f"ctx_geo.{op}", "actor_type": "agent",
            "external_side_effect": True, "irreversible_or_costly_to_reverse": True, "blast_radius": "external_party",
            "safety_domains": ["financial", "privacy"],
            "verification_contract": _gate.plan(f"{service}'s reply with a status ctx_geo maps to an outcome",
                                                "ctx_geo's status mapping", "when the reply arrives",
                                                "any status but OK or ZERO_RESULTS: reported as the failure it is",
                                                "the money cannot be returned; nothing is kept")}


@block_contract(
    purpose="answer location questions (places, addresses, routes, distances, elevation, time zones) from a map "
            "service, with its status read on every reply so a refused key or an exhausted quota is never 'no results'",
    mitigates_failure_modes=("answered_from_memory",),
    verification_method="the service's own status mapped to a typed outcome on every reply; the provider and the "
                        "request in the evidence",
)
@component_maturity(
    "beta",
    reason="Google Maps Platform's web services through g6.core.http with every status mapped; tested on recorded "
           "replies, not live; a paid call only on the action gate's allow and the person's key file",
)
class CtxGeoBlock(AIBlock[CtxGeoInput, dict, dict]):
    """Answer a location question from a map service and say where the answer came from. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *, clients: Clients | None = None,
                 gate: Any = None) -> None:
        super().__init__(name=name, state=state or {})
        self.clients, self.gate = clients, gate or _gate.ask

    def infer(self, data: CtxGeoInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        if data.op == "backends":
            listed = BACKENDS.describe()
            return Result.ok(_reply.answer(COMPONENT, "backends", LISTED, f"{len(listed)} map backend(s) registered; "
                                           "nothing was sent.", action=_ACTIONS[LISTED], backends=listed, **ids))
        return Result.ok(self._lookup(data, ids))

    def _refuse(self, op: str, code: str, why: str, ids: dict[str, str], **fields: Any) -> dict[str, Any]:
        action = _ACTIONS.get(code) or _ACTIONS.get(code.removeprefix("G6_E_GEO_").lower(), _ACTIONS[INPUT])
        return _reply.refuse(COMPONENT, op, code, why, action=action, reason=why, **ids, **fields)

    def _lookup(self, d: CtxGeoInput, ids: dict[str, str]) -> dict[str, Any]:
        backend = BACKENDS.get(d.backend)
        if backend is None:
            return self._refuse(d.op, UNKNOWN_BACKEND, f"no map backend named {d.backend!r} (registered: "
                                f"{', '.join(BACKENDS.names()) or 'none'})", ids)
        if d.op not in backend.ops:
            return self._refuse(d.op, _code("unsupported"), f"{backend.name} does not answer {d.op}", ids)
        try:
            params = _params(d)
        except ValueError as exc:
            return self._refuse(d.op, INPUT, str(exc), ids)
        gate: dict[str, Any] | None = None
        if backend.paid:
            key_name = backend.key.name if backend.key else None
            try:
                gate = self.gate(_action(backend, d.op, params), key_name)
            except Exception as exc:  # noqa: BLE001 - a gate that cannot decide holds the request
                gate = {"kind": "action_gate", "decision": "block", "reason_codes": [],
                        "message": f"the action gate could not decide: {type(exc).__name__}"}
        would = {"kind": "dry_run", "backend": backend.name, "op": d.op, "would_send": params}
        if d.dry_run:
            said = (f"A dry run: nothing was sent to {backend.service}." + (
                f" With dry_run false the action gate decides; it says {gate['decision']} now." if gate else ""))
            return _reply.answer(COMPONENT, d.op, DRY_RUN, said, action=_ACTIONS[DRY_RUN], would_send=params,
                                 gate=gate, evidence=[would, *([gate] if gate else [])], **ids)
        if gate is not None and not _gate.lets_through(gate, paid=True):
            codes = ", ".join(gate.get("reason_codes", [])) or "no reason code"
            return self._refuse(d.op, HELD, f"nothing was sent to {backend.service}, which spends the person's money: "
                                f"the action gate decided {gate['decision']} ({codes}): {gate.get('message', '')}",
                                ids, gate=gate, evidence=[would, gate])
        try:
            key = keys.require(backend.key) if backend.key else None
        except keys.KeyMissing as exc:
            return self._refuse(d.op, keys.KEY_MISSING, exc.message, ids, evidence=[would])
        answer = backend.call(d.op, params, key, (self.clients or default_client)(backend))
        return self._reply(d, backend, answer, gate, ids)

    def _reply(self, d: CtxGeoInput, backend: Any, answer: GeoAnswer, gate: dict[str, Any] | None,
               ids: dict[str, str]) -> dict[str, Any]:
        evidence = [{"kind": "provider", "backend": backend.name, "provider": backend.service, "op": d.op,
                     "sent": answer.sent, "status": answer.status, "outcome": answer.outcome, "read": answer.read,
                     "cached": False}, *([gate] if gate else [])]
        fields = {"backend": backend.name, "provider": backend.service, "outcome": answer.outcome,
                  "status": answer.status, "results": answer.results}
        if answer.outcome not in ANSWERED:
            return self._refuse(d.op, _code(answer.outcome), answer.reason or answer.outcome, ids, evidence=evidence,
                                **fields)
        texts = [str(r.get(k, "")) for r in answer.results for k in ("address", "name", "description")
                 if r.get(k)]
        if answer.outcome == "empty":
            return _reply.answer(COMPONENT, d.op, EMPTY, f"{backend.service} answered and found nothing "
                                 f"({answer.status or 'no result'}): that is the answer, not a reason to answer from "
                                 "memory.", action=_ACTIONS[EMPTY], evidence=evidence, **ids, **fields)
        if answer.outcome == "partial":
            return _reply.answer(COMPONENT, d.op, PARTIAL, f"A partial answer from {backend.service}: {answer.reason}.",
                                 action=_ACTIONS[PARTIAL], evidence=evidence, texts=texts, **ids, **fields)
        ambiguous = d.op == "geocode" and len(answer.results) > 1
        said = (f"{len(answer.results)} result(s) from {backend.service}" + (
            ": the address is ambiguous; every candidate is listed, choose one" if ambiguous else "") + ".")
        return _reply.answer(COMPONENT, d.op, ANSWERED_CODE, said, action=_ACTIONS[ANSWERED_CODE], evidence=evidence,
                             texts=texts, ambiguous=ambiguous, **ids, **fields)


__all__ = ["COMPONENT", "SENSITIVE_OPS", "CtxGeoBlock", "CtxGeoInput"]
