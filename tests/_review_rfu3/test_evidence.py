"""Review 3: live importer aliases must retain required ownership."""
from importlib import import_module, invalidate_caches, machinery, metadata, util
import os
from pathlib import Path
import sys

import pytest


@pytest.fixture
def installation(request, monkeypatch):
    from g6.mcp import dependencies, families, registrations

    tmp_path = Path(__file__).resolve().parent / ("_disk_" + request.node.name)
    tmp_path.mkdir(exist_ok=True)
    roots = {"g6": "g6", "rfu3-base": "rfu3_required",
             "rfu3-extra": "rfu3_optional"}
    needs = {"g6": ["rfu3-base", 'rfu3-extra; extra == "abstraction"'],
             "rfu3-base": [], "rfu3-extra": []}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu3_server:build"})
    monkeypatch.setattr(families, "_DETAILS", {})
    (tmp_path / "rfu3_server.py").write_text(
        "from importlib import import_module\n"
        "FAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n"
        "        return import_module('rfu3_optional')\n"
        "    except ModuleNotFoundError as exc:\n"
        "        FAILURE = exc\n"
        "        raise\n", encoding="utf-8")
    dependencies.optional_import_roots.cache_clear()
    invalidate_caches()
    yield dependencies, families, tmp_path
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith(("RFU3-", "rfu3-", "rfu3_")):
            sys.modules.pop(name, None)


def test_identity_uncertainty_is_retried_for_next_discovery(installation, monkeypatch):
    dependencies, families, disk = installation

    class Finder:
        calls = 0
        failed = False

        def find_spec(self, fullname, path=None, target=None):
            if fullname == "rfu3_optional":
                self.calls += 1
                if self.calls == 2:
                    self.failed = True
                    raise OSError("transient importer lookup failure")
            return None

    finder = Finder()
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    server = import_module("rfu3_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        families.describe("load_state")
    assert stopped.value is server.FAILURE
    assert finder.failed
    assert util.find_spec("rfu3_optional") is None
    failure = None
    try:
        families.describe("load_state")
    except ModuleNotFoundError as exc:
        failure = exc
    dependencies.optional_import_roots.cache_clear()
    reply = families.describe("load_state")
    assert reply["available"] is False and reply["reason"] == "server_unavailable"
    assert failure is None, "a healed identity lookup still fails discovery until the cache is cleared"


def test_cached_optional_roots_cannot_hide_new_required_module_alias(installation, monkeypatch):
    from g6 import mcp

    dependencies, _, disk = installation
    project = disk / "g6-1.0.dist-info" / "METADATA"
    project.write_text(project.read_text(encoding="utf-8").replace(
        'extra == "abstraction"', 'extra == "mcp"'), encoding="utf-8")
    (disk / "rfu3_extra-1.0.dist-info" / "top_level.txt").write_text("mcp\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "mcp", None)
    assert dependencies.optional_import_roots("mcp", required_extras="") == {"mcp"}
    (disk / "rfu3_required.py").write_text(
        "import sys\nsys.modules['mcp'] = sys.modules[__name__]\n", encoding="utf-8")
    invalidate_caches()
    required = import_module("rfu3_required")
    assert sys.modules["mcp"] is required
    assert dependencies._root_identities("mcp")[0] & dependencies._root_identities("rfu3_required")[0]
    monkeypatch.setattr(mcp, "_quiet", lambda: import_module("mcp.server"))
    with pytest.raises(SystemExit) as stopped:
        mcp.build_server()
    assert stopped.value.__cause__.name == "mcp.server"
    dependencies.optional_import_roots.cache_clear()
    assert dependencies.optional_import_roots("mcp", required_extras="") == frozenset()
    assert "MCP extra" not in mcp._import_error_message(stopped.value.__cause__)
    assert "MCP extra" not in str(stopped.value), str(stopped.value)


def test_split_namespace_keeps_each_required_location(installation, monkeypatch):
    dependencies, families, disk = installation
    shared = disk / "shared"
    extra = disk / "extra"
    shared.mkdir(exist_ok=True)
    extra.mkdir(exist_ok=True)
    (shared / "api.py").write_text("value = 73\n", encoding="utf-8")

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname not in {"rfu3_required", "rfu3_optional"} or not shared.exists():
                return None
            spec = machinery.ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [str(shared)]
            if fullname == "rfu3_optional":
                spec.submodule_search_locations.append(str(extra))
            return spec

    monkeypatch.setattr(sys, "meta_path", [Finder(), *sys.meta_path])
    required = import_module("rfu3_required.api")
    optional = import_module("rfu3_optional.api")
    assert required.value == optional.value == 73
    assert os.path.samefile(required.__file__, optional.__file__)
    dependencies.optional_import_roots("abstraction,mcp")
    for name in ("rfu3_required", "rfu3_required.api", "rfu3_optional", "rfu3_optional.api"):
        sys.modules.pop(name, None)
    (shared / "api.py").unlink()
    shared.rmdir()
    extra.rmdir()
    invalidate_caches()
    assert util.find_spec("rfu3_optional") is None
    server = import_module("rfu3_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        families.describe("load_state")
    assert stopped.value is server.FAILURE


def test_spec_origin_does_not_hide_loaded_file_alias(installation, monkeypatch):
    dependencies, families, disk = installation
    required = disk / "rfu3_required.py"
    required.write_text("value = 73\n", encoding="utf-8")
    alias = disk / "rfu3_optional.py"
    if alias.exists():
        alias.unlink()
    os.link(required, alias)
    invalidate_caches()
    loaded = import_module("rfu3_required")
    assert os.path.samefile(loaded.__file__, alias)
    # ModuleSpec and module attributes are independent; relocating a spec does
    # not change the still-established loaded module's file identity.
    loaded.__spec__.origin = str(disk / "relocated.py")
    realpath = dependencies.os.path.realpath

    def resolve(path, *args, **kwargs):
        if path == loaded.__spec__.origin:
            raise OSError("spec-origin path lookup failed")
        return realpath(path, *args, **kwargs)

    monkeypatch.setattr(dependencies.os.path, "realpath", resolve)
    dependencies.optional_import_roots("abstraction,mcp")
    alias.unlink()
    invalidate_caches()
    assert util.find_spec("rfu3_optional") is None
    server = import_module("rfu3_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        families.describe("load_state")
    assert stopped.value is server.FAILURE
