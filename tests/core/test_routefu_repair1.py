# SPDX-License-Identifier: Apache-2.0
"""Shared exception work and dynamic import ownership regressions."""
from importlib import import_module, invalidate_caches, metadata
import sys
from types import SimpleNamespace

import pytest


def test_shared_exception_objects_are_expanded_once():
    from g6.mcp import dependencies

    class CountedGroup(ExceptionGroup):
        reads = 0

        @property
        def exceptions(self):
            self.reads += 1
            return super().exceptions

    failure = ModuleNotFoundError("absent", name="rfu_missing")
    groups = []
    for _ in range(12):
        failure = CountedGroup("shared", [failure, failure])
        groups.append(failure)
    assert dependencies._missing_modules(failure) == ("rfu_missing",)
    assert all(group.reads == 1 for group in groups)


@pytest.mark.parametrize("operation", ("missing_module", "absent_module_roots"))
def test_exception_work_limit_retains_recursion_error(monkeypatch, operation):
    from g6.mcp import dependencies

    monkeypatch.setattr(dependencies, "_EXCEPTION_WORK_LIMIT", 30, raising=False)
    failure = ModuleNotFoundError("absent", name="rfu_missing")
    for _ in range(40):
        wrapper = ImportError("wrapper")
        wrapper.__cause__ = failure
        failure = wrapper
    with pytest.raises(RecursionError, match="exception dependency walk exceeded work limit"):
        getattr(dependencies, operation)(failure)


def test_wide_exception_group_is_bounded_before_expansion(monkeypatch):
    from g6.mcp import dependencies

    monkeypatch.setattr(dependencies, "_EXCEPTION_WORK_LIMIT", 30, raising=False)
    missing = ModuleNotFoundError("absent", name="rfu_missing")
    failure = ExceptionGroup("wide", [missing] * 40)
    with pytest.raises(RecursionError, match="exception dependency walk exceeded work limit"):
        dependencies.absent_module_roots(failure)


@pytest.mark.parametrize("source", ("mapping", "top-level", "record"))
@pytest.mark.parametrize("root", ("rfu-extra", "7rfu", "rfu+extra"))
def test_dynamic_import_root_retains_declared_owner(tmp_path, monkeypatch, source, root):
    from g6.mcp import dependencies

    module = tmp_path / (root + ".py")
    module.write_text("value = 7\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    invalidate_caches()
    try:
        assert import_module(root).value == 7
    finally:
        sys.modules.pop(root, None)
    module.unlink()
    invalidate_caches()
    distribution = SimpleNamespace(metadata={"Name": "rfu-optional"}, read_text=lambda name: {
        "top_level.txt": root + "\n" if source == "top-level" else None,
        "RECORD": root + ".py,,\n" if source == "record" else None,
    }[name])
    monkeypatch.setattr(metadata, "packages_distributions", lambda: (
        {root: ["rfu-optional"]} if source == "mapping" else {}))
    monkeypatch.setattr(metadata, "distributions", lambda: (distribution,))
    assert dependencies._import_roots()["rfu-optional"] == {root}


@pytest.mark.parametrize("source", ("mapping", "top-level"))
def test_path_like_declarations_supply_no_ownership(monkeypatch, source):
    from g6.mcp import dependencies

    invalid = ("", ".relative", "../escaped", "bad/root", "bad\\root", "C:drive", "bad\0root")
    distribution = SimpleNamespace(metadata={"Name": "rfu-optional"}, read_text=lambda name: (
        "\n".join(invalid) if name == "top_level.txt" and source == "top-level" else None))
    monkeypatch.setattr(metadata, "packages_distributions", lambda: (
        {root: ["rfu-optional"] for root in invalid} if source == "mapping" else {}))
    monkeypatch.setattr(metadata, "distributions", lambda: (distribution,))
    assert not dependencies._import_roots().get("rfu-optional")
