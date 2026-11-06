# SPDX-License-Identifier: Apache-2.0
"""What a person is shown is what runs, repair 2 of the consent change: review cons2's two findings and the runner's
binding, each brought in as a test of this file and failing on 7670b97, and the inventories that remove their classes at
the root, built from the code:

- failure_read_as_success (finding 1): the one reader of a child's act recognises a reply by its shape alone
  (``improve.REPLY_SHAPE``) and takes anything else, of any type, as ``G6_E_LOOP_ACT_FAILED`` naming what it found,
  without raising; nothing outside it reads the reply;
- one_fact_two_rules (finding 2): what taking the head back restores is decided in one place
  (``shadow._destination``, read by ``shadow.restores``), which the dialog's binding names by generation and digest and
  the act checks, under the folder's lock, against what it would write;
- check_then_act_race (the runner's binding): a start records the enrolment its dialog showed, and the runner it spawns
  runs only that one: any other on record, at the run's start or before a tick, ends the start
  (``G6_E_LOOP_ENROLMENT_CHANGED``) and leaves a stop the status shows.

FakeLLM only: no model, no runner process, no network. Every act and every run is in this process.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.mcp import improve
from g6.metaharness import monitor, runner
from g6.metaharness.api import _envelope
from g6.metaharness.shadow import lineage
from g6.self_maintenance.envelope import heartbeat

_spec = importlib.util.spec_from_file_location("repair2_binding_helpers", Path(__file__).with_name("test_consent_binding.py"))
binding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(binding)
Rig, code, START, SRC = binding.Rig, binding.code, binding.START, binding.SRC
ENROLMENT_CHANGED = "G6_E_LOOP_ENROLMENT_CHANGED"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


# Finding 1, failure_read_as_success: the one reader recognises a reply by its shape, and never raises -----------------

class _Child:
    """A child that exited ``returncode`` having written ``out``: no process is started."""

    def __init__(self, returncode, out: str) -> None:
        self.returncode, self.out = returncode, out

    def communicate(self, request, timeout):
        return self.out, ""


def test_a_child_reply_whose_card_code_is_a_list_is_an_act_failure_and_not_a_raise(tmp_path, monkeypatch):
    """Finding 1 (review cons2). Failed on 7670b97: {'raised': "TypeError: unhashable type: 'list'"}; expected
    G6_E_LOOP_ACT_FAILED."""
    child = _Child(0, json.dumps({"completion_state": "qualified-draft", "warning_card": {"code": []}}) + "\n")
    monkeypatch.setattr(improve.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(improve, "_interpreter", lambda settings: ["fake-python"])
    try:
        reply = improve._run_act(tmp_path, {}, {"act": "enrol"})
    except Exception as exc:  # noqa: BLE001 - what the finding saw
        reply = {"raised": f"{type(exc).__name__}: {exc}"}
    assert reply.get("warning_card", {}).get("code") == improve.ACT_FAILED, reply
    assert "warning_card.code is a list" in reply["message"]


DONE = json.loads(json.dumps(_envelope("qualified-draft", "G6_E_LOOP_ENROLLED", "enrolled", next_action="loop.tick()")))
REFUSED = json.loads(json.dumps(_envelope("blocked-escalated", "G6_E_LOOP_TERMS_CHANGED", "refused", next_action="ask")))
#: A value of each JSON type, and how the reader names it.
VALUES = [(None, "null"), (True, "a boolean"), (7, "a number"), (2.5, "a number"), ("text", "a string"), (["x"], "a list"),
          ({"x": 1}, "an object")]
#: Each field of an act's reply that the reader vouches for, and the one JSON type it may hold: the cross-surface
#: envelope's fields and the card's, as g6.metaharness.api._envelope writes them.
FIELDS = {"completion_state": "a string", "reliability_label": "a string", "warning_card": "an object", "code": "a string",
          "message": "a string", "next_action": "a string", "evidence": "a list", "request_id": "a string",
          "run_id": "a string", "warning_card.code": "a string", "warning_card.message": "a string",
          "warning_card.suggested_action": "a string"}


def _with(reply: dict, field: str, value) -> dict:
    changed = json.loads(json.dumps(reply))
    if field.startswith("warning_card."):
        changed["warning_card"][field.split(".", 1)[1]] = value
    else:
        changed[field] = value
    return changed


def _without(reply: dict, field: str) -> dict:
    changed = json.loads(json.dumps(reply))
    (changed["warning_card"] if field.startswith("warning_card.") else changed).pop(field.split(".")[-1])
    return changed


def _read(status, out) -> dict:
    try:
        return improve._read_act(status, out, "", act_name="enrol")
    except Exception as exc:  # noqa: BLE001 - the reader must never raise
        pytest.fail(f"the reader raised {type(exc).__name__}: {exc}")


def test_the_fields_the_reader_vouches_for_are_the_fields_an_act_writes():
    assert set(improve.REPLY_SHAPE) == {name for name in FIELDS if "." not in name} and set(improve.REPLY_SHAPE) <= set(DONE)
    assert set(improve.CARD_SHAPE) == {name.split(".")[1] for name in FIELDS if "." in name} <= set(DONE["warning_card"])
    assert code(_read(0, json.dumps(DONE))) == "G6_E_LOOP_ENROLLED"           # the real envelope, done and refused
    assert code(_read(1, json.dumps(REFUSED))) == "G6_E_LOOP_TERMS_CHANGED"


@pytest.mark.parametrize("field, value, kind", [(field, value, kind) for field in sorted(FIELDS) for value, kind in VALUES
                                                if kind != FIELDS[field]])
@pytest.mark.parametrize("status, base", [(0, DONE), (1, REFUSED)])
def test_each_field_of_the_reply_at_each_wrong_type_is_an_act_failure_naming_it(field, value, kind, status, base):
    got = _read(status, json.dumps(_with(base, field, value)) + "\n")
    assert code(got) == improve.ACT_FAILED and got["completion_state"] == "blocked-escalated"
    assert f"{field} is {kind}" in got["message"], got["message"]


@pytest.mark.parametrize("field", ["completion_state", "warning_card", "warning_card.code"])
@pytest.mark.parametrize("status, base", [(0, DONE), (1, REFUSED)])
def test_a_reply_missing_a_field_the_reader_needs_is_an_act_failure_naming_it(field, status, base):
    got = _read(status, json.dumps(_without(base, field)))
    assert code(got) == improve.ACT_FAILED and "no " + field.split(".")[-1] in got["message"], got["message"]


@pytest.mark.parametrize("out, said", [
    ("[]", "a reply that is a list, not an object"), ("null", "a reply that is null, not an object"),
    ('"G6_E_LOOP_ENROLLED"', "a reply that is a string, not an object"), ("7", "a reply that is a number, not an object"),
    ("true", "a reply that is a boolean, not an object"),
    (json.dumps({**DONE, "warning_card": "G6_E_LOOP_ENROLLED"}), "warning_card is a string"),   # a card that is no card
    (json.dumps({**DONE, "warning_card": ["G6_E_LOOP_ENROLLED"]}), "warning_card is a list"),
    (json.dumps({**DONE, "warning_card": None}), "warning_card is null"),
])
def test_a_reply_that_is_not_an_object_or_whose_card_is_no_card_is_an_act_failure(out, said):
    got = _read(0, out)
    assert code(got) == improve.ACT_FAILED and said in got["message"], got["message"]


@pytest.mark.parametrize("out", [
    "[" * 100_000 + "]" * 100_000,                                       # deeper than any decoder goes
    json.dumps({"reply": DONE}),                                         # the envelope, one level down
    json.dumps({**DONE, "code": "G6_E_LOOP_RESUMED"}),                   # two codes that disagree
    json.dumps({**DONE, "reliability_label": "verified"}),               # two states that disagree
    json.dumps({**DONE, "warning_card": {**DONE["warning_card"], "code": {"G6_E_LOOP_ENROLLED": True}}}),
    "﻿" + json.dumps(DONE)[:-1],                                    # cut short
])
def test_a_reply_shape_never_seen_is_an_act_failure_and_not_a_raise(out):
    assert code(_read(0, out)) == improve.ACT_FAILED


@pytest.mark.parametrize("status", [True, False, 0.0, 1.0, "0", [0], None, -1])
def test_an_exit_status_of_any_other_type_or_value_is_never_the_act_done(status):
    assert code(_read(status, json.dumps(DONE))) == improve.ACT_FAILED
    assert code(_read(status, json.dumps(REFUSED))) == improve.ACT_FAILED


@pytest.mark.parametrize("out", [None, b'{"completion_state": "qualified-draft"}', 7, ["x"]])
def test_output_that_is_not_text_is_an_act_failure_and_not_a_raise(out):
    assert code(_read(0, out)) == improve.ACT_FAILED


# Finding 2, one_fact_two_rules: what a demotion restores is decided in one place --------------------------------------

def _two_step_domain(rig) -> None:
    """The rig's domain, made to adopt twice in a row with a deterministic FakeLLM (as review cons2 built it)."""
    source = rig.source.replace("WordProblems(Path(__file__).parent)", "WordProblems(Path(__file__).parent, two_steps=True)")
    source = source.replace("LOOP =", '''
import json
from g6.core.llm import FakeLLM
def reads_the_base(system, user):
    change = {"prompt": domains.WORKING}
    return json.dumps(change if "step by step" not in user else {**change, "skill_text": domains.UNITS})
LOOP =''')
    rig.source = source.replace('domains.proposes({"prompt": domains.WORKING})', "FakeLLM(reads_the_base)")
    rig.domain.write_text(rig.source, encoding="utf-8")


def _adopting(rig, adoptions: int):
    """An enrolled folder with ``adoptions`` generations adopted, and the digest of generation 0's profile."""
    _two_step_domain(rig)
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    loop, _ = runner.open_loop(rig.root, now=lambda: START + timedelta(days=2))
    loop._clock = loop.mh._clock = loop.domain.__class__.__init__.__globals__["Clock"]()
    first = loop.mh._profile_at(0).digest()
    for _ in range(adoptions):
        assert loop.tick()["outcome"] == "adopted"
    return loop, first


def _monitor_rolls_back(loop, monkeypatch) -> None:
    """The monitor's own rollback of the head, through ShadowHarness.audit, with its drift test made to say so."""
    monkeypatch.setattr(monitor, "assess", lambda differences, alpha: SimpleNamespace(
        pairs=5, mean_difference=1.0, threshold=0.5, demote=True))
    assert code(loop.mh.audit(loop.cut.monitor[:1], count=1)) == "G6_E_LOOP_ROLLED_BACK"


def _line(message: str, label: str) -> str | None:
    return next((line.split(": ", 1)[1] for line in message.splitlines() if line.startswith(label + ": ")), None)


def _demoted(rig, generation: int) -> tuple[dict, dict | None]:
    """A person's demotion of ``generation`` through the real handler: its reply, and what its dialog showed it goes
    back to, or ``None`` when no dialog was shown."""
    asked = len(rig.questions)
    reply = rig.call("g6_improve_demote", generation=generation)
    if len(rig.questions) == asked:
        return reply, None
    message = rig.questions[-1]
    return reply, {"generation": _line(message, "Goes back to generation"),
                   "profile": _line(message, "Profile it goes back to (SHA-256)")}


def test_a_demotion_after_a_demotion_shows_the_generation_it_restores(rig):
    """Finding 2 (review cons2), as review cons2 wrote it. Failed on 7670b97: dialog promised generation [2], act
    restored generation 0."""
    _adopting(rig, 2)
    # 0 -> adoption 1 -> adoption 2 -> demotion 3 (restoring adoption 1).
    assert code(rig.call("g6_improve_demote", generation=2)) == "G6_E_LOOP_DEMOTED"
    assert lineage(rig.root / "workspace")["recent"][-1]["restores"] == 1
    shown = []

    def agree(message):
        for line in message.splitlines():
            if line.startswith("Goes back to generation:"):
                shown.append(int(line.split(":", 1)[1]))
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    assert code(rig.call("g6_improve_demote", generation=3, handler=agree)) == "G6_E_LOOP_DEMOTED"
    actual = lineage(rig.root / "workspace")["recent"][-1]["restores"]
    assert shown == [actual], f"dialog promised generation {shown}, act restored generation {actual}"


@pytest.mark.parametrize("chain, restored", [
    ("no adoption", [None]), ("one adoption", [0]), ("two adoptions", [1]), ("after a rollback", [0]),
    ("after a demotion", [1, 0]), ("after two demotions in a row", [1, 0, None]),
])
def test_each_demotion_restores_the_generation_and_profile_its_dialog_shows(rig, monkeypatch, chain, restored):
    """Finding 2's class. Failed on 7670b97: no dialog named the profile restored, and after a demotion or a rollback
    the dialog named the head row's parent, not the generation restored."""
    loop, first = _adopting(rig, 0 if chain == "no adoption" else 1 if chain == "one adoption" else 2)
    if chain == "after a rollback":
        _monitor_rolls_back(loop, monkeypatch)
    for expected in restored:
        before = lineage(rig.root / "workspace")
        reply, shown = _demoted(rig, before["current"])
        if expected is None:                          # nothing adopted stands: nothing is asked, nothing written
            assert shown is None and code(reply) == "G6_E_LOOP_NOT_DEMOTABLE", reply
            assert lineage(rig.root / "workspace") == before
            continue
        assert code(reply) == "G6_E_LOOP_DEMOTED", reply
        row = lineage(rig.root / "workspace")["recent"][-1]
        profile = first if expected == 0 else next(g["profile_digest"] for g in lineage(rig.root / "workspace", last=99)
                                                   ["recent"] if g["generation"] == expected)
        assert row["restores"] == expected and row["profile_digest"] == profile
        assert shown == {"generation": str(expected), "profile": profile}, (chain, shown, row)


@pytest.mark.parametrize("wrong", ["generation", "profile_digest", "undoes"])
def test_a_demotion_whose_restore_is_not_the_one_bound_refuses_holding_the_lock_and_writes_nothing(rig, monkeypatch, wrong):
    """The act checks what it would write against the binding, not only the binding against the files: here both reads
    of the binding agree with each other and not with the chain. Failed on 7670b97: no binding named what is restored."""
    _adopting(rig, 2)
    real = runner.restores

    def misread(workspace):
        found = real(workspace)
        return {**found, wrong: {"generation": 0, "profile_digest": "0" * 64, "undoes": 1}[wrong]}

    monkeypatch.setattr(runner, "restores", misread)
    before = lineage(rig.root / "workspace")
    reply = rig.call("g6_improve_demote", generation=2)
    assert code(reply) == improve.TERMS_CHANGED and lineage(rig.root / "workspace") == before, reply


def test_the_destination_is_decided_in_one_place_that_the_binding_and_the_act_both_read(rig):
    """The inventory, built from the code (one_fact_two_rules): every read of what a chain's rows say about where a
    rollback goes, and every call of the rule, in the loop's modules, is listed with the rule it follows."""
    assert _destination_reads() == set(DESTINATION_READS), "a read of where a rollback goes was added or moved: list it"
    functions = binding._functions(SRC / "metaharness" / "runner.py")
    assert binding._calls(functions["person_terms"], "restores"), "the dialog's binding does not take the rule's answer"
    shadow_functions = binding._functions(SRC / "metaharness" / "shadow.py")
    assert binding._calls(shadow_functions["demote"], "_destination") and binding._calls(shadow_functions["restores"],
                                                                                        "_destination")
    assert set(runner.RESTORING) == {"generation", "profile_digest", "undoes"}


#: Every read of a rollback's destination in a chain's rows, and every call of the rule that decides it, in the loop's
#: modules: (module, function, what is read or called) and the rule that read follows.
DESTINATION_READS = {
    ("metaharness/shadow.py", "_adopted", "target_version"): "the rule: walks a rollback back to the adoption it restored",
    ("metaharness/shadow.py", "_destination", "_adopted"): "the rule: the adoption the head stands for is undone",
    ("metaharness/shadow.py", "_destination", "base_version"): "the rule: the generation that adoption was made on returns",
    ("metaharness/shadow.py", "restores", "_destination"): "what the dialog's binding names, read from the files",
    ("metaharness/shadow.py", "_generation", "base_version"): "renders a row's own parent for the lineage; decides nothing",
    ("metaharness/shadow.py", "_generation", "target_version"): "renders what a rollback row restored; decides nothing",
    ("metaharness/shadow.py", "_generation", "undoes"): "renders what a rollback row undid; decides nothing",
    ("metaharness/shadow.py", "_rules", "_destination"): "the chain refuses a rollback row that is not the rule's",
    ("metaharness/shadow.py", "_rules", "target_version"): "the row checked against the rule",
    ("metaharness/shadow.py", "audit", "_adopted"): "the adoption the monitor audits, its profile and number",
    ("metaharness/shadow.py", "audit", "_destination"): "where the monitor's rollback goes",
    ("metaharness/shadow.py", "demote", "_destination"): "where a person's demotion goes, checked by its last_check",
    ("metaharness/shadow.py", "demote", "undoes"): "the rule's own answer, written into the demotion's row",
    ("self_maintenance/digest.py", "_generation", "parent"): "the daily page shows each row's parent; decides nothing",
    ("metaharness/runner.py", "person_terms", "restores"): "the dialog's binding: the rule's answer, by generation and digest",
    ("mcp/improve.py", "_cannot_act", "restores"): "nothing is asked when the rule finds nothing to take back",
    ("metaharness/loop.py", "demote", "undoes"): "logs what the harness's reply says it undid",
    ("metaharness/loop.py", "_cycle", "undoes"): "logs what the monitor's reply says it undid",
    ("metaharness/store.py", "_resolve", "target_version"): "a person's profile store, another chain with its own rule",
    ("metaharness/store.py", "_store_rules", "target_version"): "a person's profile store, another chain with its own rule",
}
_KEYS = {"base_version", "target_version", "parent", "restores", "undoes"}
_RULES = {"_adopted", "_destination", "restores"}


def _destination_reads() -> set[tuple[str, str, str]]:
    """Each read, in ``metaharness``, ``mcp`` and ``self_maintenance``, of a key that says where a rollback goes (a
    subscript or a ``.get`` of it), and each call of the rule, by module, innermost function and name."""
    found: set[tuple[str, str, str]] = set()

    def visit(node: ast.AST, rel: str, owner: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and isinstance(node.slice, ast.Constant)
                and node.slice.value in _KEYS):
            found.add((rel, owner, node.slice.value))
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "get" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in _KEYS:
                found.add((rel, owner, node.args[0].value))
            if name in _RULES:
                found.add((rel, owner, name))
        for child in ast.iter_child_nodes(node):
            visit(child, rel, owner)

    for folder in ("metaharness", "mcp", "self_maintenance"):
        for path in sorted((SRC / folder).rglob("*.py")):
            rel = path.relative_to(SRC).as_posix()
            visit(ast.parse(path.read_text(encoding="utf-8")), rel, "<module>")
    return found


# The runner's binding, check_then_act_race: a start's runner runs only the enrolment the start showed ------------------

def _then_stop(root: Path):
    def sleep(seconds: float) -> None:
        (root / "STOP").write_text("the test is over\n", encoding="utf-8")
    return sleep


def _run(rig, sleep=None) -> int:
    """The runner the start spawns, here in this process, on a clock two days on, until a stop."""
    return runner.run(rig.root, now=lambda: START + timedelta(days=2), sleep=sleep or _then_stop(rig.root))


def _enrol_again(rig, how: str) -> None:
    """Another enrolment through the real handler: of the same file (later) or of an edited one (changed)."""
    if how == "changed":
        rig.domain.write_text(rig.source + "\n# a changed revision\n", encoding="utf-8")
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"


def _started(rig, monkeypatch, before_spawn=lambda: None) -> list[int]:
    """A person's start through the real handler. Its spawn runs ``before_spawn`` and then the runner, in this process,
    and returns what it exited with."""
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    exits: list[int] = []

    def spawn(root, settings):
        before_spawn()
        exits.append(_run(rig))
        return SimpleNamespace(poll=lambda: exits[-1])

    monkeypatch.setattr(improve, "_spawn", spawn)
    monkeypatch.setattr(improve, "_watch", lambda root, process, since: _envelope("qualified-draft", improve.STARTED,
                                                                                  "the runner ran in this process"))
    assert code(rig.call("g6_improve_start")) == improve.STARTED
    return exits


def _ran(rig) -> int:
    return len(rig.log().cycles())


def _status_stop(rig) -> str:
    return improve.status(str(rig.root))["stop"]


@pytest.mark.parametrize("how", ["later", "changed"])
def test_an_enrolment_between_the_dialog_and_the_spawn_runs_no_tick(rig, monkeypatch, how):
    """Failed on 7670b97: the runner opened the enrolment made after the start was agreed and ticked on it."""
    exits = _started(rig, monkeypatch, before_spawn=lambda: _enrol_again(rig, how))
    assert exits == [1] and _ran(rig) == 0
    assert heartbeat(rig.root)["code"] == ENROLMENT_CHANGED
    assert ENROLMENT_CHANGED in _status_stop(rig)


def test_an_enrolment_while_the_dialog_is_up_starts_nothing(rig, monkeypatch):
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    monkeypatch.setattr(improve, "_spawn", lambda *args: pytest.fail("spawned on an enrolment the person did not read"))

    def enrol_then_agree(message):
        other = threading.Thread(target=_enrol_again, args=(rig, "later"))   # another session, while this dialog is up
        other.start()
        other.join(60)
        return SimpleNamespace(action="accept", content={"value": "Ada"})

    assert code(rig.call("g6_improve_start", handler=enrol_then_agree)) == improve.TERMS_CHANGED
    assert rig.log().started() is None


@pytest.mark.parametrize("how", ["later", "changed"])
def test_an_enrolment_between_the_spawn_and_the_first_tick_runs_no_tick(rig, monkeypatch, how):
    """Failed on 7670b97: the runner ticked on an enrolment made after it opened the folder."""
    real = runner.Runner.run

    def enrolled_once_open(self):
        _enrol_again(rig, how)
        return real(self)

    monkeypatch.setattr(runner.Runner, "run", enrolled_once_open)
    assert _started(rig, monkeypatch) == [0] and _ran(rig) == 0
    assert not rig.log().start_in_force() and ENROLMENT_CHANGED in _status_stop(rig)


def test_an_enrolment_between_the_runners_check_and_the_ticks_lock_is_refused_by_the_tick(rig, monkeypatch):
    """The tick reads it again holding the folder's lock. Failed on 7670b97: the tick ran on the new enrolment."""
    real, replies = runner.Runner._tick, []

    def enrolled_then_ticked(self, now):
        if not replies:
            _enrol_again(rig, "later")
        real(self, now)
        replies.append(self.last)

    monkeypatch.setattr(runner.Runner, "_tick", enrolled_then_ticked)
    assert _started(rig, monkeypatch) == [0] and _ran(rig) == 0
    assert code(replies[0]) == ENROLMENT_CHANGED, replies[0]


@pytest.mark.parametrize("how", ["later", "changed"])
def test_an_enrolment_between_two_ticks_ends_the_run_and_a_restart_runs_nothing(rig, monkeypatch, how):
    """Failed on 7670b97: the runner went on ticking on the new enrolment."""
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    naps = []

    def enrol_at_the_first_nap(seconds):
        naps.append(seconds)
        if len(naps) == 1:
            _enrol_again(rig, how)
        else:
            (rig.root / "STOP").write_text("the test is over\n", encoding="utf-8")

    monkeypatch.setattr(improve, "_spawn", lambda root, settings: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(improve, "_watch", lambda root, process, since: _envelope("qualified-draft", improve.STARTED, "x"))
    assert code(rig.call("g6_improve_start")) == improve.STARTED
    assert _run(rig, sleep=enrol_at_the_first_nap) == 0 and _ran(rig) == 1 and len(naps) == 1
    assert not rig.log().start_in_force() and ENROLMENT_CHANGED in _status_stop(rig)
    assert _run(rig) == 1 and _ran(rig) == 1                   # a restart: the start it would carry on is over


class _Crash(BaseException):
    """The runner's process dying between two passes."""


def test_a_restart_with_the_agreed_enrolment_still_on_record_carries_on(rig, monkeypatch):
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    monkeypatch.setattr(improve, "_spawn", lambda root, settings: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(improve, "_watch", lambda root, process, since: _envelope("qualified-draft", improve.STARTED, "x"))
    assert code(rig.call("g6_improve_start")) == improve.STARTED

    def crash(seconds):
        raise _Crash

    with pytest.raises(_Crash):
        _run(rig, sleep=crash)
    ran = _ran(rig)
    assert ran == 1 and rig.log().start_in_force() and _status_stop(rig) == ""
    assert _run(rig) == 0 and _ran(rig) == ran + 1             # the restart carries on the start, on its enrolment


def test_a_restart_refused_for_another_enrolment_is_refused_again_and_the_status_shows_it(rig, monkeypatch):
    _started(rig, monkeypatch, before_spawn=lambda: _enrol_again(rig, "later"))
    assert _run(rig) == 1 and _ran(rig) == 0 and heartbeat(rig.root)["code"] == ENROLMENT_CHANGED
    assert ENROLMENT_CHANGED in _status_stop(rig)


def test_a_start_that_names_no_enrolment_runs_nothing_of_the_folder(rig):
    """A start recorded as before this change, with no enrolment named. Failed on 7670b97: the runner opened the folder
    and ran its Python file."""
    marker = rig.tmp / "domain-ran"
    rig.domain.write_text(rig.source + f"\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    marker.unlink()
    terms = improve._terms(rig.root)
    rig.log().start("Ada", at=START.isoformat(), settings=terms[improve.SETTINGS_DIGEST], stops=terms[improve.STOPS_COUNTED])
    assert _run(rig) == 1 and not marker.exists() and _ran(rig) == 0
    assert heartbeat(rig.root)["code"] == ENROLMENT_CHANGED


def test_a_start_naming_an_enrolment_not_on_record_runs_nothing(rig):
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    terms = improve._terms(rig.root)
    rig.log().start("Ada", at=START.isoformat(), settings=terms[improve.SETTINGS_DIGEST], stops=terms[improve.STOPS_COUNTED],
                    enrolment="0" * 64)
    assert _run(rig) == 1 and _ran(rig) == 0 and heartbeat(rig.root)["code"] == ENROLMENT_CHANGED
    assert ENROLMENT_CHANGED in _status_stop(rig)


#: Each field of a start's binding (``improve._terms``), and how the runner the start spawns holds to it.
START_HOLDS = {
    "Folder": "the runner's own folder, where the start is recorded",
    "Domain": "part of the enrolment, held by Enrolment on record",
    "Python file": "part of the settings, held by Settings digest",
    "Python file SHA-256": "frozen by the enrolment, held by Enrolment on record: the run opens only its bytes",
    "Enrolled by": "part of the enrolment, held by Enrolment on record",
    "Enrolment on record": "recorded as enrolment: runner._admission and stop_standing refuse any other",
    "Use": "part of the settings, held by Settings digest",
    "Runs on": "part of the settings, held by Settings digest",
    "Settings digest": "recorded as settings: runner._admission refuses any other",
    "Stops counted": "recorded as stops: runner._admission and stop_standing refuse any other",
    "Stops the last start counted": "shown only: what the last start saw",
    "Stop now in place": "shown only: the STOP file below is what is lifted",
    "STOP file SHA-256": "recorded as lifted: stop_reason stands any other file",
    "STOP file bytes": "recorded as lifted_bytes: stop_reason stands any other file",
    "STOP file modified (ns)": "recorded as lifted_mtime_ns: stop_reason stands any other file",
    "Paused because": "shown only: a pause holds every tick until a person lifts it",
}


def test_every_field_a_start_binds_is_held_by_the_runner_it_spawns(rig):
    """The inventory, built from the code (check_then_act_race): each field of a start's binding is recorded with the
    start and held by the runner, or is part of one that is, or is shown only, and the start records what the runner
    checks."""
    assert code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED"
    binding._paused(rig)
    improve.stop(str(rig.root), "the owner's review")
    assert set(improve._terms(rig.root)) == set(START_HOLDS), "a start binds a field the runner does not hold: list it"
    [recorded] = [c for c in binding._calls(binding._functions(SRC / "mcp" / "improve.py")["start"], "start")
                  if isinstance(c.func, ast.Attribute) and "CycleLog" in ast.unparse(c.func)]
    kept = {k.arg: ast.unparse(k.value) for k in recorded.keywords if k.arg}
    assert kept["enrolment"] == "bound[ENROLMENT]" and kept["settings"] == "bound[SETTINGS_DIGEST]"
    assert kept["stops"] == "bound[STOPS_COUNTED]"
    admission = ast.unparse(binding._functions(SRC / "metaharness" / "runner.py")["_admission"])
    for name in ("stops", "settings", "enrolment"):
        assert f"start.get({name!r})" in admission, f"the runner does not hold the start to its {name}"
