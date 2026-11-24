# SPDX-License-Identifier: Apache-2.0
"""Lookup uncertainty preserves the original failure and required alias ownership."""
from importlib import machinery
import os
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def installation(monkeypatch):
    from g6.mcp import dependencies
    # Load the parser before faulting filesystem and metadata boundaries.
    from packaging.requirements import Requirement

    Requirement("repair-base")
    needs = {"g6": ["repair-base", 'repair-extra; extra == "abstraction"',
                    'repair-mcp; extra == "mcp"'],
             "repair-base": [], "repair-extra": [], "repair-mcp": []}
    declarations = {"rfuam_required": ["repair-base"], "rfuam_optional": ["repair-extra"],
                    "mcp": ["repair-mcp"]}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions", lambda: declarations)
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    monkeypatch.setattr(dependencies.util, "find_spec", lambda name: None)
    monkeypatch.setitem(sys.modules, "mcp", None)
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, declarations
    dependencies.optional_import_roots.cache_clear()


def _fault(monkeypatch, dependencies, lookup, error_type):
    def fail(*args, **kwargs):
        raise error_type("cannot establish dependency identity")

    if lookup in {"requires", "packages_distributions", "distributions"}:
        monkeypatch.setattr(dependencies.metadata, lookup, fail)
    elif lookup in {"distribution-name", "top-level", "record"}:
        class Distribution:
            @property
            def metadata(self):
                if lookup == "distribution-name":
                    fail()
                return {"Name": "repair-extra"}

            def read_text(self, name):
                if name == ("top_level.txt" if lookup == "top-level" else "RECORD"):
                    fail()
                return None

        monkeypatch.setattr(dependencies.metadata, "distributions", lambda: [Distribution()])
    elif lookup == "find-spec":
        monkeypatch.setattr(dependencies.util, "find_spec", fail)
    else:
        spec = machinery.ModuleSpec("rfuam_optional", loader=None,
                                    origin="rfuam_optional.py", is_package=True)
        spec.has_location = lookup != "namespace-path"
        if lookup == "namespace-path":
            spec.submodule_search_locations = ["rfuam_namespace"]
        monkeypatch.setattr(dependencies.util, "find_spec", lambda name: spec)
        target = dependencies.os if lookup == "stat" else dependencies.os.path
        attribute = "stat" if lookup == "stat" else "realpath"
        original = getattr(target, attribute)
        fault_path = "rfuam_namespace" if lookup == "namespace-path" else "rfuam_optional.py"

        def identity(path, *args, **kwargs):
            if path == fault_path:
                fail()
            return original(path, *args, **kwargs)

        monkeypatch.setattr(target, attribute, identity)


LOOKUPS = ("find-spec", "file-path", "namespace-path", "stat")


@pytest.mark.parametrize("lookup", LOOKUPS)
@pytest.mark.parametrize("error_type", (ValueError, ImportError, OSError, RuntimeError))
def test_lookup_failure_is_not_missing_optional(monkeypatch, installation, lookup, error_type):
    dependencies, _ = installation
    failure = ModuleNotFoundError("original server failure", name="rfuam_optional")
    _fault(monkeypatch, dependencies, lookup, error_type)
    assert dependencies.optional_dependency_missing("abstraction", failure) is False


@pytest.mark.parametrize("lookup", LOOKUPS)
@pytest.mark.parametrize("entry", ("family", "startup"))
def test_lookup_failure_keeps_original_exception(monkeypatch, installation, lookup, entry):
    from g6 import mcp
    from g6.mcp import families, registrations

    dependencies, _ = installation
    missing = "mcp.server" if entry == "startup" else "rfuam_optional"
    failure = ModuleNotFoundError("original server failure", name=missing)

    def build():
        raise failure

    server = ModuleType("rfuam_server")
    server.build = build
    monkeypatch.setitem(sys.modules, server.__name__, server)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": server.__name__ + ":build"})
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(mcp, "_quiet", build)
    _fault(monkeypatch, dependencies, lookup, ValueError)
    if entry == "family":
        with pytest.raises(ModuleNotFoundError) as stopped:
            families.describe("load_state")
        assert stopped.value is failure
    else:
        with pytest.raises(SystemExit) as stopped:
            mcp.build_server()
        assert stopped.value.__cause__ is failure
        assert "MCP extra" not in str(stopped.value)
        assert missing in str(stopped.value)


@pytest.mark.parametrize("uncertain", ("rfuam_required", "rfuam_optional"))
@pytest.mark.parametrize("lookup", ("file-path", "stat"))
def test_uncertain_root_keeps_known_aliases_required(monkeypatch, installation, uncertain, lookup):
    dependencies, declarations = installation
    declarations.update({"rfuam_alias": ["repair-extra"], "rfuam_healthy": ["repair-extra"]})
    paths = {root: os.path.join(os.getcwd(), root + ".py")
             for root in ("rfuam_required", "rfuam_optional", "rfuam_alias")}

    def find_spec(root):
        if root not in paths:
            return None
        canonical = "rfuam_shared" if root in {uncertain, "rfuam_alias"} else root
        spec = machinery.ModuleSpec(canonical, loader=None, origin=paths[root])
        spec.has_location = True
        return spec

    def resolve(path):
        if lookup == "file-path" and path == paths[uncertain]:
            raise OSError("identity unavailable")
        return path

    def stat(path):
        if lookup == "stat" and path == paths[uncertain]:
            raise OSError("identity unavailable")
        return SimpleNamespace(st_ino=0, st_dev=0)

    monkeypatch.setattr(dependencies.util, "find_spec", find_spec)
    monkeypatch.setattr(dependencies.os.path, "realpath", resolve)
    monkeypatch.setattr(dependencies.os, "stat", stat)
    roots = dependencies.optional_import_roots("abstraction")
    assert "rfuam_alias" not in roots
    assert uncertain not in roots
    assert "rfuam_healthy" in roots


def test_failed_path_lookup_still_keeps_required_inode_alias(monkeypatch, installation):
    dependencies, _ = installation

    def find_spec(root):
        if root not in {"rfuam_required", "rfuam_optional"}:
            return None
        spec = machinery.ModuleSpec(root, loader=None, origin=root + ".py")
        spec.has_location = True
        return spec

    def resolve(path):
        if path == "rfuam_required.py":
            raise OSError("required path identity unavailable")
        return path

    monkeypatch.setattr(dependencies.util, "find_spec", find_spec)
    monkeypatch.setattr(dependencies.os.path, "realpath", resolve)
    monkeypatch.setattr(dependencies.os, "stat", lambda path: SimpleNamespace(st_dev=7, st_ino=41))
    assert dependencies.optional_import_roots("abstraction") == frozenset()


@pytest.mark.parametrize("entry", ("family", "startup"))
def test_established_absent_optional_still_recovers(installation, entry):
    from g6.mcp import _import_error_message

    dependencies, _ = installation
    missing = "mcp.server" if entry == "startup" else "rfuam_optional"
    failure = ModuleNotFoundError("optional is absent", name=missing)
    if entry == "family":
        assert dependencies.optional_dependency_missing("abstraction", failure) is True
    else:
        assert "MCP extra" in _import_error_message(failure)


@pytest.mark.parametrize("startup", (False, True))
@pytest.mark.parametrize("error_type", (OSError, RuntimeError))
def test_absence_lookup_uncertainty_is_a_defect(monkeypatch, installation, startup, error_type):
    dependencies, _ = installation
    failure = ModuleNotFoundError("original server failure", name="rfuam_optional")
    _fault(monkeypatch, dependencies, "find-spec", error_type)
    assert dependencies.absent_module_roots(failure, mcp_startup=startup) == frozenset()
