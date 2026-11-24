"""Importlib accepts module roots that are not Python identifiers."""
import importlib
from importlib import metadata, util
from pathlib import Path
import sys

from packaging.utils import canonicalize_name


def test_missing_optional_dynamic_import_root_keeps_family_visible(monkeypatch):
    from g6.mcp import dependencies, families, registrations

    disk = Path(__file__).resolve().parent / "_disk_alias"
    disk.mkdir(exist_ok=True)
    requirements = {
        "g6": ["rfu-base", 'rfu-optional; extra == "abstraction"',
               'rfu-mcp; extra == "mcp"'],
        "rfu-base": [], "rfu-optional": [], "rfu-mcp": [],
    }
    for name, needs in requirements.items():
        folder = disk / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir(exist_ok=True)
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {need}\n" for need in needs), encoding="utf-8",
        )
        root = "rfu-extra" if name == "rfu-optional" else name.replace("-", "_")
        (folder / "top_level.txt").write_text(root + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(disk)])}
    monkeypatch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(disk)]))
    monkeypatch.setattr(metadata, "requires", lambda name: records[canonicalize_name(name)].requires)
    monkeypatch.syspath_prepend(str(disk))
    monkeypatch.setattr(families, "_DETAILS", {})
    monkeypatch.setattr(registrations, "SERVERS", {"abstraction": "rfu_alias_server:build"})
    dependencies.optional_import_roots.cache_clear()
    try:
        module = disk / "rfu-extra.py"
        module.write_text("value = 7\n", encoding="utf-8")
        importlib.invalidate_caches()
        assert importlib.import_module("rfu-extra").value == 7
        assert metadata.packages_distributions()["rfu-extra"] == ["rfu-optional"]
        sys.modules.pop("rfu-extra", None)
        module.unlink()
        importlib.invalidate_caches()
        assert util.find_spec("rfu-extra") is None
        (disk / "rfu_alias_server.py").write_text(
            "def build():\n"
            "    from importlib import import_module\n"
            "    return import_module('rfu-extra')\n", encoding="utf-8",
        )
        importlib.invalidate_caches()
        reply = families.describe("logic")
        assert reply["available"] is True
        members = [row for row in reply["members"] if row["id"].startswith("mcp:abstraction/")]
        assert members and all(row["reason"] == "server_unavailable" for row in members)
    finally:
        dependencies.optional_import_roots.cache_clear()
        for name in ("rfu-extra", "rfu_alias_server"):
            sys.modules.pop(name, None)
