# SPDX-License-Identifier: Apache-2.0
"""Discovery must distinguish missing extras from server defects on every load path."""
from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest


PATHS = ("inventory", "inventory-family", "family", "tool", "tool-schema")


def _call(families, path):
    if path == "inventory":
        return families.inventory()
    if path == "inventory-family":
        return families.inventory("logic")
    return families.describe("logic" if path == "family" else "load_state")


def _server(monkeypatch, path, stage, fail):
    from g6.mcp import families, registrations

    module = ModuleType("family_dependency_server")

    class Server:
        def get_tools(self):
            if stage == "list":
                fail()
            return {"load_state": SimpleNamespace(description="Read state.", parameters={})}

    def build():
        if stage == "build":
            fail()
        return Server()

    module.build = build
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": module.__name__ + ":build"})
    details = {"abstraction": {"load_state": {"guidance": "Read state."}}} if path == "tool-schema" else {}
    monkeypatch.setattr(families, "_DETAILS", details)
    return families


class TestOptionalServerFailurePaths:
    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("stage", ("build", "list"))
    @pytest.mark.parametrize("defect", (
        "runtime", "system-exit", "unnamed-import", "internal-module", "base-module", "undeclared-module",
    ))
    def test_non_dependency_defect_propagates(self, monkeypatch, path, stage, defect):
        failures = {
            "runtime": RuntimeError("constructor or listing regression"),
            "system-exit": SystemExit("server stopped"),
            "unnamed-import": ImportError("cannot import a symbol"),
            "internal-module": ModuleNotFoundError("internal regression", name="g6.abstraction.typo"),
            "base-module": ModuleNotFoundError("base dependency missing", name="redis.asyncio"),
            "undeclared-module": ModuleNotFoundError("undeclared dependency missing", name="rq"),
        }
        failure = failures[defect]

        def fail():
            raise failure

        families = _server(monkeypatch, path, stage, fail)
        with pytest.raises(type(failure)) as stopped:
            _call(families, path)
        assert stopped.value is failure

    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("stage", ("build", "list"))
    @pytest.mark.parametrize("chain", ("cause", "context", "system-exit"))
    @pytest.mark.parametrize("error_type", (ModuleNotFoundError, ImportError))
    def test_wrapped_declared_dependency_is_unavailable(self, monkeypatch, path, stage, chain, error_type):
        def fail():
            try:
                raise error_type("PRIVATE_IMPORT_DETAIL", name="numpy.linalg")
            except ImportError as exc:
                if chain == "cause":
                    raise ImportError("wrapper", name="fastmcp") from exc
                if chain == "system-exit":
                    raise SystemExit("wrapper") from exc
                raise RuntimeError("wrapper")

        families = _server(monkeypatch, path, stage, fail)
        reply = _call(families, path)
        if path == "family":
            assert reply["available"] is True
            rows = [row for row in reply["members"] if row["id"].startswith("mcp:abstraction/")]
        elif path.startswith("inventory"):
            rows = [row for row in reply if row["id"].startswith("mcp:abstraction/")]
        else:
            rows = [reply]
        assert rows
        for row in rows:
            assert row["available"] is False
            assert row["reason"] == "server_unavailable"
            assert "g6[abstraction,mcp]" in row["install"]
        assert "PRIVATE_IMPORT_DETAIL" not in json.dumps(reply)


@pytest.fixture
def dependency_metadata(monkeypatch):
    from g6.mcp import dependencies

    dependencies.optional_import_roots.cache_clear()
    yield dependencies
    dependencies.optional_import_roots.cache_clear()


def test_optional_roots_use_declared_extras_and_required_transitive_closure(monkeypatch, dependency_metadata):
    dependencies = dependency_metadata

    requirements = {
        "g6": ["base", 'fastmcp; extra == "mcp"', 'mcp; extra == "mcp"',
               'redis; extra == "rq"', 'rq; extra == "rq"', 'Pillow; extra == "image"',
               'numpy; extra == "array"', 'g6[image,array]; extra == "bundle"'],
        "base": [], "fastmcp": ["pydocket[queue]"], "mcp": [],
        "pydocket": ['redis; extra == "queue"', 'ignored; python_version < "2"'],
        "redis": ["base"],
    }
    imports = {name: [name] for name in ("base", "fastmcp", "mcp", "pydocket", "redis", "rq", "numpy")}
    imports["PIL"] = ["Pillow"]
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: requirements[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions", lambda: imports)
    assert dependencies.optional_import_roots("rq") == frozenset({"rq"})
    assert dependencies.optional_import_roots("bundle") == frozenset({"PIL", "numpy"})
    assert dependencies.optional_import_roots("mcp") == frozenset()


def test_missing_required_metadata_does_not_claim_optional_unavailability(monkeypatch, dependency_metadata):
    dependencies = dependency_metadata

    def absent(name):
        raise dependencies.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(dependencies.metadata, "requires", absent)
    with pytest.raises(dependencies.metadata.PackageNotFoundError):
        dependencies.optional_import_roots("abstraction")
