"""Offline, real-disk review 3 reproductions; no source repairs."""
from importlib import _bootstrap_external, import_module, invalidate_caches, metadata, reload, util
from collections import Counter
import os
from pathlib import Path
import py_compile
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu53_required"
OPTIONAL = "rfu53_optional"
TOOLS = (
    "from types import SimpleNamespace\n"
    "def get_tools():\n"
    "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n"
)


@pytest.fixture
def env(monkeypatch):
    # Avoid pytest's mode-0700 temporary directories in the Windows sandbox.
    tmp_path = Path(__file__).resolve().parent / "_disk" / uuid4().hex
    tmp_path.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(tmp_path / "home"),
        "FASTMCP_HOME": str(tmp_path / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull,
        "FASTMCP_CHECK_FOR_UPDATES": "off",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies as deps, families, registrations

    needs = {
        "g6": ['rfu53-base; extra == "mcp"',
               'rfu53-extra; extra == "abstraction"'],
        "rfu53-base": [], "rfu53-extra": [],
    }
    roots = {"g6": "g6", "rfu53-base": REQUIRED, "rfu53-extra": OPTIONAL}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu53_server:build"})
    (tmp_path / "rfu53_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu53_optional')\n"
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
        if name.startswith("rfu53_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def start(env):
    from g6 import mcp

    source = env.disk / "required.py"
    source.write_text(TOOLS, encoding="utf-8")
    env.bindings[REQUIRED] = source
    loaded = import_module(REQUIRED)
    loaded.__cached__ = None
    loaded.__spec__.cached = None
    assert mcp.build_server().name == "g6"
    optional = env.disk / "optional.py"
    optional.write_text(TOOLS, encoding="utf-8")
    env.bindings[OPTIONAL] = optional
    assert env.families.describe("load_state")["available"] is True
    assert "abstraction" in env.families._RECORDED_SERVERS
    return loaded


def discover(env, entry):
    if entry == "cached-describe":
        assert env.families.describe("load_state")["available"] is True
    else:
        family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
        assert any(row["available"] for row in env.families.inventory(family))


def missing_result(env):
    family = env.families.family_for({"id": "mcp:abstraction/load_state", "kind": "mcp"})
    try:
        rows = env.families.inventory(family)
    except ModuleNotFoundError as exc:
        assert exc is import_module("rfu53_server").FAILURE
        return "required"
    row = next(r for r in rows if r["id"] == "mcp:abstraction/load_state")
    assert row["available"] is False
    assert "g6[abstraction,mcp]" in row["install"]
    return "optional-install"


def transfer_then_remove(env, source):
    for name in tuple(sys.modules):
        if name == OPTIONAL or name == REQUIRED or name.startswith(REQUIRED + "."):
            sys.modules.pop(name)
    alias = env.disk / ("alias" + source.suffix)
    source.rename(alias)
    env.bindings.clear()
    env.bindings[OPTIONAL] = alias
    discover(env, "inventory-control")
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    alias.unlink()
    (env.disk / "optional.py").unlink()
    invalidate_caches()


@pytest.mark.parametrize("entry", ["cached-describe", "inventory-control"])
def test_new_spec_origin_is_not_covered(env, entry):
    loaded = start(env)
    new_origin = env.disk / "new-origin.py"
    new_origin.write_text(TOOLS, encoding="utf-8")
    original_file = loaded.__file__
    loaded.__spec__ = util.spec_from_file_location(REQUIRED, new_origin)
    loaded.__spec__.cached = None
    loaded.__spec__.loader.exec_module(loaded)
    assert loaded.__file__ == original_file
    assert loaded.get_tools.__code__.co_filename == str(new_origin)
    discover(env, entry)
    transfer_then_remove(env, new_origin)
    assert missing_result(env) == "required", "new required origin received optional install recovery"


@pytest.mark.parametrize("entry", ["cached-describe", "inventory-control"])
def test_absent_bytecode_hint_becomes_a_required_file(env, monkeypatch, entry):
    loaded = start(env)
    cache = Path(util.cache_from_source(loaded.__file__))
    loaded.__cached__ = str(cache)
    loaded.__spec__.cached = str(cache)
    # Establish the excluded, absent hint before it becomes a real loaded cache.
    discover(env, "inventory-control")
    assert "abstraction" in env.families._RECORDED_SERVERS
    assert not cache.exists()
    py_compile.compile(loaded.__file__, cfile=str(cache), doraise=True)
    compile_bytecode = _bootstrap_external._compile_bytecode
    reads = []

    def tracked_bytecode(data, name=None, bytecode_path=None, source_path=None):
        if bytecode_path == str(cache):
            reads.append(bytecode_path)
        return compile_bytecode(data, name=name, bytecode_path=bytecode_path, source_path=source_path)

    with monkeypatch.context() as observation:
        observation.setattr(_bootstrap_external, "_compile_bytecode", tracked_bytecode)
        assert reload(loaded) is loaded
    assert reads, "the required reload must really consume the newly created bytecode"
    discover(env, entry)
    transfer_then_remove(env, cache)
    assert missing_result(env) == "required", "new required bytecode received optional install recovery"


@pytest.mark.parametrize("entry", ["cached-describe", "inventory-control"])
def test_historical_required_spelling_is_not_statted(env, entry):
    start(env)
    source = env.disk / "child.py"
    source.write_text(TOOLS, encoding="utf-8")
    child_name = REQUIRED + ".child"
    spec = util.spec_from_file_location(child_name, source)
    child = util.module_from_spec(spec)
    sys.modules[child_name] = child
    spec.loader.exec_module(child)
    discover(env, "inventory-control")
    sys.modules.pop(child_name)
    # The next complete recording still retains the child's file ownership.
    discover(env, "inventory-control")
    assert any(r[2] == str(source) for r in env.deps._required_paths)
    assert "abstraction" in env.families._RECORDED_SERVERS
    retired = env.disk / "retired.py"
    source.rename(retired)
    source.write_text(TOOLS, encoding="utf-8")
    old_time = retired.stat()
    os.utime(source, ns=(old_time.st_atime_ns, old_time.st_mtime_ns))
    assert source.stat().st_size == retired.stat().st_size
    assert source.stat().st_mtime_ns == retired.stat().st_mtime_ns
    assert env.deps._stat_identity(source) != env.deps._stat_identity(retired)
    discover(env, entry)
    # Remove the replacement before any later discovery can repair retirement.
    source.unlink()
    transfer_then_remove(env, retired)
    assert missing_result(env) == "optional-install", "retired child identity still counted as required"


@pytest.mark.parametrize("invalidate", ["epoch", "generation", "token"])
def test_reuse_rechecks_guards_after_stat_control(env, monkeypatch, invalidate):
    loaded = start(env)
    stat, locations = env.deps._stat_identity, env.deps._loaded_locations
    walks, fired = [], []

    def tracked_locations(module):
        if module is loaded:
            walks.append(True)
        return locations(module)

    def intervening_stat(path):
        status = stat(path)
        if str(path) == loaded.__file__ and not fired:
            fired.append(True)
            if invalidate == "epoch":
                env.deps.optional_import_roots.cache_clear()
            elif invalidate == "generation":
                other = env.disk / "other-optional.py"
                other.write_text(TOOLS, encoding="utf-8")
                sys.modules[OPTIONAL].__file__ = str(other)
                generation = env.deps._ownership_generation
                env.deps.optional_import_roots("abstraction,mcp")
                assert env.deps._ownership_generation > generation
            else:
                with env.deps._ownership_lock:
                    env.deps._loaded_required_recordings.pop("mcp")
        return status

    monkeypatch.setattr(env.deps, "_loaded_locations", tracked_locations)
    monkeypatch.setattr(env.deps, "_stat_identity", intervening_stat)
    discover(env, "cached-describe")
    assert fired and len(walks) == 1


@pytest.mark.parametrize("count", [1, 32])
def test_cached_stat_cost_is_one_per_captured_spelling_control(env, monkeypatch, count):
    start(env)
    for index in range(count):
        source = env.disk / f"child-{index}.py"
        source.write_text(TOOLS, encoding="utf-8")
        name = REQUIRED + f".child{index}"
        spec = util.spec_from_file_location(name, source)
        child = util.module_from_spec(spec)
        sys.modules[name] = child
        spec.loader.exec_module(child)
    discover(env, "inventory-control")
    files = dict(env.families._RECORDED_SERVERS["abstraction"][1][3])
    stat = env.deps._stat_identity
    calls = Counter()

    def counted(path):
        calls[str(path)] += 1
        return stat(path)

    monkeypatch.setattr(env.deps, "_stat_identity", counted)
    discover(env, "cached-describe")
    assert set(calls) == set(files)
    assert all(value == 1 for value in calls.values())


def test_in_place_same_identity_edit_control(env):
    start(env)
    source = env.disk / "required.py"
    before = source.stat()
    source.write_text(TOOLS.replace("Load state.", "Read state."), encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert env.deps._stat_identity(source) == (before.st_dev, before.st_ino)
    discover(env, "cached-describe")
    transfer_then_remove(env, source)
    assert missing_result(env) == "required"
