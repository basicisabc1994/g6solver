# SPDX-License-Identifier: Apache-2.0
"""Direct source evidence and ordered startup/discovery ownership observations."""
from importlib import invalidate_caches, util
import os
import sys
from types import ModuleType

import pytest


REQUIRED = "rfu4r2_required"
OPTIONAL = "rfu4r2_optional"


@pytest.fixture
def ownership(monkeypatch):
    from g6.mcp import dependencies

    needs = {"g6": ['rfu4r2-base; extra == "mcp"', 'rfu4r2-extra; extra == "abstraction"'],
             "rfu4r2-base": [], "rfu4r2-extra": []}
    monkeypatch.setattr(dependencies.metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(dependencies.metadata, "packages_distributions",
                        lambda: {REQUIRED: ["rfu4r2-base"], OPTIONAL: ["rfu4r2-extra"]})
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


def _startup(dependencies, monkeypatch, source):
    module = ModuleType(REQUIRED)
    module.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, module)
    dependencies.record_loaded_required_imports()


def _optional(dependencies):
    return dependencies.optional_import_roots("abstraction", "")


@pytest.mark.parametrize("error", (OSError, FileNotFoundError, None),
                         ids=("unreadable-keeps", "not-found-retires", "direct-replacement-control"))
def test_secondary_replacement_cannot_retire_unreadable_source(ownership, tmp_path, monkeypatch, error):
    dependencies, _ = ownership
    source, retired, resolved = (tmp_path / name for name in ("source.py", "retired.py", "resolved.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    _startup(dependencies, monkeypatch, source)
    monkeypatch.delitem(sys.modules, REQUIRED)
    identity = dependencies._stat_identity(str(source))
    source.rename(retired)
    resolved.write_text("value = 'replacement'\n", encoding="utf-8")
    if error is None:
        os.link(resolved, source)
    stat = dependencies._stat_identity
    resolve = os.path.realpath
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
                        else resolve(path, **kw))
        _optional(dependencies)
    assert bool(secondary) is (error is not None)
    assert any(record[4:] == identity for record in dependencies._required_paths) is (error is OSError)


@pytest.mark.parametrize("moved", (True, False), ids=("moved-record", "replacement-control"))
def test_obsolete_resolution_does_not_decide_record(ownership, tmp_path, monkeypatch, moved):
    dependencies, specs = ownership
    source, old, alias, retired = (tmp_path / name for name in ("source.py", "old.py", "alias.py", "retired.py"))
    source.write_text("value = 73\n", encoding="utf-8")
    os.link(source, alias)
    resolve = os.path.realpath
    with monkeypatch.context() as initial:
        initial.setattr(os.path, "realpath", lambda path, **kw: str(old) if path == str(source)
                        else resolve(path, **kw))
        _startup(dependencies, monkeypatch, source)
    monkeypatch.delitem(sys.modules, REQUIRED)
    old.write_text("value = 'replacement'\n", encoding="utf-8")
    if not moved:
        source.rename(retired)
        source.write_text("value = 'replacement'\n", encoding="utf-8")
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, old)
    assert _optional(dependencies) == {OPTIONAL}
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
    assert _optional(dependencies) == (frozenset() if moved else {OPTIONAL})


@pytest.mark.parametrize("remove", (True, False), ids=("restored-then-absent", "live-control"))
def test_later_success_supersedes_retirement_during_startup(ownership, tmp_path, monkeypatch, remove):
    dependencies, specs = ownership
    source, retired = tmp_path / "source.py", tmp_path / "retired.py"
    source.write_text("value = 73\n", encoding="utf-8")
    _startup(dependencies, monkeypatch, source)
    refresh = dependencies._path_refresh_observations
    calls = 0

    def interrupt(paths):
        nonlocal calls
        result = refresh(paths)
        calls += 1
        if calls == 1:
            with monkeypatch.context() as nested:
                nested.setattr(dependencies, "_path_refresh_observations", refresh)
                monkeypatch.delitem(sys.modules, REQUIRED)
                source.rename(retired)
                source.write_text("value = 'replacement'\n", encoding="utf-8")
                assert _optional(dependencies) == {OPTIONAL}
                source.unlink()
                retired.rename(source)
                specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, source)
                assert _optional(dependencies) == {OPTIONAL}
                if remove:
                    source.unlink()
        return result

    with monkeypatch.context() as recording:
        recording.setattr(dependencies, "_path_refresh_observations", interrupt)
        dependencies.record_loaded_required_imports()
    assert calls == 2
    specs.clear()
    if source.exists():
        source.unlink()
    assert _optional(dependencies) == frozenset()


@pytest.mark.parametrize("overlap", (True, False), ids=("first-startup-retry", "serial-control"))
def test_first_startup_keeps_later_discovery_success(ownership, tmp_path, monkeypatch, overlap):
    dependencies, specs = ownership
    source, retired = tmp_path / "source.py", tmp_path / "retired.py"
    source.write_text("value = 73\n", encoding="utf-8")
    module = ModuleType(REQUIRED)
    module.__file__ = str(source)
    monkeypatch.setitem(sys.modules, REQUIRED, module)
    refresh = dependencies._path_refresh_observations
    calls = 0

    def replace():
        monkeypatch.delitem(sys.modules, REQUIRED)
        source.rename(retired)
        source.write_text("value = 'replacement'\n", encoding="utf-8")
        specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, source)
        with monkeypatch.context() as nested:
            nested.setattr(dependencies, "_path_refresh_observations", refresh)
            assert _optional(dependencies) == {OPTIONAL}
        source.unlink()

    def interrupt(paths):
        nonlocal calls
        result = refresh(paths)
        calls += 1
        if overlap and calls == 1:
            replace()
        return result

    with monkeypatch.context() as recording:
        recording.setattr(dependencies, "_path_refresh_observations", interrupt)
        dependencies.record_loaded_required_imports()
    assert calls == (2 if overlap else 1)
    if not overlap:
        replace()
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, retired)
    assert _optional(dependencies) == {OPTIONAL}


@pytest.mark.parametrize("clear", (False, True), ids=("ordinary-absence-keeps", "explicit-clear-control"))
def test_absence_history_and_explicit_clear(ownership, tmp_path, monkeypatch, clear):
    dependencies, specs = ownership
    source, alias = tmp_path / "source.py", tmp_path / "alias.py"
    source.write_text("value = 73\n", encoding="utf-8")
    os.link(source, alias)
    _startup(dependencies, monkeypatch, source)
    monkeypatch.delitem(sys.modules, REQUIRED)
    specs[OPTIONAL] = util.spec_from_file_location(OPTIONAL, alias)
    assert _optional(dependencies) == frozenset()
    specs.clear()
    source.unlink()
    alias.unlink()
    if clear:
        dependencies.optional_import_roots.cache_clear()
    assert _optional(dependencies) == ({OPTIONAL} if clear else frozenset())
