"""Disk-backed follow-up review; no network, host program, or model calls."""
from importlib import import_module, invalidate_caches, metadata, util
import os
import sys
from types import SimpleNamespace

import pytest


REQUIRED = "rfufu1_required"
OPTIONAL = "rfufu1_optional"


@pytest.fixture
def installation(tmp_path, monkeypatch):
    from g6.mcp import dependencies, families, registrations

    needs = {"g6": ["rfufu1-base", 'rfufu1-extra; extra == "abstraction"'],
             "rfufu1-base": [], "rfufu1-extra": []}
    roots = {"g6": "g6", "rfufu1-base": REQUIRED, "rfufu1-extra": OPTIONAL}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfufu1_server:build"})
    (tmp_path / "rfufu1_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfufu1_optional')\n"
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
        if name.startswith("rfufu1_"):
            sys.modules.pop(name, None)


@pytest.mark.parametrize("failed_probe", [True, False], ids=["stat-fails", "healthy-control"])
def test_resolved_required_alias_survives_stat_uncertainty(installation, monkeypatch, failed_probe):
    env = installation
    file = env.disk / "shared.py"
    file.write_text("value = 73\n", encoding="utf-8")
    env.bindings[OPTIONAL] = str(file)
    assert import_module(OPTIONAL).value == 73
    assert env.deps.optional_import_roots("abstraction,mcp") == {OPTIONAL}
    # The optional importer name disappears; required code still loads that file.
    sys.modules.pop(OPTIONAL)
    env.bindings.pop(OPTIONAL)
    (env.disk / "through").mkdir()
    spelling = str(env.disk / "through") + os.sep + ".." + os.sep + "shared.py"
    env.bindings[REQUIRED] = spelling
    required = import_module(REQUIRED)
    assert required.value == 73
    assert os.path.samefile(required.__file__, file)
    assert os.path.realpath(required.__file__) == os.path.realpath(file)
    stat = os.stat

    def transient_stat(path, *args, **kwargs):
        if failed_probe and os.fspath(path) == spelling:
            raise OSError("transient required stat failure")
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", transient_stat)
    server = import_module("rfufu1_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        reply = env.families.describe("load_state")
        pytest.fail(f"required alias hidden as optional: {reply}")
    assert stopped.value is server.FAILURE


@pytest.mark.parametrize("failed_probe", [True, False], ids=["realpath-fails", "healthy-control"])
def test_established_inode_alias_survives_realpath_uncertainty(installation, monkeypatch, failed_probe):
    env = installation
    required = env.disk / "required.py"
    optional = env.disk / "optional.py"
    required.write_text("value = 73\n", encoding="utf-8")
    os.link(required, optional)
    env.bindings.update({REQUIRED: str(required), OPTIONAL: str(optional)})
    assert import_module(REQUIRED).value == import_module(OPTIONAL).value == 73
    assert os.path.samefile(required, optional)
    identity = optional.stat()
    realpath = os.path.realpath

    def transient_realpath(path, *args, **kwargs):
        if failed_probe and os.fspath(path) == str(optional):
            raise OSError("transient optional realpath failure")
        return realpath(path, *args, **kwargs)

    with monkeypatch.context() as transient:
        transient.setattr(os.path, "realpath", transient_realpath)
        # Even with the failed probe, the successful stat establishes the alias.
        identities, _ = env.deps._root_identities(OPTIONAL)
        assert ("inode", identity.st_dev, identity.st_ino) in identities
        assert env.deps.optional_import_roots("abstraction,mcp") == frozenset()
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root)
    env.bindings.clear()
    required.unlink()
    optional.unlink()
    invalidate_caches()
    server = import_module("rfufu1_server")
    with pytest.raises(ModuleNotFoundError) as stopped:
        reply = env.families.describe("load_state")
        pytest.fail(f"established required inode hidden as optional: {reply}")
    assert stopped.value is server.FAILURE
