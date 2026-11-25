"""Offline measurement of the shipped main server's describe-all boundary."""
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4


def test_describe_all_main_tools_within_budget(monkeypatch):
    disk = Path(__file__).resolve().parents[2] / ".bt_rev_rfu6_2" / "perf" / uuid4().hex
    disk.mkdir(parents=True)
    for name, value in {
        "G6_HOME": str(disk / "home"), "FASTMCP_HOME": str(disk / "fastmcp"),
        "FASTMCP_ENV_FILE": os.devnull, "FASTMCP_CHECK_FOR_UPDATES": "off",
        "G6_CLAUDE": r"C:\nonexistent\g6-no-host.exe",
        "G6_CODEX": r"C:\nonexistent\g6-no-host.exe",
    }.items():
        monkeypatch.setenv(name, value)
    from g6 import mcp
    from g6.mcp import dependencies, families, registrations

    for attr in ("_VALIDATED_DETAILS", "_RECORDED_SERVERS"):
        monkeypatch.setattr(families, attr, {})
    dependencies.optional_import_roots.cache_clear()
    try:
        began = perf_counter()
        server = mcp.build_server()
        tools = registrations._listed(server)
        build_seconds = perf_counter() - began
        assert len(tools) == 58
        began = perf_counter()
        for name, _ in tools:
            assert families.describe(name)["available"] is True
        seconds = perf_counter() - began
        print(f"build={build_seconds:.3f}s describe={seconds:.3f}s tools={len(tools)}")
        assert seconds <= 10.0
    finally:
        dependencies.optional_import_roots.cache_clear()

