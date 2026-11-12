# SPDX-License-Identifier: Apache-2.0
"""Review 3 of claude/stop-holds-followup: its five findings as the reviewer reproduced them, with the reviewer's fixture
(its own temporary folder aside: pytest's serves here), and the four classes they belong to, each removed at its root with
an inventory built from the code: where a row ends, how caller text becomes bytes, a revocation that survives the repair
of what caused it, and the stop as one handler. Nothing starts a process or a model: FakeLLM answers the real loop's ticks."""
from __future__ import annotations

import ast
import contextlib
import inspect
import json
import os
import stat
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.core.ledger import ChainLedger, digest
from g6.mcp import improve
from g6.metaharness import loop as loop_module
from g6.metaharness import runner
from g6.self_maintenance import envelope
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    REFUSED_FILE,
    RUNNER_LOCK,
    STOP_FILE,
    STOP_UNREADABLE,
    STOPPED,
    STOPS_FILE,
    CycleLog,
    file_digest,
    heartbeat,
    stop_in_force,
)
from g6.self_maintenance.pacing import read_settings
from g6.workspace._coordination import Coordination

NOW = datetime(2026, 11, 6, tzinfo=UTC)
PACKAGE = Path(g6.__file__).resolve().parent
DOMAINS = Path(__file__).parents[1] / "metaharness" / "_loop_domains.py"
DOMAIN_FILE = """
import importlib.util
import sys
from pathlib import Path

from g6.metaharness.method import Method

_spec = importlib.util.spec_from_file_location("loop_domains", {rig!r})
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

LOOP = {{"domain": domains.WordProblems(Path(__file__).parent), "proposer": domains.proposes({{"prompt": domains.WORKING}}),
        "method": Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1),
        "audit_per_tick": 60}}
"""


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))

    def forbidden(*args, **kwargs):
        raise AssertionError("review tests must not start processes")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def active(tmp_path, monkeypatch):
    """The reviewer's fixture: an enrolled folder with a start in force and an inert domain file that never runs."""
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Inert; this file must never execute.\n", encoding="utf-8")
    settings = {"domain_file": str(domain), "read_account": False, "windows": [
        {"provider": "claude", "allowance": 1000000, "week_start": "2026-10-26T08:00:00+00:00"}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    log = CycleLog(root / CYCLE_LOG)
    log.enrol("Ada", {"domain_file": file_digest(domain)}, domain="review")
    log.start("Ada", settings=digest(settings), **stop_in_force(root).seen.binding())
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *args: spawned.append(args) or object())
    monkeypatch.setattr(improve, "_watch", lambda *args: {"completion_state": "qualified-draft"})
    return SimpleNamespace(root=root, settings=settings, log=log, spawned=spawned)


@pytest.fixture
def looped(tmp_path, monkeypatch):
    """A folder whose domain is a real one, enrolled by a person, with a start in force: for what only a tick can show."""
    (tmp_path / "domain.py").write_text(DOMAIN_FILE.format(rig=str(DOMAINS)), encoding="utf-8")
    root = tmp_path / "loop"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({
        "domain_file": str(tmp_path / "domain.py"), "read_account": False,
        "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": "2026-10-26T08:00:00+00:00"}]}),
        encoding="utf-8")
    loop, _ = runner.open_loop(root, now=lambda: NOW)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    settings = read_settings(root)
    CycleLog(root / CYCLE_LOG).start("Ada", settings=digest(settings), **stop_in_force(root).seen.binding())
    assert runner._gated(root, settings)[0] == ""
    return SimpleNamespace(root=root, loop=loop, settings=settings)


def _after(held: bytes) -> bytes:
    """What an append writes first, as the rule states it: a newline after a last row with none, else nothing."""
    return b"\n" if held and not held.endswith(b"\n") else b""


def _spelled(text: str) -> str:
    """Text as the rule writes it: UTF-8, with what it cannot carry spelled out with a backslash."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def _forbidden(*args, **kwargs):
    raise AssertionError("the stopped preflight must not open a real loop or read settings")


def _end_runner(root: Path, monkeypatch) -> int:
    """A runner restarted on ``root``: its preflight reads the stop and ends there, opening no loop."""
    with monkeypatch.context() as held:
        held.setattr(runner, "open_loop", _forbidden)
        held.setattr(runner, "read_settings", _forbidden)
        return runner.run(root, now=lambda: NOW)


def _refused_again(root: Path, settings: dict) -> str:
    """What the restart gate says: a code when it refuses, ``""`` when the start on record runs again."""
    return runner._gated(root, settings)[0]


# The reviewer's findings, as they reproduced them ---------------------------------------------------------------------

def test_start_does_not_corrupt_a_ledger_accepted_unterminated_last_row(active):
    """Finding 1."""
    records = active.root / envelope.CYCLE_LOG
    records.write_bytes(records.read_bytes().rstrip(b"\n"))
    approved = envelope.stop_in_force(active.root)
    assert not approved and approved.log.start_in_force()
    shown = improve.asked(str(active.root))
    assert isinstance(shown, tuple)

    reply = improve.start(active.root, "Ada", shown[2])

    landed = envelope.stop_in_force(active.root)
    assert "records" not in landed.unread, (
        "start concatenated its row to a valid final JSON row and corrupted the approved ledger; "
        f"reply={reply!r}"
    )


def test_stop_reason_encoding_cannot_leave_the_approved_start_live(active):
    """Finding 2."""
    envelope.ask_to_stop(active.root, "previous stop")
    log = envelope.CycleLog(active.root / envelope.CYCLE_LOG)
    log.start("Ada", settings=digest(active.settings), **envelope.stop_in_force(active.root).seen.binding())
    assert not envelope.stop_in_force(active.root)

    reply = improve.stop(str(active.root), "stop now: \ud800")

    assert envelope.stop_in_force(active.root), (
        "an unpaired surrogate in the caller's reason prevented both stop writes and left the start live; "
        f"reply={reply!r}"
    )


def test_unreadable_cutoff_survives_failure_to_note_it_in_records(active, monkeypatch):
    """Finding 3."""
    loop = loop_module.ShadowLoop.__new__(loop_module.ShadowLoop)
    loop.root, loop.log = active.root, active.log
    loop.cannot_record, loop._on_record, loop.evaluating = "", None, True
    loop._now = lambda: datetime(2026, 11, 6, tzinfo=UTC)
    journal = active.root / envelope.STOPS_FILE
    journal.mkdir()  # An actual unreadable stop, while the records and STOP remain writable.
    halted = loop._halted()
    assert halted.code == envelope.STOP_UNREADABLE

    def cannot_note(*args, **kwargs):
        raise OSError("the records write failed")

    with monkeypatch.context() as fault:
        fault.setattr(loop.log, "stop", cannot_note)
        reply = loop._cut_off(halted, charged=1)
    assert reply["warning_card"]["code"] == envelope.STOP_UNREADABLE
    journal.rmdir()  # A person repairs the unreadable entry without granting a new start.

    assert runner._gated(active.root, active.settings)[0], (
        "the cut-off lost its only revocation when the log write failed; "
        "repairing the journal made the old start runnable again"
    )


def test_runner_ending_under_tick_contention_durably_revokes_start(active, monkeypatch):
    """Finding 4."""
    journal = active.root / envelope.STOPS_FILE
    journal.mkdir()

    monkeypatch.setattr(runner, "open_loop", _forbidden)
    monkeypatch.setattr(runner, "read_settings", _forbidden)
    with Coordination(active.root / ".tick").mutex(wait=0):
        result = runner.run(active.root, now=lambda: datetime(2026, 11, 6, tzinfo=UTC))
    assert result == 1
    terminal = envelope.heartbeat(active.root)
    assert terminal["code"] == envelope.STOP_UNREADABLE
    assert "not_noted" in terminal
    journal.rmdir()

    assert runner._gated(active.root, active.settings)[0], (
        "the runner ended on a stop but contention prevented its revocation; "
        "after repair, a restart accepts the old start without new consent"
    )


def test_settings_metadata_failure_cannot_abort_writing_a_stop(active, monkeypatch):
    """Finding 5."""
    real_is_file = Path.is_file

    def settings_unreadable(path, *args, **kwargs):
        if path == active.root / "settings.json":
            raise PermissionError("settings metadata cannot be read")
        return real_is_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", settings_unreadable)
    escaped = None
    try:
        improve.stop(str(active.root), "stop despite unreadable settings")
    except Exception as exc:
        escaped = type(exc).__name__

    assert escaped is None, "stop's loop-folder check escaped before the stop write: " + str(escaped)
    assert envelope.stop_in_force(active.root), "settings metadata prevented a writable STOP from being written"


# Class 1, where a row ends (boundary_case): one rule, for the reader and every writer ---------------------------------

def _rows(path: Path) -> list[dict]:
    """The records as the ledger itself reads them, refusing any that do not check out."""
    problems = ChainLedger.audit(path, validate=envelope._rules)
    assert problems == (), problems
    return ChainLedger(path, validate=envelope._rules).entries()


#: What the records' last row can end with, as the ledger reads it: each is records it accepts.
ENDINGS = {
    "an empty file": lambda held: b"",
    "a last row with its newline": lambda held: held,
    "a last row without its newline": lambda held: held.rstrip(b"\n"),
    "every row ending in CRLF": lambda held: held.replace(b"\n", b"\r\n"),
}


@pytest.mark.parametrize("ending", list(ENDINGS))
@pytest.mark.parametrize("writer", ["a stopped row", "a started row", "a stop noted by record_stop", "a cycle"])
def test_every_writer_of_the_records_adds_a_row_of_its_own_whatever_the_last_one_ends_with(active, ending, writer):
    records = active.root / CYCLE_LOG
    records.write_bytes(ENDINGS[ending](records.read_bytes()))
    before, rows = records.read_bytes(), len(_rows(records)) if records.read_bytes() else 0
    if writer == "a stopped row":
        CycleLog(records).stop("why")
    elif writer == "a started row":
        CycleLog(records).start("Ada", settings="")
    elif writer == "a stop noted by record_stop":
        if not CycleLog(records).start_in_force():
            CycleLog(records).start("Ada", settings="")
            before, rows = records.read_bytes(), rows + 1
        assert envelope.record_stop(active.root, "why", at="noon") == ""
    else:
        CycleLog(records).cycle({"outcome": "idle", "code": "x", "reason": ""})
    after = records.read_bytes()
    added = after[len(before):]
    assert after.startswith(before) and added.startswith(_after(before))
    assert added[len(_after(before)):].count(b"\n") == 1 and added.endswith(b"\n")
    assert len(_rows(records)) == rows + 1


def test_records_whose_last_row_is_cut_short_take_no_row_and_are_a_stop_in_force(active):
    records = active.root / CYCLE_LOG
    records.write_bytes(records.read_bytes() + b'{"seq": 2, "ki')
    before = records.read_bytes()
    with pytest.raises(Exception):
        CycleLog(records).stop("why")
    assert envelope.record_stop(active.root, "why", at="noon")           # it says why no note was made
    assert records.read_bytes() == before and stop_in_force(active.root).code == STOP_UNREADABLE


@pytest.mark.parametrize("ending", [name for name in ENDINGS if name != "an empty file"])
def test_a_start_through_the_dialog_lands_one_row_on_the_approved_records_whatever_they_end_with(active, ending):
    records = active.root / CYCLE_LOG
    records.write_bytes(ENDINGS[ending](records.read_bytes()))
    approved = stop_in_force(active.root)
    shown = improve.asked(str(active.root))
    reply = improve.start(active.root, "Ada", shown[2])
    landed = stop_in_force(active.root)
    assert reply["completion_state"] == "qualified-draft" and len(active.spawned) == 1, reply
    assert landed.unread == () and improve._appended_onto(landed, approved)
    assert len(_rows(records)) == len(approved.log._rows()) + 1


@pytest.mark.parametrize("approved, added, lands", [
    (b'{"a":1}\n', b'{"b":2}\n', True),
    (b'{"a":1}', b'\n{"b":2}\n', True),
    (b'{"a":1}\r\n', b'{"b":2}\n', True),
    (b'{"a":1}', b'{"b":2}\n', False),                        # joined onto the approved last row
    (b'{"a":1}\n', b'\n{"b":2}\n', False),                    # a blank line the rule never writes
    (b'{"a":1}', b'\n{"b":2}\n{"c":3}\n', False),             # two rows
    (b'{"a":1}', b'\n{"b":2}', False),                        # a row with no end
])
def test_what_landed_is_one_row_started_by_the_one_rule(approved, added, lands):
    reading = lambda held: envelope.Stop(records=held)          # noqa: E731 - a reading of these bytes alone
    assert improve._appended_onto(reading(approved + added), reading(approved)) is lands


#: What the journal of stops can end with: absent, empty, a whole line, a line a crash cut short, a line ending in CRLF.
JOURNALS = {"absent": None, "empty": b"", "a whole line": b"an old stop\n", "a line cut short": b"an old st",
            "a line ending in CRLF": b"an old stop\r\n"}


@pytest.mark.parametrize("journal", list(JOURNALS))
def test_every_stop_adds_a_line_of_its_own_to_the_journal_whatever_it_ends_with(active, journal):
    path = active.root / STOPS_FILE
    if JOURNALS[journal] is not None:
        path.write_bytes(JOURNALS[journal])
    CycleLog(active.root / CYCLE_LOG).start("Ada", settings=digest(active.settings),
                                            **stop_in_force(active.root).seen.binding())
    assert not stop_in_force(active.root)
    before = path.read_bytes() if path.exists() else b""
    envelope.ask_to_stop(active.root, "a new stop")
    after = path.read_bytes()
    added = after[len(before):]
    assert after.startswith(before) and added.startswith(_after(before) + b"a new stop")      # a line of its own,
    assert added.endswith(b"\n") and added[len(_after(before)):].count(b"\n") == 1           # ended, LF or CRLF
    assert stop_in_force(active.root).code == STOPPED


def test_a_stop_after_a_hand_written_stop_with_no_newline_is_a_line_of_its_own(active):
    (active.root / STOP_FILE).write_bytes(b"stop")
    CycleLog(active.root / CYCLE_LOG).start("Ada", settings=digest(active.settings),
                                            **stop_in_force(active.root).seen.binding())
    assert not stop_in_force(active.root)
    envelope.ask_to_stop(active.root, "a new stop")
    assert (active.root / STOP_FILE).read_bytes().startswith(b"stop\na new stop")
    assert stop_in_force(active.root).reason == "a STOP file is present: a new stop"


def test_a_refusal_after_a_line_cut_short_is_still_known(active):
    (active.root / REFUSED_FILE).write_bytes(b"0" * 30)
    known = "a" * envelope.DIGEST_LENGTH
    envelope.note_refusal(active.root, known, "refused")
    assert known in envelope.refused_requests(active.root)


#: Every write in the envelope, which alone writes the records, the journal and the STOP file (``test_stop_holds``'s
#: ``OPENS``): what it writes, and how.
WRITES = {
    ("_durably", "open"): "every row of the journal, the STOP file and the refusals, and the end of the records' last row: "
                          "on a fresh line (fresh_line), as utf8 writes it",
    ("CycleLog._append", "_chain.append"): "every row of the records, after _durably ends the last one",
    # The merge with the consent follow-up: rows written with an identity of their own, and the stop's canonical record.
    ("CycleLog._write_record", "_chain.append"): "every row written with its identity, after _durably ends the last one",
    ("_establish_stop", "os.open"): "a stop's canonical record, a new file of G6's own id and time, and the marker "
                                    "that stands in when its folder cannot be made",
    ("_parsed", "os.fdopen"): "a copy of the bytes a reading took, whole, for the ledger to read",
    ("flush_journal", "os.open"): "the journal flushed, writing no byte",
    ("beat", "write_bytes"): "a whole heartbeat, swapped in, as utf8 writes it",
}


def _writes_in(source: str) -> set[tuple[str, str]]:
    """Every call in ``source`` that writes a file, with the function (``Class.method`` in a class) it is in."""
    found: set[tuple[str, str]] = set()

    def mode_writes(call: ast.Call, name: str) -> bool:
        modes = [arg for arg in call.args[1:2]] + [k.value for k in call.keywords if k.arg in ("mode", "flags")]
        if not modes:
            return False
        if name == "os.open":                                   # flags: any that opens the file to write
            return any(flag in ast.unparse(modes[0]) for flag in ("WRONLY", "RDWR", "APPEND", "CREAT"))
        texts = [node.value for node in ast.walk(modes[0]) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        return not texts or any(flag in text for text in texts for flag in "awx+")   # a mode it cannot read: a write

    def walk(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            inside = where
            if isinstance(child, ast.ClassDef):
                inside = child.name
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inside = f"{where}.{child.name}" if where and where[0].isupper() else child.name
            if isinstance(child, ast.Call):
                name = ast.unparse(child.func)
                if name in ("open", "os.open", "os.fdopen") and mode_writes(child, name):
                    found.add((where, name))
                elif name.endswith((".write_text", ".write_bytes")):
                    found.add((where, name.rsplit(".", 1)[1]))
                elif name.endswith("_chain.append"):
                    found.add((where, "_chain.append"))
            walk(child, inside)

    walk(ast.parse(source), "")
    return found


def test_every_append_to_the_records_and_the_journal_goes_through_the_one_rule():
    source = (PACKAGE / "self_maintenance" / "envelope.py").read_text(encoding="utf-8")
    assert _writes_in(source) == set(WRITES), sorted(_writes_in(source) ^ set(WRITES))
    functions = {node.name: node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)}
    durably = {ast.unparse(call.func) for call in ast.walk(functions["_durably"]) if isinstance(call, ast.Call)}
    assert {"fresh_line", "utf8"} <= durably
    appended = [ast.unparse(call.func) for call in ast.walk(functions["_append"]) if isinstance(call, ast.Call)]
    assert appended.index("_durably") < appended.index("self._chain.append")
    onto = {ast.unparse(call.func) for call in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(
        improve._appended_onto)))) if isinstance(call, ast.Call)}
    assert "fresh_line" in onto                      # the start's check of what landed holds to the same rule
    old = "def _durably(path, text):\n    with open(path, 'a', encoding='utf-8') as kept:\n        kept.write(text)\n"
    assert _writes_in(old) == {("_durably", "open")}            # the old writer, the inventory finds
    # Nothing outside the envelope opens the records or the journal at all: test_stop_holds's OPENS inventory.


# Class 2, caller text into bytes (boundary_case): one rule that cannot fail, on every stop write ---------------------

#: What a caller's reason can hold: lone surrogates of both halves, NUL, newlines, nothing, a great deal.
REASONS = {"a lone high surrogate": "stop now: \ud800", "a lone low surrogate": "\udfff stop", "both, reversed": "\udfff\ud800",
           "NUL": "stop\x00now", "newlines": "one\ntwo\r\nthree four", "nothing": "", "a very long one": "\ud800x" * 50_000}


def _readable_utf8(root: Path) -> None:
    for name in (STOP_FILE, STOPS_FILE, str(CYCLE_LOG), envelope.HEARTBEAT_FILE):
        if (root / name).is_file():
            (root / name).read_bytes().decode("utf-8")             # strictly: every byte written is plain UTF-8


@pytest.mark.parametrize("reason", list(REASONS))
def test_a_stop_through_the_tool_is_written_whatever_the_reason_holds(active, reason):
    envelope.ask_to_stop(active.root, "previous stop")
    CycleLog(active.root / CYCLE_LOG).start("Ada", settings=digest(active.settings),
                                            **stop_in_force(active.root).seen.binding())
    assert not stop_in_force(active.root)
    reply = improve.stop(str(active.root), REASONS[reason])
    found = stop_in_force(active.root)
    assert _code(reply) == improve.STOP_ASKED and found.code == STOPPED and found.unread == ()
    assert not CycleLog(active.root / CYCLE_LOG).start_in_force()       # its row was written too
    _readable_utf8(active.root)


@pytest.mark.parametrize("reason", list(REASONS))
@pytest.mark.parametrize("write", ["ask_to_stop", "a stopped row", "record_stop", "end_start", "beat", "note_refusal",
                                   "the runner's ending"])
def test_every_stop_write_takes_any_text_and_the_reading_reads_it_back(active, write, reason):
    said, root = REASONS[reason], active.root
    if write == "ask_to_stop":
        envelope.ask_to_stop(root, said)
    elif write == "a stopped row":
        CycleLog(root / CYCLE_LOG).stop(said or "-", at=said)
    elif write == "record_stop":
        assert envelope.record_stop(root, said or "-", at=said) == ""
    elif write == "end_start":
        assert envelope.end_start(root, STOP_UNREADABLE, said) == ""
    elif write == "beat":
        envelope.beat(root, reason=said, **{said: said})
        assert heartbeat(root)["reason"] == _spelled(said)
    elif write == "note_refusal":
        envelope.note_refusal(root, "b" * envelope.DIGEST_LENGTH, said)
        assert "b" * envelope.DIGEST_LENGTH in envelope.refused_requests(root)
    else:
        assert runner._ending(root, STOP_UNREADABLE, said, NOW, lock=runner.HOLDS) == 1
        assert heartbeat(root)["code"] == STOP_UNREADABLE and "stop_not_written" not in heartbeat(root)
    found = stop_in_force(root)
    assert found.unread == () and (found.code == STOPPED or write in ("beat", "note_refusal"))
    _readable_utf8(root)


#: Where text becomes bytes on a stop path: the one rule, and nothing else.
ENCODES = {("g6/self_maintenance/envelope.py", "utf8"): "the one rule: UTF-8, what it cannot carry spelled out",
           # The merge with the consent follow-up: the one writer appends, in text mode with LF, what utf8 has already
           # made storable, so the stream's own encoding adds no rule and a write cut short is seen as one.
           ("g6/self_maintenance/envelope.py", "_durably"): "appends what utf8 made storable, as UTF-8 with LF"}


def _encodes_in(source: str, names: set[str] | None) -> set[str]:
    """The functions in ``source`` (those in ``names``, or all) that make bytes of text themselves: ``.encode``, a text-mode
    write ``open``, ``write_text``, or a ``print`` of text the rule has not made (``writable``)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (names is None or node.name in names):
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = ast.unparse(call.func)
                modes = [ast.unparse(a) for a in call.args[1:2]] + [ast.unparse(k.value) for k in call.keywords
                                                                     if k.arg == "mode"]
                text_write = (name == "open" and modes and "b" not in modes[0]
                              and any(flag in modes[0] for flag in "awx+"))
                printed = (name == "print" and call.args
                           and not (isinstance(call.args[0], ast.Call) and ast.unparse(call.args[0].func) == "writable"))
                if name.endswith(".encode") or name.endswith(".write_text") or text_write or printed:
                    found.add(node.name)
    return found


def _stop_paths() -> dict[str, list[str]]:
    """``test_stop_holds``'s inventory of every stop path, as that file states it."""
    tree = ast.parse(Path(__file__).with_name("test_stop_holds.py").read_text(encoding="utf-8"))
    [value] = [node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "STOP_PATHS" for target in node.targets)]
    return ast.literal_eval(value)


def test_every_place_caller_text_becomes_bytes_on_a_stop_path_is_the_one_rule():
    STOP_PATHS = _stop_paths()
    found = {("g6/self_maintenance/envelope.py", name)
             for name in _encodes_in((PACKAGE / "self_maintenance" / "envelope.py").read_text(encoding="utf-8"), None)}
    for path, names in STOP_PATHS.items():
        if path != "g6/self_maintenance/envelope.py":
            found |= {(path, name) for name in _encodes_in((PACKAGE.parent / path).read_text(encoding="utf-8"),
                                                           set(names))}
    assert found == set(ENCODES), sorted(found ^ set(ENCODES))
    old = "def _durably(path, text):\n    with open(path, 'a', encoding='utf-8') as kept:\n        kept.write(text)\n"
    assert _encodes_in(old, None) == {"_durably"}               # the old writer, the inventory finds
    assert _encodes_in("def _ending(why):\n    print(why, file=sys.stderr)\n", None) == {"_ending"}   # and the old print
    assert loop_module._writable is envelope.writable           # the tick's rows hold to the same rule


# Class 3, a revocation that survives the repair of what caused it (state_not_durable) ----------------------------------

def _partial_row(f) -> None:
    path = f.root / CYCLE_LOG
    f.records = path.read_bytes()
    path.write_bytes(f.records + b'{"seq": 9, "ki')


def _whole_again(f) -> None:
    (f.root / CYCLE_LOG).write_bytes(f.records)


#: What makes a stop in force only because a file cannot be read, and the person's repair of it, which grants no start.
CAUSES = {
    "a journal that is a folder": (lambda f: (f.root / STOPS_FILE).mkdir(), lambda f: (f.root / STOPS_FILE).rmdir()),
    "a STOP file that is a folder": (lambda f: (f.root / STOP_FILE).mkdir(), lambda f: (f.root / STOP_FILE).rmdir()),
    "records whose last row is cut short": (_partial_row, _whole_again),
}


def _tick_held(root: Path):
    """The tick lock, held by another operation for as long as the block runs."""
    return Coordination(root / ".tick").mutex(wait=0)


def _no_row(*args, **kwargs):
    raise OSError("the records would not take the row")


@pytest.mark.parametrize("cause", list(CAUSES))
@pytest.mark.parametrize("row", ["the row is written", "the row write fails", "the tick lock is held"])
def test_a_runner_ended_on_an_unreadable_stop_stays_ended_once_the_file_is_repaired(active, monkeypatch, cause, row):
    make, repair = CAUSES[cause]
    make(active)
    with monkeypatch.context() as fault:
        if row == "the row write fails":
            fault.setattr(CycleLog, "stop", _no_row)
        with _tick_held(active.root) if row == "the tick lock is held" else contextlib.nullcontext():
            assert _end_runner(active.root, monkeypatch) == 1
    beat = heartbeat(active.root)
    assert beat["code"] == STOP_UNREADABLE
    assert ("stop_not_written" in beat) is (cause == "a STOP file that is a folder"), beat   # said only when it failed
    repair(active)
    found = stop_in_force(active.root)
    assert found.unread == () and found.code == STOPPED, found
    assert _refused_again(active.root, active.settings) == STOPPED


def test_an_unwritable_journal_leaves_the_stop_in_its_stop_file(active, monkeypatch):
    journal = active.root / STOPS_FILE
    journal.write_bytes(b"")
    CycleLog(active.root / CYCLE_LOG).start("Ada", settings=digest(active.settings),
                                            **stop_in_force(active.root).seen.binding())
    _partial_row(active)
    os.chmod(journal, stat.S_IREAD)                 # readable, and nothing may add to it
    try:
        assert _end_runner(active.root, monkeypatch) == 1
        assert journal.read_bytes() == b"" and (active.root / STOP_FILE).is_file()
    finally:
        os.chmod(journal, stat.S_IREAD | stat.S_IWRITE)
    _whole_again(active)
    assert _refused_again(active.root, active.settings) == STOPPED


def test_a_stop_file_that_cannot_be_written_leaves_the_stop_in_the_journal_and_says_so(active, monkeypatch):
    (active.root / STOP_FILE).mkdir()
    with monkeypatch.context() as fault:
        fault.setattr(CycleLog, "stop", _no_row)
        assert _end_runner(active.root, monkeypatch) == 1
    beat = heartbeat(active.root)
    assert "STOP" in beat["stop_not_written"] and b"G6_E_LOOP_STOP_UNREADABLE" in (active.root / STOPS_FILE).read_bytes()
    (active.root / STOP_FILE).rmdir()
    assert _refused_again(active.root, active.settings) == STOPPED


class Crash(BaseException):
    """The process dies here: nothing after it runs."""


def _crash_at(point: str, monkeypatch) -> None:
    real = envelope._durably

    def dies(path, *args, **kwargs):
        if Path(path).name == point:
            raise Crash(point)
        return real(path, *args, **kwargs)

    if point == "the row":
        def no_row(*args, **kwargs):
            raise Crash(point)

        monkeypatch.setattr(CycleLog, "stop", no_row)
    else:
        monkeypatch.setattr(envelope, "_durably", dies)


def _hand_written(f) -> None:
    (f.root / STOP_FILE).write_text("the owner wrote this\n", encoding="utf-8")


#: Every stop a runner ends on and its person's repair: the unreadable files, and a STOP file a person wrote and removes.
ENDINGS_ON = {**CAUSES, "a STOP file a person wrote": (_hand_written, lambda f: (f.root / STOP_FILE).unlink())}
#: The writes an ending on each makes, in order: a file that cannot be read is no file to write.
WRITTEN = {"a journal that is a folder": ["the row", STOP_FILE], "a STOP file that is a folder": ["the row", STOPS_FILE],
           "records whose last row is cut short": [STOPS_FILE, STOP_FILE],
           "a STOP file a person wrote": ["the row"]}          # a stop asked for stands in its own file: only the row
#: A crash at each of those writes, and then a restart before the person's repair, or the repair alone. The repair alone
#: after a crash at the first write has nothing written to stand on: the window this leaves open (the report says so).
CRASHES = [(cause, point, then) for cause, points in WRITTEN.items() for point in points
           for then in ("a restart before the repair", "the repair alone")
           if then == "a restart before the repair" or point != points[0]]


@pytest.mark.parametrize("cause, point, then", CRASHES)
def test_a_crash_between_any_two_writes_of_an_ending_leaves_the_start_ended(active, monkeypatch, cause, point, then):
    make, repair = ENDINGS_ON[cause]
    make(active)
    with monkeypatch.context() as dying:
        _crash_at(point, dying)
        with pytest.raises(Crash):
            _end_runner(active.root, monkeypatch)
    if then == "a restart before the repair":
        _end_runner(active.root, monkeypatch)
    repair(active)
    assert stop_in_force(active.root).unread == ()
    assert _refused_again(active.root, active.settings) == STOPPED


def test_the_durable_stop_names_its_code_and_a_person_starting_again_lifts_it(active, monkeypatch):
    (active.root / STOPS_FILE).mkdir()
    with _tick_held(active.root):
        _end_runner(active.root, monkeypatch)
    (active.root / STOPS_FILE).rmdir()
    question = improve.asked(str(active.root))[1]
    assert f"a STOP file is present: {STOP_UNREADABLE}" in question
    shown = improve.asked(str(active.root))[2]
    assert improve.start(active.root, "Ada", shown)["completion_state"] == "qualified-draft"
    assert _refused_again(active.root, active.settings) == ""


def _kept(root: Path) -> dict[str, bytes | None]:
    return {name: (root / name).read_bytes() if (root / name).is_file() else None for name in (STOP_FILE, STOPS_FILE)}


@pytest.mark.parametrize("ended", ["by a stop on record", "with no start on record", "on an unreadable stop once ended",
                                   "on a stop a person asked for"])
def test_an_ending_writes_through_the_stops_writer_only_what_a_repair_could_lift(active, monkeypatch, ended):
    if ended == "by a stop on record":
        envelope.ask_to_stop(active.root, "the owner said no")
        CycleLog(active.root / CYCLE_LOG).stop("the owner said no")
    elif ended == "with no start on record":
        (active.root / CYCLE_LOG).unlink()
        envelope.ask_to_stop(active.root, "the owner said no")
    elif ended == "on an unreadable stop once ended":
        (active.root / STOPS_FILE).mkdir()
        _end_runner(active.root, monkeypatch)                         # its row and its STOP file
        assert not CycleLog(active.root / CYCLE_LOG).start_in_force() and (active.root / STOP_FILE).is_file()
    else:
        (active.root / STOP_FILE).write_text("the owner wrote this\n", encoding="utf-8")
    before = _kept(active.root)
    for _ in range(3):
        _end_runner(active.root, monkeypatch)
    assert _kept(active.root) == before
    assert not CycleLog(active.root / CYCLE_LOG).start_in_force() or ended == "with no start on record"


@pytest.mark.parametrize("cause", ["a journal that is a folder", "a STOP file that is a folder"])
def test_a_tick_refused_by_its_guard_on_an_unreadable_stop_ends_the_start_for_good(looped, cause):
    make, repair = CAUSES[cause]
    make(looped)
    got = looped.loop.tick()
    assert _code(got) == STOP_UNREADABLE
    repair(looped)
    assert _refused_again(looped.root, looped.settings) == STOPPED


def test_a_tick_whose_records_will_not_reload_ends_the_start_for_good(looped):
    _partial_row(looped)
    got = looped.loop.tick()
    assert _code(got) == STOP_UNREADABLE
    _whole_again(looped)
    assert _refused_again(looped.root, looped.settings) == STOPPED


@pytest.mark.parametrize("row", ["the row is written", "the row write fails"])
def test_a_tick_cut_off_at_a_model_call_ends_the_start_for_good(looped, monkeypatch, row):
    domain, seen = looped.loop.domain, []
    tutor = domain.tutor

    def unreadable_at_the_third(system: str, user: str) -> str:
        seen.append(user)
        if len(seen) == 3:
            (looped.root / STOPS_FILE).mkdir()
        return tutor(system, user)

    domain.llm._responder = unreadable_at_the_third
    if row == "the row write fails":
        monkeypatch.setattr(CycleLog, "stop", _no_row)
    got = looped.loop.tick()
    monkeypatch.undo()
    assert _code(got) == STOP_UNREADABLE and len(seen) == 3
    (looped.root / STOPS_FILE).rmdir()
    assert _refused_again(looped.root, looped.settings) == STOPPED


def test_a_runner_step_that_meets_an_unreadable_stop_ends_the_start_for_good(looped, monkeypatch):
    run = runner.Runner(looped.loop, now=lambda: NOW, sleep=lambda seconds: None)
    (looped.root / STOP_FILE).mkdir()
    monkeypatch.setattr(CycleLog, "stop", _no_row)
    assert run.step() is None
    monkeypatch.undo()
    (looped.root / STOP_FILE).rmdir()
    assert _refused_again(looped.root, looped.settings) == STOPPED


def _calls(node: ast.AST) -> set[str]:
    return {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}


def _ends_on_a_stop(source: str) -> dict[str, bool]:
    """Every function in ``source`` that ends a start on a stop, and whether it writes a stop through the stop's own writer:
    one that writes a stopped row (``record_stop``, or a records writer's ``stop``), or refuses a tick with a folder stop's
    code (``_blocked`` with ``halted.code``, ``STOP_UNREADABLE`` or ``STOPPED`` first)."""
    found = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = _calls(node)
        rows = "record_stop" in calls or any(name.endswith(("log.stop", "writer.stop")) for name in calls)
        refuses = any(isinstance(call, ast.Call) and ast.unparse(call.func) == "_blocked" and call.args
                      and ast.unparse(call.args[0]) in ("halted.code", "STOP_UNREADABLE", "STOPPED")
                      for call in ast.walk(node))
        if rows or refuses:
            found[node.name] = bool(calls & {"end_start", "ask_to_stop"})
    return found


#: Every way a start ends on a stop, by module, as the code has them.
ENDS = {"g6/metaharness/loop.py": {"_reloaded", "_refused", "_cut_off"},
        "g6/metaharness/runner.py": {"_ending"},
        "g6/mcp/improve.py": {"_revoke", "start", "stop"}}


def test_every_way_a_start_ends_on_a_stop_writes_a_stop_through_the_stops_own_writer():
    for path, names in ENDS.items():
        found = _ends_on_a_stop((PACKAGE.parent / path).read_text(encoding="utf-8"))
        assert set(found) == names, (path, sorted(set(found) ^ names))
        assert all(found.values()), (path, [name for name, kept in found.items() if not kept])
    old = "def _cut_off(self, halted):\n    self.log.stop(halted.said)\n    return _blocked(halted.code, 'cut')\n"
    assert _ends_on_a_stop(old) == {"_cut_off": False}           # the old cut-off, the inventory finds
    ending = ast.parse(textwrap.dedent(inspect.getsource(runner._ending)))
    noted = [node for node in ast.walk(ending) if isinstance(node, ast.If) and "ENDING_STOPS" in ast.unparse(node.test)]
    assert len(noted) == 1 and {"record_stop", "end_start"} <= _calls(noted[0])   # both, on every stop an ending meets


# Class 4, the stop as one handler (guard_covers_less_than_it_guards) --------------------------------------------------

#: What the stop's loop-folder check reads, in its order: each is a check that cannot be made, which never keeps the stop
#: from its writes, and what is taken away first so the check reaches it.
CHECKS = {"the settings": ("is_file", lambda root: root / "settings.json", []),
          "the records": ("is_file", lambda root: root / CYCLE_LOG, ["settings.json"]),
          "the runner's lock": ("is_dir", lambda root: root / RUNNER_LOCK, ["settings.json", str(CYCLE_LOG)])}


@pytest.mark.parametrize("check", list(CHECKS))
def test_a_loop_check_that_cannot_be_made_never_keeps_the_stop_from_its_writes(active, monkeypatch, check):
    method, at, before = CHECKS[check]
    real = getattr(Path, method)
    for name in before:
        (active.root / name).unlink()

    def cannot(path, *args, **kwargs):
        if path == at(active.root):
            raise PermissionError("cannot be looked at")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, cannot)
    reply = improve.stop(str(active.root), "stop all the same")
    monkeypatch.undo()
    assert _code(reply) == improve.STOP_ASKED and stop_in_force(active.root).code == STOPPED


def test_a_folder_that_cannot_be_found_is_a_stop_that_says_it_failed_and_nothing_escapes(active, monkeypatch):
    def lost(folder):
        raise PermissionError("the folder cannot be resolved")

    monkeypatch.setattr(improve, "_root", lost)
    reply = improve.stop(str(active.root), "stop")
    assert _code(reply) == improve.STOP_FAILED and not (active.root / STOP_FILE).exists()


def test_a_stop_that_cannot_be_written_says_so_and_nothing_escapes(active):
    (active.root / STOP_FILE).mkdir()
    reply = improve.stop(str(active.root), "stop")
    assert _code(reply) == improve.STOP_FAILED and Path(reply["folder"]) == active.root.resolve()
