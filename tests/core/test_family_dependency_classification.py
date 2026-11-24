# SPDX-License-Identifier: Apache-2.0
"""Only absent optional modules may turn load/list defects into member recovery."""
from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest


PATHS = ("inventory", "inventory-family", "family", "tool", "tool-schema")
SHAPES = (
    ("installed-symbol", False), ("named-import-absent", True),
    ("missing-absent", True), ("missing-submodule", True), ("missing-present", False),
    ("missing-unnamed", False), ("required-absent", False), ("runtime", False),
    ("suppressed-context", False), ("implicit-context", True), ("explicit-cause", True),
    ("cause-over-context", True), ("visible-defective-context", False),
    ("defective-cause", False), ("system-exit-cause", True),
    ("group-optional", True), ("nested-group-optional", True), ("group-runtime", False),
    ("group-symbol", False), ("group-present", False), ("group-required", False),
    ("wrapped-group", True), ("group-cause-cannot-hide-member", False),
    ("shared-group-leaf", True), ("optional-cycle", True), ("defect-cycle", False),
    ("self-cycle", True), ("rotated-optional-cycle", True), ("group-cycle-only-member", False),
)


def _failure(shape):
    missing = ModuleNotFoundError("PRIVATE_IMPORT_DETAIL", name="g6_absent_extra")
    wrapper = RuntimeError("PRIVATE_WRAPPER_DETAIL")
    if shape == "installed-symbol":
        try:
            from json import g6_absent_api_symbol  # noqa: F401
        except ImportError as exc:
            assert exc.name == "json"
            return exc
        raise AssertionError("the API symbol must not exist")
    if shape == "named-import-absent":
        return ImportError("absent optional root", name="g6_absent_extra")
    if shape == "missing-absent":
        return missing
    if shape == "missing-submodule":
        return ModuleNotFoundError("absent", name="g6_absent_extra.state")
    if shape == "missing-present":
        return ModuleNotFoundError("synthetic failure in installed package", name="json.state")
    if shape == "missing-unnamed":
        return ModuleNotFoundError("no name")
    if shape == "required-absent":
        return ModuleNotFoundError("broken base install", name="redis.asyncio")
    if shape == "runtime":
        return wrapper
    if shape == "suppressed-context":
        try:
            raise missing
        except ModuleNotFoundError:
            try:
                raise wrapper from None
            except RuntimeError as exc:
                return exc
    if shape == "implicit-context":
        wrapper.__context__ = missing
        return wrapper
    if shape in {"explicit-cause", "cause-over-context", "system-exit-cause"}:
        if shape == "system-exit-cause":
            wrapper = SystemExit("wrapped optional import")
        wrapper.__cause__ = missing
        wrapper.__context__ = ValueError("handled earlier defect")
        assert wrapper.__suppress_context__ is True
        return wrapper
    if shape == "defective-cause":
        wrapper.__cause__ = ValueError("actual defect")
        wrapper.__context__ = missing
        return wrapper
    if shape == "visible-defective-context":
        wrapper.__cause__ = missing
        wrapper.__context__ = ValueError("visible defect")
        wrapper.__suppress_context__ = False
        return wrapper
    if shape == "group-optional":
        return ExceptionGroup("optional", [missing])
    if shape == "nested-group-optional":
        return ExceptionGroup("outer", [missing, ExceptionGroup("inner", [
            ModuleNotFoundError("absent", name="g6_absent_extra.state")])])
    other = {
        "group-runtime": ValueError("member defect"),
        "group-symbol": ImportError("API defect", name="json"),
        "group-present": ModuleNotFoundError("present", name="json"),
        "group-required": ModuleNotFoundError("required", name="redis"),
    }
    if shape in other:
        return ExceptionGroup("mixed", [missing, ExceptionGroup("inner", [other[shape]])])
    if shape == "wrapped-group":
        wrapper.__cause__ = ExceptionGroup("optional", [missing])
        return wrapper
    if shape == "group-cause-cannot-hide-member":
        group = ExceptionGroup("defective", [ValueError("member defect")])
        group.__cause__ = missing
        return group
    if shape == "shared-group-leaf":
        return ExceptionGroup("same import twice", [missing, missing])
    if shape in {"optional-cycle", "rotated-optional-cycle"}:
        wrapper.__cause__ = missing
        missing.__context__ = wrapper
        return missing if shape == "rotated-optional-cycle" else wrapper
    if shape == "defect-cycle":
        wrapper.__cause__ = ImportError("API wrapper", name="json")
        wrapper.__cause__.__context__ = wrapper
        return wrapper
    if shape == "self-cycle":
        missing.__cause__ = missing
        return missing
    if shape == "group-cycle-only-member":
        cyclic = RuntimeError("no missing-module evidence")
        cyclic.__cause__ = cyclic
        return ExceptionGroup("mixed", [missing, cyclic])
    raise AssertionError(shape)


@pytest.fixture
def dependency_environment(monkeypatch):
    from g6.mcp import dependencies

    requirements = {
        "g6": ['fastmcp; extra == "mcp"', 'mcp; extra == "mcp"',
               'numpy; extra == "abstraction"', 'g6_absent_extra; extra == "abstraction"',
               'redis; extra == "abstraction"'],
        "fastmcp": ["pydocket"], "mcp": [], "pydocket": ["redis"], "redis": [],
    }
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: requirements[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions", lambda: {
        "json": ["numpy"], "g6_absent_extra": ["g6_absent_extra"],
        **{name: [name] for name in ("fastmcp", "mcp", "pydocket", "redis")},
    })
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    monkeypatch.setitem(sys.modules, "g6_absent_extra", None)
    dependencies.optional_import_roots.cache_clear()
    yield dependencies
    dependencies.optional_import_roots.cache_clear()


def _server(monkeypatch, path, stage, failure):
    from g6.mcp import families, registrations

    module = ModuleType("family_classification_server")

    class Server:
        def get_tools(self):
            if stage == "list":
                raise failure
            return {"load_state": SimpleNamespace(description="Read state.", parameters={})}

    def build():
        if stage == "build":
            raise failure
        return Server()

    module.build = build
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": module.__name__ + ":build"})
    details = {"abstraction": {"load_state": {"guidance": "Read state."}}} if path == "tool-schema" else {}
    monkeypatch.setattr(families, "_DETAILS", details)
    return families


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("stage", ("build", "list"))
@pytest.mark.parametrize("shape,optional", SHAPES, ids=[row[0] for row in SHAPES])
def test_exception_class_on_every_disclosure_path(monkeypatch, dependency_environment, path, stage, shape, optional):
    failure = _failure(shape)
    families = _server(monkeypatch, path, stage, failure)

    def call():
        if path.startswith("inventory"):
            return families.inventory("logic" if path == "inventory-family" else None)
        return families.describe("logic" if path == "family" else "load_state")

    if not optional:
        with pytest.raises(type(failure)) as stopped:
            call()
        assert stopped.value is failure
        return
    reply = call()
    if path == "family":
        assert reply["available"] is True
        rows = reply["members"]
    elif path.startswith("inventory"):
        rows = reply
    else:
        rows = [reply]
    rows = [row for row in rows if row.get("id", "mcp:abstraction/load_state").startswith("mcp:abstraction/")]
    assert rows
    for row in rows:
        assert row["available"] is False
        assert row["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in row["install"]
    assert "PRIVATE_" not in json.dumps(reply)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("stage", ("build", "list"))
@pytest.mark.parametrize("chain", ("cause", "context", "system-exit"))
@pytest.mark.parametrize("error_type", (ModuleNotFoundError, ImportError))
def test_wrapped_absent_optional_dependency_is_unavailable(
    monkeypatch, dependency_environment, path, stage, chain, error_type,
):
    def failure():
        try:
            raise error_type("PRIVATE_IMPORT_DETAIL", name="g6_absent_extra.linalg")
        except ImportError as exc:
            if chain == "cause":
                raise ImportError("PRIVATE_WRAPPER_DETAIL", name="fastmcp") from exc
            if chain == "system-exit":
                raise SystemExit("PRIVATE_WRAPPER_DETAIL") from exc
            raise RuntimeError("PRIVATE_WRAPPER_DETAIL")

    try:
        failure()
    except (Exception, SystemExit) as exc:
        families = _server(monkeypatch, path, stage, exc)
    if path.startswith("inventory"):
        reply = families.inventory("logic" if path == "inventory-family" else None)
    else:
        reply = families.describe("logic" if path == "family" else "load_state")
    if path == "family":
        assert reply["available"] is True
        rows = reply["members"]
    elif path.startswith("inventory"):
        rows = reply
    else:
        rows = [reply]
    rows = [row for row in rows if row.get("id", "mcp:abstraction/load_state").startswith("mcp:abstraction/")]
    assert rows
    for row in rows:
        assert row["available"] is False
        assert row["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in row["install"]
    assert "PRIVATE_" not in json.dumps(reply)


def test_suppressed_context_is_not_a_startup_dependency():
    from g6.mcp.dependencies import missing_module

    assert missing_module(_failure("suppressed-context")) is None


@pytest.mark.parametrize("error_type", (ValueError, ImportError))
def test_uncertain_module_lookup_is_a_defect(monkeypatch, dependency_environment, error_type):
    from importlib import util

    def uncertain(name):
        raise error_type("cannot establish importability")

    monkeypatch.setattr(util, "find_spec", uncertain)
    assert dependency_environment.optional_dependency_missing("abstraction", _failure("missing-absent")) is False
