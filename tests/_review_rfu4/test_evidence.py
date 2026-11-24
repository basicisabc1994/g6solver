"""Review 4: disk-backed counterexamples; no host, network or model calls."""
from importlib import import_module, invalidate_caches, machinery, metadata, util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from time import perf_counter

import pytest


REQUIRED = "rfu4_required"
OPTIONAL = "rfu4_optional"


@pytest.fixture
def installation(request, monkeypatch):
    from g6.mcp import dependencies, families, registrations

    tmp_path = Path(__file__).resolve().parent / ("_disk_" + request.node.name)
    tmp_path.mkdir(exist_ok=True)
    needs = {"g6": ["rfu4-base", 'rfu4-extra; extra == "abstraction"'],
             "rfu4-base": [], "rfu4-extra": []}
    roots = {"g6": "g6", "rfu4-base": REQUIRED, "rfu4-extra": OPTIONAL}
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
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu4_server:build"})
    (tmp_path / "rfu4_server.py").write_text(
        "from importlib import import_module\n"
        "FAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n"
        "        return import_module('rfu4_optional')\n"
        "    except ModuleNotFoundError as exc:\n"
        "        FAILURE = exc\n"
        "        raise\n", encoding="utf-8")
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
    yield SimpleNamespace(dependencies=dependencies, families=families,
                          disk=tmp_path, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfu4_"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("clear_history", [False, True], ids=["retained", "cleared-control"])
def test_reused_path_does_not_own_a_different_optional_install(installation, clear_history):
    env = installation
    slot = env.disk / "slot.py"
    slot.write_text("value = 'old required install'\n", encoding="utf-8")
    env.bindings[REQUIRED] = slot
    assert import_module(REQUIRED).value == "old required install"
    assert env.dependencies.optional_import_roots("abstraction,mcp") == {OPTIONAL}
    sys.modules.pop(REQUIRED)
    slot.unlink()
    replacement = env.disk / "required_v2.py"
    replacement.write_text("value = 'new required install'\n", encoding="utf-8")
    env.bindings[REQUIRED] = replacement
    assert import_module(REQUIRED).value == "new required install"
    slot.write_text("value = 'unrelated optional install'\n", encoding="utf-8")
    env.bindings[OPTIONAL] = slot
    assert import_module(OPTIONAL).value == "unrelated optional install"
    assert not os.path.samefile(replacement, slot)
    invalidate_caches()
    if clear_history:
        env.dependencies.optional_import_roots.cache_clear()
    # The current optional install is inspected before it becomes unavailable.
    env.dependencies.optional_import_roots("abstraction,mcp")
    sys.modules.pop(OPTIONAL)
    slot.unlink()
    invalidate_caches()
    assert util.find_spec(OPTIONAL) is None
    reply = env.families.describe("load_state")
    assert reply["available"] is False and reply["reason"] == "server_unavailable"


@pytest.mark.parametrize("clear_history", [False, True], ids=["retained", "cleared-control"])
def test_reinstalled_optional_is_not_owned_by_an_old_hardlink(installation, clear_history):
    env = installation
    required = env.disk / (REQUIRED + ".py")
    optional = env.disk / (OPTIONAL + ".py")
    required.write_text("value = 'required'\n", encoding="utf-8")
    os.link(required, optional)
    assert import_module(REQUIRED).value == import_module(OPTIONAL).value == "required"
    assert os.path.samefile(required, optional)
    assert env.dependencies.optional_import_roots("abstraction,mcp") == frozenset()
    sys.modules.pop(OPTIONAL)
    optional.unlink()
    optional.write_text("value = 'independent optional upgrade'\n", encoding="utf-8")
    assert import_module(OPTIONAL).value == "independent optional upgrade"
    assert not os.path.samefile(required, optional)
    invalidate_caches()
    if clear_history:
        env.dependencies.optional_import_roots.cache_clear()
    env.dependencies.optional_import_roots("abstraction,mcp")
    sys.modules.pop(OPTIONAL)
    optional.unlink()
    invalidate_caches()
    reply = env.families.describe("logic")
    assert reply["available"] is True
    members = [row for row in reply["members"] if row["id"].startswith("mcp:abstraction/")]
    assert members and all(row["reason"] == "server_unavailable" for row in members)


@pytest.mark.parametrize("warm_scope", ["startup", "family"], ids=["startup-history", "same-scope-control"])
def test_required_ownership_survives_between_discovery_scopes(installation, monkeypatch, warm_scope):
    from g6 import mcp

    env = installation
    (env.disk / "rfu4_extra-1.0.dist-info" / "top_level.txt").write_text(
        OPTIONAL + "\nrfu4_secondary\n", encoding="utf-8")
    server_file = env.disk / "rfu4_server.py"
    server_file.write_text(server_file.read_text(encoding="utf-8").replace(
        "return import_module('rfu4_optional')",
        "import_module('rfu4_optional')\n        return import_module('rfu4_secondary')"), encoding="utf-8")
    shared = env.disk / "shared.py"
    shared.write_text("value = 73\n", encoding="utf-8")
    env.bindings[REQUIRED] = shared
    assert import_module(REQUIRED).value == 73
    identity = shared.stat().st_ino
    if warm_scope == "startup":
        monkeypatch.setattr(mcp, "_quiet", lambda: import_module("rfu4_bootstrap"))
        with pytest.raises(SystemExit) as stopped:
            mcp.build_server()
        assert stopped.value.__cause__.name == "rfu4_bootstrap"
    else:
        assert env.families.describe("load_state")["reason"] == "server_unavailable"
    sys.modules.pop(REQUIRED)
    del env.bindings[REQUIRED]
    env.bindings[OPTIONAL] = shared
    # The required file is unchanged; an importer now exposes its other name.
    assert import_module(OPTIONAL).value == 73
    assert shared.stat().st_ino == identity
    assert util.find_spec(REQUIRED) is None
    # A second absent optional member causes a real family discovery to inspect
    # the now-loaded alias before its file disappears.
    assert env.families.describe("load_state")["reason"] == "server_unavailable"
    server = import_module("rfu4_server")
    assert server.FAILURE.name == "rfu4_secondary"
    sys.modules.pop(OPTIONAL)
    shared.unlink()
    invalidate_caches()
    assert util.find_spec(OPTIONAL) is None
    with pytest.raises(ModuleNotFoundError) as stopped:
        reply = env.families.describe("load_state")
        pytest.fail(f"required alias was hidden as optional: {reply}")
    assert stopped.value is server.FAILURE


@pytest.mark.parametrize("bad_parent", [True, False], ids=["bad-parent", "package-control"])
def test_cached_parent_check_keeps_original_sdk_export_failure(installation, monkeypatch, bad_parent):
    from g6 import mcp

    env = installation
    project = env.disk / "g6-1.0.dist-info" / "METADATA"
    project.write_text(project.read_text(encoding="utf-8").replace(
        'extra == "abstraction"', 'extra == "mcp"'), encoding="utf-8")
    (env.disk / "rfu4_extra-1.0.dist-info" / "top_level.txt").write_text(
        OPTIONAL + "\nmcp\nfastmcp\n", encoding="utf-8")
    (env.disk / "fastmcp.py").write_text(
        "from types import SimpleNamespace\n"
        "settings = SimpleNamespace(check_for_updates='off')\n"
        "FAILURE = ImportError('broken Context export', name='fastmcp')\n"
        "def __getattr__(name):\n"
        "    if name == 'Context':\n"
        "        raise FAILURE\n"
        "    raise AttributeError(name)\n", encoding="utf-8")
    monkeypatch.delitem(sys.modules, "fastmcp", raising=False)
    invalidate_caches()
    sdk = import_module("fastmcp")
    monkeypatch.setitem(sys.modules, "fastmcp", sdk)
    parent = ModuleType("mcp")
    if not bad_parent:
        parent.__path__ = []
    monkeypatch.setitem(sys.modules, "mcp", parent)
    monkeypatch.setitem(sys.modules, "mcp.server", ModuleType("mcp.server"))
    # Establish the actual failure of the SDK export import independently.
    with pytest.raises(ImportError) as original:
        exec("from fastmcp import Context, FastMCP", {})
    assert original.value is sdk.FAILURE
    with pytest.raises(SystemExit) as stopped:
        mcp.build_server()
    assert stopped.value.__cause__ is sdk.FAILURE, (repr(stopped.value.__cause__), str(stopped.value))
    assert "MCP extra" not in str(stopped.value)


@pytest.mark.parametrize("kind", ["namespace", "changed-path", "getter-import", "getter-runtime", "relative"])
def test_additional_startup_controls(installation, monkeypatch, kind):
    from g6 import mcp

    env = installation
    package = env.disk / "parent"
    package.mkdir(exist_ok=True)
    (package / "server.py").write_text("value = 73\n", encoding="utf-8")
    spec = machinery.ModuleSpec("mcp", loader=None, is_package=True)
    spec.submodule_search_locations = [str(package)]
    parent = util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "mcp", parent)
    monkeypatch.delitem(sys.modules, "mcp.server", raising=False)
    failure = ImportError("parent dependency defect", name=REQUIRED)
    if kind == "relative":
        parent.FAILURE = failure
        (package / "server.py").write_text("from . import FAILURE\nraise FAILURE\n", encoding="utf-8")
        monkeypatch.setattr(mcp, "_quiet", lambda: import_module("mcp.server"))
        with pytest.raises(SystemExit) as stopped:
            mcp.build_server()
        assert stopped.value.__cause__ is failure
        assert "MCP extra" not in str(stopped.value)
        return
    child = import_module("mcp.server")
    assert child.value == 73
    if kind in {"namespace", "changed-path"}:
        if kind == "changed-path":
            other = env.disk / "other_parent"
            other.mkdir(exist_ok=True)
            parent.__path__ = [str(other)]
        mcp._check_cached_import_parents("mcp.server")
        assert import_module("mcp.server") is child
        return
    if kind == "getter-runtime":
        failure = RuntimeError("parent attribute defect")

    def access(name):
        if name == "__path__":
            raise failure
        raise AttributeError(name)

    del parent.__path__
    parent.__getattr__ = access
    monkeypatch.setattr(mcp, "_quiet", lambda: None)
    with pytest.raises(SystemExit if kind == "getter-import" else RuntimeError) as stopped:
        mcp.build_server()
    observed = stopped.value.__cause__ if kind == "getter-import" else stopped.value
    assert observed is failure


def test_repeated_discovery_cost_control(installation):
    env = installation
    common = env.disk / "common.py"
    common.write_text("value = 73\n", encoding="utf-8")
    roots = [f"rfu4_alias_{index}" for index in range(128)]
    for root in roots:
        file = env.disk / (root + ".py")
        if not file.exists():
            os.link(common, file)
    (env.disk / "rfu4_base-1.0.dist-info" / "top_level.txt").write_text(
        "\n".join(roots) + "\n", encoding="utf-8")
    invalidate_caches()
    start = perf_counter()
    for _ in range(20):
        reply = env.families.describe("load_state")
        assert reply["available"] is False and reply["reason"] == "server_unavailable"
    print(f"128 required aliases, 20 discoveries: {perf_counter() - start:.3f}s")
