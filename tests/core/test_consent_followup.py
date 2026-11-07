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
- a changed agreement named as one (finding 3; nearest one_fact_two_rules): once a person has answered, the act compares
  what they agreed to first (``runner.moved``, ``improve._still_read``), before any other check of the folder, so a
  change to any part they read is ``G6_E_LOOP_TERMS_CHANGED`` and nothing is recorded. The inventory reads every act's
  binding from the code and moves each part of it while the act's dialog is open.
- failure_read_as_success (finding 2): a reply after a person's act, a start or a stop is decided by reading the folder
  back (``runner.on_record``, ``runner.as_recorded``, ``improve._start_as_recorded``, ``improve._stop_on_record``), never
  by which step raised, and names each part in ``recorded``; the status says a pause by what holds the ticks
  (``runner.held``). The inventory lists every act on a loop's folder from the code, fails each durable write in turn,
  refused or after it is on disk, ends the act's process between two writes, and checks each reply against the folder
  reopened.

No model, no network, and no runner left running: the two real children here are the act, refused before it runs
anything, and the runner, refused at once on a folder no person started. What is planted is under each test's own folder.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.core.ledger import ChainLedger, LedgerError
from g6.mcp import _metaharness_tools, improve
from g6.metaharness import api, runner
from g6.metaharness.api import _envelope
from g6.metaharness.loop import envelope_sha256
from g6.self_maintenance import envelope as envelope_module
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, file_digest, heartbeat, stop_standing, stops_asked

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


# Finding 3, a changed agreement named as one (nearest: one_fact_two_rules): what was agreed is compared first ---------

@pytest.mark.parametrize("act", ["resume", "demote"])
def test_a_domain_edited_while_the_dialog_is_open_is_a_changed_agreement(tmp_path, monkeypatch, act):
    """Finding 3 (review cons4), as review cons4 wrote it. Failed on 66f14b9: AssertionError: resume: the bound domain
    changed during the dialog, but got G6_E_LOOP_FROZEN_CHANGED instead of G6_E_LOOP_TERMS_CHANGED (and the same for
    demote)."""
    rig = Rig(tmp_path, monkeypatch)
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    binding._paused(rig) if act == "resume" else binding._adopted(rig)
    before = rig.log()._chain.entries()

    def edit_while_dialog_open(message):
        assert file_digest(rig.domain) in message
        rig.domain.write_text(rig.source + "\n# edited while consent was open\n", encoding="utf-8")
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    reply = rig.call("g6_improve_" + act, handler=edit_while_dialog_open, **({"generation": 1} if act == "demote" else {}))
    assert rig.log()._chain.entries() == before
    assert code(reply) == improve.TERMS_CHANGED, (
        f"{act}: the bound domain changed during the dialog, but got {code(reply)} instead of G6_E_LOOP_TERMS_CHANGED")


def _settings(rig, **changed) -> None:
    path = rig.root / "settings.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **changed}), encoding="utf-8")


def _moved_file(rig) -> None:
    """The settings name another Python file with the same bytes: only its path moves."""
    other = rig.tmp / "elsewhere" / "domain.py"
    other.parent.mkdir(exist_ok=True)
    other.write_bytes(rig.domain.read_bytes())
    _settings(rig, domain_file=str(other))


def _edited(rig) -> None:
    rig.domain.write_text(rig.source + "\n# edited while the dialog was open\n", encoding="utf-8")


def _pinned(rig) -> None:
    pinned = rig.tmp / "python.exe"
    pinned.write_bytes(b"")
    _settings(rig, python=str(pinned))


def _other_g6_code(rig) -> None:
    rig.patch.setattr(runner, "envelope_sha256", lambda: "f" * 64)


def _other_g6_folder(rig) -> None:
    rig.patch.setattr(runner, "g6", SimpleNamespace(__file__=str(rig.tmp / "another" / "g6" / "__init__.py")))


def _another_enrolment(rig) -> None:
    """Another person's enrolment, of another domain, recorded while the dialog is open."""
    log = rig.log()
    log.enrol("Cy", log.enrolled()["frozen"], domain="another domain")


def _another_pause(rig) -> None:
    rig.log().pause("another reason, written while the dialog was open")


def _another_cycle(rig) -> None:
    rig.log().cycle({"outcome": "blocked", "code": "G6_E_LOOP_TEST_CYCLE", "reason": "a cycle during the dialog"})


def _another_demotion(rig) -> None:
    """Another person takes generation 1 back while the dialog is open: the head and all it restores move."""
    loop, _ = runner.open_loop(rig.root)
    assert loop.demote(1, "Cy")["warning_card"]["code"] == "G6_E_LOOP_DEMOTED"


def _another_stop(rig) -> None:
    improve.stop(str(rig.root), "another stop, while the dialog was open")


def _stop_lifted_by_hand(rig) -> None:
    (rig.root / "STOP").unlink()


def _another_start(rig) -> None:
    """A start recorded while the dialog is open, which counted a stop."""
    rig.log().start("Cy", stops=1, settings="", enrolment="")


def _other_use(rig) -> None:
    _settings(rig, windows=[{"provider": "claude", "allowance": 2_000_000, "week_start": binding.START.isoformat()}])


def _other_settings(rig) -> None:
    _settings(rig, note="edited while the dialog was open")


#: Each part an act binds, and what moves it while the act's dialog is open. Every part of every binding is here: the
#: inventory below reads the bindings from the code and fails on one that is not.
CHANGERS = {
    "Python file": _moved_file, "Python file SHA-256": _edited, "Runs on": _pinned, runner.G6_SHA: _other_g6_code,
    runner.G6_FOLDER: _other_g6_folder, runner.SETTINGS_DIGEST: _other_settings, "It replaces the enrolment by":
    _another_enrolment, "Enrolment it replaces": _another_enrolment, "Domain": _another_enrolment, "Enrolled by":
    _another_enrolment, "Enrolment on record": _another_enrolment, "Paused because": _another_pause, "Cycles on record":
    _another_cycle, "Its change": _another_demotion, "Measured": _another_demotion,
    **{label: _another_demotion for label in runner.RESTORING.values()}, "Chain of generations": _another_demotion,
    "Use": _other_use, improve.STOPS_COUNTED: _another_stop, "Stops the last start counted": _another_start,
    "Stop now in place": _stop_lifted_by_hand, **{label: _another_stop for label in improve.LIFTS.values()},
}
#: The parts no dialog can see move: what the tool was called with.
FIXED = {"Folder": "the folder the tool was given, resolved once: another folder is another call",
         "Generation": "the generation the tool was given: another is another call"}
#: Each act that a person agrees to from a binding, and what its tool is called with.
BOUND_ACTS = {"enrol": ("g6_improve_enrol", {}), "resume": ("g6_improve_resume", {}),
              "demote": ("g6_improve_demote", {"generation": 1}), "start": ("g6_improve_start", {})}


def _ready(rig, act: str) -> None:
    """The folder as each act's dialog needs it, with every part its binding can hold: an enrolment for a new one to
    replace, a pause, an adoption to take back, and a stop for a start to lift."""
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    if act in ("resume", "start"):
        binding._paused(rig)
    if act == "demote":
        binding._adopted(rig)
    if act == "start":
        improve.stop(str(rig.root), "the owner's review")


def _binding(rig, act: str) -> dict:
    found = improve.asked(str(rig.root)) if act == "start" else improve.asked_act(act, str(rig.root),
                                                                                  BOUND_ACTS[act][1].get("generation"))
    assert not isinstance(found, dict), found
    return found[2]


#: Each part each act's binding holds once its folder is ready (``_ready``), but for the ``FIXED`` ones: what the tests
#: below move. The inventory reads each binding from the code and fails on a part not here.
HOLDS = {"enrol": ("Python file", "Python file SHA-256", "Runs on", runner.G6_SHA, runner.G6_FOLDER, runner.SETTINGS_DIGEST,
                   "It replaces the enrolment by", "Enrolment it replaces"),
         "resume": ("Python file", "Python file SHA-256", "Runs on", runner.G6_SHA, runner.G6_FOLDER, runner.SETTINGS_DIGEST,
                    "Domain", "Enrolled by", "Enrolment on record", "Paused because", "Cycles on record"),
         "demote": ("Python file", "Python file SHA-256", "Runs on", runner.G6_SHA, runner.G6_FOLDER, runner.SETTINGS_DIGEST,
                    "Domain", "Enrolled by", "Enrolment on record", "Its change", "Measured", *runner.RESTORING.values(),
                    "Chain of generations"),
         "start": ("Domain", "Python file", "Python file SHA-256", "Enrolled by", improve.ENROLMENT, "Use", "Runs on",
                   improve.SETTINGS_DIGEST, improve.STOPS_COUNTED, "Stops the last start counted", "Stop now in place",
                   *improve.LIFTS.values(), "Paused because")}


def test_every_act_a_person_agrees_to_from_a_binding_is_listed():
    """The acts whose dialog is built from a binding: the tools that take it from ``improve.asked`` or
    ``improve.asked_act``, and the acts ``person_act`` is called for, read from the code."""
    tools = binding._functions(SRC / "mcp" / "__init__.py")
    asks = {name for name, node in tools.items() if any(ast.unparse(call.func) in ("improve.asked", "improve.asked_act")
                                                        for call in ast.walk(node) if isinstance(call, ast.Call))}
    acts = {call.args[1].value for node in tools.values() for call in binding._calls(node, "person_act")
            if isinstance(call.args[1], ast.Constant)}
    assert asks == {"g6_improve_start", "person_act", "_metaharness_tools"} and acts == {"enrol", "resume", "demote"}
    assert set(BOUND_ACTS) == set(HOLDS) == acts | {"start"} == set(improve.DOES) | {"start"}
    assert {part for parts in HOLDS.values() for part in parts} <= set(CHANGERS)


@pytest.mark.parametrize("act", sorted(BOUND_ACTS))
def test_every_part_each_act_binds_is_moved_by_a_test(tmp_path, monkeypatch, act):
    rig = Rig(tmp_path, monkeypatch)
    _ready(rig, act)
    assert set(_binding(rig, act)) - set(FIXED) == set(HOLDS[act]), f"{act}'s binding is not the parts the tests move"


@pytest.mark.parametrize("act, part", [(act, part) for act, parts in HOLDS.items() for part in parts])
def test_a_bound_part_moved_while_the_dialog_is_open_is_a_changed_agreement_and_records_nothing(tmp_path, monkeypatch,
                                                                                                  act, part):
    """Whatever else the move changes, and whatever the folder's own checks would say of it, the act compares what the
    person agreed to first: ``G6_E_LOOP_TERMS_CHANGED``, its process never starts, and nothing is recorded."""
    rig = Rig(tmp_path, monkeypatch)
    _ready(rig, act)
    tool, arguments = BOUND_ACTS[act]
    shown, seen = _binding(rig, act), {}
    monkeypatch.setattr(improve, "_run_act", lambda *a: pytest.fail("an act's process started on a moved agreement"))
    monkeypatch.setattr(improve, "_spawn", lambda *a: pytest.fail("a runner started on a moved agreement"))

    def agree_after_move(message):
        CHANGERS[part](rig)
        seen["now"] = _binding_now(rig, act)
        seen["log"] = rig.log()._chain.entries()
        seen["chain"] = runner.lineage(rig.root / "workspace")
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    rig.patch = monkeypatch
    reply = rig.call(tool, handler=agree_after_move, **arguments)
    assert seen["now"].get(part, "absent") != shown.get(part, "absent"), f"moving {part!r} left it as it was"
    assert code(reply) == improve.TERMS_CHANGED, f"{act}: {part!r} moved while the dialog was open, but got {code(reply)}"
    assert rig.log()._chain.entries() == seen["log"] and runner.lineage(rig.root / "workspace") == seen["chain"]
    if act == "start":
        assert (rig.log().started() or {}).get("by") != "Ada"


def _binding_now(rig, act: str) -> dict:
    """The act's binding as its folder stands now, read by the same rule the act reads it by, refusing nothing."""
    if act == "start":
        return improve._terms(rig.root)
    return runner.person_terms(act, rig.root, generation=BOUND_ACTS[act][1].get("generation"))


# Finding 2, failure_read_as_success: a reply says what the folder holds after the act ---------------------------------

def test_a_demotion_committed_before_its_note_failed_is_not_said_to_count_for_nothing(tmp_path, monkeypatch):
    """Finding 2 (review cons4), as review cons4 wrote it. Failed on 66f14b9: AssertionError: MCP says 'Nothing counts.'
    although Ada's demotion is durable as generation 2 and the loop now restores generation 0."""
    rig = Rig(tmp_path, monkeypatch)
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    binding._adopted(rig)
    assert runner.lineage(rig.root / "workspace")["current"] == 1

    def log_cannot_write(self, *args, **kwargs):
        raise OSError("constraint log disk unavailable")

    monkeypatch.setattr(CycleLog, "demote", log_cannot_write)
    reply = rig.call("g6_improve_demote", generation=1)
    after = runner.lineage(rig.root / "workspace")
    committed = after["current"] == 2
    if committed:
        recorded = after["recent"][-1]
        assert recorded["kind"] == "rollback" and recorded["asked_by"] == "Ada" and recorded["restores"] == 0
        reopened, _ = runner.open_loop(rig.root)
        assert reopened.mh._head()[0] == 2
    assert not (committed and "Nothing counts." in reply["next_action"]), (
        "MCP says 'Nothing counts.' although Ada's demotion is durable as generation 2 and the loop now restores "
        "generation 0")
    assert committed and code(reply) == "G6_E_LOOP_DEMOTED" and reply["recorded"] == {
        "the demotion": True, "its note in the constraint log": False}, reply


class _Killed(BaseException):
    """The act's process ending between two writes: nothing in it catches this."""


class _Writes:
    """Every durable write an act makes, in order, through what makes them: a row of a hash-chained ledger
    (``ChainLedger.append``), a record under G6's state folder (``api._write_state``), and what a stop adds to ``stops.log``
    and ``STOP`` (``envelope.open``, writing). Heartbeats, locks and the meter's store are left out: no reply is decided
    by them. ``fail``: the index of the one write to fail; ``how``: ``refused``, written nowhere, ``written``, on disk and
    then raising (a ``fsync`` that fails after the line is out), ``killed``, on disk and then the process ends, or
    ``dies``, the process ends before it is written. ``onward``: every write from ``fail`` on fails the same way."""

    def __init__(self, monkeypatch, fail: int | None = None, how: str = "refused", error: type = OSError,
                 onward: bool = False) -> None:
        self.seen: list[str] = []
        self.fail, self.how, self.error, self.onward = fail, how, error, onward
        real_append, real_state, real_open = ChainLedger.append, api._write_state, open

        def append(ledger, kind, ident, body):
            return self._write(f"{Path(ledger.path).name} {kind}", lambda: real_append(ledger, kind, ident, body))

        def state(target, data):
            return self._write(f"state {Path(target).parent.name}", lambda: real_state(target, data))

        def opened(file, mode="r", *args, **kwargs):
            if not set(mode) & set("aw+x"):
                return real_open(file, mode, *args, **kwargs)
            label, index = f"file {Path(file).name}", len(self.seen)
            self.seen.append(label)
            if self._fails(index) and self.how in ("refused", "dies"):
                raise _Killed(label) if self.how == "dies" else OSError(f"no room for {label}")
            handle = real_open(file, mode, *args, **kwargs)
            return _RaisesOnClose(handle, self._raised(label)) if self._fails(index) else handle

        monkeypatch.setattr(ChainLedger, "append", append)
        monkeypatch.setattr(api, "_write_state", state)
        monkeypatch.setattr(envelope_module, "open", opened, raising=False)

    def _fails(self, index: int) -> bool:
        return self.fail is not None and (index == self.fail or self.onward and index > self.fail)

    def _raised(self, label: str) -> BaseException:
        return _Killed(label) if self.how == "killed" else self.error(f"{label} could not be finished")

    def _write(self, label: str, write):
        index = len(self.seen)
        self.seen.append(label)
        if not self._fails(index):
            return write()
        if self.how == "dies":
            raise _Killed(label)
        if self.how == "refused":
            raise self.error(f"no room for {label}")
        write()
        raise self._raised(label)


class _RaisesOnClose:
    def __init__(self, handle, error: BaseException) -> None:
        self.handle, self.error = handle, error

    def __enter__(self):
        return self.handle.__enter__()

    def __exit__(self, *exc):
        self.handle.__exit__(*exc)
        raise self.error


#: Each act on a loop's folder that writes, read from the code below, with the parts its reply names (``recorded``),
#: its own effect first, and what its reply says when that is on record.
WRITING_ACTS = {
    "enrol": (("the enrolment",), "G6_E_LOOP_ENROLLED"),
    "resume": (("the lifted pause",), "G6_E_LOOP_RESUMED"),
    "demote": (("the demotion", "its note in the constraint log"), "G6_E_LOOP_DEMOTED"),
    "start": (("the start",), improve.STARTED),
    "stop": (("the stop", "its line in the STOP file", "its count in stops.log", "its note in the constraint log"),
             improve.STOP_ASKED),
    "pause": (("the pause",), improve.PAUSED),
}
#: What a person reads as the loop's own pause: no dialog leads to it, and the status is the reply that says it.
READS_ONLY = {"status"}
#: What a reply may never say while any part of its act is on record.
NOTHING = ("Nothing counts", "nothing was recorded", "Nothing was recorded", "Nothing started", "nothing was started",
           "nothing it did is claimed", "none of it is on record")


def test_every_act_on_a_loops_folder_is_in_the_inventory():
    """The loop's tools, read from the code (``g6_improve_*``), and the loop's own pause (each caller of
    ``CycleLog.pause``): every one but the status writes, and the status writes nothing."""
    tools = {name.removeprefix("g6_improve_") for name in binding._functions(SRC / "mcp" / "__init__.py")
             if name.startswith("g6_improve_")}
    pausing = {name for rel in ("metaharness/loop.py", "metaharness/runner.py", "mcp/improve.py")
               for name, node in binding._functions(SRC / rel).items()
               if any(ast.unparse(call.func).endswith("log.pause") for call in ast.walk(node) if isinstance(call, ast.Call))}
    assert pausing == {"_pause_if_failing"}
    assert tools | {"pause"} == set(WRITING_ACTS) | READS_ONLY


def _rig(folder: Path, patch) -> Rig:
    """A rig of its own, in a folder of this test's own."""
    folder.mkdir(parents=True)
    rig = Rig(folder, patch)
    rig.patch = patch
    return rig


def _readied(rig, act: str) -> dict:
    """The folder each act needs, and what it is about: a first enrolment, a pause, an adoption, an enrolment to
    start, a start to stop, and two failed cycles before the one that pauses."""
    if act != "enrol":
        assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    if act == "resume":
        binding._paused(rig)
    if act == "demote":
        binding._adopted(rig)
    if act == "stop":
        rig.patch.setattr(improve, "_spawn", lambda root, settings: SimpleNamespace(poll=lambda: None))
        rig.patch.setattr(improve, "_watch", lambda *a: _envelope("qualified-draft", improve.STARTED, "a fake runner"))
        assert code(rig.call("g6_improve_start")) == improve.STARTED
    if act == "pause":
        log = rig.log()
        for tick in (1, 2):
            log.cycle({"outcome": "error", "code": "G6_E_LOOP_TEST_FAILED", "reason": f"failure {tick}"})
    log = rig.log()
    return {"stops": stops_asked(rig.root), "started": log.started() if log.start_in_force() else None,
            "notes": _stop_notes(log)}


def _stop_notes(log) -> int:
    """How many stop notes the constraint log holds, read from its rows."""
    return sum(1 for entry in log._chain.entries() if entry["kind"] == "stopped") if log is not None else 0


def _act(rig, act: str, monkeypatch) -> dict:
    """The act, through what a person or the loop uses: its MCP tool, or for the pause the loop's own tick that records
    the third failed cycle, and then the status, the reply that says the pause."""
    if act == "start":
        monkeypatch.setattr(improve, "_spawn", lambda root, settings: SimpleNamespace(poll=lambda: None))
        monkeypatch.setattr(improve, "_watch", lambda *a: _envelope("qualified-draft", improve.STARTED, "a fake runner",
                                                                    running=True))
        return rig.call("g6_improve_start")
    if act == "stop":
        return improve.stop(str(rig.root), "the owner's review")
    if act == "pause":
        loop, _ = runner.open_loop(rig.root)
        loop._record({"at": binding.START.isoformat(), "domain": loop.domain.name, "outcome": "error",
                      "code": "G6_E_LOOP_TEST_FAILED", "reason": "failure 3"})
        return improve.status(str(rig.root))
    return rig.call(f"g6_improve_{act}", **({"generation": 1} if act == "demote" else {}))


def _on_disk(rig, act: str, ready: dict) -> dict[str, bool]:
    """What the folder holds of the act, read afresh from its files, by this test's own reading of each part."""
    log = CycleLog(rig.root / CYCLE_LOG) if (rig.root / CYCLE_LOG).is_file() else None
    if act == "enrol":
        row = (log.enrolled() if log is not None else None) or {}
        return {"the enrolment": row.get("by") == "Ada" and row["frozen"]["domain_file"] == file_digest(rig.domain)}
    if act == "resume":
        return {"the lifted pause": not runner.held(log)}
    if act == "demote":
        chain = runner.lineage(rig.root / "workspace")
        head = chain["recent"][-1]
        return {"the demotion": chain["current"] == 2 and head["kind"] == "rollback" and head["asked_by"] == "Ada",
                "its note in the constraint log": any(e["kind"] == "demoted" for e in log._chain.entries())}
    if act == "start":
        started = log.started() or {}
        return {"the start": log.start_in_force() and started.get("by") == "Ada" and not stop_standing(rig.root, started)}
    if act == "stop":
        stop_file = rig.root / "STOP"
        line = stop_file.exists() and "the owner's review" in stop_file.read_text(encoding="utf-8")
        before, noted = ready["started"], _stop_notes(log) > ready["notes"]       # its own row, read, not a rule
        ended = before is not None and (noted or stops_asked(rig.root) != before["stops"])
        return {"the stop": line or ended, "its line in the STOP file": line,
                "its count in stops.log": stops_asked(rig.root) > ready["stops"], "its note in the constraint log": noted}
    return {"the pause": bool(runner.held(log))}


def _writes(tmp_path, monkeypatch, act: str) -> list[tuple[int, str]]:
    """Each durable write the act makes, in order, from a run with none failing, read from what the code does, with
    where it falls among all the run's writes: for the pause, those of the constraint log, the tick's other rows left
    to their own tests."""
    with monkeypatch.context() as patch:
        rig = _rig(tmp_path / "count", patch)
        rig.patch = patch
        ready = _readied(rig, act)
        writes = _Writes(patch)
        reply = _act(rig, act, patch)
    assert act == "pause" or code(reply) == WRITING_ACTS[act][1], reply
    assert all(_on_disk(rig, act, ready).values()), "with nothing failing, every part is on record"
    found = [(i, w) for i, w in enumerate(writes.seen) if act != "pause" or w.startswith("constraint_log.jsonl")]
    assert found, f"{act} wrote nothing"
    return found


def _check(reply: dict, held: dict[str, bool], act: str) -> None:
    """The reply as the reopened folder holds the act: its parts as they are on disk, the act's own code only when its
    effect is there, and no word that nothing counts while any part of it is."""
    parts, done = WRITING_ACTS[act]
    if act == "pause":
        assert (reply["pause"] is not None) == held["the pause"], f"the status says pause {reply['pause']}, held {held}"
        return
    said = f"{reply['message']} {reply.get('next_action', '')}"
    if code(reply) == done:
        assert held[parts[0]], f"{act} replied {done} with {parts[0]} not on record"
    elif act != "start":                      # a start on record whose runner did not start is START_FAILED, saying so
        assert not held[parts[0]], f"{act}'s effect is on record, but it replied {code(reply)}: {reply['message']}"
    if any(held.values()):
        assert not any(word in said for word in NOTHING), f"{act} says nothing counts with {held} on record: {said}"
    if act == "start" and held["the start"] and code(reply) != done:
        assert "The start is on record" in reply["message"], f"start on record unsaid: {reply['message']}"
    assert reply.get("recorded") == held, f"{act} names {reply.get('recorded')}, the folder holds {held}"


@pytest.mark.parametrize("how, error", [("refused", OSError), ("written", OSError), ("refused", LedgerError),
                                        ("written", LedgerError)])
@pytest.mark.parametrize("act", sorted(WRITING_ACTS))
def test_each_durable_write_of_each_act_failed_in_turn_leaves_a_reply_that_says_what_the_folder_holds(
        tmp_path, monkeypatch, act, how, error):
    """Each write the act makes is failed, one at a time, refused or after its line is on disk; then the folder is
    reopened, and the reply must say what it holds. Every write is tried, and each that fails the rule is named."""
    wrong = []
    for position, label in _writes(tmp_path, monkeypatch, act):
        if error is LedgerError and label.startswith("file "):
            continue                          # a file that cannot be written raises OSError, never a ledger's refusal
        with monkeypatch.context() as patch:
            rig = _rig(tmp_path / f"{position}", patch)
            rig.patch = patch
            ready = _readied(rig, act)
            _Writes(patch, fail=position, how=how, error=error)
            reply = _act(rig, act, patch)
        try:
            _check(reply, _on_disk(rig, act, ready), act)
        except AssertionError as exc:
            wrong.append(f"{label}: {str(exc).splitlines()[0]}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("act", ["enrol", "resume", "demote"])
def test_an_acts_process_that_ends_between_two_writes_leaves_a_reply_read_back_from_its_folder(tmp_path, monkeypatch,
                                                                                                 act):
    """The act's process ends after each write in turn, before it can reply: the server reads the folder back, and says
    what is on record. Every write is tried, and each that fails the rule is named."""
    wrong = []
    for index, label in _writes(tmp_path, monkeypatch, act):
        with monkeypatch.context() as patch:
            rig = _rig(tmp_path / f"{index}", patch)
            rig.patch = patch
            ready = _readied(rig, act)
            writes = _Writes(patch, how="killed")

            class Child:
                returncode = 1

                def communicate(self, request, timeout):
                    try:
                        writes.fail = len(writes.seen) + index
                        runner.act(json.loads(request))
                    except _Killed:
                        return "", "the act's process ended"
                    return "", "the act's process ended"

            patch.setattr(improve, "_run_act", real_run_act)
            patch.setattr(improve, "_interpreter", lambda settings: ["fake-python"])
            patch.setattr(improve.subprocess, "Popen", lambda *a, **k: Child())
            reply = _act(rig, act, patch)
        held = _on_disk(rig, act, ready)
        if code(reply) != improve.ACT_FAILED:
            wrong.append(f"{label}: replied {code(reply)}")
        elif any(held.values()) and any(word in reply["message"] for word in NOTHING):
            wrong.append(f"{label}: says nothing counts with {held} on record: {reply['message']}")
        elif reply.get("recorded") != held:
            wrong.append(f"{label}: names {reply.get('recorded')}, the folder holds {held}")
    assert not wrong, "\n".join(wrong)


def test_records_that_do_not_read_back_after_a_failed_write_claim_nothing_either_way(tmp_path, monkeypatch):
    """The demotion's note fails and the log is then damaged: what of the act counts cannot be read, so the reply says
    that, and neither that it counts nor that nothing does."""
    rig = Rig(tmp_path, monkeypatch)
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    binding._adopted(rig)

    def note_then_damage(self, *args, **kwargs):
        path = rig.root / CYCLE_LOG
        path.write_text(path.read_text(encoding="utf-8").replace('"Ada"', '"Cy"'), encoding="utf-8")
        raise OSError("constraint log disk unavailable")

    monkeypatch.setattr(CycleLog, "demote", note_then_damage)
    reply = rig.call("g6_improve_demote", generation=1)
    assert reply["recorded"] is None and code(reply) != "G6_E_LOOP_DEMOTED", reply
    said = f"{reply['message']} {reply['next_action']}"
    assert "not known" in said and not any(word in said for word in NOTHING), said


# Review cfu1: a reply after an act says only what was read back ------------------------------------------------------
#
# Finding 1's class is failure_read_as_success, met in this change's loop and in the one before it; finding 2's is other:
# a part reported from a rule, not read. Both are removed at one root: every value in ``recorded``, and every sentence of
# a reply that says what is or is not on record, comes from reading the folder after the act, through the read-back
# functions, and only a process that never started may say nothing was recorded, and only when the folder agrees. The
# three inventories below are read from the code: every handler and return around an act's process, every key
# ``recorded`` can hold with the read that sets it, and a failure at each point after each act's process started.

def test_a_pipe_that_breaks_after_an_enrolment_is_durable_does_not_say_nothing_was_recorded(tmp_path, monkeypatch):
    """Finding 1 (review cfu1), as review cfu1 wrote it, in this test's own folder. Failed on 9738d69: AssertionError: MCP
    claims Nothing was recorded after the child's enrolment is durable."""
    rig = Rig(tmp_path, monkeypatch)

    class Child:
        returncode = 0

        def communicate(self, request, timeout):
            reply = runner.act(json.loads(request))
            assert code(reply) == "G6_E_LOOP_ENROLLED", reply
            raise OSError("act reply pipe read failed")       # the parent's read fails after the child finished

    monkeypatch.setattr(improve, "_run_act", real_run_act)
    monkeypatch.setattr(improve, "_interpreter", lambda settings: ["fake-python"])
    monkeypatch.setattr(improve.subprocess, "Popen", lambda *args, **kwargs: Child())
    reply = rig.call("g6_improve_enrol")
    assert rig.log().enrolled()["by"] == "Ada"
    said = reply["message"] + " " + reply["next_action"]
    assert "Nothing was recorded" not in said, "MCP claims Nothing was recorded after the child's enrolment is durable"
    assert reply.get("recorded") == {"the enrolment": True}


def test_a_stop_on_a_folder_with_no_constraint_log_names_no_note_in_it(tmp_path, monkeypatch):
    """Finding 2 (review cfu1), as review cfu1 wrote it. Failed on 9738d69: AssertionError: stop reports a durable
    constraint-log note although no constraint log exists."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    reply = improve.stop(str(tmp_path), "stop before first enrolment")
    assert reply["code"] == improve.STOP_ASKED
    assert not (tmp_path / CYCLE_LOG).exists()
    assert reply["recorded"]["its note in the constraint log"] is False, (
        "stop reports a durable constraint-log note although no constraint log exists")


def _defs(rel: str) -> dict[str, ast.AST]:
    """Each function of a module by name, a nested one as ``outer.inner``, a class's methods left out."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + child.name] = child
                visit(child, f"{prefix}{child.name}.")
            elif not isinstance(child, ast.ClassDef):
                visit(child, prefix)

    visit(ast.parse((SRC / rel).read_text(encoding="utf-8")), "")
    return found


def _own(function: ast.AST):
    """The nodes that are a function's own, a nested function's or lambda's left out."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            yield node
            stack.extend(ast.iter_child_nodes(node))


def _text(node: ast.AST, width: int = 70) -> str:
    return " ".join(ast.unparse(node).split())[:width]


def _assigned(function: ast.AST) -> dict[str, list[ast.AST]]:
    """Each local name a function assigns, with every value it is given there."""
    found: dict[str, list[ast.AST]] = {}
    for node in _own(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
                elif isinstance(target, ast.Tuple):
                    paired = isinstance(node.value, ast.Tuple) and len(node.value.elts) == len(target.elts)
                    for i, element in enumerate(target.elts):
                        if isinstance(element, ast.Name):
                            found.setdefault(element.id, []).append(node.value.elts[i] if paired else node.value)
    return found


def _reads(expression: ast.AST, assigned: dict[str, list[ast.AST]], seen: set[str] | None = None) -> set[str]:
    """Every call an expression's value comes through, following the local names it reads to what they were given."""
    seen = set() if seen is None else seen
    found: set[str] = set()
    for node in ast.walk(expression):
        if isinstance(node, ast.Call):
            found.add(ast.unparse(node.func))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in assigned and node.id not in seen:
            seen.add(node.id)
            for value in assigned[node.id]:
                found |= _reads(value, assigned, seen)
    return found


#: The modules around an act's process: the server's tools, the server's side of each act, and the act's own process.
AROUND_MODULES = ("mcp/__init__.py", "mcp/improve.py", "metaharness/runner.py")
#: What makes a function part of the path around an act's process: it starts one, talks to one, runs the act in one, or
#: reads its folder back after it. Every function that calls one of these is in ``AROUND``, with each of its exits.
_PATH = {"subprocess.Popen", "process.communicate", "_run_act", "_read_act", "_spawn", "_watch", "improve.act",
         "improve.start", "improve.stop", "act", "as_recorded", "on_record", "_on_record", "_read_back",
         "_start_on_record", "_start_as_recorded", "_stop_on_record", "ask_to_stop", "loop.enrol", "loop.resume",
         "loop.demote"}
#: Where, in a function that has one, the act's process starts or the act first writes: an exit before it is ``BEFORE``.
MARKERS = {("mcp/improve.py", "act"): "_run_act", ("mcp/improve.py", "_run_act"): "process.communicate",
           ("mcp/improve.py", "start"): "CycleLog(root / CYCLE_LOG).start", ("mcp/improve.py", "stop"): "ask_to_stop",
           ("mcp/__init__.py", "_metaharness_tools.person_act"): "asyncio.to_thread",
           ("mcp/__init__.py", "_metaharness_tools.g6_improve_start"): "asyncio.to_thread"}


def _marker_line(rel: str, name: str, function: ast.AST) -> int:
    """The line of the act's start or first write in ``function``; 0 when it has none, so nothing in it is before."""
    call = MARKERS.get((rel, name))
    lines = [node.lineno for node in _own(function) if isinstance(node, ast.Call) and ast.unparse(node.func) == call]
    return min(lines) if call else 0


def _exits(rel: str, name: str, function: ast.AST) -> list[tuple[str, ast.AST, ast.AST | None]]:
    """Each exception handler and each return of a function, as it reads in the code, with the ``try`` a handler is in."""
    found = []
    for node in _own(function):
        if isinstance(node, ast.Return):
            found.append((f"return {_text(node.value) if node.value is not None else 'None'}", node, None))
        elif isinstance(node, ast.Try):
            for handler in node.handlers:
                caught = ast.unparse(handler.type) if handler.type is not None else "everything"
                found.append((f"except {caught} around {_text(node.body[0], 40)}", handler, node))
    return found


def _on_path() -> dict[tuple[str, str], ast.AST]:
    """Each function of ``AROUND_MODULES`` on the path around an act's process (``_PATH``), read from the code: one that
    is named so, calls one, or hands one to a call (``asyncio.to_thread(improve.act, ...)``), and every function nested
    in one of them."""
    found = {}
    for rel in AROUND_MODULES:
        defs = _defs(rel)
        for name, node in defs.items():
            calls = [n for n in _own(node) if isinstance(n, ast.Call)]
            if name.rpartition(".")[2] in _PATH or any(ast.unparse(part) in _PATH for call in calls
                                                       for part in (call.func, *call.args)):
                found[(rel, name)] = node
        found |= {(rel, name): node for name, node in defs.items()
                  if any(name.startswith(f"{outer}.") for (where, outer) in found if where == rel)}
    return found


#: The read-backs themselves: each reads the folder after an act, or builds a reply from what was read.
READBACKS = {"_on_record", "_read_back", "_start_on_record", "_start_as_recorded", "_stop_on_record", "on_record",
             "as_recorded"}
#: Handed on: a value another exit in this table reads back, by a rule the test below checks from the code.
HANDED, ITSELF, NOT_KNOWN = "handed", "itself", "not known"
_TOOLS = "_metaharness_tools"
#: Every exception handler and return around an act's process, as it reads in the code: ``before`` the process starts
#: or the act first writes (``MARKERS``), so no part of it can be on record, or ``after``, with the read-back it goes
#: through: a function that reads the folder back, ``itself`` for a read-back, ``handed`` for a value handed to an exit
#: that reads back, or ``not known`` where nothing names a folder to read.
AROUND = {
    ("mcp/__init__.py", f"{_TOOLS}.g6_improve_start", "return denied"): ("before", ""),
    ("mcp/__init__.py", f"{_TOOLS}.g6_improve_start", "return found"): ("before", ""),
    ("mcp/__init__.py", f"{_TOOLS}.g6_improve_start", "return refusal(consent, nothing='Nothing started.')"): ("before", ""),
    ("mcp/__init__.py", f"{_TOOLS}.g6_improve_start",
     "return await asyncio.to_thread(improve.start, root, consent.answer, shown)"): ("after", HANDED),
    ("mcp/__init__.py", f"{_TOOLS}.g6_improve_stop", "return improve.stop(folder, reason)"): ("after", HANDED),
    ("mcp/__init__.py", f"{_TOOLS}.person_act", "return denied"): ("before", ""),
    ("mcp/__init__.py", f"{_TOOLS}.person_act", "return found"): ("before", ""),
    ("mcp/__init__.py", f"{_TOOLS}.person_act", "return refusal(consent, nothing='Nothing was recorded.')"): ("before", ""),
    ("mcp/__init__.py", f"{_TOOLS}.person_act",
     "return await asyncio.to_thread(improve.act, act, root, consent.answer, shown,"): ("after", HANDED),
    ("mcp/improve.py", "_on_record", "return on_record(request)"): ("after", ITSELF),
    ("mcp/improve.py", "_read_act", "return reply"): ("after", HANDED),
    ("mcp/improve.py", "_read_act",
     "return _blocked(ACT_FAILED, f\"{what}, {read}{(': ' + tail[-300:] if tail else"): ("after", "_read_back"),
    ("mcp/improve.py", "_read_back",
     "except (OSError, ValueError, LedgerError, KeyError, TypeError) around found = _on_record(request)"): ("after", ITSELF),
    ("mcp/improve.py", "_read_back",
     "return (None, f\"its folder's records do not read back ({type(exc).__name__}),"): ("after", ITSELF),
    ("mcp/improve.py", "_read_back",
     "return (held, said + (': its process may still be running' if None in held.va"): ("after", ITSELF),
    ("mcp/improve.py", "_run_act", "except (OSError, ValueError, subprocess.SubprocessError) around process = "
     "subprocess.Popen(command, cwd="): ("before", "_read_back"),
    ("mcp/improve.py", "_run_act",
     "return _blocked(ACT_FAILED, f\"the act's process could not be started ({type(e"): ("before", "_read_back"),
    ("mcp/improve.py", "_run_act",
     "except subprocess.TimeoutExpired around out, err = process.communicate(json.dump"): ("after", "_read_act"),
    ("mcp/improve.py", "_run_act", "except (OSError, ValueError, subprocess.SubprocessError) around out, err = "
     "process.communicate(json.dump"): ("after", "_read_act"),
    ("mcp/improve.py", "_run_act",
     "return _read_act(process.returncode, out, err, act_name=request.get('act'), r"): ("after", "_read_act"),
    ("mcp/improve.py", "_spawn",
     "return subprocess.Popen([*_interpreter(settings), '-m', 'g6.metaharness.loop'"): ("after", HANDED),
    ("mcp/improve.py", "_start_as_recorded",
     "except (OSError, LedgerError) around found = _start_on_record(root, at)"): ("after", ITSELF),
    ("mcp/improve.py", "_start_as_recorded", "return {**reply, 'recorded': None}"): ("after", ITSELF),
    ("mcp/improve.py", "_start_as_recorded",
     "return _restated(reply, f\"{reply['message']}; the folder's records do not rea"): ("after", ITSELF),
    ("mcp/improve.py", "_start_as_recorded",
     "return _restated(reply, f\"{reply['message']}. The start is on record, so the "): ("after", ITSELF),
    ("mcp/improve.py", "_start_as_recorded", "return {**reply, 'recorded': found}"): ("after", ITSELF),
    ("mcp/improve.py", "_start_on_record",
     "return {'the start': bool(ours) and (not stop_standing(root, started))}"): ("after", ITSELF),
    ("mcp/improve.py", "_stop_on_record", "except (OSError, LedgerError) around log = _log(root)"): ("after", ITSELF),
    ("mcp/improve.py", "_stop_on_record",
     "return {'the stop': stands, 'its line in the STOP file': line, 'its count in "): ("after", ITSELF),
    ("mcp/improve.py", "_watch",
     "return _blocked(START_FAILED, f'the runner ended as it started, with exit cod"): ("after", HANDED),
    ("mcp/improve.py", "_watch",
     "return _envelope('qualified-draft', STARTED, f'the runner is alive on {root}'"): ("after", HANDED),
    ("mcp/improve.py", "_watch",
     "return _envelope('qualified-draft', STARTED, f'the runner was started on {roo"): ("after", HANDED),
    ("mcp/improve.py", "act", "except ValueError around settings = read_settings(root)"): ("before", ""),
    ("mcp/improve.py", "act",
     "return _blocked(TERMS_CHANGED, f\"what the person read is no longer what stand"): ("before", ""),
    ("mcp/improve.py", "act", "return cannot"): ("before", ""),
    ("mcp/improve.py", "act", "return {**_run_act(root, settings, request), 'by': who}"): ("after", "_run_act"),
    ("mcp/improve.py", "start", "return _not_what_was_read(root)"): ("before", ""),
    ("mcp/improve.py", "start", "return cannot"): ("before", ""),
    ("mcp/improve.py", "start",
     "except (OSError, LedgerError, WorkspaceRefused, ValueError) around now = _terms(root)"): ("after", "_start_as_recorded"),
    ("mcp/improve.py", "start", "return _start_as_recorded(root, at, reply) if at else reply"): ("after", "_start_as_recorded"),
    ("mcp/improve.py", "stop", "return _not_a_loop(root)"): ("before", ""),
    ("mcp/improve.py", "stop", "except OSError around why = ask_to_stop(root, reason)"): ("after", "_stop_on_record"),
    ("mcp/improve.py", "stop",
     "return _blocked(STOP_FAILED, f'the stop is not known to stand; {_record_said("): ("after", "_stop_on_record"),
    ("mcp/improve.py", "stop",
     "return _envelope('qualified-draft', STOP_ASKED, f'{said}; {_record_said(found"): ("after", "_stop_on_record"),
    ("metaharness/runner.py", "act", "return as_recorded(request, refused)"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act",
     "return as_recorded(request, _blocked(TERMS_CHANGED, f'{SETTINGS_FILE} changed"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act", "except DomainChanged around settings = read_settings(root)"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act",
     "return as_recorded(request, _blocked(FROZEN_CHANGED, str(exc)[:500], again))"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act", "except Exception around settings = read_settings(root)"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act",
     "return as_recorded(request, _blocked(CANNOT_OPEN, f'The loop could not be ope"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act",
     "except Exception around if name == 'enrol': reply = loop.enrol(w"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act", "return as_recorded(request, None, exc)"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act", "return as_recorded(request, reply)"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act",
     "except WorkspaceRefused around with folder_lock(root): refused = unchan"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act",
     "return as_recorded(request, _blocked(BUSY, 'another operation is running on t"): ("after", "as_recorded"),
    ("metaharness/runner.py", "act.unchanged", "return None"): ("after", HANDED),
    ("metaharness/runner.py", "act.unchanged",
     "return _blocked(TERMS_CHANGED, f\"what the person read is no longer what stand"): ("after", HANDED),
    ("metaharness/runner.py", "act_main",
     "except (ValueError, KeyError, TypeError) around request = json.loads(sys.stdin.read())"): ("after", NOT_KNOWN),
    ("metaharness/runner.py", "act_main",
     "return 1 if reply['completion_state'] == 'blocked-escalated' else 0"): ("after", HANDED),
    ("metaharness/runner.py", "as_recorded",
     "except (OSError, ValueError, LedgerError) around found = on_record(request)"): ("after", ITSELF),
    ("metaharness/runner.py", "as_recorded", "return {**reply, 'recorded': None}"): ("after", ITSELF),
    ("metaharness/runner.py", "as_recorded",
     "return _envelope(reply['completion_state'], reply['warning_card']['code'], f'"): ("after", ITSELF),
    ("metaharness/runner.py", "as_recorded",
     "return _blocked(CANNOT_RECORD, f'the act did not finish ({why}), and {unknown"): ("after", ITSELF),
    ("metaharness/runner.py", "as_recorded", "return {**reply, 'recorded': found}"): ("after", ITSELF),
    ("metaharness/runner.py", "as_recorded",
     "return _envelope('qualified-draft', DONE[name], f'{effect} is on record, read"): ("after", ITSELF),
    ("metaharness/runner.py", "as_recorded",
     "return _blocked(CANNOT_RECORD, f'the act could not be recorded ({why}): read "): ("after", ITSELF),
    ("metaharness/runner.py", "as_recorded",
     "return _blocked(CANNOT_RECORD, f\"the act could not be recorded ({why}): read "): ("after", ITSELF),
    ("metaharness/runner.py", "on_record",
     "return {'the enrolment': row.get('by') == who and (row.get('frozen') or {}).g"): ("after", ITSELF),
    ("metaharness/runner.py", "on_record",
     "return {'the lifted pause': log is not None and (not held(log))}"): ("after", ITSELF),
    ("metaharness/runner.py", "on_record",
     "return {'the demotion': chain['current'] == after and head.get('kind') == 'ro"): ("after", ITSELF),
}


def _reaches(function: ast.AST, node: ast.AST, through: str) -> bool:
    return through in _reads(node, _assigned(function))


def _shown_to_read_back(function: ast.AST, exit_node: ast.AST, around: ast.AST | None, through: str) -> bool:
    """Whether an exit goes through ``through``: a return's value does, or a handler's returns do, or, for a handler that
    returns nothing, every return after its ``try``, of which there is one at least."""
    if isinstance(exit_node, ast.Return):
        return _reaches(function, exit_node.value, through)
    returns = [n for n in ast.walk(exit_node) if isinstance(n, ast.Return)]
    if not returns:
        returns = [n for n in _own(function) if isinstance(n, ast.Return) and n.lineno > around.end_lineno]
    return bool(returns) and all(_reaches(function, n.value, through) for n in returns)


def test_every_handler_and_return_around_an_acts_process_is_before_it_started_or_reads_the_folder_back():
    """The class's first inventory. Each function on the path around an act's process, read from the code, and each of
    its exception handlers and returns: before the process started or the act first wrote, by the code's own lines, or
    after, and then shown to go through a read-back. One this cannot classify fails."""
    found, wrong = {}, []
    for (rel, name), function in _on_path().items():
        marker = _marker_line(rel, name, function)
        for text, exit_node, around in _exits(rel, name, function):
            key = (rel, name, text)
            found[key] = exit_node
            if key not in AROUND:
                wrong.append(f"not classified: {key}")
                continue
            side, through = AROUND[key]
            if side == "before" and not exit_node.lineno < marker:
                wrong.append(f"said to be before {MARKERS.get((rel, name))!r}, but is not: {key}")
            if side == "after" and not through:
                wrong.append(f"after the process started, and names no read-back: {key}")
            if through == ITSELF and name.rpartition(".")[2] not in READBACKS:
                wrong.append(f"said to be a read-back, but is not one: {key}")
            if through == NOT_KNOWN and "recorded=None" not in ast.unparse(exit_node):
                wrong.append(f"said to be not known, but does not say so: {key}")
            if through not in ("", ITSELF, HANDED, NOT_KNOWN) and not _shown_to_read_back(function, exit_node, around,
                                                                                         through):
                wrong.append(f"does not go through {through}: {key}")
    assert not wrong, "\n".join(wrong)
    assert set(found) == set(AROUND), f"listed but not in the code: {sorted(set(AROUND) - set(found))}"


def test_what_is_handed_on_after_an_acts_process_started_reaches_a_read_back():
    """The ``handed`` exits above, each by the rule that hands it on: a reply of the act's own process went through its
    read-back (``runner.as_recorded``) or says that nothing can be read; the runner's process and what watching it says
    reach the start only after the start's record, and its reply reads the folder back; the tools hand each reply on
    whole."""
    runs = _defs("metaharness/runner.py")
    act_fn, main = runs["act"], runs["act_main"]
    assert all(_reaches(act_fn, n.value, "as_recorded") for n in _own(act_fn) if isinstance(n, ast.Return))
    users = {(rel, name) for rel in AROUND_MODULES for name, f in _defs(rel).items() for n in _own(f)
             if isinstance(n, ast.Name) and n.id == "unchanged"}
    assert users == {("metaharness/runner.py", "act")}, "a refusal of unchanged reaches a reply some other way"
    replies = _assigned(main)["reply"]
    assert replies and all("recorded=None" in ast.unparse(value) or {"as_recorded", "act"} & _reads(value, {})
                           for value in replies)
    improves = _defs("mcp/improve.py")
    start = improves["start"]
    marker = _marker_line("mcp/improve.py", "start", start)
    for helper in ("_spawn", "_watch"):
        callers = {name for name, f in improves.items() for n in _own(f)
                   if isinstance(n, ast.Call) and ast.unparse(n.func) == helper}
        lines = [n.lineno for n in _own(start) if isinstance(n, ast.Call) and ast.unparse(n.func) == helper]
        assert callers == {"start"} and lines and min(lines) > marker, helper
    last = max((n for n in _own(start) if isinstance(n, ast.Return)), key=lambda n: n.lineno)
    assert _reaches(start, last.value, "_start_as_recorded")
    tools = _defs("mcp/__init__.py")
    for name in ("g6_improve_start", "g6_improve_stop", "person_act"):
        handed = [n for n in _own(tools[f"{_TOOLS}.{name}"]) if isinstance(n, ast.Return)
                  and AROUND.get(("mcp/__init__.py", f"{_TOOLS}.{name}", f"return {_text(n.value)}"), ("", ""))[1] == HANDED]
        assert len(handed) == 1, name
        value = handed[0].value.value if isinstance(handed[0].value, ast.Await) else handed[0].value
        assert isinstance(value, ast.Call), f"{name} reads its act's reply before handing it on"


#: What a reply may say of what is on record only through a read-back: words that claim a part is, or is not, there.
_RECORD_WORDS = re.compile(r"\b(?:was|were|is|are|be|been) (?:not )?recorded\b|\bon record\b|\bnothing counts\b|"
                           r"\bact counts\b", re.IGNORECASE)


def test_no_sentence_after_an_acts_process_started_says_what_is_on_record_but_a_read_back():
    """Every string in an exit's code after the act's process started, outside the read-backs, read from the code: none
    says what is or is not on record, so each sentence that does is written by a read-back from what it read."""
    wrong = []
    for (rel, name), function in _on_path().items():
        if name.rpartition(".")[2] in READBACKS:
            continue
        marker = _marker_line(rel, name, function)
        documented = {id(n.value) for n in function.body[:1] if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for node in _own(function):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.lineno >= marker
                    and id(node) not in documented):
                if _RECORD_WORDS.search(node.value):
                    wrong.append(f"{rel} {name}:{node.lineno}: {node.value[:80]!r}")
    assert not wrong, "\n".join(wrong)


#: Each part a reply after an act names in ``recorded``, by the function that reads it back, with the reads its value
#: must come through. One set any other way, from a constant or a rule that does not read that part, fails.
PARTS = {
    ("metaharness/runner.py", "on_record", "the enrolment"): {"log.enrolled", "enrolment_digest"},
    ("metaharness/runner.py", "on_record", "the lifted pause"): {"held"},
    ("metaharness/runner.py", "on_record", "the demotion"): {"lineage"},
    ("metaharness/runner.py", "on_record", "its note in the constraint log"): {"log._rows"},
    ("mcp/improve.py", "_start_on_record", "the start"): {"log.started", "log.start_in_force", "stop_standing"},
    ("mcp/improve.py", "_stop_on_record", "the stop"): {"_stop_file_size", "stop_standing", "log._rows"},
    ("mcp/improve.py", "_stop_on_record", "its line in the STOP file"): {"_stop_file_size"},
    ("mcp/improve.py", "_stop_on_record", "its count in stops.log"): {"stops_asked"},
    ("mcp/improve.py", "_stop_on_record", "its note in the constraint log"): {"log._rows"},
}
#: What a reply's ``recorded`` may be set from: a read-back's result, or ``None``, which is not known.
_SOURCES = {"on_record", "_on_record", "_start_on_record", "_stop_on_record", "_read_back"}


def _recorded_sites():
    """Every place in the modules around an act's process that sets a reply's ``recorded``, read from the code: a
    keyword, a key of a dict display, or an item assigned."""
    for rel in AROUND_MODULES:
        for name, function in _defs(rel).items():
            for node in _own(function):
                if isinstance(node, ast.Call):
                    yield from ((rel, name, function, k.value) for k in node.keywords if k.arg == "recorded")
                elif isinstance(node, ast.Dict):
                    yield from ((rel, name, function, v) for k, v in zip(node.keys, node.values)
                                if isinstance(k, ast.Constant) and k.value == "recorded")
                elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Subscript) and ast.unparse(t.slice) == "'recorded'"
                                                          for t in node.targets):
                    yield rel, name, function, None


def test_every_key_a_replys_recorded_holds_is_set_by_the_read_of_its_part():
    """The class's second inventory. Each dict a read-back returns, read from the code, holds only the parts listed,
    each from its own read, never a constant; and every place that sets a reply's ``recorded`` sets it from a read-back
    or to ``None``, never any other way."""
    wrong, keys = [], set()
    for rel, name in sorted({(rel, name) for rel, name, _ in PARTS}):
        function = _defs(rel)[name]
        assigned = _assigned(function)
        for node in _own(function):
            if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)):
                continue
            for k, v in zip(node.value.keys, node.value.values):
                part = k.value if isinstance(k, ast.Constant) else ast.unparse(k)
                keys.add((rel, name, part))
                if (rel, name, part) not in PARTS:
                    wrong.append(f"{rel} {name}: {part!r} is not a part listed with its read")
                elif isinstance(v, ast.Constant) or not PARTS[(rel, name, part)] <= _reads(v, assigned):
                    wrong.append(f"{rel} {name}: {part!r} is not set by {sorted(PARTS[(rel, name, part)])}: {_text(v)}")
                elif any(isinstance(value, ast.Constant) and value.value is not None
                         for n in ast.walk(v) if isinstance(n, ast.Name) for value in assigned.get(n.id, [])):
                    wrong.append(f"{rel} {name}: {part!r} can be set from a constant")
    assert keys == set(PARTS), f"listed but not read: {sorted(set(PARTS) - keys)}"
    for rel, name, function, value in _recorded_sites():
        if value is None or isinstance(value, ast.Constant):
            if value is None or value.value is not None:
                wrong.append(f"{rel} {name}: recorded is set to {ast.unparse(value) if value else 'an item'}")
            continue
        given = _assigned(function).get(value.id, []) if isinstance(value, ast.Name) else [value]
        if not given or not all(isinstance(g, ast.Constant) and g.value is None or _reads(g, _assigned(function)) & _SOURCES
                                for g in given):
            wrong.append(f"{rel} {name}: recorded is set from {_text(value)}, not a read-back")
    back = _defs("mcp/improve.py")["_read_back"]
    assert "_on_record" in _reads(_assigned(back)["held"][0], _assigned(back))
    assert not wrong, "\n".join(wrong)
    named = {part for _, _, part in PARTS}
    assert named == {p for parts, _ in WRITING_ACTS.values() for p in parts} - {"the pause"}
    assert {part for parts in runner.ACT_PARTS.values() for part in parts} == {
        part for rel, name, part in PARTS if name == "on_record"}


#: Each way the server's talk with an act's process can fail once the process has started, with the exit status the
#: process then shows: a number once it has ended, ``None`` while it may still be running.
TALK_FAILS = {
    "its pipe breaks": (lambda: OSError("the act's reply pipe broke"), 1),
    "its output does not decode": (lambda: UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), 1),
    "the wait for it fails": (lambda: OSError("the wait for the act's process failed"), None),
    "it outlives ACT_WAIT": (lambda: subprocess.TimeoutExpired("act", improve.ACT_WAIT), None),
    "its reply is cut off": (None, 1),
}


def _expected(held: dict[str, bool], status: int | None) -> dict[str, bool | None]:
    """What a reply may name of each part: on record as it is; not on record only once the process has ended, and
    otherwise not known, since a process that may still run may still write it."""
    return {part: True if kept else False if status is not None else None for part, kept in held.items()}


def _said(reply: dict) -> str:
    return f"{reply['message']} {reply.get('next_action', '')}"


@pytest.mark.parametrize("act", ["enrol", "resume", "demote"])
def test_a_failure_talking_to_an_acts_process_after_it_started_says_only_what_the_reopened_folder_holds(
        tmp_path, monkeypatch, act):
    """The class's third inventory, for the acts a person agrees to. Each act's process writes its first ``landed`` writes
    and ends (all, a part, or none of them, read from a run of the act), and then the server's talk with it fails each
    way it can once the process started. The folder is reopened, and the reply must name each part as it holds it,
    never say nothing was recorded, and never say nothing counts with a part on record."""
    writes, wrong = _writes(tmp_path, monkeypatch, act), []
    for landed in range(len(writes) + 1):
        for way, (error, status) in TALK_FAILS.items():
            with monkeypatch.context() as patch:
                rig = _rig(tmp_path / f"{landed}-{list(TALK_FAILS).index(way)}", patch)
                ready = _readied(rig, act)
                spy = _Writes(patch, fail=writes[landed][0] if landed < len(writes) else None, how="dies")

                class Child:
                    returncode = status

                    def communicate(self, request, timeout, error=error):
                        try:
                            runner.act(json.loads(request))
                        except _Killed:
                            pass
                        if error is None:
                            return '{"completion_state": "qualified-dr', ""
                        raise error()

                patch.setattr(improve, "_run_act", real_run_act)
                patch.setattr(improve, "_interpreter", lambda settings: ["fake-python"])
                patch.setattr(improve.subprocess, "Popen", lambda *a, **k: Child())
                reply = _act(rig, act, patch)
            assert spy.seen, "the act's process wrote nothing"
            held = _on_disk(rig, act, ready)
            label = f"{landed} of {len(writes)} writes landed, then {way}"
            if code(reply) != improve.ACT_FAILED:
                wrong.append(f"{label}: replied {code(reply)}")
            if reply.get("recorded") != _expected(held, status):
                wrong.append(f"{label}: names {reply.get('recorded')}, the folder holds {held}")
            if "Nothing was recorded" in _said(reply) or any(held.values()) and any(w in _said(reply) for w in NOTHING):
                wrong.append(f"{label}: says nothing was recorded or counts with {held} on record: {_said(reply)}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("act", ["enrol", "resume", "demote"])
@pytest.mark.parametrize("ran", [False, True])
def test_an_acts_process_that_never_started_says_nothing_was_recorded_only_when_the_folder_agrees(
        tmp_path, monkeypatch, act, ran):
    """Starting the act's process fails. With nothing of the act on record the reply may say nothing was recorded; when
    the folder holds a part of it (here, a start that ran the act and then raised), it says what it holds instead."""
    rig = _rig(tmp_path / "rig", monkeypatch)
    ready = _readied(rig, act)
    generation = 1 if act == "demote" else None
    root, _, bound = improve.asked_act(act, str(rig.root), generation)
    request = {"act": act, "folder": str(root), "who": "Ada", "binding": bound, "generation": generation}

    def popen(*args, **kwargs):
        if ran:
            runner.act(request)                   # the act ran, and creating its process raised after
        raise OSError("the act's process could not be created")

    monkeypatch.setattr(improve.subprocess, "Popen", popen)
    monkeypatch.setattr(improve, "_interpreter", lambda settings: ["fake-python"])
    reply = real_run_act(root, runner.read_settings(root), request)
    held = _on_disk(rig, act, ready)
    assert any(held.values()) == ran and code(reply) == improve.ACT_FAILED, (held, reply)
    assert reply["recorded"] == held, (reply["recorded"], held)
    assert ("Nothing was recorded" in _said(reply)) == (not ran), _said(reply)
    assert ("nothing of it ran" in reply["message"]) == (not ran), reply["message"]


def test_a_refusal_in_the_acts_process_whose_folder_no_longer_reads_keeps_its_code_and_claims_nothing(tmp_path,
                                                                                                    monkeypatch):
    """The act's own refusal goes through its read-back like every reply of its process. With records that stop reading
    between the server's check and the act's lock, it is still the refusal, and says what of it counts is not known."""
    rig = _rig(tmp_path / "rig", monkeypatch)
    _readied(rig, "resume")

    def broken_then_act(root, settings, request):
        _damaged(rig)
        return runner.act(request)

    monkeypatch.setattr(improve, "_run_act", broken_then_act)
    reply = rig.call("g6_improve_resume")
    assert code(reply) == improve.TERMS_CHANGED and reply["recorded"] is None, reply
    assert "not known" in reply["message"] and not any(word in _said(reply) for word in NOTHING), _said(reply)


class _Runner:
    """The runner's process as ``_watch`` sees it once it has started: ``then`` runs at its first poll, and it fails one
    way (``RUNNER_FAILS``)."""

    def __init__(self, way: str, then) -> None:
        self.way, self.then, self.polls = way, then, 0

    def poll(self):
        if not self.polls:
            self.then()
        self.polls += 1
        if self.way == "polling it raises":
            raise OSError("the runner's process could not be polled")
        return 1 if self.way == "it ends at once" else None


RUNNER_FAILS = ("polling it raises", "it ends at once", "it never opens its folder")


def _damaged(rig) -> None:
    path = rig.root / CYCLE_LOG
    path.write_text(path.read_text(encoding="utf-8").replace('"Ada"', '"Cy"'), encoding="utf-8")


#: What happens to the start's record while its runner is being watched: nothing, a stop that ends it, or records
#: that no longer read back.
MEANWHILE = {"nothing": lambda rig: None, "a stop": lambda rig: improve.stop(str(rig.root), "a stop while it starts"),
             "records that no longer read": _damaged}


@pytest.mark.parametrize("way", RUNNER_FAILS)
@pytest.mark.parametrize("meanwhile", sorted(MEANWHILE))
def test_a_failure_after_the_runners_process_started_says_only_what_the_reopened_folder_holds_of_the_start(
        tmp_path, monkeypatch, way, meanwhile):
    """The class's third inventory, for a start: its one write, the start's record, has landed when its process starts,
    so what follows can only change whether it stands. Each way watching the runner fails, with the start standing,
    ended by a stop, or its records unreadable, and the reply names what the reopened folder holds."""
    rig = _rig(tmp_path / "rig", monkeypatch)
    _readied(rig, "start")
    monkeypatch.setattr(improve, "START_WAIT", 0.3)
    monkeypatch.setattr(improve, "_spawn", lambda root, settings: _Runner(way, lambda: MEANWHILE[meanwhile](rig)))
    reply = rig.call("g6_improve_start")
    try:
        log = CycleLog(rig.root / CYCLE_LOG)
        started = log.started() or {}
        held = {"the start": log.start_in_force() and started.get("by") == "Ada" and not stop_standing(rig.root, started)}
    except LedgerError:
        held = None
    assert reply["recorded"] == held, (reply["recorded"], held, reply["message"])
    if held is None:
        assert code(reply) == improve.STARTED or "not known" in reply["message"], reply["message"]
    elif held["the start"]:
        assert code(reply) == improve.STARTED or "The start is on record" in reply["message"], reply["message"]
        assert not any(word in _said(reply) for word in NOTHING), _said(reply)
    else:
        assert code(reply) != improve.STARTED or meanwhile == "a stop", reply


#: What the stop's read-back may not be able to read afterwards, and what makes it so.
STOP_UNREADABLE = ("nothing", "its STOP file", "its stops.log", "its constraint log")


@pytest.mark.parametrize("unreadable", STOP_UNREADABLE)
def test_a_stop_names_each_part_as_the_reopened_folder_holds_it_and_none_it_cannot_read(tmp_path, monkeypatch,
                                                                                       unreadable):
    """The class's third inventory, for a stop: its writes (its count, its line, its note) land all, in part or not at
    all, and then a part may not read back. The reply names each part as the reopened folder holds it, one it cannot
    read as not known, and is a stop asked only when the stop is on record."""
    writes, wrong = _writes(tmp_path, monkeypatch, "stop"), []
    real_stat, real_read, real_log = Path.stat, Path.read_bytes, CycleLog.__init__
    for landed in range(len(writes) + 1):
        armed = {"on": False}

        def stat(self, *args, **kwargs):
            if armed["on"] and unreadable == "its STOP file" and self.name == "STOP":
                raise PermissionError("STOP cannot be read")
            return real_stat(self, *args, **kwargs)

        def read_bytes(self):
            if armed["on"] and unreadable == "its stops.log" and self.name == "stops.log":
                raise PermissionError("stops.log cannot be read")
            return real_read(self)

        def opened(self, path):
            if armed["on"] and unreadable == "its constraint log":
                raise LedgerError("the constraint log does not check out")
            real_log(self, path)

        with monkeypatch.context() as patch:
            rig = _rig(tmp_path / f"{landed}", patch)
            ready = _readied(rig, "stop")
            _Writes(patch, fail=writes[landed][0] if landed < len(writes) else None, onward=True)
            patch.setattr(Path, "stat", stat)
            patch.setattr(Path, "read_bytes", read_bytes)
            patch.setattr(CycleLog, "__init__", opened)
            patch.setattr(improve, "runner_alive", lambda root: armed.update(on=True) or False)
            reply = improve.stop(str(rig.root), "the owner's review")
            rule = bool(stop_standing(rig.root, ready["started"])) if ready["started"] else False
            armed["on"] = False
        held = _on_disk(rig, "stop", ready)
        log = rig.log()
        expected = {**held, "its line in the STOP file": None if unreadable == "its STOP file" else held[
                        "its line in the STOP file"],
                    "its count in stops.log": None if unreadable == "its stops.log" else held["its count in stops.log"],
                    "its note in the constraint log": None if unreadable == "its constraint log" else held[
                        "its note in the constraint log"]}
        line, note = expected["its line in the STOP file"], expected["its note in the constraint log"]
        ended = None if note is None else bool(note) or ready["started"] is not None and (not log.start_in_force() or rule)
        expected["the stop"] = True if line or ended else None if line is None or ended is None else False
        label = f"{landed} of {len(writes)} writes landed, {unreadable} unreadable"
        if reply.get("recorded") != expected:
            wrong.append(f"{label}: names {reply.get('recorded')}, the folder holds {expected}")
        if (code(reply) == improve.STOP_ASKED) != (expected["the stop"] is True):
            wrong.append(f"{label}: replied {code(reply)} with the stop {expected['the stop']}")
        if any(expected.values()) and any(word in _said(reply) for word in NOTHING):
            wrong.append(f"{label}: says nothing counts with {expected}: {_said(reply)}")
    assert not wrong, "\n".join(wrong)


def test_the_loops_own_pause_reads_as_the_reopened_folder_holds_it_whatever_its_process_wrote(tmp_path, monkeypatch):
    """The class's third inventory, for the loop's own pause: the runner's process records the third failed cycle and
    the pause, and ends after all, a part or none of those writes. The status, the reply that says the pause, reads
    the reopened folder: paused exactly when something on record holds the ticks."""
    writes, wrong = _writes(tmp_path, monkeypatch, "pause"), []
    for landed in range(len(writes) + 1):
        with monkeypatch.context() as patch:
            rig = _rig(tmp_path / f"{landed}", patch)
            ready = _readied(rig, "pause")
            _Writes(patch, fail=writes[landed][0] if landed < len(writes) else None, how="dies")
            try:
                _act(rig, "pause", patch)
            except _Killed:
                pass
        reply, held = improve.status(str(rig.root)), _on_disk(rig, "pause", ready)
        if (reply["pause"] is not None) != held["the pause"]:
            wrong.append(f"{landed} of {len(writes)} writes landed: the status says pause {reply['pause']}, held {held}")
    assert not wrong, "\n".join(wrong)


real_run_act = improve._run_act
