# SPDX-License-Identifier: Apache-2.0
"""Inventories read from the code: every engine function that builds a model-bound passage takes an ``Access`` and
calls ``ProjectStore.use`` before it returns, and every MCP tool that calls such a function builds its access from
the host binding. Later slices extend this file, never copy it."""
from __future__ import annotations

import ast
from pathlib import Path

import g6.knowledge
import g6.mcp

KNOWLEDGE = Path(g6.knowledge.__file__).parent
#: Names that build what a model may read of a passage: an item, a view of its text, or the passage itself.
BUILDERS = frozenset({"PassageItem", "View", "view_of"})
#: Modules of the engine whose public functions are inventoried.
ENGINE_MODULES = ("_engine.py",)


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _reach(functions: dict[str, ast.FunctionDef], start: str) -> set[str]:
    """Every name ``start`` references, through the module's own functions it calls."""
    seen, todo, names = set(), [start], set()
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        found = _names(functions[current])
        names |= found
        todo += [n for n in found if n in functions]
    return names


def _returns_passages(functions: dict[str, ast.FunctionDef], name: str) -> bool:
    """Whether what ``name`` returns can hold passage content: it reaches a builder of model-bound passage output."""
    return bool(_reach(functions, name) & BUILDERS)


def passage_functions() -> dict[str, tuple[ast.FunctionDef, dict[str, ast.FunctionDef]]]:
    out = {}
    for module in ENGINE_MODULES:
        functions = _functions(ast.parse((KNOWLEDGE / module).read_text(encoding="utf-8")))
        for name, node in functions.items():
            if not name.startswith("_") and _returns_passages(functions, name):
                out[f"{module}:{name}"] = (node, functions)
    return out


def test_every_engine_function_that_returns_passages_takes_an_access():
    found = passage_functions()
    assert {"_engine.py:search", "_engine.py:fetch"} <= set(found)
    for name, (node, functions) in found.items():
        annotated = {a.arg: ast.unparse(a.annotation) for a in (*node.args.args, *node.args.kwonlyargs) if a.annotation}
        assert "Access" in annotated.values(), f"{name} returns passages without taking an Access"
        uses = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "use"]
        assert len(uses) >= 2, f"{name} must call ProjectStore.use when it starts and again immediately before it returns"
        last_use = max(u.lineno for u in uses)
        builds = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and (n.func.id in BUILDERS or n.func.id in functions and _reach(functions, n.func.id) & BUILDERS)]
        assert builds and min(builds) > last_use, f"{name} builds an item before its last use() check"


def test_the_item_builder_holds_a_view_and_never_the_raw_text():
    functions = _functions(ast.parse((KNOWLEDGE / "_engine.py").read_text(encoding="utf-8")))
    built = ast.unparse(functions["_item"])
    assert "view_of(passage.text" in built and "label_view(member.relative)" in built
    assert "text=passage.text" not in built.replace("view_of(passage.text", "")


def test_every_mcp_tool_that_reaches_the_engine_builds_its_access_from_the_host_binding():
    tree = ast.parse((Path(g6.mcp.__file__).parent / "knowledge.py").read_text(encoding="utf-8"))
    engine_functions = set(_functions(ast.parse((KNOWLEDGE / "_engine.py").read_text(encoding="utf-8"))))
    tools = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
             and n.name.startswith("g6_")]
    assert len(tools) >= 7
    for tool in tools:
        arguments = {a.arg for a in (*tool.args.args, *tool.args.kwonlyargs)}
        assert not arguments & {"project", "principal", "binding", "roots", "destination", "provider", "answer",
                                "name", "approved"}, tool.name
        # every engine function the tool names, called directly or handed to a worker thread
        called = {n.attr for n in ast.walk(tool) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                  and n.value.id == "engine"} & engine_functions
        assert called, tool.name
        source = ast.unparse(tool)
        assert "host_binding()" in source, tool.name
        if called - {"preview", "commit", "excluded_counts", "shared_with"}:   # everything that reads or changes a store
            assert "access(" in source and "_destination()" in source, tool.name
        assert "bind_output" in source or "_ok(" in source, tool.name


#: Tables of the registry that are the index's own membership and bookkeeping, not a stored consumer of passages.
MEMBERSHIP_TABLES = frozenset({"registrations", "generations", "members", "member_passages", "tombstones", "receipts",
                               "dependents", "rereads", "retained", "snapshot_sources"})


def stored_consumers() -> dict[str, str]:
    """Every function of the knowledge family's shared modules and of the MCP module that writes passage ids
    somewhere a later call can read them back, outside the index's own membership: its name to its source."""
    found = {}
    files = [*sorted(KNOWLEDGE.glob("_*.py")), Path(g6.mcp.__file__).parent / "knowledge.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            source = ast.unparse(node)
            if "passage_id" not in source and "passage_ids" not in source and "items" not in source:
                continue
            inserts = {t for t in __import__("re").findall(r"INSERT (?:OR \w+ )?INTO (\w+)", source)}
            writes_file = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                              and n.func.attr in ("write_text", "write_bytes", "dump", "keep_snapshot")
                              for n in ast.walk(node))
            if (inserts - MEMBERSHIP_TABLES) or (writes_file and ("passage_id" in source or "passage_ids" in source)):
                found[f"{path.name}:{node.name}"] = source
    return found


def test_every_stored_consumer_registers_a_dependent():
    found = stored_consumers()
    assert "_snapshots.py:write" in found, sorted(found)
    for name, source in found.items():
        if name == "_registry.py:keep_snapshot":   # the table's own writer; its one caller is the consumer below
            continue
        assert ".depend(" in source, f"{name} stores passage ids without registering a dependent"
    # and every stored consumer that is read back asks whether it was invalidated first
    snapshots = ast.unparse(_functions(ast.parse((KNOWLEDGE / "_snapshots.py").read_text(encoding="utf-8")))["reconstruct"])
    assert snapshots.index(".invalidated(") < snapshots.index(".retained(")