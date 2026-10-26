---
name: g6-ctx-geo
description: Use when an agent must answer a location question (an address, a point, a route, a distance, a height, a time zone) from a map service rather than from memory, and must know when the service refused the key or ran out of quota rather than found nothing; paid calls go out only on the action gate's allow and the person's key file.
---

# G6 ctx_geo

Answers location questions from a map backend, Google Maps Platform's documented web services by default, through
G6's checked HTTP. The service's own status is read on every reply, so a refused key or a used-up quota is reported
as itself, never as "no results". The provider and the request are in the evidence; nothing is cached.

## When to use it

- Any location fact an answer rests on: where an address is, what is at a point, how far or how long a trip is, how
  high the ground is, which time zone applies at a moment.
- When an address may be ambiguous: every candidate comes back, marked `ambiguous`.

## When not to use it

- To look up a private person's address book or whereabouts.
- For traffic guarantees or a forecast: a route's duration is the service's estimate at the time asked.
- For bulk geocoding: each call is one paid request; there is no batch op.

## Ops

- `geocode`: the places an `address` names, every candidate when there is more than one. Sensitive: it reaches the network and, with google, spends money.
- `reverse_geocode`: the addresses at a point (`lat`, `lng`). Sensitive.
- `directions`: routes from `origin` to `destination` by `mode` (driving, walking, bicycling, transit, two_wheeler), with distance and duration. Sensitive.
- `distance_matrix`: distance and duration for each pair of `origins` and `destinations` (at most 100 pairs). Sensitive.
- `search_places`: places matching a text `query` (`max_results`). Sensitive.
- `elevation`: the ground's height at each of `locations` (pairs of latitude and longitude). Sensitive.
- `timezone`: the time zone and its offsets at a point and `timestamp` (seconds since 1970; now by default). Sensitive.
- `backends`: the registered map backends, what each costs and whether its key is in the key file; nothing is sent.

## Reading the result

A lookup is a dry run unless `dry_run` is false (`G6_E_GEO_DRY_RUN`, with `would_send` and the gate's decision). With
`dry_run` false, a paid backend's request is sent only when `g6.action_gating` lets it through; the gate reads the
backend's key in your key file as your approval to spend it (`allow_with_notice`, `G6_E_KEY_FILE_APPROVED`).
Otherwise the reply is `G6_E_GEO_HELD` with the line to add.

`outcome` and its code: `ok` (`G6_E_GEO_ANSWERED`), `partial` (`G6_E_GEO_PARTIAL`: some pairs of a distance matrix
failed, and the card names them) and `empty` (`G6_E_GEO_ZERO_RESULTS`: the service answered and found nothing; that is
the answer) are `qualified-draft`. Every failure is `blocked-escalated` with `G6_E_GEO_` and the
outcome: `auth_failed` (the service refused the key, or the API is not enabled for it; Google says so with HTTP 200
and `REQUEST_DENIED`), `quota_exceeded`, `rejected`, `not_found` (an origin or destination it could not find),
`server_error`, `parse_failed` (no status, or one it does not know), `unreachable`, `refused`, `too_large`,
`http_error`, `unsupported` (the backend does not answer that op). `status` is the service's own word.

`results` are the service's terms: `address`, `lat`, `lng`, `location_type`, `place_id`, `partial_match` for places;
`distance_m`, `duration` and `legs` for routes; a `condition`, an `outcome` and the service's `error` per matrix pair
(a matrix whose every pair failed is that failure, never "no route"); `elevation_m`; `time_zone_id`,
`raw_offset_s` and `dst_offset_s`. The evidence's `provider` entry names the service, the endpoint and what was sent
(never the key), the status, and a hash of the bytes read.

## Alone and composed

Put `GOOGLE_MAPS_API_KEY=<your key>` in your key file (`~/.g6/.env`), and enable the Geocoding, Elevation, Time Zone,
Routes and Places APIs for that key in Google Cloud. Over MCP every lookup runs only when the person who runs the
server lists it in `G6_MCP_SENSITIVE`: `g6_invoke("ctx_geo", "geocode", {"address": "1 Martin Place, Sydney",
"dry_run": false})`.

From Python: `CtxGeoBlock(clients=..., gate=...)`; `clients` maps a backend to a `g6.core.http.Client` (tests pass a
transport double), `gate` replaces `g6.action_gating`. Register another backend with `BACKENDS.register(backend)`,
for example a self-hosted geocoder; an unpaid one needs no gate.

Composed: `g6.grounding` checks a claim about a place against the service's answer kept in the evidence.

## Failure modes it catches

- `answered_from_memory` (a factual question answered from the model's memory instead of a source): the answer
  comes from the service, the provider and request are in the evidence, and a failure is reported as the failure it
  is, so it is never mistaken for "nothing there" and filled in from memory.

## Blind spots

- `prompt_injection`: a place name or address the service returns can carry instructions; it is untrusted data
  (instruction-like phrasing is named on the card) and changes no outcome.
- Google is the only backend registered: its coverage and errors are its own.
- A route's duration is an estimate; a time zone's rules are the service's.
- Google's error message behind an HTTP error from the Routes or Places API is not kept, only the status.
- Each lookup costs money on the person's account; G6 does not know the price.
- Every request, dry runs included, is recorded in the action gate's audit under the G6 home with the addresses and
  coordinates it names.

## What it needs

A Google Maps Platform key in your key file (paid; `GOOGLE_MAPS_API_KEY`), and network access to
maps.googleapis.com, routes.googleapis.com and places.googleapis.com. No outside program, no extra, no model. Nothing
is cached: each call asks the service, so no answer is kept beyond the call.

## Examples

```g6-example op=backends
{}
```

Lists `google`, paid, with its key's name and whether your key file holds it.

```g6-example op=geocode
{"address": "1 Martin Place, Sydney NSW", "dry_run": false}
```

Sensitive: over MCP it runs only when the server's owner allows it; it is sent only on the gate's allow.

```python
from g6.sources.ctx_geo import CtxGeoBlock, CtxGeoInput

out = CtxGeoBlock().infer(CtxGeoInput(op="geocode", address="1 Martin Place, Sydney")).unwrap()
assert out["code"] == "G6_E_GEO_DRY_RUN" and out["would_send"] == {"address": "1 Martin Place, Sydney"}
```

## The four open problems

- **Learning signals**: each reply records the service's status, the outcome and the request. Not yet: they are not
  collected across calls.
- **Evaluators the model cannot influence**: the service's own answer and status, mapped in fixed code; no model is
  called, and the model's guess is never asked.
- **Transfer by failure mode**: bound to `answered_from_memory`; the same status mapping applies to every backend
  registered.
- **Enough held-out cases**: Not yet: the tests use recorded replies for each status; there is no frozen set of
  real responses.
