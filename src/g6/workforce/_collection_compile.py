# SPDX-License-Identifier: Apache-2.0
"""G6-owned compilation worker. Run with -I; never import repository modules."""
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import tokenize
import types


def code_identity(code, checkpoint):
    """Use exactly the collection producer's location-free content encoding."""
    def pack(value):
        checkpoint()
        if isinstance(value, types.CodeType):
            return ["code", [pack(getattr(value, field, b"")) for field in (
                "co_code", "co_consts", "co_names", "co_varnames", "co_freevars", "co_cellvars",
                "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
                "co_stacksize", "co_flags", "co_name", "co_qualname", "co_exceptiontable")]]
        if isinstance(value, (tuple, frozenset)):
            items = [pack(item) for item in value]
            if isinstance(value, frozenset):
                items.sort(key=lambda item: json.dumps(item, ensure_ascii=True))
            return [type(value).__name__, items]
        return [type(value).__name__, repr(value)]

    return hashlib.sha256(json.dumps(pack(code), ensure_ascii=True).encode("utf-8")).hexdigest()


def compile_sources(request):
    deadline = time.monotonic() + request["seconds"]

    def checkpoint():
        if time.monotonic() >= deadline:
            raise TimeoutError("collection compilation time limit reached")

    # Only the runtime's installed pytest paths, supplied before collection,
    # are added. The caller removes every repository import root.
    repository = Path(request["repository"]).resolve()
    allowed = request["paths"]
    sys.path[:] = [path for path in sys.path if path and (
        not Path(path).resolve().is_relative_to(repository) or path in allowed and
        Path(path).name in ("site-packages", "dist-packages"))]
    sys.path.extend(path for path in allowed if path not in sys.path)
    from _pytest.assertion.rewrite import rewrite_asserts

    result = {}
    for file, item in request["sources"].items():
        checkpoint()
        source = base64.b64decode(item["bytes"], validate=True)
        filename = item["filename"]
        tree = ast.parse(source, filename=filename)
        calls, pending = set(), [(tree, ())]
        while pending:
            checkpoint()
            node, owners = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owners = (*owners, (node.name, min([node.lineno, *(d.lineno for d in node.decorator_list)])))
            if isinstance(node, ast.Call):
                calls.update(owners)
            pending.extend((child, owners) for child in ast.iter_child_nodes(node))
        found = {}

        def remember(code):
            pending = [code]
            while pending:
                checkpoint()
                current = pending.pop()
                found.setdefault((current.co_name, current.co_firstlineno), set()).add(code_identity(current, checkpoint))
                pending.extend(value for value in current.co_consts if isinstance(value, types.CodeType))

        remember(compile(tree, filename, "exec", dont_inherit=True, optimize=0))
        rewrite_asserts(tree, source, filename)
        remember(compile(tree, filename, "exec", dont_inherit=True, optimize=0))
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        normalized = source.decode(encoding).replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        result[file] = {"source": hashlib.sha256(normalized).hexdigest(),
                        "codes": [[name, line, sorted(codes)] for (name, line), codes in found.items()],
                        "calls": [list(key) for key in sorted(calls)]}
    return result


if __name__ == "__main__":
    try:
        print(json.dumps({"compiled": compile_sources(json.load(sys.stdin))}))
    except (ImportError, OSError, ValueError, TypeError, SyntaxError, RecursionError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
