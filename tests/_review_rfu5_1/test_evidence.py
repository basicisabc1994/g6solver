"""Real-disk, offline ownership review. No production files are changed."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu51_required"
OPTIONAL = "rfu51_optional"
OTHER = "rfu51_other"


@pytest.fixture
def env(monkeypatch):
    disk = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    disk.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull, "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies, families, registrations

    needs = {"g6": ['rfu51-base; extra == "mcp"',
                    'rfu51-extra; extra == "abstraction"',
                    'rfu51-other; extra == "other"'],
             "rfu51-base": [], "rfu51-extra": [], "rfu51-other": []}
    roots = {"g6": "g6", "rfu51-base": REQUIRED,
             "rfu51-extra": OPTIONAL, "rfu51-other": OTHER}
    for name, requirements in needs.items():
        folder = disk / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir()
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements), encoding="utf-8")
        (folder / "top_level.txt").write_text(roots[name] + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(disk)])}

    def distributions(**kw):
        assert not dependencies._ownership_lock._is_owned()
        return scan(path=[str(disk)])

    def requires(name):
        assert not dependencies._ownership_lock._is_owned()
        return records[name].requires

    stat = dependencies._stat_identity

    def stat_identity(path):
        assert not dependencies._ownership_lock._is_owned()
        return stat(path)

    monkeypatch.setattr(metadata, "distributions", distributions)
    monkeypatch.setattr(metadata, "requires", requires)
    monkeypatch.setattr(dependencies, "_stat_identity", stat_identity)
    monkeypatch.syspath_prepend(str(disk))
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu51_server:build"})
    (disk / "rfu51_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu51_optional')\n"
        "    except ModuleNotFoundError as exc:\n        FAILURE = exc\n        raise\n",
        encoding="utf-8")
    bindings = {}

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            assert not dependencies._ownership_lock._is_owned()
            file = bindings.get(fullname)
            if file is not None and file.exists():
                return util.spec_from_file_location(fullname, file)
            return None

    monkeypatch.setattr(sys, "meta_path", [Finder(), *sys.meta_path])
    dependencies.optional_import_roots.cache_clear()
    invalidate_caches()
    yield SimpleNamespace(deps=dependencies, families=families, disk=disk, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu51_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def start(env, source):
    from g6 import mcp

    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__spec__ = None
    module.__cached__ = None
    assert mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    env.bindings.clear()


def required_verdict(env):
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    server = import_module("rfu51_server")
    try:
        rows = env.families.inventory(family)
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
    else:
        row = next(row for row in rows if row["id"] == "mcp:abstraction/load_state")
        pytest.fail(f"required defect became optional recovery: {row}")


@pytest.mark.parametrize("entry", ["cached-describe", "inventory-control"])
def test_cached_describe_records_alias_after_clear(env, entry):
    source = env.disk / "shared.py"
    source.write_text(
        "from types import SimpleNamespace\n"
        "def get_tools():\n"
        "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n",
        encoding="utf-8")
    env.bindings[OPTIONAL] = source
    assert env.families.describe("load_state")["available"] is True
    env.deps.optional_import_roots.cache_clear()
    start(env, source)
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    if entry == "cached-describe":
        assert env.families.describe("load_state")["available"] is True
    else:
        assert any(r["available"] for r in env.families.inventory(family))
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    source.unlink()
    invalidate_caches()
    required_verdict(env)


@pytest.mark.parametrize("retry", ["describe", "inventory-control"])
def test_failed_startup_cache_cannot_report_healthy_retry(env, monkeypatch, retry):
    from packaging.requirements import InvalidRequirement
    from g6.mcp import registrations

    metadata_file = env.disk / "g6-1.0.dist-info" / "METADATA"
    metadata_file.write_text(
        "Metadata-Version: 2.3\nName: g6\nVersion: 1.0\nRequires-Dist: >>>broken<<<\n",
        encoding="utf-8")
    monkeypatch.setattr(registrations, "SERVERS", {"g6": "g6.mcp:build_server"})
    with pytest.raises(InvalidRequirement):
        env.families.describe("g6_help")
    family = env.families.family_for({"id": "mcp:g6/g6_help", "kind": "mcp"})
    with pytest.raises(InvalidRequirement):
        if retry == "describe":
            reply = env.families.describe("g6_help")
            pytest.fail(f"failed startup reported healthy on retry: {reply}")
        else:
            env.families.inventory(family)


@pytest.mark.parametrize("entry", ["standalone-startup", "main-startup-control"])
def test_standalone_startup_records_required_files(env, monkeypatch, entry):
    from g6 import mcp
    from g6.abstraction import server

    source = env.disk / "shared.py"
    source.write_text("value = 73\n", encoding="utf-8")
    env.bindings[REQUIRED] = source
    loaded = import_module(REQUIRED)
    loaded.__spec__ = None
    loaded.__cached__ = None
    if entry == "standalone-startup":
        monkeypatch.delenv("ABSTRACTION_MCP_STATE", raising=False)
        runs = []
        monkeypatch.setattr(server.mcp, "run", lambda: runs.append(True))
        server.main()
        assert runs == [True]
    else:
        assert mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    env.bindings.clear()
    env.bindings[OPTIONAL] = source
    env.deps.optional_import_roots("abstraction", "")
    env.bindings.clear()
    source.unlink()
    invalidate_caches()
    assert mcp.build_server().name == "g6"
    required_verdict(env)


@pytest.mark.parametrize("location", ["loaded-child", "loaded-root-control"])
def test_startup_records_loaded_child_file(env, monkeypatch, location):
    from g6 import mcp

    package = env.disk / REQUIRED
    package.mkdir()
    (package / "__init__.py").write_text("from . import child\n", encoding="utf-8")
    child = package / "child.py"
    child.write_text("value = 73\n", encoding="utf-8")
    import_module(REQUIRED)
    assert sys.modules[REQUIRED + ".child"].value == 73
    if location == "loaded-root-control":
        sys.modules[REQUIRED].__file__ = str(child)
        sys.modules[REQUIRED].__spec__ = None
    assert mcp.build_server().name == "g6"
    for name in (REQUIRED, REQUIRED + ".child"):
        sys.modules.pop(name)
    env.bindings[OPTIONAL] = child
    env.deps.optional_import_roots("abstraction", "")
    env.bindings.clear()
    child.unlink()
    invalidate_caches()
    required_verdict(env)


@pytest.mark.parametrize("change", ["remove", "replace"])
def test_full_start_discover_change_rediscover_control(env, change):
    source = env.disk / "shared.py"
    source.write_text(
        "from types import SimpleNamespace\n"
        "def get_tools():\n"
        "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n",
        encoding="utf-8")
    start(env, source)
    env.bindings[OPTIONAL] = source
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    rows = env.families.describe(family)["members"]
    assert next(r for r in rows if r["id"] == "mcp:abstraction/load_state")["available"]
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    if change == "remove":
        source.unlink()
        invalidate_caches()
        required_verdict(env)
    else:
        retired = source.with_name("retired.py")
        source.rename(retired)
        source.write_text("value = 'independent replacement'\n", encoding="utf-8")
        assert source.stat().st_ino != retired.stat().st_ino
        invalidate_caches()
        rows = env.families.describe(family)["members"]
        row = next(r for r in rows if r["id"] == "mcp:abstraction/load_state")
        assert row["available"] is False
        assert row["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in row["install"]


@pytest.mark.parametrize("contention", ["retry", "fallback", "clear-first", "clear-second"])
def test_carried_observation_clear_and_contention_controls(env, monkeypatch, contention):
    shared = env.disk / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    env.bindings[REQUIRED] = env.bindings[OPTIONAL] = shared
    observe = env.deps._root_observations
    calls = 0

    def interrupt(roots):
        nonlocal calls
        result = observe(roots)
        calls += 1
        if calls == 1:
            assert result[REQUIRED][2]
            env.bindings.clear()
            shared.rename(shared.with_name("retired.py"))
        if ((contention == "clear-first" and calls == 1)
                or (contention == "clear-second" and calls == 2)):
            env.deps.optional_import_roots.cache_clear()
        elif calls == 1 or contention == "fallback":
            other = env.disk / f"other{calls}.py"
            other.write_text(f"value = {calls}\n", encoding="utf-8")
            env.bindings[OTHER] = other
            with monkeypatch.context() as nested:
                nested.setattr(env.deps, "_root_observations", observe)
                before = env.deps._ownership_generation
                env.deps.optional_import_roots("other", "")
                assert env.deps._ownership_generation > before
        return result

    with monkeypatch.context() as probing:
        probing.setattr(env.deps, "_root_observations", interrupt)
        env.deps.optional_import_roots("abstraction")
    assert calls == 2
    env.bindings.clear()
    result = env.deps.optional_import_roots("abstraction", "")
    assert result == ({OPTIONAL} if contention.startswith("clear-") else frozenset())
