# SPDX-License-Identifier: Apache-2.0
"""One binding from what a person is shown to what the act does, and one reader of a child's act.

Repair 1 of the consent change: review cons1's six findings, each brought in as a test of this file and failing on
fc80f8e, and the inventories that remove their classes at the root, built from the code:

- failure_read_as_success (finding 1): a child's act counts only on its exit status and a reply recognised as done
  (``improve._read_act``), and every place the act path reads a child's status goes through that reader;
- check_then_act_race (findings 2, 3, 4 and 6): each act that asks through ``g6.mcp.consent`` makes its binding once,
  builds its dialog from it, passes it to the act, and the act reads it again holding the folder's lock immediately
  before it runs or records anything, and runs and records only what it names;
- undisclosed consent identity (finding 5): every act's dialog is written from its binding by one function
  (``g6.mcp.consent.question``), so every field it binds is shown.

FakeLLM only: no model, no runner process, no network. The act runs in this process.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.mcp import _metaharness_tools, improve
from g6.metaharness import runner
from g6.metaharness.api import _envelope
from g6.metaharness.surface import Surface
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, file_digest

SRC = Path(improve.__file__).resolve().parents[1]
START = datetime(2026, 10, 26, 8, tzinfo=UTC)
DOMAIN_RIG = Path(__file__).resolve().parents[1] / "metaharness" / "_loop_domains.py"
DOMAIN = '''
import importlib.util
import sys
from pathlib import Path
from g6.metaharness.method import Method
spec = importlib.util.spec_from_file_location("binding_loop_domains", {rig!r})
domains = sys.modules.setdefault(spec.name, importlib.util.module_from_spec(spec))
if not hasattr(domains, "WordProblems"):
    spec.loader.exec_module(domains)
LOOP = {{"domain": domains.WordProblems(Path(__file__).parent),
        "proposer": domains.proposes({{"prompt": domains.WORKING}}),
        "method": Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1),
        "audit_per_tick": 60}}
'''


class Server:
    """Registers the real MCP handlers by name, with no MCP server."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function
        return register


class Rig:
    """A loop's folder on the word-problems domain and the real handlers; each dialog is a fake that agrees as Ada."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.improve")
        self.tmp, self.root, self.domain = tmp_path, tmp_path / "loop", tmp_path / "domain.py"
        self.root.mkdir()
        self.source = DOMAIN.format(rig=str(DOMAIN_RIG))
        self.domain.write_text(self.source, encoding="utf-8")
        self.settings = {"domain_file": str(self.domain), "read_account": False,
                         "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": START.isoformat()}]}
        (self.root / "settings.json").write_text(json.dumps(self.settings), encoding="utf-8")
        self.server = Server()
        _metaharness_tools(self.server, None, object)
        self.questions: list[str] = []
        monkeypatch.setattr(improve, "_run_act", lambda root, settings, request: runner.act(request))

    def call(self, tool: str, *, handler=None, **arguments) -> dict:
        async def elicit_form(**kwargs):
            self.questions.append(kwargs["message"])
            return handler(kwargs["message"]) if handler else SimpleNamespace(action="accept", content={"value": "Ada"})

        ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form), request_id="binding")
        return asyncio.run(self.server.tools[tool](ctx=ctx, folder=str(self.root), **arguments))

    def log(self) -> CycleLog:
        return CycleLog(self.root / CYCLE_LOG)


def code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def declined(_message):
    return SimpleNamespace(action="decline", content=None)


def _paused(rig: Rig) -> None:
    runner.open_loop(rig.root)[0].log.pause("three failed cycles in a row")


def _adopted(rig: Rig) -> None:
    loop, _ = runner.open_loop(rig.root, now=lambda: START + timedelta(days=2))
    assert loop.tick()["outcome"] == "adopted"           # deterministic FakeLLM, no runner process


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


# Finding 1, failure_read_as_success: one reader of a child's act --------------------------------------------------------

@pytest.mark.parametrize("exit_code", [1, 2, -9, 12345])
def test_a_child_that_exited_with_a_failure_cannot_claim_its_act(tmp_path, monkeypatch, exit_code):
    """Finding 1 (review cons1). Failed on fc80f8e: ``G6_E_LOOP_ENROLLED`` was taken from a child that exited 1, 2, -9
    and 12345."""
    success = json.dumps(_envelope("qualified-draft", "G6_E_LOOP_ENROLLED", "enrolled"))

    class Child:
        returncode = exit_code

        def communicate(self, request, timeout):
            return success + "\n", "fatal error after output"

    monkeypatch.setattr(improve.subprocess, "Popen", lambda *a, **kw: Child())
    reply = improve._run_act(tmp_path, {}, {"act": "enrol"})
    assert reply["warning_card"]["code"] == improve.ACT_FAILED, (
        f"child exited {exit_code}, but parent accepted {reply['warning_card']['code']}")


def _done(said: str) -> str:
    return json.dumps(_envelope("qualified-draft", said, "done"))


def _refused(said) -> str:
    return json.dumps(_envelope("blocked-escalated", said, "refused"))


@pytest.mark.parametrize("status, out, act_name, expected", [
    (0, _done("G6_E_LOOP_ENROLLED"), "enrol", "G6_E_LOOP_ENROLLED"),
    (0, _done("G6_E_LOOP_RESUMED"), "resume", "G6_E_LOOP_RESUMED"),
    (0, _done("G6_E_LOOP_DEMOTED"), "demote", "G6_E_LOOP_DEMOTED"),
    (1, _refused("G6_E_LOOP_TERMS_CHANGED"), "enrol", "G6_E_LOOP_TERMS_CHANGED"),     # the child's own refusal
    (0, _done("G6_E_LOOP_RESUMED"), "enrol", improve.ACT_FAILED),                    # another act's success
    (0, _done("G6_E_LOOP_STATUS"), "enrol", improve.ACT_FAILED),                     # a qualified draft that is not done
    (0, _refused("G6_E_LOOP_TERMS_CHANGED"), "enrol", improve.ACT_FAILED),           # a refusal with a success status
    (3, _refused("G6_E_LOOP_TERMS_CHANGED"), "enrol", improve.ACT_FAILED),           # a status never seen before
    (None, _done("G6_E_LOOP_ENROLLED"), "enrol", improve.ACT_FAILED),                # no status at all
    (1, json.dumps({"completion_state": "blocked-escalated", "warning_card": {}}), "enrol", improve.ACT_FAILED),
    (1, json.dumps({"completion_state": "verified", "warning_card": {"code": "G6_E_LOOP_ENROLLED"}}), "enrol",
     improve.ACT_FAILED),
    (0, "", "enrol", improve.ACT_FAILED), (0, "[]", "enrol", improve.ACT_FAILED), (0, "not JSON", "enrol", improve.ACT_FAILED),
])
def test_the_one_reader_takes_an_act_only_as_done_or_refused_and_names_anything_else(status, out, act_name, expected):
    got = improve._read_act(status, out + "\n", "", act_name=act_name)
    assert code(got) == expected
    if expected == improve.ACT_FAILED:
        assert got["completion_state"] == "blocked-escalated" and got["exit_status"] == status
        assert f"exit status {status!r}" in got["message"]


#: Every place the act path reads a child process: what it reads there, and the rule that reading follows.
CHILD_READS = {
    ("mcp/improve.py", "_spawn", "Popen"): "starts the runner; its status is read only by _watch",
    ("mcp/improve.py", "_watch", "poll"): "the runner's exit, whatever its status, is START_FAILED: only a heartbeat past "
                                          "opening counts as started",
    ("mcp/improve.py", "_run_act", "Popen"): "starts a person's act",
    ("mcp/improve.py", "_run_act", "communicate"): "the act's output, read only by _read_act",
    ("mcp/improve.py", "_run_act", "returncode"): "the act's exit status, read only by _read_act",
}
#: The modules a person's act and the runner's start pass through.
ACT_PATH = ("mcp/__init__.py", "mcp/improve.py", "mcp/consent.py", "metaharness/runner.py", "metaharness/loop.py",
            "metaharness/surface.py", "metaharness/api.py", "self_maintenance/envelope.py")
_READ_NAMES = {"Popen", "run", "call", "check_call", "check_output", "poll", "communicate", "wait", "returncode",
               "waitpid", "system"}


def _child_reads() -> dict[tuple[str, str, str], ast.AST]:
    """Each child-process start or status read in ``ACT_PATH``, by module, innermost function and name: a
    ``returncode``, a ``poll``, ``communicate`` or ``wait`` call on anything, and a process call on ``subprocess`` or
    ``os``."""
    found: dict[tuple[str, str, str], ast.AST] = {}

    def visit(node: ast.AST, rel: str, owner: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        if isinstance(node, ast.Attribute) and node.attr == "returncode":
            found[(rel, owner, "returncode")] = node
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _READ_NAMES:
            base = node.func.value
            if node.func.attr in ("poll", "communicate", "wait") or (isinstance(base, ast.Name)
                                                                     and base.id in ("subprocess", "os")):
                found[(rel, owner, node.func.attr)] = node
        for child in ast.iter_child_nodes(node):
            visit(child, rel, owner)

    for rel in ACT_PATH:
        visit(ast.parse((SRC / rel).read_text(encoding="utf-8")), rel, "<module>")
    return found


#: Repair 2 (review cons2's finding 1): every place an act's reply is handed on after the one reader made it, and what
#: each does with it. Each passes it on whole, and none reads it.
REPLY_HANDS = {
    ("mcp/improve.py", "_run_act", "_read_act"): "returns what the one reader made of the child's output",
    ("mcp/improve.py", "act", "_run_act"): "returns it whole, with the name of who agreed added",
    ("mcp/__init__.py", "person_act", "improve.act"): "returns it whole to the client",
}


def _reply_hands() -> dict[tuple[str, str, str], bool]:
    """Each call of ``_read_act`` or ``_run_act``, and each use of ``improve.act``, in ``ACT_PATH``, by module, innermost
    function and name, with whether its value reaches a ``return`` unread: through a ``**`` spread, an ``await`` or
    ``asyncio.to_thread`` and nothing else."""
    found: dict[tuple[str, str, str], bool] = {}

    def handed_on(path: list[ast.AST]) -> bool:
        node = path[-1]
        for parent in reversed(path[:-1]):
            if isinstance(parent, ast.Return):
                return True
            spread = isinstance(parent, ast.Dict) and any(k is None and v is node for k, v in zip(parent.keys, parent.values))
            threaded = (isinstance(parent, ast.Call) and ast.unparse(parent.func) == "asyncio.to_thread"
                        and bool(parent.args) and parent.args[0] is node)
            if not (spread or threaded or isinstance(parent, ast.Await)):
                return False
            node = parent
        return False

    def visit(node: ast.AST, rel: str, owner: str, path: list[ast.AST]) -> None:
        path = [*path, node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        name = (getattr(node.func, "id", None) or getattr(node.func, "attr", None) if isinstance(node, ast.Call) else
                ast.unparse(node) if isinstance(node, ast.Attribute) else None)
        if name in ("_read_act", "_run_act", "improve.act"):
            found[(rel, owner, name)] = found.get((rel, owner, name), True) and handed_on(path)
        for child in ast.iter_child_nodes(node):
            visit(child, rel, owner, path)

    for rel in ACT_PATH:
        visit(ast.parse((SRC / rel).read_text(encoding="utf-8")), rel, "<module>", [])
    return found


def test_every_place_the_act_path_reads_a_child_is_listed_and_an_acts_status_goes_through_the_one_reader():
    found = _child_reads()
    assert set(found) == set(CHILD_READS), "a child-process read was added or moved: list it with the rule it follows"
    tree = ast.parse((SRC / "mcp/improve.py").read_text(encoding="utf-8"))
    run_act = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_act")
    readers = [n for n in ast.walk(run_act) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_read_act"]
    assert len(readers) == 1 and any(isinstance(a, ast.Attribute) and a.attr == "returncode" for a in readers[0].args)
    returns = [n for n in ast.walk(run_act) if isinstance(n, ast.Return)]
    assert all(isinstance(r.value, ast.Call) and getattr(r.value.func, "id", "") in ("_read_act", "_blocked")
               for r in returns), "an act's reply reaches its caller only through _read_act, or as a refusal"
    # Repair 2: the reply is read in the one reader and nowhere else. What the child wrote is handed to _read_act and
    # used for nothing else, it is decoded only by _recognised, and every hand it passes through after the reader
    # passes it on whole.
    [talk] = [n for n in ast.walk(run_act) if isinstance(n, ast.Assign) and "communicate(" in ast.unparse(n.value)]
    outputs = {element.id for element in talk.targets[0].elts}
    given = {id(n) for argument in readers[0].args for n in ast.walk(argument)}
    used = [n for n in ast.walk(run_act) if isinstance(n, ast.Name) and n.id in outputs and isinstance(n.ctx, ast.Load)]
    assert used and all(id(n) in given for n in used), "a child's output is read outside the one reader"
    decoders = [owner for owner, node in _functions(SRC / "mcp/improve.py").items() for call in ast.walk(node)
                if isinstance(call, ast.Call) and ast.unparse(call.func) == "json.loads"]
    assert decoders == ["_recognised"], "a child's reply is decoded outside the one reader"
    hands = _reply_hands()
    assert set(hands) == set(REPLY_HANDS), "an act's reply is handed somewhere new: list it with what it does there"
    assert all(hands.values()), f"an act's reply is read outside the one reader: {[k for k, ok in hands.items() if not ok]}"


def test_what_the_reader_takes_as_done_is_what_each_act_replies_when_done():
    from g6.metaharness import loop, shadow

    assert improve.ACT_DONE == {"enrol": loop.ENROLLED, "resume": loop.RESUMED, "demote": shadow.DEMOTED}


@pytest.mark.parametrize("status", [0, 1, -9])
def test_a_runner_that_ended_is_never_a_start_whatever_its_status(tmp_path, status):
    class Ended:
        def poll(self):
            return status

    assert code(improve._watch(tmp_path, Ended(), datetime.now(UTC))) == improve.START_FAILED


# Findings 2, 3, 4 and 6, check_then_act_race: one binding from what is shown to what the act does ---------------------

@pytest.mark.parametrize("act_name", ["resume", "demote"])
def test_a_delayed_act_runs_only_the_domain_bound_to_its_own_dialog(rig, monkeypatch, act_name):
    """Finding 2 (review cons1). Failed on fc80f8e: the old resume and demote ran the newly enrolled domain, and made its
    marker, before refusing ``G6_E_LOOP_TERMS_CHANGED``."""
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    _paused(rig) if act_name == "resume" else _adopted(rig)
    marker = rig.tmp / "new-domain-executed"

    def concurrent_enrolment_then_delayed_act(root, settings, request):
        # Another person enrols a new file through the same real handler, after this act passed its server-side check.
        rig.domain.write_text(rig.source + f"\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
        with monkeypatch.context() as patch:
            patch.setattr(improve, "_run_act", lambda root, settings, req: runner.act(req))
            assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
        marker.unlink()                                # that run belonged to the second dialog
        return runner.act(request)

    monkeypatch.setattr(improve, "_run_act", concurrent_enrolment_then_delayed_act)
    reply = rig.call(f"g6_improve_{act_name}", **({"generation": 1} if act_name == "demote" else {}))
    assert code(reply) == improve.TERMS_CHANGED, reply
    assert not marker.exists(), "the old act ran the newly enrolled domain before refusing TERMS_CHANGED"


def test_an_enrolment_binds_the_digest_its_dialog_shows_even_when_an_edit_is_reverted(rig, monkeypatch):
    """Finding 3 (review cons1). Failed on fc80f8e: the dialog showed revision B's digest and revision A was enrolled."""
    displayed_source = rig.source + "\n# the revision displayed to the person\n"
    displayed_digest = hashlib.sha256(displayed_source.encode("utf-8")).hexdigest()
    original_terms, first = runner.person_terms, [True]

    def edit_between_terms_and_question(*args, **kwargs):
        terms = original_terms(*args, **kwargs)
        if first[0]:
            first[0] = False
            rig.domain.write_text(displayed_source, encoding="utf-8", newline="")
        return terms

    monkeypatch.setattr(runner, "person_terms", edit_between_terms_and_question)

    def agree_to_displayed_revision(message):
        assert displayed_digest in message
        rig.domain.write_text(rig.source, encoding="utf-8")       # the editor restores the older revision
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    reply = rig.call("g6_improve_enrol", handler=agree_to_displayed_revision)
    recorded = rig.log().enrolled() if (rig.root / CYCLE_LOG).is_file() else None
    assert code(reply) == improve.TERMS_CHANGED and recorded is None, (
        f"displayed {displayed_digest}, but enrolled {recorded['frozen']['domain_file'] if recorded else None}")


def test_a_start_reads_its_binding_again_holding_the_folders_lock(rig, monkeypatch):
    """Finding 4 (review cons1). Failed on fc80f8e: a start recorded Ada's consent and spawned after its enrolled domain
    changed between its check and the lock."""
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    original_terms, reads = improve._terms, [0]

    def enrol_after_start_checks_terms(*args, **kwargs):
        terms = original_terms(*args, **kwargs)
        reads[0] += 1
        if reads[0] == 2:                             # the first read built the dialog; this is the start's own check
            rig.domain.write_text(rig.source + "\n# a different enrolled revision\n", encoding="utf-8")
            assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
        return terms

    monkeypatch.setattr(improve, "_terms", enrol_after_start_checks_terms)
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *args: spawned.append(args) or object())
    monkeypatch.setattr(improve, "_watch", lambda *args: _envelope("qualified-draft", improve.STARTED, "fake runner"))
    reply = rig.call("g6_improve_start")
    assert rig.log().started() is None and not spawned and code(reply) == improve.TERMS_CHANGED, (
        "start recorded consent and spawned after its enrolled domain changed between the check and the mutex")


def _workflow(tmp_path: Path, monkeypatch) -> tuple[Surface, str]:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    surface = Surface(workflow_root=tmp_path / "workflows")
    shown = surface.start({
        "name": "review", "task": "Label {input}",
        "cases": [{"input": "a", "expected": "x"}, {"input": "b", "expected": "y"}],
        "baseline_code": {"main.py": "def solve(task):\n    return 'unknown'\n"},
    })["workflow_digest"]
    return surface, shown


def test_a_workflow_confirmation_records_only_the_digest_its_dialog_showed(tmp_path, monkeypatch):
    """Finding 6 (review cons1). Failed on fc80f8e: the replacement baseline's digest was confirmed under the person's
    agreement to the original."""
    surface, shown = _workflow(tmp_path, monkeypatch)
    held = surface.harness("review")
    original_confirm = held.confirm

    def edit_after_final_digest_check(*args, **kwargs):
        folder = tmp_path / "workflows" / "review"
        spec = json.loads((folder / "workflow.json").read_text(encoding="utf-8"))
        spec["baseline_code"]["main.py"] = "def solve(task):\n    return 'a different program'\n"
        (folder / "code" / "main.py").write_text(spec["baseline_code"]["main.py"], encoding="utf-8")
        (folder / "workflow.json").write_text(json.dumps(spec), encoding="utf-8")
        return original_confirm(*args, **kwargs)

    monkeypatch.setattr(held, "confirm", edit_after_final_digest_check)
    server = Server()
    _metaharness_tools(server, surface, object)

    async def accept_displayed_digest(**kwargs):
        assert shown in kwargs["message"]
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=accept_displayed_digest), request_id="binding")
    reply = asyncio.run(server.tools["metaharness_confirm"]("review", shown, ctx))
    recorded = held.store.confirmed()
    assert code(reply) == "G6_E_META_WORKFLOW_CHANGED" and recorded is None, (
        f"confirmed {recorded['workflow_digest'] if recorded else None} after the person agreed to {shown}")


def test_an_agreed_confirmation_records_the_digest_the_person_read(tmp_path, monkeypatch):
    surface, shown = _workflow(tmp_path, monkeypatch)
    server = Server()
    _metaharness_tools(server, surface, object)

    async def accept(**kwargs):
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=accept), request_id="binding")
    assert code(asyncio.run(server.tools["metaharness_confirm"]("review", shown, ctx))) == "G6_E_META_CONFIRMED"
    assert surface.harness("review").store.confirmed()["workflow_digest"] == shown


def test_an_enrolment_records_the_bytes_its_binding_names(rig):
    root, _, bound = improve.asked_act("enrol", str(rig.root))
    assert code(improve.act("enrol", root, "Ada", bound)) == "G6_E_LOOP_ENROLLED"
    assert rig.log().enrolled()["frozen"]["domain_file"] == bound[runner.FILE_SHA] == file_digest(rig.domain)


@pytest.mark.parametrize("shown", ["a digest of the old form", None, {}, {"Folder": "elsewhere"}])
def test_a_binding_that_is_not_the_one_read_runs_and_records_nothing(rig, shown):
    marker = rig.tmp / "ran"
    rig.domain.write_text(rig.source + f"\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    got = improve.act("enrol", improve._root(str(rig.root)), "Ada", shown)
    assert code(got) == improve.TERMS_CHANGED and not marker.exists() and not (rig.root / CYCLE_LOG).is_file()


def test_records_that_stop_reading_between_the_check_and_the_lock_run_and_record_nothing(rig, monkeypatch):
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    _paused(rig)
    marker = rig.tmp / "ran"

    def broken_then_act(root, settings, request):
        rig.domain.write_text(rig.source + f"\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
        path = rig.root / CYCLE_LOG
        path.write_text(path.read_text(encoding="utf-8").replace('"Ada"', '"Cy"'), encoding="utf-8")
        return runner.act(request)

    monkeypatch.setattr(improve, "_run_act", broken_then_act)
    got = rig.call("g6_improve_resume")
    assert code(got) == improve.TERMS_CHANGED and not marker.exists()


def test_an_act_runs_and_records_nothing_while_another_holds_the_folders_lock(rig):
    from g6.metaharness.loop import folder_lock

    root, _, bound = improve.asked_act("enrol", str(rig.root))
    marker = rig.tmp / "ran"
    holding, release, replies = threading.Event(), threading.Event(), []

    def hold():
        with folder_lock(root):
            holding.set()
            release.wait(30)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        holding.wait(30)
        rig.domain.write_text(rig.source + f"\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
        replies.append(runner.act({"act": "enrol", "folder": str(root), "who": "Ada", "binding": bound}))
    finally:
        release.set()
        holder.join(30)
    assert code(replies[0]) == "G6_E_LOOP_BUSY" and not marker.exists()
    with folder_lock(root), folder_lock(root):           # one thread takes it once, however often it asks
        pass


# The inventory, built from the code: every act that asks through g6.mcp.consent ----------------------------------------

#: Each tool that asks a person through ``g6.mcp.consent``: the binding its dialog is built from, the binding it passes to
#: the act, and where the act reads it again holding a lock, immediately before it records or runs anything.
CONSENT_ACTS = {
    "g6_improve_enrol": ("runner.person_terms('enrol')", "improve.act(shown) -> request['binding']",
                         "runner.act: person_terms again under folder_lock, before open_loop runs the bound bytes"),
    "g6_improve_resume": ("runner.person_terms('resume')", "improve.act(shown) -> request['binding']",
                          "runner.act: person_terms again under folder_lock, before open_loop runs the bound bytes"),
    "g6_improve_demote": ("runner.person_terms('demote')", "improve.act(shown) -> request['binding']",
                          "runner.act: person_terms again under folder_lock, before open_loop runs the bound bytes"),
    "g6_improve_start": ("improve._terms", "improve.start(shown)",
                         "improve.start: _terms again under the folder's .tick mutex, before CycleLog.start"),
    "metaharness_confirm": ("the workflow digest, which covers the task and case counts shown",
                            "Surface.confirm(workflow_digest)",
                            "MetaHarness.confirm(workflow_digest=): under the workspace's mutex, before any write"),
    "metaharness_approve": ("Surface.approval_check: the decision digest and what it names", "decision_digest",
                            "Surface.approve: _approvable again under the workspace's mutex; activate revalidates"),
    "metaharness_rollback": ("Surface.rollback_check: workflow digest, store head, profile, version", "binding",
                             "Surface.rollback: rollback_check again under the workspace's mutex, compared"),
    "g6_mission_create": ("mission.execution: the revision's lines", "lines",
                          "g6.workforce.api.allow_execution; outside this change, its lock not examined here"),
    "g6_mission_confirm": ("mission.asked: the standard's lines and digest", "lines, sha",
                           "mission.confirm: the digest, then g6.workforce.api.confirm; its lock not examined here"),
}


def _functions(path: Path) -> dict[str, ast.AST]:
    return {n.name: n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)
            and (getattr(n.func, "id", None) == name or getattr(n.func, "attr", None) == name)]


def _consent_tools() -> set[str]:
    """The registered tools that ask a person: those that call ``request_consent``, and those that call one that does."""
    functions = _functions(SRC / "mcp" / "__init__.py")
    functions.pop("_metaharness_tools")                    # it holds them all: its own body asks nobody
    asking = {name for name, node in functions.items() if _calls(node, "request_consent")}
    via = {name for name, node in functions.items() if name not in asking and any(_calls(node, a) for a in asking)}
    return (asking | via) - {"person_act"}


def _inside_with(function: ast.AST, lock: str) -> list[ast.With]:
    return [n for n in ast.walk(function) if isinstance(n, ast.With)
            and any(lock in ast.unparse(item.context_expr) for item in n.items)]


def test_every_act_that_asks_through_consent_is_in_the_inventory():
    assert _consent_tools() == set(CONSENT_ACTS), "a tool that asks a person was added: list its binding and recheck"


def _handed(function: ast.AST, target: str) -> list[ast.Call]:
    """The ``asyncio.to_thread`` calls in ``function`` that run ``target``."""
    return [c for c in _calls(function, "to_thread") if c.args and ast.unparse(c.args[0]) == target]


def test_each_loop_act_builds_its_dialog_from_its_binding_and_passes_that_binding_on():
    mcp, imp = _functions(SRC / "mcp" / "__init__.py"), _functions(SRC / "mcp" / "improve.py")
    [start] = _handed(mcp["g6_improve_start"], "improve.start")
    [acted] = _handed(mcp["person_act"], "improve.act")
    assert ast.unparse(start.args[3]) == "shown" and ast.unparse(acted.args[4]) == "shown"
    for tool, asked in (("g6_improve_start", "improve.asked"), ("person_act", "improve.asked_act")):
        assert _calls(mcp[tool], asked.split(".")[1]), f"{tool} does not take its binding from {asked}"
    for name, binding in (("asked", "bound"), ("asked_act", "bound")):
        [built] = _calls(imp[name], "_question")
        assert ast.unparse(built.args[1]) == binding, f"{name} writes its dialog from something other than its binding"
        returned = [n for n in ast.walk(imp[name]) if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)]
        assert [ast.unparse(r.value.elts[2]) for r in returned] == [binding]
    [built] = _calls(mcp["metaharness_confirm"], "question")
    [confirmed] = [c for c in _calls(mcp["metaharness_confirm"], "confirm")]
    assert ast.unparse(built.args[1]) == "bound" and "bound[" in ast.unparse(confirmed.args[1])


def test_each_loop_act_reads_its_binding_again_holding_the_lock_before_it_records_or_runs():
    imp, run = _functions(SRC / "mcp" / "improve.py"), _functions(SRC / "metaharness" / "runner.py")
    [held] = _inside_with(imp["start"], "mutex")
    body = ast.unparse(held)
    assert body.index("_terms(root) != bound") < body.index(".start(")
    assert "settings=bound[SETTINGS_DIGEST]" in body and "stops=bound[STOPS_COUNTED]" in body
    [held] = _inside_with(run["act"], "folder_lock")
    body = ast.unparse(held)
    assert body.index("refused = unchanged()") < body.index("open_loop(") < body.index("loop.enrol(")
    assert "pinned=bound[FILE_SHA]" in body and "_pinned(" not in ast.unparse(run["act"])
    confirm = _functions(SRC / "metaharness" / "api.py")["confirm"]
    text = ast.unparse(confirm)
    assert "_serialized" in [ast.unparse(d) for d in confirm.decorator_list]
    assert text.index("digest_now != workflow_digest") < text.index("add_vault") < text.index("self.store.confirm(")


# Finding 5, undisclosed consent identity: a dialog shows every field its act binds --------------------------------------

def _shown(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


@pytest.mark.parametrize("act_name", ["resume", "demote"])
def test_the_dialog_shows_the_domain_digest_before_authorizing_execution(rig, act_name):
    """Finding 5 (review cons1). Failed on fc80f8e: the resume and demote dialogs omitted the domain file's SHA-256."""
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    _paused(rig) if act_name == "resume" else _adopted(rig)
    expected_digest = file_digest(rig.domain)
    reply = rig.call(f"g6_improve_{act_name}", handler=declined, **({"generation": 1} if act_name == "demote" else {}))
    assert code(reply) == "G6_E_CONSENT_DECLINED"
    assert expected_digest in rig.questions[-1], f"{act_name} dialog omitted the domain file's SHA-256"


@pytest.mark.parametrize("act_name", ["enrol", "resume", "demote", "start"])
def test_every_field_an_act_binds_appears_in_its_dialog(rig, act_name):
    if act_name != "enrol":
        assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    if act_name == "resume":
        _paused(rig)
    if act_name == "demote":
        _adopted(rig)
    if act_name == "start":
        _paused(rig)
        improve.stop(str(rig.root), "the owner's review")        # a STOP file the start would lift, and a count
    found = improve.asked(str(rig.root)) if act_name == "start" else improve.asked_act(
        act_name, str(rig.root), 1 if act_name == "demote" else None)
    assert not isinstance(found, dict), found
    _, question, bound = found
    assert len(bound) >= 5
    missing = {name: value for name, value in bound.items() if f"{name}: {_shown(value)}" not in question}
    assert missing == {}, f"{act_name} binds what its dialog does not show"
    if act_name == "start":
        assert {"STOP file SHA-256", "Stop now in place", "Paused because", "Python file SHA-256"} <= set(bound)


def test_the_workflow_confirmation_shows_every_field_it_binds(tmp_path, monkeypatch):
    surface, shown = _workflow(tmp_path, monkeypatch)
    held = surface.harness("review")
    server, asked = Server(), []
    _metaharness_tools(server, surface, object)

    async def decline(**kwargs):
        asked.append(kwargs["message"])
        return SimpleNamespace(action="decline", content=None)

    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=decline), request_id="binding")
    assert code(asyncio.run(server.tools["metaharness_confirm"]("review", shown, ctx))) == "G6_E_CONSENT_DECLINED"
    for field in ("Workflow: review", f"Workflow digest: {shown}", f"Task: {held.workflow.task}",
                  f"Development cases: {len(held.workflow.suite.dev)}",
                  f"Held-out cases: {len(held.workflow.suite.held_out)}"):
        assert field in asked[0]
