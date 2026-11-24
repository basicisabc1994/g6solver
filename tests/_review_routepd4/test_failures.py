"""Review 4 counterexamples; disk fixtures stay below this directory."""
from __future__ import annotations

import importlib
from importlib import machinery, metadata, util
from pathlib import Path
import sys

import pytest


DISK = Path(__file__).parent / "_disk"
ROOT = "g6_review_optional"


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    from g6.mcp import dependencies

    monkeypatch.setenv("G6_HOME", str(DISK / "home"))
    for key in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(key, r"C:\nonexistent\g6-no-host.exe")
    dependencies.optional_import_roots.cache_clear()
    yield
    dependencies.optional_import_roots.cache_clear()
    sys.modules.pop("routepd4_server", None)


def metadata_environment(monkeypatch, base_record="base_bridge/__init__.py,,\n"):
    """Use real PEP 376 metadata without depending on the host's extra set."""
    from g6.mcp import dependencies

    needs = {
        "g6": ["base-bridge", 'g6-review-optional; extra == "abstraction"'],
        "base_bridge": [],
        "g6_review_optional": [],
    }
    for name, requirements in needs.items():
        folder = DISK / "metadata" / f"{name}-1.0.dist-info"
        assert folder.is_dir()  # Created by PowerShell, not Python's tempfile.
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name.replace('_', '-')}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {r}\n" for r in requirements),
            encoding="utf-8",
        )
        (folder / "RECORD").write_text(
            base_record if name == "base_bridge" else f"{name}/__init__.py,,\n",
            encoding="utf-8",
        )
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(DISK / "metadata")])}
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(DISK / "metadata")]))
    monkeypatch.setattr(metadata, "requires", lambda name: records[name.replace('_', '-')].requires)
    dependencies.optional_import_roots.cache_clear()
    assert util.find_spec(ROOT) is None
    return dependencies


def disk_server(monkeypatch, source):
    from g6.mcp import families, registrations

    (DISK / "routepd4_server.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(DISK))
    monkeypatch.delitem(sys.modules, "routepd4_server", raising=False)
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "routepd4_server:build"})
    monkeypatch.setattr(families, "_DETAILS", {})
    importlib.invalidate_caches()
    return families


def test_named_importerror_without_module_not_found_is_not_an_install_hint(monkeypatch):
    metadata_environment(monkeypatch)
    native = DISK / "extension_assets" / (ROOT + machinery.EXTENSION_SUFFIXES[0])
    native.write_bytes(b"broken native extension")
    assert native.is_file() and util.find_spec(ROOT) is None
    families = disk_server(monkeypatch,
        "from importlib import machinery, util\n"
        "from pathlib import Path\n"
        "def build():\n"
        "    file = Path(__file__).with_name('extension_assets') / (\n"
        "        'g6_review_optional' + machinery.EXTENSION_SUFFIXES[0])\n"
        "    spec = util.spec_from_file_location('g6_review_optional', file)\n"
        "    return util.module_from_spec(spec)\n")
    from g6.mcp import registrations
    with pytest.raises(ImportError) as raw:
        registrations._load("routepd4_server:build")
    assert raw.value.name == ROOT and not isinstance(raw.value, ModuleNotFoundError)
    with pytest.raises(ImportError):
        families.describe("load_state")


def test_native_required_record_is_not_reclassified_as_optional(monkeypatch):
    suffix = machinery.EXTENSION_SUFFIXES[0]
    metadata_environment(monkeypatch, f"{ROOT}{suffix},,\n")
    native = DISK / "metadata" / f"{ROOT}{suffix}"
    native.write_bytes(b"")
    # Python's own reader recognizes the required distribution's native module.
    assert "base-bridge" in metadata.packages_distributions()[ROOT]
    native.unlink()  # A broken required installation, not an optional omission.
    families = disk_server(monkeypatch, f"def build():\n    import {ROOT}\n")
    with pytest.raises(ModuleNotFoundError, match=ROOT):
        families.describe("load_state")


def test_deep_visible_optional_chain_keeps_family_discoverable(monkeypatch):
    metadata_environment(monkeypatch)
    families = disk_server(monkeypatch,
        "def build():\n"
        "    try:\n"
        "        import g6_review_optional\n"
        "    except ModuleNotFoundError as exc:\n"
        "        failure = exc\n"
        "    for _ in range(1200):\n"
        "        wrapper = RuntimeError('dependency wrapper')\n"
        "        wrapper.__cause__ = failure\n"
        "        failure = wrapper\n"
        "    raise failure\n")
    try:
        reply = families.describe("logic")
    except RecursionError:
        raise AssertionError("optional chain classification raised RecursionError") from None
    assert reply["available"] is True
    rows = [r for r in reply["members"] if r["id"].startswith("mcp:abstraction/")]
    assert rows and all(r["reason"] == "server_unavailable" for r in rows)


def test_missing_transitive_optional_dependency_keeps_family_discoverable(monkeypatch):
    metadata_environment(monkeypatch)
    optional_metadata = DISK / "metadata" / "g6_review_optional-1.0.dist-info" / "METADATA"
    optional_metadata.write_text(
        optional_metadata.read_text(encoding="utf-8") + "Requires-Dist: g6-review-transitive\n",
        encoding="utf-8",
    )
    optional_module = DISK / (ROOT + ".py")
    optional_module.write_text("import g6_review_transitive\n", encoding="utf-8")
    families = disk_server(monkeypatch, f"def build():\n    import {ROOT}\n")
    try:
        reply = families.describe("logic")
        assert reply["available"] is True
        rows = [r for r in reply["members"] if r["id"].startswith("mcp:abstraction/")]
        assert rows and all(r["reason"] == "server_unavailable" for r in rows)
    finally:
        optional_module.unlink()


def test_startup_api_defect_in_installed_fastmcp_is_not_an_install_hint(monkeypatch):
    from g6 import mcp

    (DISK / "routepd4_server.py").write_text(
        "from fastmcp import g6_review_api_that_does_not_exist\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(DISK))
    monkeypatch.delitem(sys.modules, "routepd4_server", raising=False)
    monkeypatch.setenv("FASTMCP_ENV_FILE", "NUL")
    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "off")
    monkeypatch.setattr(mcp, "_quiet", lambda: importlib.import_module("routepd4_server"))
    with pytest.raises(SystemExit) as stopped:
        mcp.build_server()
    assert isinstance(stopped.value.__cause__, ImportError)
    assert not isinstance(stopped.value.__cause__, ModuleNotFoundError)
    assert util.find_spec("fastmcp") is not None
    assert "MCP extra" not in str(stopped.value), str(stopped.value)
