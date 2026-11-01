# SPDX-License-Identifier: Apache-2.0
"""The loop's controls over MCP (``g6.mcp.improve``): start goes through the host's permission and the client's dialog
and nowhere else, stop always works and kills nothing, and status only reads.

The first groups stand in for the process start and fake nothing else; the last group starts a real process. FakeLLM is
the only model anywhere here, and no test reaches a provider."""
from __future__ import annotations

import asyncio
import builtins
import json
import os
import socket
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.invariants import check_envelope

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

from g6.core.ledger import ChainLedger  # noqa: E402
from g6.core.ledger import digest as digest_of  # noqa: E402
from g6.mcp import build_server, improve  # noqa: E402
from g6.metaharness import shadow  # noqa: E402
from g6.metaharness.shadow import ADOPTIONS  # noqa: E402
from g6.self_maintenance import digest, envelope  # noqa: E402
from g6.self_maintenance.envelope import (  # noqa: E402
    CYCLE_LOG,
    CycleLog,
    beat,
    file_digest,
    runner_lock,
    stop_reason,
    stops_asked,
)
from g6.self_maintenance.pacing import read_settings  # noqa: E402

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
ENROLLED_BY = "Bo"
#: What the person must never be pointed to, and an agent never told, for what the dialog asks.
TERMINAL = "g6.metaharness.loop start"


def data(reply) -> dict:
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


async def named(message, response_type, params, context):
    return response_type(value="Ada")


async def declined(message, response_type, params, context):
    return ElicitResult(action="decline")


async def cancelled(message, response_type, params, context):
    return ElicitResult(action="cancel")


async def nameless(message, response_type, params, context):
    return response_type(value="   ")


class Finished:
    """A stand-in for the process: it holds nothing, so the watch sees it end, or ``alive`` says it took the lock."""

    def __init__(self, code: int | None = 0) -> None:
        self.code = code

    def poll(self) -> int | None:
        return self.code


class Rig:
    """A loop's folder, a fresh G6 home, and a server. Nothing here starts a process."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.improve")
        self.root = tmp_path / "loop"
        self.root.mkdir()
        self.domain_file = tmp_path / "domain.py"
        self.domain_file.write_text("# declares no LOOP: nothing here is ever meant to tick\n", encoding="utf-8")
        (self.root / "settings.json").write_text(json.dumps({
            "domain_file": str(self.domain_file),
            "windows": [{"provider": "claude", "allowance": 5000, "week_start": START.isoformat()}]}), encoding="utf-8")
        self.log = CycleLog(self.root / CYCLE_LOG)
        self.asked: list[str] = []
        self.spawned: list[Path] = []
        self.server = build_server()

        def spawn(root: Path) -> Finished:
            self.spawned.append(root)
            return Finished()

        monkeypatch.setattr(improve, "_spawn", spawn)
        monkeypatch.setattr(improve, "START_WAIT", 1.0)

    def enrol(self) -> None:
        self.log.enrol(ENROLLED_BY, {"domain_file": file_digest(self.domain_file)}, domain="word-problems")

    def client(self, handler=named):
        async def seen(message, response_type, params, context):
            self.asked.append(message)
            return await handler(message, response_type, params, context)

        return Client(self.server, elicitation_handler=seen) if handler else Client(self.server)

    def start(self, handler=named, folder: str | None = None) -> dict:
        async def flow():
            async with self.client(handler) as client:
                return data(await client.call_tool("g6_improve_start",
                                                   {"folder": str(self.root) if folder is None else folder}))

        return asyncio.run(flow())

    def call(self, tool: str, **arguments) -> dict:
        async def flow():
            async with Client(self.server) as client:
                return data(await client.call_tool(tool, arguments))

        return asyncio.run(flow())


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# Start ----------------------------------------------------------------------------------------------------------------

def test_the_host_must_allow_it_and_no_dialog_is_shown_otherwise(rig, monkeypatch):
    rig.enrol()
    monkeypatch.setenv("G6_MCP_SENSITIVE", "workforce.mission")
    got = rig.start()
    assert _code(got) == "G6_E_SENSITIVE_COMPONENT_DENIED" and "metaharness.improve" in got["error"]
    assert check_envelope(got) == [] and rig.asked == [] and rig.spawned == []


@pytest.mark.parametrize("allowed", ["metaharness.improve", "metaharness"])
def test_the_same_allow_rule_as_any_other_sensitive_operation(rig, monkeypatch, allowed):
    rig.enrol()
    monkeypatch.setenv("G6_MCP_SENSITIVE", allowed)
    rig.start()
    assert rig.spawned == [rig.root]


def test_the_person_is_asked_in_the_clients_dialog_and_what_they_read_says_what_runs(rig):
    rig.enrol()
    got = rig.start()
    [question] = rig.asked
    assert str(rig.root) in question and "word-problems" in question and "domain.py" in question
    assert "5000 a week (an estimate)" in question and "20%" in question and ENROLLED_BY in question
    assert "not authenticated" in question and "outlives this session" in question and "pushes nothing" in question
    assert rig.spawned == [rig.root] and got["started_by"] == "Ada"


def test_who_started_it_goes_in_the_log_recorded_as_given(rig):
    rig.enrol()
    rig.start()
    rig.log.reload()
    assert rig.log.started()["by"] == "Ada"


@pytest.mark.parametrize("handler", [declined, cancelled, nameless, None])
def test_a_decline_a_cancel_no_name_or_a_client_with_no_dialog_starts_nothing(rig, handler):
    rig.enrol()
    (rig.root / "STOP").write_text("held\n", encoding="utf-8")
    before = _files(rig.root)
    got = rig.start(handler)
    assert got["completion_state"] == "blocked-escalated" and check_envelope(got) == []
    assert _code(got) == "G6_E_META_APPROVAL_REQUIRED" and rig.spawned == []
    assert _files(rig.root) == before and TERMINAL not in json.dumps(got)
    assert "Nothing started" in got["next_action"] and "no other way" in got["next_action"]


def test_no_argument_can_answer_for_the_person(rig):
    tool = asyncio.run(rig.server.get_tools())["g6_improve_start"]
    assert set(tool.parameters["properties"]) == {"folder"}


def test_it_refuses_while_a_runner_is_alive_and_says_what_to_do(rig):
    rig.enrol()
    with runner_lock(rig.root):
        got = rig.start()
    assert _code(got) == "G6_E_LOOP_RUNNER_ALIVE" and got["completion_state"] == "blocked-escalated"
    assert "g6_improve_status" in got["next_action"] and rig.asked == [] and rig.spawned == []


def test_it_refuses_a_folder_with_no_enrolled_domain_and_a_person_still_enrols(rig):
    got = rig.start()
    assert _code(got) == "G6_E_LOOP_NOT_ENROLLED" and rig.asked == [] and rig.spawned == []
    assert "g6.metaharness.loop enrol" in got["next_action"] and "An agent cannot" in got["next_action"]


def test_a_python_file_that_is_not_the_one_a_person_enrolled_is_refused_before_it_could_run(rig):
    rig.enrol()
    rig.domain_file.write_text("# something else now\n", encoding="utf-8")
    got = rig.start()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "did not run" in got["message"]
    assert rig.asked == [] and rig.spawned == [] and "g6.metaharness.loop enrol" in got["next_action"]


def test_settings_pointed_at_another_file_after_enrolment_are_refused_the_same_way(rig, tmp_path):
    rig.enrol()
    other = tmp_path / "other.py"
    other.write_text("# not what a person enrolled\n", encoding="utf-8")
    settings = json.loads((rig.root / "settings.json").read_text(encoding="utf-8"))
    (rig.root / "settings.json").write_text(json.dumps({**settings, "domain_file": str(other)}), encoding="utf-8")
    assert _code(rig.start()) == "G6_E_LOOP_FROZEN_CHANGED" and rig.spawned == []


def test_a_folder_with_records_but_no_enrolment_is_refused_the_same_way(rig):
    rig.log.pause("no person ever enrolled this")
    assert _code(rig.start()) == "G6_E_LOOP_NOT_ENROLLED"


def test_it_refuses_a_folder_that_is_no_loops(rig, tmp_path):
    (tmp_path / "plain").mkdir()
    got = rig.start(folder=str(tmp_path / "plain"))
    assert _code(got) == "G6_E_LOOP_NOT_A_FOLDER" and list((tmp_path / "plain").iterdir()) == []


def test_settings_that_make_no_sense_are_refused_before_anything_is_asked(rig):
    rig.enrol()
    (rig.root / "settings.json").write_text('{"windows": []}', encoding="utf-8")
    assert _code(rig.start()) == "G6_E_LOOP_BAD_SETTINGS" and rig.asked == []


def test_records_that_do_not_check_out_are_refused_before_anything_is_asked(rig):
    rig.enrol()
    path = rig.root / CYCLE_LOG
    path.write_text(path.read_text(encoding="utf-8").replace(ENROLLED_BY, "Cy"), encoding="utf-8")
    assert _code(rig.start()) == "G6_E_LOOP_RECORDS_UNREADABLE" and rig.asked == []


def test_a_runner_that_came_alive_while_the_dialog_was_up_starts_nothing_more(rig):
    rig.enrol()
    held = []

    async def someone_else_got_there(message, response_type, params, context):
        held.append(runner_lock(rig.root))
        held[0].__enter__()
        return response_type(value="Ada")

    try:
        got = rig.start(someone_else_got_there)
    finally:
        held[0].__exit__(None, None, None)
    assert _code(got) == "G6_E_LOOP_RUNNER_ALIVE" and rig.spawned == []


def test_a_stop_in_place_is_named_in_the_dialog_and_removed_only_once_the_person_agrees(rig):
    rig.enrol()
    (rig.root / "STOP").write_text("the owner's review\n", encoding="utf-8")
    rig.start(declined)
    assert (rig.root / "STOP").exists() and "the owner's review" in rig.asked[0]
    rig.start()
    assert not (rig.root / "STOP").exists() and rig.spawned == [rig.root]


def test_a_pause_is_named_in_the_dialog(rig):
    rig.enrol()
    rig.log.pause("three failed cycles in a row")
    rig.start(declined)
    assert "three failed cycles" in rig.asked[0]


def test_a_runner_that_ends_as_it_starts_is_a_failed_start_and_says_why(rig, monkeypatch):
    rig.enrol()
    beat(rig.root, state="not-running", reason="domain_file is missing")
    monkeypatch.setattr(improve, "_spawn", lambda root: Finished(1))
    got = rig.start()
    assert _code(got) == "G6_E_LOOP_START_FAILED" and "domain_file is missing" in got["message"]
    assert got["running"] is False and check_envelope(got) == []


def test_a_runner_that_takes_its_lock_is_reported_alive(rig, monkeypatch):
    rig.enrol()
    held = runner_lock(rig.root)

    def spawn(root: Path) -> Finished:
        held.__enter__()
        return Finished(None)

    monkeypatch.setattr(improve, "_spawn", spawn)
    try:
        got = rig.start()
    finally:
        held.__exit__(None, None, None)
    assert _code(got) == "G6_E_LOOP_STARTED" and got["running"] is True and got["completion_state"] == "qualified-draft"
    assert check_envelope(got) == []


def test_a_program_that_cannot_be_started_is_a_refusal_not_a_raised_error(rig, monkeypatch):
    rig.enrol()

    def fails(root: Path) -> None:
        raise OSError("no such interpreter")

    monkeypatch.setattr(improve, "_spawn", fails)
    got = rig.start()
    assert _code(got) == "G6_E_LOOP_START_FAILED" and "no such interpreter" in got["message"]


def test_the_runner_is_detached_so_it_outlives_the_session(rig, monkeypatch):
    seen: dict = {}

    def popen(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return Finished()

    monkeypatch.undo()                                                  # the real _spawn
    monkeypatch.setattr(subprocess, "Popen", popen)
    improve._spawn(rig.root)
    assert seen["argv"][1:] == ["-P", "-m", "g6.metaharness.loop", "run", str(rig.root)]
    assert seen["argv"][0] == sys.executable and seen["stdin"] == subprocess.DEVNULL
    if os.name == "nt":
        assert seen["creationflags"] & subprocess.DETACHED_PROCESS
        assert seen["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert seen["start_new_session"] is True


# Stop -----------------------------------------------------------------------------------------------------------------

def test_stop_needs_no_dialog_and_no_permission(rig, monkeypatch):
    monkeypatch.delenv("G6_MCP_SENSITIVE", raising=False)
    got = rig.call("g6_improve_stop", folder=str(rig.root), reason="owner's review")
    assert (rig.root / "STOP").read_text(encoding="utf-8").splitlines()[0] == "owner's review"
    assert _code(got) == "G6_E_LOOP_STOP_ASKED" and check_envelope(got) == [] and rig.asked == []


def test_stop_is_safe_to_repeat_and_the_first_reason_stands(rig):
    rig.call("g6_improve_stop", folder=str(rig.root), reason="first")
    got = rig.call("g6_improve_stop", folder=str(rig.root), reason="second")
    assert (rig.root / "STOP").read_text(encoding="utf-8").splitlines()[0] == "first" and "first" in got["stop"]
    assert got["completion_state"] == "qualified-draft"


def test_stop_with_no_runner_says_it_has_stopped(rig):
    got = rig.call("g6_improve_stop", folder=str(rig.root))
    assert got["running"] is False and got["stopped"] is True and "no runner is alive" in got["message"]


def test_stop_reports_a_runner_that_has_not_gone_yet_and_ends_nothing(rig):
    with runner_lock(rig.root):
        got = rig.call("g6_improve_stop", folder=str(rig.root))
        assert got["running"] is True and got["stopped"] is False and "still alive" in got["message"]


def test_stop_kills_nothing_and_touches_no_container(rig, monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("stop reached outside the folder")

    monkeypatch.setattr(subprocess, "Popen", refused)
    monkeypatch.setattr(subprocess, "run", refused)
    monkeypatch.setattr(os, "kill", refused)
    monkeypatch.setattr(socket.socket, "connect", refused)
    assert improve.stop(str(rig.root))["stopped"] is True


def test_stop_still_works_with_a_broken_settings_file_and_broken_records(rig):
    rig.enrol()
    (rig.root / "settings.json").write_text("not json", encoding="utf-8")
    path = rig.root / CYCLE_LOG
    path.write_text(path.read_text(encoding="utf-8").replace(ENROLLED_BY, "Cy"), encoding="utf-8")
    assert rig.call("g6_improve_stop", folder=str(rig.root))["stopped"] is True and (rig.root / "STOP").exists()


def _set_allowance(rig: Rig, allowance: int) -> None:
    path = rig.root / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["windows"][0]["allowance"] = allowance
    path.write_text(json.dumps(settings), encoding="utf-8")


def _while_the_dialog_is_open(change):
    async def handler(message, response_type, params, context):
        change()
        return response_type(value="Ada")

    return handler


def test_a_larger_allowance_written_while_the_dialog_is_open_starts_nothing(rig):
    rig.enrol()
    got = rig.start(_while_the_dialog_is_open(lambda: _set_allowance(rig, 1_000_000_000)))
    assert "5000 a week" in rig.asked[0] and rig.spawned == []
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and got["completion_state"] == "blocked-escalated"
    assert "again" in got["next_action"] and check_envelope(got) == []
    rig.log.reload()
    assert rig.log.started() is None


def test_asking_again_shows_the_new_figure_and_that_is_what_starts(rig):
    rig.enrol()
    rig.start(_while_the_dialog_is_open(lambda: _set_allowance(rig, 7000)))
    rig.start()
    assert "7000 a week" in rig.asked[-1] and rig.spawned == [rig.root]


def test_a_stop_arriving_while_the_dialog_is_open_starts_nothing_and_stands(rig):
    rig.enrol()
    got = rig.start(_while_the_dialog_is_open(lambda: improve.stop(str(rig.root), "the owner said no")))
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_TERMS_CHANGED"
    assert "the owner said no" in stop_reason(rig.root)


def test_a_second_stop_while_the_dialog_is_open_is_not_lost_with_the_first(rig):
    rig.enrol()
    (rig.root / "STOP").write_text("an older stop\n", encoding="utf-8")
    got = rig.start(_while_the_dialog_is_open(lambda: improve.stop(str(rig.root), "and again")))
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_TERMS_CHANGED" and (rig.root / "STOP").is_file()


def test_a_pause_arriving_while_the_dialog_is_open_starts_nothing(rig):
    rig.enrol()
    got = rig.start(_while_the_dialog_is_open(lambda: CycleLog(rig.root / CYCLE_LOG).pause("three failed cycles in a row")))
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_TERMS_CHANGED"


def test_the_dialog_names_the_settings_and_the_start_records_them(rig):
    rig.enrol()
    rig.start()
    settings = digest_of(read_settings(rig.root))
    rig.log.reload()
    assert settings in rig.asked[0] and rig.log.started()["settings"] == settings


def test_a_stop_arriving_while_a_start_is_recorded_wins_over_it(rig, monkeypatch):
    rig.enrol()
    stopped = []
    real = CycleLog.start

    def interleaved(self, *args, **kwargs):
        recorded = real(self, *args, **kwargs)
        stopped.append(improve.stop(str(rig.root), "the owner changed their mind"))
        return recorded

    monkeypatch.setattr(CycleLog, "start", interleaved)
    got = improve.start(rig.root, "Ada")
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_START_STOPPED" and got["completion_state"] == "blocked-escalated"
    assert stopped[0]["stopped"] is True and "the owner changed their mind" in stop_reason(rig.root)
    monkeypatch.undo()
    assert not CycleLog(rig.root / CYCLE_LOG).start_in_force()


def test_a_stop_arriving_just_as_an_old_stop_is_removed_still_wins(rig, monkeypatch):
    rig.enrol()
    (rig.root / "STOP").write_text("an old stop\n", encoding="utf-8")
    real, reads = envelope.file_digest, []

    def a_stop_after_the_check(path):
        found = real(path)
        reads.append(found)
        if len(reads) == 1:                                              # read, then a stop, then the file goes
            improve.stop(str(rig.root), "a late stop")
        return found

    monkeypatch.setattr(envelope, "file_digest", a_stop_after_the_check)
    got = improve.start(rig.root, "Ada")
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_START_STOPPED" and (rig.root / "STOP").is_file()


def _while_the_start_is_recorded(rig, monkeypatch, act) -> None:
    real = CycleLog.start

    def interleaved(self, *args, **kwargs):
        recorded = real(self, *args, **kwargs)
        act()
        return recorded

    monkeypatch.setattr(CycleLog, "start", interleaved)


def test_a_stop_file_written_by_hand_during_a_start_survives_it(rig, monkeypatch):
    """It is in no count and no record: what keeps it is that nobody agreed to clear it."""
    rig.enrol()
    _while_the_start_is_recorded(rig, monkeypatch, lambda: (rig.root / "STOP").write_text("by hand\n", encoding="utf-8"))
    got = improve.start(rig.root, "Ada")
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_START_STOPPED" and "by hand" in stop_reason(rig.root)
    monkeypatch.undo()
    assert not CycleLog(rig.root / CYCLE_LOG).start_in_force()


def test_a_stop_file_changed_by_hand_during_a_start_survives_it(rig, monkeypatch):
    rig.enrol()
    (rig.root / "STOP").write_text("an old stop\n", encoding="utf-8")
    _while_the_start_is_recorded(rig, monkeypatch, lambda: (rig.root / "STOP").write_text("a new one\n", encoding="utf-8"))
    got = improve.start(rig.root, "Ada")
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_START_STOPPED" and "a new one" in stop_reason(rig.root)


def test_a_stop_that_no_record_took_survives_a_start_and_says_it_stopped(rig, monkeypatch):
    rig.enrol()
    real_open, stopped = builtins.open, []

    def no_count(path, *args, **kwargs):
        if Path(path).name == "stops.log":
            raise PermissionError("the count cannot be added to")
        return real_open(path, *args, **kwargs)

    def stopped_with_no_count():
        with monkeypatch.context() as broken:
            broken.setattr(builtins, "open", no_count)
            stopped.append(improve.stop(str(rig.root), "the owner said no"))

    _while_the_start_is_recorded(rig, monkeypatch, stopped_with_no_count)
    got = improve.start(rig.root, "Ada")
    assert stopped[0]["warning_card"]["code"] == improve.STOP_ASKED and stops_asked(rig.root) == 0
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_START_STOPPED" and "the owner said no" in stop_reason(rig.root)


def test_a_start_clears_the_stop_it_agreed_to_with_no_count_to_read(rig, monkeypatch):
    rig.enrol()
    improve.stop(str(rig.root), "the owner's review")
    monkeypatch.setattr(improve, "stops_asked", lambda root: 7)          # whatever a count says, it is not what decides
    assert _code(improve.start(rig.root, "Ada")) != "G6_E_LOOP_START_STOPPED" and not (rig.root / "STOP").exists()


def _a_stop_while_it_is_recorded(rig, monkeypatch) -> None:
    real = CycleLog.start

    def interleaved(self, *args, **kwargs):
        recorded = real(self, *args, **kwargs)
        improve.stop(str(rig.root), "a newer stop")
        return recorded

    monkeypatch.setattr(CycleLog, "start", interleaved)


def test_a_stop_count_that_cannot_be_read_refuses_the_start_so_no_stop_can_be_erased(rig, monkeypatch):
    rig.enrol()
    (rig.root / "stops.log").mkdir()                                     # a broken record, whatever this machine's rights
    _a_stop_while_it_is_recorded(rig, monkeypatch)
    offered = improve.asked(str(rig.root))
    got = improve.start(rig.root, "Ada")
    assert isinstance(offered, dict) and _code(offered) == "G6_E_LOOP_RECORDS_UNREADABLE"
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_RECORDS_UNREADABLE" and got["completion_state"] == "blocked-escalated"
    monkeypatch.undo()
    assert CycleLog(rig.root / CYCLE_LOG).started() is None


def test_a_stop_count_that_cannot_be_added_to_refuses_the_start_as_well(rig, monkeypatch):
    """A restart stands on the count, which would miss any stop that could not be counted, once its file had gone."""
    rig.enrol()
    (rig.root / "STOP").write_text("an old stop\n", encoding="utf-8")
    count = rig.root / "stops.log"
    count.write_text("an old stop\n", encoding="utf-8")
    count.chmod(stat.S_IREAD)
    _a_stop_while_it_is_recorded(rig, monkeypatch)
    try:
        got = improve.start(rig.root, "Ada")
    finally:
        count.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_RECORDS_UNREADABLE" and (rig.root / "STOP").is_file()


def test_a_count_that_breaks_after_the_check_is_never_recorded_as_what_a_start_saw(rig, monkeypatch):
    rig.enrol()
    monkeypatch.setattr(improve, "stops_asked", lambda root: -1)
    got = improve.start(rig.root, "Ada")
    assert rig.spawned == [] and got["completion_state"] == "blocked-escalated"
    monkeypatch.undo()
    assert CycleLog(rig.root / CYCLE_LOG).started() is None


def test_a_count_that_breaks_during_the_start_stops_it_and_keeps_the_stop(rig, monkeypatch):
    rig.enrol()
    real = CycleLog.start

    def broken_then_stopped(self, *args, **kwargs):
        recorded = real(self, *args, **kwargs)
        (rig.root / "stops.log").mkdir()
        improve.stop(str(rig.root), "a newer stop")
        return recorded

    monkeypatch.setattr(CycleLog, "start", broken_then_stopped)
    got = improve.start(rig.root, "Ada")
    assert rig.spawned == [] and _code(got) == "G6_E_LOOP_START_STOPPED" and "a newer stop" in stop_reason(rig.root)


def test_a_stop_that_cannot_add_to_the_count_during_a_start_survives_it(rig, monkeypatch):
    """The start's check passed, and then the count went read-only: a stop that said it stopped has to stay."""
    rig.enrol()
    (rig.root / "STOP").write_text("an old stop\n", encoding="utf-8")
    count = rig.root / "stops.log"
    count.write_text("an old stop\n", encoding="utf-8")
    real, stopped = CycleLog.start, []

    def read_only_then_stopped(self, *args, **kwargs):
        recorded = real(self, *args, **kwargs)
        count.chmod(stat.S_IREAD)
        stopped.append(improve.stop(str(rig.root), "the owner withdrew agreement"))
        return recorded

    monkeypatch.setattr(CycleLog, "start", read_only_then_stopped)
    try:
        got = improve.start(rig.root, "Ada")
    finally:
        count.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert stopped[0]["stopped"] is True and rig.spawned == [] and _code(got) == "G6_E_LOOP_START_STOPPED"
    assert (rig.root / "STOP").is_file() and stop_reason(rig.root)
    monkeypatch.undo()
    assert not CycleLog(rig.root / CYCLE_LOG).start_in_force()


def test_a_stop_with_no_count_to_write_to_still_stops_and_says_so(rig):
    rig.enrol()
    count = rig.root / "stops.log"
    count.write_text("one\n", encoding="utf-8")
    count.chmod(stat.S_IREAD)
    try:
        got = improve.stop(str(rig.root), "uncountable")
    finally:
        count.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert _code(got) == improve.STOP_ASKED and "uncountable" in got["stop"] and stops_asked(rig.root) == 1


def test_a_stop_that_cannot_change_a_stop_file_says_it_failed_since_a_start_could_clear_it(rig):
    rig.enrol()
    (rig.root / "STOP").write_text("an old stop\n", encoding="utf-8")
    (rig.root / "STOP").chmod(stat.S_IREAD)
    try:
        got = improve.stop(str(rig.root), "no room for this")
    finally:
        (rig.root / "STOP").chmod(stat.S_IREAD | stat.S_IWRITE)
    assert _code(got) == "G6_E_LOOP_STOP_FAILED" and got["completion_state"] == "blocked-escalated"


def test_no_stop_count_yet_is_no_reason_to_refuse_and_asking_writes_none(rig):
    rig.enrol()
    assert not isinstance(improve.asked(str(rig.root)), dict) and not (rig.root / "stops.log").exists()


def test_stop_ends_the_start_so_only_a_new_agreement_brings_it_back(rig):
    rig.enrol()
    rig.start()
    rig.log.reload()
    assert rig.log.start_in_force()
    rig.call("g6_improve_stop", folder=str(rig.root), reason="the owner's review")
    rig.log.reload()
    assert not rig.log.start_in_force()
    rig.start()
    rig.log.reload()
    assert rig.log.start_in_force()


def test_stop_reaches_a_live_runner_whose_settings_and_records_are_gone(rig):
    rig.enrol()
    with runner_lock(rig.root):
        (rig.root / "settings.json").unlink()
        (rig.root / CYCLE_LOG).unlink()
        got = rig.call("g6_improve_stop", folder=str(rig.root), reason="stop despite damaged records")
        assert (rig.root / "STOP").read_text(encoding="utf-8").splitlines()[0] == "stop despite damaged records"
        assert _code(got) == "G6_E_LOOP_STOP_ASKED" and got["running"] is True and got["stopped"] is False


def test_stop_reaches_a_folder_a_runner_once_held_with_nothing_else_left(rig):
    with runner_lock(rig.root):
        pass
    (rig.root / "settings.json").unlink()
    assert rig.call("g6_improve_stop", folder=str(rig.root))["stopped"] is True and (rig.root / "STOP").exists()


def test_status_reads_a_live_runner_whose_settings_and_records_are_gone(rig):
    rig.enrol()
    with runner_lock(rig.root):
        (rig.root / "settings.json").unlink()
        (rig.root / CYCLE_LOG).unlink()
        assert improve.status(str(rig.root))["running"] is True


def test_stop_on_a_folder_that_is_no_loops_writes_nothing_and_says_so(rig, tmp_path):
    (tmp_path / "plain").mkdir()
    got = rig.call("g6_improve_stop", folder=str(tmp_path / "plain"))
    assert _code(got) == "G6_E_LOOP_NOT_A_FOLDER" and list((tmp_path / "plain").iterdir()) == []


def test_a_stop_file_that_cannot_be_written_is_said(rig, monkeypatch):
    def full(*args, **kwargs):
        raise OSError("no space left")

    monkeypatch.setattr(improve, "ask_to_stop", full)
    got = rig.call("g6_improve_stop", folder=str(rig.root))
    assert _code(got) == "G6_E_LOOP_STOP_FAILED" and got["completion_state"] == "blocked-escalated"


def test_a_folder_left_empty_is_the_one_under_g6s_state_folder(rig, tmp_path):
    home = tmp_path / "home" / "improve"
    home.mkdir(parents=True)
    (home / "settings.json").write_text("{}", encoding="utf-8")
    assert rig.call("g6_improve_stop")["folder"] == str(home) and (home / "STOP").exists()


# Status ---------------------------------------------------------------------------------------------------------------

def _adoption(rig: Rig) -> Path:
    """The chain's row for the adoption ``_worked`` records, so the log and the chain agree."""
    path = rig.root / "workspace" / ADOPTIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    ChainLedger(path, validate=shadow._rules).append("adopt", "1", {
        "kind": "adopt", "version": 1, "base_version": 0, "by": shadow.ADOPTER, "scope": shadow.SCOPE, "at": NOW.isoformat(),
        "decision_digest": "d", "profile": {"prompt": "p"}, "profile_digest": "p"})
    return path


def _worked(rig: Rig) -> None:
    rig.enrol()
    _adoption(rig)
    rig.log.cycle({"at": NOW.isoformat(), "outcome": "adopted", "code": "G6_E_LOOP_ADOPTED", "reason": "it won",
                   "constraint": "wrong_answer", "basis": "b", "head_version": 1, "structure": "a regularity"})
    beat(rig.root, at=NOW.isoformat(), tick=1, state="waiting", code="G6_E_LOOP_BUDGET_WAIT", next_tick="later", pid=7,
         use=[{"provider": "claude", "day": 3, "used": 10.0, "target": 2142.0, "ceiling": 3000.0, "estimated": True,
               "account": "not read"}])
    digest.write(rig.root, NOW.date(), "# the page\n")


def test_status_says_where_the_loop_stands(rig):
    _worked(rig)
    got = rig.call("g6_improve_status", folder=str(rig.root))
    assert check_envelope(got) == [] and got["completion_state"] == "qualified-draft" and got["running"] is False
    assert got["last_heartbeat"]["pid"] == 7 and got["use_as_of"] == NOW.isoformat()
    [use] = got["use"]
    assert use["used"] == 10.0 and use["target"] == 2142.0 and use["ceiling"] == 3000.0 and use["estimated"] is True
    assert got["constraint"] == {"name": "wrong_answer", "basis": "b", "tick": 1}
    assert got["last_cycle"]["outcome"] == "adopted" and got["last_cycle"]["structure"] == "a regularity"
    assert got["digest"]["text"] == "# the page\n" and got["digest"]["path"].endswith("2026-10-28.md")
    assert got["pause"] is None and got["stop"] == "" and "Early alpha" in got["g6_status"]


def test_status_shows_a_live_runner(rig):
    with runner_lock(rig.root):
        assert rig.call("g6_improve_status", folder=str(rig.root))["running"] is True


def test_status_writes_nothing(rig):
    _worked(rig)
    before = _files(rig.root)
    improve.status(str(rig.root))
    assert _files(rig.root) == before


def test_status_on_a_folder_with_only_settings_makes_no_folder(rig, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "settings.json").write_text("{}", encoding="utf-8")
    got = improve.status(str(bare))
    assert got["cycles"] == 0 and got["last_cycle"] is None and got["digest"] is None and got["use"] == []
    assert [p.name for p in bare.iterdir()] == ["settings.json"]


def test_status_says_a_pause_and_its_reason(rig):
    _worked(rig)
    rig.log.pause("three failed cycles in a row")
    assert improve.status(str(rig.root))["pause"] == {"code": "G6_E_LOOP_PAUSED", "reason": "three failed cycles in a row"}


def test_status_shows_what_holds_the_ticks_even_when_no_pause_was_written(rig):
    _worked(rig)
    held = {"code": "G6_E_LOOP_FROZEN_CHANGED", "reason": "changed since a person enrolled this domain: cases"}
    beat(rig.root, at=NOW.isoformat(), pause=held)
    assert improve.status(str(rig.root))["pause"] == held


def test_a_pause_a_person_lifted_is_not_read_back_from_an_old_heartbeat(rig):
    _worked(rig)
    beat(rig.root, at=NOW.isoformat(), pause={"code": "G6_E_LOOP_PAUSED", "reason": "gone"})
    assert improve.status(str(rig.root))["pause"] is None


def test_status_says_the_stop_in_place(rig):
    (rig.root / "STOP").write_text("the owner's review\n", encoding="utf-8")
    assert "the owner's review" in improve.status(str(rig.root))["stop"]


def test_status_says_a_generation_the_log_saw_and_the_chain_lost(rig):
    _worked(rig)
    (rig.root / "workspace" / ADOPTIONS).write_text("", encoding="utf-8")
    got = improve.status(str(rig.root))
    assert got["pause"]["code"] == "G6_E_LOOP_RECORDS_UNREADABLE" and "generation 1" in got["pause"]["reason"]


def test_status_says_records_that_do_not_check_out_and_does_not_raise(rig):
    _worked(rig)
    path = rig.root / CYCLE_LOG
    path.write_text(path.read_text(encoding="utf-8").replace("wrong_answer", "right_answer"), encoding="utf-8")
    got = improve.status(str(rig.root))
    assert got["pause"]["code"] == "G6_E_LOOP_RECORDS_UNREADABLE" and got["last_cycle"] is None


def test_status_shows_the_current_generation_and_the_recent_lineage(rig, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).parents[1] / "metaharness" / "_loop_domains.py")
    domains = sys.modules.setdefault(spec.name, importlib.util.module_from_spec(spec))
    if not hasattr(domains, "WordProblems"):
        spec.loader.exec_module(domains)
    from g6.metaharness.loop import LoopSettings, ShadowLoop
    from g6.metaharness.method import Method
    from g6.self_maintenance.pacing import ProviderWindow

    method = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=method, audit_per_tick=60)
    loop = ShadowLoop(domains.WordProblems(tmp_path), rig.root, settings,
                      proposer=domains.proposes({"prompt": domains.WORKING}), now=lambda: NOW, clock=domains.Clock())
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    assert loop.tick()["outcome"] == "adopted"
    got = rig.call("g6_improve_status", folder=str(rig.root))
    assert got["generation"] == 1
    [one] = got["lineage"]
    assert one["generation"] == 1 and one["parent"] == 0 and one["measured"]["after"] > one["measured"]["before"]


def test_no_status_reply_says_more_than_measured(rig):
    _worked(rig)
    said = json.dumps(improve.status(str(rig.root))).lower().replace('"verified": false', "")   # the envelope's own flag
    assert "verified" not in said and "proved" not in said and "experimental" not in said


# A real process -------------------------------------------------------------------------------------------------------

def test_a_real_runner_that_cannot_open_its_folder_ends_and_says_why(rig, monkeypatch):
    """The whole path of ``_spawn``: a detached ``python -P -m g6.metaharness.loop run``, here on a domain file that
    declares no ``LOOP``, so it ends at once and leaves word."""
    rig.enrol()
    monkeypatch.undo()
    monkeypatch.setenv("PYTHONPATH", str(Path(improve.__file__).resolve().parents[2]))
    monkeypatch.setattr(improve, "START_WAIT", 120.0)
    got = improve.start(rig.root, "Ada")
    assert _code(got) == "G6_E_LOOP_START_FAILED" and "must define LOOP" in got["message"]
    assert (rig.root / improve.RUNNER_LOG).is_file() and got["running"] is False
