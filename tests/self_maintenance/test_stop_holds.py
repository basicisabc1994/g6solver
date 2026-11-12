# SPDX-License-Identifier: Apache-2.0
"""A stop always stops: whether one is in force is one decision every reader shares, a stop is on disk before anything
counts or acts on it, and a stop record that cannot be read is a stop in force with its own code. FakeLLM is the only model
anywhere here, and nothing starts a process."""
from __future__ import annotations

import builtins
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.mcp import improve
from g6.metaharness.runner import _gate, open_loop
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    RUNNER_LOCK,
    STOP_FILE,
    STOPS_FILE,
    CycleLog,
    ask_to_stop,
    file_digest,
    stops_asked,
)
from g6.self_maintenance.pacing import read_settings

STOP_UNREADABLE = "G6_E_LOOP_STOP_UNREADABLE"
STOPPED = "G6_E_LOOP_STOPPED"
NOT_STARTED = "G6_E_LOOP_NOT_STARTED"
START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)
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


# The reviewers' findings, as they reproduced them ---------------------------------------------------------------------

def test_start_cannot_erase_a_newer_stop_when_the_counter_is_broken(tmp_path, monkeypatch):
    """review-c4, finding 2."""
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# enrolled declaration\n", encoding="utf-8")
    (root / "settings.json").write_text(json.dumps({"domain_file": str(domain), "windows": [
        {"provider": "claude", "allowance": 5000, "week_start": datetime(2026, 11, 1, tzinfo=UTC).isoformat()}
    ]}), encoding="utf-8")
    CycleLog(root / CYCLE_LOG).enrol("Ada", {"domain_file": file_digest(domain)}, domain="review")
    (root / STOPS_FILE).mkdir()  # a broken record, not a permissions-dependent failure
    offered = improve.asked(str(root))
    if isinstance(offered, dict):
        # Refusing to start on an unreadable stop counter is a valid fix.
        assert offered["completion_state"] == "blocked-escalated"
        assert CycleLog(root / CYCLE_LOG).started() is None
        return
    folder, _, shown = offered
    real_start = CycleLog.start
    stopped = []

    def start_then_stop(self, *args, **kwargs):
        result = real_start(self, *args, **kwargs)
        # This is the concurrent stop's ordering: after the start row is written,
        # while start still owns .tick and before it removes the old STOP file.
        stopped.append(improve.stop(str(root), "newer stop"))
        assert (root / STOP_FILE).is_file()
        return result

    spawned = []

    class Finished:
        def poll(self):
            return 0

    monkeypatch.setattr(CycleLog, "start", start_then_stop)
    monkeypatch.setattr(improve, "_spawn", lambda path: spawned.append(path) or Finished())
    reply = improve.start(folder, "Ada", shown)
    if stopped:
        assert stopped[0]["warning_card"]["code"] == improve.STOP_ASKED
    else:
        assert reply["completion_state"] == "blocked-escalated"
    assert not spawned, "start spawned a runner after a newer stop, because both unreadable stop counts were -1"
    if stopped:
        assert (root / STOP_FILE).is_file()


def test_start_must_not_absorb_a_stop_interrupted_before_its_stop_file(tmp_path, monkeypatch):
    """review-l4, finding 1."""
    root = tmp_path / "loop"
    root.mkdir()
    domain_file = tmp_path / "domain.py"
    domain_file.write_text("# never executed\n", encoding="utf-8")
    settings = {"domain_file": str(domain_file), "windows": [
        {"provider": "claude", "allowance": 5000,
         "week_start": "2026-10-26T08:00:00+00:00"}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    log = CycleLog(root / CYCLE_LOG)
    log.enrol("Bo", {"domain_file": file_digest(domain_file)}, domain="word-problems")
    _, _, shown = improve.asked(str(root))

    class StopWriterCrashed(BaseException):
        pass

    real_open = builtins.open

    def crash_before_stop_file(path, *args, **kwargs):
        if Path(path) == root / "STOP":
            raise StopWriterCrashed()
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(builtins, "open", crash_before_stop_file)
        with pytest.raises(StopWriterCrashed):
            ask_to_stop(root, "stop requested while dialog was open")
    assert stops_asked(root) == 1
    assert not (root / "STOP").exists()

    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda root: spawned.append(root))
    monkeypatch.setattr(improve, "_watch", lambda *args: {})
    reply = improve.start(root, "Ada", shown)
    assert not spawned, (
        "start absorbed an unseen stop: spawned a runner and its restart gate says "
        f"{_gate(root, settings)!r}; reply={reply!r}"
    )


# A folder a person enrolled, and every reader of its stop ---------------------------------------------------------------

class Finished:
    """A stand-in for the runner's process, which ends at once: no process starts here."""

    def poll(self) -> int:
        return 0


@pytest.fixture
def folder(tmp_path, monkeypatch):
    (tmp_path / "domain.py").write_text(DOMAIN_FILE.format(rig=str(DOMAINS)), encoding="utf-8")
    root = tmp_path / "loop"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({
        "domain_file": str(tmp_path / "domain.py"), "read_account": False,
        "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": START.isoformat()}]}), encoding="utf-8")
    loop, _ = open_loop(root, now=lambda: NOW)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    spawned: list[Path] = []
    monkeypatch.setattr(improve, "_spawn", lambda at, settings: spawned.append(at) or Finished())
    monkeypatch.setattr(improve, "START_WAIT", 0.2)
    return SimpleNamespace(root=root, loop=loop, spawned=spawned)


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def _agreed(f) -> None:
    """A person's start through the dialog, on what it showed."""
    _, _, shown = improve.asked(str(f.root))
    improve.start(f.root, "Ada", shown)
    assert CycleLog(f.root / CYCLE_LOG).start_in_force()
    f.spawned.clear()


def _journal_only(f, said: str = "the owner said no") -> None:
    """What a stop leaves when it dies after its line in the journal and before its STOP file."""
    with open(f.root / STOPS_FILE, "a", encoding="utf-8") as kept:
        kept.write(said + "\n")


def _unreadable(path: Path) -> None:
    if path.exists():
        path.unlink()
    path.mkdir()                                    # a broken record, whatever this machine's rights


#: What can happen to the stop state after a start that lifted an old stop and saw the journal hold its line.
CHANGES = {
    "nothing": lambda f: None,
    "a stop through the tool": lambda f: improve.stop(str(f.root), "the owner said no"),
    "a stop that died before its STOP file": _journal_only,
    "a journal rewritten to as many lines": lambda f: (f.root / STOPS_FILE).write_text("a newer stop\n", encoding="utf-8"),
    "a journal emptied": lambda f: (f.root / STOPS_FILE).write_bytes(b""),
    "a journal that cannot be read": lambda f: _unreadable(f.root / STOPS_FILE),
    "a STOP file that cannot be read": lambda f: _unreadable(f.root / STOP_FILE),
}
EXPECTED = {"nothing": "", "a journal that cannot be read": STOP_UNREADABLE, "a STOP file that cannot be read": STOP_UNREADABLE}


def _readers(f) -> dict[str, str]:
    """What each reader says of the stop: the tick, the runner's gate, status and the start's dialog."""
    halted = f.loop._halted()                       # the tick's per-request guard, which reads what is on disk
    gate = _gate(f.root, read_settings(f.root))[0]
    status = improve.status(str(f.root))
    offered = improve.asked(str(f.root))
    if isinstance(offered, dict):
        dialog = _code(offered)
    else:
        question = offered[1]
        dialog = STOPPED if any(said in question for said in ("Starting lifts the stop", "no STOP file stands for it",
                                                              "ended by a stop on record")) else ""
    return {"tick": halted.code if halted else "", "gate": gate,     # the gate gives a stop's own code, never NOT_STARTED
            "status": status.get("stop_code", "?") if bool(status["stop"]) else "", "dialog": dialog}


@pytest.mark.parametrize("change", list(CHANGES))
def test_every_reader_gives_the_one_answer_on_whether_a_stop_is_in_force(folder, change):
    improve.stop(str(folder.root), "an old stop")
    _agreed(folder)
    CHANGES[change](folder)
    expected = EXPECTED.get(change, STOPPED)
    assert _readers(folder) == {"tick": expected, "gate": expected, "status": expected, "dialog": expected}


def test_a_journal_that_cannot_be_read_ends_a_tick_at_its_next_request_and_says_so(folder):
    _agreed(folder)
    domain, seen = folder.loop.domain, []
    tutor = domain.tutor

    def unreadable_at_the_third(system: str, user: str) -> str:
        seen.append(user)
        if len(seen) == 3:
            _unreadable(folder.root / STOPS_FILE)
        return tutor(system, user)

    domain.llm._responder = unreadable_at_the_third
    got = folder.loop.tick()
    assert _code(got) == STOP_UNREADABLE and len(seen) == 3 and "cannot be read" in got["message"]
    assert "was asked for" not in got["message"] and not folder.loop.log.start_in_force()
    status = improve.status(str(folder.root))
    assert status["stop_code"] == STOP_UNREADABLE and STOPS_FILE in status["stop"]


def test_a_stop_file_that_cannot_be_read_after_the_dialog_is_not_one_with_nothing_to_lift(folder):
    _, _, shown = improve.asked(str(folder.root))
    _unreadable(folder.root / STOP_FILE)
    got = improve.start(folder.root, "Ada", shown)
    assert _code(got) == STOP_UNREADABLE and got["completion_state"] == "blocked-escalated" and folder.spawned == []
    assert CycleLog(folder.root / CYCLE_LOG).started() is None


@pytest.mark.parametrize("name", [STOP_FILE, STOPS_FILE])
def test_a_start_with_no_dialog_starts_nothing_on_a_stop_state_it_cannot_read(folder, name):
    _unreadable(folder.root / name)
    got = improve.start(folder.root, "Ada")
    assert _code(got) == STOP_UNREADABLE and folder.spawned == [] and name in got["message"]
    assert CycleLog(folder.root / CYCLE_LOG).started() is None


def test_a_journal_rewritten_to_as_many_lines_while_the_dialog_is_open_asks_again(folder):
    improve.stop(str(folder.root), "an old stop")
    _, _, shown = improve.asked(str(folder.root))
    (folder.root / STOPS_FILE).write_text("a newer stop\n", encoding="utf-8")
    got = improve.start(folder.root, "Ada", shown)
    assert _code(got) == improve.TERMS_CHANGED and folder.spawned == []


def test_a_stop_is_on_disk_before_its_stop_file_is_opened(tmp_path, monkeypatch):
    order, real_open, real_sync = [], builtins.open, os.fsync

    def opened(path, *args, **kwargs):
        order.append(Path(path).name)
        return real_open(path, *args, **kwargs)

    def synced(handle):
        order.append("on disk")
        return real_sync(handle)

    monkeypatch.setattr(builtins, "open", opened)
    monkeypatch.setattr(os, "fsync", synced)
    ask_to_stop(tmp_path, "why")
    # The merge with the consent follow-up: the stop's canonical record is on disk first, before either legacy write.
    assert order[:5] == ["on disk", STOPS_FILE, "on disk", STOP_FILE, "on disk"]


# A stop between any two steps of a start ------------------------------------------------------------------------------

def _a_stop(f) -> None:
    improve.stop(str(f.root), "a newer stop")


#: The two shapes a stop can take: whole, and cut off between its two writes.
KINDS = {"whole": _a_stop, "cut off before its STOP file": _journal_only}


def _at(point: str, act, f, monkeypatch) -> None:
    """Arrange for ``act`` to run at ``point`` of the next start, which the dialog has already shown."""
    if point == "with the start's records held":
        real = improve.Coordination

        def held(path):
            act(f)
            return real(path)

        monkeypatch.setattr(improve, "Coordination", held)
    elif point == "just after the start row":
        real_start = CycleLog.start

        def written(self, *args, **kwargs):
            recorded = real_start(self, *args, **kwargs)
            act(f)
            return recorded

        monkeypatch.setattr(CycleLog, "start", written)
    elif point == "as the runner is spawned":
        def spawned(at, settings):
            act(f)
            f.spawned.append(at)
            return Finished()

        monkeypatch.setattr(improve, "_spawn", spawned)
    else:
        act(f)                                       # between the dialog and the start


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("point", ["after the dialog", "with the start's records held", "just after the start row",
                                   "as the runner is spawned"])
def test_a_stop_between_any_two_steps_of_a_start_leaves_no_runner_running_on_it(folder, monkeypatch, kind, point):
    _, _, shown = improve.asked(str(folder.root))
    _at(point, KINDS[kind], folder, monkeypatch)
    got = improve.start(folder.root, "Ada", shown)
    monkeypatch.undo()
    assert got["completion_state"] == "blocked-escalated"
    assert folder.spawned == [] or point == "as the runner is spawned"
    assert _gate(folder.root, read_settings(folder.root))[0], "a restart would run on a start a stop came after"
    assert improve.status(str(folder.root))["stop"]


def test_a_restart_after_a_stop_cut_off_before_its_file_stays_closed_whatever_reads_it(folder):
    _agreed(folder)
    _journal_only(folder)
    assert _readers(folder) == {"tick": STOPPED, "gate": STOPPED, "status": STOPPED, "dialog": STOPPED}


# Review 1's classes, at their roots -------------------------------------------------------------------------------------

def _records_a_directory(f) -> None:
    (f.root / CYCLE_LOG).unlink()
    (f.root / CYCLE_LOG).mkdir()


def _records_cut_short(f) -> None:
    held = (f.root / CYCLE_LOG).read_bytes()
    (f.root / CYCLE_LOG).write_bytes(held[: len(held) // 2])


def _records_one_byte_changed(f) -> None:
    held = bytearray((f.root / CYCLE_LOG).read_bytes())
    at = held.index(b'"by"')                            # inside the first row, so the tail and the size stay as they were
    held[at + 1] = ord("B")
    (f.root / CYCLE_LOG).write_bytes(bytes(held))


def _a_start_with_lift_fields_no_reading_gives(f) -> None:
    from g6.core.ledger import ChainLedger
    ChainLedger(f.root / CYCLE_LOG).append("started", str(len(CycleLog(f.root / CYCLE_LOG)._rows()) + 1),
                                           {"by": "Ada", "stops": "one", "lifted_bytes": "all"})


DAMAGES = {
    "records replaced by a directory": _records_a_directory,
    "records cut short": _records_cut_short,
    "records with one byte changed": _records_one_byte_changed,
    "a start whose lift fields no reading gives": _a_start_with_lift_fields_no_reading_gives,
    "a journal replaced by a directory": lambda f: _unreadable(f.root / STOPS_FILE),
    "a STOP file replaced by a directory": lambda f: _unreadable(f.root / STOP_FILE),
}


@pytest.mark.parametrize("damage", list(DAMAGES))
def test_every_consumer_gives_one_code_for_the_same_damaged_record(folder, damage):
    from g6.self_maintenance.envelope import stop_in_force

    _agreed(folder)
    DAMAGES[damage](folder)

    def code(call):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - a consumer that raises is what this looks for
            return type(exc).__name__

    seen = {"decision": stop_in_force(folder.root).code,
            "gate": code(lambda: _gate(folder.root, read_settings(folder.root))[0]),
            "dialog": code(lambda: improve.asked(str(folder.root))["warning_card"]["code"]),
            "start": code(lambda: improve.start(folder.root, "Ada")["warning_card"]["code"]),
            "tick": code(lambda: folder.loop.tick()["warning_card"]["code"]),
            "status": code(lambda: improve.status(str(folder.root))["stop_code"])}
    assert seen == dict.fromkeys(seen, STOP_UNREADABLE) and folder.spawned == []


@pytest.mark.parametrize("fields", [{"lifted_bytes": "all"}, {"lifted_bytes": -1}, {"lifted": 7}, {"stops": True},
                                    {"stops_sha256": "not hex"}, {"lifted_mtime_ns": "noon"}])
def test_a_start_with_lift_fields_no_reading_gives_is_never_written(tmp_path, fields):
    from g6.core.ledger import LedgerError

    with pytest.raises(LedgerError, match="started"):
        CycleLog(tmp_path / CYCLE_LOG).start("Ada", **fields)


def test_lift_fields_no_reading_gives_are_a_stop_in_force_where_they_are_read_too(tmp_path):
    """A start no reading gives, written by a producer that skipped the rules, is read only from the records: no reader
    takes a start of its own any more."""
    from g6.core.ledger import ChainLedger
    from g6.self_maintenance.envelope import stop_in_force

    ask_to_stop(tmp_path, "why")
    (tmp_path / CYCLE_LOG).parent.mkdir(parents=True, exist_ok=True)
    ChainLedger(tmp_path / CYCLE_LOG).append("started", "1", {"by": "Ada", "lifted": "a" * 64, "lifted_bytes": "all"})
    assert stop_in_force(tmp_path).code == STOP_UNREADABLE


@pytest.mark.parametrize("journal", [b"x", b"operator requested stop", b"one\ntwo"])
def test_with_no_start_on_record_any_bytes_in_the_journal_are_a_stop_the_dialog_names(folder, journal):
    (folder.root / STOPS_FILE).write_bytes(journal)
    _, question, _ = improve.asked(str(folder.root))
    assert "no STOP file stands for it" in question and _readers(folder)["tick"] == STOPPED


def test_a_line_cut_short_after_a_start_that_bound_only_a_count_is_a_stop(folder):
    """A start from before the digest: whole lines only, so the bytes of a line a crash cut short still stand."""
    from g6.self_maintenance.envelope import stop_in_force

    (folder.root / STOPS_FILE).write_bytes(b"an old stop\n")
    folder.loop.log.start("Ada", stops=1)
    assert not stop_in_force(folder.root)
    with open(folder.root / STOPS_FILE, "ab") as kept:
        kept.write(b"a stop cut")
    assert stop_in_force(folder.root).code == STOPPED


def test_a_journal_a_start_saw_with_a_line_cut_short_is_lifted_with_it(folder):
    (folder.root / STOPS_FILE).write_bytes(b"an old stop\na stop cut")
    _agreed(folder)
    assert _readers(folder)["tick"] == ""


def test_a_stop_on_record_after_the_last_start_holds_every_reader_until_a_person_starts_again(folder):
    _agreed(folder)
    CycleLog(folder.root / CYCLE_LOG).stop("a tick met a stop and ended", at="noon")
    assert _readers(folder) == {"tick": STOPPED, "gate": STOPPED, "status": STOPPED, "dialog": STOPPED}
    _agreed(folder)
    assert _readers(folder) == {"tick": "", "gate": "", "status": "", "dialog": ""}


def test_a_refused_start_writes_nothing_durable(folder, monkeypatch):
    _, _, shown = improve.asked(str(folder.root))
    improve.stop(str(folder.root), "while the dialog was open")
    before = (folder.root / CYCLE_LOG).read_bytes()
    got = improve.start(folder.root, "Ada", shown)
    assert _code(got) == improve.TERMS_CHANGED and (folder.root / CYCLE_LOG).read_bytes() == before


def test_a_stop_waiting_on_a_start_that_holds_the_lock_still_writes(folder):
    from g6.self_maintenance.envelope import stop_lock

    with stop_lock(folder.root):
        reply = improve.stop(str(folder.root), "while a start held the lock")
    assert _code(reply) == improve.STOP_ASKED and "while a start held the lock" in reply["stop"]


def test_a_record_changed_mid_tick_with_its_size_and_tail_kept_cuts_the_tick_off(folder):
    _agreed(folder)
    domain, seen = folder.loop.domain, []
    tutor = domain.tutor

    def changed_after_the_first(system: str, user: str) -> str:
        seen.append(user)
        if len(seen) == 1:
            _records_one_byte_changed(folder)
        return tutor(system, user)

    domain.llm._responder = changed_after_the_first
    got = folder.loop.tick()
    assert _code(got) == STOP_UNREADABLE and len(seen) == 1


# Review 2: the reading is a value, taken once from bytes, and nothing else opens the stop's files ------------------------

#: Every place in G6 that joins a path to the STOP file, the journal or the constraint log, or opens a CycleLog, and why.
#: The reading is the only reader; the rest write, or keep a writer's own handle, or only ask whether a folder is a loop.
OPENS = {
    ("g6/self_maintenance/envelope.py", "stop_in_force"): "the one reading: STOP, the journal and the records, once each",
    ("g6/self_maintenance/envelope.py", "_ask_to_stop"): "writes a stop: the journal line, then the STOP file",
    ("g6/self_maintenance/envelope.py", "_establish_stop"): "a stop's canonical record, and, when that cannot be made, a "
                                                            "line of G6's own in the journal",
    ("g6/mcp/improve.py", "_log"): "the records for a person's act and a stop's receipt, read by a row's identity; no "
                                   "stop decision is made from it",
    ("g6/mcp/improve.py", "_stop_before"): "what a stop's receipt compares with: the start it ends and whether a STOP "
                                           "file was there; no stop decision is made from it",
    ("g6/metaharness/runner.py", "person_terms"): "the binding of a person's act (enrol, resume, demote), which lifts no "
                                                  "stop",
    ("g6/metaharness/runner.py", "on_record"): "what the folder holds of a person's act, read back by its identity",
    ("g6/self_maintenance/envelope.py", "record_stop"): "writes a stopped row through the records' writer",
    ("g6/self_maintenance/envelope.py", "flush_journal"): "flushes the journal to disk, writing no byte, before a start's row "
                                                          "binds it",
    ("g6/self_maintenance/envelope.py", "_parsed"): "parses the bytes the reading took, from a copy of its own",
    ("g6/mcp/improve.py", "start"): "writes the started row through the records' writer",
    ("g6/mcp/improve.py", "_is_a_loop"): "asks only whether a folder is a loop at all, before a stop writes there",
    ("g6/metaharness/loop.py", "__init__"): "the tick's own records handle, which writes cycle rows",
}


def _opens_in(path) -> set[tuple[str, str]]:
    import ast

    names, found = {"STOP_FILE", "STOPS_FILE", "CYCLE_LOG"}, set()
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def walk(node, where):
        for child in ast.iter_child_nodes(node):
            inside = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else where
            joined = (isinstance(child, ast.BinOp) and isinstance(child.op, ast.Div) and isinstance(child.right, ast.Name)
                      and child.right.id in names)
            opened = isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "CycleLog"
            if joined or opened:
                found.add(where)
            walk(child, inside)

    walk(tree, "<module>")
    return found


def test_nothing_but_the_reading_reads_the_stop_files_and_the_records():
    import g6

    package = Path(g6.__file__).resolve().parent
    seen = {(f"g6/{path.relative_to(package).as_posix()}", where)
            for path in sorted(package.rglob("*.py")) for where in _opens_in(path)}
    assert seen == set(OPENS), f"a reader outside the one reading, or one gone: {sorted(seen ^ set(OPENS))}"


def _records_folder_a_file(f) -> None:
    (f.root / CYCLE_LOG).unlink()
    (f.root / CYCLE_LOG).parent.rmdir()
    (f.root / CYCLE_LOG).parent.write_bytes(b"a file where the records folder was")


def _records_not_json(f) -> None:
    (f.root / CYCLE_LOG).write_bytes(b"this is not a chain\n")


def _a_start_with_null_lift_fields(f) -> None:
    from g6.core.ledger import ChainLedger
    rows = len(CycleLog(f.root / CYCLE_LOG)._rows())
    ChainLedger(f.root / CYCLE_LOG).append("started", str(rows + 1), {"by": "Ada", "lifted": None, "lifted_bytes": None})


ON_DISK = {
    "nothing at STOP or the journal": (lambda f: None, ""),
    "a directory at STOP": (lambda f: _unreadable(f.root / STOP_FILE), STOP_UNREADABLE),
    "a directory at the journal": (lambda f: _unreadable(f.root / STOPS_FILE), STOP_UNREADABLE),
    "a directory at the records": (_records_a_directory, STOP_UNREADABLE),
    "a file in place of the records folder": (_records_folder_a_file, STOP_UNREADABLE),
    "records cut short": (_records_cut_short, STOP_UNREADABLE),
    "records that are not a chain": (_records_not_json, STOP_UNREADABLE),
    "a start with null lift fields": (_a_start_with_null_lift_fields, STOP_UNREADABLE),
    "a cut-off row after the start": (lambda f: CycleLog(f.root / CYCLE_LOG).stop("cut off", cut_off={"charged": 1}),
                                      STOPPED),
}


@pytest.mark.parametrize("state", list(ON_DISK))
def test_every_consumer_takes_the_one_reading_and_gives_its_code(folder, state):
    from g6.self_maintenance.envelope import stop_in_force

    _agreed(folder)
    change, expected = ON_DISK[state]
    change(folder)
    seen = {"decision": stop_in_force(folder.root).code, **_readers(folder)}
    assert seen == dict.fromkeys(seen, expected)
    if expected == STOP_UNREADABLE:
        assert _code(improve.start(folder.root, "Ada")) == STOP_UNREADABLE and folder.spawned == []


def test_a_path_under_a_regular_file_is_not_absent_on_this_machine(tmp_path):
    """Windows says such a path is not found, where elsewhere it is not a directory: only the folder above can say."""
    from g6.self_maintenance.envelope import stop_in_force

    (tmp_path / "records").write_bytes(b"a file")
    try:
        (tmp_path / CYCLE_LOG).read_bytes()
    except OSError as exc:
        said = type(exc).__name__
    assert said in ("FileNotFoundError", "NotADirectoryError")
    assert stop_in_force(tmp_path).code == STOP_UNREADABLE


def test_a_missing_records_folder_in_a_readable_loop_folder_is_absent(tmp_path):
    from g6.self_maintenance.envelope import stop_in_force

    found = stop_in_force(tmp_path)
    assert found.code == "" and found.start is None and found.records_sha256 == "" and found.log is None


def test_the_reading_names_every_byte_it_stood_on(folder):
    from g6.self_maintenance.envelope import stop_in_force

    _agreed(folder)
    first = stop_in_force(folder.root)
    assert stop_in_force(folder.root).digest == first.digest
    CycleLog(folder.root / CYCLE_LOG).pause("a person paused it")
    assert stop_in_force(folder.root).digest != first.digest


def test_a_runner_ended_by_a_stop_beats_that_stops_code_not_the_last_ticks(folder, monkeypatch):
    from g6.metaharness.runner import Runner
    from g6.self_maintenance.envelope import heartbeat

    _agreed(folder)
    runner = Runner(folder.loop, now=lambda: NOW)
    runner.last = {"warning_card": {"code": "G6_E_LOOP_BUDGET_WAIT"}, "message": "a tick before"}
    monkeypatch.setattr(runner, "_digest", lambda *args: None)
    _unreadable(folder.root / STOPS_FILE)
    assert runner.step() is None and heartbeat(folder.root)["code"] == STOP_UNREADABLE


# Review 3's classes: the read's identity is the handle's, the start's row lands on the approved bytes, older readers are
# fields of the one reading, and one ending for every way the runner ends --------------------------------------------------

@pytest.mark.parametrize("name", [STOP_FILE, STOPS_FILE, CYCLE_LOG])
def test_a_file_replaced_once_its_bytes_are_read_is_read_again_from_the_file_now_there(tmp_path, monkeypatch, name):
    from g6.self_maintenance.envelope import _read_file

    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"old bytes\n")
    other = tmp_path / "other"
    other.write_bytes(b"new bytes\n")                      # as many bytes, another file
    real_open, swapped = Path.open, []

    class Swaps:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self.handle.__exit__(*exc)

        def fileno(self):
            return self.handle.fileno()

        def read(self, *args):
            data = self.handle.read(*args)
            self.handle.close()
            os.replace(other, path)
            swapped.append(True)
            return data

    monkeypatch.setattr(Path, "open", lambda self, mode="r", *a, **k: (
        Swaps(real_open(self, mode, *a, **k)) if self == path and not swapped else real_open(self, mode, *a, **k)))
    assert _read_file(path, tmp_path)[0] == b"new bytes\n" and swapped


def test_this_machine_names_a_file_alike_through_its_open_handle_and_its_path(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"x")
    with path.open("rb") as handle:
        held = os.fstat(handle.fileno())
    named = path.stat()
    assert held.st_ino and (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)


def _states_for_readers(f) -> dict:
    def revoked(g):
        CycleLog(g.root / CYCLE_LOG).stop("a tick durably ended the start")

    def lifted(g):
        improve.stop(str(g.root), "the owner said no")
        _agreed(g)

    return {**{name: change for name, (change, _) in ON_DISK.items()}, "a start revoked on record": revoked,
            "a stop that died before its STOP file": _journal_only, "a STOP file a start lifted": lifted,
            "a stop through the tool": lambda g: improve.stop(str(g.root), "the owner said no")}


READER_STATES = list(_states_for_readers(None))


@pytest.mark.parametrize("state", READER_STATES)
def test_every_older_reader_is_a_field_of_the_one_full_reading(folder, state):
    from g6.self_maintenance import envelope

    _agreed(folder)
    _states_for_readers(folder)[state](folder)
    found = envelope.stop_in_force(folder.root)

    def said(read):
        try:
            return read()
        except envelope.StopUnreadable:
            return "raised"

    assert said(lambda: envelope.stop_standing(folder.root)) == found.reason
    assert said(lambda: envelope.stop_seen(folder.root)) == (
        "raised" if {STOP_FILE, STOPS_FILE} & set(found.unread) else found.seen)
    assert said(lambda: envelope.stop_to_lift(folder.root)) == ("raised" if STOP_FILE in found.unread else found.seen.lift())
    assert said(lambda: envelope.stops_asked(folder.root)) == (
        "raised" if STOPS_FILE in found.unread else found.seen.journal.count(b"\n"))
    assert said(lambda: envelope.stop_reason(folder.root)) == (
        f"a STOP file is present and could not be read ({found.reason})"
        if STOP_FILE in found.unread or found.seen.stop is not None and "records" in found.unread
        else found.seen.by_file(found.start))


def test_no_reader_takes_an_argument_that_skips_part_of_the_reading():
    import ast
    import inspect

    from g6.self_maintenance import envelope

    assert list(inspect.signature(envelope.stop_in_force).parameters) == ["root", "known"]
    for reader in (envelope.stop_standing, envelope.stop_seen, envelope.stop_reason, envelope.stop_to_lift,
                   envelope.stops_asked):
        assert list(inspect.signature(reader).parameters) == ["root"], reader
        calls = [node for node in ast.walk(ast.parse(inspect.getsource(reader)))
                 if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "stop_in_force"]
        assert len(calls) == 1 and len(calls[0].args) == 1 and not calls[0].keywords, reader


def _reformatted(path: Path) -> None:
    """The same chain, entry for entry, in other bytes (compact, where the ledger writes a space after each separator):
    what a reformatting editor leaves."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")


def _another_chain(path: Path) -> None:
    """Another valid chain: the records with a row written after the start's."""
    CycleLog(path).pause("a row the start never approved")


@pytest.mark.parametrize("after", [_reformatted, _another_chain])
def test_a_start_whose_row_did_not_land_on_the_approved_bytes_is_revoked_and_spawns_nothing(folder, monkeypatch, after):
    from g6.self_maintenance.envelope import stop_in_force

    _, _, shown = improve.asked(str(folder.root))
    real = CycleLog.start

    def then_changed(self, *args, **kwargs):
        row = real(self, *args, **kwargs)
        after(self.path)                                     # once the row is on disk, before the start reads it back
        return row

    monkeypatch.setattr(CycleLog, "start", then_changed)
    got = improve.start(folder.root, "Ada", shown)
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and folder.spawned == [], got["message"]
    assert stop_in_force(folder.root).code in (STOPPED, STOP_UNREADABLE)


def test_a_start_that_lands_where_it_was_approved_is_the_approved_bytes_and_one_row(folder):
    from g6.self_maintenance.envelope import stop_in_force

    before = stop_in_force(folder.root)
    _agreed(folder)
    after = stop_in_force(folder.root)
    assert after.records.startswith(before.records) and after.records[len(before.records):].count(b"\n") == 1


def _returns(function) -> list[str]:
    """Each way ``function`` returns, as its source reads, nested functions left out."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    found: list[str] = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                found.append(ast.unparse(child.value) if child.value is not None else "None")
            if isinstance(child, ast.Raise) or (isinstance(child, ast.Call) and "exit" in ast.unparse(child.func)):
                found.append(f"raise or exit: {ast.unparse(child)}")
            walk(child)

    walk(tree.body[0])
    return found


def test_every_way_the_runner_ends_goes_through_the_one_ending():
    from g6.metaharness import runner

    ways = _returns(runner.run)
    assert ways and all(way.startswith("_ending(") for way in ways if not way.startswith("Runner(")), ways
    assert [way for way in ways if way.startswith("Runner(")] == ["Runner(loop, now=now, sleep=sleep, reader=reader).run()"]
    assert _returns(runner.Runner.run) == ["0"]                  # only once a pass returned None, which only its end does
    step = _returns(runner.Runner.step)
    assert step[0] == "self._end(now, halted)" and all(way.startswith("min(") for way in step[1:]), step
    assert _returns(runner.Runner._end) == [] and "_ending(" in __import__("inspect").getsource(runner.Runner._end)


@pytest.mark.parametrize("journal, code", [(b"", NOT_STARTED), (b"a stop no start saw\n", STOPPED)])
def test_the_gate_says_not_started_only_with_no_start_and_no_stop(tmp_path, journal, code):
    root = tmp_path / "loop"
    root.mkdir()
    (root / STOPS_FILE).write_bytes(journal)
    assert _gate(root, {})[0] == code


def test_noting_a_stop_never_raises_and_says_what_it_could_not_note(folder, monkeypatch):
    from g6.self_maintenance.envelope import record_stop

    records, real = folder.root / CYCLE_LOG, Path.stat

    def denied(path, *args, **kwargs):
        if path == records:
            raise PermissionError("denied")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    assert "cannot be read" in record_stop(folder.root, "why", at=NOW.isoformat())


@pytest.mark.parametrize("ending", ["preflight STOP", "gate stop", "unreadable stop"])
def test_each_ending_beats_the_code_of_the_reading_that_ended_it(folder, monkeypatch, ending):
    from g6.metaharness import runner
    from g6.self_maintenance.envelope import beat, heartbeat, stop_in_force

    _agreed(folder)
    beat(folder.root, state="waiting", code="an earlier tick's code")
    if ending == "preflight STOP":
        ask_to_stop(folder.root, "the owner said no")
    elif ending == "gate stop":
        _journal_only(folder)
    else:
        _unreadable(folder.root / STOPS_FILE)
    reading = stop_in_force(folder.root)
    monkeypatch.setattr(runner, "open_loop", lambda *a, **k: pytest.fail("a stopped run opened its loop"))
    runner.run(folder.root, now=lambda: NOW)
    assert heartbeat(folder.root)["code"] == reading.code and reading.code in (STOPPED, STOP_UNREADABLE)


# Review 4's classes: a start's bytes checked before it acts, any error in a stop path is unreadable, a stop honoured before
# anything else, and every ending the one ending with its page --------------------------------------------------------------

def _reformat(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")


def test_a_writer_that_opened_other_bytes_than_the_approved_writes_no_row(folder, monkeypatch):
    _, _, shown = improve.asked(str(folder.root))
    records, real = folder.root / CYCLE_LOG, CycleLog

    def reformatted_first(path):
        _reformat(path)                                     # the same chain, other bytes, after the locked reading
        return real(path)

    monkeypatch.setattr(improve, "CycleLog", reformatted_first)
    changed = records.read_bytes()
    got = improve.start(folder.root, "Ada", shown)
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and folder.spawned == []
    assert records.read_bytes() != changed or not CycleLog(records).start_in_force()
    assert not CycleLog(records).start_in_force()


@pytest.mark.parametrize("failure", [OSError("the disk is full"), RecursionError("deep"), KeyboardInterrupt()])
def test_an_append_that_fails_after_its_row_landed_is_revoked_on_the_way_out(folder, monkeypatch, failure):
    from g6.self_maintenance.envelope import stop_in_force

    _, _, shown = improve.asked(str(folder.root))
    real = CycleLog.start

    def landed_then_failed(self, *args, **kwargs):
        real(self, *args, **kwargs)
        raise failure

    monkeypatch.setattr(CycleLog, "start", landed_then_failed)
    try:
        got = improve.start(folder.root, "Ada", shown)
    except KeyboardInterrupt:
        got = None
    assert folder.spawned == [] and (got is None or got["completion_state"] == "blocked-escalated")
    assert stop_in_force(folder.root)                       # the start that landed was revoked, or a STOP stands for it


def test_a_crash_right_after_the_append_leaves_a_start_only_on_the_approved_bytes(folder, monkeypatch):
    from g6.self_maintenance.envelope import stop_in_force

    _, _, shown = improve.asked(str(folder.root))
    approved = stop_in_force(folder.root).records
    real = CycleLog.start

    class Crash(BaseException):
        pass

    def landed_then_died(self, *args, **kwargs):
        real(self, *args, **kwargs)
        raise Crash()

    monkeypatch.setattr(CycleLog, "start", landed_then_died)
    monkeypatch.setattr(improve, "_revoke", lambda *a: None)      # the process died: nothing after the append ran
    with pytest.raises(Crash):
        improve.start(folder.root, "Ada", shown)
    after = stop_in_force(folder.root).records
    assert after.startswith(approved) and after[len(approved):].count(b"\n") == 1 and folder.spawned == []


#: Every stop path: no handler in these may name a list narrower than ``Exception``, unless a try around it does.
STOP_PATHS = {"g6/self_maintenance/envelope.py": ["_absent", "_read_file", "_parsed", "_start_of", "stop_in_force",
                                                  "admission", "_stop_held",
                                                  "_ask_to_stop", "_establish_stop", "canonical_stops",
                                                  "_canonical_stops_on_disk", "canonical_stop_record", "what_stops",
                                                  "record_stop", "ask_to_stop", "stop_seen", "stop_reason", "stop_standing",
                                                  "stop_to_lift", "stops_asked", "end_start"],
              "g6/metaharness/loop.py": ["_reloaded", "_stop", "_halted", "_cut_off", "lost_cycle", "_refused"],
              "g6/mcp/improve.py": ["start", "stop", "asked", "_cannot_start", "_revoke", "_settings", "_refusal", "_failed",
                                    "_dialog", "_terms", "_watch", "status", "_unread_status", "_running", "_looped"],
              "g6/metaharness/runner.py": ["run", "_ending", "_folder_page", "_plain_page", "_gated", "_end", "_pages",
                                           "step"]}


def _narrow_handlers(source: str, names: list[str]) -> list[str]:
    import ast

    broad = {"Exception", "BaseException"}

    def is_broad(handler) -> bool:
        kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        return handler.type is None or any(isinstance(kind, ast.Name) and kind.id in broad for kind in kinds)

    found: list[str] = []

    def walk(node, covered: bool, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Try):
                mine = any(is_broad(handler) for handler in child.handlers)
                if child.handlers and not (mine or covered):
                    found.append(f"{where}: line {child.lineno}")
                for part in child.body:
                    walk(part, covered or mine, where)
                for part in (*child.handlers, *child.orelse, *child.finalbody):
                    walk(part, covered, where)
            else:
                walk(child, covered, where)

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            walk(node, False, node.name)
    return found


def test_no_handler_in_a_stop_path_names_a_narrower_list_than_every_exception():
    import g6

    package = Path(g6.__file__).resolve().parent
    narrow = {path: _narrow_handlers((package.parent / path).read_text(encoding="utf-8"), names)
              for path, names in STOP_PATHS.items()}
    assert not any(narrow.values()), narrow
    sample = "def f():\n    try:\n        pass\n    except (OSError, ValueError):\n        pass\n"
    assert _narrow_handlers(sample, ["f"]) == ["f: line 2"]          # what the old handlers were, the test finds


STOP_STATES = {
    "no stop": (lambda f: None, ""),
    "a STOP file": (lambda f: ask_to_stop(f.root, "the owner said no"), STOPPED),
    "a stop that died before its STOP file": (_journal_only, STOPPED),
    "a stop on record after the start": (lambda f: CycleLog(f.root / CYCLE_LOG).stop("a tick cut it off"), STOPPED),
    "a STOP that cannot be read": (lambda f: _unreadable(f.root / STOP_FILE), STOP_UNREADABLE),
    "a journal that cannot be read": (lambda f: _unreadable(f.root / STOPS_FILE), STOP_UNREADABLE),
    "records that cannot be read": (lambda f: _records_a_directory(f), STOP_UNREADABLE),
}
SETTINGS_STATES = {"cut short": b'{"truncated":', "not an object": b"[1, 2]", "no windows": b"{}"}


@pytest.mark.parametrize("settings", list(SETTINGS_STATES))
@pytest.mark.parametrize("state", list(STOP_STATES))
def test_a_stop_is_honoured_before_the_settings_at_every_entry_point(folder, monkeypatch, state, settings):
    from g6.metaharness import runner
    from g6.self_maintenance.envelope import heartbeat

    _agreed(folder)
    change, expected = STOP_STATES[state]
    change(folder)
    (folder.root / "settings.json").write_bytes(SETTINGS_STATES[settings])
    monkeypatch.setattr(runner, "open_loop", lambda *a, **k: pytest.fail("a run on broken settings opened its loop"))
    dialog, started = improve.asked(str(folder.root)), improve.start(folder.root, "Ada")
    runner.run(folder.root, now=lambda: NOW)
    ended = heartbeat(folder.root)["code"]
    if expected == STOP_UNREADABLE:
        assert _code(dialog) == _code(started) == ended == STOP_UNREADABLE
    elif expected == STOPPED:                              # a stop a start may lift: never hidden, and the run ends on it
        assert _code(dialog) == _code(started) == "G6_E_LOOP_BAD_SETTINGS" and ended == STOPPED
        assert dialog["stop_code"] == started["stop_code"] == STOPPED
    else:                                                  # no stop: the settings are what refuse
        assert _code(dialog) == _code(started) == "G6_E_LOOP_BAD_SETTINGS" and "stop_code" not in dialog
        assert ended in ("G6_E_LOOP_CANNOT_OPEN", "G6_E_LOOP_SETTINGS_CHANGED")
    assert folder.spawned == []


def _settings_changed(f) -> None:
    settings = json.loads((f.root / "settings.json").read_text(encoding="utf-8"))
    settings["windows"][0]["allowance"] = 999_999
    (f.root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


ENDINGS = {
    "a STOP file at the preflight": lambda f, m: ask_to_stop(f.root, "the owner said no"),
    "a journal stop at the preflight": lambda f, m: _journal_only(f),
    "records that cannot be read": lambda f, m: _records_a_directory(f),
    "settings changed at the gate": lambda f, m: _settings_changed(f),
    "settings that cannot be read": lambda f, m: (f.root / "settings.json").write_bytes(b"{"),
    "a lock folder that is a file": lambda f, m: (f.root / RUNNER_LOCK).write_bytes(b"a file"),
    "a loop that will not open": lambda f, m: m.setattr("g6.metaharness.runner.open_loop",
                                                      lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broken domain"))),
}


@pytest.mark.parametrize("ending", list(ENDINGS))
def test_every_ending_writes_the_days_page_before_the_terminal_heartbeat(folder, monkeypatch, ending):
    from g6.metaharness import runner
    from g6.self_maintenance import digest as daily

    _agreed(folder)
    ENDINGS[ending](folder, monkeypatch)
    order: list[str] = []
    real_write, real_beat = daily.write, runner.beat
    monkeypatch.setattr(daily, "write", lambda *a, **k: (order.append("page"), real_write(*a, **k))[1])
    monkeypatch.setattr(runner, "beat", lambda *a, **k: (order.append(f"beat {k.get('state')}"), real_beat(*a, **k))[1])
    assert runner.run(folder.root, now=lambda: NOW) in (0, 1)
    terminal = [index for index, said in enumerate(order) if said.startswith("beat") and said != "beat starting"]
    assert terminal and "page" in order[:terminal[-1]], order
    assert daily.digest_path(folder.root, NOW.date()).is_file()


def test_the_ending_inventory_covers_exceptions_before_the_hand_off():
    import ast
    import inspect
    import textwrap

    from g6.metaharness import runner

    body = ast.parse(textwrap.dedent(inspect.getsource(runner.run))).body[0].body
    guarded = [node for node in body if isinstance(node, ast.Try)]
    assert len(guarded) == 1
    broad = [handler for handler in guarded[0].handlers
             if isinstance(handler.type, ast.Name) and handler.type.id == "Exception"]
    assert broad and all(isinstance(part, ast.Return) and ast.unparse(part.value).startswith("_ending(")
                         for handler in guarded[0].handlers for part in handler.body)
    unguarded = [ast.unparse(node) for node in body[body.index(guarded[0]) + 1:]]
    assert unguarded == [], unguarded                     # nothing after the hand-off's try runs outside it


# The follow-up's review 1: one maker of every refusal, every statement inside the one handler, only the runner lock's
# refusal is another runner, a page always, and a start binds only durable journal lines --------------------------------

def _functions(module) -> dict:
    import ast
    import inspect

    return {node.name: node for node in ast.parse(inspect.getsource(module)).body if isinstance(node, ast.FunctionDef)}


def _calls(node) -> set[str]:
    import ast

    return {call.func.id for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}


def _kind(value) -> str:
    """What a return gives, as the refusal inventory names it: a refusal of the maker's, a start, or its source."""
    import ast

    call = value.elts[0] if isinstance(value, ast.Tuple) else value
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_start_as_recorded":
        return _kind(call.args[-1])                 # a reply read back keeps its kind: the maker's, or the watch's
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in ("_refusal", "_failed"):
        return "refusal"
    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_envelope"
            and isinstance(call.args[0], ast.Constant) and call.args[0].value == "qualified-draft"):
        return "started"
    if isinstance(value, ast.Dict) and None in value.keys and "_watch" in _calls(value):
        return "watched"
    return ast.unparse(value)


def _returns_in_order(node) -> list[str]:
    import ast

    found = [child for child in ast.walk(node) if isinstance(child, ast.Return) and child.value is not None]
    return [_kind(child.value) for child in sorted(found, key=lambda child: (child.lineno, child.col_offset))]


def test_every_refusal_of_the_dialog_and_the_start_is_made_by_the_one_maker():
    import ast

    functions = _functions(improve)
    reached, todo = set(), ["asked", "start"]
    while todo:                                       # all the dialog and the start run, in this module
        name = todo.pop()
        if name in functions and name not in reached:
            reached.add(name)
            todo += sorted(_calls(functions[name]))
    assert {"_cannot_start", "_dialog", "_watch", "_failed", "_refusal", "_settings"} <= reached
    builders = sorted(name for name in reached if _calls(functions[name]) & {"_blocked", "blocked_reply", "_envelope"}
                      and name != "_watch")
    # ``_restated`` says a reply the maker made again, its state and code kept, once the start's row is read back.
    assert builders == ["_refusal", "_restated"], builders           # no refusal is built anywhere else
    listed = {name: _returns_in_order(functions[name]) for name in ("asked", "start", "_cannot_start", "_watch", "_failed")}
    assert listed == {
        "asked": ["cannot", "cannot", "made", "refusal", "refusal"],
        "start": ["refusal", "cannot", "cannot", "refusal", "refusal", "refusal", "refusal", "watched", "refusal"],
        "_cannot_start": ["refusal", "refusal", "refusal", "refusal", "refusal", "(None, settings)"],
        "_watch": ["refusal", "started", "started"],
        "_failed": ["refusal", "refusal"],
    }, listed
    old = ast.parse("def f():\n    return _blocked(TERMS_CHANGED, 'moved')\n").body[0]
    assert _returns_in_order(old) == ["_blocked(TERMS_CHANGED, 'moved')"]      # what the old returns were, it names


def _a_recorded_stop(f) -> None:
    CycleLog(f.root / CYCLE_LOG).stop("a tick cut it off")


def _no_enrolment_and_a_journal_stop(f) -> None:
    (f.root / CYCLE_LOG).unlink()
    CycleLog(f.root / CYCLE_LOG).pause("no person ever enrolled this")
    _journal_only(f)


def _spawn_that(f, monkeypatch, act, *, fails: bool = False) -> None:
    def spawned(at, settings):
        act(f)
        if fails:
            raise OSError("no such interpreter")
        f.spawned.append(at)
        return Finished()

    monkeypatch.setattr(improve, "_spawn", spawned)


def _writer_that(f, monkeypatch, act, *, fails: bool = False) -> None:
    real = CycleLog

    def opened(path):
        act(f)
        if fails:
            raise OSError("the records will not open")
        return real(path)

    monkeypatch.setattr(improve, "CycleLog", opened)


def _row_lands_elsewhere(f, monkeypatch) -> None:
    real = CycleLog.start

    def landed(self, *args, **kwargs):
        recorded = real(self, *args, **kwargs)
        _reformat(f.root / CYCLE_LOG)
        return recorded

    monkeypatch.setattr(CycleLog, "start", landed)


def _dialog_breaks(f, monkeypatch, act) -> None:
    def broken(*args, **kwargs):
        act(f)
        raise RuntimeError("the dialog broke")

    monkeypatch.setattr(improve, "_terms", broken)


#: Each refusal the dialog or a start can give, set up with a stop a start may lift in force when it is made: the entry
#: point, what is arranged before it, and the refusal's own code.
REFUSALS = {
    "not a loop's folder": ("dialog", lambda f, m: (f.root / "settings.json").unlink(), "G6_E_LOOP_NOT_A_FOLDER"),
    "a runner alive": ("dialog", None, "G6_E_LOOP_RUNNER_ALIVE"),
    "no one enrolled": ("dialog", lambda f, m: _no_enrolment_and_a_journal_stop(f), "G6_E_LOOP_NOT_ENROLLED"),
    "the domain file changed": ("dialog", lambda f, m: (f.root.parent / "domain.py").write_text("# other\n"),
                                "G6_E_LOOP_FROZEN_CHANGED"),
    "a dialog that broke": ("dialog", lambda f, m: _dialog_breaks(f, m, lambda f: None), "G6_E_LOOP_START_FAILED"),
    "the records moved under the writer": ("start", lambda f, m: _writer_that(f, m, _a_recorded_stop),
                                           "G6_E_LOOP_TERMS_CHANGED"),
    "a writer that failed": ("start", lambda f, m: _writer_that(f, m, lambda f: None, fails=True), "G6_E_LOOP_START_FAILED"),
    "a row that did not land": ("start", _row_lands_elsewhere, "G6_E_LOOP_TERMS_CHANGED"),
    "a runner that ended as it started": ("start", lambda f, m: _spawn_that(f, m, _a_recorded_stop),
                                          "G6_E_LOOP_START_FAILED"),
    "a spawn that failed": ("start", lambda f, m: _spawn_that(f, m, _a_recorded_stop, fails=True), "G6_E_LOOP_START_FAILED"),
}


@pytest.mark.parametrize("refusal", list(REFUSALS))
def test_every_refusal_names_a_stop_in_force_beside_its_own_code(folder, monkeypatch, refusal):
    from g6.self_maintenance.envelope import runner_lock, stop_in_force

    entry, arrange, code = REFUSALS[refusal]
    _agreed(folder)
    _a_recorded_stop(folder)                                    # a stop a start may lift, in force before anything else
    shown = improve.asked(str(folder.root))[2] if entry == "start" else None
    if arrange is not None:
        arrange(folder, monkeypatch)
    if refusal == "a runner alive":
        with runner_lock(folder.root):
            got = improve.asked(str(folder.root))
    else:
        got = improve.asked(str(folder.root)) if entry == "dialog" else improve.start(folder.root, "Ada", shown)
    assert _code(got) == code, got
    assert stop_in_force(folder.root).code == STOPPED and got["stop_code"] == STOPPED, got
    assert folder.spawned == [] or refusal == "a runner that ended as it started"


def _stop_unreadable_now(f) -> None:
    _unreadable(f.root / STOP_FILE)


#: The refusals made after the dialog's or the start's first reading, each met by a stop that cannot be read arriving there.
LATE = {
    "a reading that cannot be made under the writer": lambda f, m: _writer_that(f, m, _stop_unreadable_now),
    "a writer that failed": lambda f, m: _writer_that(f, m, _stop_unreadable_now, fails=True),
    "a runner that ended as it started": lambda f, m: _spawn_that(f, m, _stop_unreadable_now),
    "a spawn that failed": lambda f, m: _spawn_that(f, m, _stop_unreadable_now, fails=True),
    "a dialog that broke": lambda f, m: _dialog_breaks(f, m, _stop_unreadable_now),
}


@pytest.mark.parametrize("refusal", list(LATE))
def test_a_stop_that_cannot_be_read_when_a_refusal_is_made_is_that_refusals_code(folder, monkeypatch, refusal):
    shown = improve.asked(str(folder.root))[2]
    LATE[refusal](folder, monkeypatch)
    got = improve.asked(str(folder.root)) if refusal == "a dialog that broke" else improve.start(folder.root, "Ada", shown)
    assert _code(got) == got["stop_code"] == STOP_UNREADABLE, got
    assert folder.spawned == [] or refusal == "a runner that ended as it started"


def test_every_statement_of_the_dialog_and_the_start_runs_inside_the_one_handler():
    import ast
    import inspect
    import textwrap

    def outside(source: str, maker: str = "return _failed(") -> list[str]:
        body = ast.parse(textwrap.dedent(source)).body[0].body
        body = body[1:] if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) else body
        tries = [node for node in body if isinstance(node, ast.Try)]
        assert len(tries) == 1 and body[-1] is tries[0], "one handler, and nothing after it"
        [handler] = tries[0].handlers
        assert isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
        assert [ast.unparse(part)[:len(maker)] for part in handler.body] == [maker]
        return [ast.unparse(node) for node in body[:-1]                     # nothing before it touches a file
                if {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)} - {"Path"}]

    assert outside(inspect.getsource(improve.asked)) == [] and outside(inspect.getsource(improve.start)) == []
    assert outside(inspect.getsource(improve.status), "return _unread_status(") == []     # status, the follow-up's review 2
    assert outside(inspect.getsource(improve.stop), "return _blocked(STOP_FAILED") == []  # the stop, the follow-up's review 3
    old = ("def f(root):\n    if not (root / 'settings.json').is_file():\n        return 1\n    try:\n        pass\n"
           "    except Exception:\n        return _failed(root, None)\n")
    assert outside(old) == ["if not (root / 'settings.json').is_file():\n    return 1"]   # the old shape, it finds


#: What can fail where the dialog or the start touches a file, beside the settings: what fails, and who meets it.
FAILURES = [("finding the folder", "_root", "dialog"), ("asking whether a runner is alive", "runner_alive", "dialog"),
            ("asking whether a runner is alive", "runner_alive", "start"),
            ("the domain file's digest", "file_digest", "dialog"), ("the domain file's digest", "file_digest", "start"),
            ("the tick lock", "Coordination", "start")]


@pytest.mark.parametrize("failure, name, consumer", FAILURES)
def test_whatever_fails_where_the_dialog_or_the_start_touches_a_file_is_a_refusal_with_the_stop(folder, monkeypatch,
                                                                                                 failure, name, consumer):
    (folder.root / STOP_FILE).write_bytes(b"the owner said no\n")

    def denied(*args, **kwargs):
        raise PermissionError(f"{failure} was denied")

    monkeypatch.setattr(improve, name, denied)
    got = improve.asked(str(folder.root)) if consumer == "dialog" else improve.start(folder.root, "Ada")
    assert got["completion_state"] == "blocked-escalated" and got["stop_code"] == STOPPED and folder.spawned == [], got


def test_only_the_runner_locks_refusal_is_read_as_another_runner():
    import ast

    run = _functions(__import__("g6.metaharness.runner", fromlist=["run"]))["run"]
    refusals = [node for node in ast.walk(run) if isinstance(node, ast.Try)
                and any("WorkspaceRefused" in ast.unparse(handler.type) for handler in node.handlers if handler.type)]
    assert [[ast.unparse(part) for part in node.body] for node in refusals] == [["held.enter_context(runner_lock(root))"]]


@pytest.mark.parametrize("held", ["by another runner", "a hard-linked lock", "the workspace, after the lock"])
def test_a_refusal_is_another_runner_only_when_another_holds_the_runner_lock(folder, monkeypatch, held):
    from g6.metaharness import runner
    from g6.self_maintenance import digest as daily
    from g6.self_maintenance.envelope import beat, heartbeat, runner_lock
    from g6.workspace import _coordination
    from g6.workspace._envelope import BUSY, WorkspaceRefused

    _agreed(folder)
    beat(folder.root, state="waiting", code="the live runner's")
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    monkeypatch.setattr(runner, "open_loop", lambda *a, **k: (_ for _ in ()).throw(WorkspaceRefused(BUSY, "busy workspace")))
    if held == "by another runner":
        with runner_lock(folder.root):
            assert runner.run(folder.root, now=lambda: NOW) == 1
        assert heartbeat(folder.root)["code"] == "the live runner's"          # its page and heartbeat are the live one's
        assert not daily.digest_path(folder.root, NOW.date()).is_file()
        return
    if held == "a hard-linked lock":
        with runner_lock(folder.root):
            pass                                                              # the lock's folder and file, made
        (folder.root / RUNNER_LOCK / "mutex").unlink()
        spare = folder.root / "spare"
        spare.write_bytes(b"")
        (folder.root / RUNNER_LOCK / "mutex").hardlink_to(spare)
    assert runner.run(folder.root, now=lambda: NOW) == 1
    terminal = heartbeat(folder.root)
    assert (terminal["state"], terminal["code"]) == ("not-running", runner.CANNOT_OPEN), terminal
    assert daily.digest_path(folder.root, NOW.date()).is_file()


def _render_breaks(monkeypatch) -> None:
    from g6.self_maintenance import digest as daily

    monkeypatch.setattr(daily, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cannot render")))


def _page_a_folder(f) -> None:
    from g6.self_maintenance import digest as daily

    daily.digest_path(f.root, NOW.date()).mkdir(parents=True)          # a page that cannot be written there at all


@pytest.mark.parametrize("ending", ["a STOP at the preflight", "a gate refusal", "a running loop's stop"])
@pytest.mark.parametrize("page", ["cannot be made", "cannot be written"])
def test_an_ending_always_writes_a_page_or_says_it_could_not(folder, monkeypatch, ending, page):
    from g6.metaharness import runner
    from g6.self_maintenance import digest as daily
    from g6.self_maintenance.envelope import heartbeat

    _agreed(folder)
    path = daily.digest_path(folder.root, NOW.date())
    if ending == "a gate refusal":
        _settings_changed(folder)
    else:
        ask_to_stop(folder.root, "the owner said no")
    if page == "cannot be made":
        daily.write(folder.root, NOW.date(), "the old page\n")
        _render_breaks(monkeypatch)
    else:
        _page_a_folder(folder)
    if ending == "a running loop's stop":
        assert runner.Runner(folder.loop, now=lambda: NOW, sleep=lambda seconds: None).step() is None
    else:
        monkeypatch.setattr(runner, "open_loop", lambda *a, **k: pytest.fail("this ending opens no loop"))
        runner.run(folder.root, now=lambda: NOW)
    terminal = heartbeat(folder.root)
    assert terminal["state"] in ("stopped", "not-running") and terminal["code"] in (STOPPED, "G6_E_LOOP_SETTINGS_CHANGED")
    if page == "cannot be made":
        text = path.read_text(encoding="utf-8")
        assert terminal["code"] in text and "The runner ended" in text and "the old page" not in text, text
        assert "page_not_written" not in terminal
    else:
        assert "could not be written" in terminal["page_not_written"], terminal


def test_a_start_flushes_the_journal_before_its_row_binds_it(folder, monkeypatch):
    real_sync, real_start, order = os.fsync, CycleLog.start, []
    journal = folder.root / STOPS_FILE
    _agreed(folder)
    improve.stop(str(folder.root), "the owner said no")
    _, _, shown = improve.asked(str(folder.root))

    def synced(handle):
        if journal.exists() and os.fstat(handle).st_ino == journal.stat().st_ino:
            order.append("journal on disk")
        return real_sync(handle)

    def written(self, *args, **kwargs):
        order.append("row")
        return real_start(self, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(CycleLog, "start", written)
    improve.start(folder.root, "Ada", shown)
    assert order == ["journal on disk", "row"], order


def test_a_journal_that_cannot_be_flushed_starts_nothing_and_writes_no_row(folder, monkeypatch):
    real_sync, journal = os.fsync, folder.root / STOPS_FILE
    _journal_only(folder)
    _, _, shown = improve.asked(str(folder.root))
    before = (folder.root / CYCLE_LOG).read_bytes()

    def failed(handle):
        if os.fstat(handle).st_ino == journal.stat().st_ino:
            raise OSError("the disk would not take it")
        return real_sync(handle)

    monkeypatch.setattr(os, "fsync", failed)
    got = improve.start(folder.root, "Ada", shown)
    assert _code(got) == improve.START_FAILED and got["stop_code"] == STOPPED and folder.spawned == []
    assert (folder.root / CYCLE_LOG).read_bytes() == before
