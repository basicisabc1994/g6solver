# SPDX-License-Identifier: Apache-2.0
"""The recorder resolves names to objects (0.3 design 14.1): no inventory here decides what a name is from how it is
spelled. Each test derives its cases from the code: the server classes from the installed distributions' own
namespaces, the blocks from the registry, the dialogs from the modules that import the one transport, and the texts
a server shows from the built servers."""
from __future__ import annotations

import ast
import enum
import importlib
import importlib.util
import sys
import types
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

import pytest
from pydantic import BaseModel

from g6.core.registry import ComponentRef, get_registry
from g6.core.result import Result
from g6.mcp import consent, registrations
from g6.skills import routing


def _package(tmp_path: Path, files: dict[str, str]) -> Path:
    package = tmp_path / "g6"
    for rel, source in files.items():
        (package / rel).parent.mkdir(parents=True, exist_ok=True)
        (package / rel).write_text(source, encoding="utf-8")
    return package


def _holders() -> list[tuple[str, str]]:
    """Every (module, name) of the server distributions, as loaded, under which a server class can be imported:
    read from the modules' own namespaces, whatever they are called."""
    classes = registrations._server_classes()
    tops = {cls.__module__.split(".")[0] for cls in classes}
    found = []
    for name, module in sorted(sys.modules.items()):
        if module is None or name.split(".")[0] not in tops:
            continue
        found += [(name, key) for key, value in sorted(vars(module).items())
                  if any(value is cls for cls in classes) and key.isidentifier()]
    return found


def test_a_server_built_from_any_name_a_distribution_gives_its_class_is_found(tmp_path: Path) -> None:
    holders = _holders()
    assert len(holders) >= 6 and len({module for module, _ in holders}) >= 4, holders
    missed = []
    for n, (module, name) in enumerate(holders):
        direct = _package(tmp_path / f"d{n}", {"extra/__init__.py": f"from {module} import {name} as K\ns = K('s')\n"})
        through = _package(tmp_path / f"m{n}", {"extra/__init__.py": f"import {module} as m\ns = m.{name}('s')\n"})
        if registrations.server_sources(direct) != {"g6.extra"} or registrations.server_sources(through) != {"g6.extra"}:
            missed.append((module, name))
    assert not missed, f"a server class imported from these places is not seen: {missed}"


def test_local_names_subclasses_and_the_packages_own_modules_are_followed(tmp_path: Path) -> None:
    module, name = _holders()[0]
    cases = {
        "a local name": ({"extra/__init__.py": f"from {module} import {name}\nMaker = {name}\nAgain = Maker\n"
                                                "one = Again('one')\n"}, {"g6.extra"}),
        "two constructions, one through a local name": (
            {"extra/__init__.py": f"from {module} import {name}\nMaker = {name}\none = {name}('one')\n"
                                  "two = Maker('two')\n"}, {"g6.extra", "g6.extra#2"}),
        "a subclass of a subclass": ({"extra/__init__.py": f"import {module} as m\n\n\nclass A(m.{name}):\n    pass\n"
                                                             "\n\nclass B(A):\n    pass\n\n\ns = B('s')\n"},
                                      {"g6.extra"}),
        "handed on by a module of the package": (
            {"helpers.py": f"from {module} import {name} as Maker\n",
             "extra/__init__.py": "from g6.helpers import Maker\ns = Maker('s')\n",
             "other/__init__.py": "from .. import helpers\ns = helpers.Maker('s')\n"},
            {"g6.helpers", "g6.extra", "g6.other"}),
        "importlib": ({"extra/__init__.py": f"import importlib\ns = importlib.import_module('{module}').{name}('s')\n"},
                      {"g6.extra"}),
    }
    for said, (files, expected) in cases.items():
        assert registrations.server_sources(_package(tmp_path / said.replace(" ", "_").replace(",", ""), files)) \
            == expected, said


def test_a_module_that_takes_something_else_from_a_distribution_is_not_a_server(tmp_path: Path) -> None:
    classes = registrations._server_classes()
    module = importlib.import_module(classes[0].__module__)
    other = next(key for key, value in sorted(vars(module).items())
                 if isinstance(value, type) and value.__module__ == module.__name__
                 and not issubclass(value, classes) and not key.startswith("_"))
    package = _package(tmp_path, {"extra/__init__.py": f"from {module.__name__} import {other}\nx = {other}\n",
                                  "plain/__init__.py": "import json\nx = json.dumps({})\n"})
    assert registrations.server_sources(package) == set()


def test_the_shipped_package_has_the_known_servers_and_no_other() -> None:
    known = {target.split(":", 1)[0] for target in registrations.SERVERS.values()}
    assert registrations.server_sources(registrations.package_root()) == known


# --- the ops a block's typed input accepts ----------------------------------------------------------------------------

class _Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


@pytest.mark.parametrize("annotation, accepted", [
    (Literal["a", "b"], {"a", "b"}),
    (Optional[Literal["a", "b"]], {"a", "b"}),
    (Literal["a", "b"] | None, {"a", "b"}),
    (Annotated[Literal["a", "b"], "said"], {"a", "b"}),
    (Annotated[Literal["a"] | None, "said"], {"a"}),
    (Union[Literal["a"], Literal["b", "c"]], {"a", "b", "c"}),
    (_Colour, {"red", "blue"}),
    (str, None), (Optional[str], None), (Union[Literal["a"], str], None), (Literal["a", 1], None), (Any, None),
])
def test_the_accepted_ops_are_read_through_every_wrapper(annotation: Any, accepted: set[str] | None) -> None:
    assert registrations._accepted(annotation) == accepted


def test_every_block_declares_its_ops_or_is_a_stated_exception_that_refuses_an_unlisted_op() -> None:
    registry = get_registry()
    untyped = {name for name in registry.names() if registry.get_class(name) is not None
               and registrations._typed_ops(registry.get_class(name)) is None}
    assert untyped == set(registrations.OPS_READ_FROM_THE_REPLY), (
        "a block whose typed input does not say its ops, and the stated exceptions, are the same set")
    for name, why in registrations.OPS_READ_FROM_THE_REPLY.items():
        assert len(why) >= 20, name
        assert registry.invoke(name, registrations._NO_SUCH_OP).__class__.__name__ != "Success", name
    registrations.component_ops()                       # and the recorder itself accepts the shipped tree


class _Loose(BaseModel):
    op: str = "ops"


class _LooseBlock:
    def infer(self, data: _Loose) -> Result:
        return Result.ok({"ops": ["run"]} if data.op == "ops" else {"ran": data.op})


def test_a_new_block_with_a_plain_string_op_stops_the_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("g6_test_loose_block")
    module.__g6_component__, module.__g6_block__, module.Block = True, "Block", _LooseBlock
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(get_registry().refs, "g6_test_loose", ComponentRef("g6_test_loose", module.__name__, "builtin"))
    with pytest.raises(registrations.RegistrationError, match="g6_test_loose.*does not say which ops"):
        registrations.component_ops()
    # stating it as an exception is not enough either: this block runs an op its reply does not list
    monkeypatch.setitem(registrations.OPS_READ_FROM_THE_REPLY, "g6_test_loose", "a test block that accepts any op at all")
    with pytest.raises(registrations.RegistrationError, match="does not refuse"):
        registrations.component_ops()


# --- the dialogs' fields ----------------------------------------------------------------------------------------------

def _senders() -> set[str]:
    """The modules of the package that bind the one transport of a form (``g6.mcp.consent.elicit``), found by what
    each imported name is: the object, not its spelling. The module that defines it is one."""
    package = registrations.package_root()
    found = {consent.__name__}
    for path in sorted(package.rglob("*.py")):
        parts = path.relative_to(package).with_suffix("").parts
        name = ".".join(("g6", *(parts[:-1] if parts[-1] == "__init__" else parts)))
        home = name if path.name == "__init__.py" else name.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                source = importlib.util.resolve_name("." * node.level + (node.module or ""), home) if node.level \
                    else node.module
                if source == consent.__name__ and any(getattr(consent, alias.name, None) is consent.elicit
                                                      for alias in node.names):
                    found.add(name)
                if any(f"{source}.{alias.name}" == consent.__name__ for alias in node.names) and any(
                        isinstance(n, ast.Attribute) and getattr(consent, n.attr, None) is consent.elicit
                        for n in ast.walk(tree)):
                    found.add(name)
            elif isinstance(node, ast.Import) and any(alias.name == consent.__name__ for alias in node.names):
                found.add(name)
    return found


def test_every_module_that_sends_a_form_has_its_schema_read() -> None:
    assert _senders() == set(registrations.DIALOG_SCHEMAS)


def test_the_fields_are_what_the_forms_return_however_they_are_built(monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.harness import behaviour_question

    real = behaviour_question.form
    before = registrations.dialog_fields()

    def more(question: Any, candidate: Any, rule: Any = None, *, scenario: Any = None) -> tuple[str, dict]:
        message, schema = real(question, candidate, rule, scenario=scenario)
        asked = {**schema["properties"], "witness": {"type": "object", "properties": {"badge": {"type": "string"}}}}
        return message, {"type": "object", "allOf": [{"properties": asked}]}

    monkeypatch.setattr(behaviour_question, "form", more)
    assert registrations.dialog_fields() == before | {"witness", "badge"}
    monkeypatch.setattr(behaviour_question, "form", lambda *a, **k: ("?", {"type": "object"}))
    with pytest.raises(registrations.RegistrationError):
        registrations.dialog_fields()

    def broken(*a: Any, **k: Any) -> tuple[str, dict]:
        raise ValueError("the form changed its arguments")

    monkeypatch.setattr(behaviour_question, "form", broken)
    with pytest.raises(registrations.RegistrationError, match="could not be built"):
        registrations.dialog_fields()


# --- what the servers show --------------------------------------------------------------------------------------------

def test_everything_a_built_server_shows_is_a_guidance_text() -> None:
    texts = registrations.guidance()
    seen = 0
    for server_id, server in registrations._built():
        if isinstance(getattr(server, "instructions", None), str) and server.instructions.strip():
            assert texts[f"server:{server_id}"] == server.instructions
            seen += 1
        for name, tool in registrations._listed(server):
            if (getattr(tool, "description", None) or "").strip():
                assert texts[f"tool:{server_id}/{name}"] == tool.description, name
                seen += 1
    assert seen == len(registrations.shown()) >= 50


def test_a_description_that_names_a_tool_that_is_gone_is_stale_and_an_exempt_tools_own_is_not() -> None:
    given = registrations.inputs()
    manifest = routing.load()
    assert routing.check(manifest, **given) == []
    surface = next(key for key in sorted(given["texts"]) if key.startswith("tool:g6/"))
    texts = {**given["texts"], surface: given["texts"][surface] + " Then call g6_a_tool_that_is_gone."}
    found = routing.check(manifest, **{**given, "texts": texts})
    assert [(p.code, p.key) for p in found] == [("STALE", surface)]
    # an exempt tool's own description is shown by the server whatever the manifest says: it is not guidance naming
    # an exempt member; any other text that names the tool is
    key = next(k for k in sorted(manifest.entries) if k.startswith("mcp:") and f"tool:{k[4:]}" in given["texts"])
    own, tool = f"tool:{key[4:]}", key.split("/", 1)[1]
    other = next(k for k in sorted(given["texts"]) if k.startswith("tool:") and k != own
                 and tool not in given["texts"][k])
    entries = {**manifest.entries, key: routing.Entry(key, False, "", "called by the server itself, never by an agent",
                                                      manifest.entries[key].since)}
    exempted = routing.Manifest(manifest.format, manifest.release, manifest.routes, entries)
    texts = {**given["texts"], own: given["texts"][own] + f" `{tool}` is this tool."}
    before = {(p.code, p.key) for p in routing.check(exempted, **{**given, "texts": texts})}
    assert ("EXEMPT_NAMED", own) not in before and ("EXEMPT_NAMED", other) not in before
    texts[other] = given["texts"][other] + f" See `{tool}`."
    after = {(p.code, p.key) for p in routing.check(exempted, **{**given, "texts": texts})}
    assert after - before == {("EXEMPT_NAMED", other)}
