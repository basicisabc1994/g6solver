"""Offline, real-disk review of required file identity reconciliation."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu41_required"
OPTIONAL = "rfu41_optional"
OTHER = "rfu41_other"


@pytest.fixture
def installation(monkeypatch):
    from g6.mcp import dependencies, families, registrations

    disk = Path(__file__).resolve().parents[2] / ".bt_rev_rfu4_1" / uuid4().hex
    disk.mkdir(parents=True)
    for name, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_CHECK_FOR_UPDATES": "off", "FASTMCP_ENV_FILE": os.devnull,
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(name, value)
    needs = {"g6": ['rfu41-base; extra == "mcp"',
                    'rfu41-extra; extra == "abstraction"',
                    'rfu41-other; extra == "other"'],
             "rfu41-base": [], "rfu41-extra": [], "rfu41-other": []}
    roots = {"g6": "g6", "rfu41-base": REQUIRED,
             "rfu41-extra": OPTIONAL, "rfu41-other": OTHER}
    for name, requirements in needs.items():
        folder = disk / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir()
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements), encoding="utf-8")
        (folder / "top_level.txt").write_text(roots[name] + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(disk)])}
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(disk)]))
    monkeypatch.setattr(metadata, "requires", lambda name: records[name].requires)
    monkeypatch.syspath_prepend(str(disk))
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu41_server:build"})
    (disk / "rfu41_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu41_optional')\n"
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
    yield SimpleNamespace(deps=dependencies, families=families, disk=disk, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu41_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def assert_recovery(env, required=False):
    server = import_module("rfu41_server")
    try:
        reply = env.families.describe("load_state")
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
        if not required:
            pytest.fail("retired required identity blocked optional recovery")
    else:
        assert not required, f"required defect became an install hint: {reply}"
        assert reply["available"] is False
        assert reply["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in reply["install"]


@pytest.mark.parametrize("interleave", ["retry", "fallback", "serial-control", "clear-retry-control"])
def test_startup_retry_does_not_restore_retired_identity(installation, monkeypatch, interleave):
    from g6 import mcp

    env = installation
    required, retired, optional = (env.disk / name for name in ("required.py", "retired.py", "optional.py"))
    required.write_text("value = 73\n", encoding="utf-8")
    env.bindings[REQUIRED] = required
    assert import_module(REQUIRED).value == 73
    env.deps.record_loaded_required_imports()
    generation = env.deps._ownership_generation
    env.deps.record_loaded_required_imports()
    assert env.deps._ownership_generation == generation, "duplicate snapshot changed ownership"
    refresh = env.deps._path_refresh_observations
    calls = 0
    observed, resume = [Event(), Event()], [Event(), Event()]
    results, failures = [], []

    def replace_and_retire():
        sys.modules.pop(REQUIRED)
        env.bindings.pop(REQUIRED)
        required.rename(retired)
        required.write_text("value = 'replacement'\n", encoding="utf-8")
        assert required.stat().st_ino != retired.stat().st_ino
        os.link(retired, optional)
        env.bindings[OPTIONAL] = optional
        with monkeypatch.context() as transaction:
            transaction.setattr(env.deps, "_path_refresh_observations", refresh)
            assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
        required.unlink()

    def pause(paths):
        nonlocal calls
        result = refresh(paths)
        index = calls
        calls += 1
        if interleave != "serial-control":
            assert index < 2
            observed[index].set()
            assert resume[index].wait(15), "startup was not released"
        return result

    def start():
        try:
            results.append(mcp.build_server())
        except BaseException as exc:
            failures.append(exc)

    with monkeypatch.context() as recording:
        recording.setattr(env.deps, "_path_refresh_observations", pause)
        if interleave == "serial-control":
            start()
            replace_and_retire()
        else:
            worker = Thread(target=start, daemon=True)
            worker.start()
            try:
                assert observed[0].wait(15), "startup did not reach its first refresh"
                replace_and_retire()
                resume[0].set()
                assert observed[1].wait(15), "startup did not retry"
                if interleave == "fallback":
                    other = env.disk / "other.py"
                    other.write_text("value = 'unrelated'\n", encoding="utf-8")
                    env.bindings[OTHER] = other
                    with monkeypatch.context() as transaction:
                        transaction.setattr(env.deps, "_path_refresh_observations", refresh)
                        before = env.deps._ownership_generation
                        assert env.deps.optional_import_roots("other", required_extras="") == {OTHER}
                        assert env.deps._ownership_generation > before
                elif interleave == "clear-retry-control":
                    env.deps.optional_import_roots.cache_clear()
                resume[1].set()
                worker.join(15)
                assert not worker.is_alive(), "startup did not finish"
            finally:
                for event in resume:
                    event.set()
                worker.join(15)
    assert not failures, failures
    assert len(results) == 1 and results[0].name == "g6"
    assert calls == (1 if interleave == "serial-control" else 2)
    # The optional alias still names the old file; its missing import is optional.
    assert import_module(OPTIONAL).value == 73
    env.deps.optional_import_roots("abstraction", required_extras="")
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    optional.unlink()
    retired.unlink()
    invalidate_caches()
    assert_recovery(env)


@pytest.mark.parametrize("alternate_spelling", [True, False], ids=["junction-spelling", "same-spelling-control"])
def test_later_stat_at_resolved_spelling_retires_owner(installation, monkeypatch, alternate_spelling):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir()
    junction = env.disk / "site"
    _winapi.CreateJunction(str(target), str(junction))
    required = target / "required.py"
    retired = target / "retired.py"
    optional = env.disk / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    source = junction / "required.py" if alternate_spelling else required
    env.bindings[REQUIRED] = source
    assert import_module(REQUIRED).value == 73
    env.deps.record_loaded_required_imports()
    sys.modules.pop(REQUIRED)
    env.bindings.clear()
    observe = env.deps._root_observations
    replaced = False

    def replace_between_probes(roots):
        nonlocal replaced
        if not replaced:
            replaced = True
            required.rename(retired)
            required.write_text("value = 'replacement'\n", encoding="utf-8")
            assert required.stat().st_ino != retired.stat().st_ino
            env.bindings[OPTIONAL] = required
        return observe(roots)

    try:
        with monkeypatch.context() as transaction:
            transaction.setattr(env.deps, "_root_observations", replace_between_probes)
            assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
        required.unlink()  # No future refresh can repair a missed replacement stat.
        os.link(retired, optional)
        env.bindings[OPTIONAL] = optional
        assert import_module(OPTIONAL).value == 73
        env.deps.optional_import_roots("abstraction", required_extras="")
        sys.modules.pop(OPTIONAL)
        env.bindings.clear()
        optional.unlink()
        retired.unlink()
        invalidate_caches()
        assert_recovery(env)
    finally:
        os.rmdir(junction)


@pytest.mark.parametrize("retarget", [True, False], ids=["same-file-retarget", "replacement-control"])
def test_resolved_second_stat_must_still_describe_recorded_source(installation, monkeypatch, retarget):
    import _winapi

    env = installation
    first, second = env.disk / "first", env.disk / "second"
    first.mkdir()
    second.mkdir()
    original, alias = first / "shared.py", second / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, alias)
    junction = env.disk / "site"
    _winapi.CreateJunction(str(first), str(junction))
    source = junction / "shared.py"
    for root in (REQUIRED, OPTIONAL):
        env.bindings[root] = source
        module = import_module(root)
        assert module.value == 73
        # File-only loaded modules are supported; both roots describe one record.
        module.__spec__ = None
        module.__cached__ = None
    assert env.deps.optional_import_roots("abstraction") == frozenset()
    identity = (source.stat().st_dev, source.stat().st_ino)
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    stat = os.stat
    raced = False

    def unavailable_source(path, *args, **kwargs):
        nonlocal raced
        spelling = os.fspath(path)
        if spelling == str(source):
            raise OSError("transient primary stat failure")
        if spelling == str(original) and not raced:
            raced = True
            # Strict realpath already selected first/shared.py. Before its stat,
            # keep the recorded source on A while that resolved spelling gets B.
            if retarget:
                os.rmdir(junction)
                _winapi.CreateJunction(str(second), str(junction))
            original.rename(first / "retired.py")
            original.write_text("value = 'replacement'\n", encoding="utf-8")
            assert (stat(source).st_dev, stat(source).st_ino) == (
                identity if retarget else (stat(original).st_dev, stat(original).st_ino))
            assert (stat(original).st_dev, stat(original).st_ino) != identity
        return stat(path, *args, **kwargs)

    try:
        with monkeypatch.context() as probing:
            probing.setattr(os, "stat", unavailable_source)
            env.deps.optional_import_roots("abstraction", required_extras="")
        assert raced
        os.rmdir(junction)
        invalidate_caches()
        assert_recovery(env, required=retarget)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("error", [OSError, ValueError])
def test_secondary_stat_failure_keeps_owner_and_runs_outside_lock(installation, monkeypatch, error):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir()
    file = target / "shared.py"
    file.write_text("value = 73\n", encoding="utf-8")
    junction = env.disk / "site"
    _winapi.CreateJunction(str(target), str(junction))
    source = junction / "shared.py"
    for root in (REQUIRED, OPTIONAL):
        env.bindings[root] = source
        assert import_module(root).value == 73
    assert env.deps.optional_import_roots("abstraction") == frozenset()
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    stat = os.stat
    secondary_calls = []

    def unavailable(path, *args, **kwargs):
        if os.fspath(path) == str(source):
            raise OSError("primary stat unavailable")
        if os.fspath(path) == str(file):
            secondary_calls.append(env.deps._ownership_lock._is_owned())
            raise error("secondary stat unavailable")
        return stat(path, *args, **kwargs)

    try:
        with monkeypatch.context() as probing:
            probing.setattr(os, "stat", unavailable)
            env.deps.optional_import_roots("abstraction", required_extras="")
        assert secondary_calls and not any(secondary_calls)
        os.rmdir(junction)
        invalidate_caches()
        assert_recovery(env, required=True)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("module_name", ["_frozen_importlib", "sys"])
def test_real_origin_sentinel_stays_nonfile_after_chdir(installation, monkeypatch, module_name):
    env = installation
    loaded = import_module(module_name)
    sentinel = loaded.__spec__.origin
    assert sentinel in {"frozen", "built-in"}
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    for name in ("first", "second"):
        directory = env.disk / name
        directory.mkdir()
        (directory / sentinel).write_text("value = 'unrelated disk file'\n", encoding="utf-8")
    optional = env.disk / "optional.py"
    os.link(env.disk / "first" / sentinel, optional)
    env.bindings[OPTIONAL] = optional
    stat = os.stat
    sentinel_probes = []

    def watch(path, *args, **kwargs):
        if os.fspath(path) == sentinel:
            sentinel_probes.append(path)
        return stat(path, *args, **kwargs)

    with monkeypatch.context() as probing:
        probing.setattr(os, "stat", watch)
        for name in ("first", "second"):
            probing.chdir(env.disk / name)
            env.deps.record_loaded_required_imports()
            assert env.deps.optional_import_roots("abstraction") == {OPTIONAL}
    assert not sentinel_probes


def test_later_failed_probe_does_not_erase_startup_success(installation, monkeypatch):
    env = installation
    shared = env.disk / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    env.bindings[OPTIONAL] = shared
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
    env.bindings[REQUIRED] = shared
    assert import_module(REQUIRED).value == 73
    stat = env.deps._stat_identity
    calls = []

    def fail_after_success(path):
        if path == str(shared):
            calls.append(path)
            if len(calls) > 1:
                raise OSError("later probe failed")
        return stat(path)

    with monkeypatch.context() as probing:
        probing.setattr(env.deps, "_stat_identity", fail_after_success)
        env.deps.record_loaded_required_imports()
    assert len(calls) > 1
    sys.modules.pop(REQUIRED)
    env.bindings.clear()
    shared.unlink()
    invalidate_caches()
    assert_recovery(env, required=True)


@pytest.mark.parametrize("probe_alias", [False, True], ids=["healthy-family-discovery", "direct-probe-control"])
def test_healthy_family_discovery_records_loaded_required_alias(installation, probe_alias):
    from g6 import mcp

    env = installation
    required, optional = env.disk / "required.py", env.disk / "optional.py"
    required.write_text(
        "from types import SimpleNamespace\n"
        "def get_tools():\n"
        "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n",
        encoding="utf-8")
    os.link(required, optional)
    env.bindings.update({REQUIRED: required, OPTIONAL: optional})
    import_module(REQUIRED)
    assert mcp.build_server().name == "g6"
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    rows = env.families.inventory(family)
    assert next(row for row in rows if row["id"] == "mcp:abstraction/load_state")["available"] is True
    assert os.path.samefile(sys.modules[REQUIRED].__file__, sys.modules[OPTIONAL].__file__)
    if probe_alias:
        assert env.deps.optional_import_roots("abstraction") == frozenset()
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    required.unlink()
    optional.unlink()
    invalidate_caches()
    server = import_module("rfu41_server")
    try:
        rows = env.families.inventory(family)
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
    else:
        member = next(row for row in rows if row["id"] == "mcp:abstraction/load_state")
        pytest.fail(f"healthy family discovery lost its loaded required alias: {member}")
