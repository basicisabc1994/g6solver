# SPDX-License-Identifier: Apache-2.0
"""Refresh live ownership while retaining established file and namespace aliases."""
from importlib import machinery
import sys
from types import ModuleType, SimpleNamespace

import pytest


REQUIRED = "rfu_repair3_required"
OPTIONAL = "rfu_repair3_optional"


def _file_spec(name, path):
    spec = machinery.ModuleSpec(name, loader=None, origin=path)
    spec.has_location = True
    return spec


def _namespace_spec(name, locations):
    spec = machinery.ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = locations
    return spec


@pytest.fixture
def ownership(monkeypatch):
    from g6.mcp import dependencies

    needs = {"g6": ["repair3-base", 'repair3-extra; extra == "abstraction"'],
             "repair3-base": [], "repair3-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["repair3-base"], OPTIONAL: ["repair3-extra"]})
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    specs = {}
    monkeypatch.setattr(dependencies.util, "find_spec", lambda name: specs.get(name))
    monkeypatch.setattr(dependencies.os.path, "realpath", lambda path: path)
    monkeypatch.setattr(dependencies.os, "stat", lambda path: SimpleNamespace(st_dev=0, st_ino=0))
    monkeypatch.delitem(sys.modules, REQUIRED, raising=False)
    monkeypatch.delitem(sys.modules, OPTIONAL, raising=False)
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, specs
    dependencies.optional_import_roots.cache_clear()


@pytest.mark.parametrize("probe", ("find-spec", "file-path", "namespace-path", "stat"))
def test_healed_identity_is_retried_without_cache_clear(ownership, monkeypatch, probe):
    dependencies, specs = ownership
    if probe == "namespace-path":
        specs[OPTIONAL] = _namespace_spec(OPTIONAL, ["optional-location"])
    elif probe != "find-spec":
        specs[OPTIONAL] = _file_spec(OPTIONAL, "optional-file")
    target, attribute = ((dependencies.util, "find_spec") if probe == "find-spec" else
                         (dependencies.os, "stat") if probe == "stat" else
                         (dependencies.os.path, "realpath"))
    original = getattr(target, attribute)
    broken = True

    def lookup(value):
        if broken and value in {OPTIONAL, "optional-file", "optional-location"}:
            raise OSError("transient identity failure")
        return original(value)

    monkeypatch.setattr(target, attribute, lookup)
    assert dependencies.optional_import_roots("abstraction") == frozenset()
    broken = False
    assert dependencies.optional_import_roots("abstraction") == {OPTIONAL}


def test_loaded_alias_is_live_in_both_directions(ownership, monkeypatch):
    dependencies, _ = ownership
    assert dependencies.optional_import_roots("abstraction") == {OPTIONAL}
    shared = ModuleType("rfu_repair3_shared")
    monkeypatch.setitem(sys.modules, REQUIRED, shared)
    monkeypatch.setitem(sys.modules, OPTIONAL, shared)
    assert dependencies.optional_import_roots("abstraction") == frozenset()
    monkeypatch.delitem(sys.modules, OPTIONAL)
    assert dependencies.optional_import_roots("abstraction") == {OPTIONAL}


@pytest.mark.parametrize("source", ("loaded-file", "loaded-cache", "spec-cache", "loaded-path"))
@pytest.mark.parametrize("failed_probe", ("realpath", "stat"))
def test_failed_origin_keeps_independent_path_sources(ownership, monkeypatch, source, failed_probe):
    dependencies, specs = ownership
    loaded = ModuleType(REQUIRED)
    loaded.__spec__ = _file_spec(REQUIRED, "broken-origin")
    if source == "loaded-file":
        loaded.__file__ = "shared-file"
    elif source == "loaded-cache":
        loaded.__cached__ = "shared-file"
    elif source == "spec-cache":
        loaded.__spec__.cached = "shared-file"
    else:
        loaded.__path__ = ["shared-location"]
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    specs[OPTIONAL] = (_namespace_spec(OPTIONAL, ["shared-location", "extra-location"])
                       if source == "loaded-path" else _file_spec(OPTIONAL, "optional-file"))

    def resolve(path):
        if failed_probe == "realpath" and path == "broken-origin":
            raise OSError("origin unavailable")
        return path

    def stat(path):
        if failed_probe == "stat" and path == "broken-origin":
            raise OSError("origin unavailable")
        return SimpleNamespace(st_dev=7, st_ino=41 if path in {"shared-file", "optional-file"} else 0)

    monkeypatch.setattr(dependencies.os.path, "realpath", resolve)
    monkeypatch.setattr(dependencies.os, "stat", stat)
    assert dependencies.optional_import_roots("abstraction") == frozenset()
    specs.clear()
    assert dependencies.optional_import_roots("abstraction") == frozenset()


@pytest.mark.parametrize("locations", (["shared", "extra"], ["extra", "shared"]))
def test_namespace_locations_overlap_independently_and_survive_removal(ownership, locations):
    dependencies, specs = ownership
    specs[REQUIRED] = _namespace_spec(REQUIRED, ["shared"])
    specs[OPTIONAL] = _namespace_spec(OPTIONAL, locations)
    assert dependencies.optional_import_roots("abstraction") == frozenset()
    specs.clear()
    assert dependencies.optional_import_roots("abstraction") == frozenset()


def test_failed_namespace_location_cannot_hide_later_shared_location(ownership, monkeypatch):
    dependencies, specs = ownership
    specs[REQUIRED] = _namespace_spec(REQUIRED, ["broken-location", "shared"])
    specs[OPTIONAL] = _namespace_spec(OPTIONAL, ["extra", "shared"])

    def resolve(path):
        if path == "broken-location":
            raise OSError("one location unavailable")
        return path

    monkeypatch.setattr(dependencies.os.path, "realpath", resolve)
    assert dependencies.optional_import_roots("abstraction") == frozenset()


def test_disjoint_namespaces_remain_optional(ownership):
    dependencies, specs = ownership
    specs[REQUIRED] = _namespace_spec(REQUIRED, ["required-location"])
    specs[OPTIONAL] = _namespace_spec(OPTIONAL, ["optional-location", "extra-location"])
    assert dependencies.optional_import_roots("abstraction") == {OPTIONAL}


def test_file_ownership_history_is_cleared_with_declarations(ownership):
    dependencies, specs = ownership
    specs[REQUIRED] = _file_spec(REQUIRED, "shared-file")
    specs[OPTIONAL] = _file_spec(OPTIONAL, "shared-file")
    assert dependencies.optional_import_roots("abstraction") == frozenset()
    specs.clear()
    assert dependencies.optional_import_roots("abstraction") == frozenset()
    dependencies.optional_import_roots.cache_clear()
    assert dependencies.optional_import_roots("abstraction") == {OPTIONAL}
