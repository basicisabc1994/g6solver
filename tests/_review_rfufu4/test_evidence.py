"""Review 4: real-disk startup history, without network or host calls."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfufu4_required"
OPTIONAL = "rfufu4_optional"


@pytest.fixture
def tmp_path():
    # Ordinary mkdir avoids tempfile's restrictive Windows ACL in this sandbox.
    base = Path(__file__).resolve().parents[2] / ".bt_rev_rfufu4"
    base.mkdir(exist_ok=True)
    folder = base / ("evidence-" + uuid4().hex)
    folder.mkdir()
    return folder


@pytest.fixture
def installation(tmp_path, monkeypatch):
    from g6.mcp import dependencies, families, registrations

    needs = {"g6": ["rfufu4-base", 'rfufu4-extra; extra == "abstraction"'],
             "rfufu4-base": [], "rfufu4-extra": []}
    roots = {"g6": "g6", "rfufu4-base": REQUIRED, "rfufu4-extra": OPTIONAL}
    for name, requirements in needs.items():
        folder = tmp_path / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir()
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfufu4_server:build"})
    (tmp_path / "rfufu4_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfufu4_optional')\n"
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
    yield SimpleNamespace(deps=dependencies, families=families, disk=tmp_path, bindings=bindings)
    dependencies.optional_import_roots.cache_clear()
    for name in tuple(sys.modules):
        if name.startswith("rfufu4_"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("explicit_snapshot", [False, True],
                         ids=["successful-startup", "snapshot-control"])
def test_successful_startup_retains_required_file_ownership(installation, monkeypatch, explicit_snapshot):
    from g6 import mcp

    env = installation
    required = env.disk / "required.py"
    optional = env.disk / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    os.link(required, optional)
    env.bindings[OPTIONAL] = optional
    assert import_module(OPTIONAL).value == 73
    assert env.deps.optional_import_roots("abstraction", required_extras="") == {OPTIONAL}
    env.bindings[REQUIRED] = required
    original_quiet = mcp._quiet

    def quiet_with_required_dependency():
        original_quiet()
        assert import_module(REQUIRED).value == 73

    monkeypatch.setattr(mcp, "_quiet", quiet_with_required_dependency)
    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "off")
    monkeypatch.setenv("FASTMCP_ENV_FILE", os.devnull)
    server = mcp.build_server()
    assert server.name == "g6"
    assert import_module(OPTIONAL).value == 73
    assert os.path.samefile(required, optional)
    if explicit_snapshot:
        assert env.deps.optional_import_roots("abstraction,mcp") == frozenset()
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    required.unlink()
    optional.unlink()
    invalidate_caches()
    failed_server = import_module("rfufu4_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        reply = env.families.describe("load_state")
        pytest.fail(f"successful startup lost required ownership: {reply}")
    assert stopped.value is failed_server.FAILURE


@pytest.mark.parametrize("remove_live_peer", [False, True],
                         ids=["split-namespace", "all-locations-absent-control"])
def test_namespace_history_survives_disappearing_location(installation, remove_live_peer):
    import _winapi

    env = installation
    first = env.disk / "first"
    second = env.disk / "second"
    target = env.disk / "target"
    for folder in (first, second, target):
        folder.mkdir()
    required = first / REQUIRED
    optional = first / OPTIONAL
    peer = second / OPTIONAL
    peer.mkdir()
    _winapi.CreateJunction(str(target), str(required))
    _winapi.CreateJunction(str(target), str(optional))
    sys.path[:0] = [str(first), str(second)]
    try:
        req = import_module(REQUIRED)
        opt = import_module(OPTIONAL)
        assert len(list(opt.__path__)) == 2
        assert os.path.samefile(next(iter(req.__path__)), next(iter(opt.__path__)))
        assert env.deps.optional_import_roots("abstraction,mcp") == frozenset()
        identity = (target.stat().st_dev, target.stat().st_ino)
        os.rmdir(optional)
        if remove_live_peer:
            peer.rmdir()
        invalidate_caches()
        # With the peer present, Python drops the vanished namespace location.
        if not remove_live_peer:
            assert list(opt.__path__) == [str(peer)]
        env.deps.optional_import_roots("abstraction,mcp")
        assert (target.stat().st_dev, target.stat().st_ino) == identity
        sys.modules.pop(OPTIONAL)
        if not remove_live_peer:
            peer.rmdir()
        invalidate_caches()
        failed_server = import_module("rfufu4_server")
        with pytest.raises(ModuleNotFoundError) as stopped:
            reply = env.families.describe("load_state")
            pytest.fail(f"vanished namespace ownership became an install hint: {reply}")
        assert stopped.value is failed_server.FAILURE
    finally:
        sys.path.remove(str(first))
        sys.path.remove(str(second))
        for junction in (required, optional):
            if os.path.lexists(junction):
                os.rmdir(junction)
