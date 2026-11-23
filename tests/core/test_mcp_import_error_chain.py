# SPDX-License-Identifier: Apache-2.0
"""Startup diagnostics must name the dependency beneath named or unnamed wrappers."""
from __future__ import annotations

import pytest


class TestBuildImportErrorChain:
    @pytest.mark.parametrize("entry", ("build", "main"))
    @pytest.mark.parametrize("chain", ("cause", "context"))
    @pytest.mark.parametrize("wrapper_name", (None, "fastmcp"))
    @pytest.mark.parametrize("missing", ("rq", "redis.asyncio", "mcp.server", "fastmcp.tools"))
    def test_innermost_dependency_controls_name_and_recovery(self, monkeypatch, entry, chain, wrapper_name, missing):
        from g6 import mcp

        def fail():
            try:
                raise ModuleNotFoundError("inner dependency", name=missing)
            except ImportError as exc:
                try:
                    raise ImportError("middle wrapper", name="another_wrapper") from exc
                except ImportError as middle:
                    if chain == "cause":
                        raise ImportError("outer wrapper", name=wrapper_name) from middle
                    raise ImportError("outer wrapper", name=wrapper_name)

        monkeypatch.setattr(mcp, "_quiet" if entry == "build" else "build_server", fail)
        with pytest.raises(SystemExit) as stopped:
            (mcp.build_server if entry == "build" else mcp.main)()
        message = str(stopped.value)
        assert missing in message
        if missing in {"mcp.server", "fastmcp.tools"}:
            assert "MCP extra" in message and "g6[mcp]" in message
        else:
            assert "MCP extra" not in message and "g6[mcp]" not in message
        assert isinstance(stopped.value.__cause__, ImportError)
        assert stopped.value.__cause__.name == wrapper_name

    def test_unnamed_inner_import_does_not_hide_named_missing_module(self):
        from g6.mcp import _import_error_message

        inner = ImportError("unnamed")
        outer = ImportError("named", name="rq")
        outer.__cause__ = inner
        assert "rq" in _import_error_message(outer)
        assert "MCP extra" not in _import_error_message(outer)

    def test_cyclic_chain_terminates_and_uses_inner_dependency(self):
        from g6.mcp import _import_error_message

        inner = ModuleNotFoundError("missing", name="rq")
        outer = ImportError("wrapper", name="fastmcp")
        outer.__cause__ = inner
        inner.__context__ = outer
        message = _import_error_message(outer)
        assert "rq" in message
        assert "MCP extra" not in message
