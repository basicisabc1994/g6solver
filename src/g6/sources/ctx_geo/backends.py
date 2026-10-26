# SPDX-License-Identifier: Apache-2.0
"""Where ctx_geo's answers come from: map services registered as backends, each reached through G6's checked HTTP.

A backend (:class:`GeoBackend`) answers the ops it supports with a :class:`GeoAnswer`: an outcome, the results in the
service's own terms, the service's own status, what was sent (never a key) and what was read. :data:`BACKENDS` holds
them by name; the one registered by default is ``google``.

:class:`GoogleBackend` uses Google Maps Platform's documented web services: the Geocoding API (``geocode``,
``reverse_geocode``), the Elevation API (``elevation``) and the Time Zone API (``timezone``) over
``https://maps.googleapis.com/maps/api``; the Routes API (``directions``: ``directions/v2:computeRoutes``;
``distance_matrix``: ``distanceMatrix/v2:computeRouteMatrix``); and the Places API (``search_places``:
``v1/places:searchText``). Every request goes through ``g6.core.http`` with no redirect followed (a key is never sent
to another address), a time limit and a size cap. Its key comes from the person's key file (``g6.core.keys``) and is
paid. Google answers a refused key or an exhausted quota with HTTP 200 and a ``status``; that status is read on every
reply and mapped to an outcome:

``OK`` ok; ``ZERO_RESULTS`` empty; ``REQUEST_DENIED`` auth_failed; ``OVER_DAILY_LIMIT`` and ``OVER_QUERY_LIMIT``
quota_exceeded; ``INVALID_REQUEST`` and the ``MAX_*_EXCEEDED`` statuses rejected; ``NOT_FOUND`` not_found;
``UNKNOWN_ERROR`` server_error; anything else, or none, parse_failed. An HTTP error is mapped too (400 rejected, 401
and 403 auth_failed, 404 not_found, 429 quota_exceeded, 5xx server_error), so a failure is never read as "no results".
A route matrix carries a status per pair (``google.rpc`` codes): each pair's is mapped first, so a matrix whose every
pair failed is that failure with the service's own message, and one where some failed is ``partial`` and names them.
Nothing is cached: every call asks the service.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

from g6.core import http, keys
from g6.knowledge._types import Source

OPS = ("geocode", "reverse_geocode", "directions", "distance_matrix", "search_places", "elevation", "timezone")
OUTCOMES = ("ok", "partial", "empty", "auth_failed", "quota_exceeded", "rejected", "not_found", "server_error",
            "parse_failed", "unreachable", "refused", "too_large", "http_error", "unsupported")
ANSWERED = frozenset({"ok", "partial", "empty"})
GOOGLE_KEY = keys.Key("GOOGLE_MAPS_API_KEY", "Google Maps Platform web services: Geocoding, Elevation, Time Zone, "
                      "Routes and Places (ctx_geo's google backend)",
                      where="https://console.cloud.google.com/google/maps-apis/credentials", paid=True)
TIMEOUT = 20.0
MAX_BYTES = 5 * 1024 * 1024
_MAPS = "https://maps.googleapis.com/maps/api"
_ROUTES = "https://routes.googleapis.com"
_PLACES = "https://places.googleapis.com/v1/places:searchText"
_STATUS = {"OK": "ok", "ZERO_RESULTS": "empty", "REQUEST_DENIED": "auth_failed", "OVER_DAILY_LIMIT": "quota_exceeded",
           "OVER_QUERY_LIMIT": "quota_exceeded", "INVALID_REQUEST": "rejected", "MAX_ELEMENTS_EXCEEDED": "rejected",
           "MAX_DIMENSIONS_EXCEEDED": "rejected", "MAX_WAYPOINTS_EXCEEDED": "rejected",
           "MAX_ROUTE_LENGTH_EXCEEDED": "rejected", "NOT_FOUND": "not_found", "UNKNOWN_ERROR": "server_error"}
#: A route matrix pair's google.rpc status code, as an outcome (any other code that is not 0 is server_error).
_RPC = {3: "rejected", 5: "not_found", 7: "auth_failed", 16: "auth_failed", 8: "quota_exceeded", 9: "rejected",
        11: "rejected", 13: "server_error", 14: "server_error", 4: "server_error"}
_MODES = {"driving": "DRIVE", "walking": "WALK", "bicycling": "BICYCLE", "transit": "TRANSIT",
          "two_wheeler": "TWO_WHEELER"}
_KEY_IN_URL = re.compile(r"([?&]key=)[^&]*")


@dataclass
class GeoAnswer:
    """What one backend said: the outcome, the results, the service's own status, what was sent and what was read."""

    outcome: str
    results: list[dict[str, Any]] = field(default_factory=list)
    status: str = ""
    reason: str = ""
    sent: dict[str, Any] = field(default_factory=dict)
    read: list[dict[str, Any]] = field(default_factory=list)


class GeoBackend(Protocol):
    """A map service. ``paid`` and ``key`` say what using it spends; ``ops`` which ops it answers."""

    name: str
    service: str
    paid: bool
    key: keys.Key | None
    ops: tuple[str, ...]

    def call(self, op: str, params: dict[str, Any], key: str | None, client: http.Client) -> GeoAnswer: ...


def shown(url: str) -> str:
    """``url`` with any key in it hidden."""
    return _KEY_IN_URL.sub(r"\1***", url)


def _http_outcome(exc: http.HTTPError) -> str:
    if exc.code == http.REFUSED:
        return "refused"
    if exc.code == http.TOO_LARGE:
        return "too_large"
    if exc.code == http.FAILED:
        return "unreachable"
    status = exc.status or 0
    if status == 400:
        return "rejected"
    if status in (401, 403):
        return "auth_failed"
    if status == 404:
        return "not_found"
    if status == 429:
        return "quota_exceeded"
    if status >= 500:
        return "server_error"
    return "http_error"


class _Failed(Exception):
    def __init__(self, answer: GeoAnswer) -> None:
        super().__init__(answer.reason)
        self.answer = answer


def _point(lat: float, lng: float) -> str:
    return f"{lat:.7f},{lng:.7f}"


class GoogleBackend:
    """Google Maps Platform's web services (see the module docstring)."""

    name, service, paid, key = "google", "Google Maps Platform", True, GOOGLE_KEY
    ops = OPS

    def _send(self, client: http.Client, method: str, url: str, sent: dict[str, Any], *, key: str,
              body: dict[str, Any] | None = None, mask: str = "") -> Any:
        """The JSON Google answered; :class:`_Failed` with the outcome for an HTTP failure or a reply that is not
        JSON. The key goes in the URL (the Maps web services) or the ``X-Goog-Api-Key`` header (Routes, Places), and
        never into what is recorded."""
        headers = {"Accept": "application/json"}
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": mask})
        else:
            url = f"{url}&{urlencode({'key': key})}"
        try:
            reply = client.request(method, url, headers=headers,
                                   body=json.dumps(body).encode() if body is not None else None)
        except http.HTTPError as exc:
            outcome = _http_outcome(exc)
            said = f"{self.service} answered HTTP {exc.status}" if exc.status else f"{exc.code} for {self.service}"
            raise _Failed(GeoAnswer(outcome, reason=said, sent=sent)) from None
        read = [Source.of(shown(reply.url), reply.body, provenance="fetched").model_dump()]
        try:
            return json.loads(reply.body), read
        except (ValueError, UnicodeDecodeError):
            raise _Failed(GeoAnswer("parse_failed", reason=f"{self.service} answered with something that is not JSON",
                                    sent=sent, read=read)) from None

    def _maps(self, client: http.Client, api: str, query: dict[str, Any], key: str) -> GeoAnswer | tuple:
        url = f"{_MAPS}/{api}/json?" + urlencode(query)
        sent = {"endpoint": f"{_MAPS}/{api}/json", "params": query}
        data, read = self._send(client, "GET", url, sent, key=key)
        status = data.get("status") if isinstance(data, dict) else None
        outcome = _STATUS.get(status, "parse_failed") if isinstance(status, str) else "parse_failed"
        if outcome not in ANSWERED:
            why = data.get("error_message") if isinstance(data, dict) else ""
            reason = (f"{self.service} answered status {status}" if isinstance(status, str) else
                      f"{self.service}'s reply has no status") + (f": {str(why)[:300]}" if why else "")
            return GeoAnswer(outcome, status=str(status or ""), reason=reason, sent=sent, read=read)
        return data, read, sent, status

    def call(self, op: str, params: dict[str, Any], key: str | None, client: http.Client) -> GeoAnswer:
        if not key:
            return GeoAnswer("auth_failed", reason=f"{self.service} needs a key; none was given")
        try:
            return getattr(self, f"_{op}")(params, key, client)
        except _Failed as exc:
            return exc.answer

    def _legacy(self, api: str, query: dict[str, Any], key: str, client: http.Client,
                pick: Callable[[dict[str, Any]], list[dict[str, Any]]]) -> GeoAnswer:
        found = self._maps(client, api, query, key)
        if isinstance(found, GeoAnswer):
            return found
        data, read, sent, status = found
        results = pick(data) if status == "OK" else []
        return GeoAnswer("ok" if results else "empty", results, status, sent=sent, read=read,
                         reason="" if results else f"{self.service} found nothing ({status})")

    def _geocode(self, p: dict[str, Any], key: str, client: http.Client) -> GeoAnswer:
        return self._legacy("geocode", {"address": p["address"]}, key, client, _places_of)

    def _reverse_geocode(self, p: dict[str, Any], key: str, client: http.Client) -> GeoAnswer:
        return self._legacy("geocode", {"latlng": _point(p["lat"], p["lng"])}, key, client, _places_of)

    def _elevation(self, p: dict[str, Any], key: str, client: http.Client) -> GeoAnswer:
        query = {"locations": "|".join(_point(a, b) for a, b in p["locations"])}
        return self._legacy("elevation", query, key, client, lambda d: [
            {"elevation_m": r.get("elevation"), "lat": (r.get("location") or {}).get("lat"),
             "lng": (r.get("location") or {}).get("lng"), "resolution_m": r.get("resolution")}
            for r in d.get("results") or () if isinstance(r, dict)])

    def _timezone(self, p: dict[str, Any], key: str, client: http.Client) -> GeoAnswer:
        query = {"location": _point(p["lat"], p["lng"]), "timestamp": p["timestamp"]}
        return self._legacy("timezone", query, key, client, lambda d: [
            {"time_zone_id": d.get("timeZoneId"), "time_zone_name": d.get("timeZoneName"),
             "raw_offset_s": d.get("rawOffset"), "dst_offset_s": d.get("dstOffset")}] if d.get("timeZoneId") else [])

    def _post(self, url: str, body: dict[str, Any], mask: str, key: str, client: http.Client,
              listed: str | None) -> tuple[Any, list, dict] | GeoAnswer:
        sent = {"endpoint": url, "body": body, "fields": mask}
        data, read = self._send(client, "POST", url, sent, key=key, body=body, mask=mask)
        if listed is not None and not isinstance(data, dict):
            return GeoAnswer("parse_failed", reason=f"{self.service}'s reply is not an object", sent=sent, read=read)
        if listed is None and not isinstance(data, list):
            return GeoAnswer("parse_failed", reason=f"{self.service}'s reply is not a list", sent=sent, read=read)
        return data, read, sent

    def _directions(self, p: dict[str, Any], key: str, client: http.Client) -> GeoAnswer:
        body = {"origin": {"address": p["origin"]}, "destination": {"address": p["destination"]},
                "travelMode": _MODES[p["mode"]]}
        mask = "routes.distanceMeters,routes.duration,routes.description,routes.warnings,routes.legs.distanceMeters," \
               "routes.legs.duration"
        found = self._post(f"{_ROUTES}/directions/v2:computeRoutes", body, mask, key, client, "routes")
        if isinstance(found, GeoAnswer):
            return found
        data, read, sent = found
        routes = [{"distance_m": r.get("distanceMeters"), "duration": r.get("duration"),
                   "description": r.get("description", ""), "warnings": r.get("warnings", []),
                   "legs": [{"distance_m": leg.get("distanceMeters"), "duration": leg.get("duration")}
                            for leg in r.get("legs") or () if isinstance(leg, dict)]}
                  for r in data.get("routes") or () if isinstance(r, dict)]
        return GeoAnswer("ok" if routes else "empty", routes, sent=sent, read=read,
                         reason="" if routes else f"{self.service} found no route")

    def _distance_matrix(self, p: dict[str, Any], key: str, client: http.Client) -> GeoAnswer:
        body = {"origins": [{"waypoint": {"address": a}} for a in p["origins"]],
                "destinations": [{"waypoint": {"address": a}} for a in p["destinations"]],
                "travelMode": _MODES[p["mode"]]}
        mask = "originIndex,destinationIndex,status,condition,distanceMeters,duration"
        found = self._post(f"{_ROUTES}/distanceMatrix/v2:computeRouteMatrix", body, mask, key, client, None)
        if isinstance(found, GeoAnswer):
            return found
        data, read, sent = found

        def at(names: list[str], index: Any) -> str | None:
            return names[index] if isinstance(index, int) and 0 <= index < len(names) else None

        cells = []
        for e in data:
            if not isinstance(e, dict):
                continue
            problem = e.get("status") if isinstance(e.get("status"), dict) else {}
            code = problem.get("code") if isinstance(problem.get("code"), int) else 0
            condition = e.get("condition", "")
            cells.append({"origin": at(p["origins"], e.get("originIndex", 0)),
                          "destination": at(p["destinations"], e.get("destinationIndex", 0)),
                          "condition": condition, "distance_m": e.get("distanceMeters"), "duration": e.get("duration"),
                          "outcome": _RPC.get(code, "server_error") if code else (
                              "ok" if condition == "ROUTE_EXISTS" else "empty"),
                          "error": str(problem.get("message", ""))[:300] if code else ""})
        failed = [c for c in cells if c["outcome"] not in ANSWERED]
        if cells and len(failed) == len(cells):   # every pair an error: that is the answer's outcome, not "no route"
            outcome = Counter(c["outcome"] for c in failed).most_common(1)[0][0]
            return GeoAnswer(outcome, cells, sent=sent, read=read,
                             reason=f"{self.service} answered an error for every one of the {len(cells)} pair(s): "
                                    f"{failed[0]['error'] or failed[0]['outcome']}")
        if failed:
            named = "; ".join(f"{c['origin']} to {c['destination']} ({c['outcome']}: {c['error']})" for c in failed[:10])
            return GeoAnswer("partial", cells, sent=sent, read=read,
                             reason=f"{len(failed)} of {len(cells)} pair(s) failed, so their distances are not known: "
                                    f"{named}" + ("; and more" if len(failed) > 10 else ""))
        routed = [c for c in cells if c["condition"] == "ROUTE_EXISTS"]
        return GeoAnswer("ok" if routed else "empty", cells, sent=sent, read=read,
                         reason="" if routed else f"{self.service} found no route between any pair")

    def _search_places(self, p: dict[str, Any], key: str, client: http.Client) -> GeoAnswer:
        body = {"textQuery": p["query"], "pageSize": p["max_results"]}
        mask = "places.id,places.displayName,places.formattedAddress,places.location,places.types"
        found = self._post(_PLACES, body, mask, key, client, "places")
        if isinstance(found, GeoAnswer):
            return found
        data, read, sent = found
        places = [{"place_id": r.get("id"), "name": (r.get("displayName") or {}).get("text", ""),
                   "address": r.get("formattedAddress", ""), "lat": (r.get("location") or {}).get("latitude"),
                   "lng": (r.get("location") or {}).get("longitude"), "types": r.get("types", [])}
                  for r in data.get("places") or () if isinstance(r, dict)]
        return GeoAnswer("ok" if places else "empty", places, sent=sent, read=read,
                         reason="" if places else f"{self.service} found no place")


def _places_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in data.get("results") or ():
        if not isinstance(r, dict):
            continue
        where = (r.get("geometry") or {}).get("location") or {}
        out.append({"address": r.get("formatted_address", ""), "lat": where.get("lat"), "lng": where.get("lng"),
                    "location_type": (r.get("geometry") or {}).get("location_type", ""),
                    "place_id": r.get("place_id", ""), "types": r.get("types", []),
                    "partial_match": bool(r.get("partial_match", False))})
    return out


class GeoBackends:
    """The registered backends, by name."""

    def __init__(self, backends: Iterable[GeoBackend] = ()) -> None:
        self._backends: dict[str, GeoBackend] = {}
        for backend in backends:
            self.register(backend)

    def register(self, backend: GeoBackend) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", getattr(backend, "name", "")):
            raise ValueError("a backend's name is a lower-case word")
        self._backends[backend.name] = backend

    def get(self, name: str) -> GeoBackend | None:
        return self._backends.get(name)

    def names(self) -> list[str]:
        return sorted(self._backends)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for name in self.names():
            b = self._backends[name]
            out.append({"name": b.name, "service": b.service, "paid": b.paid, "ops": list(b.ops),
                        "key": b.key.name if b.key else "", "key_in_file": keys.present(b.key.name) if b.key else None})
        return out


BACKENDS = GeoBackends([GoogleBackend()])
Clients = Callable[[GeoBackend], http.Client]


def default_client(backend: GeoBackend) -> http.Client:
    """A checked client that follows no redirect, so a key is never sent on to another address."""
    return http.Client(timeout=TIMEOUT, max_bytes=MAX_BYTES, max_redirects=0, min_interval=0.0,
                       user_agent="g6-ctx-geo")


__all__ = ["ANSWERED", "BACKENDS", "GOOGLE_KEY", "OPS", "OUTCOMES", "Clients", "GeoAnswer", "GeoBackend",
           "GeoBackends", "GoogleBackend", "default_client", "shown"]
