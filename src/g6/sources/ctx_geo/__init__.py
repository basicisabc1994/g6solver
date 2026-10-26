# SPDX-License-Identifier: Apache-2.0
"""g6.sources.ctx_geo — answer location questions from a map service, with its status read on every reply, so a refused key or a used-up quota is never "no results".

An extension in the sources family. ``geocode``, ``reverse_geocode``, ``directions``, ``distance_matrix``,
``search_places``, ``elevation`` and ``timezone`` ask a map backend (:data:`BACKENDS`; ``google``, Google Maps
Platform's documented web services, by default) through ``g6.core.http``, and map the service's own status on every
reply to a typed outcome: ``ok``, ``empty`` (the service found nothing), or the failure it was (``auth_failed``,
``quota_exceeded``, ``rejected``, ``not_found``, ``server_error``, ``parse_failed``, ``unreachable``...). An address
with more than one match returns every candidate, marked ambiguous. The provider and the request (never the key) are
in the evidence, and nothing is cached.

    from g6.core.registry import get_registry
    get_registry().invoke("ctx_geo", "geocode", {"address": "1 Martin Place, Sydney", "dry_run": False})

Every lookup is sensitive (it reaches the network and, with google, spends money). It is a dry run unless asked
otherwise, and then a paid request is sent only on ``g6.action_gating``'s allow: the gate reads the backend's key in
the person's key file, ``~/.g6/.env`` (``__g6_keys__``), as their approval to spend it. ``backends`` lists the
backends and sends nothing.

Not ported from the archive's ctx_geo, and why: the MCP sub-package, its store and its 720-hour geocode cache (Google
Maps Platform's terms limit caching; nothing is kept here); static maps, place details, batch geocoding, weather and
air quality; the googlemaps package (its own networking, outside g6.core.http); the key read from the environment and
the hosted entitlement check; the model rerank and the production gate; and the default that labelled every Google
answer verified.
"""
from g6.sources.ctx_geo import backends
from g6.sources.ctx_geo.backends import BACKENDS, GOOGLE_KEY, GeoAnswer, GeoBackend, GoogleBackend
from g6.sources.ctx_geo.block import SENSITIVE_OPS, CtxGeoBlock, CtxGeoInput

__g6_component__ = True
__g6_block__ = "CtxGeoBlock"
__g6_family__ = "sources"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # each reaches the network and, with google, spends money
__g6_requires__: tuple[str, ...] = ("action_gating", "grounding", "knowledge")  # the gate; the shared reply and Source
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a place name the
# service returns can carry instructions an agent might follow; a paid service used without the person choosing it).
__g6_mitigates__: tuple[str, ...] = ("answered_from_memory",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()  # Google Maps Platform is the person's own paid account, reached over HTTPS, never assumed
__g6_extras__: dict[str, tuple[str, ...]] = {}
__g6_keys__ = (GOOGLE_KEY,)  # from the person's key file; a key there approves spending it

__all__ = ["BACKENDS", "GOOGLE_KEY", "SENSITIVE_OPS", "CtxGeoBlock", "CtxGeoInput", "GeoAnswer", "GeoBackend",
           "GoogleBackend", "backends"]
