"""Offline real-disk review of ordered required-file evidence."""
from importlib import import_module, invalidate_caches, metadata, util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


REQUIRED = "rfu43_required"
OPTIONAL = "rfu43_optional"


@pytest.fixture
def installation(monkeypatch):
    disk = Path(__file__).resolve().parents[2] / ".bt_rev_rfu4_3" / uuid4().hex
    disk.mkdir(parents=True)
    for key, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull, "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(key, value)
    from g6.mcp import dependencies, families, registrations

    needs = {"g6": ['rfu43-base; extra == "mcp"',
                    'rfu43-extra; extra == "abstraction"'],
             "rfu43-base": [], "rfu43-extra": []}
    roots = {"g6": "g6", "rfu43-base": REQUIRED, "rfu43-extra": OPTIONAL}
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
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu43_server:build"})
    (disk / "rfu43_server.py").write_text(
        "from importlib import import_module\nFAILURE = None\n"
        "def build():\n"
        "    global FAILURE\n"
        "    try:\n        return import_module('rfu43_optional')\n"
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
        if name.startswith("rfu43_"):
            sys.modules.pop(name, None)
    invalidate_caches()


def start(env, source):
    from g6 import mcp

    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__spec__ = None
    module.__cached__ = None
    assert mcp.build_server().name == "g6"
    sys.modules.pop(REQUIRED)
    env.bindings.clear()


def verdict(env, required):
    server = import_module("rfu43_server")
    try:
        reply = env.families.describe("load_state")
    except ModuleNotFoundError as exc:
        assert exc is server.FAILURE
        assert required, "retired required identity still blocks optional recovery"
    else:
        assert not required, f"required defect became an install hint: {reply}"
        assert reply["available"] is False
        assert reply["reason"] == "server_unavailable"
        assert "g6[abstraction,mcp]" in reply["install"]


@pytest.mark.parametrize("overlap", [True, False], ids=["absence-interleaves", "serial-control"])
@pytest.mark.parametrize("owner", ["old", "new"])
def test_absence_cannot_reorder_an_uncommitted_success(installation, monkeypatch, overlap, owner):
    env = installation
    source, retired, alias, replacement = (env.disk / name for name in
                                          ("source.py", "retired.py", "alias.py", "replacement.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    os.link(source, alias)
    start(env, source)
    source.rename(retired)
    source.write_text("value = 'replacement'\n", encoding="utf-8")
    os.link(source, replacement)
    assert source.stat().st_ino != alias.stat().st_ino
    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__spec__ = None
    module.__cached__ = None
    refresh = env.deps._path_refresh_observations
    calls = 0

    def interrupt(paths):
        nonlocal calls
        calls += 1
        if overlap and calls == 1:
            # Startup has already statted replacement B at the recorded source.
            # Discovery now sees only absence, while committed ownership is A.
            sys.modules.pop(REQUIRED)
            env.bindings.clear()
            source.unlink()
            with monkeypatch.context() as nested:
                nested.setattr(env.deps, "_path_refresh_observations", refresh)
                env.deps.optional_import_roots("abstraction", "")
        return refresh(paths)

    with monkeypatch.context() as recording:
        recording.setattr(env.deps, "_path_refresh_observations", interrupt)
        env.deps.record_loaded_required_imports()
    if not overlap:
        sys.modules.pop(REQUIRED)
        env.bindings.clear()
        source.unlink()
    # A was retired by startup's direct stat of B, before the later absence.
    env.bindings[OPTIONAL] = alias if owner == "old" else replacement
    env.deps.optional_import_roots("abstraction", "")
    env.bindings.clear()
    alias.unlink()
    replacement.unlink()
    retired.unlink()
    invalidate_caches()
    verdict(env, required=owner == "new")


@pytest.mark.parametrize("error", [OSError, ValueError, None],
                         ids=["unreadable-recheck", "malformed-recheck", "successful-recheck-control"])
def test_failed_recheck_cannot_redate_old_replacement(installation, monkeypatch, error):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir()
    junction = env.disk / "site"
    _winapi.CreateJunction(str(target), str(junction))
    original, retired = target / "shared.py", target / "retired.py"
    source = junction / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    try:
        start(env, source)
        # Populate the empty optional history, so a later identical confirmation
        # changes only the counter and does not force the outer call to retry.
        assert env.deps.optional_import_roots("abstraction", "") == {OPTIONAL}
        generation = env.deps._ownership_generation
        original.rename(retired)
        original.write_text("value = 'temporary replacement'\n", encoding="utf-8")
        refresh, observe = env.deps._path_refresh_observations, env.deps._root_observations
        refresh_calls = 0

        def refreshing(paths):
            nonlocal refresh_calls
            refresh_calls += 1
            if refresh_calls == 2 and error is not None:
                def unreadable(path):
                    assert not env.deps._ownership_lock._is_owned()
                    raise error("recheck cannot read this source")

                with monkeypatch.context() as failed:
                    failed.setattr(env.deps, "_stat_identity", unreadable)
                    return refresh(paths)
            return refresh(paths)

        def observing(roots):
            # The first refresh saw B. Another discovery now directly confirms A
            # through the recorded source before the outer call's failed recheck.
            original.unlink()
            retired.rename(original)
            with monkeypatch.context() as nested:
                nested.setattr(env.deps, "_path_refresh_observations", refresh)
                nested.setattr(env.deps, "_root_observations", observe)
                assert env.deps.optional_import_roots("abstraction", "") == {OPTIONAL}
            assert env.deps._ownership_generation == generation
            env.bindings[OPTIONAL] = original
            return observe(roots)

        with monkeypatch.context() as outer:
            outer.setattr(env.deps, "_path_refresh_observations", refreshing)
            outer.setattr(env.deps, "_root_observations", observing)
            env.deps.optional_import_roots("abstraction", "")
        assert refresh_calls == 2, "the contradictory discovery did not trigger a direct recheck"
        # A remains the last successful direct observation at the recorded source.
        env.bindings.clear()
        os.rmdir(junction)
        original.unlink()
        invalidate_caches()
        verdict(env, required=True)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("aba", [True, False], ids=["not-found-resolution-ABA", "replacement-control"])
def test_not_found_does_not_authorize_unrelated_secondary_stat(installation, monkeypatch, aba):
    import _winapi

    env = installation
    first, second = env.disk / "first", env.disk / "second"
    first.mkdir()
    second.mkdir()
    original, alias = first / "shared.py", second / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, alias)
    junction = env.disk / "site"
    _winapi.CreateJunction(str(first), str(junction))
    source = junction / "shared.py"
    try:
        start(env, source)
        stat = env.deps._stat_identity
        secondary = 0

        def race(path):
            nonlocal secondary
            assert not env.deps._ownership_lock._is_owned()
            if path == str(source):
                # A real not-found at the recorded spelling, then restoration.
                os.rmdir(junction)
                try:
                    return stat(path)
                finally:
                    _winapi.CreateJunction(str(first), str(junction))
            if path == str(original):
                secondary += 1
                if aba:
                    os.rmdir(junction)
                    _winapi.CreateJunction(str(second), str(junction))
                retired = first / "retired.py"
                original.rename(retired)
                original.write_text("value = 'independent replacement'\n", encoding="utf-8")
                result = stat(path)
                if aba:
                    # B was never reachable through the recorded source.
                    assert os.path.samefile(source, alias)
                    original.unlink()
                    retired.rename(original)
                    os.rmdir(junction)
                    _winapi.CreateJunction(str(first), str(junction))
                    assert os.path.samefile(source, alias)
                return result
            return stat(path)

        with monkeypatch.context() as probing:
            probing.setattr(env.deps, "_stat_identity", race)
            env.deps.optional_import_roots("abstraction", "")
        assert secondary == 1
        env.bindings[OPTIONAL] = alias
        env.deps.optional_import_roots("abstraction", "")
        env.bindings.clear()
        os.rmdir(junction)
        original.unlink()
        alias.unlink()
        invalidate_caches()
        verdict(env, required=aba)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("chain", [False, True], ids=["single-link", "link-chain"])
@pytest.mark.parametrize("vanishes", [False, True], ids=["replacement-appears", "replacement-vanishes"])
def test_not_found_replacement_lifetime_controls(installation, monkeypatch, chain, vanishes):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir()
    junction, via = env.disk / "site", env.disk / "via"
    if chain:
        _winapi.CreateJunction(str(target), str(via))
    _winapi.CreateJunction(str(via if chain else target), str(junction))
    original, retired, alias = (target / name for name in ("shared.py", "retired.py", "alias.py"))
    source = junction / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, alias)
    try:
        start(env, source)
        original.rename(retired)
        stat = env.deps._stat_identity
        probes = []

        def race(path):
            assert not env.deps._ownership_lock._is_owned()
            if path == str(source):
                probes.append("absent")
                try:
                    return stat(path)
                except FileNotFoundError:
                    original.write_text("value = 'replacement'\n", encoding="utf-8")
                    raise
            if path == str(original):
                probes.append("replacement")
                result = stat(path)
                if vanishes:
                    original.unlink()
                return result
            return stat(path)

        with monkeypatch.context() as probing:
            probing.setattr(env.deps, "_stat_identity", race)
            env.deps.optional_import_roots("abstraction", "")
        assert probes == ["absent", "replacement"]
        os.rmdir(junction)
        env.bindings[OPTIONAL] = alias
        env.deps.optional_import_roots("abstraction", "")
        env.bindings.clear()
        alias.unlink()
        invalidate_caches()
        verdict(env, required=vanishes)
    finally:
        for link in (junction, via):
            if os.path.lexists(link):
                os.rmdir(link)


@pytest.mark.parametrize("intervening", ["confirmation", "clear", "none"])
def test_successful_recheck_commit_gap_controls(installation, monkeypatch, intervening):
    import _winapi

    env = installation
    target = env.disk / "target"
    target.mkdir()
    junction = env.disk / "site"
    _winapi.CreateJunction(str(target), str(junction))
    original, retired, alias = (target / name for name in ("shared.py", "retired.py", "alias.py"))
    source = junction / "shared.py"
    original.write_text("value = 73\n", encoding="utf-8")
    os.link(original, alias)
    try:
        start(env, source)
        assert env.deps.optional_import_roots("abstraction", "") == {OPTIONAL}
        observe, recheck = env.deps._root_observations, env.deps._observe_current_sources
        changed = False

        def discovering(roots):
            nonlocal changed
            if not changed:
                changed = True
                original.rename(retired)
                original.write_text("value = 'replacement'\n", encoding="utf-8")
                env.bindings[OPTIONAL] = original
            return observe(roots)

        def committing(refresh, observations):
            recheck(refresh, observations)
            if retired.exists():
                # The successful direct recheck saw B; now restore A in the gap
                # before commit, with or without a later observation or clear.
                original.unlink()
                retired.rename(original)
                env.bindings.clear()
                if intervening == "clear":
                    env.deps.optional_import_roots.cache_clear()
                elif intervening == "confirmation":
                    with monkeypatch.context() as nested:
                        nested.setattr(env.deps, "_root_observations", observe)
                        nested.setattr(env.deps, "_observe_current_sources", recheck)
                        env.deps.optional_import_roots("abstraction", "")

        with monkeypatch.context() as outer:
            outer.setattr(env.deps, "_root_observations", discovering)
            outer.setattr(env.deps, "_observe_current_sources", committing)
            env.deps.optional_import_roots("abstraction", "")
        assert changed
        os.rmdir(junction)
        env.bindings[OPTIONAL] = alias
        env.deps.optional_import_roots("abstraction", "")
        env.bindings.clear()
        alias.unlink()
        original.unlink()
        invalidate_caches()
        verdict(env, required=intervening == "confirmation")
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.parametrize("spelling", ["case", "dot"])
def test_repeated_spelling_observations_do_not_grow_history(installation, spelling):
    env = installation
    source, alias = env.disk / "shared.py", env.disk / "alias.py"
    source.write_text("value = 73\n", encoding="utf-8")
    os.link(source, alias)
    alternate = str(source).upper() if spelling == "case" else str(source.parent) + "\\.\\shared.py"
    assert os.path.samefile(source, alternate)
    env.bindings[REQUIRED] = source
    module = import_module(REQUIRED)
    module.__spec__ = SimpleNamespace(origin=alternate, has_location=True)
    module.__cached__ = None
    env.deps.record_loaded_required_imports()
    sys.modules.pop(REQUIRED)
    env.bindings.clear()
    env.bindings[OPTIONAL] = alias
    assert env.deps.optional_import_roots("abstraction", "") == frozenset()
    before = env.deps._ownership_state()
    sources = set(env.deps._ownership_observations)
    assert str(source) in sources and alternate in sources
    for _ in range(20):
        assert env.deps.optional_import_roots("abstraction", "") == frozenset()
    assert env.deps._ownership_state() == before
    assert set(env.deps._ownership_observations) == sources
    env.bindings.clear()
    source.unlink()
    alias.unlink()
    invalidate_caches()
    verdict(env, required=True)
    env.deps.optional_import_roots.cache_clear()
    assert not env.deps._ownership_observations
    assert not env.deps._required_paths
    assert not env.deps._known_paths
