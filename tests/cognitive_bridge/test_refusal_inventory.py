# SPDX-License-Identifier: Apache-2.0
"""One maker of refusals (review 1, class 1). The package's own source is parsed and every place that gives a result a
reliability state is listed: each ``blocked-escalated`` it writes, and each ``completion_state`` it sets to anything but
the literal ``qualified-draft``. Every such place must be ``codes.refusal``, the one function that sets the state, a
registered ``G6_E_*`` code and a ``warning_card``; a ``blocked-escalated`` read in a comparison is not a write, and
``codes.STATES`` names the vocabulary. The list is built from the code itself, so a new refusal path is found wherever it
is written."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "g6" / "cognitive_bridge"
BLOCKED, QUALIFIED, FIELD = "blocked-escalated", "qualified-draft", "completion_state"
#: (file, enclosing function or module-level name) allowed to write a state that is not qualified-draft.
MAKER = {("codes.py", "refusal"), ("codes.py", "STATES")}


@dataclass(frozen=True)
class Site:
    file: str
    line: int
    where: str  # the enclosing function, or the module-level name assigned
    kind: str   # "read", "writes blocked-escalated", "sets completion_state"

    def __str__(self) -> str:
        return f"{self.file}:{self.line} in {self.where}: {self.kind}"


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _where(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    at = node
    while id(at) in parents:
        at = parents[id(at)]
        if isinstance(at, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return at.name
        if isinstance(at, (ast.Assign, ast.AnnAssign)) and isinstance(parents.get(id(at)), ast.Module):
            targets = at.targets if isinstance(at, ast.Assign) else [at.target]
            return ",".join(t.id for t in targets if isinstance(t, ast.Name)) or "<module>"
    return "<module>"


def _read(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """The literal is an operand of a comparison (``x == "blocked-escalated"``, ``x in {...}``): it is read."""
    at = parents.get(id(node))
    while isinstance(at, (ast.Tuple, ast.List, ast.Set)):
        at = parents.get(id(at))
    return isinstance(at, ast.Compare)


def _qualified(value: ast.AST | None) -> bool:
    return isinstance(value, ast.Constant) and value.value == QUALIFIED


def _target_is_field(target: ast.AST) -> bool:
    if isinstance(target, ast.Name):
        return target.id == FIELD
    if isinstance(target, ast.Attribute):
        return target.attr == FIELD
    return (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
            and target.slice.value == FIELD)


def inventory() -> list[Site]:
    """Every place in the package that reads or writes a result's reliability state, from the package's own source."""
    sites: list[Site] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parents(tree)
        name = path.relative_to(PACKAGE).as_posix()

        def add(node: ast.AST, kind: str) -> None:
            sites.append(Site(name, getattr(node, "lineno", 0), _where(node, parents), kind))

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == BLOCKED:
                add(node, "read" if _read(node, parents) else f"writes {BLOCKED}")
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == FIELD and not _qualified(value):
                        add(key, f"sets {FIELD}")
            elif isinstance(node, ast.keyword) and node.arg == FIELD and not _qualified(node.value):
                add(node.value, f"sets {FIELD}")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(_target_is_field(t) for t in targets) and node.value is not None and not _qualified(node.value):
                    add(node, f"sets {FIELD}")
    return sites


def test_every_state_but_qualified_draft_is_written_by_the_one_maker_of_refusals():
    sites = inventory()
    offenders = [str(s) for s in sites if s.kind != "read" and (s.file, s.where) not in MAKER]
    assert offenders == [], "written outside codes.refusal:\n" + "\n".join(offenders)


def test_the_inventory_finds_the_maker_itself():
    # the listing is not vacuous: it finds the maker's own write, so a renamed or moved maker fails here
    writes = {(s.file, s.where) for s in inventory() if s.kind != "read"}
    assert ("codes.py", "refusal") in writes


def test_the_maker_refuses_a_code_nobody_registered():
    import pytest

    from g6.cognitive_bridge import codes

    with pytest.raises(ValueError, match="registered"):
        codes.refusal("G6_E_COGNITIVE_NOT_A_CODE", "a reason")
    for code in codes.CODES:
        card = codes.refusal(code, "a reason")
        assert card["completion_state"] == BLOCKED and card["code"] == card["warning_card"]["code"] == code
