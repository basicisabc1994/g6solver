# SPDX-License-Identifier: Apache-2.0
"""MCP-only progressive disclosure, joined to actual registrations and the routing manifest."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from g6.core.tool_guidance import _DETAILS, register_guidance as register_guidance, short_description


@dataclass(frozen=True)
class Family:
    purpose: str
    routes: tuple[str, ...]
    skills: tuple[str, ...] = ()
    component_families: tuple[str, ...] = ()


# Membership policy, once per route or skill. Tools and ops come from registrations, never a tool-name list.
FAMILIES = {
    "discover": Family("Find the next call.", ("discover", "select"), ("using-superpowers",)),
    "engineering": Family("Plan and build changes.", ("mission", "question"), (
        "brainstorming", "writing-plans", "using-git-worktrees", "test-driven-development",
        "subagent-driven-development", "executing-plans", "dispatching-parallel-agents",
        "systematic-debugging", "writing-skills"), ("code", "workspace")),
    "review": Family("Check patches before completion.", ("review",), (
        "verification-before-completion", "requesting-code-review", "receiving-code-review",
        "finishing-a-development-branch")),
    "consent": Family("Manage sharing with consent.", ("consent",)),
    "retrieval": Family("Retrieve cited source passages.", ("context",), ("g6-ctx-langextract",),
                        component_families=("knowledge", "memory", "sources")),
    "compose": Family("Connect typed operations.", ("compose",)),
    "logic": Family("Reason over rules and abstractions.", ("rules", "abstraction"),
                    component_families=("cogarch", "provers", "domains")),
    "fit": Family("Evaluate and improve applications.", ("fit", "improve"), component_families=("ml",)),
    "workflow": Family("Manage review records and feedback.", ("workflow",)),
    "media": Family("Process audio, images and video.", (), component_families=("media",)),
    "automation": Family("Connect agents, integrations and physical adapters.", (),
                         component_families=("agents", "jobs", "deploy", "integrations", "embodied")),
}


def family_table() -> str:
    return "\n".join(f"{name}: {family.purpose}" for name, family in FAMILIES.items())


def _only_family(matches: list[str], member: str) -> str:
    if len(matches) != 1:
        raise ValueError(f"{member} needs exactly one family; found {matches}")
    return matches[0]


def family_for(entry: dict[str, Any]) -> str:
    """Use typed entry identity and the declared route, never words in guidance or outcomes."""
    from g6.skills import routing

    kind, identity = entry["kind"], entry["id"]
    if kind == "skill":
        return _only_family([name for name, f in FAMILIES.items() if identity[6:] in f.skills], identity)
    if kind == "library":
        if identity != "library:compose":
            raise ValueError(f"{identity} has no family")
        return "compose"
    manifest = routing.load()
    if kind == "route":
        route = identity[6:]
    else:
        key = f"mcp:g6/{identity[5:]}" if kind == "tool" else identity
        if kind == "op":
            key = "op:" + identity[3:].replace(".", "/", 1)
        member = manifest.entries.get(key)
        if member is None or not member.agent_facing:
            raise ValueError(f"{identity} has no declared family route")
        route = member.route
        if kind == "op":
            from g6.core.registry import FAMILIES as COMPONENT_FAMILIES
            from g6.mcp.catalogue import shipped

            component = identity[3:].split(".", 1)[0]
            registered_module = shipped().get(component)
            if not registered_module:
                raise ValueError(f"{identity} has no shipped component record for its family")
            module = registered_module.split(".")
            if len(module) > 2:
                if module[1] not in COMPONENT_FAMILIES:
                    raise ValueError(f"{identity} has an unknown component family")
                return _only_family([name for name, f in FAMILIES.items()
                                     if module[1] in f.component_families], identity)
    return _only_family([name for name, f in FAMILIES.items() if route in f.routes], identity)


def compact_server(server: Any, server_id: str) -> None:
    """Shorten registered descriptions, preserving the actual schema, callable and complete guidance."""
    from g6.mcp.registrations import _listed

    details = {}
    for name, tool in _listed(server):
        guidance = tool.description or ""
        schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {}
        details[name] = {"guidance": guidance, "schema": deepcopy(schema)}
        tool.description = short_description(name, guidance)
    _DETAILS[server_id] = details


def full_guidance(server_id: str, tool: str) -> str:
    if server_id not in _DETAILS:
        from g6.mcp.registrations import SERVERS, _load
        _load(SERVERS[server_id])
    return _DETAILS[server_id][tool]["guidance"]


def inventory() -> list[dict[str, Any]]:
    """Every registered tool, route, vendored skill and runnable op, once, with its call template."""
    from g6.mcp import registrations
    from g6.skills import corpus

    rows = []
    for server_id, server in registrations._built():
        for name, tool in registrations._listed(server):
            identity = f"mcp:{server_id}/{name}"
            family = family_for({"id": identity, "kind": "mcp"})
            schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {}
            properties = schema.get("properties", {})
            arguments = {key: corpus._placeholder(key, corpus._json_kind(properties.get(key, {})))
                         for key in schema.get("required", [])}
            call = {"tool": name, "arguments": arguments}
            if server_id != "g6":
                call["server"] = server_id
            rows.append({"id": identity, "kind": "tool", "family": family,
                         "summary": " ".join(full_guidance(server_id, name).split())[:160], "call": call})
    entries = corpus.entries()
    routes = corpus._routes([entry for entry in entries if entry["kind"] == "tool"])
    for entry in [*routes, *corpus._skills(), *corpus._bundled_skills(),
                  *(entry for entry in entries if entry["kind"] == "op")]:
        family = family_for(entry)
        rows.append({"id": entry["id"], "kind": entry["kind"], "family": family,
                     "summary": " ".join(entry.get("when", entry["text"]).split())[:160],
                     "call": deepcopy(entry["call"])})
    return sorted(rows, key=lambda row: row["id"])


def describe(name: str) -> dict[str, Any] | None:
    """Public built-in MCP guidance only; unknown names continue through the existing catalogue gate."""
    if name in FAMILIES:
        return {"name": name, "available": True, "purpose": FAMILIES[name].purpose,
                "members": [row for row in inventory() if row["family"] == name]}
    from g6.mcp import registrations
    from g6.skills import corpus, routing

    manifest = routing.load()
    matches = [(server_id, name) for server_id in registrations.SERVERS
               if f"mcp:{server_id}/{name}" in manifest.entries]
    if len(matches) > 1:
        raise ValueError(f"ambiguous tool identity: {name}")
    for server_id, tool_name in matches:
        if server_id not in _DETAILS:
            registrations._load(registrations.SERVERS[server_id])
        details = _DETAILS[server_id].get(tool_name)
        if details is None:
            return {"name": name, "available": False, "note": "The declared tool is absent from this server."}
        if "schema" not in details:
            server = registrations._load(registrations.SERVERS[server_id])
            details = {**details, "schema": registrations._tools(server)[tool_name]}
        schema = details["schema"]
        arguments = {key: corpus._placeholder(key, corpus._json_kind(schema.get("properties", {}).get(key, {})))
                     for key in schema.get("required", [])}
        family = family_for({"id": f"mcp:{server_id}/{name}", "kind": "mcp"})
        call = {"tool": name, "arguments": arguments}
        if server_id != "g6":
            call["server"] = server_id
        guidance = details["guidance"]
        if name == "g6_select":
            guidance = ("MCP preserves the requested limit up to eight candidates, their fields and family lines, plus its envelope. "
                        "Explicit considering inputs retain their assessment; refusals retain recovery. "
                        "The detailed engine record described below remains internal.\n\n" + guidance)
        elif name == "g6_describe":
            guidance = ("Give a family name for its registered tools, skill routes and operations with call templates. "
                        "Give a tool name for its full guidance; component descriptions keep their existing scope.\n\n"
                        + guidance)
        return {"name": name, "available": True, "family": family, "call": call,
                "guidance": guidance}
    return None


def compact_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add family links without changing a caller's limit, records or extension disclosures."""
    out = deepcopy(rows)
    for row in out:
        if "id" not in row:
            continue  # component-only results retain their historical shape
        family = family_for(row)
        row["families"] = [f"{family}: {FAMILIES[family].purpose}"]
    return out


def compact_selection(reply: dict[str, Any], *, considering: bool = False) -> dict[str, Any]:
    """Family links are additive; all selector diagnostics and recovery fields survive."""
    out = deepcopy(reply)
    if out.get("ok") is True:
        out["candidates"] = compact_candidates(out["candidates"])
    return out
