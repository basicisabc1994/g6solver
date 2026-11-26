# SPDX-License-Identifier: Apache-2.0
"""Compile-only identities for explicitly stubbed collection unit tests.

Callers supply the selected nodes; this helper neither discovers tests nor
executes repository modules. Real collection tests continue to use the runner.
"""
import ast
import types


def collection_answer(sources, answer):
    # Imported on use: reviewer harnesses import these test modules while an
    # older change_map, which has no code_identity, stands in for the current one.
    from g6.harness.change_map import code_identity

    found = {**answer, "bodies": dict(answer.get("bodies", {})), "codes": {}}
    definitions, compiled = {}, {}
    for file, source in sources.items():
        if not file.endswith(".py"):
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                line = min([node.lineno, *(item.lineno for item in node.decorator_list)])
                definitions.setdefault(node.name, []).append({"file": file, "line": line})
        pending = [compile(source, "<collection-fixture>", "exec", dont_inherit=True)]
        while pending:
            code = pending.pop()
            compiled[file, code.co_name, code.co_firstlineno] = code_identity(code)
            pending.extend(item for item in code.co_consts if isinstance(item, types.CodeType))
    for node in answer["nodes"]:
        name = node.split("[", 1)[0].rsplit("::", 1)[-1]
        if node not in found["bodies"]:
            candidates = definitions.get(name, [])
            assert len(candidates) == 1, (node, candidates)
            found["bodies"][node] = candidates[0]
        body = found["bodies"][node]
        key = body["file"], name, body["line"]
        if key in compiled:
            found["codes"][node] = compiled[key]
    return found
