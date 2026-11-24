"""Review 2: real imports and metadata must retain defect evidence."""
from importlib import import_module, invalidate_caches, metadata, util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def installation(patch, disk, required_root="rfu2_base", optional_root="rfu2_optional",
                 optional_extra="abstraction"):
    from g6.mcp import dependencies, families, registrations

    needs = {
        "g6": ["rfu2-base", f'rfu2-optional; extra == "{optional_extra}"',
               'rfu2-mcp; extra == "mcp"'],
        "rfu2-base": [], "rfu2-optional": [], "rfu2-mcp": [],
    }
    roots = {"g6": "g6", "rfu2-base": required_root,
             "rfu2-optional": optional_root, "rfu2-mcp": "rfu2_mcp"}
    for name, requirements in needs.items():
        folder = disk / (name.replace("-", "_") + "-1.0.dist-info")
        folder.mkdir(exist_ok=True)
        (folder / "METADATA").write_text(
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requirements), encoding="utf-8")
        (folder / "top_level.txt").write_text(roots[name] + "\n", encoding="utf-8")
    scan = metadata.distributions
    records = {d.metadata["Name"]: d for d in scan(path=[str(disk)])}
    patch.setattr(metadata, "distributions", lambda **kw: scan(path=[str(disk)]))
    patch.setattr(metadata, "requires", lambda name: records[name].requires)
    patch.syspath_prepend(str(disk))
    patch.setenv("G6_HOME", str(disk / "home"))
    patch.setenv("G6_CLAUDE", r"C:\nonexistent\g6-no-host.exe")
    patch.setenv("G6_CODEX", r"C:\nonexistent\g6-no-host.exe")
    patch.setattr(families, "_DETAILS", {})
    patch.setattr(registrations, "SERVERS", {"abstraction": "rfu2_server:build"})
    patch.delitem(sys.modules, "rfu2_server", raising=False)
    dependencies.optional_import_roots.cache_clear()
    invalidate_caches()
    return dependencies, families, registrations


@pytest.mark.parametrize("edge", ("__cause__", "__context__"))
@pytest.mark.parametrize("entry", ("family", "startup"))
def test_cycle_keeps_installed_member_defect(monkeypatch, edge, entry):
    from g6 import mcp

    tmp_path = Path(__file__).resolve().parent / f"_disk_{entry}_{edge}"
    tmp_path.mkdir(exist_ok=True)
    dependencies, families, registrations = installation(
        monkeypatch, tmp_path, optional_extra="mcp" if entry == "startup" else "abstraction")
    (tmp_path / "rfu2_server.py").write_text(
        "from importlib import import_module\n"
        "errors = []\n"
        "for name in ('json.rfu2_missing_api', 'rfu2_optional'):\n"
        "    try:\n"
        "        import_module(name)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        errors.append(exc)\n"
        "group = ExceptionGroup('mixed imports', errors)\n"
        f"setattr(errors[0], {edge!r}, group)\n"
        "FAILURE = ImportError('server loader')\n"
        "FAILURE.__cause__ = group\n"
        "def build():\n"
        "    raise FAILURE\n", encoding="utf-8")
    invalidate_caches()
    server = import_module("rfu2_server")
    assert server.errors[0].name == "json.rfu2_missing_api"
    assert util.find_spec("json") is not None
    assert util.find_spec("rfu2_optional") is None
    try:
        if entry == "family":
            with pytest.raises(ImportError) as stopped:
                families.describe("logic")
            assert stopped.value is server.FAILURE
        else:
            monkeypatch.setattr(mcp, "_quiet", lambda: registrations._load("rfu2_server:build"))
            with pytest.raises(SystemExit) as stopped:
                mcp.build_server()
            assert stopped.value.__cause__ is server.FAILURE
            assert "MCP extra" not in str(stopped.value), str(stopped.value)
    finally:
        dependencies.optional_import_roots.cache_clear()
        sys.modules.pop("rfu2_server", None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows case-insensitive import mode")
def test_required_dynamic_case_alias_is_not_optional():
    tmp_path = Path(__file__).resolve().parent / "_disk_case"
    tmp_path.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(PYTHONCASEOK="1", PYTHONDONTWRITEBYTECODE="1",
                       G6_CLAUDE=r"C:\nonexistent\g6-no-host.exe",
                       G6_CODEX=r"C:\nonexistent\g6-no-host.exe")
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--case-probe", str(tmp_path)],
        env=environment, text=True, capture_output=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["propagated"], result


def case_probe(disk):
    with pytest.MonkeyPatch.context() as patch:
        dependencies, families, _ = installation(
            patch, disk, required_root="RFU2-Shared", optional_root="rfu2-shared")
        module_file = disk / "RFU2-Shared.py"
        module_file.write_text("value = 73\n", encoding="utf-8")
        invalidate_caches()
        # Prove that both declarations refer to the installed required file.
        for name in ("RFU2-Shared", "rfu2-shared"):
            module = import_module(name)
            assert module.value == 73
            assert Path(module.__file__).samefile(module_file)
            sys.modules.pop(name, None)
        module_file.unlink()
        invalidate_caches()
        assert util.find_spec("rfu2-shared") is None
        (disk / "rfu2_server.py").write_text(
            "from importlib import import_module\n"
            "def build():\n"
            "    return import_module('rfu2-shared')\n", encoding="utf-8")
        invalidate_caches()
        try:
            reply = families.describe("load_state")
        except ModuleNotFoundError:
            return {"propagated": True}
        return {"propagated": False, "reply": reply,
                "optional": sorted(dependencies.optional_import_roots("abstraction,mcp"))}


if __name__ == "__main__" and sys.argv[1:2] == ["--case-probe"]:
    print(json.dumps(case_probe(Path(sys.argv[2]))))
