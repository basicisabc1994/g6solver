"""Review 3: offline real-disk ownership races and foreign path values."""
from importlib import import_module, invalidate_caches, machinery, metadata, util
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from threading import Event, Thread
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu33_required"
OPTIONAL = "rfu33_optional"


@pytest.fixture(autouse=True)
def no_real_host_program(monkeypatch):
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")


@pytest.fixture
def installation(monkeypatch):
    from g6.mcp import dependencies, families, registrations

    disk = Path(__file__).resolve().parents[2] / ".bt_rev_rfu3_3" / uuid4().hex
    disk.mkdir(parents=True)
    monkeypatch.setenv("G6_HOME", str(disk / "home"))
    needs = {"g6": ['rfu33-base; extra == "mcp"',
                    'rfu33-extra; extra == "abstraction"'],
             "rfu33-base": [], "rfu33-extra": []}
    roots = {"g6": "g6", "rfu33-base": REQUIRED, "rfu33-extra": OPTIONAL}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu33_server:build"})
    (disk / "rfu33_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu33_optional')\n"
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
    yield SimpleNamespace(deps=dependencies, families=families, disk=disk,
                          bindings=bindings, records=records)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu33_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def assert_optional_recovery(env):
    server = import_module("rfu33_server")
    try:
        reply = env.families.describe("load_state")
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
        pytest.fail("stale required evidence made an optional member propagate its missing-module error")
    assert reply["available"] is False
    assert "g6[abstraction,mcp]" in reply["install"]


@pytest.mark.parametrize("overlap", [True, False], ids=["stale-apply", "serial-control"])
def test_retired_identity_cannot_be_restored_by_older_observation(installation, monkeypatch, overlap):
    env = installation
    required = env.disk / "required.py"
    old = env.disk / "retired.py"
    optional = env.disk / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    env.bindings[REQUIRED] = required
    assert import_module(REQUIRED).value == 73
    env.deps.record_loaded_required_imports()
    observed, resume = Event(), Event()
    original = env.deps._root_observations
    results, failures = [], []

    def pause(roots):
        snapshot = original(roots)
        if REQUIRED in roots:
            observed.set()
            assert resume.wait(10), "review did not release the observation"
        return snapshot

    def discover():
        try:
            results.append(env.deps.optional_import_roots("abstraction"))
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(env.deps, "_root_observations", pause)
    worker = None
    try:
        if overlap:
            worker = Thread(target=discover, daemon=True)
            worker.start()
            assert observed.wait(10)
        else:
            resume.set()
            discover()
        sys.modules.pop(REQUIRED)
        env.bindings.pop(REQUIRED)
        required.rename(old)
        required.write_text("value = 'replacement'\n", encoding="utf-8")
        assert old.stat().st_ino != required.stat().st_ino
        os.link(old, optional)
        env.bindings[OPTIONAL] = optional
        assert import_module(OPTIONAL).value == 73
        # This scope sees the new stat identity and retires required.py's old ownership.
        assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
        required.unlink()  # Subsequent absence must retain the retirement, not revive it.
        resume.set()
        if worker is not None:
            worker.join(10)
            assert not worker.is_alive(), "discovery did not finish"
        assert not failures, failures
        assert len(results) == 1
        sys.modules.pop(OPTIONAL)
        env.bindings.clear()
        optional.unlink()
        old.unlink()
        invalidate_caches()
        assert_optional_recovery(env)
    finally:
        resume.set()
        if worker is not None:
            worker.join(10)


@pytest.mark.parametrize("overlap", [True, False], ids=["clear-during-observation", "serial-control"])
def test_cache_clear_cannot_be_undone_by_preclear_observations(installation, monkeypatch, overlap):
    env = installation
    file = env.disk / "shared.py"
    file.write_text("value = 73\n", encoding="utf-8")
    env.bindings.update({REQUIRED: file, OPTIONAL: file})
    assert import_module(REQUIRED).value == 73
    assert import_module(OPTIONAL).value == 73
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
    observed, resume = Event(), Event()
    original = env.deps._root_observations
    failures = []

    def pause(roots):
        snapshot = original(roots)
        observed.set()
        assert resume.wait(10)
        return snapshot

    def discover():
        try:
            env.deps.optional_import_roots("abstraction")
        except BaseException as exc:
            failures.append(exc)

    worker = None
    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_root_observations", pause)
        try:
            if overlap:
                worker = Thread(target=discover, daemon=True)
                worker.start()
                assert observed.wait(10)
            else:
                resume.set()
                discover()
            # The refreshed declaration no longer assigns any required owner.
            (Path(env.records["g6"]._path) / "METADATA").write_text(
                'Metadata-Version: 2.3\nName: g6\nVersion: 1.0\n'
                'Requires-Dist: rfu33-extra; extra == "abstraction"\n', encoding="utf-8")
            sys.modules.pop(REQUIRED)
            env.bindings.pop(REQUIRED)
            env.deps.optional_import_roots.cache_clear()
            resume.set()
            if worker is not None:
                worker.join(10)
                assert not worker.is_alive()
            assert not failures, failures
        finally:
            resume.set()
            if worker is not None:
                worker.join(10)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    file.unlink()
    invalidate_caches()
    assert_optional_recovery(env)


@pytest.mark.parametrize("overlap", [True, False], ids=["stale-apply", "serial-control"])
def test_older_optional_observation_cannot_erase_new_required_alias(installation, monkeypatch, overlap):
    env = installation
    optional = env.disk / "optional.py"
    required = env.disk / "required.py"
    optional.write_text("value = 'optional only'\n", encoding="utf-8")
    env.bindings[OPTIONAL] = optional
    assert import_module(OPTIONAL).value == "optional only"
    observed, resume = Event(), Event()
    original = env.deps._root_observations
    failures = []

    def pause(roots):
        snapshot = original(roots)
        if REQUIRED in roots:
            observed.set()
            assert resume.wait(10)
        return snapshot

    def discover():
        try:
            env.deps.optional_import_roots("abstraction")
        except BaseException as exc:
            failures.append(exc)

    worker = None
    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_root_observations", pause)
        try:
            if overlap:
                worker = Thread(target=discover, daemon=True)
                worker.start()
                assert observed.wait(10)
            else:
                resume.set()
                discover()
            sys.modules.pop(OPTIONAL)
            old = env.disk / "old-optional.py"
            optional.rename(old)
            optional.write_text("value = 'now required'\n", encoding="utf-8")
            assert old.stat().st_ino != optional.stat().st_ino
            os.link(optional, required)
            env.bindings[REQUIRED] = required
            assert import_module(REQUIRED).value == "now required"
            assert import_module(OPTIONAL).value == "now required"
            env.deps.record_loaded_required_imports()
            # The later discovery records this optional spelling as a required alias.
            assert env.deps.optional_import_roots("abstraction", required_extras="") == frozenset()
            for root in (REQUIRED, OPTIONAL):
                sys.modules.pop(root)
            env.bindings.clear()
            required.unlink()
            optional.unlink()
            old.unlink()
            invalidate_caches()
            resume.set()
            if worker is not None:
                worker.join(10)
                assert not worker.is_alive()
            assert not failures, failures
        finally:
            resume.set()
            if worker is not None:
                worker.join(10)
    server = import_module("rfu33_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        reply = env.families.describe("load_state")
        pytest.fail(f"required defect was downgraded to optional recovery: {reply}")
    assert stopped.value is server.FAILURE


@pytest.mark.parametrize("foreign_string", [True, False], ids=["str-subclass", "plain-str-control"])
def test_startup_path_hash_cannot_run_import_code_under_lock(installation, foreign_string):
    env = installation
    source = env.disk / "actual.py"
    source.write_text("value = 73\n", encoding="utf-8")
    (env.disk / "rfu33_foreign_provider.py").write_text(
        "from rfu33_support import entered, release, deps\n"
        "entered.set()\nassert release.wait(8)\n"
        "print('provider entering classification', flush=True)\n"
        "deps.optional_import_roots('abstraction')\nvalue = 73\n", encoding="utf-8")
    program = textwrap.dedent(r'''
        from importlib import import_module
        from pathlib import Path
        import faulthandler
        import sys
        from threading import Event, Thread
        from types import ModuleType
        from g6.mcp import dependencies as deps

        faulthandler.dump_traceback_later(3)
        source = Path(sys.argv[1])
        sys.path.insert(0, str(source.parent))
        deps._optional_root_declarations = lambda *args: ({"rfu33_optional"}, {"rfu33_required"})
        support = ModuleType("rfu33_support")
        support.entered, support.release, support.deps = Event(), Event(), deps
        sys.modules[support.__name__] = support
        failures = []

        class Location(str):
            def __hash__(self):
                if deps._ownership_lock._is_owned():
                    print('path hash owns ownership lock', flush=True)
                    support.release.set()
                    import_module('rfu33_foreign_provider')
                return super().__hash__()

        loaded = ModuleType("rfu33_required")
        loaded.__file__ = Location(str(source)) if sys.argv[2] == 'True' else str(source)
        sys.modules[loaded.__name__] = loaded

        def load():
            try:
                assert import_module('rfu33_foreign_provider').value == 73
            except BaseException as exc:
                failures.append(exc)

        worker = Thread(target=load, daemon=True)
        worker.start()
        assert support.entered.wait(2)
        deps.record_loaded_required_imports()
        support.release.set()
        worker.join(2)
        assert not worker.is_alive(), 'provider stayed blocked'
        assert not failures, failures
        print('startup and provider completed', flush=True)
    ''')
    try:
        result = subprocess.run([sys.executable, "-c", program, str(source), str(foreign_string)],
                                capture_output=True, text=True, timeout=6)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        error = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        (env.disk / "deadlock.txt").write_text(output + error, encoding="utf-8")
        assert "path hash owns ownership lock" in output
        assert "provider entering classification" in output
        assert "_root_identities" in error and "optional_import_roots" in error
        pytest.fail("startup deadlocked: path __hash__ imports under ownership lock while provider needs that lock")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "startup and provider completed" in result.stdout


@pytest.mark.parametrize("entry", ["raises", "non-string", "bytes", "string-control"])
@pytest.mark.parametrize("scope", ["startup", "discovery"])
def test_invalid_namespace_entry_keeps_later_required_location(installation, monkeypatch, entry, scope):
    env = installation
    shared = env.disk / "namespace"
    shared.mkdir()

    class Invalid(os.PathLike):
        def __fspath__(self):
            if entry == "raises":
                raise ValueError("unusable provider location")
            return 73

    bad = Invalid() if entry in {"raises", "non-string"} else (
        os.fsencode(str(shared)) if entry == "bytes" else str(shared))
    for root, locations in ((REQUIRED, [bad, str(shared)]), (OPTIONAL, [str(shared)])):
        module = ModuleType(root)
        module.__spec__ = machinery.ModuleSpec(root, loader=None, is_package=True)
        module.__spec__.submodule_search_locations = locations
        module.__path__ = locations
        monkeypatch.setitem(sys.modules, root, module)
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
    if scope == "startup":
        env.deps.record_loaded_required_imports()
    else:
        env.deps.optional_import_roots("abstraction")
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    shared.rmdir()
    invalidate_caches()
    server = import_module("rfu33_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        reply = env.families.describe("load_state")
        pytest.fail(f"later valid required namespace location was lost: {reply}")
    assert stopped.value is server.FAILURE
