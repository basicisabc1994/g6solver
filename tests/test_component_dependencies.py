# SPDX-License-Identifier: Apache-2.0
"""Every component stands on its own: it declares which other components it uses
(``__g6_requires__``), the declaration matches its real imports, and the graph has no cycles.
(docs/transition/DECISIONS.md #21)"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import g6
from g6.core.registry import FAMILIES, get_registry

SRC = Path(g6.__file__).parent
_IMPORT = re.compile(r"^\s*(?:from|import)\s+g6\.([\w.]+)", re.MULTILINE)
REFS = get_registry().refs
NAMES = sorted(REFS)


def _component(dotted: str) -> str:
    """The component an import of ``g6.<dotted>`` reaches: a family member by its own name, else the top package."""
    parts = dotted.split(".")
    return parts[1] if parts[0] in FAMILIES and len(parts) > 1 and not parts[1].startswith("_") else parts[0]


def _shared(dotted: str) -> list[Path]:
    """The files of the family's shared code (``g6.<family>._x``) that an import of ``g6.<dotted>`` reaches."""
    parts = dotted.split(".")
    if not (parts[0] in FAMILIES and len(parts) > 1 and parts[1].startswith("_")):
        return []
    base = SRC.joinpath(parts[0], parts[1])
    return sorted(base.rglob("*.py")) if base.is_dir() else [p for p in (base.with_suffix(".py"),) if p.is_file()]


def _actual(name: str) -> set[str]:
    """What a component imports. A member's shared family code (``g6.<family>._x``) is its own, so what that code
    imports is the member's too: a member that runs on its family's runtime requires what the runtime requires."""
    found: set[str] = set()
    todo, seen = sorted(SRC.joinpath(*REFS[name].module.split(".")[1:]).rglob("*.py")), set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for dotted in _IMPORT.findall(f.read_text(encoding="utf-8")):
            found.add(_component(dotted))
            todo += _shared(dotted)
    return found - {name, "core", REFS[name].family}


def _declared(name: str) -> set[str]:
    mod = importlib.import_module(REFS[name].module)
    assert hasattr(mod, "__g6_requires__"), f"{REFS[name].module} must declare __g6_requires__ (use () for none)"
    return set(mod.__g6_requires__)


@pytest.mark.parametrize("name", NAMES)
def test_declared_dependencies_match_imports(name: str) -> None:
    assert _declared(name) == _actual(name)


def test_component_graph_is_acyclic() -> None:
    graph = {n: _declared(n) for n in NAMES}
    state: dict[str, int] = {}

    def visit(n: str, path: tuple[str, ...]) -> None:
        if state.get(n) == 1:
            raise AssertionError("dependency cycle: " + " -> ".join((*path, n)))
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in graph.get(n, ()):
            visit(d, (*path, n))
        state[n] = 2

    for n in graph:
        visit(n, ())
