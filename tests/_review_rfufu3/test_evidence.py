"""Disk-backed review probes; no host, network, model, or API calls."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfufu3_required"
OPTIONAL = "rfufu3_optional"


@pytest.fixture
def installation(monkeypatch):
    from g6.mcp import dependencies, families, registrations

    disk = Path(__file__).resolve().parent / ("_disk_" + uuid4().hex[:12])
    disk.mkdir()
    needs = {"g6": ["rfufu3-base", 'rfufu3-extra; extra == "abstraction"'],
             "rfufu3-base": [], "rfufu3-extra": []}
    roots = {"g6": "g6", "rfufu3-base": REQUIRED, "rfufu3-extra": OPTIONAL}
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
    monkeypatch.setenv("G6_HOME", str(disk / "home"))
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfufu3_server:build"})
    (disk / "rfufu3_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfufu3_optional')\n"
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
        if name.startswith("rfufu3_"):
            sys.modules.pop(name, None)


def assert_family_result(env, required):
    server = import_module("rfufu3_server")
    if required:
        with pytest.raises(ModuleNotFoundError) as stopped:
            reply = env.families.describe("load_state")
            pytest.fail(f"required failure became an install hint: {reply}")
        assert stopped.value is server.FAILURE
    else:
        reply = env.families.describe("load_state")
        assert reply["available"] is False
        assert reply["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in reply["install"]


@pytest.mark.parametrize("change", ["junction-absent", "file-absent", "replacement"])
def test_junction_history_across_scopes_and_threads(installation, change):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir()
    file = target / "shared.py"
    file.write_text("value = 73\n", encoding="utf-8")
    junction = env.disk / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        env.bindings.update({REQUIRED: source, OPTIONAL: source})
        assert import_module(REQUIRED).value == import_module(OPTIONAL).value == 73
        assert env.deps.optional_import_roots("abstraction,mcp") == frozenset()
        for root in (REQUIRED, OPTIONAL):
            sys.modules.pop(root)
        env.bindings.clear()
        if change == "file-absent":
            file.unlink()
        else:
            os.rmdir(junction)
            if change == "replacement":
                replacement = env.disk / "replacement"
                replacement.mkdir()
                new_file = replacement / "shared.py"
                new_file.write_text("value = 'independent install'\n", encoding="utf-8")
                assert not os.path.samefile(file, new_file)
                _winapi.CreateJunction(str(replacement), str(junction))
        scopes = [("abstraction", "mcp"), ("abstraction", "")] * 8

        def discover(scope):
            return env.deps.optional_import_roots(scope[0], required_extras=scope[1])

        expected = {OPTIONAL} if change == "replacement" else frozenset()
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(result == expected for result in pool.map(discover, scopes))
        if junction.exists():
            os.rmdir(junction)
        invalidate_caches()
        assert_family_result(env, required=change != "replacement")
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("clear", [False, True], ids=["retained", "explicit-clear-control"])
def test_hardlink_history_after_disappearance(installation, clear):
    env = installation
    required = env.disk / "required.py"
    optional = env.disk / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    os.link(required, optional)
    env.bindings.update({REQUIRED: required, OPTIONAL: optional})
    assert import_module(REQUIRED).value == import_module(OPTIONAL).value == 73
    assert env.deps.optional_import_roots("abstraction,mcp") == frozenset()
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    required.unlink()
    optional.unlink()
    if clear:
        env.deps.optional_import_roots.cache_clear()
    invalidate_caches()
    assert_family_result(env, required=not clear)


@pytest.mark.parametrize("same_file", [True, False], ids=["same-inode", "independent-file-control"])
def test_junction_retarget_keeps_an_unchanged_file_identity(installation, same_file):
    import _winapi

    env = installation
    target = env.disk / "target"
    replacement = env.disk / "replacement"
    target.mkdir()
    replacement.mkdir()
    file = target / "shared.py"
    other = replacement / "shared.py"
    file.write_text("value = 73\n", encoding="utf-8")
    if same_file:
        os.link(file, other)
    else:
        other.write_text("value = 'independent install'\n", encoding="utf-8")
    assert os.path.samefile(file, other) is same_file
    junction = env.disk / "site"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        source = junction / "shared.py"
        env.bindings.update({REQUIRED: source, OPTIONAL: source})
        assert import_module(REQUIRED).value == import_module(OPTIONAL).value == 73
        assert env.deps.optional_import_roots("abstraction,mcp") == frozenset()
        before = source.stat()
        sys.modules.pop(OPTIONAL)
        env.bindings.pop(OPTIONAL)
        os.rmdir(junction)
        _winapi.CreateJunction(str(replacement), str(junction))
        after = source.stat()
        assert ((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)) is same_file
        sys.modules.pop(REQUIRED)
        assert import_module(REQUIRED).value == (73 if same_file else "independent install")
        invalidate_caches()
        assert util.find_spec(OPTIONAL) is None
        assert_family_result(env, required=same_file)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)
