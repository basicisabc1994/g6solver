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

#: the one reader, by the names it is called by, with its reader of a command's own exit in a container (``own_exit``)
READERS = {"completed", "checked", "own_exit"}
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
            if reader or (kind.startswith("subprocess.") and any(
                    k.arg == "check" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords)):
                use = "read"              # a subprocess run with ``check=True`` raises on any exit but 0 itself
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


_REMOVED = "a container or network removed at the end of its step, once every result of it is read: its name is the step's own "\
           "(a fresh uuid), nothing reads it again, and one a failure leaves is labelled g6we=1 for the sweep"
_KILLED = "a session's processes killed at its stop, before its process is: a kill whose exec failed is a container that does " \
          "not answer, and the final patch's capture after it, read, then fails the run"
#: Every result nothing reads (study review 3: no result left unread), each named with why its failure cannot change any outcome.
#: A command run for its effect whose failure could (a capture, a copy of probes, a folder cleared) is read, and fails its step.
EXCUSED = {
    ("final_review", "_review", "docker rm"): _REMOVED, ("final_review", "_step", "docker rm"): _REMOVED,
    ("probe_replay", "_sealed", "docker rm"): _REMOVED, ("probe_replay", "replay_task", "docker rm"): _REMOVED,
    ("product_review", "review", "docker rm"): _REMOVED, ("replay3", "run_on", "docker rm"): _REMOVED,
    ("run", "key_check", "docker rm"): _REMOVED, ("run", "parent_check", "docker rm"): _REMOVED,
    ("run", "_run_task", "docker rm"): _REMOVED, ("run", "_run_task", "docker network"): _REMOVED,
    ("run", "_oracle_run", "docker rm"): _REMOVED, ("run", "g6replay", "docker rm"): _REMOVED,
    ("run", "modelcheck", "docker rm"): _REMOVED, ("run", "modelcheck", "docker network"): _REMOVED,
    ("run", "end", "a shell in a container"): _KILLED, ("run", "run_session", "a shell in a container"): _KILLED,
    ("run", "_run_task", "a shell in a container"): _KILLED,
    ("run", "end_codex", "subprocess.run"): "the host's stray reviewer processes ended once the review's reply is read: one a "
                                            "failure leaves writes only to a pack already removed",
}
#: Every command in a container whose exit but 0 is read as an answer without its own report (``own_exit``), named with why the
#: runtime's failure, which ends a command with an exit it can end with too, cannot be read as the command's answer there.
EXITS_READ = {
    ("product_review", "git"): "any exit but 0 raises, on the next line, whichever gave it",
    ("replay3", "sh"): "any exit but 0 raises, on the next line, whichever gave it",
    ("run", "kept"): "any exit but 0 keeps nothing and ends the capture unfinished: the round's patch is missing, never a failure",
    ("run", "_run_task"): "g6 doctor's report is a record nothing reads; g6 setup's exit but 0 ends the run 'error', whichever gave it",
    ("run", "copy_out"): "docker's 1 is read as no file only once the container's own 0 says nothing is there (``ABSENT``)",
}


def _container_reads(folder: Path = STUDY) -> list[tuple[str, str, str, bool]]:
    """Every read of a command in a container with exits named (``ok``), as (module, function, reader, whether the command was run
    with its own report)."""
    found = []
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            name = getattr(node.func, "attr", getattr(node.func, "id", "")) if isinstance(node, ast.Call) else ""
            if name not in ("completed", "own_exit") or not node.args or not isinstance(node.args[0], ast.Call):
                continue
            kind = _kind(node.args[0])
            if kind in ("a shell in a container", "docker exec", "docker cp") and (
                    name == "own_exit" or any(k.arg == "ok" for k in node.keywords)):
                fn = parents.get(node)
                while fn is not None and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = parents.get(fn)
                reported = any(k.arg == "own_exit" and isinstance(k.value, ast.Constant) and k.value.value is True
                               for k in node.args[0].keywords)
                found.append((path.stem, fn.name if fn is not None else "<module>", name, reported))
    return sorted(set(found))


def test_no_result_is_left_unread_but_those_named_with_why():
    """Study review 3, findings 4, 8 and 12: a capture and a copy of probes whose results nothing read. Every result nothing reads
    is one named in ``EXCUSED``, and each of those is still a site of the code."""
    left = sorted((m, fn, kind) for m, _, fn, kind, use in sites() if use == "discarded" and m not in STANDALONE)
    assert left == sorted(EXCUSED), "\n".join(["results nothing reads that are not named with why:",
                                               *map(str, sorted(set(left) - set(EXCUSED))),
                                               "named, but no longer sites:", *map(str, sorted(set(EXCUSED) - set(left)))])


def test_an_exit_but_0_of_a_command_in_a_container_is_its_own_only_by_its_report():
    """Study review 3, findings 1 and 7: the runtime's failure ends ``docker exec`` with an exit git's own no has too. Every site
    that reads such an exit as the command's answer runs it with its report and reads it by ``own_exit``; any other is named."""
    reads = _container_reads()
    unreported = [(m, fn) for m, fn, name, reported in reads if name == "own_exit" and not reported]
    assert unreported == [], unreported
    named = sorted({(m, fn) for m, fn, name, _ in reads if name == "completed"})
    assert named == sorted(EXITS_READ), named
    assert {m for m, _, name, _ in reads if name == "own_exit"} >= {"run", "final_review", "probe_replay"}


@pytest.mark.parametrize("code,stdout,ok,passes", [
    (0, b"", c.APPLY_EXITS, True),                                   # a 0 is the command's: the runtime's failures are not 0
    (1, b"error: patch failed\n\ng6we-own-exit=1\n", c.APPLY_EXITS, True),
    (128, b"\ng6we-own-exit=128\n", c.APPLY_EXITS, True),
    (1, b"", c.APPLY_EXITS, False),                                  # a stopped or missing container: no report, unknown
    (1, b"g6we-own-exit=1\ntrailing text\n", c.APPLY_EXITS, False),  # the report is the last line, or none
    (1, b"\ng6we-own-exit=0\n", c.APPLY_EXITS, False),               # a report of another exit than the status
    (126, b"", c.APPLY_EXITS, False), (127, b"", c.APPLY_EXITS, False), (137, b"\ng6we-own-exit=137\n", c.APPLY_EXITS, False),
    (999, b"\ng6we-own-exit=999\n", c.APPLY_EXITS, False), (None, b"", c.APPLY_EXITS, False), (True, b"", (0, 1), False),
])
def test_the_own_exit_reader_takes_an_exit_but_0_only_from_the_commands_report(code, stdout, ok, passes):
    class Done:
        returncode, stderr = code, b"Error response from daemon: container x is not running"

    Done.stdout = stdout
    if passes:
        assert c.own_exit(Done(), "the step", ok=ok).returncode == code
    else:
        with pytest.raises(RuntimeError):
            c.own_exit(Done(), "the step", ok=ok)


def test_the_report_is_the_shells_own_last_line():
    """``reporting`` wraps a script so its shell names the script's exit last and exits with it; ``told`` gives the output without it."""
    script = c.reporting("git apply x")
    assert script.startswith("git apply x\n") and c.OWN_EXIT in script and script.endswith('exit "$code"')

    class Done:
        returncode, stdout, stderr = 0, b"text\nmore\n\ng6we-own-exit=0\n", b""

    assert c.told(Done()) == b"text\nmore\n"


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
