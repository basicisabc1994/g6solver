"""The budget language is finite; no conversion accepts a second language."""
import ast
from pathlib import Path

import pytest

from g6.harness import question_store as qs


@pytest.mark.parametrize("raw", [None, *map(str, range(11)), "", " ", " 1", "1 ", "01", "+1", "-1",
                                 "11", "1.0", "\u00b2", "\u0661", "\uff11", "1\n", "9" * 5000])
def test_only_the_ascii_budget_language(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
    else:
        monkeypatch.setenv(qs.BUDGET_ENV, raw)
    value, note = qs.default_budget()
    accepted = raw is None or raw in tuple(map(str, range(11)))
    assert value == (1 if raw is None else int(raw) if accepted else 0)
    assert bool(note) is not accepted


def test_inventory_one_environment_reader():
    root = Path(__file__).resolve().parents[2] / "src/g6"
    readers = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any((isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id == "BUDGET_ENV")
                       or (isinstance(n, ast.Constant) and n.value == qs.BUDGET_ENV)
                       or (isinstance(n, ast.Attribute) and n.attr == "BUDGET_ENV")
                       for n in ast.walk(node)):
                    readers.append((path.relative_to(root).as_posix(), node.name))
    assert readers == [("harness/question_store.py", "default_budget")]
