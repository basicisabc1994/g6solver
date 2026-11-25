# SPDX-License-Identifier: Apache-2.0
"""MCP-only progressive disclosure, joined to actual registrations and the routing manifest."""
from __future__ import annotations

from copy import copy, deepcopy
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

    listed = _listed(server)
    if "g6_describe" not in dict(listed):
        raise ValueError("short tool descriptions require g6_describe on the same server")
    details = {}
    attached = []
    for name, tool in listed:
        guidance = tool.description or ""
        schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {}
        details[name] = {"guidance": guidance, "schema": deepcopy(schema)}
        # A tool can be registered on more than one server. Only this server's
        # copy may advertise its local g6_describe target.
        compact = copy(tool)
        compact.description = short_description(name, guidance)
        attached.append((name, compact))
    for name, tool in attached:
        server.remove_tool(name)
        server.add_tool(tool)
    _DETAILS[server_id] = details


def _server_details(server_id: str, *, listed: list[tuple[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Read full guidance from the server that owns it, including unshortened SDK servers."""
    if server_id not in _DETAILS:
        if listed is None:
            from g6.mcp.registrations import SERVERS, _listed, _load
            server = _load(SERVERS[server_id])
            if server_id not in _DETAILS:
                listed = _listed(server)
        if server_id not in _DETAILS:
            _DETAILS[server_id] = {
                name: {"guidance": tool.description or "",
                       "schema": deepcopy(getattr(tool, "parameters", None) or
                                          getattr(tool, "inputSchema", None) or {})}
                for name, tool in listed}
    return _DETAILS[server_id]


def full_guidance(server_id: str, tool: str) -> str:
    return _server_details(server_id)[tool]["guidance"]


def _unavailable(server_id: str) -> dict[str, Any]:
    """Fixed public recovery, never an import error's text (which may contain host paths)."""
    from g6.mcp.registrations import EXTRAS

    extra = EXTRAS[server_id]
    return {"available": False, "reason": "server_unavailable",
            "note": "G6 could not load or list this member's MCP server on this computer.",
            "install": f"pip install 'g6[{extra}] @ git+https://github.com/basicisabc1994/g6solver'"}


def inventory(family: str | None = None) -> list[dict[str, Any]]:
    """Registered members, plus declared tools whose own server cannot load; unrelated servers stay unloaded."""
    from g6.mcp import registrations
    from g6.mcp.dependencies import optional_dependency_missing, optional_import_roots
    from g6.skills import corpus, routing

    rows = []
    declared: dict[str, dict[str, str]] = {}
    for identity, entry in routing.load().entries.items():
        if not identity.startswith("mcp:") or not entry.agent_facing:
            continue
        member_family = family_for({"id": identity, "kind": "mcp"})
        if family is None or member_family == family:
            server_id, name = identity[4:].split("/", 1)
            declared.setdefault(server_id, {})[name] = member_family
    for server_id, target in sorted(registrations.SERVERS.items()):
        members = declared.get(server_id, {})
        if family is not None and not members:
            continue
        try:
            server = registrations._load(target)
            listed = dict(registrations._listed(server))
            details = _server_details(server_id, listed=list(listed.items()))
        except (Exception, SystemExit) as exc:
            if not optional_dependency_missing(server_id, exc):
                raise
            # A missing optional server affects its declared members only. Keep
            # each identity visible, without inventing a schema or importing peers.
            for name, member_family in members.items():
                call = {"tool": name, "arguments": {}}
                if server_id != "g6":
                    call["server"] = server_id
                rows.append({"id": f"mcp:{server_id}/{name}", "kind": "tool", "family": member_family,
                             "summary": "This tool's MCP server is unavailable.", "call": call,
                             **_unavailable(server_id)})
            continue
        if extra := registrations.EXTRAS.get(server_id):
            # Healthy imports establish the same file aliases as failure-time
            # probes, before a later inventory can observe those files missing.
            optional_import_roots(extra)
        for name, tool in listed.items():
            identity = f"mcp:{server_id}/{name}"
            member_family = family_for({"id": identity, "kind": "mcp"})
            if family is not None and member_family != family:
                continue
            schema = getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {}
            properties = schema.get("properties", {})
            arguments = {key: corpus._placeholder(key, corpus._json_kind(properties.get(key, {})))
                         for key in schema.get("required", [])}
            call = {"tool": name, "arguments": arguments}
            if server_id != "g6":
                call["server"] = server_id
            rows.append({"id": identity, "kind": "tool", "family": member_family, "available": True,
                         "summary": " ".join(details[name]["guidance"].split())[:160], "call": call})
        for name in members.keys() - listed.keys():
            call = {"tool": name, "arguments": {}}
            if server_id != "g6":
                call["server"] = server_id
            rows.append({"id": f"mcp:{server_id}/{name}", "kind": "tool", "family": members[name],
                         "summary": "The declared tool is absent from this server.", "call": call,
                         "available": False, "reason": "tool_unavailable"})
    entries = corpus.entries()
    routes = corpus._routes([entry for entry in entries if entry["kind"] == "tool"])
    for entry in [*routes, *corpus._skills(), *corpus._bundled_skills(),
                  *(entry for entry in entries if entry["kind"] == "op")]:
        member_family = family_for(entry)
        if family is not None and member_family != family:
            continue
        rows.append({"id": entry["id"], "kind": entry["kind"], "family": member_family,
                     "summary": " ".join(entry.get("when", entry["text"]).split())[:160],
                     "call": deepcopy(entry["call"])})
    return sorted(rows, key=lambda row: row["id"])


def describe(name: str) -> dict[str, Any] | None:
    """Public built-in MCP guidance only; unknown names continue through the existing catalogue gate."""
    if name in FAMILIES:
        return {"name": name, "available": True, "purpose": FAMILIES[name].purpose,
                "members": inventory(name)}
    from g6.mcp import registrations
    from g6.mcp.dependencies import optional_dependency_missing
    from g6.skills import corpus, routing

    manifest = routing.load()
    matches = [(server_id, name) for server_id in registrations.SERVERS
               if f"mcp:{server_id}/{name}" in manifest.entries]
    if name == "g6_describe" and ("g6", name) in matches:
        # The standalone pointer target is local to abstraction. Unqualified
        # discovery here still describes the main server's existing entry point.
        matches = [("g6", name)]
    if len(matches) > 1:
        raise ValueError(f"ambiguous tool identity: {name}")
    for server_id, tool_name in matches:
        try:
            details = _server_details(server_id).get(tool_name)
            if details is not None and "schema" not in details:
                server = registrations._load(registrations.SERVERS[server_id])
                details = {**details, "schema": registrations._tools(server)[tool_name]}
        except (Exception, SystemExit) as exc:
            if not optional_dependency_missing(server_id, exc):
                raise
            return {"name": name, **_unavailable(server_id)}
        if details is None:
            return {"name": name, "available": False, "note": "The declared tool is absent from this server."}
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
