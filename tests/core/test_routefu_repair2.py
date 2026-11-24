# SPDX-License-Identifier: Apache-2.0
"""Cyclic members retain their own evidence; required import aliases win."""
from importlib import import_module, invalidate_caches, machinery, metadata, util
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest


@pytest.mark.parametrize("edge", ("__cause__", "__context__"))
@pytest.mark.parametrize("kind", ("installed", "plain-import", "runtime", "unnamed"))
def test_cyclic_member_cannot_borrow_optional_sibling(edge, kind):
    from g6.mcp import dependencies

    missing = ModuleNotFoundError("optional absent", name="rfurepair_optional")
    defects = {
        "installed": ModuleNotFoundError("installed API missing", name="json.rfurepair_api"),
        "plain-import": ImportError("API defect", name="json"),
        "runtime": ValueError("body defect"),
        "unnamed": ModuleNotFoundError("no missing name"),
    }
    defect = defects[kind]
    failure = ExceptionGroup("mixed", [defect, missing])
    setattr(defect, edge, failure)
    assert dependencies.absent_module_roots(failure) == frozenset()
    assert dependencies.absent_module_roots(failure, mcp_startup=True) == frozenset()


def test_installed_missing_name_is_retained_beside_chained_optional_evidence():
    from g6.mcp import dependencies

    failure = ModuleNotFoundError("installed API missing", name="json.rfurepair_api")
    failure.__cause__ = ModuleNotFoundError("optional absent", name="rfurepair_optional")
    assert dependencies.absent_module_roots(failure) == frozenset()


def test_cyclic_member_with_own_evidence_remains_optional():
    from g6.mcp import dependencies

    missing = ModuleNotFoundError("optional absent", name="rfurepair_optional")
    wrapper = ImportError("dependency wrapper")
    wrapper.__cause__ = missing
    failure = ExceptionGroup("shared cycle", [wrapper, missing, wrapper])
    missing.__context__ = failure
    assert dependencies.absent_module_roots(failure) == frozenset({"rfurepair_optional"})


def _configure_ownership(monkeypatch):
    from g6.mcp import dependencies

    declarations = {"rfurepair_required": ["repair-base"], "rfurepair_optional": ["repair-extra"]}
    needs = {"g6": ["repair-base", 'repair-extra; extra == "abstraction"'],
             "repair-base": [], "repair-extra": []}
    monkeypatch.setattr(metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(metadata, "packages_distributions", lambda: declarations)
    monkeypatch.setattr(metadata, "distributions", lambda: ())
    dependencies.optional_import_roots.cache_clear()
    return dependencies, declarations


@pytest.fixture
def ownership(monkeypatch):
    dependencies, declarations = _configure_ownership(monkeypatch)
    yield dependencies, declarations
    dependencies.optional_import_roots.cache_clear()


@pytest.mark.parametrize("shared_file", (True, False), ids=("same-file", "distinct-files"))
def test_required_ownership_follows_resolved_file(tmp_path, monkeypatch, ownership, shared_file):
    dependencies, _ = ownership
    required_file = tmp_path / "required.py"
    required_file.write_text("value = 73\n", encoding="utf-8")
    optional_file = required_file if shared_file else tmp_path / "optional.py"
    if not shared_file:
        optional_file.write_text("value = 91\n", encoding="utf-8")
    paths = {"rfurepair_required": required_file, "rfurepair_optional": optional_file}

    class AliasFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname in paths:
                return util.spec_from_file_location(fullname, paths[fullname])
            return None

    monkeypatch.setattr(sys, "meta_path", [AliasFinder(), *sys.meta_path])
    try:
        required = import_module("rfurepair_required")
        optional = import_module("rfurepair_optional")
        assert required.value == 73
        assert optional.value == (73 if shared_file else 91)
        for name in paths:
            sys.modules.pop(name, None)
        expected = frozenset() if shared_file else frozenset({"rfurepair_optional"})
        assert dependencies.optional_import_roots("abstraction") == expected
        assert all(name not in sys.modules for name in paths)
        required_file.unlink()
        dependencies.optional_import_roots.cache_clear()
        invalidate_caches()
        assert dependencies.optional_import_roots("abstraction") == expected
    finally:
        for name in paths:
            sys.modules.pop(name, None)


def test_required_ownership_follows_cached_module_identity(monkeypatch, ownership):
    dependencies, _ = ownership
    shared = ModuleType("rfurepair_canonical")
    monkeypatch.setitem(sys.modules, "rfurepair_required", shared)
    monkeypatch.setitem(sys.modules, "rfurepair_optional", shared)
    assert import_module("rfurepair_required") is import_module("rfurepair_optional")
    assert dependencies.optional_import_roots("abstraction") == frozenset()


def test_required_ownership_follows_resolved_import_name(monkeypatch, ownership):
    dependencies, _ = ownership

    class AliasFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname in {"rfurepair_required", "rfurepair_optional"}:
                return machinery.ModuleSpec("rfurepair_canonical", loader=None, is_package=True)
            return None

    monkeypatch.setattr(sys, "meta_path", [AliasFinder(), *sys.meta_path])
    try:
        for name in ("rfurepair_required", "rfurepair_optional"):
            assert import_module(name).__name__ == "rfurepair_canonical"
            sys.modules.pop("rfurepair_canonical", None)
        assert dependencies.optional_import_roots("abstraction") == frozenset()
    finally:
        sys.modules.pop("rfurepair_canonical", None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows import name matching")
@pytest.mark.parametrize("relaxed", (True, False), ids=("caseok", "strict"))
def test_required_ownership_uses_live_case_matching(tmp_path, relaxed):
    environment = os.environ.copy()
    environment.update(G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe",
                       G6_CODEX=r"C:\nonexistent\g6-no-host.exe")
    if relaxed:
        environment["PYTHONCASEOK"] = "1"
    else:
        environment.pop("PYTHONCASEOK", None)
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--case-probe", str(tmp_path), str(int(relaxed))],
        env=environment, text=True, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _case_probe(tmp_path, relaxed):
    with pytest.MonkeyPatch.context() as monkeypatch:
        dependencies, declarations = _configure_ownership(monkeypatch)
        declarations.clear()
        declarations.update({"RFURepair-Shared": ["repair-base"], "rfurepair-shared": ["repair-extra"]})
        monkeypatch.syspath_prepend(str(tmp_path))
        module_file = tmp_path / "RFURepair-Shared.py"
        module_file.write_text("value = 73\n", encoding="utf-8")
        invalidate_caches()
        try:
            assert import_module("RFURepair-Shared").value == 73
            if relaxed:
                assert import_module("rfurepair-shared").value == 73
            else:
                with pytest.raises(ModuleNotFoundError):
                    import_module("rfurepair-shared")
            for name in declarations:
                sys.modules.pop(name, None)
            module_file.unlink()
            invalidate_caches()
            expected = frozenset() if relaxed else frozenset({"rfurepair-shared"})
            assert dependencies.optional_import_roots("abstraction") == expected
            failure = ModuleNotFoundError("broken required installation", name="rfurepair-shared")
            assert dependencies.optional_dependency_missing("abstraction", failure) is (not relaxed)
        finally:
            dependencies.optional_import_roots.cache_clear()
            for name in declarations:
                sys.modules.pop(name, None)


if __name__ == "__main__" and sys.argv[1:2] == ["--case-probe"]:
    _case_probe(Path(sys.argv[2]), bool(int(sys.argv[3])))
