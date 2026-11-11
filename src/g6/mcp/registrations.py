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
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: Server id -> ``module:attribute``. The attribute is the server, or a function that builds it.
SERVERS: dict[str, str] = {"g6": "g6.mcp:build_server", "abstraction": "g6.abstraction.server:mcp"}
#: The extra each server needs beyond the base install.
EXTRAS: dict[str, str] = {"g6": "mcp", "abstraction": "abstraction,mcp"}
#: Words kept by hand (see ``DIALOG_WORDS`` below). No registered tool takes a dialog word as an argument, except those
#: listed in ``DIALOG_EXEMPT`` with what the argument really is.
_HAND_KEPT_WORDS = frozenset({"answer", "approved", "confirm", "decision", "name", "choice"})
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


def dialog_fields() -> frozenset[str]:
    """The fields a person answers in G6's dialogs, read from the code that sends them: the schema ``request_consent``
    hands the client, and the properties ``behaviour_question.form`` builds."""
    from types import SimpleNamespace

    from g6.harness import behaviour_question
    from g6.mcp import consent

    sent: list[dict[str, Any]] = []

    class _Session:
        async def elicit_form(self, *, message: str, requestedSchema: dict, related_request_id: Any) -> Any:
            sent.append(requestedSchema)
            return SimpleNamespace(action="cancel", content=None)

    _drive(consent.request_consent(SimpleNamespace(session=_Session(), request_id="r"), "?"))
    fields = {name for schema in sent for name in schema.get("properties", {})}
    for node in ast.walk(ast.parse(inspect.getsource(behaviour_question.form).lstrip())):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name) and target.id == "properties" and isinstance(node.value, ast.Dict):
                    fields |= {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
                if isinstance(target, ast.Subscript) and getattr(target.value, "id", "") == "properties" \
                        and isinstance(target.slice, ast.Constant):
                    fields.add(target.slice.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update" \
                and getattr(node.func.value, "id", "") == "properties":
            for argument in node.args:
                if isinstance(argument, ast.Dict):
                    fields |= {key.value for key in argument.keys if isinstance(key, ast.Constant)}
    if not sent or not fields:
        raise RegistrationError("the dialogs' fields could not be read from the code that sends them")
    return frozenset(str(name) for name in fields)


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


#: Argument names that would let a tool's caller answer for the person: every field G6's dialogs ask a person for
#: (``dialog_fields``, read from the code), and six words kept by hand for dialogs a later tool might build.
DIALOG_WORDS = _HAND_KEPT_WORDS | dialog_fields()


def recorded(servers: Mapping[str, str] | None = None) -> dict[str, tuple[str, ...]]:
    """Server id -> the sorted names of the tools it registers, for every server in ``SERVERS`` (or ``servers``).
    Builds or imports each server and reads its tools; never calls ``run``; no network, no model. Raises
    ``RegistrationError``, naming the server and its extra, when one cannot be built."""
    return {server_id: tuple(sorted(tools)) for server_id, tools in _all(servers).items()}


def arguments(servers: Mapping[str, str] | None = None) -> dict[str, dict[str, tuple[str, ...]]]:
    """Server id -> tool -> the sorted names of the arguments its input schema takes."""
    return {server_id: {tool: tuple(sorted(schema.get("properties", {}))) for tool, schema in sorted(tools.items())}
            for server_id, tools in _all(servers).items()}


#: The modules whose ``FastMCP`` class or low-level ``Server`` class makes an MCP server.
_SERVER_CLASSES = {"fastmcp": ("FastMCP",), "mcp.server.fastmcp": ("FastMCP",), "mcp.server.lowlevel": ("Server",),
                   "mcp.server": ("Server", "FastMCP")}


def _constructions(tree: ast.Module) -> int | None:
    """How many MCP servers a module's source constructs, however the constructor is reached: an imported class under
    any alias, a class reached through an imported module or package, or a subclass of either. ``None`` when the module
    has nothing to do with a server class; a module that imports one and constructs none counts as one."""
    classes: set[str] = set()                  # local names bound to a server class
    modules: dict[str, tuple[str, ...]] = {}   # local dotted names bound to a module, with its server classes
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name in _SERVER_CLASSES.get(node.module, ()):
                    classes.add(alias.asname or alias.name)
                elif f"{node.module}.{alias.name}" in _SERVER_CLASSES:
                    modules[alias.asname or alias.name] = _SERVER_CLASSES[f"{node.module}.{alias.name}"]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _SERVER_CLASSES:
                    modules[alias.asname or alias.name] = _SERVER_CLASSES[alias.name]

    def is_server_class(expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in classes
        return isinstance(expr, ast.Attribute) and expr.attr in modules.get(ast.unparse(expr.value), ())

    for _ in range(4):                         # subclasses of subclasses, to a small fixed depth
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(is_server_class(base) for base in node.bases):
                classes.add(node.name)
    if not classes and not modules:
        return None
    built = sum(isinstance(node, ast.Call) and is_server_class(node.func) for node in ast.walk(tree))
    return max(built, 1)


def server_sources(root: Path) -> frozenset[str]:
    """The modules under ``root`` (the ``g6`` package folder) whose source constructs an MCP server, found by what
    each module imports (aliases, modules and subclasses resolved), not by a spelled name. A module that constructs
    more than one server is listed once for each beyond the first, as ``<module>#2``, ``#3``: a second server inside a
    known server module is not the first, and ``routing.check`` reports it. It finds servers, not tools."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "mcp" not in text.lower():
            continue
        count = _constructions(ast.parse(text))
        if count:
            parts = path.relative_to(root).with_suffix("").parts
            module = ".".join((root.name, *(parts[:-1] if parts[-1] == "__init__" else parts)))
            found |= {module, *(f"{module}#{n}" for n in range(2, count + 1))}
    return frozenset(found)


#: The ops every block answers about itself, which are no members: they route nowhere and need no entry.
META_OPS = frozenset({"ops", "help"})


def _typed_ops(block: Any) -> frozenset[str] | None:
    """The ops a block's typed input accepts (the ``Literal`` on its ``op`` field), or ``None`` when its input does
    not declare them that way."""
    try:
        hints = typing.get_type_hints(block.infer)
    except Exception:
        return None
    model = next((value for key, value in hints.items() if key != "return"), None)
    field = getattr(model, "model_fields", {}).get("op")
    if field is None or typing.get_origin(field.annotation) is not typing.Literal:
        return None
    return frozenset(typing.get_args(field.annotation))


def component_ops() -> dict[str, tuple[str, ...]]:
    """Component -> its ops: what its block lists for itself, which must be what its typed input accepts (the two
    meta-ops ``ops`` and ``help`` aside); ``()`` for a component that declares no block. A block whose ops cannot be
    listed, that lists an op twice, or whose list and typed input disagree is an error, never fewer members: an op
    the typed input accepts runs, so it is classified or the inventory stops."""
    from g6.core import discovery
    from g6.core.registry import get_registry

    registry, out = get_registry(), {}
    for name in registry.names():
        listed = tuple(discovery.ops(name))
        block = registry.get_class(name)
        if not listed and block is not None:
            raise RegistrationError(f"the component {name} declares a block, and its ops could not be listed")
        if len(set(listed)) != len(listed):
            raise RegistrationError(f"the component {name} lists an op twice: {sorted(listed)}")
        typed = _typed_ops(block) if block is not None else None
        if typed is not None:
            unlisted, untyped = typed - set(listed) - META_OPS, set(listed) - typed
            if unlisted or untyped:
                raise RegistrationError(f"the component {name}'s ops reply and its typed input disagree: accepted and "
                                        f"not listed {sorted(unlisted)}, listed and not accepted {sorted(untyped)}")
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
