"""Review 4: offline, real-disk ownership evidence and bounded interleavings."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from threading import Event, Thread, current_thread
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu34_required"
OPTIONAL = "rfu34_optional"
OTHER = "rfu34_other"


@pytest.fixture
def installation(monkeypatch):
    from g6.mcp import dependencies, families, registrations

    disk = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    disk.mkdir(parents=True)
    monkeypatch.setenv("G6_HOME", str(disk / "home"))
    monkeypatch.setenv("FASTMCP_HOME", str(disk / "fastmcp"))
    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "off")
    monkeypatch.setenv("FASTMCP_ENV_FILE", os.devnull)
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    needs = {"g6": ['rfu34-base; extra == "mcp"',
                    'rfu34-extra; extra == "abstraction"',
                    'rfu34-other; extra == "other"'],
             "rfu34-base": [], "rfu34-extra": [], "rfu34-other": []}
    roots = {"g6": "g6", "rfu34-base": REQUIRED,
             "rfu34-extra": OPTIONAL, "rfu34-other": OTHER}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu34_server:build"})
    (disk / "rfu34_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu34_optional')\n"
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
        if name.startswith("rfu34_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def assert_family_result(env, required):
    server = import_module("rfu34_server")
    if required:
        with pytest.raises(ModuleNotFoundError) as stopped:
            reply = env.families.describe("load_state")
            pytest.fail(f"required defect became an install hint: {reply}")
        assert stopped.value is server.FAILURE
    else:
        try:
            reply = env.families.describe("load_state")
        except ModuleNotFoundError as exc:
            assert exc is server.FAILURE
            pytest.fail("retired required identity still blocks optional recovery")
        assert reply["available"] is False
        assert reply["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in reply["install"]


@pytest.mark.parametrize("overlap", [True, False], ids=["unrelated-transaction", "serial-control"])
def test_successful_startup_cannot_lose_owner_to_unrelated_transaction(installation, monkeypatch, overlap):
    from g6 import mcp

    env = installation
    shared = env.disk / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    env.bindings[OPTIONAL] = shared
    assert import_module(OPTIONAL).value == 73
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
    env.bindings[REQUIRED] = shared
    assert import_module(REQUIRED).value == 73
    observed, resume = Event(), Event()
    refresh = env.deps._path_refresh_observations
    results, failures = [], []

    def pause(paths):
        result = refresh(paths)
        if current_thread().name == "rfu34-startup":
            observed.set()
            assert resume.wait(15), "startup was not released"
        return result

    def start():
        try:
            results.append(mcp.build_server())
        except BaseException as exc:
            failures.append(exc)

    worker = None
    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_path_refresh_observations", pause)
        try:
            if overlap:
                worker = Thread(target=start, name="rfu34-startup", daemon=True)
                worker.start()
                assert observed.wait(15), "startup never reached its final snapshot"
            else:
                start()
            # This scope has no required roots and cannot observe the loaded required module.
            generation = env.deps._ownership_generation
            assert env.deps.optional_import_roots("other", required_extras="") == {OTHER}
            assert env.deps._ownership_generation > generation
            resume.set()
            if worker is not None:
                worker.join(15)
                assert not worker.is_alive(), "startup did not finish"
            assert not failures, failures
            assert len(results) == 1 and results[0].name == "g6"
        finally:
            resume.set()
            if worker is not None:
                worker.join(15)
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    shared.unlink()
    invalidate_caches()
    assert_family_result(env, required=True)


@pytest.mark.parametrize("entry", ["discovery", "startup"])
@pytest.mark.parametrize("overlap", [True, False], ids=["between-probes", "serial-control"])
def test_later_replacement_probe_retires_old_owner_in_same_transaction(installation, monkeypatch, entry, overlap):
    env = installation
    required = env.disk / "required.py"
    retired = env.disk / "retired.py"
    optional = env.disk / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    env.bindings[REQUIRED] = required
    assert import_module(REQUIRED).value == 73
    env.deps.record_loaded_required_imports()

    def replace():
        required.rename(retired)
        required.write_text("value = 'replacement'\n", encoding="utf-8")
        assert required.stat().st_ino != retired.stat().st_ino
        os.link(retired, optional)
        env.bindings[OPTIONAL] = optional

    if not overlap:
        replace()
    with monkeypatch.context() as patch:
        if overlap and entry == "discovery":
            observe = env.deps._root_observations

            def replace_after_refresh(roots):
                replace()
                return observe(roots)

            patch.setattr(env.deps, "_root_observations", replace_after_refresh)
        elif overlap:
            refresh = env.deps._path_refresh_observations

            def replace_after_startup_observation(paths):
                replace()
                return refresh(paths)

            patch.setattr(env.deps, "_path_refresh_observations", replace_after_startup_observation)
        if entry == "discovery":
            env.deps.optional_import_roots("abstraction")
        else:
            env.deps.record_loaded_required_imports()
    # No intervening discovery can repair the inconsistent committed transaction.
    sys.modules.pop(REQUIRED)
    env.bindings.pop(REQUIRED)
    required.unlink()
    assert import_module(OPTIONAL).value == 73
    env.deps.optional_import_roots("abstraction", required_extras="")
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    optional.unlink()
    retired.unlink()
    invalidate_caches()
    assert_family_result(env, required=False)


@pytest.mark.parametrize("failed_stat", [True, False], ids=["stat-fails", "healthy-control"])
def test_same_file_retarget_does_not_retire_owner_when_source_stat_fails(installation, monkeypatch, failed_stat):
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
    try:
        env.bindings.update({REQUIRED: source, OPTIONAL: source})
        assert import_module(REQUIRED).value == import_module(OPTIONAL).value == 73
        assert env.deps.optional_import_roots("abstraction") == frozenset()
        identity = (source.stat().st_dev, source.stat().st_ino)
        for root in (REQUIRED, OPTIONAL):
            sys.modules.pop(root)
        env.bindings.clear()
        os.rmdir(junction)
        _winapi.CreateJunction(str(second), str(junction))
        assert identity == (source.stat().st_dev, source.stat().st_ino)
        stat = os.stat

        def unavailable_stat(path, *args, **kwargs):
            if failed_stat and os.fspath(path) == str(source):
                raise OSError("transient identity probe failure")
            return stat(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(os, "stat", unavailable_stat)
            env.deps.optional_import_roots("abstraction", required_extras="")
        os.rmdir(junction)
        invalidate_caches()
        assert_family_result(env, required=True)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("entry", ["startup", "discovery"])
@pytest.mark.parametrize("spelling", ["frozen", "./frozen"], ids=["sentinel-named-directory", "path-control"])
def test_real_namespace_directory_is_not_a_spec_origin_sentinel(installation, monkeypatch, entry, spelling):
    import _winapi

    env = installation
    monkeypatch.chdir(env.disk)
    shared = env.disk / "frozen"
    shared.mkdir()
    (shared / "api.py").write_text("value = 73\n", encoding="utf-8")
    optional = env.disk / OPTIONAL
    _winapi.CreateJunction(str(shared), str(optional))
    required = env.disk / REQUIRED
    required.mkdir()
    (required / "__init__.py").write_text(f"__path__ = [{spelling!r}]\n", encoding="utf-8")
    try:
        module = import_module(OPTIONAL)
        assert os.path.samefile(next(iter(module.__path__)), shared)
        assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
        assert import_module(REQUIRED).__path__ == [spelling]
        assert import_module(REQUIRED + ".api").value == import_module(OPTIONAL + ".api").value == 73
        if entry == "startup":
            env.deps.record_loaded_required_imports()
        else:
            env.deps.optional_import_roots("abstraction")
        for root in (REQUIRED, OPTIONAL, REQUIRED + ".api", OPTIONAL + ".api"):
            sys.modules.pop(root)
        os.rmdir(optional)
        (shared / "api.py").unlink()
        shared.rmdir()
        invalidate_caches()
        assert_family_result(env, required=True)
    finally:
        if os.path.lexists(optional):
            os.rmdir(optional)
