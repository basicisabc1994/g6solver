# SPDX-License-Identifier: Apache-2.0
"""A narrow formal route for straight-line, exact integer expressions.

No supplied assertion about Python code is accepted. The expression is extracted
from the exact implementing function; unsupported semantics remain unknown. Z3,
through g6.formal, checks equality to the independent statement without premises.
The public claim is restricted to integer inputs and this expression grammar.
"""
from __future__ import annotations

import ast
import json
import re

from g6.core import sandbox

CHILD = r'''
import json
import sys
from pathlib import Path

def offline(event, args):
    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.fork"):
        raise PermissionError("formal certificate checks run offline")
sys.addaudithook(offline)
from g6.formal import check_equivalence
p = json.loads(Path("proof.json").read_text(encoding="utf-8"))
r = check_equivalence(p["actual"], p["expected"], sorts={k: "Int" for k in p["parameters"]}, timeout=5)
outcome = "unknown"
if not r.code and not r.vacuous and not r.conclusion_assumed and r.artifact is not None:
    if r.valid:
        outcome = "pass"
    elif r.counterexample is not None:
        outcome = "fail"
print("G6-FORMAL " + outcome)
'''


def expression_of(source: str, function: str, parameters: list[str]) -> str:
    """Translate only pure integer +, -, *; no imports, defaults, decorators, calls or rebinding."""
    tree = ast.parse(source)
    statements = [n for n in tree.body
                  if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                          and isinstance(n.value.value, str))]
    if len(statements) != 1 or not isinstance(statements[0], ast.FunctionDef):
        raise ValueError("formal route requires one pure implementing function")
    fn = statements[0]
    args = fn.args
    if (fn.name != function or fn.decorator_list or args.defaults or args.kw_defaults or args.kwonlyargs
            or args.posonlyargs or args.vararg or args.kwarg or [a.arg for a in args.args] != parameters
            or any(a.annotation for a in args.args) or fn.returns is not None):
        raise ValueError("formal function signature is outside the exact integer grammar")
    if not parameters or len(set(parameters)) != len(parameters) or not all(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", p) for p in parameters):
        raise ValueError("formal parameters must be distinct plain identifiers")
    body = [n for n in fn.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                     and isinstance(n.value.value, str))]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        raise ValueError("formal route requires a single return expression")

    def term(node):
        if isinstance(node, ast.Name) and node.id in parameters:
            return node.id
        if isinstance(node, ast.Constant) and type(node.value) is int and abs(node.value) < 10**100:
            return str(node.value) if node.value >= 0 else f"(- {abs(node.value)})"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            inner = term(node.operand)
            return inner if isinstance(node.op, ast.UAdd) else f"(- {inner})"
        if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult):
            op = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(node.op)]
            return f"({op} {term(node.left)} {term(node.right)})"
        raise ValueError("formal expression is outside the exact integer grammar")

    return term(body[0].value)


def check(source: str, function: str, proof: dict) -> str:
    try:
        actual = expression_of(source, function, proof["parameters"])
        payload = {"actual": actual, "expected": proof["expression"], "parameters": proof["parameters"]}
        run = sandbox.run_script(CHILD, {"proof.json": json.dumps(payload, sort_keys=True)},
                                 timeout_s=10, mem_mb=512, file_mb=8, max_output_bytes=65536)
    except (KeyError, TypeError, ValueError, SyntaxError):
        return "unknown"
    if run.status != "ok":
        return "unknown"
    rows = [s.removeprefix("G6-FORMAL ") for s in run.stdout.splitlines() if s.startswith("G6-FORMAL ")]
    return rows[0] if len(rows) == 1 and rows[0] in ("pass", "fail", "unknown") else "unknown"
