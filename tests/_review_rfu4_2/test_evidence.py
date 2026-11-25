"""Offline real-disk ownership review; no writes outside this review folder."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu42_required"
OPTIONAL = "rfu42_optional"


@pytest.fixture
def tmp_path():
    # Pytest's mode-0700 basetemp is inaccessible in this Windows sandbox.
    disk = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    disk.mkdir(parents=True)
    return disk


@pytest.fixture
def installation(tmp_path, monkeypatch):
    for key, value in {
        "G6_HOME": str(tmp_path / "home"), "FASTMCP_HOME": str(tmp_path / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull, "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies, families, registrations

    needs = {"g6": ['rfu42-base; extra == "mcp"',
                    'rfu42-extra; extra == "abstraction"'],
             "rfu42-base": [], "rfu42-extra": []}
    roots = {"g6": "g6", "rfu42-base": REQUIRED, "rfu42-extra": OPTIONAL}
    for name, requirements in needs.items():
        folder = tmp_path / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir()
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements), encoding="utf-8")
        (folder / "top_level.txt").write_text(roots[name] + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(tmp_path)])}
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(tmp_path)]))
    monkeypatch.setattr(metadata, "requires", lambda name: records[name].requires)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu42_server:build"})
    (tmp_path / "rfu42_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu42_optional')\n"
        "    except ModuleNotFoundError as exc:\n        FAILURE = exc\n        raise\n",
        encoding="utf-8")
    bindings = {}

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            file = bindings.get(fullname)
            if file is not None and file.exists():
                return util.spec_from_file_location(fullname, file)
            return None

    monkeypatch.setattr(sys, "meta_path", [Finder(), *sys.meta_path])
    dependencies.optional_import_roots.cache_clear()
    invalidate_caches()
    yield SimpleNamespace(deps=dependencies, families=families, disk=tmp_path, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu42_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def start(env, source):
    from g6 import mcp

    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__spec__ = None  # Supported file-only loaded module; one ownership record.
    module.__cached__ = None
    assert mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    env.bindings.clear()


def verdict(env, required):
    server = import_module("rfu42_server")
    try:
        reply = env.families.describe("load_state")
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
        assert required, "retired required identity still blocks optional recovery"
    else:
        assert not required, f"required defect became an install hint: {reply}"
        assert reply["available"] is False
        assert reply["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in reply["install"]


def layout(env):
    import _winapi

    first, second = env.disk / "first", env.disk / "second"
    first.mkdir()
    second.mkdir()
    original, alias = first / "shared.py", second / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, alias)
    junction = env.disk / "site"
    _winapi.CreateJunction(str(first), str(junction))
    return first, second, original, alias, junction, junction / "shared.py"


@pytest.mark.parametrize("retarget", [True, False], ids=["moved-record", "replacement-control"])
def test_old_resolved_spelling_cannot_override_current_source(installation, retarget):
    import _winapi

    env = installation
    first, second, original, alias, junction, source = layout(env)
    try:
        start(env, source)
        if retarget:
            os.rmdir(junction)
            _winapi.CreateJunction(str(second), str(junction))
        original.rename(first / "retired.py")
        original.write_text("value = 'independent replacement'\n", encoding="utf-8")
        assert os.path.samefile(source, alias) is retarget
        # Refresh now sees source -> second/A, then discovery sees first/B.
        env.bindings[OPTIONAL] = original
        assert env.deps.optional_import_roots("abstraction") == {OPTIONAL}
        # Transfer the surviving A to the optional root, then remove all imports.
        env.bindings[OPTIONAL] = alias
        env.deps.optional_import_roots("abstraction")
        env.bindings.clear()
        os.rmdir(junction)
        original.unlink()
        alias.unlink()
        invalidate_caches()
        verdict(env, required=retarget)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("aba", [True, False], ids=["resolution-ABA", "replacement-control"])
def test_double_resolution_does_not_authorize_unrelated_stat(installation, monkeypatch, aba):
    import _winapi

    env = installation
    first, second, original, alias, junction, source = layout(env)
    try:
        start(env, source)
        stat = env.deps._stat_identity
        raced = False

        def race(path):
            nonlocal raced
            assert not env.deps._ownership_lock._is_owned()
            if path == str(source):
                raise OSError("primary identity probe unavailable")
            if path == str(original) and not raced:
                raced = True
                if aba:
                    os.rmdir(junction)
                    _winapi.CreateJunction(str(second), str(junction))
                retired = first / "retired.py"
                original.rename(retired)
                original.write_text("value = 'independent replacement'\n", encoding="utf-8")
                result = stat(path)  # Actual different file on disk.
                if aba:
                    assert os.path.samefile(source, alias)
                    original.unlink()
                    retired.rename(original)
                    os.rmdir(junction)
                    _winapi.CreateJunction(str(first), str(junction))
                    assert os.path.samefile(source, alias)
                return result
            return stat(path)

        with monkeypatch.context() as probing:
            probing.setattr(env.deps, "_stat_identity", race)
            env.deps.optional_import_roots("abstraction")
        assert raced
        env.bindings[OPTIONAL] = alias
        env.deps.optional_import_roots("abstraction")
        env.bindings.clear()
        os.rmdir(junction)
        original.unlink()
        alias.unlink()
        invalidate_caches()
        verdict(env, required=aba)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("remove", [True, False], ids=["restored-then-absent", "live-retry-control"])
def test_newer_success_supersedes_committed_retirement(installation, monkeypatch, remove):
    env = installation
    source, retired = env.disk / "required.py", env.disk / "retired.py"
    source.write_text("value = 73\n", encoding="utf-8")
    start(env, source)
    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__spec__ = None
    module.__cached__ = None
    refresh = env.deps._path_refresh_observations
    calls = 0

    def interrupt(paths):
        nonlocal calls
        result = refresh(paths)
        calls += 1
        if calls == 1:
            with monkeypatch.context() as nested:
                nested.setattr(env.deps, "_path_refresh_observations", refresh)
                sys.modules.pop(REQUIRED)
                env.bindings.clear()
                source.rename(retired)
                source.write_text("value = 'replacement'\n", encoding="utf-8")
                assert env.deps.optional_import_roots("abstraction", "") == {OPTIONAL}
                source.unlink()
                retired.rename(source)
                env.bindings[OPTIONAL] = source
                # Later successful stat of A contradicts committed retirement B.
                assert env.deps.optional_import_roots("abstraction", "") == {OPTIONAL}
                if remove:
                    source.unlink()
        return result

    with monkeypatch.context() as recording:
        recording.setattr(env.deps, "_path_refresh_observations", interrupt)
        env.deps.record_loaded_required_imports()
    assert calls == 2
    env.bindings.clear()
    if source.exists():
        source.unlink()
    invalidate_caches()
    verdict(env, required=True)


@pytest.mark.parametrize("overlap", [True, False], ids=["first-startup-retry", "serial-control"])
def test_first_startup_keeps_later_successful_replacement_evidence(installation, monkeypatch, overlap):
    from g6 import mcp

    env = installation
    source, retired = env.disk / "required.py", env.disk / "retired.py"
    source.write_text("value = 73\n", encoding="utf-8")
    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__spec__ = None
    module.__cached__ = None
    refresh = env.deps._path_refresh_observations
    calls = 0

    def replace_and_observe():
        sys.modules.pop(REQUIRED)
        env.bindings.clear()
        source.rename(retired)
        source.write_text("value = 'replacement'\n", encoding="utf-8")
        env.bindings[OPTIONAL] = source
        with monkeypatch.context() as nested:
            nested.setattr(env.deps, "_path_refresh_observations", refresh)
            assert env.deps.optional_import_roots("abstraction", "") == {OPTIONAL}
        source.unlink()

    def interrupt(paths):
        nonlocal calls
        result = refresh(paths)
        calls += 1
        if overlap and calls == 1:
            replace_and_observe()
        return result

    with monkeypatch.context() as recording:
        recording.setattr(env.deps, "_path_refresh_observations", interrupt)
        assert mcp.build_server().name == "g6"
    assert calls == (2 if overlap else 1)
    if not overlap:
        replace_and_observe()
    env.bindings[OPTIONAL] = retired
    env.deps.optional_import_roots("abstraction")
    env.bindings.clear()
    retired.unlink()
    invalidate_caches()
    verdict(env, required=False)


@pytest.mark.parametrize("partial", [True, False], ids=["partial-import", "healthy-inventory"])
def test_inventory_retains_required_alias_after_import_exit(installation, partial):
    env = installation
    source, alias = env.disk / "required.py", env.disk / "alias.py"
    source.write_text(
        "from types import SimpleNamespace\n"
        "from g6.mcp.dependencies import _ownership_lock\n"
        "assert not _ownership_lock._is_owned()\n"
        f"if __name__ == '{OPTIONAL}' and {partial!r}:\n"
        "    raise ModuleNotFoundError('independent defect', name='rfu42_unknown')\n"
        "def get_tools():\n"
        "    assert not _ownership_lock._is_owned()\n"
        "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n",
        encoding="utf-8")
    os.link(source, alias)
    start(env, source)
    env.bindings[OPTIONAL] = alias
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    if partial:
        with pytest.raises(ModuleNotFoundError) as stopped:
            env.families.inventory(family)
        assert stopped.value is import_module("rfu42_server").FAILURE
        assert stopped.value.name == "rfu42_unknown"
    else:
        rows = env.families.inventory(family)
        assert next(row for row in rows if row["id"] == "mcp:abstraction/load_state")["available"] is True
    sys.modules.pop(OPTIONAL, None)
    env.bindings.clear()
    source.unlink()
    alias.unlink()
    invalidate_caches()
    with pytest.raises(ModuleNotFoundError) as stopped:
        env.families.inventory(family)
    assert stopped.value is import_module("rfu42_server").FAILURE
    assert stopped.value.name == OPTIONAL


@pytest.mark.parametrize("mode", ["before-error", "after-error", "link-chain"])
def test_secondary_resolution_uncertainty_preserves_owner(installation, monkeypatch, mode):
    import _winapi

    env = installation
    first, second, original, alias, junction, source = layout(env)
    via = env.disk / "via"
    try:
        if mode == "link-chain":
            _winapi.CreateJunction(str(first), str(via))
            os.rmdir(junction)
            _winapi.CreateJunction(str(via), str(junction))
        start(env, source)
        stat, resolve = env.deps._stat_identity, env.deps._existing_realpath
        calls = 0

        def failed_primary(path):
            assert not env.deps._ownership_lock._is_owned()
            if path == str(source):
                raise OSError("primary stat unavailable")
            return stat(path)

        def resolution(path):
            nonlocal calls
            assert not env.deps._ownership_lock._is_owned()
            if path == str(source):
                calls += 1
                if (mode, calls) in {("before-error", 1), ("after-error", 2)}:
                    raise OSError("resolution unavailable")
            return resolve(path)

        with monkeypatch.context() as probing:
            probing.setattr(env.deps, "_stat_identity", failed_primary)
            probing.setattr(env.deps, "_existing_realpath", resolution)
            env.deps.optional_import_roots("abstraction")
        assert calls == (1 if mode == "before-error" else 2)
        env.bindings[OPTIONAL] = alias
        env.deps.optional_import_roots("abstraction")
        env.bindings.clear()
        os.rmdir(junction)
        original.unlink()
        alias.unlink()
        invalidate_caches()
        verdict(env, required=True)
    finally:
        for link in (junction, via):
            if os.path.lexists(link):
                os.rmdir(link)
