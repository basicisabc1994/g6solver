"""Offline, real-disk checks of loaded-location ownership."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import textwrap
from types import ModuleType, SimpleNamespace
from uuid import uuid4
import zipfile

import pytest


REQUIRED = "rfu32_required"
OPTIONAL = "rfu32_optional"


@pytest.fixture
def installation(monkeypatch):
    from g6 import mcp
    from g6.mcp import dependencies, families, registrations

    disk = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    disk.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_CHECK_FOR_UPDATES": "off", "FASTMCP_ENV_FILE": os.devnull,
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    mcp._quiet()
    needs = {"g6": ["rfu32-base", 'rfu32-extra; extra == "abstraction"'],
             "rfu32-base": [], "rfu32-extra": []}
    roots = {"g6": "g6", "rfu32-base": REQUIRED, "rfu32-extra": OPTIONAL}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu32_server:build"})
    (disk / "rfu32_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu32_optional')\n"
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
    yield SimpleNamespace(mcp=mcp, deps=dependencies, families=families,
                          disk=disk, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu32_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def assert_required_failure(env):
    failed_server = import_module("rfu32_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        reply = env.families.describe("load_state")
        pytest.fail(f"required file was downgraded to optional recovery: {reply}")
    assert stopped.value is failed_server.FAILURE


@pytest.mark.parametrize("bytecode", [False, True], ids=["source", "sourceless-pyc"])
def test_startup_keeps_hardlink_identity(installation, bytecode):
    env = installation
    source = env.disk / "source.py"
    source.write_text("value = 73\n", encoding="utf-8")
    required = source
    if bytecode:
        required = env.disk / "required.pyc"
        py_compile.compile(str(source), cfile=str(required), doraise=True)
        source.unlink()
    optional = env.disk / ("optional.pyc" if bytecode else "optional.py")
    os.link(required, optional)
    env.bindings[OPTIONAL] = optional
    assert import_module(OPTIONAL).value == 73
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
    env.bindings[REQUIRED] = required
    assert import_module(REQUIRED).value == 73
    assert env.mcp.build_server().name == "g6"
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    required.unlink()
    optional.unlink()
    invalidate_caches()
    assert_required_failure(env)


@pytest.mark.parametrize("unhashable", [True, False], ids=["pathlike", "string-control"])
def test_loaded_pathlike_file_is_recorded(installation, monkeypatch, unhashable):
    env = installation
    file = env.disk / "actual.py"
    file.write_text("value = 73\n", encoding="utf-8")
    env.bindings[OPTIONAL] = file
    assert import_module(OPTIONAL).value == 73
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}

    class FileLocation(os.PathLike):
        __hash__ = None

        def __fspath__(self):
            return str(file)

    loaded = ModuleType(REQUIRED)
    loaded.__file__ = FileLocation() if unhashable else str(file)
    monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    observed = set()
    _, certain = env.deps._root_identities(REQUIRED, observed)
    assert certain and observed  # The existing identity probe accepts this location.
    assert any(record[4:] == (file.stat().st_dev, file.stat().st_ino) for record in observed)
    assert env.mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    file.unlink()
    invalidate_caches()
    assert_required_failure(env)


def test_zip_members_do_not_claim_unrelated_disk_file(installation, monkeypatch):
    env = installation
    archive = env.disk / "modules.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(REQUIRED + ".py", "value = 73\n")
    monkeypatch.syspath_prepend(str(archive))
    loaded = import_module(REQUIRED)
    assert loaded.value == 73
    assert ".zip" in loaded.__file__
    optional = env.disk / "independent.py"
    optional.write_text("value = 74\n", encoding="utf-8")
    env.bindings[OPTIONAL] = optional
    assert import_module(OPTIONAL).value == 74
    assert env.mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    optional.unlink()
    invalidate_caches()
    reply = env.families.describe("load_state")
    assert reply["available"] is False
    assert "g6[abstraction,mcp]" in reply["install"]


@pytest.mark.parametrize("lazy", [True, False], ids=["lazy-loader", "eager-control"])
def test_startup_does_not_deadlock_with_loading_required_module(installation, lazy):
    env = installation
    source = env.disk / "concurrent.py"
    source.write_text(
        "from rfu32_support import entered, release, deps\n"
        "entered.set()\n"
        "assert release.wait(10)\n"
        "print('loader entering classification', flush=True)\n"
        "deps.optional_import_roots('abstraction')\n"
        "value = 73\n", encoding="utf-8")
    program = textwrap.dedent(r'''
        from importlib import util
        import sys
        from threading import Event, Thread
        from types import ModuleType
        import faulthandler
        from g6 import mcp
        from g6.mcp import dependencies as deps

        mcp._quiet()
        faulthandler.dump_traceback_later(8)
        root = "rfu32_required"
        # Metadata is not the subject of this lock-order reproduction.
        deps._optional_root_declarations = lambda *args: (set(), {root})
        support = ModuleType("rfu32_support")
        support.entered, support.release, support.deps = Event(), Event(), deps
        sys.modules[support.__name__] = support
        spec = util.spec_from_file_location(root, sys.argv[1])
        eager_loader = spec.loader
        lazy = sys.argv[2] == "True"
        if lazy:
            spec.loader = util.LazyLoader(eager_loader)
        module = util.module_from_spec(spec)
        sys.modules[root] = module
        if lazy:
            spec.loader.exec_module(module)
        failures = []

        def load():
            try:
                if lazy:
                    assert module.value == 73
                else:
                    eager_loader.exec_module(module)
            except BaseException as exc:
                failures.append(exc)

        thread = Thread(target=load, daemon=True)
        thread.start()
        assert support.entered.wait(5), "loader did not enter module body"
        original = deps._loaded_locations

        def snapshot(loaded):
            assert deps._ownership_lock._is_owned()
            print("snapshot owns ownership lock", flush=True)
            support.release.set()
            return original(loaded)

        deps._loaded_locations = snapshot
        assert mcp.build_server().name == "g6"
        thread.join(3)
        assert not thread.is_alive(), "loader remained blocked"
        assert not failures, failures
        print("startup and loader completed", flush=True)
    ''')
    try:
        result = subprocess.run([sys.executable, "-c", program, str(source), str(lazy)],
                                capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        error = exc.stderr or ""
        if isinstance(output, bytes):
            output = output.decode()
        if isinstance(error, bytes):
            error = error.decode()
        (env.disk / "deadlock.txt").write_text(output + error, encoding="utf-8")
        assert "snapshot owns ownership lock" in output
        assert "loader entering classification" in output
        pytest.fail("startup deadlocked: snapshot owns ownership lock; lazy loader waits for that lock")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "startup and loader completed" in result.stdout
