"""Offline real-disk adversarial ownership review; no production changes."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu44_required"
OPTIONAL = "rfu44_optional"
OTHER = "rfu44_other"


@pytest.fixture
def tmp_path():
    # pytest's mode-0700 basetemp is inaccessible to this Windows sandbox.
    disk = Path(__file__).parent / "_disk" / uuid4().hex
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

    needs = {"g6": ['rfu44-base; extra == "mcp"',
                    'rfu44-extra; extra == "abstraction"',
                    'rfu44-other; extra == "other"'],
             "rfu44-base": [], "rfu44-extra": [], "rfu44-other": []}
    roots = {"g6": "g6", "rfu44-base": REQUIRED,
             "rfu44-extra": OPTIONAL, "rfu44-other": OTHER}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu44_server:build"})
    (tmp_path / "rfu44_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu44_optional')\n"
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
    yield SimpleNamespace(deps=dependencies, families=families, disk=tmp_path, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu44_"):
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


def verdict(env, required):
    server = import_module("rfu44_server")
    try:
        reply = env.families.describe("load_state")
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
        assert required, "a directly retired identity still blocks optional recovery"
    else:
        assert not required, f"required defect became an install hint: {reply}"
        assert reply["available"] is False
        assert reply["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in reply["install"]


@pytest.mark.parametrize("entry", ["startup", "discovery"])
@pytest.mark.parametrize("contention", ["retry", "fallback", "serial-control", "clear-control"])
def test_retry_keeps_successful_refresh_evidence(installation, monkeypatch, entry, contention):
    env = installation
    source, retired, replacement = (env.disk / name for name in
                                    ("source.py", "retired.py", "replacement.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    start(env, source)
    if entry == "startup":
        env.bindings[REQUIRED] = source
        module = import_module(REQUIRED)
        module.__spec__ = None
        module.__cached__ = None
    refresh = env.deps._path_refresh_observations
    calls = 0

    def interrupt(paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            source.rename(retired)
            source.write_text("value = 'replacement'\n", encoding="utf-8")
        result = refresh(paths)
        if calls == 1:
            assert any(event[1] == env.deps._stat_identity(str(source))
                       for events in result.values() for event in events)
            source.rename(replacement)
            sys.modules.pop(REQUIRED, None)
            env.bindings.clear()
        if contention in {"retry", "fallback", "clear-control"} and (
                calls == 1 or contention == "fallback"):
            with monkeypatch.context() as nested:
                nested.setattr(env.deps, "_path_refresh_observations", refresh)
                if contention == "clear-control":
                    env.deps.optional_import_roots.cache_clear()
                else:
                    other = env.disk / f"other{calls}.py"
                    other.write_text(f"value = {calls}\n", encoding="utf-8")
                    env.bindings[OTHER] = other
                    before = env.deps._ownership_generation
                    env.deps.optional_import_roots("other", "")
                    assert env.deps._ownership_generation > before
        return result

    with monkeypatch.context() as outer:
        outer.setattr(env.deps, "_path_refresh_observations", interrupt)
        if entry == "startup":
            env.deps.record_loaded_required_imports()
        else:
            env.deps.optional_import_roots("abstraction", "")
    assert calls == (1 if contention == "serial-control" or
                     (contention == "clear-control" and entry == "startup") else 2)
    env.bindings[OPTIONAL] = retired
    env.deps.optional_import_roots("abstraction", "")
    env.bindings.clear()
    retired.unlink()
    replacement.unlink()
    invalidate_caches()
    verdict(env, required=False)


@pytest.mark.parametrize("confirm_old", [True, False], ids=["secondary-old", "direct-old-control"])
def test_secondary_confirmation_cannot_undo_direct_retirement(installation, monkeypatch, confirm_old):
    from g6 import mcp

    env = installation
    source, retired, replacement = (env.disk / name for name in
                                    ("source.py", "retired.py", "replacement.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    spelling = str(source.parent) + "\\.\\" + source.name
    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__file__ = spelling
    module.__spec__ = SimpleNamespace(origin=spelling)
    module.__cached__ = None
    assert mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    env.bindings.clear()
    assert len([r for r in env.deps._required_paths if r[2] == spelling]) == 2
    source.rename(retired)
    source.write_text("value = 'replacement'\n", encoding="utf-8")
    stat = env.deps._stat_identity
    direct, secondary = 0, 0

    def probe(path):
        nonlocal direct, secondary
        assert not env.deps._ownership_lock._is_owned()
        if path == spelling:
            direct += 1
            if direct == 1:
                result = stat(path)  # Successful direct replacement B.
                source.rename(replacement)
                retired.rename(source)  # Restore A before the other record's probe.
                return result
            if confirm_old:
                raise OSError("recorded spelling unreadable")
        elif path == str(source):
            secondary += 1
        return stat(path)

    with monkeypatch.context() as probing:
        probing.setattr(env.deps, "_stat_identity", probe)
        env.deps.optional_import_roots("abstraction", "")
    assert direct == 2
    assert secondary == int(confirm_old)
    source.rename(retired)  # No later direct stat of A can change the decision.
    env.bindings[OPTIONAL] = retired
    env.deps.optional_import_roots("abstraction", "")
    env.bindings.clear()
    retired.unlink()
    replacement.unlink()
    invalidate_caches()
    verdict(env, required=not confirm_old)


@pytest.mark.parametrize("contention", ["retry", "fallback", "serial-control", "clear-control"])
def test_discovery_retry_keeps_new_required_file(installation, monkeypatch, contention):
    env = installation
    original, shared, removed = (env.disk / name for name in
                                 ("original.py", "shared.py", "removed.py"))
    original.write_text("value = 'startup required'\n", encoding="utf-8")
    start(env, original)
    shared.write_text("value = 'new required identity'\n", encoding="utf-8")
    env.bindings[OPTIONAL] = shared
    assert env.deps.optional_import_roots("abstraction", "") == {OPTIONAL}
    env.bindings[REQUIRED] = shared
    module = import_module(REQUIRED)
    module.__spec__ = None
    module.__cached__ = None
    observe = env.deps._root_observations
    calls = 0

    def interrupt(roots):
        nonlocal calls
        result = observe(roots)
        calls += 1
        if calls == 1:
            # This discovery really statted the newly loaded required file.
            assert any(r[4:] == env.deps._stat_identity(str(shared))
                       for _, r in result[REQUIRED][5])
            sys.modules.pop(REQUIRED)
            env.bindings.clear()
            shared.rename(removed)
        if contention in {"retry", "fallback", "clear-control"} and (
                calls == 1 or contention == "fallback"):
            with monkeypatch.context() as nested:
                nested.setattr(env.deps, "_root_observations", observe)
                if contention == "clear-control":
                    env.deps.optional_import_roots.cache_clear()
                else:
                    other = env.disk / f"other{calls}.py"
                    other.write_text(f"value = {calls}\n", encoding="utf-8")
                    env.bindings[OTHER] = other
                    before = env.deps._ownership_generation
                    env.deps.optional_import_roots("other", "")
                    assert env.deps._ownership_generation > before
        return result

    with monkeypatch.context() as outer:
        outer.setattr(env.deps, "_root_observations", interrupt)
        env.deps.optional_import_roots("abstraction")
    assert calls == (1 if contention == "serial-control" else 2)
    env.bindings.clear()
    removed.unlink()
    original.unlink()
    invalidate_caches()
    verdict(env, required=contention != "clear-control")


@pytest.mark.parametrize("entry", ["tool-describe", "inventory-control"])
def test_healthy_tool_description_records_required_alias(installation, entry):
    env = installation
    source = env.disk / "shared.py"
    source.write_text(
        "from types import SimpleNamespace\n"
        "def get_tools():\n"
        "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n",
        encoding="utf-8")
    start(env, source)
    env.bindings[OPTIONAL] = source
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    if entry == "tool-describe":
        assert env.families.describe("load_state")["available"] is True
    else:
        rows = env.families.inventory(family)
        assert next(row for row in rows if row["id"] == "mcp:abstraction/load_state")["available"] is True
    assert sys.modules[OPTIONAL].__file__ == str(source)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    source.unlink()
    invalidate_caches()
    server = import_module("rfu44_server")
    try:
        rows = env.families.inventory(family)
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
    else:
        member = next(row for row in rows if row["id"] == "mcp:abstraction/load_state")
        pytest.fail(f"healthy tool description lost required alias: {member}")
