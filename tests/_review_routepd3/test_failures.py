"""Review 3 counterexamples, with server modules imported from real disk."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def tmp_path(request):
    # Pre-created through the shell: this machine denies traversal into
    # directories made by Python's tempfile, independently of G6.
    path = Path(__file__).parent / "_disk" / request.node.name
    assert path.is_dir()
    return path


@pytest.fixture(autouse=True)
def review_environment(monkeypatch):
    for key in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(key, r"C:\nonexistent\g6-no-host.exe")
    from g6.mcp import dependencies
    dependencies.optional_import_roots.cache_clear()
    yield
    dependencies.optional_import_roots.cache_clear()


def disk_server(tmp_path, monkeypatch, source):
    from g6.mcp import families, registrations

    name = "routepd3_disk_server"
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": f"{name}:build"})
    monkeypatch.setattr(families, "_DETAILS", {})
    importlib.invalidate_caches()
    return families


def require_defect(families, exception, text):
    try:
        reply = families.describe("load_state")
    except exception as exc:
        assert text in str(exc)
        return
    pytest.fail(f"Expected {exception.__name__}, got {reply!r}")


def test_installed_package_missing_symbol_is_a_defect(tmp_path, monkeypatch):
    import numpy
    assert numpy.__file__
    families = disk_server(tmp_path, monkeypatch,
        "def build():\n"
        "    from numpy import g6_review_api_that_does_not_exist\n")

    # Python sets ImportError.name='numpy' for an invalid API import even
    # though numpy is present. Installing the extra does not repair this code.
    require_defect(families, ImportError, "cannot import name")


def test_suppressed_optional_import_context_does_not_hide_defect(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hypothesis", None)
    families = disk_server(tmp_path, monkeypatch,
        "def build():\n"
        "    try:\n"
        "        import hypothesis\n"
        "    except ImportError:\n"
        "        raise RuntimeError('fallback schema regression') from None\n")

    # This is a new defect in the fallback, explicitly detached from the
    # handled import. Its suppressed context must not become an install hint.
    require_defect(families, RuntimeError, "fallback schema regression")


def test_grouped_missing_optional_dependency_preserves_family(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hypothesis", None)
    families = disk_server(tmp_path, monkeypatch,
        "def build():\n"
        "    failures = []\n"
        "    try:\n"
        "        import hypothesis\n"
        "    except ModuleNotFoundError as exc:\n"
        "        failures.append(exc)\n"
        "    if failures:\n"
        "        raise ExceptionGroup('optional imports', failures)\n")

    reply = families.describe("logic")
    assert reply["available"] is True
    rows = [r for r in reply["members"] if r["id"].startswith("mcp:abstraction/")]
    assert rows and all(r["reason"] == "server_unavailable" for r in rows)


def test_editable_required_mcp_module_never_becomes_optional(tmp_path, monkeypatch):
    from importlib import metadata
    from g6.mcp import dependencies

    # Real PEP 376 metadata on disk. The editable fastmcp distribution has a
    # .pth RECORD entry, but no top_level.txt or package files in its RECORD.
    # That layout is valid for editable installs and produces no import map.
    requirements = {
        "g6": ['fastmcp; extra == "mcp"', 'mcp; extra == "mcp"',
               'numpy; extra == "abstraction"', 'hypothesis; extra == "abstraction"'],
        "fastmcp": [], "mcp": [],
    }
    for name, needs in requirements.items():
        directory = tmp_path / f"{name}-1.0.dist-info"
        assert directory.is_dir()  # pre-created by the shell
        text = f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
        text += "".join(f"Requires-Dist: {need}\n" for need in needs)
        (directory / "METADATA").write_text(text, encoding="utf-8")
        record = ("_fastmcp.pth,,\n" if name == "fastmcp" else f"{name}/__init__.py,,\n")
        (directory / "RECORD").write_text(record, encoding="utf-8")

    (tmp_path / "_fastmcp.pth").write_text("editable-source\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(tmp_path)])}
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(tmp_path)]))
    assert "fastmcp" not in metadata.packages_distributions()
    monkeypatch.setattr(dependencies, "metadata", SimpleNamespace(
        requires=lambda name: records[name].requires,
        packages_distributions=metadata.packages_distributions,
    ))
    monkeypatch.setitem(sys.modules, "fastmcp", None)
    families = disk_server(tmp_path, monkeypatch, "def build():\n    import fastmcp\n")

    # fastmcp is required by g6[mcp], so this is a broken base install, not
    # an absent optional abstraction dependency.
    require_defect(families, ModuleNotFoundError, "fastmcp")
