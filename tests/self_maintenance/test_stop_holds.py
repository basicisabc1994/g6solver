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
    f.loop.log.reload()                             # as a tick does under its lock
    halted = f.loop._halted()
    gate = _gate(f.root, read_settings(f.root))[0]
    status = improve.status(str(f.root))
    offered = improve.asked(str(f.root))
    if isinstance(offered, dict):
        dialog = _code(offered)
    else:
        question = offered[1]
        dialog = STOPPED if "Starting lifts the stop" in question or "no STOP file stands for it" in question else ""
    return {"tick": halted.code if halted else "", "gate": {NOT_STARTED: STOPPED}.get(gate, gate),
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
    assert order[:4] == [STOPS_FILE, "on disk", STOP_FILE, "on disk"]


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
    from g6.self_maintenance.envelope import stop_in_force

    ask_to_stop(tmp_path, "why")
    assert stop_in_force(tmp_path, {"lifted": "a" * 64, "lifted_bytes": "all"}).code == STOP_UNREADABLE


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
    assert _readers(folder) == {"tick": STOPPED, "gate": STOPPED, "status": STOPPED, "dialog": ""}
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
