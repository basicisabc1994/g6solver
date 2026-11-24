# SPDX-License-Identifier: Apache-2.0
"""Completion filesystem calls must be read-only or use the audited store."""
import ast
from pathlib import Path

import pytest


def unguarded_calls(source):
    """Fail closed on filesystem methods, including captured and dynamic writers.

    Non-filesystem methods are admitted explicitly too: an unfamiliar method on
    a local receiver needs review rather than inheriting permission to write.
    """
    tree = ast.parse(source)
    readonly = {
        "absolute", "as_posix", "exists", "expanduser", "glob", "is_absolute", "is_dir", "is_file",
        "is_relative_to", "is_symlink", "iterdir", "joinpath", "lstat", "read_bytes", "read_text",
        "relative_to", "resolve", "rglob", "stat", "with_name", "with_suffix",
    }
    values = {
        "add", "append", "clear", "count", "decode", "encode", "endswith", "extend", "format", "fromkeys",
        "fullmatch", "get", "get_grouped_opcodes", "hexdigest", "items", "join", "keys", "lower", "lstrip",
        "match", "partition", "pop", "removeprefix", "rfind", "rsplit", "rstrip", "search", "setdefault",
        "split", "splitlines", "start", "startswith", "strip", "sub", "update", "values", "set", "reset",
    }
    # These are collaborators, never inferred from an arbitrary local receiver.
    imports = {alias.asname or alias.name.split('.')[0]: alias.name
               for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imports.update({alias.asname or alias.name: f"{node.module}.{alias.name}"
                    for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names})
    filesystem = {"os", "shutil", "tempfile", "pathlib", "json", "builtins"}
    collaborators = {"self", "run", "watch", "record", "report"}
    collaboration = {"into", "total", "mark", "refused", "told_since", "body", "full", "wire",
                     "_launch", "_reply", "check", "locate", "tree", "masked", "__init__"}
    special = {"sys.stdin.buffer.read", "super().__init__"}
    data_fields = {"modes", "old", "text", "session", "ran", "pending", "refused_provider"}
    found = set()

    def allowed(node, *, captured=False):
        call = ast.unparse(node)
        root = call.split('.', 1)[0]
        origin = imports.get(root, root)
        if call.startswith("store.") and imports.get('store') == 'g6.harness._completion_storage':
            return True
        receiver = node
        while isinstance(receiver, ast.Attribute):
            receiver = receiver.value
        if isinstance(receiver, ast.Name) and origin.split('.')[0] in filesystem:
            # All shutil/tempfile calls require the gate. json.dump is a writer.
            canonical = imports.get(receiver.id, receiver.id) + call[len(receiver.id):]
            return canonical in {"os.environ.get", "os.fspath", "json.loads", "json.dumps"}
        if isinstance(node, ast.Attribute) and node.attr in readonly | values:
            return True
        if root in imports and origin != "pathlib.Path":
            return True
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                constructor = imports.get(node.value.func.id, '')
                if constructor and not constructor.startswith(('pathlib.', 'os.', 'shutil.', 'tempfile.')):
                    return True
            if root in collaborators and node.attr in collaboration:
                return True
            if node.attr == "replace" and not captured:
                return False  # admitted below only for the two-string form
        return call in special or call in {"consent.guard(caller).complete", "_seen(rest).masked",
                                           "_seen(task).masked", "_seen(text).masked"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call = ast.unparse(node.func)
            if call == 'getattr':
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant) or node.args[1].value not in {
                    'model', 'program', 'name',
                }:
                    found.add((node.lineno, call))
            elif call == 'open':
                found.add((node.lineno, call))
            elif isinstance(node.func, ast.Call):
                found.add((node.lineno, call))
            elif isinstance(node.func, ast.Attribute):
                # str.replace takes two arguments; Path.replace takes one.
                root = call.split('.', 1)[0]
                origin = imports.get(root, root).split('.', 1)[0]
                text_replace = node.func.attr == "replace" and len(node.args) >= 2 and origin not in filesystem
                if not text_replace and not allowed(node.func):
                    found.add((node.lineno, call))
            elif isinstance(node.func, ast.Name):
                origin = imports.get(call, "")
                if origin.startswith(("os.", "shutil.", "tempfile.", "pathlib.Path.", "builtins.open", "json.dump")):
                    found.add((node.lineno, call))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr not in data_fields and not allowed(value, captured=True):
                found.add((node.lineno, ast.unparse(value)))
    return found


@pytest.mark.parametrize("body", [
    "shutil.move(a, b)", "os.truncate(p, 0)", "tempfile.mkstemp(dir=session)", "shutil.copyfileobj(a, b)",
    "json.dump(state, handle)", "os.utime(p)", "os.removedirs(p)", "p.chmod(0)",
    "getattr(p, 'write_text')('x')", "w = p.write_bytes\nw(b'')",
    "p.future_filesystem_method()", "from os import truncate as cut\ncut(p, 0)",
    "import shutil as fs\nfs.move(a, b)", "from pathlib import Path\nPath(p).chmod(0)",
    "os.replace(a, b)", "import os as fs\nfs.replace(a, b)", "Path(p).touch()", "open(p, 'w')",
    "os.environ.update({'x': 'y'})",
    "import shutil as store\nstore.move(a, b)",
])
def test_inventory_refuses_unlisted_and_indirect_writers(body):
    assert unguarded_calls(body), body


def test_completion_files_use_only_readonly_calls_or_the_gate():
    from g6.harness import completion_hook, completion_loop
    from g6.mcp import review_loop

    for module in (completion_hook, completion_loop, review_loop):
        assert not unguarded_calls(Path(module.__file__).read_text(encoding="utf-8")), module.__name__
