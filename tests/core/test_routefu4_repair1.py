# SPDX-License-Identifier: Apache-2.0
"""Retirement evidence survives races, alternate spellings and healthy inventory."""
from importlib import import_module, invalidate_caches, util
import os
import sys
from types import ModuleType

import pytest


REQUIRED = "rfu4repair_required"
OPTIONAL = "rfu4repair_optional"


@pytest.fixture
def ownership(monkeypatch):
    from g6.mcp import dependencies

    needs = {"g6": ['rfu4repair-base; extra == "mcp"',
                    'rfu4repair-extra; extra == "abstraction"'],
             "rfu4repair-base": [], "rfu4repair-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu4repair-base"], OPTIONAL: ["rfu4repair-extra"]})
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    specs = {}
    find_spec = dependencies.util.find_spec
    monkeypatch.setattr(dependencies.util, "find_spec",
                        lambda name: specs.get(name) if name in {REQUIRED, OPTIONAL} else find_spec(name))
    for root in (REQUIRED, OPTIONAL):
        monkeypatch.delitem(sys.modules, root, raising=False)
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, specs
    dependencies.optional_import_roots.cache_clear()
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root, None)
    invalidate_caches()


def _startup(dependencies, monkeypatch, path):
    module = ModuleType(REQUIRED)
    module.__file__ = str(path)
    monkeypatch.setitem(sys.modules, REQUIRED, module)
    dependencies.record_loaded_required_imports()


def _optional(dependencies):
    return dependencies.optional_import_roots("abstraction", required_extras="")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
@pytest.mark.parametrize("retarget", (True, False), ids=("moved", "stable-replacement"))
def test_secondary_stat_requires_stable_source_resolution(ownership, tmp_path, monkeypatch, retarget):
    import _winapi

    dependencies, specs = ownership
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    original, alias = first / "shared.py", second / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, alias)
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(first), str(junction))
    source = junction / "shared.py"
    try:
        _startup(dependencies, monkeypatch, source)
        monkeypatch.delitem(sys.modules, REQUIRED)
        stat = os.stat
        raced = False

        def race(path, *args, **kwargs):
            nonlocal raced
            if os.fspath(path) == str(source):
                raise OSError("primary stat unavailable")
            if os.fspath(path) == str(original) and not raced:
                raced = True
                if retarget:
                    os.rmdir(junction)
                    _winapi.CreateJunction(str(second), str(junction))
                original.rename(first / "retired.py")
                original.write_text("value = 'replacement'\n", encoding="utf-8")
            return stat(path, *args, **kwargs)

        with monkeypatch.context() as probing:
            probing.setattr(os, "stat", race)
            _optional(dependencies)
        assert raced
        os.rmdir(junction)
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
        assert _optional(dependencies) == (frozenset() if retarget else {OPTIONAL})
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("interleave", ("retry", "fallback"))
def test_startup_merges_intervening_retirement(ownership, tmp_path, monkeypatch, interleave):
    dependencies, specs = ownership
    required, retired, optional = (tmp_path / name for name in ("required.py", "retired.py", "optional.py"))
    required.write_text("value = 73\n", encoding="utf-8")
    _startup(dependencies, monkeypatch, required)
    refresh = dependencies._path_refresh_observations
    calls = 0

    def interrupt(paths):
        nonlocal calls
        result = refresh(paths)
        calls += 1
        with monkeypatch.context() as transaction:
            transaction.setattr(dependencies, "_path_refresh_observations", refresh)
            if calls == 1:
                monkeypatch.delitem(sys.modules, REQUIRED)
                required.rename(retired)
                required.write_text("value = 'replacement'\n", encoding="utf-8")
                os.link(retired, optional)
                specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
                assert _optional(dependencies) == {OPTIONAL}
                required.unlink()
            elif interleave == "fallback":
                transaction.setattr(dependencies, "_optional_root_declarations",
                                    lambda *args: ({"rfu4repair_other"}, set()))
                assert dependencies.optional_import_roots("other", "") == {"rfu4repair_other"}
        return result

    with monkeypatch.context() as recording:
        recording.setattr(dependencies, "_path_refresh_observations", interrupt)
        dependencies.record_loaded_required_imports()
    assert calls == 2
    assert _optional(dependencies) == {OPTIONAL}
    specs.clear()
    optional.unlink()
    retired.unlink()
    assert _optional(dependencies) == {OPTIONAL}


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
def test_later_resolved_spelling_stat_supersedes_junction_stat(ownership, tmp_path, monkeypatch):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    required, retired, optional = target / "required.py", target / "retired.py", tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    try:
        _startup(dependencies, monkeypatch, junction / "required.py")
        monkeypatch.delitem(sys.modules, REQUIRED)
        observe = dependencies._root_observations

        def replace(roots):
            required.rename(retired)
            required.write_text("value = 'replacement'\n", encoding="utf-8")
            specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, required)
            return observe(roots)

        with monkeypatch.context() as transaction:
            transaction.setattr(dependencies, "_root_observations", replace)
            assert _optional(dependencies) == {OPTIONAL}
        required.unlink()
        os.link(retired, optional)
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
        assert _optional(dependencies) == {OPTIONAL}
    finally:
        os.rmdir(junction)


def test_successful_inventory_records_loaded_required_hardlink(ownership, tmp_path, monkeypatch):
    from g6.mcp import families, registrations

    dependencies, _ = ownership
    required, optional = tmp_path / "required.py", tmp_path / f"{OPTIONAL}.py"
    required.write_text(
        "from types import SimpleNamespace\n"
        "def get_tools():\n"
        "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n",
        encoding="utf-8")
    os.link(required, optional)
    _startup(dependencies, monkeypatch, required)
    monkeypatch.syspath_prepend(str(tmp_path))
    server = ModuleType("rfu4repair_server")
    failures = []

    def build():
        try:
            return import_module(OPTIONAL)
        except ModuleNotFoundError as exc:
            failures.append(exc)
            raise

    server.build = build
    monkeypatch.setitem(sys.modules, server.__name__, server)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": f"{server.__name__}:build"})
    monkeypatch.setattr(families, "_DETAILS", {})
    family = families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    rows = families.inventory(family)
    assert next(row for row in rows if row["id"] == "mcp:abstraction/load_state")["available"] is True
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    required.unlink()
    optional.unlink()
    invalidate_caches()
    with pytest.raises(ModuleNotFoundError) as stopped:
        families.inventory(family)
    assert stopped.value is failures[-1]
