# SPDX-License-Identifier: Apache-2.0
"""Structural contracts independent of persistence and host isolation backends."""
import ast
from pathlib import Path


def test_distill_imports_no_model_mcp_or_protected_meta_layer():
    root = Path(__file__).resolve().parents[2] / "src" / "g6" / "distill"
    forbidden = ("g6.core.llm", "g6.mcp", "g6.metaharness", "subprocess", "socket", "http", "requests")
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name == prefix or name.startswith(prefix + ".") for name in names for prefix in forbidden), path


def test_catalogue_matches_the_existing_finding_classes():
    from typing import get_args
    from g6.distill.records import FindingClass
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "validation" / "own_code" / "ledger.py").read_text(encoding="utf-8"))
    classes = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "CLASSES" for target in node.targets))
    assert get_args(FindingClass) == classes
