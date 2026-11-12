# SPDX-License-Identifier: Apache-2.0
"""Section 17 adds no model caller in 0.3.0: the inventory for the class "a new path that could send data to a
model". Every module of g6.harness.workflow is read; none may import a model caller or a network client."""
from __future__ import annotations

import ast
from pathlib import Path

import g6.harness.workflow

_PACKAGE = Path(g6.harness.workflow.__file__).parent
FORBIDDEN = ("g6.core.llm", "g6.core.host_models", "g6.core.model_calls", "g6.core.consent_callers", "g6.llm_router",
             "g6.agents", "g6.reasoning", "g6.metaharness", "anthropic", "openai", "httpx", "requests", "aiohttp",
             "urllib", "http", "socket", "subprocess")


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found |= {node.module} | {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in (
                "__import__", "import_module"):
            found.add("<dynamic import>")
    return found


def test_workflow_package_imports_no_model_caller():
    modules = sorted(_PACKAGE.rglob("*.py"))
    assert len(modules) >= 5
    for path in modules:
        bad = {name for name in _imports(path)
               if name == "<dynamic import>" or any(name == f or name.startswith(f + ".") for f in FORBIDDEN)}
        assert not bad, f"{path.name} imports {sorted(bad)}"
