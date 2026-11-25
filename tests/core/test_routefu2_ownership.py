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


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
@pytest.mark.parametrize("advertised", (False, True), ids=("unloaded", "still-advertised"))
@pytest.mark.parametrize("missing", ("junction", "target", "file-control"))
def test_unresolved_junction_keeps_required_history(ownership, tmp_path, missing, advertised):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    target.mkdir()
    shared = target / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        for root in (REQUIRED, OPTIONAL):
            specs[root] = util.spec_from_file_location(root, source)
        assert _roots(dependencies, SCOPES[0]) == frozenset()
        if missing == "junction":
            os.rmdir(junction)
        elif missing == "target":
            target.rename(tmp_path / "retained-target")
        else:
            shared.unlink()
        if not advertised:
            specs.clear()
        invalidate_caches()
        failure = ModuleNotFoundError("required alias is unavailable", name=OPTIONAL)
        assert not dependencies.optional_dependency_missing("abstraction", failure)
        specs.clear()
        assert _roots(dependencies, SCOPES[1]) == frozenset()
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
@pytest.mark.parametrize("failed_stat", (True, False), ids=("stat-fails", "healthy-control"))
def test_existing_junction_replacement_retires_history(ownership, tmp_path, monkeypatch, failed_stat):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    replacement = tmp_path / "replacement"
    target.mkdir()
    replacement.mkdir()
    (target / "shared.py").write_text("value = 'required'\n", encoding="utf-8")
    (replacement / "shared.py").write_text("value = 'independent optional'\n", encoding="utf-8")
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        for root in (REQUIRED, OPTIONAL):
            specs[root] = util.spec_from_file_location(root, source)
        assert _roots(dependencies, SCOPES[0]) == frozenset()
        specs.clear()
        os.rmdir(junction)
        _winapi.CreateJunction(str(replacement), str(junction))
        stat = os.stat

        def transient_stat(path, *args, **kwargs):
            if failed_stat and os.fspath(path) == str(source):
                raise OSError("transient stat failure beside a resolved replacement")
            return stat(path, *args, **kwargs)

        with monkeypatch.context() as transient:
            transient.setattr(os, "stat", transient_stat)
            assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
        os.rmdir(junction)
        assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
def test_unresolved_namespace_junction_keeps_history_beside_live_location(ownership, tmp_path):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    extra = tmp_path / "extra"
    target.mkdir()
    extra.mkdir()
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        for root, locations in ((REQUIRED, [str(junction)]), (OPTIONAL, [str(junction), str(extra)])):
            spec = machinery.ModuleSpec(root, loader=None, is_package=True)
            spec.submodule_search_locations = locations
            specs[root] = spec
        assert _roots(dependencies, SCOPES[0]) == frozenset()
        os.rmdir(junction)
        assert _roots(dependencies, SCOPES[1]) == frozenset()
        specs.clear()
        assert _roots(dependencies, SCOPES[2]) == frozenset()
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
def test_unresolved_advertised_alias_keeps_history_through_stat_uncertainty(ownership, tmp_path, monkeypatch):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    target.mkdir()
    (target / "shared.py").write_text("value = 73\n", encoding="utf-8")
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        for root in (REQUIRED, OPTIONAL):
            specs[root] = util.spec_from_file_location(root, source)
        assert _roots(dependencies, SCOPES[0]) == frozenset()
        specs.pop(REQUIRED)
        os.rmdir(junction)
        stat = os.stat

        def transient_stat(path, *args, **kwargs):
            if os.fspath(path) == str(source):
                raise OSError("transient stat failure on an unresolved alias")
            return stat(path, *args, **kwargs)

        with monkeypatch.context() as transient:
            transient.setattr(os, "stat", transient_stat)
            assert _roots(dependencies, SCOPES[1]) == frozenset()
        specs.clear()
        assert _roots(dependencies, SCOPES[2]) == frozenset()
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
@pytest.mark.parametrize("advertised", (False, True), ids=("unloaded", "required-advertised"))
@pytest.mark.parametrize("same_file", (True, False), ids=("same-inode", "independent-file-control"))
def test_junction_retarget_uses_stat_identity(ownership, tmp_path, same_file, advertised):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    replacement = tmp_path / "replacement"
    target.mkdir()
    replacement.mkdir()
    original = target / "shared.py"
    other = replacement / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    if same_file:
        os.link(original, other)
    else:
        other.write_text("value = 'independent optional'\n", encoding="utf-8")
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        for root in (REQUIRED, OPTIONAL):
            specs[root] = util.spec_from_file_location(root, source)
        assert _roots(dependencies, SCOPES[0]) == frozenset()
        before = source.stat()
        previous_path = os.path.realpath(source, strict=True)
        specs.pop(OPTIONAL)
        if not advertised:
            specs.clear()
        os.rmdir(junction)
        _winapi.CreateJunction(str(replacement), str(junction))
        after = source.stat()
        assert os.path.realpath(source, strict=True) != previous_path
        assert ((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)) is same_file
        invalidate_caches()
        assert _roots(dependencies, SCOPES[1]) == (frozenset() if same_file else {OPTIONAL})
        specs.clear()
        os.rmdir(junction)
        failure = ModuleNotFoundError("retargeted required alias is unavailable", name=OPTIONAL)
        assert dependencies.optional_dependency_missing("abstraction", failure) is (not same_file)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)
        other.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
def test_same_inode_retarget_records_new_path_spelling(ownership, tmp_path, monkeypatch):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    replacement = tmp_path / "replacement"
    target.mkdir()
    replacement.mkdir()
    original = target / "shared.py"
    other = replacement / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, other)
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        specs[REQUIRED] = util.spec_from_file_location(REQUIRED, source)
        assert _roots(dependencies, SCOPES[0]) == {OPTIONAL}
        specs.clear()
        os.rmdir(junction)
        _winapi.CreateJunction(str(replacement), str(junction))
        assert os.path.samefile(source, original)
        # Refresh with no live root, then remove the source of that observation.
        assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
        os.rmdir(junction)
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, other)
        stat = os.stat

        def transient_stat(path, *args, **kwargs):
            if os.fspath(path) == str(other):
                raise OSError("only the new resolved spelling is available")
            return stat(path, *args, **kwargs)

        with monkeypatch.context() as transient:
            transient.setattr(os, "stat", transient_stat)
            assert _roots(dependencies, SCOPES[1]) == frozenset()
        specs.clear()
        other.unlink()
        failure = ModuleNotFoundError("newly spelled required alias is unavailable", name=OPTIONAL)
        assert not dependencies.optional_dependency_missing("abstraction", failure)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)
        other.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
def test_path_only_history_retires_on_existing_retarget(ownership, tmp_path, monkeypatch):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    replacement = tmp_path / "replacement"
    target.mkdir()
    replacement.mkdir()
    original = target / "shared.py"
    other = replacement / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, other)
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        for root in (REQUIRED, OPTIONAL):
            specs[root] = util.spec_from_file_location(root, source)
        stat = os.stat

        def transient_stat(path, *args, **kwargs):
            if os.fspath(path) == str(source):
                raise OSError("no initial stat identity")
            return stat(path, *args, **kwargs)

        with monkeypatch.context() as transient:
            transient.setattr(os, "stat", transient_stat)
            assert _roots(dependencies, SCOPES[0]) == frozenset()
        specs.clear()
        os.rmdir(junction)
        _winapi.CreateJunction(str(replacement), str(junction))
        assert os.path.samefile(source, original)
        # There is no retained stat identity to compare with the current one.
        assert _roots(dependencies, SCOPES[1]) == {OPTIONAL}
        os.rmdir(junction)
        assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)
        other.unlink(missing_ok=True)


@pytest.fixture
def startup_ownership(tmp_path, monkeypatch, request):
    from g6 import mcp

    monkeypatch.setenv("G6_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path / "sdk-state"))
    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "off")
    monkeypatch.setenv("FASTMCP_ENV_FILE", os.devnull)
    # Load the real SDK before the ownership fixture supplies dependency metadata.
    mcp._quiet()
    return mcp, request.getfixturevalue("ownership")


@pytest.mark.parametrize("clear_history", (False, True), ids=("retained", "explicit-clear"))
@pytest.mark.parametrize("load_stage", ("quiet", "final-registration"))
def test_successful_startup_records_loaded_required_file(startup_ownership, tmp_path, monkeypatch,
                                                       load_stage, clear_history):
    from g6.mcp import families

    mcp, (dependencies, specs) = startup_ownership
    required = tmp_path / "required.py"
    optional = tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    os.link(required, optional)
    try:
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
        assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
        spec = util.spec_from_file_location(REQUIRED, required)

        def load_required():
            loaded = util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, REQUIRED, loaded)
            spec.loader.exec_module(loaded)
            assert loaded.value == 73

        if load_stage == "quiet":
            quiet = mcp._quiet

            def quiet_with_required():
                quiet()
                load_required()

            monkeypatch.setattr(mcp, "_quiet", quiet_with_required)
        else:
            compact = families.compact_server

            def compact_with_required(server, server_id):
                compact(server, server_id)
                load_required()

            monkeypatch.setattr(families, "compact_server", compact_with_required)

        assert mcp.build_server().name == "g6"
        monkeypatch.delitem(sys.modules, REQUIRED)
        specs.clear()
        required.unlink()
        optional.unlink()
        invalidate_caches()
        failure = ModuleNotFoundError("loaded required alias disappeared", name=OPTIONAL)
        assert not dependencies.optional_dependency_missing("abstraction", failure)
        if clear_history:
            dependencies.optional_import_roots.cache_clear()
            assert dependencies.optional_dependency_missing("abstraction", failure)
    finally:
        optional.unlink(missing_ok=True)


def test_successful_startup_does_not_own_an_unloaded_required_file(startup_ownership, tmp_path):
    mcp, (dependencies, specs) = startup_ownership
    shared = tmp_path / "unloaded.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    specs[REQUIRED] = util.spec_from_file_location(REQUIRED, shared)
    assert mcp.build_server().name == "g6"
    assert REQUIRED not in sys.modules
    specs.clear()
    shared.unlink()
    invalidate_caches()
    failure = ModuleNotFoundError("unloaded optional alias disappeared", name=OPTIONAL)
    assert dependencies.optional_dependency_missing("abstraction", failure)


@pytest.mark.parametrize("clear_history", (False, True), ids=("retained", "explicit-clear"))
def test_namespace_history_survives_one_location_then_the_other(ownership, tmp_path, monkeypatch, clear_history):
    dependencies, specs = ownership
    shared = tmp_path / "shared"
    extra = tmp_path / "extra"
    shared.mkdir()
    extra.mkdir()
    for root, locations in ((REQUIRED, [str(shared)]), (OPTIONAL, [str(shared), str(extra)])):
        spec = machinery.ModuleSpec(root, loader=None, is_package=True)
        spec.submodule_search_locations = locations
        specs[root] = spec
    loaded = util.module_from_spec(specs[OPTIONAL])
    monkeypatch.setitem(sys.modules, OPTIONAL, loaded)
    assert _roots(dependencies, SCOPES[0]) == frozenset()
    shared.rmdir()
    # Mirror the namespace path recalculation: the vanished location is no
    # longer advertised by either independent live source.
    loaded.__path__ = [str(extra)]
    loaded.__spec__.submodule_search_locations = loaded.__path__
    invalidate_caches()
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    monkeypatch.delitem(sys.modules, OPTIONAL)
    specs.clear()
    extra.rmdir()
    invalidate_caches()
    failure = ModuleNotFoundError("namespace alias disappeared", name=OPTIONAL)
    assert not dependencies.optional_dependency_missing("abstraction", failure)
    if clear_history:
        dependencies.optional_import_roots.cache_clear()
        assert dependencies.optional_dependency_missing("abstraction", failure)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
@pytest.mark.parametrize("replace_file", (True, False), ids=("different-inode", "same-inode-control"))
def test_stale_resolved_path_cannot_own_a_different_stat_identity(ownership, tmp_path, monkeypatch,
                                                              replace_file):
    import _winapi

    dependencies, specs = ownership
    target = tmp_path / "target"
    target.mkdir()
    shared = target / "shared.py"
    shared.write_text("value = 'required'\n", encoding="utf-8")
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
        assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
        loaded = ModuleType(REQUIRED)
        loaded.__file__ = str(junction / "shared.py")
        monkeypatch.setitem(sys.modules, REQUIRED, loaded)
        dependencies.record_loaded_required_imports()
        monkeypatch.delitem(sys.modules, REQUIRED)
        os.rmdir(junction)
        if replace_file:
            old = target / "old-required.py"
            shared.rename(old)
            shared.write_text("value = 'independent optional'\n", encoding="utf-8")
            assert (old.stat().st_dev, old.stat().st_ino) != (shared.stat().st_dev, shared.stat().st_ino)
        assert _roots(dependencies, SCOPES[2]) == ({OPTIONAL} if replace_file else frozenset())
        specs.clear()
        shared.unlink()
        failure = ModuleNotFoundError("optional replacement disappeared", name=OPTIONAL)
        assert dependencies.optional_dependency_missing("abstraction", failure) is replace_file
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("location", ("fileless", "file", "cache-only"))
def test_loaded_snapshot_does_not_promote_optional_name_history(ownership, tmp_path, monkeypatch, location):
    dependencies, specs = ownership
    needs = {"g6": ['rfu2build-base; extra == "mcp"', 'rfu2build-extra; extra == "abstraction"'],
             "rfu2build-base": [], "rfu2build-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu2build-base", "rfu2build-extra"], OPTIONAL: ["rfu2build-extra"]})
    old_optional = tmp_path / "optional-only.py"
    actual_required = tmp_path / "actual-required.py"
    old_optional.write_text("value = 'optional only'\n", encoding="utf-8")
    actual_required.write_text("value = 'required'\n", encoding="utf-8")
    for root in (REQUIRED, OPTIONAL):
        specs[root] = util.spec_from_file_location(root, old_optional)
    assert _roots(dependencies, SCOPES[2]) == {REQUIRED, OPTIONAL}
    loaded = ModuleType(REQUIRED)
    loaded.__spec__ = machinery.ModuleSpec(REQUIRED, loader=None, origin="frozen")
    if location == "file":
        loaded.__file__ = str(actual_required)
    elif location == "cache-only":
        loaded.__cached__ = str(old_optional)
        loaded.__spec__.cached = str(old_optional)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    old_optional.unlink()
    actual_required.unlink()
    assert OPTIONAL in _roots(dependencies, SCOPES[2])


@pytest.mark.parametrize("mutate_locations", (False, True), ids=("unload-only", "unload-and-mutate"))
def test_loaded_snapshot_keeps_original_file_after_unload(ownership, tmp_path, monkeypatch, mutate_locations):
    dependencies, specs = ownership
    loaded_alias = "rfu2build_loaded_alias"
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu2build-base"], OPTIONAL: ["rfu2build-extra"],
                                 loaded_alias: ["rfu2build-extra"]})
    original_file = tmp_path / "actually-loaded.py"
    replacement = tmp_path / "never-loaded.py"
    original_file.write_text("value = 'required'\n", encoding="utf-8")
    replacement.write_text("value = 'optional only'\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, replacement)
    specs[loaded_alias] = util.spec_from_file_location(loaded_alias, original_file)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL, loaded_alias}
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(original_file)
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, original_file)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    record = dependencies._without_required_aliases

    def unload_after_snapshot(optional, required):
        assert not optional and REQUIRED in required
        monkeypatch.delitem(sys.modules, REQUIRED)
        specs[REQUIRED] = util.spec_from_file_location(REQUIRED, replacement)
        if mutate_locations:
            loaded.__file__ = str(replacement)
            loaded.__spec__.origin = str(replacement)
        return record(optional, required)

    with monkeypatch.context() as snapshot:
        snapshot.setattr(dependencies, "_without_required_aliases", unload_after_snapshot)
        dependencies.record_loaded_required_imports()
    specs.clear()
    original_file.unlink()
    replacement.unlink()
    # Both optional names were seen before startup; only the actually loaded
    # file becomes required, even after its module object is unloaded/mutated.
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}


def test_loaded_snapshot_freezes_each_namespace_provider(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    other_alias = "rfu2build_namespace_alias"
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu2build-base"], OPTIONAL: ["rfu2build-extra"],
                                 other_alias: ["rfu2build-extra"]})
    module_location = tmp_path / "module-location"
    spec_location = tmp_path / "spec-location"
    module_location.mkdir()
    spec_location.mkdir()
    for root, location in ((OPTIONAL, module_location), (other_alias, spec_location)):
        spec = machinery.ModuleSpec(root, loader=None, is_package=True)
        spec.submodule_search_locations = [str(location)]
        specs[root] = spec
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL, other_alias}
    loaded = ModuleType(REQUIRED)
    loaded.__spec__ = machinery.ModuleSpec(REQUIRED, loader=None, is_package=True)
    loaded.__spec__.submodule_search_locations = [str(spec_location)]
    loaded.__path__ = [str(module_location)]
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    record = dependencies._without_required_aliases

    def remove_namespace_after_snapshot(optional, required):
        assert not optional and REQUIRED in required
        monkeypatch.delitem(sys.modules, REQUIRED)
        loaded.__path__.clear()
        loaded.__spec__.submodule_search_locations.clear()
        return record(optional, required)

    with monkeypatch.context() as snapshot:
        snapshot.setattr(dependencies, "_without_required_aliases", remove_namespace_after_snapshot)
        dependencies.record_loaded_required_imports()
    specs.clear()
    module_location.rmdir()
    spec_location.rmdir()
    assert _roots(dependencies, SCOPES[2]) == frozenset()
    dependencies.optional_import_roots.cache_clear()
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL, other_alias}


@pytest.mark.parametrize("entry", ("startup", "discovery"))
@pytest.mark.parametrize("declarations", ("cold", "cached"))
def test_ownership_lock_excludes_foreign_code(ownership, tmp_path, monkeypatch, entry, declarations):
    import builtins

    dependencies, specs = ownership
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    arguments = ("", "mcp") if entry == "startup" else ("abstraction", "mcp")
    if declarations == "cached":
        dependencies._optional_root_declarations(*arguments)
    events = []
    owned = dependencies._ownership_lock._is_owned

    def observe(label):
        events.append((label, owned()))

    class WatchedModule(ModuleType):
        def __getattribute__(self, name):
            observe("module." + name)
            return super().__getattribute__(name)

    class WatchedSpec(machinery.ModuleSpec):
        def __getattribute__(self, name):
            observe("spec." + name)
            return super().__getattribute__(name)

    class Location(os.PathLike):
        def __fspath__(self):
            observe("fspath")
            return str(shared)

    class NamespaceLocations:
        def __iter__(self):
            observe("namespace")
            return iter((str(tmp_path),))

    loaded = WatchedModule(REQUIRED)
    spec = WatchedSpec(REQUIRED, loader=None, origin=str(shared), is_package=True)
    spec.has_location = True
    spec.submodule_search_locations = NamespaceLocations()
    loaded.__spec__ = spec
    loaded.__file__ = Location()
    loaded.__path__ = NamespaceLocations()
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    requires = dependencies.metadata.requires
    find_spec = dependencies.util.find_spec
    import_module = builtins.__import__

    def read_metadata(name):
        observe("metadata")
        return requires(name)

    def find_module(name):
        observe("find-spec")
        return find_spec(name)

    def run_import(*args, **kwargs):
        observe("import")
        return import_module(*args, **kwargs)

    events.clear()
    with monkeypatch.context() as watched:
        watched.setattr(dependencies.metadata, "requires", read_metadata)
        watched.setattr(dependencies.util, "find_spec", find_module)
        watched.setattr(builtins, "__import__", run_import)
        if entry == "startup":
            dependencies.record_loaded_required_imports()
        else:
            assert _roots(dependencies, SCOPES[1]) == frozenset()
    assert not [label for label, locked in events if locked], events
    assert any(label.startswith("module.") for label, _ in events)
    assert any(label.startswith("spec.") for label, _ in events)
    assert any(label == "namespace" for label, _ in events)
    assert any(label == "fspath" for label, _ in events)
    if declarations == "cold":
        assert any(label == "metadata" for label, _ in events)
        assert any(label == "import" for label, _ in events)
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    shared.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset()
    dependencies.optional_import_roots.cache_clear()
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}


@pytest.mark.parametrize("location", ("file", "origin", "module-namespace", "spec-namespace"))
def test_loaded_unhashable_pathlike_keeps_required_ownership(ownership, tmp_path, monkeypatch, location):
    dependencies, specs = ownership
    namespace = location.endswith("namespace")
    shared = tmp_path / ("shared" if namespace else "shared.py")
    if namespace:
        shared.mkdir()
        optional_spec = machinery.ModuleSpec(OPTIONAL, loader=None, is_package=True)
        optional_spec.submodule_search_locations = [str(shared)]
    else:
        shared.write_text("value = 73\n", encoding="utf-8")
        optional_spec = util.spec_from_file_location(OPTIONAL, shared)
    specs[OPTIONAL] = optional_spec
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}

    class Location(os.PathLike):
        __hash__ = None

        def __fspath__(self):
            return str(shared)

    loaded = ModuleType(REQUIRED)
    loaded.__spec__ = machinery.ModuleSpec(REQUIRED, loader=None)
    if location == "file":
        loaded.__file__ = Location()
    elif location == "origin":
        loaded.__spec__.origin = Location()
    elif location == "module-namespace":
        loaded.__path__ = [object(), "built-in", "frozen", Location()]
    else:
        loaded.__spec__.submodule_search_locations = [object(), "built-in", "frozen", Location()]
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    if namespace:
        shared.rmdir()
    else:
        shared.unlink()
    failure = ModuleNotFoundError("loaded required location disappeared", name=OPTIONAL)
    assert not dependencies.optional_dependency_missing("abstraction", failure)
    dependencies.optional_import_roots.cache_clear()
    assert dependencies.optional_dependency_missing("abstraction", failure)


def test_ownership_seam_keeps_two_positional_arguments(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    original = dependencies._without_required_aliases
    calls = []
    entry = "discovery"

    def two_arguments(optional, required, /):
        assert dependencies._ownership_lock._is_owned()
        assert bool(optional) == (entry != "startup")
        assert REQUIRED in required
        calls.append(bool(optional))
        return original(optional, required)

    monkeypatch.setattr(dependencies, "_without_required_aliases", two_arguments)
    assert _roots(dependencies, SCOPES[0]) == {OPTIONAL}
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(shared)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    entry = "startup"
    dependencies.record_loaded_required_imports()
    entry = "discovery"
    assert dependencies.optional_import_roots("", required_extras="mcp") == frozenset()
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    shared.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset()
    dependencies.optional_import_roots.cache_clear()
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    assert calls == [True, True, False, True, True]
