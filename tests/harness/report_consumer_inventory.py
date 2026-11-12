"""Inventory raw report field reads, including scalar and persisted decisions."""
from __future__ import annotations

import ast

# These are every product entry point that receives a repo_tests run (including
# helpers its callers hand that run to). Exclude similarly named summary rows.
RECEIVERS = {
    "harness/patch_review.py": {"ran", "first", "second", "out"},
    "harness/verification_search.py": {"ran"},
    "harness/completion_loop.py": {"ran"},
    "workforce/receipt_store.py": {"found"},
    "bundles/agent_change.py": {"found", "out"},
    "workforce/bench.py": {"found"},
    "algorithm_design/autonomous.py": {"raw"},
    "metaharness/coding.py": {"result", "got"},
    "workforce/repo_tests.py": {"ran", "baseline"},
}
ONLY = {"workforce/bench.py": {"_in_place"}, "algorithm_design/autonomous.py": {"_observed"}}


def consumer_fields(source, relative):
    """Field accesses plus the literal vocabularies used by dynamic key reads."""
    tree = ast.parse(source)
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = {part.value for part in ast.walk(node.value)
                                            if isinstance(part, ast.Constant) and type(part.value) is str}
    found = {}

    def visit(node, scope="", function=None):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope, function = node.name, node
        if relative in ONLY and scope not in ONLY[relative]:
            for child in ast.iter_child_nodes(node):
                visit(child, scope, function)
            return
        receiver, key = None, None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args):
            receiver, key = node.func.value, node.args[0]
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            receiver, key = node.value, node.slice
        if isinstance(receiver, ast.Name) and receiver.id in RECEIVERS[relative]:
            if isinstance(key, ast.Constant) and type(key.value) is str:
                found.setdefault(scope, set()).add(key.value)
            elif isinstance(key, ast.Name):
                # Dynamic reads iterate the literal tuples or module vocabularies.
                if scope == "_listing" and key.id == "key":
                    found.setdefault(scope, set()).update(constants.get("LISTINGS", set()))
                for loop in ast.walk(function):
                    if isinstance(loop, (ast.comprehension, ast.For)) and ast.unparse(loop.target) == key.id:
                        vocabulary = loop.iter
                        values = (constants.get(vocabulary.id, set()) if isinstance(vocabulary, ast.Name) else
                                  {part.value for part in ast.walk(vocabulary)
                                   if isinstance(part, ast.Constant) and type(part.value) is str})
                        found.setdefault(scope, set()).update(values)
        for child in ast.iter_child_nodes(node):
            visit(child, scope, function)

    visit(tree)
    return {name: sorted(fields) for name, fields in sorted(found.items()) if fields}
