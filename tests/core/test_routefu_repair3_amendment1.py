# SPDX-License-Identifier: Apache-2.0
"""Startup rechecks live package parents before accepting a cached SDK import."""
from importlib import import_module
import sys
from types import ModuleType

import pytest


class _ReachedConstruction(Exception):
    pass


@pytest.fixture
def startup(monkeypatch):
    from g6 import mcp
    from g6.mcp import dependencies

    needs = {"g6": ["repair3-base", 'repair3-extra; extra == "mcp"'],
             "repair3-base": [], "repair3-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {"rfu3am_required": ["repair3-base"], "mcp": ["repair3-extra"]})
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.delitem(sys.modules, "rfu3am_required", raising=False)
    dependencies.optional_import_roots.cache_clear()
    assert dependencies.optional_import_roots("mcp", required_extras="") == {"mcp"}

    cached = ModuleType("mcp.server")
    monkeypatch.setitem(sys.modules, "mcp.server", cached)
    monkeypatch.setattr(mcp, "_quiet", lambda: import_module("mcp.server"))
    sdk = ModuleType("fastmcp")
    sdk.Context = object

    def construct(*args, **kwargs):
        raise _ReachedConstruction

    sdk.FastMCP = construct
    monkeypatch.setitem(sys.modules, "fastmcp", sdk)
    yield mcp, dependencies, cached
    dependencies.optional_import_roots.cache_clear()


def test_startup_rejects_required_alias_even_with_cached_child(startup, monkeypatch):
    mcp, dependencies, cached = startup
    required = ModuleType("rfu3am_required")
    monkeypatch.setitem(sys.modules, "rfu3am_required", required)
    monkeypatch.setitem(sys.modules, "mcp", required)

    stopped = None
    try:
        mcp.build_server()
    except SystemExit as exc:
        stopped = exc
    except _ReachedConstruction:
        pass
    assert stopped is not None, "startup reused a cached child of a non-package required alias"
    assert isinstance(stopped.__cause__, ModuleNotFoundError)
    assert stopped.__cause__.name == "mcp.server"
    assert "MCP extra" not in str(stopped)
    assert dependencies.optional_import_roots("mcp", required_extras="") == frozenset()
    assert sys.modules["mcp.server"] is cached
    assert sys.modules["mcp"] is required


@pytest.mark.parametrize("parent_state", ("missing", "none"))
def test_startup_rejects_cached_child_without_live_parent(startup, monkeypatch, parent_state):
    mcp, _, _ = startup
    if parent_state == "missing":
        monkeypatch.delitem(sys.modules, "mcp")

    stopped = None
    try:
        mcp.build_server()
    except SystemExit as exc:
        stopped = exc
    except _ReachedConstruction:
        pass
    assert stopped is not None, "startup reused a cached child without its package parent"
    assert stopped.__cause__.name == "mcp.server"


def test_startup_rechecks_repaired_package_without_cache_clear(startup, monkeypatch):
    mcp, _, cached = startup
    broken = ModuleType("rfu3am_required")
    monkeypatch.setitem(sys.modules, "rfu3am_required", broken)
    monkeypatch.setitem(sys.modules, "mcp", broken)
    with pytest.raises(SystemExit):
        mcp.build_server()

    repaired = ModuleType("mcp")
    repaired.__path__ = []
    monkeypatch.setitem(sys.modules, "mcp", repaired)
    with pytest.raises(_ReachedConstruction):
        mcp.build_server()
    assert sys.modules["mcp.server"] is cached


def test_startup_keeps_original_import_failure(startup, monkeypatch):
    mcp, _, _ = startup
    failure = ModuleNotFoundError("original dependency failure", name="rfu3am_required.api")

    def fail():
        raise failure

    monkeypatch.setattr(mcp, "_quiet", fail)
    with pytest.raises(SystemExit) as stopped:
        mcp.build_server()
    assert stopped.value.__cause__ is failure


def test_cached_import_checks_each_parent_again(startup, monkeypatch):
    mcp, _, _ = startup
    package = ModuleType("rfu3am_package")
    package.__path__ = []
    nested = ModuleType("rfu3am_package.nested")
    nested.__path__ = []
    child = ModuleType("rfu3am_package.nested.child")
    monkeypatch.setitem(sys.modules, "rfu3am_package", package)
    monkeypatch.setitem(sys.modules, "rfu3am_package.nested", nested)
    monkeypatch.setitem(sys.modules, "rfu3am_package.nested.child", child)
    mcp._check_cached_import_parents("rfu3am_package.nested.child")
    monkeypatch.setitem(sys.modules, "rfu3am_package", ModuleType("replacement"))
    with pytest.raises(ModuleNotFoundError) as stopped:
        mcp._check_cached_import_parents("rfu3am_package.nested.child")
    assert stopped.value.name == "rfu3am_package.nested.child"
