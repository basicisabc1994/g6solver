"""Passing boundary controls; only test_failures.py contains findings."""
from __future__ import annotations

import sys

import pytest

from .test_failures import disk_server, review_environment, tmp_path


def test_cycle_terminates_and_similar_prefix_is_not_optional():
    from g6.mcp.dependencies import missing_module, optional_dependency_missing

    inner = ModuleNotFoundError("absent", name="numpy_extra")
    outer = ImportError("wrapper", name="fastmcp")
    outer.__cause__ = inner
    inner.__context__ = outer
    assert missing_module(outer) == "numpy_extra"
    assert optional_dependency_missing("abstraction", outer) is False


def test_missing_optional_load_is_retried(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hypothesis", None)
    families = disk_server(tmp_path, monkeypatch,
        "from types import SimpleNamespace\n"
        "attempt = 0\n"
        "def build():\n"
        "    global attempt\n"
        "    attempt += 1\n"
        "    if attempt == 1:\n"
        "        import hypothesis\n"
        "    return SimpleNamespace(get_tools=lambda: {'load_state': "
        "SimpleNamespace(description='Read state.', parameters={})})\n")
    assert families.describe("load_state")["reason"] == "server_unavailable"
    assert families.describe("load_state")["available"] is True


def test_defective_load_is_retried(tmp_path, monkeypatch):
    families = disk_server(tmp_path, monkeypatch,
        "from types import SimpleNamespace\n"
        "attempt = 0\n"
        "def build():\n"
        "    global attempt\n"
        "    attempt += 1\n"
        "    if attempt == 1:\n"
        "        raise RuntimeError('transient defect')\n"
        "    return SimpleNamespace(get_tools=lambda: {'load_state': "
        "SimpleNamespace(description='Read state.', parameters={})})\n")
    with pytest.raises(RuntimeError, match="transient defect"):
        families.describe("load_state")
    assert families.describe("load_state")["available"] is True


def test_missing_distribution_record_is_not_optional(monkeypatch):
    from g6.mcp import dependencies

    def absent(name):
        raise dependencies.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(dependencies.metadata, "requires", absent)
    with pytest.raises(dependencies.metadata.PackageNotFoundError):
        dependencies.optional_dependency_missing(
            "abstraction", ModuleNotFoundError("missing", name="hypothesis"))
