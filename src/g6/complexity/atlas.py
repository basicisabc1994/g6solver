# SPDX-License-Identifier: Apache-2.0
"""Conservative Python AST atlas over an explicit file map. Never imports or executes target code."""
from __future__ import annotations

import ast
from collections.abc import Mapping

from g6.code.design_governance.graph import build
from g6.complexity.ir import CIR, ComplexityFinding, Dependency, Effect, FrozenIntent, Snapshot, StateVariable
from g6.core.ledger import digest

_DYNAMIC = {"eval", "exec", "__import__", "getattr", "setattr", "delattr", "globals", "locals"}
_IO = {"open", "print", "input"}
_KNOWN_PURE = {"len", "range", "sorted", "sum", "min", "max", "all", "any", "abs", "enumerate", "zip"}


def build_atlas(files: Mapping[str, str], *, package: str, intent: FrozenIntent,
                environment_digest: str, isolation: str = "unknown") -> CIR:
    """Static best-effort extraction. Unknown external/dynamic behavior is never silently assumed absent."""
    if not package.isidentifier() or not environment_digest:
        raise ValueError("package and environment digest are required")
    if not files:
        raise ValueError("files cannot be empty")
    source_files = dict(sorted(files.items()))
    if any(not isinstance(p, str) or not isinstance(s, str) for p, s in source_files.items()):
        raise ValueError("file paths and contents must be strings")
    # Fingerprint the whole supplied tree, including non-Python inputs.
    snapshot = Snapshot(digest(source_files), environment_digest, isolation)
    graph = build(source_files, package)
    deps = [Dependency(e.source, e.target, "imports", e.file, e.line) for e in graph.edges]
    unknowns = [f"parse:{row['file']}:{row['line']}" for row in graph.unparsed]
    components = set(graph.modules)
    state: list[StateVariable] = []
    effects: list[Effect] = []
    findings: list[ComplexityFinding] = []
    if graph.external:
        unknowns.append(f"external_imports:{graph.external}")
    for module, path in sorted(graph.modules.items()):
        try:
            tree = ast.parse(source_files[path])
        except (SyntaxError, ValueError, RecursionError):
            continue
        globals_in_module: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        globals_in_module.add(target.id)
                        state.append(StateVariable(f"{module}.{target.id}", module, True))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                op = f"{module}.{node.name}"
                components.add(op)
                declared_globals = {name for sub in ast.walk(node) if isinstance(sub, ast.Global)
                                    for name in sub.names}
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if isinstance(func, ast.Name) and func.id in _DYNAMIC:
                            unknowns.append(f"dynamic:{op}:{sub.lineno}:{func.id}")
                        elif isinstance(func, ast.Name) and func.id in _IO:
                            effects.append(Effect(op, func.id, "unknown", "static", path, sub.lineno))
                            unknowns.append(f"io:{op}:{sub.lineno}")
                        elif isinstance(func, ast.Name) and func.id in _KNOWN_PURE:
                            pass
                        elif isinstance(func, ast.Name) and func.id in {
                            n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        }:
                            deps.append(Dependency(op, f"{module}.{func.id}", "calls", path, sub.lineno))
                        else:
                            unknowns.append(f"unresolved_call:{op}:{sub.lineno}")
                    if isinstance(sub, ast.Name) and sub.id in globals_in_module:
                        if isinstance(sub.ctx, ast.Load):
                            effects.append(Effect(op, f"{module}.{sub.id}", "read", "static", path, sub.lineno))
                        elif isinstance(sub.ctx, (ast.Store, ast.Del)) and sub.id in declared_globals:
                            effects.append(Effect(op, f"{module}.{sub.id}", "write", "static", path, sub.lineno))
                    if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self":
                        if isinstance(sub.ctx, (ast.Store, ast.Del)):
                            effects.append(Effect(op, f"{module}.self.{sub.attr}", "write", "static", path, sub.lineno))
                            state.append(StateVariable(f"{module}.self.{sub.attr}", module, True))
                        elif isinstance(sub.ctx, ast.Load):
                            effects.append(Effect(op, f"{module}.self.{sub.attr}", "read", "static", path, sub.lineno))
                if declared_globals:
                    findings.append(ComplexityFinding(f"global:{op}", "shared mutable global state",
                                                       f"{path}:{node.lineno}", evidence="static global declaration"))
            if isinstance(node, (ast.Call, ast.Attribute)) and isinstance(node, ast.Attribute):
                if node.attr in {"__dict__", "__getattribute__", "__setattr__"}:
                    unknowns.append(f"reflective:{path}:{node.lineno}")
    # Effects/edges are may-facts; their presence is evidence of potential dependency, not of a defect.
    unique_state = {s.id: s for s in state}
    return CIR(snapshot, intent, tuple(sorted(components)),
               tuple(sorted(set(deps), key=lambda d: (d.source, d.target, d.kind, d.line))),
               tuple(unique_state[k] for k in sorted(unique_state)),
               tuple(sorted(set(effects), key=lambda e: (e.operation, e.resource, e.kind, e.line))),
               tuple(findings), tuple(sorted(set(unknowns))))
