from __future__ import annotations

import importlib
from pathlib import Path
import re
import sys

import pytest


@pytest.fixture
def tmp_path(request, monkeypatch):
    path = Path(__file__).parent / "_disk" / re.sub(
        r"[^A-Za-z0-9_-]", "_", request.node.name
    )
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("G6_HOME", str(path / "home"))
    return path


@pytest.mark.parametrize("entry", ["logic", "load_state"])
@pytest.mark.parametrize("stage", ["build", "list"])
def test_non_dependency_server_defect_is_not_optional_unavailability(
    tmp_path, monkeypatch, entry, stage
):
    from g6.core.tool_guidance import _DETAILS
    from g6.mcp import families, registrations

    source = (
        'def build():\n    raise RuntimeError("constructor regression")\n'
        if stage == "build"
        else 'class Server:\n    def get_tools(self):\n'
             '        raise RuntimeError("listing regression")\n'
             'def build():\n    return Server()\n'
    )
    (tmp_path / "review_broken_server.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "review_broken_server", raising=False)
    monkeypatch.setattr(
        registrations, "SERVERS", {"abstraction": "review_broken_server:build"}
    )
    monkeypatch.delitem(_DETAILS, "abstraction", raising=False)

    with pytest.raises(RuntimeError, match="regression"):
        families.describe(entry)


@pytest.mark.parametrize("wrapper_name", [None, "fastmcp"])
def test_build_import_error_names_missing_dependency_through_wrapper(
    tmp_path, monkeypatch, wrapper_name
):
    from g6 import mcp

    source = (
        'try:\n    import rq\nexcept ImportError as exc:\n'
        f'    raise ImportError("backend initialization failed", '
        f'name={wrapper_name!r}) from exc\n'
    )
    (tmp_path / "review_nested_import.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "review_nested_import", raising=False)
    monkeypatch.setitem(sys.modules, "rq", None)
    monkeypatch.setattr(
        mcp, "_quiet", lambda: importlib.import_module("review_nested_import")
    )

    with pytest.raises(SystemExit) as stopped:
        mcp.build_server()
    assert "rq" in str(stopped.value), str(stopped.value)
    assert "MCP extra" not in str(stopped.value), str(stopped.value)
