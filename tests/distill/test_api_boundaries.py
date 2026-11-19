# SPDX-License-Identifier: Apache-2.0
"""Structural contracts independent of persistence and host isolation backends."""
import ast
from pathlib import Path

import pytest


def _check_imports(filename, source):
    forbidden = ("g6.mcp", "g6.metaharness", "subprocess", "socket", "http", "requests")
    if filename == "acceptance_gate.py":
        # The protected evaluator reuses the metaharness gate and confined runner.
        restricted = tuple(p for p in forbidden if p not in ("g6.metaharness", "subprocess")) + ("g6.core.llm",)
    else:
        restricted = forbidden + (() if filename == "synthesis.py" else ("g6.core.llm",))
    if filename == "graph.py":
        restricted += ("g6.core.model_calls",)
    for node in ast.walk(ast.parse(source)):
        if filename == "graph.py":
            # Deliberate v3 design change, not a weakening: only exception and counter names cross
            # this boundary; the model caller remains injected.
            allowed = {"g6.core.llm": {"LLMError"}, "g6.core.model_calls": {"counting"}}
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in allowed:
                assert all(alias.name in allowed[node.module] for alias in node.names), filename
                continue
        names = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
            [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
        if isinstance(node, ast.ImportFrom):
            names += [f"{node.module}.{alias.name}" for alias in node.names]
        assert not any(name == prefix or name.startswith(prefix + ".") for name in names for prefix in restricted), filename


def test_only_synthesis_may_import_model_caller_and_no_mcp_or_protected_meta_layer():
    root = Path(__file__).resolve().parents[2] / "src" / "g6" / "distill"
    for path in root.glob("*.py"):
        _check_imports(path.name, path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", ["from g6.core.llm import LLMError",
                                 "from g6.core.model_calls import counting"])
def test_graph_allows_only_exception_and_counter_imports(source):
    _check_imports("graph.py", source)


@pytest.mark.parametrize("source", [
    "from g6.core.llm import provider_from_env",
    "from g6.core.llm import LLMError, provider_from_env",
    "from g6.core.llm import FakeLLM as LLMError",
    "from g6.core.llm import *",
    "import g6.core.llm",
    "from g6.core import llm",
    "from g6.core.model_calls import note_model_call",
    "import g6.core.model_calls",
    "from g6.core import model_calls",
])
def test_graph_rejects_caller_and_other_model_imports(source):
    with pytest.raises(AssertionError):
        _check_imports("graph.py", source)


def test_catalogue_matches_the_existing_finding_classes():
    from typing import get_args
    from g6.distill.records import FindingClass
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "validation" / "own_code" / "ledger.py").read_text(encoding="utf-8"))
    classes = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "CLASSES" for target in node.targets))
    assert get_args(FindingClass) == classes
