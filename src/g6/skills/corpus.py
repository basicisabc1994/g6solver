# SPDX-License-Identifier: Apache-2.0
"""The selector's corpus, as data: one entry for every next call ``g6_select`` can offer (plan revision 8, phase B).

``corpus.json``, beside this file, is written by ``g6.mcp.corpus_build`` from the code and checked in; a test builds
it again and compares, so it cannot drift. This module only reads it: it imports the standard library alone, so core
(``g6.core.selector``, ``agent_guidance``, ``refusals``) reads the corpus without importing ``g6.mcp`` or
``g6.metaharness``, even indirectly (pyproject's import contracts). ``g6.mcp.corpus_build`` says what the file holds.

    python -P -m g6.mcp.corpus_build --write     # after a tool, route or op changes
    python -P -m g6.mcp.corpus_build --check     # exit 1 when the file is stale
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

FORMAT = "g6.corpus/1"
PATH = Path(__file__).resolve().with_name("corpus.json")
PHASES = ("plan", "implement", "verify", "diagnose", "stuck")
#: Cost classes, cheapest first: ``low`` reads or computes locally; ``medium`` runs a program or tests, or asks the
#: person; ``high`` calls a model or runs a loop of runs.
COSTS = ("low", "medium", "high")
#: Servers the recorder knows that ``g6-mcp`` does not register by default, with the reason none of their tools is
#: offered. The routing check reads this as each of their tools' exemption.
SERVERS_NOT_SELECTED: dict[str, str] = {
    "abstraction": "the abstraction server is a separate server the person registers alone "
                   "(python -P -m g6.abstraction.server); g6-mcp does not register it, so g6_select never offers it",
}


def entries(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every entry in full: an op's entry joined with what its component shares (what it establishes, from the
    contract's guarantees or its help line; what it does not, from ``bad_at``; its needs, the failure modes it
    addresses as hypothesis ids, and its typed input's fields). Deep copies: a caller that fills in a template it
    read here changes nothing a later call returns."""
    return copy.deepcopy(_expanded(load() if data is None else data))


def _expanded(data: dict[str, Any]) -> list[dict[str, Any]]:
    """``entries`` without the copy, for a caller that copies its own reply (``g6.core.selector.select``); it holds
    the cached corpus's own objects, so nothing may change what it returns."""
    out = []
    for entry in data["entries"]:
        if entry["kind"] != "op":
            out.append(entry)
            continue
        shared = data["components"][entry["component"]]
        component, op = entry["id"][3:].split(".", 1)
        out.append({**entry,
                    "establishes": ("This op is sensitive. The person must allow it themselves; tell the person."
                                    if entry["sensitive"] else "; ".join(shared["guarantees"]) or entry["text"]),
                    "not_established": "; ".join(shared["bad_at"]) or "not stated by the component: read g6_describe",
                    "needs": {"sensitive": f"{component}.{op}" if entry["sensitive"] else "", **shared["needs"]},
                    "hypotheses": shared["mitigates"], "fields": shared["fields"]})
    return out


@lru_cache(maxsize=1)
def _loaded(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except (RecursionError, MemoryError):
        raise json.JSONDecodeError("a JSON record exceeds decoder limits", "", 0) from None


def load() -> dict[str, Any]:
    """The checked-in corpus (read once per change of the file's text). Callers must not change what it returns."""
    return _loaded(PATH.read_text(encoding="utf-8"))


def selectable(registrations: dict[str, Any] | None = None) -> dict[str, str]:
    """Member key (``mcp:<server>/<tool>``, ``op:<component>/<op>``) -> ``"corpus"`` for each member the corpus offers
    with text, or the reason it is not offered. ``g6.skills.routing.check`` reads it: an agent-facing member that is
    neither is a problem."""
    out: dict[str, str] = {}
    for entry in load()["entries"]:
        if not entry["text"].strip():
            continue
        if entry["kind"] == "tool":
            out[f"mcp:g6/{entry['call']['tool']}"] = "corpus"
        elif entry["kind"] == "op":
            component, op = entry["id"][3:].split(".", 1)
            out[f"op:{component}/{op}"] = "corpus"
    for server, reason in SERVERS_NOT_SELECTED.items():
        for tool in (registrations or {}).get(server, ()):
            out[f"mcp:{server}/{tool}"] = reason
    return out
