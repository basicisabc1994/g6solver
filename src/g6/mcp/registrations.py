# SPDX-License-Identifier: Apache-2.0
"""The offline recorder: what G6's MCP servers really register, read from the servers themselves (0.3 design, 14.1).

A scan of the source misses a tool registered in a loop, by ``add_tool`` or under another name, so ``recorded``
builds each known server exactly as it is built to be served and asks it for its tools. It never calls ``run``, opens
no socket, starts no event loop and calls no model. A server that cannot be built is an error naming the extra it
needs: the recorder never answers with a shorter list.

``inputs`` gathers everything ``g6.skills.routing.check`` joins with the routing manifest: the recorded tools, the ops
each component's block lists, the guidance texts an agent reads, and the files that exist.

Nothing here decides what a name is from how it is spelled. A server class is the class object an import really
binds (``server_sources``), a dialog's fields are the fields of the schema the dialog's own code returns
(``dialog_fields``), the ops a block accepts are read from its typed input through every wrapper (``_typed_ops``),
and the texts an agent is shown are read from the built servers (``shown``).
"""
from __future__ import annotations

import ast
import enum
import importlib
import inspect
import types
import typing
from dataclasses import dataclass
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


#: The modules that send a form to the person through the one transport (``g6.mcp.consent.elicit``) -> where
#: ``dialog_fields`` reads that form's schema. A test finds the senders from the code, by the object each imported
#: name is bound to: a module that sends a form and is not here fails it, so a new dialog's fields are read or the
#: inventory stops.
DIALOG_SCHEMAS: dict[str, str] = {
    "g6.mcp.consent": "request_consent: the schema it hands the client, read from what it sends",
    "g6.mcp.behaviour": "the behaviour form: the schema behaviour_question.form returns",
}
#: Components whose typed input does not say which ops it accepts (``op`` is a plain string, checked in the block's
#: own code), with the reason each is let through. For these the op inventory rests on the block's ``ops`` reply and
#: on the block refusing an op the reply does not list, which ``component_ops`` probes; an op the block accepts and
#: its reply leaves out is not seen. Short on purpose: an entry here is read by a reviewer, and a block that
#: declares its ops as a ``Literal`` leaves this list or the recorder stops.
OPS_READ_FROM_THE_REPLY: dict[str, str] = {
    "adapt_rq": "RQInput.op is a string; the block refuses any op outside its OPS tuple, which is its ops reply",
    "australian_legal_corpus": "its input's op is a string; the block refuses any op outside the table its ops "
                               "reply lists",
    "hat_orchestrator": "HATOrchestratorInput.op is a string; the block refuses any op outside its OPS tuple, "
                        "which is its ops reply",
}
#: An op no block has, for the probe above.
_NO_SUCH_OP = "g6_recorder_probe_of_an_op_that_does_not_exist"
#: What ``dialog_fields`` shows the behaviour form: a task, and a candidate a calling agent could give for it, with
#: a rule, so the form is built in both of its shapes.
_SAMPLE_TASK = "Make total(prices) add the prices. A negative price is refused."
_SAMPLE_CANDIDATE = {"requirement_id": "R1", "function": "shop.prices.total", "args": [[2, -3]],
                     "outcomes": {"A": {"returns": -1}, "B": {"raises": "ValueError"}},
                     "cited": "A negative price is refused.", "evidence": ["shop/prices.py: total sums the list"],
                     "rule": "A negative price is refused wherever it stands in the list."}


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


def _properties(schema: Any) -> set[str]:
    """Every field name a form schema asks for, at any depth."""
    found: set[str] = set()
    if isinstance(schema, Mapping):
        if isinstance(schema.get("properties"), Mapping):
            found |= {str(name) for name in schema["properties"]}
        for value in schema.values():
            found |= _properties(value)
    elif isinstance(schema, (list, tuple)):
        for value in schema:
            found |= _properties(value)
    return found


def dialog_fields() -> frozenset[str]:
    """The fields a person answers in G6's dialogs, read from the schemas the dialogs' own code produces: the one
    ``request_consent`` hands the client, and the ones ``behaviour_question.form`` returns for a question with and
    without a rule. Neither is read from source text: a form that sends another field, however it is written, adds
    that field here, and a form that cannot be built or returns no field is an error, never a shorter list."""
    from types import SimpleNamespace

    from g6.harness import behaviour_question
    from g6.mcp import consent

    sent: list[Any] = []

    class _Session:
        async def elicit_form(self, *, message: str, requestedSchema: dict, related_request_id: Any) -> Any:
            sent.append(requestedSchema)
            return SimpleNamespace(action="cancel", content=None)

    _drive(consent.request_consent(SimpleNamespace(session=_Session(), request_id="r"), "?"))
    try:
        [candidate] = behaviour_question.caller_candidates(_SAMPLE_TASK, [dict(_SAMPLE_CANDIDATE)])
        case, rule = candidate.questions("0" * 64)
        if rule is None:
            raise ValueError("the sample candidate offers no rule")
        forms = [behaviour_question.form(case, candidate)[1], behaviour_question.form(case, candidate, rule)[1]]
    except Exception as exc:  # noqa: BLE001 - whatever stops the form being built stops the inventory
        raise RegistrationError(f"the behaviour form could not be built to read its fields. {type(exc).__name__}: "
                                f"{exc}") from exc
    if not sent or any(not _properties(schema) for schema in (*sent, *forms)):
        raise RegistrationError("a dialog's schema asks for no field: the dialogs' fields could not be read")
    return frozenset(name for schema in (*sent, *forms) for name in _properties(schema))


def _listed(server: Any) -> list[tuple[str, Any]]:
    """(name, tool) for every tool the built server holds (FastMCP's ``get_tools``, the SDK's ``list_tools``)."""
    lister = getattr(server, "get_tools", None) or getattr(server, "list_tools", None)
    if lister is None:
        raise RegistrationError(f"{type(server).__name__} lists its tools by neither get_tools nor list_tools")
    listed = lister()
    listed = _drive(listed) if inspect.iscoroutine(listed) else listed
    return [(str(name), tool) for name, tool in (listed.items() if isinstance(listed, Mapping)
                                                 else [(tool.name, tool) for tool in listed])]


def _tools(server: Any) -> dict[str, dict[str, Any]]:
    """Tool name -> input schema, as the built server holds them."""
    out: dict[str, dict[str, Any]] = {}
    for name, tool in _listed(server):
        if name in out:
            raise RegistrationError(f"the tool {name} is listed twice")
        out[name] = dict(getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {})
    return out


def _built(servers: Mapping[str, str] | None = None) -> list[tuple[str, Any]]:
    """(server id, the built server) for every known server; a server that cannot be built is an error naming its
    extra."""
    out = []
    for server_id, target in sorted((SERVERS if servers is None else servers).items()):
        try:
            out.append((server_id, _load(target)))
        except (Exception, SystemExit) as exc:
            raise _unrecorded(server_id, target, exc) from exc
    return out


def _unrecorded(server_id: str, target: str, exc: BaseException) -> RegistrationError:
    extra = EXTRAS.get(server_id, "")
    needs = (f"; it needs the extra: pip install 'g6[{extra}] @ git+https://github.com/basicisabc1994/g6solver'"
             if extra else "")
    return RegistrationError(f"the MCP server {server_id!r} ({target}) could not be recorded{needs}. "
                             f"{type(exc).__name__}: {exc}")


def _all(servers: Mapping[str, str] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    targets = SERVERS if servers is None else servers
    for server_id, server in _built(servers):
        try:
            out[server_id] = _tools(server)
        except (Exception, SystemExit) as exc:
            raise _unrecorded(server_id, targets[server_id], exc) from exc
    return out


def shown(servers: Mapping[str, str] | None = None) -> dict[str, str]:
    """What each server shows an agent before any skill file is read, from the built servers: ``server:<id>`` -> the
    server's instructions and ``tool:<server>/<tool>`` -> the tool's registered description, each when it is not
    empty. Never listed by hand: a tool registered by any means is here with what it says."""
    out: dict[str, str] = {}
    targets = SERVERS if servers is None else servers
    for server_id, server in _built(servers):
        said = getattr(server, "instructions", None)
        if isinstance(said, str) and said.strip():
            out[f"server:{server_id}"] = said
        try:
            listed = _listed(server)
        except (Exception, SystemExit) as exc:
            raise _unrecorded(server_id, targets[server_id], exc) from exc
        for name, tool in listed:
            description = getattr(tool, "description", None)
            if isinstance(description, str) and description.strip():
                out[f"tool:{server_id}/{name}"] = description
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


#: Where the classes that make an MCP server are taken from: module -> class names. Read only to get the class
#: objects (``_server_classes``); the scan compares objects, so the same class imported from any other module, under
#: any name, is the same class.
_SERVER_CLASSES = {"fastmcp": ("FastMCP",), "mcp.server.fastmcp": ("FastMCP",), "mcp.server.lowlevel": ("Server",),
                   "mcp.server": ("Server", "FastMCP")}
#: A class defined in the scanned package on top of a server class.
_SUBCLASS = object()


@dataclass(frozen=True)
class _Inside:
    """A module of the scanned package, by its path inside it."""
    parts: tuple[str, ...]


def _server_classes() -> tuple[type, ...]:
    """The server classes as objects, from every distribution that is installed."""
    found: list[type] = []
    for module, names in _SERVER_CLASSES.items():
        try:
            loaded = importlib.import_module(module)
        except ImportError:
            continue
        found += [cls for cls in (getattr(loaded, name, None) for name in names)
                  if isinstance(cls, type) and not any(cls is known for known in found)]
    if not found:
        raise RegistrationError("no MCP server class could be imported, so no module can be held against one; it "
                                "needs the extra: pip install 'g6[mcp] @ git+https://github.com/basicisabc1994/g6solver'")
    return tuple(found)


def _bindings(tree: ast.Module, package: tuple[str, ...], top: str) -> tuple[list, list, list]:
    """What one module's source binds and calls, unresolved: its imports as (local name, module, attribute or
    ``None``), its assignments and class definitions as (local name, expressions, whether a class), its callees."""
    imports: list[tuple[str, str, str | None]] = []
    local: list[tuple[str, list[ast.expr], bool]] = []
    calls: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.asname, alias.name, None) if alias.asname
                               else (alias.name.split(".")[0], alias.name, ""))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                above = package[:len(package) - (node.level - 1)] if node.level - 1 <= len(package) else ()
                base = ".".join((top, *above, *(base.split(".") if base else ())))
            imports += [(alias.asname or alias.name, base, alias.name) for alias in node.names]
        elif isinstance(node, ast.Assign):
            local += [(target.id, [node.value], False) for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            local.append((node.target.id, [node.value], False))
        elif isinstance(node, ast.ClassDef):
            local.append((node.name, list(node.bases), True))
        elif isinstance(node, ast.Call):
            calls.append(node.func)
    return imports, local, calls


def server_sources(root: Path) -> frozenset[str]:
    """The modules under ``root`` (the ``g6`` package folder) that construct an MCP server. Each name a module
    imports is resolved to what it is bound to: a module of a server distribution is imported and asked for the
    attribute, a module of the scanned package is followed to its own bindings, a local name bound to either
    (``Maker = FastMCP``) is followed too, and a class built on a server class is one. A callee is a construction
    when the object it resolves to is one of the server classes or a subclass, whatever module it was taken from
    and whatever it is called there. A module that binds a server class, or a module that holds one, and constructs
    none counts as one. A module that constructs more than one server is listed once for each beyond the first, as
    ``<module>#2``, ``#3``: ``routing.check`` reports it. It finds servers, not tools. Not followed: a server class
    handed on by a distribution other than the ones the classes come from, and a class reached only when the code
    runs (a factory's return value)."""
    classes = _server_classes()
    outside_tops = {cls.__module__.split(".")[0] for cls in classes} | {name.split(".")[0] for name in _SERVER_CLASSES}
    files: dict[tuple[str, ...], Path] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).with_suffix("").parts
        files[rel[:-1] if rel[-1] == "__init__" else rel] = path
    raw = {parts: _bindings(ast.parse(path.read_text(encoding="utf-8", errors="replace")),
                            parts if path.name == "__init__.py" else parts[:-1], root.name)
           for parts, path in files.items()}
    env: dict[tuple[str, ...], dict[str, Any]] = {parts: {} for parts in files}

    def is_server(value: Any) -> bool:
        return value is _SUBCLASS or (isinstance(value, type) and issubclass(value, classes))

    def module(dotted: str) -> Any:
        parts = tuple(dotted.split("."))
        if parts[0] == root.name:
            return _Inside(parts[1:]) if len(parts) == 1 or parts[1:] in files else None
        if parts[0] in outside_tops or dotted == "importlib":
            try:
                return importlib.import_module(dotted)
            except Exception:  # noqa: BLE001 - a module that cannot be imported binds nothing here
                return None
        return None

    def attribute(base: Any, name: str) -> Any:
        if isinstance(base, _Inside):
            found = env.get(base.parts, {}).get(name)
            return found if found is not None else (_Inside((*base.parts, name)) if (*base.parts, name) in files
                                                    else None)
        if isinstance(base, types.ModuleType):
            found = getattr(base, name, None)
            return found if found is not None else module(f"{base.__name__}.{name}")
        return None

    def value(expr: ast.AST, mine: Mapping[str, Any]) -> Any:
        if isinstance(expr, ast.Name):
            return mine.get(expr.id)
        if isinstance(expr, ast.Attribute):
            return attribute(value(expr.value, mine), expr.attr)
        if isinstance(expr, ast.Call) and expr.args and isinstance(expr.args[0], ast.Constant) \
                and isinstance(expr.args[0].value, str) and value(expr.func, mine) is importlib.import_module:
            return module(expr.args[0].value)            # importlib.import_module("mcp.server.fastmcp").FastMCP
        return None

    changed = True
    while changed:
        changed = False
        for parts, (imports, local, _calls) in raw.items():
            mine = env[parts]
            found: list[tuple[str, Any]] = []
            for name, source, attr in imports:
                if attr == "*":
                    held = module(source)
                    found += list(env.get(held.parts, {}).items()) if isinstance(held, _Inside) else [
                        (key, got) for key, got in vars(held).items() if not key.startswith("_")] if held else []
                elif attr is None:
                    found.append((name, module(source)))
                elif attr == "":                        # ``import a.b.c`` binds ``a``, with ``a.b.c`` loaded
                    module(source)
                    found.append((name, module(name)))
                else:
                    found.append((name, attribute(module(source), attr)))
            for name, exprs, is_class in local:
                got = [value(expr, mine) for expr in exprs]
                if is_class:
                    found.append((name, _SUBCLASS if any(is_server(one) for one in got) else None))
                else:
                    found.append((name, got[0]))
            for name, got in found:
                if got is not None and not (name in mine and (mine[name] is got or mine[name] == got)):
                    mine[name] = got
                    changed = True

    def holds(one: Any) -> bool:
        return is_server(one) or (isinstance(one, types.ModuleType) and any(is_server(v) for v in vars(one).values()))

    out: set[str] = set()
    for parts, (_imports, _local, calls) in raw.items():
        mine = env[parts]
        built = sum(is_server(value(callee, mine)) for callee in calls)
        count = built or int(any(holds(one) for one in mine.values()))
        if count:
            name = ".".join((root.name, *parts))
            out |= {name, *(f"{name}#{n}" for n in range(2, count + 1))}
    return frozenset(out)


#: The ops every block answers about itself, which are no members: they route nowhere and need no entry.
META_OPS = frozenset({"ops", "help"})


def _accepted(annotation: Any) -> frozenset[str] | None:
    """The values an annotation of ``op`` accepts, read through every wrapper: a ``Literal``, an ``Annotated`` one,
    a union of them (``None`` aside), a named alias, an enumeration. ``None`` when it does not say (a plain string)."""
    annotation = getattr(annotation, "__value__", annotation)             # ``type Op = Literal[...]``
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        values = typing.get_args(annotation)
        return frozenset(values) if all(isinstance(one, str) for one in values) else None
    if origin is typing.Annotated:
        return _accepted(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        parts = [_accepted(one) for one in typing.get_args(annotation) if one is not type(None)]
        return frozenset().union(*parts) if parts and all(part is not None for part in parts) else None
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return frozenset(str(member.value) for member in annotation)
    return None


def _typed_ops(block: Any) -> frozenset[str] | None:
    """The ops a block's typed input accepts (what the annotation of its ``op`` field allows, ``_accepted``), or
    ``None`` when its input does not say."""
    try:
        hints = typing.get_type_hints(block.infer)
    except Exception:
        return None
    model = next((value for key, value in hints.items() if key != "return"), None)
    field = getattr(model, "model_fields", {}).get("op")
    return None if field is None else _accepted(field.annotation)


def component_ops() -> dict[str, tuple[str, ...]]:
    """Component -> its ops: what its block lists for itself, which must be what its typed input accepts (the two
    meta-ops ``ops`` and ``help`` aside); ``()`` for a component that declares no block. A block whose ops cannot be
    listed, that lists an op twice, or whose list and typed input disagree is an error, never fewer members: an op
    the typed input accepts runs, so it is classified or the inventory stops. A block whose typed input does not
    say which ops it accepts is an error too, unless ``OPS_READ_FROM_THE_REPLY`` names it with the reason; such a
    block has to refuse an op its reply does not list."""
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
        if block is not None and typed is None:
            if name not in OPS_READ_FROM_THE_REPLY:
                raise RegistrationError(f"the component {name}'s typed input does not say which ops it accepts, so "
                                        "its ops rest on its own reply alone: declare op as a Literal, or list the "
                                        "component in OPS_READ_FROM_THE_REPLY with the reason")
            if registry.invoke(name, _NO_SUCH_OP).__class__.__name__ == "Success":
                raise RegistrationError(f"the component {name} is in OPS_READ_FROM_THE_REPLY and does not refuse an "
                                        "op its reply does not list")
        elif name in OPS_READ_FROM_THE_REPLY and block is not None:
            raise RegistrationError(f"the component {name} declares its ops in its typed input now: take it out of "
                                    "OPS_READ_FROM_THE_REPLY")
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
    """Every guidance text an agent reads, found by glob or read from the built servers and never listed by hand:
    the root skill and its references, any ``AGENTS.md`` inside the package, ``g6_help``'s text, each server's
    instructions and each registered tool's description (``shown``), and, in a source checkout (``repo``, or the
    folder two above the package when it holds ``plugins``), each plugin's skills and ``AGENTS.md``. Keys are paths
    inside the package, ``repo:<path>`` for a checkout's files, ``help:g6_help``, ``server:<id>`` and
    ``tool:<server>/<tool>``."""
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
    texts.update(shown())
    return texts


def identifiers(paths: typing.Iterable[str]) -> dict[str, frozenset[str]]:
    """For each detail file that is a component's own skill (a path inside the package, outside the root skill's
    folder): every name that exists in that component's code, read from its syntax trees (names, attributes,
    definitions, arguments, keywords, and string constants that are a name), and the name of every registered
    component. ``routing.check`` lets such a file put these in backticks beside the tools of its route's servers."""
    from g6.core.registry import get_registry
    from g6.skills import routing

    package, out, components = package_root(), {}, frozenset(get_registry().names())
    for path in paths:
        if path.startswith(routing.ROOT.rsplit("/", 1)[0] + "/") or "/" not in path:
            continue
        names: set[str] = set()
        for source in sorted((package / path.split("/", 1)[0]).rglob("*.py")):
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8", errors="replace"))):
                names |= {str(found) for found in (
                    getattr(node, "id", None), getattr(node, "attr", None), getattr(node, "name", None),
                    getattr(node, "arg", None), node.value if isinstance(node, ast.Constant) else None)
                    if isinstance(found, str) and found.isidentifier()}
        out[path] = frozenset(names) | components
    return out


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
            "sources": server_sources(package), "servers": dict(SERVERS), "identifiers": identifiers(sorted(wanted))}
