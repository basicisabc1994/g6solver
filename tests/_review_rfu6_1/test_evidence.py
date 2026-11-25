"""Offline disk-backed review 1; run with --confcutdir=tests/_review_rfu6_1."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu61_required"
OPTIONAL = "rfu61_optional"
TOOLS = (
    "from types import SimpleNamespace\n"
    "def get_tools():\n"
    "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n"
)


@pytest.fixture
def env(monkeypatch):
    disk = Path(__file__).resolve().parents[2] / ".bt_rev_rfu6_1" / "cases" / uuid4().hex
    disk.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull, "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies as deps, families, registrations

    needs = {"g6": ['rfu61-base; extra == "mcp"', 'rfu61-extra; extra == "abstraction"'],
             "rfu61-base": [], "rfu61-extra": []}
    roots = {"g6": "g6", "rfu61-base": REQUIRED, "rfu61-extra": OPTIONAL}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu61_server:build"})
    (disk / "rfu61_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu61_optional')\n"
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
        if name.startswith("rfu61_"):
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
        assert exc is import_module("rfu61_server").FAILURE
        return "required"
    row = next(row for row in rows if row["id"] == "mcp:abstraction/load_state")
    assert not row["available"] and "g6[abstraction,mcp]" in row["install"]
    return "optional-install"



@pytest.mark.parametrize("interrupted", [True, False], ids=["recording-interrupted", "normal-handoff-control"])
def test_rejected_probe_survives_interrupted_handoff(env, monkeypatch, interrupted):
    loaded = start(env)
    source = Path(loaded.__file__)
    source.rename(env.disk / "retired.py")
    source.write_text(TOOLS, encoding="utf-8")
    replacement = env.disk / "replacement.py"
    replacement_identity = env.deps._stat_identity(source)
    guard_path = env.disk / "guard.py"
    guard_path.write_text(
        "def __getattr__(name):\n"
        "    if name == '__path__':\n"
        "        raise SystemExit('interrupted module introspection')\n"
        "    raise AttributeError(name)\n", encoding="utf-8")
    guard_name = REQUIRED + ".guard"
    observed = []
    stat = env.deps._stat_identity

    def intercept(path):
        result = stat(path)
        if str(path) == str(source) and not observed:
            observed.append(result)
            source.rename(replacement)
            if interrupted:
                spec = util.spec_from_file_location(guard_name, guard_path)
                guard = util.module_from_spec(spec)
                spec.loader.exec_module(guard)
                sys.modules[guard_name] = guard
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intercept)
        if interrupted:
            with pytest.raises(SystemExit, match="interrupted module introspection"):
                env.families.describe("load_state")
        else:
            assert env.families.describe("load_state")["available"] is True
    assert observed == [replacement_identity]
    sys.modules.pop(guard_name, None)
    assert env.families.describe("load_state")["available"] is True
    assert transfer_and_check(env, replacement) == "required", "rejected evidence vanished on interrupted handoff"


def load_child(env, source, suffix="child"):
    name = REQUIRED + "." + suffix
    spec = util.spec_from_file_location(name, source)
    child = util.module_from_spec(spec)
    sys.modules[name] = child
    spec.loader.exec_module(child)
    child.__spec__.cached = child.__cached__ = None
    assert child.get_tools.__code__.co_filename == str(source)
    return child


@pytest.mark.parametrize("change", ["new-child", "replacement-module"])
def test_second_stat_pass_rechecks_module_changes_control(env, monkeypatch, change):
    loaded = start(env)
    if change == "replacement-module":
        initial = env.disk / "initial-child.py"
        initial.write_text(TOOLS, encoding="utf-8")
        load_child(env, initial)
        assert env.families.describe("load_state")["available"] is True
    source = env.disk / "second-pass-child.py"
    source.write_text(TOOLS, encoding="utf-8")
    stat = env.deps._stat_identity
    calls = []

    def intercept(path):
        result = stat(path)
        if str(path) == loaded.__file__:
            calls.append(result)
            if len(calls) == 2:
                load_child(env, source)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intercept)
        assert env.families.describe("load_state")["available"] is True
    assert len(calls) >= 2
    assert transfer_and_check(env, source) == "required"


@pytest.mark.parametrize("owner", ["root", "descendant"])
def test_rejected_cache_hint_keeps_ownership_boundary_control(env, monkeypatch, owner):
    loaded = start(env)
    if owner == "descendant":
        child_source = env.disk / "child-source.py"
        child_source.write_text(TOOLS, encoding="utf-8")
        loaded = load_child(env, child_source)
    cache = env.disk / "hint.py"
    loaded.__cached__ = str(cache)
    assert env.families.describe("load_state")["available"] is True
    assert "abstraction" in env.families._RECORDED_SERVERS
    cache.write_text(TOOLS, encoding="utf-8")
    moved = env.disk / "moved-hint.py"
    stat = env.deps._stat_identity
    observed = []

    def intercept(path):
        result = stat(path)
        if str(path) == str(cache) and not observed:
            observed.append(result)
            cache.rename(moved)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intercept)
        assert env.families.describe("load_state")["available"] is True
    assert observed
    expected = "required" if owner == "root" else "optional-install"
    assert transfer_and_check(env, moved) == expected


@pytest.mark.parametrize("contention", ["retry", "fallback", "clear"])
def test_rejected_evidence_retry_and_clear_control(env, monkeypatch, contention):
    loaded = start(env)
    source = Path(loaded.__file__)
    source.rename(env.disk / "retired.py")
    source.write_text(TOOLS, encoding="utf-8")
    moved = env.disk / "moved.py"
    stat = env.deps._stat_identity
    observed = []
    snapshot = env.deps._ownership_snapshot
    snapshots = []
    refresh = env.deps._path_refresh_observations
    refreshes = []

    def intercept(path):
        result = stat(path)
        if str(path) == str(source) and not observed:
            observed.append(result)
            source.rename(moved)
            if contention == "clear":
                sys.modules.pop(REQUIRED, None)
                env.bindings.pop(REQUIRED)
                env.deps.optional_import_roots.cache_clear()
        return result

    def copy_snapshot():
        result = snapshot()
        snapshots.append(result[0])
        return result

    def contend(paths):
        result = refresh(paths)
        refreshes.append(True)
        if contention != "clear" and len(refreshes) <= (2 if contention == "fallback" else 1):
            # A real independent ownership transaction, with no filesystem or
            # module callbacks under the lock. It leaves this source alone.
            with env.deps._ownership_lock:
                before = env.deps._ownership_state()
                env.deps._known_paths.setdefault("independent_" + str(len(refreshes)), set())
                env.deps._advance_ownership_generation(before)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intercept)
        patch.setattr(env.deps, "_ownership_snapshot", copy_snapshot)
        patch.setattr(env.deps, "_path_refresh_observations", contend)
        assert env.families.describe("load_state")["available"] is True
    assert observed and snapshots
    if contention != "clear":
        assert len(refreshes) >= (3 if contention == "fallback" else 2)
    expected = "optional-install" if contention == "clear" else "required"
    assert transfer_and_check(env, moved) == expected


def test_concurrent_describe_preserves_pending_evidence_control(env, monkeypatch):
    from threading import Event, Thread, current_thread

    loaded = start(env)
    source = Path(loaded.__file__)
    source.rename(env.disk / "retired.py")
    source.write_text(TOOLS, encoding="utf-8")
    moved = env.disk / "moved.py"
    stat = env.deps._stat_identity
    ready, finished = Event(), Event()
    observed, errors = [], []
    owner = current_thread()

    def intercept(path):
        result = stat(path)
        if current_thread() is owner and str(path) == str(source) and not observed:
            observed.append(result)
            source.rename(moved)
            ready.set()
            assert finished.wait(10), "concurrent describe hung"
        return result

    def other_describe():
        try:
            assert ready.wait(10), "reuse probe did not start"
            assert env.families.describe("load_state")["available"] is True
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", intercept)
        worker = Thread(target=other_describe, daemon=True)
        worker.start()
        try:
            assert env.families.describe("load_state")["available"] is True
        finally:
            ready.set()
            worker.join(12)
    assert not worker.is_alive(), "concurrent describe did not terminate"
    assert not errors, errors
    assert observed
    assert transfer_and_check(env, moved) == "required"


def test_unchanged_reuse_cost_and_module_lock_control(env, monkeypatch):
    from collections import Counter
    from types import ModuleType

    loaded = start(env)
    key = env.families._RECORDED_SERVERS["abstraction"][1]
    reads = []

    class CheckedModule(ModuleType):
        def __getattribute__(self, name):
            if name in {"__file__", "__spec__", "__cached__", "__path__"}:
                assert not env.deps._ownership_lock._is_owned()
                reads.append(name)
            return super().__getattribute__(name)

    loaded.__class__ = CheckedModule
    stat = env.deps._stat_identity
    calls = Counter()

    def count(path):
        calls[str(path)] += 1
        return stat(path)

    def no_full_recording(*args, **kwargs):
        pytest.fail("unchanged cached describe performed a full recording")

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", count)
        patch.setattr(env.deps, "record_loaded_required_imports", no_full_recording)
        patch.setattr(env.deps, "optional_import_roots", no_full_recording)
        assert env.families.describe("load_state")["available"] is True
    assert reads
    assert calls == Counter({source: 2 for source, _ in key[3]})
