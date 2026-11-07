# SPDX-License-Identifier: Apache-2.0
"""Consent through the client's dialog, the follow-up: review cons4's findings, each brought in as a test of this file and
failing on 66f14b9, and the inventory that removes its class at the root, built from the code:

- untrusted_input_given_authority (finding 1): a G6 child starts in the loop's folder a caller names, so what Python reads
  against that folder decided which ``g6`` ran. Every G6 child now starts on the server's own import path, fixed in the
  parent with no relative or empty entry (``improve._import_path``), with no path of Python's own (``-P``, or ``-I`` on a
  pinned interpreter), and a person's act names the G6 it runs, by folder and digest, which the act's process reads again
  of the G6 it imported before it runs anything. The inventory lists, from the code, every child the MCP, metaharness and
  self-maintenance code starts, and starts each G6 child for real with a ``g6``, a ``sitecustomize`` and a ``json`` planted
  where the folder, ``.``, an empty entry and a relative entry would reach.

No model, no network, and no runner left running: the two real children here are the act, refused before it runs
anything, and the runner, refused at once on a folder no person started. What is planted is under each test's own folder.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.mcp import _metaharness_tools, improve
from g6.metaharness import runner
from g6.metaharness.api import _envelope
from g6.metaharness.loop import envelope_sha256
from g6.self_maintenance.envelope import CYCLE_LOG, file_digest, heartbeat

_spec = importlib.util.spec_from_file_location("followup_binding_helpers", Path(__file__).with_name("test_consent_binding.py"))
binding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(binding)
Rig, Server, code, SRC = binding.Rig, binding.Server, binding.code, binding.SRC
#: The folder holding the ``g6`` this process imported, which a G6 child must import too.
HOME = Path(g6.__file__).resolve().parent.parent


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


# Finding 1, untrusted_input_given_authority: a G6 child runs the server's G6 ------------------------------------------

def _plant(folder: Path, marker: Path) -> None:
    """A ``g6`` whose every import leaves ``marker``, with ``sitecustomize`` and ``json`` beside it: anything Python takes
    from ``folder`` in place of the server's runs one of them."""
    note = f"from pathlib import Path\nPath({str(marker)!r}).write_text({str(folder)!r}, encoding='utf-8')\n"
    for rel in ("g6/__init__.py", "g6/metaharness/__init__.py", "g6/metaharness/runner.py", "g6/metaharness/loop.py",
                "sitecustomize.py", "usercustomize.py", "json.py"):
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note, encoding="utf-8")


def _odd_path(monkeypatch, root: Path, marker: Path) -> None:
    """The loop's folder and a folder under it planted, and the server's ``PYTHONPATH`` holding ``.``, an empty entry and
    that relative folder ahead of the server's own source, as a server started with a relative path has it."""
    _plant(root, marker)
    _plant(root / "planted", marker)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([".", "", "planted", str(HOME)]))


def test_an_act_child_runs_the_servers_g6_and_not_one_in_the_folder_a_caller_named(tmp_path, monkeypatch):
    """Finding 1 (review cons4), as review cons4 wrote it, in this test's own folder. Failed on 66f14b9: AssertionError:
    act child executed the caller-selected folder's g6 instead of the server's; the parent accepted G6_E_LOOP_ENROLLED."""
    root = tmp_path / "loop"
    package = root / "g6" / "metaharness"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    marker = root / "wrong-code-ran"
    fake_reply = _envelope("qualified-draft", "G6_E_LOOP_ENROLLED", "foreign G6 says enrolled")
    (package / "runner.py").write_text(
        "from pathlib import Path\n"
        "def act_main():\n"
        f"    Path({str(marker)!r}).write_text('foreign G6 ran', encoding='utf-8')\n"
        f"    print({json.dumps(fake_reply)!r})\n"
        "    return 0\n",
        encoding="utf-8",
    )
    domain = tmp_path / "domain.py"
    domain.write_text("raise AssertionError('bound domain refused to enrol')\n", encoding="utf-8")
    (root / "settings.json").write_text(json.dumps({
        "domain_file": str(domain), "read_account": False,
        "windows": [{"provider": "claude", "allowance": 1000, "week_start": "2026-11-02T00:00:00+00:00"}],
    }), encoding="utf-8")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.improve")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((".", str(HOME))))
    server = Server()
    _metaharness_tools(server, None, object)

    async def agree(**kwargs):
        assert str(domain) in kwargs["message"] and file_digest(domain) in kwargs["message"]
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=agree), request_id="followup")
    reply = asyncio.run(server.tools["g6_improve_enrol"](ctx=ctx, folder=str(root)))
    assert not marker.exists(), (
        "act child executed the caller-selected folder's g6 instead of the server's; "
        f"the parent accepted {reply['warning_card']['code']}")
    assert code(reply) == runner.CANNOT_OPEN and "bound domain refused to enrol" in reply["message"], reply
    assert not (root / CYCLE_LOG).is_file()


#: What a child is, for the inventory: one that runs G6 in the loop's folder, a sandbox run, or another program.
G6_CHILD, SANDBOX, PROGRAM = "g6", "sandbox", "program"
#: Each child G6's MCP, metaharness and self-maintenance code starts, directly or through a function or class of
#: ``g6.core`` that starts one, by module, innermost function or class, and what starts it, with what it is.
CHILDREN = {
    ("mcp/improve.py", "_spawn", "subprocess.Popen"): G6_CHILD,                      # the runner, in the loop's folder
    ("mcp/improve.py", "_run_act", "subprocess.Popen"): G6_CHILD,                    # a person's act, in the loop's folder
    ("metaharness/executor.py", "execute", "g6.core.sandbox.run_script"): SANDBOX,
    ("metaharness/lora.py", "sandbox_trainer", "g6.core.sandbox.run_script"): SANDBOX,
    ("metaharness/steps.py", "run_shown", "g6.core.sandbox.run_script"): SANDBOX,
    ("mcp/mission.py", "register", "subprocess.run"): PROGRAM,                       # git, in G6's copy
    ("mcp/mission.py", "register", "g6.core.git_env.safe_git"): PROGRAM,             # git's own checks
    ("mcp/__init__.py", "describe_reply", "g6.core.discovery.describe"): PROGRAM,    # outside programs' version probes
    ("self_maintenance/usage.py", "read_limits", "g6.core.component_process.run_process"): PROGRAM,   # codex app-server
    ("self_maintenance/usage.py", "WatchedClaude", "g6.core.host_models.ClaudeCodeCaller"): PROGRAM,  # claude
}
#: The packages whose children the inventory reads.
PACKAGES = ("mcp", "metaharness", "self_maintenance")
#: What starts a process, by the module it is called on.
_STARTS = {"subprocess": {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
           "os": {"system", "popen", "startfile", "fork", "forkpty", "posix_spawn", "posix_spawnp", "execl", "execle",
                  "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                  "spawnv", "spawnve", "spawnvp", "spawnvpe"},
           "asyncio": {"create_subprocess_exec", "create_subprocess_shell"}, "multiprocessing": {"Process", "Pool"},
           "pty": {"spawn"}}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Each name a file binds by an import, anywhere in it, to the module or the module's name it stands for."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return found


def _reference(node: ast.AST, names: dict[str, str]) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in names:
        return f"{names[node.value.id]}.{node.attr}"
    return names.get(node.id) if isinstance(node, ast.Name) else None


def _callables(node: ast.AST):
    """The names in ``node`` that can start something: a call's target and the expression it is made on
    (``(spawn or subprocess.Popen)(...)``), a callable passed to a call (``partial(confine.popen, ...)``), and a class's
    bases. An annotation, an ``except`` clause or an ``isinstance`` check names a class without starting anything."""
    parts = []
    if isinstance(node, ast.Call):
        plain = isinstance(node.func, ast.Name) and node.func.id in ("isinstance", "issubclass")
        parts = [node.func] if plain else [node.func, *node.args, *(k.value for k in node.keywords)]
    elif isinstance(node, ast.ClassDef):
        parts = list(node.bases)
    for part in parts:
        yield from (n for n in ast.walk(part) if isinstance(n, (ast.Attribute, ast.Name))
                    and not isinstance(getattr(n, "ctx", None), ast.Store))


def _starts(dotted: str | None) -> bool:
    module, _, name = (dotted or "").rpartition(".")
    return name in _STARTS.get(module, ())


def _core_starters() -> set[str]:
    """Each function and class of ``g6.core`` that starts a process, directly or through another, read from the code."""
    defs = {}
    for path in (SRC / "core").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module, names = _module_name(path), _aliases(tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[f"{module}.{node.name}"] = (node, names, module)
    starters: set[str] = set()
    grew = True
    while grew:
        grew = False
        for dotted, (node, names, module) in defs.items():
            local = {**{d.rpartition(".")[2]: d for d in defs if d.rpartition(".")[0] == module}, **names}
            if dotted not in starters and any(_starts(r) or r in starters for r in (
                    _reference(n, local) for inner in ast.walk(node) for n in _callables(inner))):
                starters.add(dotted)
                grew = True
    return starters


def _children() -> dict[tuple[str, str, str], ast.AST]:
    """Each child the three packages start, read from the code, by module, innermost function or class, and what starts
    it."""
    starters, found = _core_starters(), {}
    for package in PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names, rel = _aliases(tree), path.relative_to(SRC).as_posix()

            def visit(node: ast.AST, owner: str) -> None:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    owner = node.name
                for part in _callables(node):
                    reached = _reference(part, names)
                    if _starts(reached) or reached in starters:
                        found[(rel, owner, reached)] = node
                for child in ast.iter_child_nodes(node):
                    visit(child, owner)

            visit(tree, "<module>")
    return found


def test_every_child_the_mcp_metaharness_and_self_maintenance_code_starts_is_in_the_inventory():
    assert set(_children()) == set(CHILDREN), "a child process was added or moved: list it with what it is"


def _functions(rel: str) -> dict[str, ast.AST]:
    return {n.name: n for n in ast.walk(ast.parse((SRC / rel).read_text(encoding="utf-8")))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def test_each_g6_child_starts_on_the_interpreter_and_the_import_path_the_server_fixes():
    """A G6 child's command begins with ``_interpreter`` (``-P``, or ``-I`` on a pinned one) and it starts on
    ``_environment``, read from the code; a sandbox run starts isolated in a folder of G6's own; another program is not
    G6's Python."""
    for (rel, owner, _), kind in CHILDREN.items():
        body = ast.unparse(_functions(rel)[owner])
        if kind == G6_CHILD:
            [start] = [n for n in ast.walk(_functions(rel)[owner]) if isinstance(n, ast.Call)
                       and ast.unparse(n.func) == "subprocess.Popen"]
            command = start.args[0]
            command = next(n.value for n in ast.walk(_functions(rel)[owner]) if isinstance(n, ast.Assign)
                           and ast.unparse(n.targets[0]) == ast.unparse(command)) if isinstance(command, ast.Name) else command
            assert ast.unparse(command).startswith("[*_interpreter(settings)"), (rel, owner)
            assert {k.arg: ast.unparse(k.value) for k in start.keywords}.get("env") == "_environment()", (rel, owner)
        elif kind == PROGRAM:
            assert "sys.executable" not in body and "_interpreter" not in body, (rel, owner)
    script = ast.unparse(_functions("core/sandbox.py")["run_script"])
    assert 'argv = [python, "-I", "-c", _BOOT' in script.replace("'", '"') and "confine.run_folder(" in script


@pytest.mark.parametrize("settings", [{}, {"python": sys.executable}])
def test_a_g6_childs_import_path_is_absolute_and_fixed_whatever_the_servers_pythonpath_holds(tmp_path, monkeypatch,
                                                                                               settings):
    root = tmp_path / "loop"
    root.mkdir()
    _odd_path(monkeypatch, root, tmp_path / "planted-ran")
    seen = []

    class Child:
        returncode = 1

        def communicate(self, request, timeout):
            return "", ""

        def poll(self):
            return 1

    def popen(argv, **kwargs):
        seen.append((argv, kwargs))
        return Child()

    monkeypatch.setattr(improve.subprocess, "Popen", popen)
    improve._run_act(root, settings, {"act": "enrol"})
    improve._spawn(root, settings)
    assert len(seen) == 2
    for argv, kwargs in seen:
        entries = kwargs["env"]["PYTHONPATH"].split(os.pathsep)
        assert entries and all(entry and os.path.isabs(entry) for entry in entries), entries
        assert not any(Path(entry).resolve().is_relative_to(root.resolve()) for entry in entries), entries
        first = next(Path(entry) for entry in entries if (Path(entry) / "g6" / "__init__.py").is_file())
        assert first.resolve() == HOME
        assert argv[1] == ("-I" if settings else "-P") and kwargs["cwd"] == root


def test_nothing_planted_runs_in_a_real_act_child_and_the_servers_g6_answers(tmp_path, monkeypatch):
    """The act's real process, in a folder with a ``g6``, a ``sitecustomize`` and a ``json`` planted where the folder, ``.``,
    an empty entry and a relative entry reach. It is refused before it runs anything: its binding is empty."""
    root, marker = tmp_path / "loop", tmp_path / "planted-ran"
    root.mkdir()
    (root / "settings.json").write_text("{}", encoding="utf-8")
    _odd_path(monkeypatch, root, marker)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    reply = improve._run_act(root, {}, {"act": "enrol", "folder": str(root), "who": "Ada", "binding": {}})
    assert not marker.exists(), f"the act's process ran what was planted in {marker.read_text(encoding='utf-8')}"
    assert code(reply) == improve.TERMS_CHANGED, reply                  # the server's G6 answered, and refused
    assert not (root / CYCLE_LOG).is_file()


def test_nothing_planted_runs_in_a_real_runner_and_the_servers_g6_answers(tmp_path, monkeypatch):
    """The runner's real process, in a folder planted as above. No person started the folder, so it refuses before it
    opens anything and ends (``G6_E_LOOP_NOT_STARTED``)."""
    root, marker = tmp_path / "loop", tmp_path / "planted-ran"
    root.mkdir()
    (root / "settings.json").write_text("{}", encoding="utf-8")
    _odd_path(monkeypatch, root, marker)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    process = improve._spawn(root, {})
    try:
        assert process.wait(timeout=120) == 1
    finally:
        if process.poll() is None:              # this test started it, so it ends it
            process.kill()
            process.wait(timeout=30)
    assert not marker.exists(), f"the runner ran what was planted in {marker.read_text(encoding='utf-8')}"
    assert (heartbeat(root) or {}).get("code") == runner.NOT_STARTED, (root / improve.RUNNER_LOG).read_text(
        encoding="utf-8", errors="replace")


def test_an_acts_binding_names_the_g6_it_runs_by_folder_and_digest_and_its_dialog_shows_them(rig, tmp_path):
    root, question, bound = improve.asked_act("enrol", str(rig.root))
    assert bound[runner.G6_FOLDER] == str(HOME / "g6") and bound[runner.G6_SHA] == envelope_sha256()
    assert f"{runner.G6_FOLDER}: {HOME / 'g6'}" in question and f"{runner.G6_SHA}: {envelope_sha256()}" in question
    pinned = tmp_path / "python.exe"
    pinned.write_bytes(b"")
    path = rig.root / "settings.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "python": str(pinned)}), encoding="utf-8")
    _, question, bound = improve.asked_act("enrol", str(rig.root))
    assert runner.G6_SHA not in bound and "installed for the Python it runs on" in bound[runner.G6_FOLDER]


@pytest.mark.parametrize("field, value", [("G6 code SHA-256", "0" * 64), ("G6 it runs", "a folder the dialog named")])
def test_a_real_act_child_refuses_a_g6_its_dialog_did_not_name(rig, monkeypatch, field, value):
    """The check is the act's process's own: given a binding that names other G6 code, or another folder, it refuses
    before it runs anything, and records nothing."""
    monkeypatch.undo()                                                    # the real _run_act, not the rig's
    monkeypatch.setenv("G6_HOME", str(rig.tmp / "home"))
    marker = rig.tmp / "domain-ran"
    rig.domain.write_text(rig.source + f"\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    root, _, bound = improve.asked_act("enrol", str(rig.root))
    assert runner.G6_SHA in bound
    request = {"act": "enrol", "folder": str(root), "who": "Ada", "binding": {**bound, field: value}}
    reply = improve._run_act(root, runner.read_settings(root), request)
    assert code(reply) == improve.TERMS_CHANGED, reply
    assert not marker.exists() and not (rig.root / CYCLE_LOG).is_file()
    good = improve._run_act(root, runner.read_settings(root), {**request, "binding": bound})
    assert code(good) == "G6_E_LOOP_ENROLLED", good                      # the same request, as named, enrols
