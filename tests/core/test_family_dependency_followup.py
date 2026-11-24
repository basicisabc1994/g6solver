# SPDX-License-Identifier: Apache-2.0
"""Follow-up regressions for absent imports, RECORD roots and optional closures."""
from __future__ import annotations

import importlib
from importlib import machinery, metadata
import sys

import pytest


@pytest.fixture
def installation(tmp_path, monkeypatch):
    from packaging.utils import canonicalize_name

    from g6.mcp import dependencies

    needs = {
        "g6": ["route-fu-base", 'route-fu-optional; extra == "abstraction"',
               'route-fu-mcp; extra == "mcp"'],
        "route-fu-base": [],
        "route-fu-optional": [],
        "route-fu-mcp": [],
    }

    def write(name, requirements=(), record=None, top_level=None):
        directory = tmp_path / (name.replace("-", "_") + "-1.0.dist-info")
        directory.mkdir(exist_ok=True)
        (directory / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements), encoding="utf-8")
        (directory / "RECORD").write_text(
            record if record is not None else name.replace("-", "_") + "/__init__.py,,\n",
            encoding="utf-8")
        if top_level is not None:
            (directory / "top_level.txt").write_text(top_level, encoding="utf-8")
        dependencies.optional_import_roots.cache_clear()

    for name, requirements in needs.items():
        write(name, requirements)
    scan = metadata.distributions

    def requires(name):
        for distribution in scan(path=[str(tmp_path)]):
            if canonicalize_name(distribution.metadata["Name"]) == canonicalize_name(name):
                return distribution.requires
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(tmp_path)]))
    monkeypatch.setattr(metadata, "requires", requires)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, write, tmp_path
    dependencies.optional_import_roots.cache_clear()
    for name in ("route_fu_server", "route_fu_optional", "route_fu_mid"):
        sys.modules.pop(name, None)


def server(installation, monkeypatch, source):
    from g6.mcp import families, registrations

    _, _, directory = installation
    (directory / "route_fu_server.py").write_text(source, encoding="utf-8")
    monkeypatch.delitem(sys.modules, "route_fu_server", raising=False)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "route_fu_server:build"})
    monkeypatch.setattr(families, "_DETAILS", {})
    importlib.invalidate_caches()
    return families


def assert_unavailable_family(families):
    reply = families.describe("logic")
    assert reply["available"] is True
    rows = [row for row in reply["members"] if row["id"].startswith("mcp:abstraction/")]
    assert rows and all(row["reason"] == "server_unavailable" for row in rows)


def test_named_importerror_propagates_from_family(installation, monkeypatch):
    families = server(installation, monkeypatch,
        "def build():\n    raise ImportError('DLL load failed', name='route_fu_optional')\n")
    with pytest.raises(ImportError, match="DLL load failed") as stopped:
        families.describe("load_state")
    assert type(stopped.value) is ImportError


@pytest.mark.parametrize("entry", ("build", "main"))
@pytest.mark.parametrize("shape", (
    "named-import", "body-import", "absent", "wrapped-absent", "present", "mixed", "required",
))
def test_startup_uses_absence_and_ownership(installation, monkeypatch, entry, shape):
    from g6 import mcp

    _, write, _ = installation
    failures = {
        "named-import": ImportError("native defect", name="route_fu_mcp"),
        "body-import": ImportError("body defect"),
        "absent": ModuleNotFoundError("missing", name="route_fu_mcp.child"),
        "wrapped-absent": ImportError("dependency wrapper", name="route_fu_mcp"),
        "present": ModuleNotFoundError("installed root", name="json.child"),
        "mixed": ImportError("group wrapper"),
        "required": ModuleNotFoundError("broken base", name="route_fu_shared"),
    }
    if shape == "present":
        write("route-fu-mcp", record="json/__init__.py,,\n")
    if shape == "required":
        write("route-fu-base", record="route_fu_shared.py,,\n")
        write("route-fu-mcp", record="route_fu_shared.py,,\n")
    failure = failures[shape]
    if shape == "mixed":
        failure.__cause__ = ExceptionGroup("mixed", [failures["absent"], ValueError("defect")])
    if shape == "wrapped-absent":
        failure.__cause__ = failures["absent"]

    def fail():
        raise failure

    monkeypatch.setattr(mcp, "_quiet" if entry == "build" else "build_server", fail)
    with pytest.raises(SystemExit) as stopped:
        (mcp.build_server if entry == "build" else mcp.main)()
    assert stopped.value.__cause__ is failure
    assert ("MCP extra" in str(stopped.value)) is (shape in {"absent", "wrapped-absent"})


@pytest.mark.parametrize("suffix", (*machinery.SOURCE_SUFFIXES, *machinery.BYTECODE_SUFFIXES,
                                    *machinery.EXTENSION_SUFFIXES))
def test_required_record_module_stays_required_when_file_absent(installation, monkeypatch, suffix):
    dependencies, write, _ = installation
    write("route-fu-base", record=f"route_fu_optional{suffix},,\n")
    assert "route_fu_optional" not in dependencies.optional_import_roots("abstraction")
    families = server(installation, monkeypatch, "def build():\n    import route_fu_optional\n")
    with pytest.raises(ModuleNotFoundError, match="route_fu_optional"):
        families.describe("load_state")


@pytest.mark.parametrize("top_level, expected", (
    ("declared_root\n", {"seed_root", "declared_root"}),
    (None, {"seed_root", "record_root"}),
), ids=("top-level-wins", "record-only"))
def test_import_root_readings_are_additive(installation, monkeypatch, top_level, expected):
    dependencies, write, _ = installation
    write("route-fu-base", top_level=top_level, record="record_root.pyc,,\n")
    monkeypatch.setattr(metadata, "packages_distributions", lambda: {"seed_root": ["route-fu-base"]})
    assert dependencies._import_roots()["route-fu-base"] == expected


@pytest.mark.parametrize("depth", (1, 2))
def test_transitive_optional_import_keeps_family(installation, monkeypatch, depth):
    dependencies, write, directory = installation
    write("route-fu-optional", ["route-fu-mid[child]", 'inactive; python_version < "2"'])
    if depth == 2:
        write("route-fu-mid", ['route-fu-leaf; extra == "child"', "route-fu-optional"])
        (directory / "route_fu_mid.py").write_text("import route_fu_leaf\n", encoding="utf-8")
    (directory / "route_fu_optional.py").write_text("import route_fu_mid\n", encoding="utf-8")
    expected = "route_fu_mid" if depth == 1 else "route_fu_leaf"
    assert expected in dependencies.optional_import_roots("abstraction")
    assert "inactive" not in dependencies.optional_import_roots("abstraction")
    assert_unavailable_family(server(installation, monkeypatch, "def build():\n    import route_fu_optional\n"))


def test_required_transitive_owner_wins_over_optional_owner(installation, monkeypatch):
    dependencies, write, _ = installation
    write("route-fu-base", ["route-fu-shared"])
    write("route-fu-shared")
    write("route-fu-optional", ["route-fu-mid"])
    write("route-fu-mid", ["route-fu-shared", "route-fu-leaf"])
    roots = dependencies.optional_import_roots("abstraction")
    assert "route_fu_leaf" in roots
    assert "route_fu_shared" not in roots
    families = server(installation, monkeypatch, "def build():\n    import route_fu_shared\n")
    with pytest.raises(ModuleNotFoundError, match="route_fu_shared"):
        families.describe("load_state")


@pytest.mark.parametrize("link", ("cause", "context", "group"))
def test_deep_visible_optional_chain(installation, monkeypatch, link):
    wrap = {
        "cause": "        wrapper = RuntimeError('wrapper')\n        wrapper.__cause__ = failure\n",
        "context": "        wrapper = RuntimeError('wrapper')\n        wrapper.__context__ = failure\n",
        "group": "        wrapper = ExceptionGroup('wrapper', [failure])\n",
    }[link]
    families = server(installation, monkeypatch,
        "def build():\n"
        "    failure = ModuleNotFoundError('absent', name='route_fu_optional')\n"
        "    for _ in range(2000):\n" + wrap + "        failure = wrapper\n    raise failure\n")
    assert_unavailable_family(families)


def test_deep_mixed_group_remains_defective(installation, monkeypatch):
    families = server(installation, monkeypatch,
        "def build():\n"
        "    failure = ModuleNotFoundError('absent', name='route_fu_optional')\n"
        "    for _ in range(2000):\n"
        "        failure = ExceptionGroup('wrapper', [failure])\n"
        "    raise ExceptionGroup('mixed', [failure, ValueError('body defect')])\n")
    with pytest.raises(ExceptionGroup) as stopped:
        families.describe("logic")
    assert isinstance(stopped.value.exceptions[1], ValueError)


def test_group_members_sharing_cycle_each_keep_evidence(installation, monkeypatch):
    families = server(installation, monkeypatch,
        "def build():\n"
        "    missing = ModuleNotFoundError('absent', name='route_fu_optional')\n"
        "    wrapper = RuntimeError('wrapper')\n"
        "    wrapper.__cause__ = missing\n"
        "    missing.__context__ = wrapper\n"
        "    raise ExceptionGroup('shared cycle', [missing, wrapper, missing])\n")
    assert_unavailable_family(families)
