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
#: Every shared module of the family, read from the folder: the inventories below walk all of them.
SHARED_MODULES = tuple(sorted(p.name for p in KNOWLEDGE.glob("_*.py") if p.name != "__init__.py"))


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
    assert {"_engine.py:search", "_engine.py:fetch", "_engine.py:context"} <= set(found)
    for name, (node, functions) in found.items():
        annotated = {a.arg: ast.unparse(a.annotation) for a in (*node.args.args, *node.args.kwonlyargs) if a.annotation}
        assert "Access" in annotated.values(), f"{name} returns passages without taking an Access"
        # the function itself, or the one function of the module it hands the work to, checks use() twice and
        # builds no item before the second check
        checked = []
        for inner in [node, *(functions[n] for n in _names(node) if n in functions)]:
            uses = [n for n in ast.walk(inner) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "use"]
            builds = [n.lineno for n in ast.walk(inner) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and (n.func.id in BUILDERS or n.func.id in functions and _reach(functions, n.func.id) & BUILDERS)]
            if len(uses) >= 2 and builds and min(builds) > max(u.lineno for u in uses):
                checked.append(inner.name)
        assert checked, (f"{name} must call ProjectStore.use when it starts and again immediately before it returns, "
                         "and build no item before the second call")


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
    assert len(tools) >= 8
    for tool in tools:
        arguments = {a.arg for a in (*tool.args.args, *tool.args.kwonlyargs)}
        assert not arguments & {"project", "principal", "binding", "roots", "destination", "provider", "answer",
                                "name", "approved"}, tool.name
        # every engine function the tool names, called directly or handed to a worker thread
        called = {n.attr for n in ast.walk(tool) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                  and n.value.id == "engine"} & engine_functions
        # the dispatcher, the graph and the planner take the same access; the jobs take the host binding itself
        other = {n.value.id for n in ast.walk(tool) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                 and n.value.id in ("_dispatch", "_jobs", "_graph", "_planner")}
        assert called or other, tool.name
        called |= other & {"_dispatch", "_graph", "_planner"}
        source = ast.unparse(tool)
        assert "host_binding()" in source, tool.name
        if called - {"preview", "commit", "discard_preview", "excluded_counts", "shared_with"}:   # everything that reads or changes a store
            assert "access(" in source and "_destination()" in source, tool.name
        assert "bind_output" in source or "_ok(" in source, tool.name


#: Tables of the registry that are the index's own membership and bookkeeping, not a stored consumer of passages.
MEMBERSHIP_TABLES = frozenset({"registrations", "generations", "members", "member_passages", "tombstones", "receipts",
                               "dependents", "rereads", "retained", "snapshot_sources", "jobs", "nodes", "edges",
                               "pending_removals"})


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

#: The registry's tables that hold a stored consumer's content, each with the registry method that writes it.
CONSUMER_TABLES = {"snapshots": "keep_snapshot", "normative_grants": "grant_normative", "proposed_edges": "propose_edge"}


def test_every_registry_table_is_membership_or_a_consumer_whose_writer_registers_a_dependent():
    import re

    registry = (KNOWLEDGE / "_registry.py").read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", registry))
    assert tables == MEMBERSHIP_TABLES | set(CONSUMER_TABLES), "a new table: is it membership, or a stored consumer?"
    files = [*sorted(KNOWLEDGE.glob("_*.py")), Path(g6.mcp.__file__).parent / "knowledge.py"]
    for table, writer in CONSUMER_TABLES.items():
        assert f"INSERT OR IGNORE INTO {table}" in ast.unparse(_functions_of_class(registry)[writer])
        callers = []
        for path in files:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name != writer and any(
                        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == writer
                        for n in ast.walk(node)):
                    callers.append((f"{path.name}:{node.name}", ast.unparse(node)))
        assert callers, f"nothing writes {table}"
        for name, source in callers:
            assert ".depend(" in source, f"{name} stores a consumer in {table} without registering a dependent"


def _functions_of_class(source: str) -> dict[str, ast.FunctionDef]:
    return {n.name: n for cls in ast.parse(source).body if isinstance(cls, ast.ClassDef) for n in cls.body
            if isinstance(n, ast.FunctionDef)}

def test_a_saved_pack_is_checked_again_at_every_use():
    functions = _functions(ast.parse((KNOWLEDGE / "_engine.py").read_text(encoding="utf-8")))
    source = ast.unparse(functions["pack"])
    assert "Access" in ast.unparse(functions["pack"].args)
    assert ".invalidated(" in source and ".use(" in source and "_granted(" in source
    assert source.index(".invalidated(") < source.index("saved['items']") or source.index(".use(") < source.rindex("saved['items']")
    readers = [name for name, node in functions.items() if "packs" in ast.unparse(node) and ".read_text(" in ast.unparse(node)]
    assert readers == ["pack"], "one function reads a saved pack, and it is the one that checks"

def test_every_function_that_reads_a_store_and_builds_a_view_asks_with_the_time():
    """Every shared module, derived from the folder: a public function that opens a project store and builds a
    model-bound view of passage text asks ``use`` or ``invalidated`` itself, and every such call gives the time
    (``at=``): expiry is checked against a clock at every use, not in one function."""
    missing, seen = [], []
    for module in SHARED_MODULES:
        functions = _functions(ast.parse((KNOWLEDGE / module).read_text(encoding="utf-8")))
        for name, node in functions.items():
            reached = _reach(functions, name)
            if name.startswith("_") or not reached & BUILDERS or "ProjectStore" not in reached:
                continue
            seen.append(f"{module}:{name}")
            checks = [c for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                      and c.func.attr in ("use", "invalidated")]
            if not checks or not all(any(k.arg == "at" for k in c.keywords) for c in checks):
                missing.append(f"{module}:{name}")
    assert {"_engine.py:search", "_engine.py:fetch", "_engine.py:context", "_snapshots.py:reconstruct",
            "_support.py:support_for"} <= set(seen), seen
    assert missing == []


def test_no_module_reads_a_registrations_state_alone_or_checks_use_without_the_time():
    """The one predicate: outside the registry, nothing compares a registration's state itself (``may_use`` and
    ``is_kept`` do), and every ``use``, ``invalidated`` and ``usable`` call anywhere gives the time."""
    import re

    modules = [KNOWLEDGE / m for m in SHARED_MODULES if m != "_registry.py"] + [Path(g6.mcp.__file__).parent / "knowledge.py"]
    for path in modules:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""\.state\s*[!=]=\s*["'](?:active|revoked|deleted)["']""", text), path.name
        for call in ast.walk(ast.parse(text)):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in (
                    "invalidated", "usable_registrations") or (
                    isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "use"
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "store"):
                assert any(k.arg == "at" for k in call.keywords), f"{path.name}:{call.lineno}"
    registry = _functions_of_class((KNOWLEDGE / "_registry.py").read_text(encoding="utf-8"))
    body = ast.unparse(registry["invalidated"])
    assert "datetime.now(UTC)" in body and "may_use(registration, at)" in body, (
        "a call that gives no time is answered for this moment: the expiry check is never skipped")
    assert "at: datetime" in ast.unparse(registry["use"].args) and "None" not in ast.unparse(registry["use"].args).split("at:")[1].split(",")[0]


def test_every_file_removal_goes_through_the_checked_removal():
    """Every ``unlink`` in the family's shared modules and the MCP module is the checked removal itself
    (``ProjectStore.remove_file``, which keeps a durable list of what it could not remove) or the removal of a
    preview listing before any registry exists (``discard_preview``, which says whether the file is gone)."""
    found = {}
    for path in [*(KNOWLEDGE / m for m in SHARED_MODULES), Path(g6.mcp.__file__).parent / "knowledge.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in ("unlink", "remove", "rmtree") \
                    and not (isinstance(call.func.value, ast.Name) and call.func.value.id in ("index", "writer", "wanted")):
                owner = call
                while owner in parents and not isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef):
                    owner = parents[owner]
                if call.func.attr != "unlink" and not isinstance(call.func.value, ast.Name):
                    continue
                if call.func.attr in ("unlink", "rmtree") or (isinstance(call.func.value, ast.Name) and call.func.value.id in ("os", "shutil")):
                    found[f"{path.name}:{getattr(owner, 'name', '?')}"] = call.func.attr
    assert set(found) == {"_registry.py:remove_file", "_engine.py:discard_preview", "_store.py:_write"}, found