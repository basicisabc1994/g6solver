"""Offline disk-backed review 4; run with --confcutdir=tests/_review_rfu5_4."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest


REQUIRED = "rfu54_required"
OPTIONAL = "rfu54_optional"
TOOLS = (
    "from types import SimpleNamespace\n"
    "def get_tools():\n"
    "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n"
)


@pytest.fixture
def env(monkeypatch):
    disk = Path(__file__).resolve().parents[2] / ".bt_rev_rfu5_4" / "cases" / uuid4().hex
    disk.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull, "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies as deps, families, registrations

    needs = {"g6": ['rfu54-base; extra == "mcp"', 'rfu54-extra; extra == "abstraction"'],
             "rfu54-base": [], "rfu54-extra": []}
    roots = {"g6": "g6", "rfu54-base": REQUIRED, "rfu54-extra": OPTIONAL}
    for name, requirements in needs.items():
        folder = disk / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir()
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements), encoding="utf-8")
        (folder / "top_level.txt").write_text(roots[name] + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(disk)])}

    def distributions(**kwargs):
        assert not deps._ownership_lock._is_owned()
        return scan(path=[str(disk)])

    def requires(name):
        assert not deps._ownership_lock._is_owned()
        return records[name].requires

    stat = deps._stat_identity

    def stat_identity(path):
        assert not deps._ownership_lock._is_owned()
        return stat(path)

    monkeypatch.setattr(metadata, "distributions", distributions)
    monkeypatch.setattr(metadata, "requires", requires)
    monkeypatch.setattr(deps, "_stat_identity", stat_identity)
    monkeypatch.syspath_prepend(str(disk))
    for attr in ("_DETAILS", "_VALIDATED_DETAILS", "_RECORDED_SERVERS"):
        monkeypatch.setattr(families, attr, {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu54_server:build"})
    (disk / "rfu54_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu54_optional')\n"
        "    except ModuleNotFoundError as exc:\n        FAILURE = exc\n        raise\n",
        encoding="utf-8")
    bindings = {}

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            assert not deps._ownership_lock._is_owned()
            file = bindings.get(fullname)
            if file is not None and file.exists():
                return util.spec_from_file_location(fullname, file)
            return None

    monkeypatch.setattr(sys, "meta_path", [Finder(), *sys.meta_path])
    deps.optional_import_roots.cache_clear()
    invalidate_caches()
    yield SimpleNamespace(deps=deps, families=families, disk=disk, bindings=bindings)
    deps.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu54_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def start(env, source=None):
    from g6 import mcp

    source = source or env.disk / "required.py"
    source.write_text(TOOLS, encoding="utf-8")
    env.bindings[REQUIRED] = source
    loaded = import_module(REQUIRED)
    loaded.__spec__.cached = loaded.__cached__ = None
    assert mcp.build_server().name == "g6"
    optional = env.disk / "optional.py"
    optional.write_text(TOOLS, encoding="utf-8")
    env.bindings[OPTIONAL] = optional
    assert env.families.describe("load_state")["available"] is True
    assert "abstraction" in env.families._RECORDED_SERVERS
    return loaded


def discover(env):
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    return env.families.inventory(family)


def transfer_and_check(env, source):
    for name in tuple(sys.modules):
        if name in {REQUIRED, OPTIONAL} or name.startswith(REQUIRED + "."):
            sys.modules.pop(name)
    alias = env.disk / "alias.py"
    source.rename(alias)
    env.bindings.clear()
    env.bindings[OPTIONAL] = alias
    assert discover(env)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    alias.unlink()
    (env.disk / "optional.py").unlink()
    invalidate_caches()
    try:
        rows = discover(env)
    except ModuleNotFoundError as exc:
        assert exc is import_module("rfu54_server").FAILURE
        return "required"
    row = next(row for row in rows if row["id"] == "mcp:abstraction/load_state")
    assert not row["available"] and "g6[abstraction,mcp]" in row["install"]
    return "optional-install"


@pytest.mark.parametrize("timing", ["during-probes", "before-control"])
def test_child_import_during_reuse_validation(env, monkeypatch, timing):
    loaded = start(env)
    source = env.disk / "child.py"
    source.write_text(TOOLS, encoding="utf-8")
    fired = []

    def load_child():
        name = REQUIRED + ".child"
        spec = util.spec_from_file_location(name, source)
        child = util.module_from_spec(spec)
        sys.modules[name] = child
        spec.loader.exec_module(child)
        child.__spec__.cached = child.__cached__ = None
        assert child.get_tools.__code__.co_filename == str(source)
        fired.append(True)

    if timing == "before-control":
        load_child()
    stat = env.deps._stat_identity

    def intervening_stat(path):
        result = stat(path)
        if str(path) == loaded.__file__ and not fired:
            load_child()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intervening_stat)
        assert env.families.describe("load_state")["available"] is True
    assert fired
    assert transfer_and_check(env, source) == "required", "executed required child received optional-install"


@pytest.mark.parametrize("timing", ["after-stat", "before-control"])
def test_replacement_between_stat_and_reuse(env, monkeypatch, timing):
    loaded = start(env)
    source = Path(loaded.__file__)
    old = env.disk / "retired.py"
    fired = []

    def replace():
        source.rename(old)
        source.write_text(TOOLS, encoding="utf-8")
        status = old.stat()
        os.utime(source, ns=(status.st_atime_ns, status.st_mtime_ns))
        assert source.stat().st_size == old.stat().st_size
        assert source.stat().st_mtime_ns == old.stat().st_mtime_ns
        assert env.deps._stat_identity(source) != env.deps._stat_identity(old)
        fired.append(True)

    if timing == "before-control":
        replace()
    stat = env.deps._stat_identity

    def intervening_stat(path):
        result = stat(path)
        if str(path) == str(source) and not fired:
            fired.append(False)  # Prevent recursion through replacement's checks.
            replace()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intervening_stat)
        assert env.families.describe("load_state")["available"] is True
    assert fired
    assert transfer_and_check(env, source) == "required", "required replacement received optional-install"


@pytest.mark.parametrize("kind", ["namespace", "frozen-file", "cache-hint", "zip"])
def test_non_racing_coverage_controls(env, monkeypatch, kind):
    loaded = start(env)
    name = REQUIRED + ".edge"
    if kind == "zip":
        archive = env.disk / "modules.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("edge.py", TOOLS)
        import zipimport
        child = zipimport.zipimporter(str(archive)).load_module("edge")
        monkeypatch.setitem(sys.modules, name, child)
        monkeypatch.delitem(sys.modules, "edge")
        discover(env)
        assert "abstraction" not in env.families._RECORDED_SERVERS
        return
    path = env.disk / "edge.py"
    if kind == "namespace":
        path = env.disk / "portion"
        path.mkdir()
        spec = util.spec_from_loader(name, loader=None, is_package=True)
        spec.submodule_search_locations = [str(path)]
        child = util.module_from_spec(spec)
        child.__path__ = [str(path)]
    else:
        path.write_text(TOOLS, encoding="utf-8")
        spec = util.spec_from_file_location(name, path)
        child = util.module_from_spec(spec)
        spec.loader.exec_module(child)
        child.__spec__.cached = child.__cached__ = None
        if kind == "frozen-file":
            child.__spec__.origin = "frozen"
        else:
            # A descendant's cache is covered, but startup never owns it.
            monkeypatch.setattr(loaded, "__cached__", str(path))
            env.deps.record_loaded_required_imports()
            key = env.deps._loaded_required_recordings["mcp"]
            assert dict(key[3])[str(path)] == env.deps._stat_identity(path)
            assert not any(record[2] == str(path) for record in env.deps._required_paths)
            return
    monkeypatch.setitem(sys.modules, name, child)
    discover(env)
    key = env.families._RECORDED_SERVERS["abstraction"][1]
    assert dict(key[3])[str(path)] == env.deps._stat_identity(path)
    assert any(record[2] == str(path) for record in env.deps._required_paths)
    retired = path.with_name("retired-edge")
    path.rename(retired)
    if kind == "namespace":
        path.mkdir()
    else:
        path.write_text(TOOLS, encoding="utf-8")
    assert not env.deps._loaded_required_recording_current(key)


@pytest.mark.parametrize("entry", ["reuse-probe", "full-refresh-control"])
def test_rejected_reuse_discards_direct_replacement_evidence(env, monkeypatch, entry):
    loaded = start(env)
    source = Path(loaded.__file__)
    old = env.disk / "retired.py"
    replacement = env.disk / "replacement.py"
    source.rename(old)
    source.write_text(TOOLS, encoding="utf-8")
    identity = env.deps._stat_identity(source)
    assert identity != env.deps._stat_identity(old)
    stat = env.deps._stat_identity
    observed = []

    def intervening_stat(path):
        result = stat(path)
        if str(path) == str(source) and not observed:
            assert result == identity
            observed.append(result)
            source.rename(replacement)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intervening_stat)
        if entry == "reuse-probe":
            assert env.families.describe("load_state")["available"] is True
        else:
            assert discover(env)
    assert observed == [identity]
    assert not source.exists()
    assert "abstraction" not in env.families._RECORDED_SERVERS
    assert transfer_and_check(env, replacement) == "required", "observed required replacement received optional-install"


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
@pytest.mark.parametrize("same_file", [False, True], ids=["replacement", "hardlink"])
def test_junction_retarget_controls(env, same_file):
    import _winapi

    first, second, link = (env.disk / name for name in ("first", "second", "site"))
    first.mkdir()
    second.mkdir()
    _winapi.CreateJunction(str(first), str(link))
    try:
        start(env, link / "required.py")
        key = env.families._RECORDED_SERVERS["abstraction"][1]
        if same_file:
            os.link(first / "required.py", second / "required.py")
        else:
            (second / "required.py").write_text(TOOLS, encoding="utf-8")
        os.rmdir(link)
        _winapi.CreateJunction(str(second), str(link))
        assert env.deps._loaded_required_recording_current(key) is same_file
    finally:
        if os.path.lexists(link):
            os.rmdir(link)
