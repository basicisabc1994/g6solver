# SPDX-License-Identifier: Apache-2.0
"""The offline recorder: what G6's MCP servers really register, read from the servers themselves (0.3 design, 14.1).

A scan of the source misses a tool registered in a loop, by ``add_tool`` or under another name, so ``recorded``
builds each known server exactly as it is built to be served and asks it for its tools. It never calls ``run``, opens
no socket, starts no event loop and calls no model. A server that cannot be built is an error naming the extra it
needs: the recorder never answers with a shorter list.

``inputs`` gathers everything ``g6.skills.routing.check`` joins with the routing manifest: the recorded tools, the ops
each component's block lists, the guidance texts an agent reads, and the files that exist.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: Server id -> ``module:attribute``. The attribute is the server, or a function that builds it.
SERVERS: dict[str, str] = {"g6": "g6.mcp:build_server", "abstraction": "g6.abstraction.server:mcp"}
#: The extra each server needs beyond the base install.
EXTRAS: dict[str, str] = {"g6": "mcp", "abstraction": "abstraction,mcp"}
#: Argument names that would let a tool's caller answer for the person. No registered tool takes one, except those
#: listed in ``DIALOG_EXEMPT`` with what the argument really is.
DIALOG_WORDS = frozenset({"answer", "approved", "confirm", "decision", "name", "choice"})
#: ``mcp:<server>/<tool>`` -> {argument: what it is}. Short on purpose: an entry here is read by a reviewer.
DIALOG_EXEMPT: dict[str, dict[str, str]] = {
    "mcp:abstraction/describe_domain": {"name": "the name of a loaded dataset, never a person's answer"},
    "mcp:abstraction/load_dataset": {"name": "the name the loaded dataset is kept under, never a person's answer"},
}


class RegistrationError(RuntimeError):
    """A server could not be built or listed, or a component's ops could not be read."""


def _load(target: str) -> Any:
    module, _, attribute = target.partition(":")
    found = getattr(importlib.import_module(module), attribute)
    return found() if inspect.isfunction(found) or inspect.isclass(found) else found


def _drive(listing: Any) -> Any:
    """Run a coroutine that needs no event loop to its result. ``asyncio.run`` would open a socket pair on Windows."""
    try:
        listing.send(None)
    except StopIteration as stop:
        return stop.value
    listing.close()
    raise RegistrationError("listing the tools waits on an event loop; the recorder starts none")


def _tools(server: Any) -> dict[str, dict[str, Any]]:
    """Tool name -> input schema, as the built server holds them (FastMCP's ``get_tools``, the SDK's ``list_tools``)."""
    lister = getattr(server, "get_tools", None) or getattr(server, "list_tools", None)
    if lister is None:
        raise RegistrationError(f"{type(server).__name__} lists its tools by neither get_tools nor list_tools")
    listed = lister()
    listed = _drive(listed) if inspect.iscoroutine(listed) else listed
    tools = list(listed.items()) if isinstance(listed, Mapping) else [(tool.name, tool) for tool in listed]
    out: dict[str, dict[str, Any]] = {}
    for name, tool in tools:
        if name in out:
            raise RegistrationError(f"the tool {name} is listed twice")
        out[str(name)] = dict(getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {})
    return out


def _all(servers: Mapping[str, str] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for server_id, target in sorted((SERVERS if servers is None else servers).items()):
        try:
            out[server_id] = _tools(_load(target))
        except (Exception, SystemExit) as exc:
            extra = EXTRAS.get(server_id, "")
            needs = (f"; it needs the extra: pip install 'g6[{extra}] @ git+https://github.com/basicisabc1994/g6solver'"
                     if extra else "")
            raise RegistrationError(f"the MCP server {server_id!r} ({target}) could not be recorded{needs}. "
                                    f"{type(exc).__name__}: {exc}") from exc
    return out


def recorded(servers: Mapping[str, str] | None = None) -> dict[str, tuple[str, ...]]:
    """Server id -> the sorted names of the tools it registers, for every server in ``SERVERS`` (or ``servers``).
    Builds or imports each server and reads its tools; never calls ``run``; no network, no model. Raises
    ``RegistrationError``, naming the server and its extra, when one cannot be built."""
    return {server_id: tuple(sorted(tools)) for server_id, tools in _all(servers).items()}


def arguments(servers: Mapping[str, str] | None = None) -> dict[str, dict[str, tuple[str, ...]]]:
    """Server id -> tool -> the sorted names of the arguments its input schema takes."""
    return {server_id: {tool: tuple(sorted(schema.get("properties", {}))) for tool, schema in sorted(tools.items())}
            for server_id, tools in _all(servers).items()}


def server_sources(root: Path) -> frozenset[str]:
    """The modules under ``root`` (the ``g6`` package folder) whose source constructs an MCP server: a call of
    ``FastMCP(...)``, or an import of the SDK's low-level ``Server``. It finds servers, not tools."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "FastMCP" not in text and "mcp.server" not in text:
            continue
        constructs = False
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call):
                called = node.func
                constructs |= (called.id if isinstance(called, ast.Name) else getattr(called, "attr", "")) == "FastMCP"
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("mcp.server"):
                constructs |= any(alias.name == "Server" for alias in node.names)
        if constructs:
            parts = path.relative_to(root).with_suffix("").parts
            found.add(".".join((root.name, *(parts[:-1] if parts[-1] == "__init__" else parts))))
    return frozenset(found)


def component_ops() -> dict[str, tuple[str, ...]]:
    """Component -> the ops its block lists for itself; ``()`` for a component that declares no block. A component
    that declares a block whose ops cannot be listed is an error, never zero members."""
    from g6.core import discovery
    from g6.core.registry import get_registry

    registry, out = get_registry(), {}
    for name in registry.names():
        listed = tuple(discovery.ops(name))
        if not listed and registry.get_class(name) is not None:
            raise RegistrationError(f"the component {name} declares a block, and its ops could not be listed")
        if len(set(listed)) != len(listed):
            raise RegistrationError(f"the component {name} lists an op twice: {sorted(listed)}")
        out[name] = listed
    return out


def package_root() -> Path:
    import g6

    return Path(g6.__file__).resolve().parent


def guidance(repo: Path | None = None) -> dict[str, str]:
    """Every guidance text an agent reads, found by glob and never listed by hand: the root skill and its references,
    any ``AGENTS.md`` inside the package, ``g6_help``'s text, and, in a source checkout (``repo``, or the folder two
    above the package when it holds ``plugins``), each plugin's skills and ``AGENTS.md``. Keys are paths inside the
    package, ``repo:<path>`` for a checkout's files, and ``help:g6_help``."""
    from g6.core import discovery

    package = package_root()
    texts = {path.relative_to(package).as_posix(): path.read_text(encoding="utf-8")
             for found in ((package / "skills" / "using-g6").rglob("*.md"), package.rglob("AGENTS.md"))
             for path in found}
    repo = package.parent.parent if repo is None else repo
    plugins = repo / "plugins"
    if plugins.is_dir():
        for path in sorted({*plugins.glob("**/skills/**/*.md"), *plugins.rglob("AGENTS.md")}):
            texts["repo:" + path.relative_to(repo).as_posix()] = path.read_text(encoding="utf-8")
    texts["help:g6_help"] = discovery.HELP
    return texts


def inputs(repo: Path | None = None) -> dict[str, Any]:
    """The keyword arguments of ``g6.skills.routing.check`` as the code stands: ``check(load(), **inputs()) == []`` is
    the inventory test, and any change that adds a tool or an op runs it."""
    import g6
    from g6.skills import routing

    package = package_root()
    texts = guidance(repo)
    files = frozenset(path.relative_to(package).as_posix() for path in package.rglob("*.md"))
    wanted = {path for route in routing.load().routes.values() for path in route.detail}
    details = {path: (package / path).read_text(encoding="utf-8") for path in sorted(wanted & files - set(texts))}
    return {"registrations": recorded(), "ops": component_ops(), "texts": texts, "details": details,
            "root_words": routing.root_words(texts[routing.ROOT]), "version": g6.__version__, "files": files,
            "sources": server_sources(package), "servers": dict(SERVERS)}
