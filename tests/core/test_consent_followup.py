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
    then raising (a ``fsync`` that fails after the line is out), or ``killed``, on disk and then the process ends."""

    def __init__(self, monkeypatch, fail: int | None = None, how: str = "refused", error: type = OSError) -> None:
        self.seen: list[str] = []
        self.fail, self.how, self.error = fail, how, error
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
            if index == self.fail and self.how == "refused":
                raise OSError(f"no room for {label}")
            handle = real_open(file, mode, *args, **kwargs)
            return _RaisesOnClose(handle, self._raised(label)) if index == self.fail else handle

        monkeypatch.setattr(ChainLedger, "append", append)
        monkeypatch.setattr(api, "_write_state", state)
        monkeypatch.setattr(envelope_module, "open", opened, raising=False)

    def _raised(self, label: str) -> BaseException:
        return _Killed(label) if self.how == "killed" else self.error(f"{label} could not be finished")

    def _write(self, label: str, write):
        index = len(self.seen)
        self.seen.append(label)
        if index != self.fail:
            return write()
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
    return {"stops": stops_asked(rig.root), "started": log.started() if log.start_in_force() else None}


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
        before, noted = ready["started"], not log.start_in_force()
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


real_run_act = improve._run_act
