# SPDX-License-Identifier: Apache-2.0
"""Every command's result the study reads goes through one reader of its status (``wecommon.completed``; ``run.checked`` is it,
returning the output): study review 2, findings 6, 7 and 11, failed commands whose output was read as a success. The inventory is
made from the code's own listing: every module of the study parsed with ``ast``, and every call that runs a command (a docker call,
a shell in a container, a subprocess), with how its result is used."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(STUDY))

import wecommon as c  # noqa: E402

#: the one reader, by the names it is called by
READERS = {"completed", "checked"}
#: what runs a command: docker's own subcommands, as the study calls them (``Docker.__call__``), and a shell in a container
DOCKER = {"exec", "run", "cp", "rm", "image", "images", "network", "build", "inspect", "kill", "stop", "logs", "ps"}
#: a module that is a program of its own, with the standard library alone (``-I``), and cannot import the reader: its sites are
#: listed, each reading the status itself, and named here with why
STANDALONE = {"box_mcp": "the host's MCP server for the agent, standard library only (its own docstring): its mirror raises on any "
                         "exit but 0, and its run hands the agent's command's output and exit back to the agent"}


def _kind(call: ast.Call) -> str | None:
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "subprocess" and name in (
            "run", "Popen", "check_output", "call", "check_call"):
        return f"subprocess.{name}"
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "os" and name in (
            "system", "popen", "spawnl", "spawnv", "execv", "execvp"):
        return f"os.{name}"
    if name == "sh" and isinstance(f, ast.Attribute):
        return "a shell in a container"
    if name == "popen" and isinstance(f, ast.Attribute):
        return "a session process"
    if name == "docker" and call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value in DOCKER:
        return f"docker {call.args[0].value}"
    return None


def sites(folder: Path = STUDY) -> list[tuple[str, int, str, str, str]]:
    """Every call that runs a command, as (module, line, function, kind, use): ``read`` (its result goes straight into the reader),
    ``discarded`` (nothing of it is read), ``process`` (a session, whose status its ``wait`` reads), ``returned`` (a wrapper of the
    docker call itself, ``sh`` or ``popen``, handing the result to its own caller), or ``unread status`` (anything else)."""
    found = []
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def function(node: ast.AST) -> ast.AST | None:
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node
            return None

        for node in ast.walk(tree):
            kind = _kind(node) if isinstance(node, ast.Call) else None
            if kind is None:
                continue
            up, fn = parents.get(node), function(node)
            reader = isinstance(up, ast.Call) and node in up.args and (
                getattr(up.func, "attr", None) in READERS or getattr(up.func, "id", None) in READERS)
            if reader:
                use = "read"
            elif isinstance(up, ast.Expr):
                use = "discarded"
            elif kind in ("a session process", "subprocess.Popen"):
                use = "process"
            elif isinstance(up, ast.Return) and fn is not None and fn.name in ("sh", "popen"):
                use = "returned"
            else:
                use = "unread status"
            found.append((path.relative_to(folder).with_suffix("").as_posix(), node.lineno,
                          fn.name if fn is not None else "<module>", kind, use))
    return sorted(found)


def test_every_command_result_the_study_reads_goes_through_the_one_reader():
    unread = [f"{m}:{n} {fn}: {kind}" for m, n, fn, kind, use in sites() if use == "unread status" and m not in STANDALONE]
    assert unread == [], "\n".join(["a command's result read past its status:", *unread])


def test_the_inventory_lists_every_kind_of_call_and_sees_each_use(tmp_path):
    """The listing's own control, on a module written for it."""
    (tmp_path / "caller.py").write_text(
        "import subprocess\nimport wecommon as c\n"
        "def a(d):\n    return c.completed(d.sh('x', 'true'), 'w').stdout\n"
        "def b(d):\n    d.docker('rm', '-f', 'x')\n"
        "def e(d):\n    got = d.sh('x', 'cat f')\n    return got.stdout\n"
        "def f():\n    return subprocess.run(['git'], capture_output=True).stdout\n"
        "def g(d):\n    return d.popen('exec', 'x')\n"
        "def h(d):\n    return checked(d.docker('image', 'inspect', 't'), 'w')\n"
        "def sh(self, x):\n    return self.raw.sh(x)\n"
        "import os\ndef k():\n    return os.system('make')\n", encoding="utf-8")
    assert [(fn, kind, use) for _, _, fn, kind, use in sites(tmp_path)] == [
        ("a", "a shell in a container", "read"), ("b", "docker rm", "discarded"), ("e", "a shell in a container", "unread status"),
        ("f", "subprocess.run", "unread status"), ("g", "a session process", "process"), ("h", "docker image", "read"),
        ("sh", "a shell in a container", "returned"), ("k", "os.system", "unread status")]


def test_the_standalone_module_reads_each_status_itself():
    """``box_mcp`` cannot import the reader; each of its command sites is listed, and its source reads the status (``returncode``)."""
    listed = [(n, fn) for m, n, fn, _, use in sites() if m in STANDALONE]
    source = (STUDY / "box_mcp.py").read_text(encoding="utf-8")
    assert listed and all(use != "read" for m, _, _, _, use in sites() if m in STANDALONE)
    assert source.count(".returncode") >= len(listed)


@pytest.mark.parametrize("code,ok,passes", [(0, (0,), True), (1, (0,), False), (137, (0,), False), (None, (0,), False),
                                            (True, (0, 1), False), (1, c.APPLY_EXITS, True), (128, c.APPLY_EXITS, True),
                                            (126, c.APPLY_EXITS, False), (255, c.EXITS, True), (-9, c.EXITS, False)])
def test_the_reader_raises_on_any_status_not_named_a_success(code, ok, passes):
    class Done:
        returncode, stdout, stderr = code, b"exit=0\n", b"killed"

    if passes:
        assert c.completed(Done(), "the step", ok=ok).returncode == code
    else:
        with pytest.raises(RuntimeError, match="the step failed"):
            c.completed(Done(), "the step", ok=ok)
