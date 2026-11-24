# SPDX-License-Identifier: Apache-2.0
"""Required file ownership crosses scopes, but never crosses a replacement file."""
from importlib import invalidate_caches, machinery, util
import os
import sys
from types import ModuleType

import pytest


REQUIRED = "rfu2build_required"
OPTIONAL = "rfu2build_optional"
STARTUP_SCOPE = ("mcp", "")
SCOPES = (("abstraction,mcp", "mcp"), ("abstraction", "mcp"), ("abstraction", ""))


@pytest.fixture
def ownership(monkeypatch):
    from g6.mcp import dependencies

    needs = {"g6": ["rfu2build-base", 'rfu2build-extra; extra == "abstraction"'],
             "rfu2build-base": [], "rfu2build-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu2build-base"], OPTIONAL: ["rfu2build-extra"]})
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    specs = {}
    monkeypatch.setattr(dependencies.util, "find_spec", lambda name: specs.get(name))
    monkeypatch.delitem(sys.modules, REQUIRED, raising=False)
    monkeypatch.delitem(sys.modules, OPTIONAL, raising=False)
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, specs
    dependencies.optional_import_roots.cache_clear()


def _roots(dependencies, scope):
    extras, required_extras = scope
    return dependencies.optional_import_roots(extras, required_extras=required_extras)


@pytest.mark.parametrize("warm_scope,next_scope", (
    (STARTUP_SCOPE, SCOPES[0]), (SCOPES[0], SCOPES[1]), (SCOPES[0], SCOPES[2]),
    (SCOPES[1], SCOPES[1]),
), ids=("startup-to-family", "family-to-other-family", "different-required-extras", "same-scope"))
def test_required_file_history_crosses_scopes(ownership, tmp_path, warm_scope, next_scope):
    dependencies, specs = ownership
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, shared)
    assert _roots(dependencies, warm_scope) == (frozenset() if warm_scope == STARTUP_SCOPE else {OPTIONAL})
    specs.clear()
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    assert _roots(dependencies, next_scope) == frozenset()
    specs.clear()
    shared.unlink()
    invalidate_caches()
    assert _roots(dependencies, next_scope) == frozenset()


def test_reused_path_loses_old_required_identity(ownership, tmp_path):
    dependencies, specs = ownership
    slot = tmp_path / "slot.py"
    slot.write_text("value = 'old required'\n", encoding="utf-8")
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, slot)
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
    # Rename keeps the old identity allocated, avoiding inode reuse in this test.
    old = tmp_path / "old.py"
    slot.rename(old)
    replacement = tmp_path / "replacement.py"
    replacement.write_text("value = 'new required'\n", encoding="utf-8")
    slot.write_text("value = 'independent optional'\n", encoding="utf-8")
    assert (old.stat().st_dev, old.stat().st_ino) != (slot.stat().st_dev, slot.stat().st_ino)
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, replacement)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, slot)
    invalidate_caches()
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
    specs.pop(OPTIONAL)
    slot.unlink()
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}


def test_replaced_hardlink_loses_alias_ownership(ownership, tmp_path):
    dependencies, specs = ownership
    required = tmp_path / "required.py"
    optional = tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    os.link(required, optional)
    assert os.path.samefile(required, optional)
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, required)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    optional.unlink()
    optional.write_text("value = 'independent optional'\n", encoding="utf-8")
    assert not os.path.samefile(required, optional)
    invalidate_caches()
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
    specs.pop(OPTIONAL)
    optional.unlink()
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}


def test_changed_alias_path_drops_its_previous_file(ownership, tmp_path):
    dependencies, specs = ownership
    required = tmp_path / "required.py"
    optional = tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    optional.write_text("value = 'new alias target'\n", encoding="utf-8")
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, required)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, required)
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
    specs.pop(OPTIONAL)
    optional.unlink()
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}


def test_explicit_clear_drops_history_in_every_scope(ownership, tmp_path):
    dependencies, specs = ownership
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, shared)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    for scope in SCOPES:
        assert _roots(dependencies, scope) == frozenset()
    specs.clear()
    shared.unlink()
    for scope in SCOPES:
        assert _roots(dependencies, scope) == frozenset()
    dependencies.optional_import_roots.cache_clear()
    for scope in SCOPES:
        assert _roots(dependencies, scope) == {OPTIONAL}


def test_absent_namespace_location_keeps_ownership_beside_live_location(ownership, tmp_path):
    dependencies, specs = ownership
    shared = tmp_path / "shared"
    extra = tmp_path / "extra"
    shared.mkdir()
    extra.mkdir()
    for root, locations in ((REQUIRED, [str(shared)]), (OPTIONAL, [str(shared), str(extra)])):
        spec = machinery.ModuleSpec(root, loader=None, is_package=True)
        spec.submodule_search_locations = locations
        specs[root] = spec
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    shared.rmdir()
    invalidate_caches()
    assert _roots(dependencies, SCOPES[1]) == frozenset()


def test_live_object_alias_does_not_create_file_ownership(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    file = tmp_path / "optional.py"
    file.write_text("value = 73\n", encoding="utf-8")
    shared = ModuleType(REQUIRED)
    shared.__file__ = str(file)
    monkeypatch.setitem(sys.modules, REQUIRED, shared)
    monkeypatch.setitem(sys.modules, OPTIONAL, shared)
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    # A file observed directly on a required root is established ownership.
    # The object-only control has no path evidence at all after an explicit clear.
    del shared.__file__
    dependencies.optional_import_roots.cache_clear()
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    monkeypatch.delitem(sys.modules, REQUIRED)
    monkeypatch.delitem(sys.modules, OPTIONAL)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, file)
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}


@pytest.mark.parametrize("bad_parent", (True, False), ids=("bad-parent", "package-control"))
def test_startup_keeps_sdk_export_failure(monkeypatch, bad_parent):
    from g6 import mcp

    failure = ImportError("broken Context export", name="fastmcp")
    sdk = ModuleType("fastmcp")

    def export(name):
        if name == "Context":
            raise failure
        raise AttributeError(name)

    sdk.__getattr__ = export
    parent = ModuleType("mcp")
    if not bad_parent:
        parent.__path__ = []
    monkeypatch.setitem(sys.modules, "fastmcp", sdk)
    monkeypatch.setitem(sys.modules, "mcp", parent)
    monkeypatch.setitem(sys.modules, "mcp.server", ModuleType("mcp.server"))
    monkeypatch.setattr(mcp, "_quiet", lambda: None)
    # Confirm independently which exception this SDK actually exports.
    with pytest.raises(ImportError) as original:
        exec("from fastmcp import Context, FastMCP", {})
    assert original.value is failure
    with pytest.raises(SystemExit) as stopped:
        mcp.build_server()
    assert stopped.value.__cause__ is failure
    assert "MCP extra" not in str(stopped.value)


def test_changed_source_retires_old_identity_despite_independent_lookup_failure(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    required = tmp_path / "required.py"
    optional = tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    optional.write_text("value = 'independent optional'\n", encoding="utf-8")
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, required)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, required)
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    loaded = ModuleType(OPTIONAL)
    loaded.__spec__ = util.spec_from_file_location(OPTIONAL, optional)

    def access(name):
        if name == "__file__":
            raise OSError("independent module attribute lookup failed")
        raise AttributeError(name)

    loaded.__getattr__ = access
    monkeypatch.setitem(sys.modules, OPTIONAL, loaded)
    assert _roots(dependencies, SCOPES[1]) == frozenset()  # Lookup uncertainty for this discovery only.
    monkeypatch.delitem(sys.modules, OPTIONAL)
    specs.pop(OPTIONAL)
    optional.unlink()
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}


@pytest.mark.parametrize("failed_probe", (True, False), ids=("stat-fails", "healthy-control"))
def test_required_path_evidence_survives_stat_failure(ownership, tmp_path, monkeypatch, failed_probe):
    dependencies, specs = ownership
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    assert _roots(dependencies, SCOPES[0]) == {OPTIONAL}
    specs.clear()
    (tmp_path / "through").mkdir()
    spelling = str(tmp_path / "through") + os.sep + ".." + os.sep + "shared.py"
    spec = util.spec_from_file_location(REQUIRED, spelling)
    spec.origin = spelling
    specs[REQUIRED] = spec
    stat = os.stat

    def transient_stat(path, *args, **kwargs):
        if failed_probe and os.fspath(path) == spelling:
            raise OSError("transient required stat failure")
        return stat(path, *args, **kwargs)

    failure = ModuleNotFoundError("required alias is unavailable", name=OPTIONAL)
    with monkeypatch.context() as transient:
        transient.setattr(os, "stat", transient_stat)
        assert not dependencies.optional_dependency_missing("abstraction", failure)
    specs.clear()
    shared.unlink()
    invalidate_caches()
    assert _roots(dependencies, SCOPES[1]) == frozenset()


@pytest.mark.parametrize("failed_probe", (True, False), ids=("realpath-fails", "healthy-control"))
def test_inode_evidence_survives_realpath_failure_and_removal(ownership, tmp_path, monkeypatch, failed_probe):
    dependencies, specs = ownership
    required = tmp_path / "required.py"
    optional = tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    os.link(required, optional)
    try:
        specs[REQUIRED] = util.spec_from_file_location(REQUIRED, required)
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
        realpath = os.path.realpath

        def transient_realpath(path, *args, **kwargs):
            if failed_probe and os.fspath(path) == str(optional):
                raise OSError("transient optional realpath failure")
            return realpath(path, *args, **kwargs)

        with monkeypatch.context() as transient:
            transient.setattr(os.path, "realpath", transient_realpath)
            assert _roots(dependencies, SCOPES[0]) == frozenset()
        specs.clear()
        required.unlink()
        optional.unlink()
        invalidate_caches()
        failure = ModuleNotFoundError("established required alias is unavailable", name=OPTIONAL)
        assert not dependencies.optional_dependency_missing("abstraction", failure)
    finally:
        optional.unlink(missing_ok=True)


def test_inode_replacement_retires_history_while_realpath_fails(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    required = tmp_path / "required.py"
    optional = tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    os.link(required, optional)
    try:
        specs[REQUIRED] = util.spec_from_file_location(REQUIRED, required)
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
        assert _roots(dependencies, SCOPES[0]) == frozenset()
        optional.unlink()
        optional.write_text("value = 'independent optional'\n", encoding="utf-8")
        assert not os.path.samefile(required, optional)
        realpath = os.path.realpath

        def transient_realpath(path, *args, **kwargs):
            if os.fspath(path) == str(optional):
                raise OSError("transient optional realpath failure")
            return realpath(path, *args, **kwargs)

        with monkeypatch.context() as transient:
            transient.setattr(os.path, "realpath", transient_realpath)
            assert _roots(dependencies, SCOPES[0]) == frozenset()  # Live uncertainty only.
        specs.pop(OPTIONAL)
        optional.unlink()
        assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
    finally:
        optional.unlink(missing_ok=True)


def test_path_only_history_acquires_stat_identity_before_replacement(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    slot = tmp_path / "slot.py"
    slot.write_text("value = 'required'\n", encoding="utf-8")
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, slot)
    stat = os.stat

    def transient_stat(path, *args, **kwargs):
        if os.fspath(path) == str(slot):
            raise OSError("transient required stat failure")
        return stat(path, *args, **kwargs)

    with monkeypatch.context() as transient:
        transient.setattr(os, "stat", transient_stat)
        assert _roots(dependencies, SCOPES[0]) == {OPTIONAL}
    specs.clear()
    # Refresh the retained path without a live spec; the successful stat must
    # supply the identity needed to detect a later independent replacement.
    assert _roots(dependencies, STARTUP_SCOPE) == frozenset()
    slot.rename(tmp_path / "old.py")
    slot.write_text("value = 'independent optional'\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, slot)
    assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
