# SPDX-License-Identifier: Apache-2.0
"""Static string evaluation for the refusal census; never imports component code."""
from __future__ import annotations

import ast
import re
import string
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PATTERN = re.compile(r"G6_E_(?:[A-Z0-9_]|\{\})+")


def parts(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, "{}")
    if isinstance(node, ast.FormattedValue):
        return parts(node.value, constants)
    if isinstance(node, ast.JoinedStr):
        return "".join(parts(v, constants) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return parts(node.left, constants) + parts(node.right, constants)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        values = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        values = iter(parts(v, constants) for v in values)
        return re.sub(r"%[-+\d.]*[srd]", lambda m: next(values, "{}"), parts(node.left, constants))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        values = [parts(v, constants) for v in node.args]
        keywords = {v.arg: parts(v.value, constants) for v in node.keywords}
        out, index = "", 0
        try:
            for literal, field, spec, conversion in string.Formatter().parse(parts(node.func.value, constants)):
                out += literal
                if field is not None:
                    key = str(index) if not field else field
                    out += (values[int(key)] if key.isdigit() and int(key) < len(values)
                            else keywords.get(key, "{}"))
                    index += not field
            return out
        except ValueError:
            return "{}"
    return "{}"


def constants(tree, path=None, seen=frozenset()):
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if not any(alias.name.isupper() for alias in node.names):
                continue
            module = node.module or ""
            if node.level and isinstance(path, Path):
                parent = path.parent
                for _ in range(node.level - 1):
                    parent = parent.parent
                target = parent.joinpath(*module.split("."))
            else:
                target = SRC.joinpath(*module.split("."))
            target = target / "__init__.py" if target.is_dir() else target.with_suffix(".py")
            if target in seen or not target.is_file() or not target.resolve().is_relative_to(SRC.resolve()):
                continue
            imported = constants(ast.parse(target.read_text(encoding="utf-8")), target, seen | {target})
            for alias in node.names:
                if alias.name in imported:
                    out[alias.asname or alias.name] = imported[alias.name]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = parts(node.value, out)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and "{}" not in value:
                    out[target.id] = value
    return out


def expressions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.JoinedStr, ast.BinOp, ast.Call))]


def builders(source, path=None):
    tree = ast.parse(source)
    names = constants(tree, path)
    out = set()
    for node in expressions(tree):
        literals = {m.group() for child in ast.walk(node) if isinstance(child, ast.Constant)
                    and isinstance(child.value, str) for m in PATTERN.finditer(child.value)}
        for match in PATTERN.finditer(parts(node, names)):
            code = match.group()
            if not code.endswith("_") and ("{}" in code or code not in literals):
                out.add(code)
    return out
