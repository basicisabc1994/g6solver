# SPDX-License-Identifier: Apache-2.0
"""Ownership facts retain their own order; only direct replacement retires them."""
from importlib import invalidate_caches, util
import os
import sys
from types import ModuleType

import pytest


REQUIRED = "rfu4r3_required"
OPTIONAL = "rfu4r3_optional"


@pytest.fixture
def ownership(monkeypatch):
    from g6.mcp import dependencies

    needs = {"g6": ['rfu4r3-base; extra == "mcp"', 'rfu4r3-extra; extra == "abstraction"'],
             "rfu4r3-base": [], "rfu4r3-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu4r3-base"], OPTIONAL: ["rfu4r3-extra"]})
    monkeypatch.setattr(dependencies.metadata, "distributions", lambda: ())
    specs = {}
    find_spec = dependencies.util.find_spec
    monkeypatch.setattr(dependencies.util, "find_spec",
                        lambda name: specs.get(name) if name in {REQUIRED, OPTIONAL} else find_spec(name))
    for root in (REQUIRED, OPTIONAL):
        monkeypatch.delitem(sys.modules, root, raising=False)
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, specs
    dependencies.optional_import_roots.cache_clear()
    for root in (REQUIRED, OPTIONAL):
        sys.modules.pop(root, None)
    invalidate_caches()


def _loaded(monkeypatch, source):
    module = ModuleType(REQUIRED)
    module.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, module)


def _optional(dependencies):
    return dependencies.optional_import_roots("abstraction", "")


@pytest.mark.parametrize("error", (OSError, ValueError, None),
                         ids=("unreadable-recheck", "malformed-recheck", "successful-recheck-control"))
def test_failed_recheck_keeps_each_facts_original_order(ownership, tmp_path, monkeypatch, error):
    dependencies, specs = ownership
    source, resolved, retired = (tmp_path / name for name in ("source.py", "resolved.py", "retired.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    os.link(source, resolved)
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda path, **kw: str(resolved) if path == str(source)
                        else realpath(path, **kw))
    _loaded(monkeypatch, source)
    dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    assert _optional(dependencies) == {OPTIONAL}
    generation = dependencies._ownership_generation
    source.rename(retired)
    source.write_text("value = 'temporary replacement'\n", encoding="utf-8")
    refresh, observe = dependencies._path_refresh_observations, dependencies._root_observations
    calls = 0

    def refreshing(paths):
        nonlocal calls
        calls += 1
        if calls == 2 and error is not None:
            def unreadable(path):
                assert not dependencies._ownership_lock._is_owned()
                raise error("failed recheck")

            with monkeypatch.context() as failed:
                failed.setattr(dependencies, "_stat_identity", unreadable)
                return refresh(paths)
        return refresh(paths)

    def observing(roots):
        source.unlink()
        retired.rename(source)
        with monkeypatch.context() as nested:
            nested.setattr(dependencies, "_path_refresh_observations", refresh)
            nested.setattr(dependencies, "_root_observations", observe)
            assert _optional(dependencies) == {OPTIONAL}
        assert dependencies._ownership_generation == generation
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, resolved)
        return observe(roots)

    with monkeypatch.context() as outer:
        outer.setattr(dependencies, "_path_refresh_observations", refreshing)
        outer.setattr(dependencies, "_root_observations", observing)
        _optional(dependencies)
    assert calls == 2
    specs.clear()
    source.unlink()
    resolved.unlink()
    assert _optional(dependencies) == frozenset()


@pytest.mark.parametrize("overlap", (True, False), ids=("absence-interleaves", "serial-control"))
@pytest.mark.parametrize("owner", ("old", "new"))
def test_absence_does_not_redate_committed_identity(ownership, tmp_path, monkeypatch, overlap, owner):
    dependencies, specs = ownership
    source, retired, old, new = (tmp_path / name for name in ("source.py", "retired.py", "old.py", "new.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    os.link(source, old)
    _loaded(monkeypatch, source)
    dependencies.record_loaded_required_imports()
    source.rename(retired)
    source.write_text("value = 'replacement'\n", encoding="utf-8")
    os.link(source, new)
    refresh = dependencies._path_refresh_observations
    calls = 0

    def interrupt(paths):
        nonlocal calls
        calls += 1
        if overlap and calls == 1:
            monkeypatch.delitem(sys.modules, REQUIRED)
            source.unlink()
            with monkeypatch.context() as nested:
                nested.setattr(dependencies, "_path_refresh_observations", refresh)
                _optional(dependencies)
        return refresh(paths)

    with monkeypatch.context() as recording:
        recording.setattr(dependencies, "_path_refresh_observations", interrupt)
        dependencies.record_loaded_required_imports()
    if not overlap:
        monkeypatch.delitem(sys.modules, REQUIRED)
        source.unlink()
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, old if owner == "old" else new)
    assert _optional(dependencies) == ({OPTIONAL} if owner == "old" else frozenset())
    specs.clear()
    for path in (old, new, retired):
        path.unlink()
    assert _optional(dependencies) == ({OPTIONAL} if owner == "old" else frozenset())


@pytest.mark.parametrize("error", (FileNotFoundError, OSError, None),
                         ids=("not-found-keeps", "unreadable-control", "direct-replacement-control"))
def test_only_direct_successful_replacement_retires(ownership, tmp_path, monkeypatch, error):
    dependencies, specs = ownership
    source, retired, resolved = (tmp_path / name for name in ("source.py", "retired.py", "resolved.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    _loaded(monkeypatch, source)
    dependencies.record_loaded_required_imports()
    monkeypatch.delitem(sys.modules, REQUIRED)
    source.rename(retired)
    resolved.write_text("value = 'replacement'\n", encoding="utf-8")
    if error is None:
        os.link(resolved, source)
    stat, realpath = dependencies._stat_identity, os.path.realpath
    secondary = []

    def probe(path):
        assert not dependencies._ownership_lock._is_owned()
        if path == str(source) and error is not None:
            raise error("recorded source unavailable")
        if path == str(resolved):
            secondary.append(path)
        return stat(path)

    with monkeypatch.context() as probing:
        probing.setattr(dependencies, "_stat_identity", probe)
        probing.setattr(os.path, "realpath", lambda path, **kw: str(resolved) if path == str(source)
                        else realpath(path, **kw))
        _optional(dependencies)
    assert bool(secondary) is (error is not None)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, retired)
    assert _optional(dependencies) == ({OPTIONAL} if error is None else frozenset())
