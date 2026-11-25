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


@pytest.mark.parametrize("entry", ("startup", "discovery"))
def test_plain_location_boundary_excludes_foreign_string_hooks(ownership, tmp_path, monkeypatch, entry):
    dependencies, specs = ownership
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    events = []

    class Location(str):
        def __hash__(self):
            events.append(("hash", dependencies._ownership_lock._is_owned()))
            return str.__hash__(self)

        def __eq__(self, other):
            events.append(("eq", dependencies._ownership_lock._is_owned()))
            return str.__eq__(self, other)

        def __str__(self):
            raise AssertionError("location coercion must not call a foreign __str__")

    if entry == "startup":
        # Startup reads only loaded required modules, so the optional root's own file history
        # must be observed first, as the plain-location startup controls in this file do.
        assert _roots(dependencies, SCOPES[0]) == {OPTIONAL}
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = Location(str(shared))
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    if entry == "startup":
        dependencies.record_loaded_required_imports()
    else:
        assert _roots(dependencies, SCOPES[1]) == frozenset()
    assert not [event for event in events if event[1]], events
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    shared.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset()


def _repair3_extra_required_metadata(dependencies, monkeypatch):
    needs = {"g6": ['rfu2build-base; extra == "mcp"', 'rfu2build-extra; extra == "abstraction"'],
             "rfu2build-base": [], "rfu2build-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    return needs


def test_stale_optional_observation_keeps_new_required_alias(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    optional = tmp_path / "optional.py"
    required = tmp_path / "required.py"
    old = tmp_path / "old.py"
    optional.write_text("value = 'optional'\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
    observe = dependencies._root_observations
    pending = True

    def supersede(roots):
        nonlocal pending
        result = observe(roots)
        if pending:
            pending = False
            optional.rename(old)
            optional.write_text("value = 'required'\n", encoding="utf-8")
            os.link(optional, required)
            loaded = ModuleType(REQUIRED)
            loaded.__file__ = str(required)
            monkeypatch.setitem(sys.modules, REQUIRED, loaded)
            dependencies.record_loaded_required_imports()
            assert _roots(dependencies, SCOPES[2]) == frozenset()
            monkeypatch.delitem(sys.modules, REQUIRED)
            specs.clear()
            required.unlink()
            optional.unlink()
            old.unlink()
        return result

    monkeypatch.setattr(dependencies, "_root_observations", supersede)
    _roots(dependencies, SCOPES[1])
    failure = ModuleNotFoundError("required alias disappeared", name=OPTIONAL)
    assert not dependencies.optional_dependency_missing("abstraction", failure)


def test_stale_observation_does_not_restore_retired_identity(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    required = tmp_path / "required.py"
    retired = tmp_path / "retired.py"
    optional = tmp_path / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(required)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    dependencies.record_loaded_required_imports()
    observe = dependencies._root_observations
    pending = True

    def supersede(roots):
        nonlocal pending
        result = observe(roots)
        if pending:
            pending = False
            monkeypatch.delitem(sys.modules, REQUIRED)
            required.rename(retired)
            required.write_text("value = 'replacement'\n", encoding="utf-8")
            assert required.stat().st_ino != retired.stat().st_ino
            os.link(retired, optional)
            specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
            assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
            required.unlink()
        return result

    monkeypatch.setattr(dependencies, "_root_observations", supersede)
    _roots(dependencies, SCOPES[1])
    specs.clear()
    optional.unlink()
    retired.unlink()
    failure = ModuleNotFoundError("optional old file disappeared", name=OPTIONAL)
    assert dependencies.optional_dependency_missing("abstraction", failure)


def test_clear_during_observation_discards_preclear_required_owner(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    needs = _repair3_extra_required_metadata(dependencies, monkeypatch)
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    for root in (REQUIRED, OPTIONAL):
        specs[root] = util.spec_from_file_location(root, shared)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    observe = dependencies._root_observations
    pending = True

    def clear_after_probe(roots):
        nonlocal pending
        result = observe(roots)
        if pending:
            pending = False
            needs["g6"] = ['rfu2build-extra; extra == "abstraction"']
            specs.pop(REQUIRED)
            dependencies.optional_import_roots.cache_clear()
        return result

    monkeypatch.setattr(dependencies, "_root_observations", clear_after_probe)
    _roots(dependencies, SCOPES[1])
    specs.clear()
    shared.unlink()
    failure = ModuleNotFoundError("now optional file disappeared", name=OPTIONAL)
    assert dependencies.optional_dependency_missing("abstraction", failure)


@pytest.mark.parametrize("invalid", ("raises", "non-string"))
def test_discovery_namespace_rejects_each_invalid_entry_independently(ownership, tmp_path, invalid):
    dependencies, specs = ownership
    shared = tmp_path / "shared"
    shared.mkdir()

    class Invalid(os.PathLike):
        def __fspath__(self):
            if invalid == "raises":
                raise ValueError("bad namespace entry")
            return 73

    for root, locations in ((REQUIRED, [Invalid(), str(shared)]), (OPTIONAL, [str(shared)])):
        spec = machinery.ModuleSpec(root, loader=None, is_package=True)
        spec.submodule_search_locations = locations
        specs[root] = spec
    assert _roots(dependencies, SCOPES[1]) == frozenset()
    specs.clear()
    shared.rmdir()
    failure = ModuleNotFoundError("required namespace disappeared", name=OPTIONAL)
    assert not dependencies.optional_dependency_missing("abstraction", failure)


@pytest.mark.parametrize("interleaving", ("unrelated", "sustained", "clear", "serial"))
def test_startup_loaded_owner_survives_unrelated_generation_changes(ownership, tmp_path, monkeypatch, interleaving):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    shared = tmp_path / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, shared)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(shared)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    refresh = dependencies._path_refresh_observations
    refreshes = 0

    def interleave(paths):
        nonlocal refreshes
        result = refresh(paths)
        refreshes += 1
        if interleaving == "clear" and refreshes == 1:
            dependencies.optional_import_roots.cache_clear()
        elif interleaving == "sustained" or (interleaving == "unrelated" and refreshes == 1):
            # A completed discovery that cannot see the loaded required root.
            other = f"rfu4build_other_{refreshes}"
            with monkeypatch.context() as transaction:
                transaction.setattr(dependencies, "_path_refresh_observations", refresh)
                transaction.setattr(dependencies, "_optional_root_declarations", lambda *args: ({other}, set()))
                assert dependencies.optional_import_roots("other", required_extras="") == {other}
        return result

    with monkeypatch.context() as recording:
        recording.setattr(dependencies, "_path_refresh_observations", interleave)
        dependencies.record_loaded_required_imports()
    assert refreshes <= 2
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    shared.unlink()
    expected = {OPTIONAL} if interleaving == "clear" else frozenset()
    assert _roots(dependencies, SCOPES[2]) == expected


@pytest.mark.parametrize("entry", ("discovery", "startup"))
def test_transaction_later_stat_retires_earlier_required_owner(ownership, tmp_path, monkeypatch, entry):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    required, retired, optional = (tmp_path / name for name in ("required.py", "retired.py", "optional.py"))
    required.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(required)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    dependencies.record_loaded_required_imports()

    def replace():
        required.rename(retired)
        required.write_text("value = 'replacement'\n", encoding="utf-8")
        assert required.stat().st_ino != retired.stat().st_ino
        os.link(retired, optional)
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)

    seam = "_root_observations" if entry == "discovery" else "_path_refresh_observations"
    observe = getattr(dependencies, seam)

    def replace_between_probes(paths):
        replace()
        return observe(paths)

    with monkeypatch.context() as transaction:
        transaction.setattr(dependencies, seam, replace_between_probes)
        if entry == "discovery":
            _roots(dependencies, SCOPES[1])
        else:
            dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    required.unlink()
    # No successful probe of the replacement may be needed to repair history.
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    specs.clear()
    optional.unlink()
    retired.unlink()
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}


@pytest.mark.skipif(os.name != "nt", reason="Windows junction ownership")
@pytest.mark.parametrize("target_kind", ("same", "different"))
@pytest.mark.parametrize("failure", ("primary", "both", "healthy"))
def test_resolved_stat_decides_retargeted_owner(ownership, tmp_path, monkeypatch, target_kind, failure):
    import _winapi

    dependencies, specs = ownership
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    original, alias = first / "shared.py", second / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    if target_kind == "same":
        os.link(original, alias)
    else:
        alias.write_text("value = 'replacement'\n", encoding="utf-8")
    junction = tmp_path / "site"
    _winapi.CreateJunction(str(first), str(junction))
    try:
        source = junction / "shared.py"
        for root in (REQUIRED, OPTIONAL):
            specs[root] = util.spec_from_file_location(root, source)
        assert _roots(dependencies, SCOPES[0]) == frozenset()
        specs.clear()
        os.rmdir(junction)
        _winapi.CreateJunction(str(second), str(junction))
        stat = os.stat

        def unavailable_stat(path, *args, **kwargs):
            spelling = os.fspath(path)
            if failure != "healthy" and (spelling == str(source) or (failure == "both" and spelling == str(alias))):
                raise OSError("transient stat failure")
            return stat(path, *args, **kwargs)

        expected = {OPTIONAL} if target_kind == "different" and failure != "both" else frozenset()
        with monkeypatch.context() as probing:
            probing.setattr(os, "stat", unavailable_stat)
            _roots(dependencies, SCOPES[1])  # Current uncertainty alone cannot decide the retained result.
        os.rmdir(junction)
        assert _roots(dependencies, SCOPES[2]) == expected
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("entry", ("startup", "discovery"))
@pytest.mark.parametrize("location", ("namespace", "file", "origin"))
@pytest.mark.parametrize("spelling", ("frozen", "built-in"))
def test_sentinels_belong_only_to_spec_origin(ownership, tmp_path, monkeypatch, entry, location, spelling):
    dependencies, specs = ownership
    monkeypatch.chdir(tmp_path)
    shared = tmp_path / spelling
    if location == "namespace":
        shared.mkdir()
    else:
        shared.write_text("value = 73\n", encoding="utf-8")
    optional = machinery.ModuleSpec(OPTIONAL, loader=None, is_package=True)
    if location == "namespace":
        optional.submodule_search_locations = [str(shared)]
    else:
        optional = util.spec_from_file_location(OPTIONAL, str(shared), loader=machinery.SourceFileLoader(OPTIONAL, str(shared)))
    specs[OPTIONAL] = optional
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    required = ModuleType(REQUIRED)
    required.__spec__ = machinery.ModuleSpec(REQUIRED, loader=None, origin=spelling, is_package=True)
    required.__spec__.has_location = True
    if location == "namespace":
        required.__path__ = [spelling]
        required.__spec__.submodule_search_locations = [spelling]
    elif location == "file":
        required.__file__ = spelling
    monkeypatch.setitem(sys.modules, REQUIRED, required)
    if entry == "startup":
        dependencies.record_loaded_required_imports()
    else:
        _roots(dependencies, SCOPES[1])
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    if location == "namespace":
        shared.rmdir()
    else:
        shared.unlink()
    expected = {OPTIONAL} if location == "origin" else frozenset()
    assert _roots(dependencies, SCOPES[2]) == expected


def test_startup_contention_fallback_reconciles_its_loaded_probes(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    shared, retired, optional = (tmp_path / name for name in ("shared.py", "retired.py", "optional.py"))
    shared.write_text("value = 73\n", encoding="utf-8")
    os.link(shared, optional)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    loaded = ModuleType(REQUIRED)
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, shared)
    loaded.__file__ = str(shared)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    stat, refresh = dependencies._stat_identity, dependencies._path_refresh_observations
    replaced, refreshes = False, 0

    def replace_after_first_stat(path):
        nonlocal replaced
        identity = stat(path)
        if path == str(shared) and not replaced:
            replaced = True
            shared.rename(retired)
            shared.write_text("value = 'replacement'\n", encoding="utf-8")
            assert identity != stat(str(shared))
        return identity

    def supersede_every_refresh(paths):
        nonlocal refreshes
        result = refresh(paths)
        refreshes += 1
        other = f"rfu4build_fallback_{refreshes}"
        with monkeypatch.context() as transaction:
            transaction.setattr(dependencies, "_path_refresh_observations", refresh)
            transaction.setattr(dependencies, "_optional_root_declarations", lambda *args: ({other}, set()))
            assert dependencies.optional_import_roots("other", required_extras="") == {other}
        return result

    with monkeypatch.context() as recording:
        recording.setattr(dependencies, "_stat_identity", replace_after_first_stat)
        recording.setattr(dependencies, "_path_refresh_observations", supersede_every_refresh)
        dependencies.record_loaded_required_imports()
    assert replaced and 1 <= refreshes <= 2
    monkeypatch.delitem(sys.modules, REQUIRED)
    shared.unlink()  # A later discovery cannot repair a stale fallback by statting the replacement.
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    specs.clear()
    optional.unlink()
    retired.unlink()
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}


@pytest.mark.parametrize("entry", ("startup", "discovery"))
@pytest.mark.parametrize("contention", ("retry", "sustained"))
@pytest.mark.parametrize("later", ("absent", "unreadable", "direct-restored"))
def test_retry_retains_completed_refresh_stat(ownership, tmp_path, monkeypatch, entry, contention, later):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    source, retired, replacement = (tmp_path / name for name in ("source.py", "retired.py", "replacement.py"))
    source.write_text("value = 'required'\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    dependencies.record_loaded_required_imports()
    if entry == "discovery":
        monkeypatch.delitem(sys.modules, REQUIRED)
    refresh = dependencies._path_refresh_observations
    stat = dependencies._stat_identity
    calls = 0

    def probe(path):
        if calls > 1 and later == "unreadable" and path == str(source):
            raise OSError("recorded source unreadable")
        return stat(path)

    def interrupt(paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            source.rename(retired)
            source.write_text("value = 'replacement'\n", encoding="utf-8")
        result = refresh(paths)
        if calls == 1:
            source.rename(replacement)
            monkeypatch.delitem(sys.modules, REQUIRED, raising=False)
            if later == "direct-restored":
                retired.rename(source)
        if calls == 1 or contention == "sustained":
            with monkeypatch.context() as nested:
                nested.setattr(dependencies, "_path_refresh_observations", refresh)
                other = f"rfu5build_other_{calls}"
                nested.setattr(dependencies, "_optional_root_declarations", lambda *args: ({other}, set()))
                before = dependencies._ownership_generation
                dependencies.optional_import_roots("other", "")
                assert dependencies._ownership_generation > before
        return result

    with monkeypatch.context() as transaction:
        transaction.setattr(dependencies, "_path_refresh_observations", interrupt)
        transaction.setattr(dependencies, "_stat_identity", probe)
        if entry == "startup":
            dependencies.record_loaded_required_imports()
        else:
            result = _roots(dependencies, SCOPES[2])
            if contention == "sustained":
                assert result == frozenset()
    assert calls == 2
    old = source if later == "direct-restored" else retired
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, old)
    expected = frozenset() if later == "direct-restored" else {OPTIONAL}
    assert _roots(dependencies, SCOPES[2]) == expected
    specs.clear()
    old.unlink()
    replacement.unlink()
    assert _roots(dependencies, SCOPES[2]) == expected


@pytest.mark.parametrize("contention", ("retry", "sustained", "clear-control"))
def test_discovery_retry_retains_completed_required_root(ownership, tmp_path, monkeypatch, contention):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    source, removed = tmp_path / "shared.py", tmp_path / "removed.py"
    source.write_text("value = 'new required file'\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, source)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    observe = dependencies._root_observations
    calls = 0

    def interrupt(roots):
        nonlocal calls
        result = observe(roots)
        calls += 1
        if calls == 1:
            monkeypatch.delitem(sys.modules, REQUIRED)
            specs.clear()
            source.rename(removed)
        if calls == 1 or contention == "sustained":
            with monkeypatch.context() as nested:
                nested.setattr(dependencies, "_root_observations", observe)
                if contention == "clear-control":
                    dependencies.optional_import_roots.cache_clear()
                else:
                    other = f"rfu5build_other_{calls}"
                    nested.setattr(dependencies, "_optional_root_declarations", lambda *args: ({other}, set()))
                    before = dependencies._ownership_generation
                    dependencies.optional_import_roots("other", "")
                    assert dependencies._ownership_generation > before
        return result

    with monkeypatch.context() as transaction:
        transaction.setattr(dependencies, "_root_observations", interrupt)
        result = _roots(dependencies, SCOPES[1])
        if contention == "sustained":
            assert result == frozenset()
    assert calls == 2
    removed.unlink()
    expected = {OPTIONAL} if contention == "clear-control" else frozenset()
    assert _roots(dependencies, SCOPES[2]) == expected


@pytest.fixture
def _rfu5_tool_ownership(monkeypatch):
    from g6.mcp import dependencies

    _repair3_extra_required_metadata(dependencies, monkeypatch)
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu2build-base"], OPTIONAL: ["rfu2build-extra"]})
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    for root in (REQUIRED, OPTIONAL):
        monkeypatch.delitem(sys.modules, root, raising=False)
    # Inventory also locates shipped skills. Keep the real import lookup for
    # this load/removal test instead of the ownership fixture's global stub.
    dependencies.optional_import_roots.cache_clear()
    yield dependencies
    dependencies.optional_import_roots.cache_clear()
    sys.modules.pop(OPTIONAL, None)


@pytest.mark.parametrize("entry", ("describe", "inventory-control"))
def test_tool_description_records_required_file_before_removal(_rfu5_tool_ownership, tmp_path, monkeypatch, entry):
    from importlib import import_module

    from g6.mcp import families, registrations

    dependencies = _rfu5_tool_ownership
    source = tmp_path / f"{OPTIONAL}.py"
    source.write_text(
        "from types import SimpleNamespace\n"
        "def get_tools():\n"
        "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n",
        encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    monkeypatch.syspath_prepend(str(tmp_path))
    server = ModuleType("rfu5build_server")
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
    if entry == "describe":
        assert families.describe("load_state")["available"] is True
    else:
        rows = families.inventory(family)
        assert next(row for row in rows if row["id"] == "mcp:abstraction/load_state")["available"] is True
    monkeypatch.delitem(sys.modules, OPTIONAL)
    source.unlink()
    invalidate_caches()
    with pytest.raises(ModuleNotFoundError) as stopped:
        families.inventory(family)
    assert stopped.value is failures[-1]


@pytest.mark.parametrize("confirmation", ("secondary", "later-direct-control"))
def test_secondary_confirmation_preserves_direct_replacement_decision(ownership, tmp_path, monkeypatch, confirmation):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    source, retired, replacement = (tmp_path / name for name in ("source.py", "retired.py", "replacement.py"))
    source.write_text("value = 'required'\n", encoding="utf-8")
    spelling = str(source.parent) + os.sep + "." + os.sep + source.name
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = spelling
    loaded.__spec__ = machinery.ModuleSpec(REQUIRED, loader=None, origin=spelling)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    source.rename(retired)
    source.write_text("value = 'replacement'\n", encoding="utf-8")
    stat = dependencies._stat_identity
    direct = 0

    def probe(path):
        nonlocal direct
        assert not dependencies._ownership_lock._is_owned()
        if path == spelling:
            direct += 1
            if direct == 1:
                result = stat(path)
                source.rename(replacement)
                retired.rename(source)
                return result
            if confirmation == "secondary":
                raise OSError("recorded source unreadable")
        return stat(path)

    with monkeypatch.context() as transaction:
        transaction.setattr(dependencies, "_stat_identity", probe)
        _roots(dependencies, SCOPES[2])
    assert direct == 2
    source.rename(retired)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, retired)
    expected = {OPTIONAL} if confirmation == "secondary" else frozenset()
    assert _roots(dependencies, SCOPES[2]) == expected
    specs.clear()
    retired.unlink()
    replacement.unlink()
    assert _roots(dependencies, SCOPES[2]) == expected


@pytest.mark.parametrize("entry", ("failed-load", "cached-loaded", "clear-cache"))
def test_describe_requires_successful_current_recording(_rfu5_tool_ownership, tmp_path, monkeypatch, entry):
    from importlib import import_module
    from packaging.requirements import InvalidRequirement

    from g6.mcp import families, registrations

    dependencies = _rfu5_tool_ownership
    monkeypatch.setattr(families, "_DETAILS", {})
    if entry == "failed-load":
        needs = _repair3_extra_required_metadata(dependencies, monkeypatch)
        valid = needs["g6"]
        needs["g6"] = [">>>broken<<<"]
        monkeypatch.setattr(registrations, "SERVERS", {"g6": "g6.mcp:build_server"})
        for _ in range(2):
            with pytest.raises(InvalidRequirement):
                families.describe("g6_help")
        needs["g6"] = valid
        assert families.describe("g6_help")["available"] is True
        return

    source = tmp_path / f"{OPTIONAL}.py"
    source.write_text(
        "from types import SimpleNamespace\n"
        "GUIDANCE = 'Load state.'\n"
        "def get_tools():\n"
        "    return {'load_state': SimpleNamespace(description=GUIDANCE, parameters={})}\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    server = ModuleType("rfu5repair_server")
    builds = []

    def build():
        builds.append(True)
        return import_module(OPTIONAL)

    server.build = build
    monkeypatch.setitem(sys.modules, server.__name__, server)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": f"{server.__name__}:build"})
    assert families.describe("load_state")["available"] is True
    if entry == "clear-cache":
        dependencies.optional_import_roots.cache_clear()
        monkeypatch.setattr(sys.modules[OPTIONAL], "GUIDANCE", "Reloaded state.")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    reply = families.describe("load_state")
    assert reply["available"] is True
    assert len(builds) == (2 if entry == "clear-cache" else 1)
    assert reply["guidance"] == ("Reloaded state." if entry == "clear-cache" else "Load state.")
    monkeypatch.delitem(sys.modules, REQUIRED)
    monkeypatch.delitem(sys.modules, OPTIONAL)
    source.unlink()
    invalidate_caches()
    assert _roots(dependencies, SCOPES[2]) == frozenset()


def test_startup_records_each_loaded_required_descendant(ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    package = tmp_path / "required"
    package.mkdir()
    parent_file, child_file = package / "__init__.py", package / "child.py"
    parent_file.write_text("value = 'parent'\n", encoding="utf-8")
    child_file.write_text("value = 'child'\n", encoding="utf-8")
    parent = ModuleType(REQUIRED)
    parent.__file__ = str(parent_file)
    parent.__path__ = [str(package)]
    child = ModuleType(REQUIRED + ".child")
    child.__file__ = str(child_file)
    monkeypatch.setitem(sys.modules, REQUIRED, parent)
    monkeypatch.setitem(sys.modules, child.__name__, child)
    dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    monkeypatch.delitem(sys.modules, child.__name__)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, child_file)
    assert _roots(dependencies, SCOPES[2]) == frozenset()
    specs.clear()
    child_file.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset()


def test_standalone_records_required_files_before_run(_rfu5_tool_ownership, tmp_path, monkeypatch):
    from g6.abstraction import server

    dependencies = _rfu5_tool_ownership
    source = tmp_path / "standalone-required.py"
    source.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    monkeypatch.delenv("ABSTRACTION_MCP_STATE", raising=False)
    calls = []

    def run():
        monkeypatch.delitem(sys.modules, REQUIRED)
        # Establish the optional spelling while the loaded file still exists.
        with monkeypatch.context() as probe:
            probe.setattr(dependencies.util, "find_spec",
                          lambda name: util.spec_from_file_location(OPTIONAL, source) if name == OPTIONAL else None)
            assert _roots(dependencies, SCOPES[2]) == frozenset()
        source.unlink()
        calls.append(True)

    monkeypatch.setattr(server.mcp, "run", run)
    server.main()
    assert calls == [True]
    invalidate_caches()
    assert _roots(dependencies, SCOPES[2]) == frozenset()


def test_describe_reuses_recording_until_required_modules_change(ownership, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from g6.mcp import families, registrations

    dependencies, _ = ownership
    source = tmp_path / "describe-required.py"
    source.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    tools = {name: SimpleNamespace(description=name, parameters={})
             for name in ("load_state", "save_state", "describe_domain")}
    server = ModuleType("rfu5am1_server")
    server.get_tools = lambda: tools
    server.mcp = server
    monkeypatch.setitem(sys.modules, server.__name__, server)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": server.__name__ + ":mcp"})
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(families, "_VALIDATED_DETAILS", {})
    copy_locations = dependencies._loaded_locations
    observe_roots = dependencies._root_observations
    walks, discoveries = [], []

    def count_walk(module):
        # This persistent required module is copied once in each full walk.
        assert not dependencies._ownership_lock._is_owned()
        if module is loaded:
            walks.append(True)
        return copy_locations(module)

    def count_discovery(roots):
        assert not dependencies._ownership_lock._is_owned()
        discoveries.append(True)
        return observe_roots(roots)

    monkeypatch.setattr(dependencies, "_loaded_locations", count_walk)
    monkeypatch.setattr(dependencies, "_root_observations", count_discovery)
    for name in tools:
        assert families.describe(name)["available"] is True
    assert len(walks) == 1
    assert len(discoveries) == 1

    child = ModuleType(REQUIRED + ".child")
    child.__file__ = str(source)
    monkeypatch.setitem(sys.modules, child.__name__, child)
    for name in tools:
        assert families.describe(name)["available"] is True
    assert len(walks) == 2
    assert len(discoveries) == 2

    # A changed file value and a replaced object each invalidate the fingerprint.
    (tmp_path / "changed-required.py").write_text("value = 74\n", encoding="utf-8")
    child.__file__ = str(tmp_path / "changed-required.py")
    for name in tools:
        assert families.describe(name)["available"] is True
    assert len(walks) == 3
    assert len(discoveries) == 3
    replacement = ModuleType(child.__name__)
    replacement.__file__ = child.__file__
    monkeypatch.setitem(sys.modules, child.__name__, replacement)
    for name in tools:
        assert families.describe(name)["available"] is True
    assert len(walks) == 4
    assert len(discoveries) == 4

    # An independent ownership transaction invalidates both caches even though
    # no loaded required object has changed. Include that discovery separately.
    generation = dependencies._ownership_generation
    with monkeypatch.context() as transaction:
        transaction.setattr(dependencies, "_optional_root_declarations",
                            lambda *args: ({"rfu5am1_other"}, set()))
        dependencies.optional_import_roots("other", "")
    assert dependencies._ownership_generation > generation
    assert len(discoveries) == 5
    for name in tools:
        assert families.describe(name)["available"] is True
    assert len(walks) == 5
    assert len(discoveries) == 6

    dependencies.optional_import_roots.cache_clear()
    for name in tools:
        assert families.describe(name)["available"] is True
    assert len(walks) == 6
    assert len(discoveries) == 7


@pytest.mark.parametrize("entry", (
    "direct-recording", "direct-optional", "first-load", "listed-reload", "failed-load",
))
def test_only_cached_describe_may_skip_disk_refresh(ownership, tmp_path, monkeypatch, entry):
    from types import SimpleNamespace

    from g6.mcp import families, registrations

    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    source, retired = tmp_path / "source.py", tmp_path / "retired.py"
    source.write_text("value = 'original required'\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    tools = {"load_state": SimpleNamespace(description="Load state.", parameters={})}
    server = ModuleType("rfu5am2_server")
    server.get_tools = lambda: tools
    server.mcp = server
    monkeypatch.setitem(sys.modules, server.__name__, server)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": server.__name__ + ":mcp"})
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(families, "_VALIDATED_DETAILS", {})
    assert families.describe("load_state")["available"] is True
    assert families.describe("load_state")["available"] is True

    # Keep every module name, object and __file__ unchanged. Only disk evidence
    # changes, so an internal recorder shortcut would miss this retirement.
    source.rename(retired)
    source.write_text("value = 'replacement required'\n", encoding="utf-8")
    assert (source.stat().st_dev, source.stat().st_ino) != (retired.stat().st_dev, retired.stat().st_ino)
    if entry == "direct-recording":
        dependencies.record_loaded_required_imports()
    elif entry == "direct-optional":
        key = dependencies._loaded_required_recordings["mcp"]
        dependencies.optional_import_roots("abstraction", _recording_key=key)
    elif entry == "listed-reload":
        families._server_details("abstraction", listed=list(tools.items()))
    else:
        families._VALIDATED_DETAILS.clear()
        if entry == "failed-load":
            def broken():
                raise RuntimeError("reload failed")

            monkeypatch.setattr(server, "get_tools", broken)
            with pytest.raises(RuntimeError, match="reload failed"):
                families.describe("load_state")
            monkeypatch.setattr(server, "get_tools", lambda: tools)
        assert families.describe("load_state")["available"] is True

    # Remove the replacement before discovery so a later stat cannot repair a
    # missed refresh. The old identity must already have lost required ownership.
    monkeypatch.delitem(sys.modules, REQUIRED)
    source.unlink()
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, retired)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    specs.clear()
    retired.unlink()
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}


@pytest.fixture
def _rfu5_r2_describe_server(ownership, monkeypatch):
    from types import SimpleNamespace

    from g6.mcp import families, registrations

    server = ModuleType("rfu5r2_server")
    server.get_tools = lambda: {"load_state": SimpleNamespace(description="Load state.", parameters={})}
    server.mcp = server
    monkeypatch.setitem(sys.modules, server.__name__, server)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": server.__name__ + ":mcp"})
    for attribute in ("_DETAILS", "_VALIDATED_DETAILS", "_RECORDED_SERVERS"):
        monkeypatch.setattr(families, attribute, {})
    return *ownership, families


@pytest.mark.parametrize("identity", ("replacement", "retired", "absent", "unreadable", "unchanged-control"))
def test_cached_describe_rechecks_recorded_required_files(
        _rfu5_r2_describe_server, tmp_path, monkeypatch, identity):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, retired, alias = (tmp_path / name for name in ("required.py", "retired.py", "alias.py"))
    source.write_text("value = 'required'\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    copy_locations, stat = dependencies._loaded_locations, dependencies._stat_identity
    walks = []

    def count_walk(module):
        assert not dependencies._ownership_lock._is_owned()
        if module is loaded:
            walks.append(True)
        return copy_locations(module)

    def unreadable(path):
        assert not dependencies._ownership_lock._is_owned()
        if os.fspath(path) == str(source):
            raise OSError("temporary required identity failure")
        return stat(path)

    monkeypatch.setattr(dependencies, "_loaded_locations", count_walk)
    assert families.describe("load_state")["available"] is True
    candidate = dependencies._loaded_required_recording_key()

    if identity in {"replacement", "retired"}:
        source.rename(retired)
        source.write_text("value = 'replacement'\n", encoding="utf-8")
        assert dependencies._stat_identity(source) != dependencies._stat_identity(retired)
    elif identity == "absent":
        source.rename(retired)
    # The module-only key cannot see disk replacement; the reuse check must.
    assert dependencies._loaded_required_recording_key() == candidate
    with monkeypatch.context() as fault:
        if identity == "unreadable":
            fault.setattr(dependencies, "_stat_identity", unreadable)
        assert families.describe("load_state")["available"] is True
    assert len(walks) == (1 if identity == "unchanged-control" else 2)
    if identity == "absent":
        retired.rename(source)
    if identity in {"absent", "unreadable"}:
        assert families.describe("load_state")["available"] is True
        assert len(walks) == 3  # The incomplete refresh must not validate a reuse.
    monkeypatch.delitem(sys.modules, REQUIRED)
    if identity == "retired":
        source.unlink()  # No subsequent probe can recover a missed retirement.
        alias = retired
    else:
        source.rename(alias)  # Preserve the recorded file's identity at a new spelling.
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
    expected = {OPTIONAL} if identity == "retired" else frozenset()
    assert _roots(dependencies, SCOPES[2]) == expected
    specs.clear()
    alias.unlink()
    assert _roots(dependencies, SCOPES[2]) == expected


@pytest.mark.parametrize("probe", ("unreadable", "absent", "location-failure", "unchanged-control"))
def test_cached_describe_retries_incomplete_required_child(
        _rfu5_r2_describe_server, tmp_path, monkeypatch, probe):
    dependencies, specs, families = _rfu5_r2_describe_server
    parent_file, child_file, alias = (tmp_path / name for name in ("parent.py", "child.py", "alias.py"))
    parent_file.write_text("value = 'parent'\n", encoding="utf-8")
    if probe != "absent":
        child_file.write_text("value = 'child'\n", encoding="utf-8")
    walks, faults = [], []
    location_failure = probe == "location-failure"

    class LoadedChild(ModuleType):
        @property
        def __path__(self):
            if location_failure:
                faults.append("__path__")
                raise OSError("temporary child location failure")
            return None

    parent, child = ModuleType(REQUIRED), LoadedChild(REQUIRED + ".child")
    parent.__file__, child.__file__ = str(parent_file), str(child_file)
    monkeypatch.setitem(sys.modules, REQUIRED, parent)
    monkeypatch.setitem(sys.modules, child.__name__, child)
    stat, copy_locations = dependencies._stat_identity, dependencies._loaded_locations

    def unreadable(path):
        assert not dependencies._ownership_lock._is_owned()
        if os.fspath(path) == str(child_file):
            faults.append(path)
            raise OSError("temporary child identity failure")
        return stat(path)

    def count_walk(module):
        assert not dependencies._ownership_lock._is_owned()
        if module is child:
            walks.append(True)
        return copy_locations(module)

    monkeypatch.setattr(dependencies, "_loaded_locations", count_walk)
    with monkeypatch.context() as fault:
        if probe == "unreadable":
            fault.setattr(dependencies, "_stat_identity", unreadable)
        assert families.describe("load_state")["available"] is True
    assert len(walks) == 1
    if probe in {"unreadable", "location-failure"}:
        assert faults
    location_failure = False
    if probe == "absent":
        child_file.write_text("value = 'child'\n", encoding="utf-8")
    assert families.describe("load_state")["available"] is True
    assert len(walks) == (1 if probe == "unchanged-control" else 2)
    monkeypatch.delitem(sys.modules, REQUIRED)
    monkeypatch.delitem(sys.modules, child.__name__)
    child_file.rename(alias)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
    assert _roots(dependencies, SCOPES[2]) == frozenset()
    specs.clear()
    alias.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset()


@pytest.mark.parametrize("cache_state", ("never-owned", "previously-owned", "unreadable"))
def test_different_tool_describes_reuse_only_owned_required_file_stats(
        ownership, tmp_path, monkeypatch, cache_state):
    from collections import Counter
    from types import SimpleNamespace

    from g6.mcp import families, registrations

    dependencies, _ = ownership
    source, cache_file = tmp_path / "required.py", tmp_path / "required.pyc"
    source.write_text("value = 73\n", encoding="utf-8")
    if cache_state == "previously-owned":
        cache_file.write_bytes(b"previously recorded cache")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    loaded.__cached__ = str(cache_file)
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, source)
    loaded.__spec__.cached = str(cache_file)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    tools = {name: SimpleNamespace(description=name, parameters={})
             for name in ("load_state", "save_state", "describe_domain")}
    server = ModuleType("rfu5r2am2_server")
    server.get_tools = lambda: tools
    server.mcp = server
    monkeypatch.setitem(sys.modules, server.__name__, server)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": server.__name__ + ":mcp"})
    for attribute in ("_DETAILS", "_VALIDATED_DETAILS", "_RECORDED_SERVERS"):
        monkeypatch.setattr(families, attribute, {})
    record, stat = dependencies.record_loaded_required_imports, dependencies._stat_identity
    recordings, stats = [], Counter()

    def count_recording(*args, **kwargs):
        recordings.append(True)
        return record(*args, **kwargs)

    def count_stat(path):
        assert not dependencies._ownership_lock._is_owned()
        stats[os.fspath(path)] += 1
        if cache_state == "unreadable" and os.fspath(path) == str(cache_file):
            raise OSError("cache identity unavailable")
        return stat(path)

    monkeypatch.setattr(dependencies, "record_loaded_required_imports", count_recording)
    monkeypatch.setattr(dependencies, "_stat_identity", count_stat)
    names = list(tools)
    assert families.describe(names[0])["available"] is True
    if cache_state == "never-owned":
        # Absent bytecode hints contribute neither ownership nor live aliases.
        assert all(str(cache_file) not in record[2:4] for record in dependencies._required_paths)
        files = {record[2] for record in dependencies._required_paths}
        assert files and str(source) in files and str(cache_file) not in files
        # The authorized cost bound includes a stat of the absent cache hint.
        files |= {str(cache_file)}
    elif cache_state == "previously-owned":
        assert any(record[2] == str(cache_file) for record in dependencies._required_paths)
        cache_file.unlink()
    for name in names[1:]:
        stats.clear()
        assert families.describe(name)["available"] is True
        if cache_state == "never-owned":
            assert len(recordings) == 1
            assert set(stats) <= files
            assert all(count <= 2 for count in stats.values())
    assert len(recordings) == (1 if cache_state == "never-owned" else len(names))
    if cache_state == "never-owned":
        assert set(dict(families._RECORDED_SERVERS["abstraction"][1][3])) == files


def test_cached_describe_covers_absent_bytecode_destination(
        _rfu5_r2_describe_server, tmp_path, monkeypatch):
    import py_compile

    dependencies, specs, families = _rfu5_r2_describe_server
    source, cache_file = tmp_path / "required.py", tmp_path / "required.pyc"
    source.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, source)
    loaded.__spec__.cached = loaded.__cached__ = str(cache_file)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    assert families.describe("load_state")["available"] is True
    assert not cache_file.exists()
    py_compile.compile(str(source), cfile=str(cache_file), doraise=True)
    machinery.SourcelessFileLoader(REQUIRED, str(cache_file)).exec_module(loaded)
    assert loaded.value == 73
    assert loaded.__file__ == str(source)
    assert families.describe("load_state")["available"] is True
    monkeypatch.delitem(sys.modules, REQUIRED)
    alias = tmp_path / "alias.pyc"
    cache_file.rename(alias)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
    assert _roots(dependencies, SCOPES[2]) == frozenset()
    specs.clear()
    alias.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset()


def test_cached_describe_covers_changed_spec_origin(
        _rfu5_r2_describe_server, tmp_path, monkeypatch):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, origin = tmp_path / "required.py", tmp_path / "new-origin.py"
    source.write_text("value = 73\n", encoding="utf-8")
    origin.write_text("value = 74\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, source)
    loaded.__spec__.cached = None
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    assert families.describe("load_state")["available"] is True
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, origin)
    loaded.__spec__.cached = None
    loaded.__spec__.loader.exec_module(loaded)
    assert loaded.value == 74 and loaded.__file__ == str(source)
    assert families.describe("load_state")["available"] is True
    monkeypatch.delitem(sys.modules, REQUIRED)
    alias = tmp_path / "alias.py"
    origin.rename(alias)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
    assert _roots(dependencies, SCOPES[2]) == frozenset()
    specs.clear()
    alias.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset()


def test_cached_describe_covers_unloaded_required_child_spelling(
        _rfu5_r2_describe_server, tmp_path, monkeypatch):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, retired = tmp_path / "child.py", tmp_path / "retired.py"
    source.write_text("value = 73\n", encoding="utf-8")
    child = ModuleType(REQUIRED + ".child")
    child.__file__ = str(source)
    monkeypatch.setitem(sys.modules, child.__name__, child)
    assert families.describe("load_state")["available"] is True
    monkeypatch.delitem(sys.modules, child.__name__)
    assert families.describe("load_state")["available"] is True
    assert any(record[2] == str(source) for record in dependencies._required_paths)
    source.rename(retired)
    source.write_text("value = 74\n", encoding="utf-8")
    old = retired.stat()
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert source.stat().st_size == old.st_size
    assert source.stat().st_mtime_ns == old.st_mtime_ns
    assert dependencies._stat_identity(source) != dependencies._stat_identity(retired)
    assert families.describe("load_state")["available"] is True
    source.unlink()
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, retired)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    specs.clear()
    retired.unlink()
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}


def test_loaded_cache_only_hint_is_covered_without_owning_optional_file(
        ownership, tmp_path, monkeypatch):
    dependencies, specs = ownership
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    optional = tmp_path / "optional-only.py"
    optional.write_text("value = 'optional only'\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, optional)
    assert _roots(dependencies, SCOPES[2]) == {OPTIONAL}
    loaded = ModuleType(REQUIRED)
    loaded.__spec__ = machinery.ModuleSpec(REQUIRED, loader=None, origin="frozen")
    loaded.__spec__.cached = loaded.__cached__ = str(optional)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)

    key = dependencies.record_loaded_required_imports()
    assert key is not None
    assert dict(key[3])[str(optional)] == dependencies._stat_identity(optional)
    assert not any(record[2] == str(optional) for record in dependencies._required_paths)
    assert dependencies._loaded_required_recording_current(key)

    optional.unlink()
    assert not dependencies._loaded_required_recording_current(key)
    key = dependencies.record_loaded_required_imports()
    assert key is not None and dict(key[3])[str(optional)] is None
    assert dependencies._loaded_required_recording_current(key)
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs.clear()
    assert OPTIONAL in _roots(dependencies, SCOPES[2])


def _rfu6_transfer_required_file(dependencies, specs, source, tmp_path):
    """Exercise alias ownership, then its retained missing-file classification."""
    alias = tmp_path / "transferred.py"
    source.rename(alias)
    specs.clear()
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
    assert _roots(dependencies, SCOPES[2]) == frozenset(), "required file received optional recovery"
    specs.clear()
    alias.unlink()
    assert _roots(dependencies, SCOPES[2]) == frozenset(), "missing required alias received optional recovery"


def test_rejected_describe_reuse_keeps_observed_replacement(
        _rfu5_r2_describe_server, tmp_path, monkeypatch):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, retired, replacement = (tmp_path / name for name in
                                    ("required.py", "retired.py", "replacement.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    assert families.describe("load_state")["available"] is True
    source.rename(retired)
    source.write_text("value = 74\n", encoding="utf-8")
    identity = dependencies._stat_identity(source)
    assert identity != dependencies._stat_identity(retired)
    stat, observed = dependencies._stat_identity, []

    def move_observed_replacement(path):
        assert not dependencies._ownership_lock._is_owned()
        result = stat(path)
        if os.fspath(path) == str(source) and not observed:
            assert result == identity
            observed.append(result)
            source.rename(replacement)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(dependencies, "_stat_identity", move_observed_replacement)
        assert families.describe("load_state")["available"] is True
    assert observed == [identity] and not source.exists()
    monkeypatch.delitem(sys.modules, REQUIRED)
    _rfu6_transfer_required_file(dependencies, specs, replacement, tmp_path)


def test_describe_records_child_loaded_during_validation_stats(
        _rfu5_r2_describe_server, tmp_path, monkeypatch):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, child_file = tmp_path / "required.py", tmp_path / "child.py"
    source.write_text("value = 73\n", encoding="utf-8")
    child_file.write_text("value = 74\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    assert families.describe("load_state")["available"] is True
    stat, children = dependencies._stat_identity, []

    def import_child_after_stat(path):
        assert not dependencies._ownership_lock._is_owned()
        result = stat(path)
        if os.fspath(path) == str(source) and not children:
            spec = util.spec_from_file_location(REQUIRED + ".child", child_file)
            child = util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, child.__name__, child)
            spec.loader.exec_module(child)
            child.__spec__.cached = child.__cached__ = None
            assert child.value == 74
            children.append(child)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(dependencies, "_stat_identity", import_child_after_stat)
        assert families.describe("load_state")["available"] is True
    assert len(children) == 1
    monkeypatch.delitem(sys.modules, children[0].__name__)
    monkeypatch.delitem(sys.modules, REQUIRED)
    _rfu6_transfer_required_file(dependencies, specs, child_file, tmp_path)


def test_describe_records_replacement_after_validation_stat(
        _rfu5_r2_describe_server, tmp_path, monkeypatch):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, retired = tmp_path / "required.py", tmp_path / "retired.py"
    source.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    assert families.describe("load_state")["available"] is True
    stat, replaced = dependencies._stat_identity, []

    def replace_after_stat(path):
        assert not dependencies._ownership_lock._is_owned()
        result = stat(path)
        if os.fspath(path) == str(source) and not replaced:
            source.rename(retired)
            source.write_text("value = 74\n", encoding="utf-8")
            old = retired.stat()
            os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
            assert source.stat().st_size == old.st_size
            assert source.stat().st_mtime_ns == old.st_mtime_ns
            assert stat(source) != result
            replaced.append(True)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(dependencies, "_stat_identity", replace_after_stat)
        assert families.describe("load_state")["available"] is True
    assert replaced == [True]
    monkeypatch.delitem(sys.modules, REQUIRED)
    _rfu6_transfer_required_file(dependencies, specs, source, tmp_path)


@pytest.mark.parametrize("hint", ("root", "child", "clear-root"))
def test_rejected_reuse_preserves_discovery_cache_evidence_without_promoting_child_hint(
        _rfu5_r2_describe_server, tmp_path, monkeypatch, hint):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, cache_file, saved = (tmp_path / name for name in ("required.py", "required.pyc", "saved.pyc"))
    source.write_text("value = 73\n", encoding="utf-8")
    name = REQUIRED + ".child" if hint == "child" else REQUIRED
    loaded = ModuleType(name)
    loaded.__file__ = str(source)
    loaded.__spec__ = util.spec_from_file_location(name, source)
    loaded.__spec__.cached = loaded.__cached__ = str(cache_file)
    monkeypatch.setitem(sys.modules, name, loaded)
    assert families.describe("load_state")["available"] is True
    assert not cache_file.exists()
    cache_file.write_bytes(b"observed cache identity")
    stat, observed = dependencies._stat_identity, []

    def move_observed_cache(path):
        assert not dependencies._ownership_lock._is_owned()
        result = stat(path)
        if os.fspath(path) == str(cache_file) and not observed:
            observed.append(result)
            cache_file.rename(saved)
            if hint == "clear-root":
                dependencies.optional_import_roots.cache_clear()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(dependencies, "_stat_identity", move_observed_cache)
        assert families.describe("load_state")["available"] is True
    assert len(observed) == 1 and not cache_file.exists()
    monkeypatch.delitem(sys.modules, name)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, saved)
    expected = frozenset() if hint == "root" else {OPTIONAL}
    assert _roots(dependencies, SCOPES[2]) == expected, "reuse evidence changed cache ownership eligibility"
    specs.clear()
    saved.unlink()
    assert _roots(dependencies, SCOPES[2]) == expected


@pytest.mark.parametrize("interruption", ("recording-attributes", "recording-entry", "discovery-metadata"))
@pytest.mark.parametrize("failure", (SystemExit, KeyboardInterrupt, BaseException))
def test_rejected_reuse_evidence_survives_callback_interruption(
        _rfu5_r2_describe_server, tmp_path, monkeypatch, interruption, failure):
    dependencies, specs, families = _rfu5_r2_describe_server
    source, cache_file, saved = (tmp_path / name for name in ("required.py", "hint.pyc", "saved.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    loaded = ModuleType(REQUIRED)
    loaded.__file__ = str(source)
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, source)
    loaded.__spec__.cached = loaded.__cached__ = str(cache_file)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    assert families.describe("load_state")["available"] is True
    if interruption == "discovery-metadata":
        # Only root discovery owns this cache hint; startup merely covers it.
        target = cache_file
        target.write_bytes(b"required root cache")
    else:
        target = source
        source.rename(tmp_path / "retired.py")
        source.write_text("value = 74\n", encoding="utf-8")
    identity = dependencies._stat_identity(target)
    stat, observed = dependencies._stat_identity, []
    declarations = dependencies._optional_root_declarations

    def interrupt(*args, **kwargs):
        assert not dependencies._ownership_lock._is_owned()
        raise failure("interrupted rejected reuse handoff")

    def attribute(name):
        if name == "__path__":
            interrupt()
        raise AttributeError(name)

    def move_observed_identity(path):
        result = stat(path)
        if os.fspath(path) == str(target) and not observed:
            observed.append(result)
            target.rename(saved)
            if interruption == "recording-attributes":
                loaded.__getattr__ = attribute
        return result

    def interrupt_discovery(extras, *args):
        if extras and observed:
            interrupt()
        return declarations(extras, *args)

    with monkeypatch.context() as patch:
        patch.setattr(dependencies, "_stat_identity", move_observed_identity)
        if interruption == "recording-entry":
            patch.setattr(dependencies, "record_loaded_required_imports", interrupt)
        elif interruption == "discovery-metadata":
            patch.setattr(dependencies, "_optional_root_declarations", interrupt_discovery)
        with pytest.raises(failure, match="interrupted rejected reuse handoff"):
            families.describe("load_state")
    assert observed == [identity]
    if interruption == "recording-attributes":
        del loaded.__getattr__
    assert families.describe("load_state")["available"] is True
    monkeypatch.delitem(sys.modules, REQUIRED)
    _rfu6_transfer_required_file(dependencies, specs, saved, tmp_path)


@pytest.mark.parametrize("entry", ("discovery", "failed-describe", "failed-inventory"))
@pytest.mark.parametrize("hint", ("file", "origin", "namespace", "root-cache", "child-cache"))
def test_pending_required_evidence_precedes_failed_load_classification(
        tmp_path, monkeypatch, request, entry, hint):
    from g6.mcp import registrations

    # Capture the real finder before ownership replaces importlib.util.find_spec
    # globally; inventory must still discover the installed vendored skills.
    real_find_spec = util.find_spec
    dependencies, specs, families = request.getfixturevalue("_rfu5_r2_describe_server")

    def find_spec(name):
        if name.split(".", 1)[0] in (REQUIRED, OPTIONAL):
            return specs.get(name)
        return real_find_spec(name)

    monkeypatch.setattr(dependencies.util, "find_spec", find_spec)
    assert dependencies.util.find_spec("g6.skills") is not None
    _repair3_extra_required_metadata(dependencies, monkeypatch)
    source, saved = tmp_path / "observed.py", tmp_path / "saved.py"
    namespace = hint == "namespace"

    def create():
        if namespace:
            source.mkdir()
        else:
            source.write_text("value = 74\n", encoding="utf-8")

    name = REQUIRED if hint == "root-cache" else REQUIRED + ".pending"
    loaded = ModuleType(name)
    loaded.__spec__ = machinery.ModuleSpec(name, loader=None, origin="frozen")
    if hint == "file":
        loaded.__file__ = str(source)
    elif hint == "origin":
        loaded.__spec__.origin = str(source)
    elif namespace:
        loaded.__path__ = [str(source)]
        loaded.__spec__.submodule_search_locations = [str(source)]
    else:
        loaded.__spec__.cached = loaded.__cached__ = str(source)
    monkeypatch.setitem(sys.modules, name, loaded)
    if "cache" not in hint:
        create()
    assert families.describe("load_state")["available"] is True
    if source.exists():
        source.rename(tmp_path / "retired")
    create()
    stat, observed = dependencies._stat_identity, []
    guard = ModuleType(REQUIRED + ".guard")

    def interrupt(attribute):
        if attribute == "__path__":
            raise SystemExit("pending recording interrupted")
        raise AttributeError(attribute)

    guard.__getattr__ = interrupt

    def move_after_probe(path):
        assert not dependencies._ownership_lock._is_owned()
        result = stat(path)
        if os.fspath(path) == str(source) and not observed:
            observed.append(result)
            source.rename(saved)
            monkeypatch.setitem(sys.modules, guard.__name__, guard)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(dependencies, "_stat_identity", move_after_probe)
        with pytest.raises(SystemExit, match="pending recording interrupted"):
            families.describe("load_state")
    assert observed == [stat(saved)]
    assert dependencies._pending_reuse_evidence
    monkeypatch.delitem(sys.modules, guard.__name__)
    monkeypatch.delitem(sys.modules, name)
    if namespace:
        spec = machinery.ModuleSpec(OPTIONAL, loader=None, is_package=True)
        spec.submodule_search_locations = [str(saved)]
    else:
        spec = util.spec_from_file_location(OPTIONAL, saved)
    specs[OPTIONAL] = spec
    failure = ModuleNotFoundError("failed before recording", name=OPTIONAL)

    def failed_load(target):
        assert not dependencies._ownership_lock._is_owned()
        raise failure

    monkeypatch.setattr(registrations, "_load", failed_load)
    family = families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})

    def classify():
        if entry == "discovery":
            # Ownership is process-wide even when this scope has no required extras.
            return OPTIONAL in _roots(dependencies, SCOPES[2])
        try:
            result = (families.describe("load_state") if entry == "failed-describe"
                      else families.inventory(family))
        except ModuleNotFoundError as exc:
            assert exc is failure
            return False
        if entry == "failed-describe":
            assert result["available"] is False and "install" in result
        else:
            row = next(row for row in result if row["id"] == "mcp:abstraction/load_state")
            assert row["available"] is False and "install" in row
        return True

    if entry == "discovery":
        assert classify() is (hint == "child-cache"), "pending required evidence bypassed discovery"
    else:
        # The alias exists, so even an optional installed-module failure is a defect.
        assert classify() is False
    specs.clear()
    saved.rmdir() if namespace else saved.unlink()
    assert classify() is (hint == "child-cache"), "pending required evidence received optional recovery"


@pytest.mark.parametrize("exotic", (False, True), ids=("plain-control", "string-subclass"))
def test_exception_name_hooks_never_hold_ownership_lock(ownership, exotic):
    dependencies, _ = ownership

    class MissingName(str):
        def __hash__(self):
            assert not dependencies._ownership_lock._is_owned(), "exception-name hash executed under ownership lock"
            return str.__hash__(self)

        def __eq__(self, other):
            assert not dependencies._ownership_lock._is_owned(), "exception-name equality executed under ownership lock"
            return str.__eq__(self, other)

        def split(self, sep=None, maxsplit=-1):
            assert not dependencies._ownership_lock._is_owned(), "exception-name split executed under ownership lock"
            return [self]

        def __str__(self):
            raise AssertionError("exception-name copying must bypass foreign __str__")

    name = MissingName(OPTIONAL) if exotic else OPTIONAL
    failure = ModuleNotFoundError("absent optional dependency", name=name)
    assert dependencies.optional_dependency_missing("abstraction", failure)
    assert all(type(root) is str for root in dependencies.absent_module_roots(failure))


@pytest.mark.parametrize("exotic", (False, True), ids=("plain-control", "string-subclass"))
def test_exception_names_copied_before_dependency_operations(ownership, exotic):
    dependencies, _ = ownership

    class MissingName(str):
        def forbidden(self, *args, **kwargs):
            raise AssertionError("exception-name operation must use an exact string copy")

        __hash__ = __eq__ = __str__ = split = forbidden

    name = MissingName(OPTIONAL) if exotic else OPTIONAL
    failure = ModuleNotFoundError("absent optional dependency", name=name)
    diagnostic = dependencies.missing_module(failure)
    assert type(diagnostic) is str
    assert diagnostic == OPTIONAL
    assert dependencies.optional_dependency_missing("abstraction", failure)
    assert dependencies.absent_module_roots(failure) == {OPTIONAL}


@pytest.mark.parametrize("name", (None, 7, b"rfu2build_optional", [OPTIONAL]),
                         ids=("unnamed", "integer", "bytes", "unhashable"))
def test_invalid_exception_names_fail_closed(ownership, name):
    dependencies, _ = ownership
    failure = ModuleNotFoundError("invalid missing name", name=name)
    assert dependencies.missing_module(failure) is None
    assert not dependencies.optional_dependency_missing("abstraction", failure)
    assert dependencies.absent_module_roots(failure) == frozenset()
