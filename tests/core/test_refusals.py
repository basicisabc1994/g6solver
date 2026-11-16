# SPDX-License-Identifier: Apache-2.0
"""The census of refusal declarations (``g6.core.refusals``): who repairs each refusal G6 writes is declared where it
is written, and every ``G6_E_*`` code written into a reply has exactly one declaration for the component or layer
that writes it. A new code without one fails here."""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "g6"
CODE = re.compile(r"G6_E_[A-Z0-9_]*[A-Z0-9](?![A-Z0-9_])")
#: The declarations themselves are not writers.
NOT_WRITERS = {"core/refusals.py"}


def _skipped(tree: ast.AST) -> set[int]:
    """The string nodes that do not write a code into a reply: docstrings, and every part of a comparison."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
        elif isinstance(node, ast.Compare):
            out |= {id(sub) for sub in ast.walk(node)}
    return out


def written(path: Path) -> set[str]:
    """Every whole ``G6_E_*`` code a module writes: in any string it holds, f-strings' parts included, but a docstring
    or a comparison (``code == "G6_E_X"``, ``"G6_E_X" in text``) only reads one. A family prefix (``G6_E_LOOP_``) is
    no code."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _skipped(tree)
    return {m.group(0) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
            for m in CODE.finditer(node.value)}


def _packages() -> dict[Path, str]:
    from g6.core.registry import get_registry

    registry = get_registry()
    out = {}
    for name, ref in registry.refs.items():
        if ref.source == "builtin":
            spec = importlib.util.find_spec(ref.module.split(":", 1)[0])
            out[Path(spec.origin).resolve().parent] = name
    return out


def census() -> dict[str, set[str]]:
    """Owner (``component:<name>`` or ``layer:<top folder>``) -> the codes it writes."""
    packages, root = _packages(), SRC.resolve()
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in NOT_WRITERS:
            continue
        codes = written(path)
        if not codes:
            continue
        owner = next((f"component:{packages[p]}" for p in path.resolve().parents
                      if p in packages and (p == root or root in p.parents)), None)
        if owner is None:
            parts = path.relative_to(SRC).parts
            owner = "layer:" + (parts[0] if len(parts) > 1 else "g6")
        found.setdefault(owner, set()).update(codes)
    return found


@pytest.fixture(scope="module")
def written_by() -> dict[str, set[str]]:
    return census()


def test_the_census_reads_writes_and_not_reads(tmp_path) -> None:
    path = tmp_path / "probe.py"
    path.write_text('"""G6_E_IN_A_DOCSTRING."""\n'
                    'X = "G6_E_WRITTEN"\n'
                    'def f(code):\n'
                    '    """G6_E_ALSO_A_DOCSTRING"""\n'
                    '    if code == "G6_E_COMPARED" or "G6_E_MEMBER" in code:\n'
                    '        return f"[G6_E_IN_AN_FSTRING] {code}"\n'
                    '    return code.startswith("G6_E_LOOP_")\n', encoding="utf-8")
    assert written(path) == {"G6_E_WRITTEN", "G6_E_IN_AN_FSTRING"}


def test_every_code_g6_writes_has_exactly_one_declaration_for_its_writer(written_by) -> None:
    from g6.core import refusals

    declared = refusals.component_declarations()
    assert len(written_by) > 60 and sum(len(c) for c in written_by.values()) > 1000
    missing = []
    for owner, codes in sorted(written_by.items()):
        kind, name = owner.split(":", 1)
        for code in sorted(codes):
            if kind == "component":
                if code not in declared.get(name, {}):
                    missing.append((owner, code))
            elif code not in refusals.LAYERS or name not in refusals.LAYERS[code][1].split():
                missing.append((owner, code))
    assert not missing, f"{len(missing)} codes without a declaration: {missing[:40]}"


def test_no_declaration_names_a_code_its_owner_does_not_write(written_by) -> None:
    from g6.core import refusals

    stale = [(name, code) for name, codes in refusals.component_declarations().items() for code in codes
             if code not in written_by.get(f"component:{name}", set())]
    assert not stale, stale
    layers: dict[str, set[str]] = {}
    for owner, codes in written_by.items():
        if owner.startswith("layer:"):
            for code in codes:
                layers.setdefault(code, set()).add(owner.split(":", 1)[1])
    wrong = {code: (row[1], sorted(layers.get(code, ()))) for code, row in refusals.LAYERS.items()
             if set(row[1].split()) != layers.get(code, set())}
    assert not wrong, wrong


def test_a_component_declares_in_its_package_or_in_the_overlay_never_both() -> None:
    from g6.core import refusals

    overlaid = set(refusals.OVERLAY)
    in_packages = set(refusals.package_declarations())
    assert not overlaid & in_packages, sorted(overlaid & in_packages)
    bound = set(refusals.BOUND_COMPONENTS)
    assert overlaid <= bound and not in_packages & bound


def test_every_declaration_is_one_class_or_a_tool_g6_offers() -> None:
    from g6.core import refusals
    from g6.skills import corpus

    data = corpus.load()
    tools = {e["call"]["tool"] for e in data["entries"] if e["kind"] == "tool"}
    ops = {e["id"][3:] for e in data["entries"] if e["kind"] == "op"}
    every = [r for codes in refusals.component_declarations().values() for r in codes.values()]
    every += [refusals.layer(code) for code in refusals.LAYERS]
    for row in every:
        assert row.fix_by in refusals.CLASSES or (
            row.fix_by.startswith("tool:") and row.fix_by[5:] in tools | ops), (row.code, row.fix_by)
        assert row.words.strip() and row.words == " ".join(row.words.split()), row.code


def test_a_declaration_with_a_code_twice_is_refused(tmp_path) -> None:
    from g6.core import refusals

    path = tmp_path / "__init__.py"
    path.write_text('__g6_refusals__ = {"G6_E_A": "input", "G6_E_A": "person"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="G6_E_A"):
        refusals.read_package(path)
    path.write_text('__g6_refusals__ = {"G6_E_A": "nobody"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="G6_E_A"):
        refusals.read_package(path)


def test_an_undeclared_code_resolves_to_the_person() -> None:
    from g6.core import refusals

    found = refusals.resolve("G6_E_NO_ONE_EVER_DECLARED_THIS")
    assert found.fix_by == "person" and not found.declared


def test_a_named_component_never_borrows_another_components_declaration() -> None:
    from g6.core import refusals

    assert refusals.resolve("G6_E_DECISION_INPUT").fix_by == "input"
    assert refusals.resolve("G6_E_DECISION_INPUT", component="decision_making").fix_by == "input"
    assert refusals.resolve("G6_E_DECISION_INPUT", component="knowledge_chunker").fix_by == "person"
