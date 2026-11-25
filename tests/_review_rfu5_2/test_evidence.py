"""Offline real-disk reproductions for route-fu-5 review 2."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu52_required"
OPTIONAL = "rfu52_optional"
TOOLS = (
    "from types import SimpleNamespace\n"
    "def get_tools():\n"
    "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n"
)


@pytest.fixture
def env(monkeypatch):
    tmp_path = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    tmp_path.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(tmp_path / "home"),
        "FASTMCP_HOME": str(tmp_path / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull,
        "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies as deps, families, registrations

    needs = {
        "g6": ['rfu52-base; extra == "mcp"',
               'rfu52-extra; extra == "abstraction"'],
        "rfu52-base": [], "rfu52-extra": [],
    }
    roots = {"g6": "g6", "rfu52-base": REQUIRED, "rfu52-extra": OPTIONAL}
    for name, requirements in needs.items():
        folder = tmp_path / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir()
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements),
            encoding="utf-8",
        )
        (folder / "top_level.txt").write_text(roots[name] + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(tmp_path)])}

    def distributions(**kwargs):
        assert not deps._ownership_lock._is_owned()
        return scan(path=[str(tmp_path)])

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
    monkeypatch.syspath_prepend(str(tmp_path))
    for attr in ("_DETAILS", "_VALIDATED_DETAILS", "_RECORDED_SERVERS"):
        monkeypatch.setattr(families, attr, {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu52_server:build"})
    (tmp_path / "rfu52_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu52_optional')\n"
        "    except ModuleNotFoundError as exc:\n        FAILURE = exc\n        raise\n",
        encoding="utf-8",
    )
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
    yield SimpleNamespace(deps=deps, families=families, disk=tmp_path, bindings=bindings)
    deps.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu52_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def required_failure(env):
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    server = import_module("rfu52_server")
    try:
        rows = env.families.inventory(family)
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
    else:
        row = next(r for r in rows if r["id"] == "mcp:abstraction/load_state")
        pytest.fail(f"required file received optional install recovery: {row}")


@pytest.mark.parametrize("identity", ["replacement", "retired"])
@pytest.mark.parametrize("entry", ["cached-describe", "inventory-control"])
def test_cached_describe_misses_required_replacement(env, entry, identity):
    from g6 import mcp

    source, retired, alias = (env.disk / name for name in
                              ("required.py", "retired.py", "alias.py"))
    source.write_text(TOOLS, encoding="utf-8")
    env.bindings[REQUIRED] = source
    loaded = import_module(REQUIRED)
    loaded.__spec__ = None
    loaded.__cached__ = None
    assert mcp.build_server().name == "g6"
    optional = env.disk / "optional.py"
    optional.write_text(TOOLS, encoding="utf-8")
    env.bindings[OPTIONAL] = optional
    assert env.families.describe("load_state")["available"] is True
    key = env.deps._loaded_required_recording_key()

    source.rename(retired)
    source.write_text(TOOLS, encoding="utf-8")
    assert source.stat().st_ino != retired.stat().st_ino
    assert env.deps._loaded_required_recording_key() == key
    if entry == "cached-describe":
        assert env.families.describe("load_state")["available"] is True
    else:
        family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
        assert any(r["available"] for r in env.families.inventory(family))

    # No later probe can recover the required spelling's replacement identity.
    sys.modules.pop(REQUIRED)
    if identity == "replacement":
        source.rename(alias)
    else:
        source.unlink()
        alias = retired
    env.bindings.pop(REQUIRED)
    sys.modules.pop(OPTIONAL)
    env.bindings[OPTIONAL] = alias
    assert env.families.inventory(
        env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    )
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    alias.unlink()
    optional.unlink()
    invalidate_caches()
    if identity == "replacement":
        required_failure(env)
    else:
        try:
            rows = env.families.inventory(
                env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
            )
        except ModuleNotFoundError:
            pytest.fail("retired file still has required ownership")
        row = next(r for r in rows if r["id"] == "mcp:abstraction/load_state")
        assert row["available"] is False
        assert row["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in row["install"]


@pytest.mark.parametrize("entry", ["cached-describe", "inventory-control"])
def test_incomplete_child_recording_is_reused(env, monkeypatch, entry):
    package = env.disk / REQUIRED
    package.mkdir()
    (package / "__init__.py").write_text("from . import child\n", encoding="utf-8")
    source = package / "child.py"
    source.write_text(TOOLS, encoding="utf-8")
    alias = env.disk / "child-alias.py"
    import_module(REQUIRED)
    child = sys.modules[REQUIRED + ".child"]
    child.__spec__ = None
    child.__cached__ = None
    optional = env.disk / "optional.py"
    optional.write_text(TOOLS, encoding="utf-8")
    env.bindings[OPTIONAL] = optional
    stat = env.deps._stat_identity
    faults = []

    def unreadable(path):
        if os.fspath(path) == str(source):
            faults.append(path)
            raise OSError("temporarily unreadable child identity")
        return stat(path)

    with monkeypatch.context() as fault:
        fault.setattr(env.deps, "_stat_identity", unreadable)
        assert env.families.describe("load_state")["available"] is True
    assert faults
    if entry == "cached-describe":
        assert env.families.describe("load_state")["available"] is True
    else:
        family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
        assert any(r["available"] for r in env.families.inventory(family))

    sys.modules.pop(REQUIRED + ".child")
    sys.modules.pop(REQUIRED)
    source.rename(alias)
    sys.modules.pop(OPTIONAL)
    env.bindings[OPTIONAL] = alias
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    assert env.families.inventory(family)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    alias.unlink()
    optional.unlink()
    invalidate_caches()
    required_failure(env)


@pytest.mark.parametrize("failure", ["recording-fails", "successful-control"])
def test_standalone_recording_precedes_run(env, monkeypatch, failure):
    from g6.abstraction import server

    monkeypatch.delenv("ABSTRACTION_MCP_STATE", raising=False)
    calls = []
    error = RuntimeError("recording failed")

    def record():
        calls.append("record")
        if failure == "recording-fails":
            raise error

    monkeypatch.setattr(env.deps, "record_loaded_required_imports", record)
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append("run"))
    if failure == "recording-fails":
        with pytest.raises(RuntimeError) as stopped:
            server.main()
        assert stopped.value is error
        assert calls == ["record"]
    else:
        server.main()
        assert calls == ["record", "run"]


@pytest.mark.parametrize("invalidate", ["clear", "recording-failure"])
def test_invalidated_describe_reloads(env, monkeypatch, invalidate):
    from g6.mcp import registrations

    source = env.disk / "optional.py"
    source.write_text(TOOLS, encoding="utf-8")
    env.bindings[OPTIONAL] = source
    load = registrations._load
    calls = []

    def tracked(target):
        calls.append(target)
        return load(target)

    monkeypatch.setattr(registrations, "_load", tracked)
    if invalidate == "clear":
        assert env.families.describe("load_state")["available"] is True
        env.deps.optional_import_roots.cache_clear()
    else:
        def broken():
            raise RuntimeError("recorder refused")

        with monkeypatch.context() as fault:
            fault.setattr(env.deps, "record_loaded_required_imports", broken)
            with pytest.raises(RuntimeError, match="recorder refused"):
                env.families.describe("load_state")
    assert env.families.describe("load_state")["available"] is True
    assert len(calls) == 2
