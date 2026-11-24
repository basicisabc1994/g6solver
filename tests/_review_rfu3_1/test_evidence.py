"""Real-disk review of successful-startup ownership and replacement identity."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

REQUIRED = "rfu31_required"
OPTIONAL = "rfu31_optional"


@pytest.fixture
def installation(monkeypatch):
    from g6 import mcp
    from g6.mcp import dependencies, families, registrations

    disk = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    disk.mkdir(parents=True)
    monkeypatch.setenv("G6_HOME", str(disk / "home"))
    monkeypatch.setenv("FASTMCP_HOME", str(disk / "fastmcp"))
    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "off")
    monkeypatch.setenv("FASTMCP_ENV_FILE", os.devnull)
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    mcp._quiet()
    needs = {"g6": ["rfu31-base", 'rfu31-extra; extra == "abstraction"'],
             "rfu31-base": [], "rfu31-extra": []}
    roots = {"g6": "g6", "rfu31-base": REQUIRED, "rfu31-extra": OPTIONAL}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu31_server:build"})
    (disk / "rfu31_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu31_optional')\n"
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
    yield SimpleNamespace(mcp=mcp, deps=dependencies, families=families, disk=disk, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu31_"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("remove_junction", [True, False],
                         ids=["missing-required-source", "live-source-control"])
def test_replaced_optional_file_is_not_owned_by_stale_resolved_path(installation, remove_junction):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir()
    junction = env.disk / "required-site"
    _winapi.CreateJunction(str(target), str(junction))
    shared = target / "shared.py"
    required = junction / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    env.bindings.update({REQUIRED: required, OPTIONAL: shared})
    try:
        assert import_module(OPTIONAL).value == 73
        # Warm only optional ownership, before the required root is discoverable.
        env.bindings.pop(REQUIRED)
        assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
        env.bindings[REQUIRED] = required
        assert import_module(REQUIRED).value == 73
        assert env.mcp.build_server().name == "g6"
        sys.modules.pop(REQUIRED)
        sys.modules.pop(OPTIONAL)
        env.bindings.pop(REQUIRED)
        if remove_junction:
            os.rmdir(junction)
        old = target / "old-required.py"
        shared.rename(old)
        shared.write_text("value = 'independent optional replacement'\n", encoding="utf-8")
        assert (old.stat().st_dev, old.stat().st_ino) != (shared.stat().st_dev, shared.stat().st_ino)
        # A positive, different stat identity must supersede a mere path match.
        env.deps.optional_import_roots("abstraction")
        assert import_module(OPTIONAL).value == "independent optional replacement"
        sys.modules.pop(OPTIONAL)
        shared.unlink()
        env.bindings.clear()
        invalidate_caches()
        failed_server = import_module("rfu31_server")
        try:
            reply = env.families.describe("load_state")
        except ModuleNotFoundError as exc:
            assert exc is failed_server.FAILURE
            pytest.fail("independent optional replacement retained required ownership from a stale resolved path")
        assert reply["available"] is False
        assert "g6[abstraction,mcp]" in reply["install"]
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("race", [True, False], ids=["unload-during-snapshot", "unload-before-snapshot-control"])
def test_startup_does_not_claim_an_unloaded_replacement(installation, monkeypatch, race):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    env = installation
    loaded_file = env.disk / "actually-loaded.py"
    replacement = env.disk / "never-loaded-required.py"
    loaded_file.write_text("value = 'loaded required'\n", encoding="utf-8")
    replacement.write_text("value = 'optional only'\n", encoding="utf-8")
    env.bindings[OPTIONAL] = replacement
    assert import_module(OPTIONAL).value == "optional only"
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
    env.bindings[REQUIRED] = loaded_file
    assert import_module(REQUIRED).value == "loaded required"
    filtered = Event()
    swapped = Event()
    original = env.deps._without_required_aliases

    def unload_and_repoint():
        sys.modules.pop(REQUIRED)
        env.bindings[REQUIRED] = replacement
        swapped.set()

    def pause_after_loaded_name_filter(optional, required):
        if not optional and REQUIRED in required:
            filtered.set()
            assert swapped.wait(10), "replacement thread did not finish"
        return original(optional, required)

    monkeypatch.setattr(env.deps, "_without_required_aliases", pause_after_loaded_name_filter)
    if race:
        def replace_from_other_thread():
            assert filtered.wait(10), "startup never reached ownership snapshot"
            unload_and_repoint()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(replace_from_other_thread)
            assert env.mcp.build_server().name == "g6"
            future.result(timeout=10)
    else:
        compact = env.families.compact_server

        def unload_at_registration_end(server, server_id):
            compact(server, server_id)
            unload_and_repoint()

        monkeypatch.setattr(env.families, "compact_server", unload_at_registration_end)
        assert env.mcp.build_server().name == "g6"
    assert REQUIRED not in sys.modules
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    loaded_file.unlink()
    replacement.unlink()
    invalidate_caches()
    failed_server = import_module("rfu31_server")
    try:
        reply = env.families.describe("load_state")
    except ModuleNotFoundError as exc:
        assert exc is failed_server.FAILURE
        pytest.fail("startup claimed a replacement that was never loaded as a required root")
    assert reply["available"] is False
    assert "g6[abstraction,mcp]" in reply["install"]


@pytest.fixture
def tmp_path():
    # Avoid pytest's private Windows tempfile ACL, which excludes this sandbox.
    folder = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    folder.mkdir(parents=True)
    return folder


@pytest.mark.parametrize("fileless", [True, False], ids=["fileless-required-module", "located-module-control"])
def test_startup_does_not_promote_optional_only_history(installation, monkeypatch, fileless):
    from importlib import machinery
    from types import ModuleType

    env = installation
    records = {d.metadata["Name"]: d for d in metadata.distributions()}
    g6_metadata = Path(records["g6"]._path) / "METADATA"
    g6_metadata.write_text(
        'Metadata-Version: 2.3\nName: g6\nVersion: 1.0\n'
        'Requires-Dist: rfu31-base; extra == "mcp"\n'
        'Requires-Dist: rfu31-extra; extra == "abstraction"\n', encoding="utf-8")
    optional_roots = Path(records["rfu31-extra"]._path) / "top_level.txt"
    optional_roots.write_text(REQUIRED + "\n" + OPTIONAL + "\n", encoding="utf-8")
    env.deps.optional_import_roots.cache_clear()
    old_optional = env.disk / "old-optional.py"
    actual_required = env.disk / "actual-required.py"
    old_optional.write_text("value = 'optional only'\n", encoding="utf-8")
    actual_required.write_text("value = 'actual required'\n", encoding="utf-8")
    env.bindings.update({REQUIRED: old_optional, OPTIONAL: old_optional})
    assert import_module(OPTIONAL).value == "optional only"
    assert REQUIRED not in sys.modules
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {REQUIRED, OPTIONAL}
    env.bindings.pop(REQUIRED)
    if fileless:
        loaded = ModuleType(REQUIRED)
        loaded.__spec__ = machinery.ModuleSpec(REQUIRED, loader=None, origin="frozen")
        monkeypatch.setitem(sys.modules, REQUIRED, loaded)
    else:
        env.bindings[REQUIRED] = actual_required
        assert import_module(REQUIRED).value == "actual required"
    assert env.mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    old_optional.unlink()
    actual_required.unlink()
    invalidate_caches()
    # This later scope has no MCP-required roots: only startup's actual file
    # evidence may exclude OPTIONAL. Its file was never loaded as required.
    eligible = env.deps.optional_import_roots("abstraction", required_extras="")
    assert OPTIONAL in eligible, "startup promoted optional-only historical files without loaded required file evidence"
