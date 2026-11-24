# SPDX-License-Identifier: Apache-2.0
"""Amendment regressions for startup chains and metadata ownership seams."""
from __future__ import annotations

from importlib import metadata
from types import SimpleNamespace

import pytest


@pytest.fixture
def installation(monkeypatch):
    from g6.mcp import dependencies

    needs = {
        "g6": ["amend-base", 'amend-optional; extra == "abstraction"',
               'fastmcp; extra == "mcp"', 'mcp; extra == "mcp"'],
        "amend-base": [], "amend-optional": [], "fastmcp": [], "mcp": [],
    }
    imports = {"base_root": ["amend-base"], "optional_root": ["amend-optional"],
               "fastmcp": ["fastmcp"], "mcp": ["mcp"]}

    def requires(name):
        if name not in needs:
            raise metadata.PackageNotFoundError(name)
        value = needs[name]
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(metadata, "requires", requires)
    monkeypatch.setattr(metadata, "packages_distributions", lambda: imports)
    monkeypatch.setattr(metadata, "distributions", lambda: ())
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, needs, imports
    dependencies.optional_import_roots.cache_clear()


@pytest.mark.parametrize("entry", ("build", "main"))
@pytest.mark.parametrize("link", ("cause", "context"))
@pytest.mark.parametrize("missing", ("mcp.server", "fastmcp.tools"))
def test_startup_missing_mcp_beneath_deep_named_wrappers(installation, monkeypatch, entry, link, missing):
    from g6 import mcp

    dependencies, _, _ = installation
    # An installed root must not hide the missing MCP submodule at startup.
    monkeypatch.setattr(dependencies.util, "find_spec", lambda name: object())
    failure = ModuleNotFoundError("missing child", name=missing)
    for _ in range(2000):
        wrapper = ImportError("wrapper", name="wrapper_name")
        setattr(wrapper, "__" + link + "__", failure)
        failure = wrapper

    def fail():
        raise failure

    monkeypatch.setattr(mcp, "_quiet" if entry == "build" else "build_server", fail)
    with pytest.raises(SystemExit) as stopped:
        (mcp.build_server if entry == "build" else mcp.main)()
    assert missing in str(stopped.value)
    assert "MCP extra" in str(stopped.value)
    assert stopped.value.__cause__ is failure


def test_missing_module_diagnostic_prefers_missing_evidence_over_plain_import(installation):
    dependencies, _, _ = installation
    failure = ImportError("outer", name="outer_name")
    failure.__cause__ = ImportError("plain diagnostic", name="wrapper_name")
    failure.__context__ = ModuleNotFoundError("missing child", name="mcp.server")
    failure.__suppress_context__ = False
    assert dependencies.missing_module(failure) == "mcp.server"


@pytest.mark.parametrize("required", ("g6", "amend-base", "amend-required-leaf"))
def test_required_metadata_error_survives_installed_root(installation, monkeypatch, required):
    dependencies, needs, _ = installation
    failure = metadata.PackageNotFoundError(required)
    needs["amend-base"] = ["amend-required-leaf"]
    needs["amend-required-leaf"] = []
    needs[required] = failure
    monkeypatch.setattr(dependencies.util, "find_spec", lambda name: object())
    with pytest.raises(metadata.PackageNotFoundError) as stopped:
        dependencies.optional_dependency_missing(
            "abstraction", ModuleNotFoundError("broken install", name="optional_root"))
    assert stopped.value is failure


@pytest.mark.parametrize("missing_error", (metadata.PackageNotFoundError, KeyError))
def test_optional_closure_uses_requires_seam_and_required_mcp_wins(installation, monkeypatch, missing_error):
    dependencies, needs, imports = installation
    needs["amend-optional"] = ["amend-mid[child]", 'inactive; python_version < "2"']
    needs["amend-mid"] = ['amend-leaf; extra == "child"', "amend-optional", "amend-shared"]
    needs["amend-leaf"] = missing_error("amend-leaf")
    needs["amend-shared"] = []
    needs["fastmcp"] = ["amend-shared"]
    imports.update({"shared_root": ["amend-shared"], "leaf_root": ["amend-leaf"],
                    "mid_root": ["amend-mid"]})

    class Distribution:
        metadata = {"Name": "amend-optional"}

        def read_text(self, name):
            return None

        @property
        def requires(self):
            raise AssertionError("closure bypassed metadata.requires")

    monkeypatch.setattr(metadata, "distributions", lambda: (Distribution(),))
    assert dependencies.optional_import_roots("abstraction") == frozenset({
        "optional_root", "mid_root", "leaf_root",
    })


@pytest.mark.parametrize("top_level, expected", (
    ("declared_root\n", {"seed_root", "declared_root"}),
    (None, {"seed_root", "record_root"}),
))
def test_top_level_precedes_record_without_replacing_seed(installation, monkeypatch, top_level, expected):
    dependencies, _, imports = installation
    imports["seed_root"] = ["amend-optional"]
    del imports["optional_root"]
    record = SimpleNamespace(metadata={"Name": "amend-optional"}, requires=[], read_text=lambda name: {
        "top_level.txt": top_level, "RECORD": "record_root.pyc,,\n",
    }[name])
    monkeypatch.setattr(metadata, "distributions", lambda: (record,))
    assert dependencies.extra_import_roots("abstraction") == frozenset(expected)
