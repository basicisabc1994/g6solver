"""Review 2 disk reproductions; no hosts, network, or models."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


REQUIRED = "rfufu2_required"
OPTIONAL = "rfufu2_optional"


@pytest.fixture
def installation(request, monkeypatch):
    from g6.mcp import dependencies, families, registrations

    tmp_path = Path(__file__).resolve().parent / ("_disk_" + request.node.name)
    tmp_path.mkdir(exist_ok=True)
    needs = {"g6": ["rfufu2-base", 'rfufu2-extra; extra == "abstraction"'],
             "rfufu2-base": [], "rfufu2-extra": []}
    roots = {"g6": "g6", "rfufu2-base": REQUIRED, "rfufu2-extra": OPTIONAL}
    for name, requirements in needs.items():
        folder = tmp_path / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir(exist_ok=True)
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements), encoding="utf-8")
        (folder / "top_level.txt").write_text(roots[name] + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(tmp_path)])}
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(tmp_path)]))
    monkeypatch.setattr(metadata, "requires", lambda name: records[name].requires)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfufu2_server:build"})
    (tmp_path / "rfufu2_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfufu2_optional')\n"
        "    except ModuleNotFoundError as exc:\n        FAILURE = exc\n        raise\n",
        encoding="utf-8")
    bindings = {}

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            file = bindings.get(fullname)
            if file is not None:
                return util.spec_from_file_location(fullname, file)
            return None

    monkeypatch.setattr(sys, "meta_path", [Finder(), *sys.meta_path])
    dependencies.optional_import_roots.cache_clear()
    invalidate_caches()
    yield SimpleNamespace(deps=dependencies, families=families, disk=tmp_path, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfufu2_"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("remove_junction", [True, False], ids=["junction-removed", "file-removed-control"])
def test_missing_junction_keeps_required_ownership(installation, remove_junction):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir(exist_ok=True)
    file = target / "shared.py"
    file.write_text("value = 73\n", encoding="utf-8")
    junction = env.disk / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        env.bindings.update({REQUIRED: source, OPTIONAL: source})
        assert import_module(REQUIRED).value == import_module(OPTIONAL).value == 73
        assert os.path.samefile(source, file)
        assert env.deps.optional_import_roots("abstraction,mcp") == frozenset()
        for root in (REQUIRED, OPTIONAL):
            sys.modules.pop(root)
        env.bindings.clear()
        if remove_junction:
            os.rmdir(junction)
        else:
            file.unlink()
        invalidate_caches()
        assert not source.exists()
        assert util.find_spec(OPTIONAL) is None
        server = import_module("rfufu2_server")
        with pytest.raises(ModuleNotFoundError) as stopped:
            reply = env.families.describe("load_state")
            pytest.fail(f"required alias hidden as optional: {reply}")
        assert stopped.value is server.FAILURE
    finally:
        if junction.exists():
            os.rmdir(junction)
