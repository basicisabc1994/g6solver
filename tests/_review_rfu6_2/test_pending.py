"""Offline real-disk review 2: error discovery must use pending ownership."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu62_required"
OPTIONAL = "rfu62_optional"
TOOLS = (
    "from types import SimpleNamespace\n"
    "def get_tools():\n"
    "    return {'load_state': SimpleNamespace(description='Load state.', parameters={})}\n"
)


@pytest.fixture
def env(monkeypatch):
    disk = Path(__file__).resolve().parents[2] / ".bt_rev_rfu6_2" / "cases" / uuid4().hex
    disk.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull, "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies as deps, families, registrations

    needs = {"g6": ['rfu62-base; extra == "mcp"', 'rfu62-extra; extra == "abstraction"'],
             "rfu62-base": [], "rfu62-extra": ["rfu62-missing"], "rfu62-missing": []}
    roots = {"g6": "g6", "rfu62-base": REQUIRED, "rfu62-extra": OPTIONAL,
             "rfu62-missing": "rfu62_missing"}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu62_server:build"})
    (disk / "rfu62_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\nEXTRA_FAILURE = False\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n"
        "        value = import_module('rfu62_optional')\n"
        "        if EXTRA_FAILURE:\n            import_module('rfu62_missing')\n"
        "        return value\n"
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
        if name.startswith("rfu62_"):
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


@pytest.mark.parametrize("recovery", ["load-fails", "recording-control"])
def test_interrupted_evidence_then_failed_load(env, monkeypatch, recovery):
    loaded = start(env)
    source = Path(loaded.__file__)
    source.rename(env.disk / "retired.py")
    source.write_text(TOOLS, encoding="utf-8")
    saved = env.disk / "saved.py"
    stat = env.deps._stat_identity
    observed = []
    guard_name = REQUIRED + ".guard"
    guard_path = env.disk / "guard.py"
    guard_path.write_text(
        "def __getattr__(name):\n"
        "    if name == '__path__':\n"
        "        raise SystemExit('interrupted before loaded recording')\n"
        "    raise AttributeError(name)\n", encoding="utf-8")

    def probe(path):
        result = stat(path)
        if str(path) == str(source) and not observed:
            observed.append(result)
            source.rename(saved)
            spec = util.spec_from_file_location(guard_name, guard_path)
            guard = util.module_from_spec(spec)
            spec.loader.exec_module(guard)
            sys.modules[guard_name] = guard
        return result

    with monkeypatch.context() as patch:
        patch.setattr(env.deps, "_stat_identity", probe)
        with pytest.raises(SystemExit, match="interrupted before loaded recording"):
            env.families.describe("load_state")
    assert observed == [stat(saved)]
    assert env.deps._pending_reuse_evidence

    sys.modules.pop(guard_name)
    sys.modules.pop(REQUIRED)
    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    env.bindings[OPTIONAL] = saved
    server = import_module("rfu62_server")
    server.EXTRA_FAILURE = recovery == "load-fails"
    result = env.families.describe("load_state")
    assert result["available"] is (recovery == "recording-control")
    if recovery == "load-fails":
        assert server.FAILURE.name == "rfu62_missing"

    sys.modules.pop(OPTIONAL)
    env.bindings.clear()
    saved.unlink()
    (env.disk / "optional.py").unlink()
    server.EXTRA_FAILURE = False
    invalidate_caches()
    try:
        rows = discover(env)
    except ModuleNotFoundError as failure:
        assert failure is server.FAILURE
        assert failure.name == OPTIONAL
        outcome = "required-defect"
    else:
        row = next(row for row in rows if row["id"] == "mcp:abstraction/load_state")
        assert row["available"] is False
        assert "g6[abstraction,mcp]" in row["install"]
        assert any("loaded" in entry[2] for entry in env.deps._pending_reuse_evidence.values())
        outcome = "optional-install"
    assert outcome == "required-defect", "pending required evidence was bypassed: " + outcome
