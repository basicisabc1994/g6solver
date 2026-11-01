# SPDX-License-Identifier: Apache-2.0
"""The loop's controls over MCP (``g6.mcp.improve``): start goes through the host's permission and the client's dialog
and nowhere else, stop always works and kills nothing, and status only reads.

The first groups stand in for the process start and fake nothing else; the last group starts a real process. FakeLLM is
the only model anywhere here, and no test reaches a provider."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.invariants import check_envelope

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

from g6.mcp import build_server, improve  # noqa: E402
from g6.self_maintenance import digest  # noqa: E402
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, beat, file_digest, runner_lock  # noqa: E402

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
    assert (rig.root / "STOP").read_text(encoding="utf-8") == "owner's review\n"
    assert _code(got) == "G6_E_LOOP_STOP_ASKED" and check_envelope(got) == [] and rig.asked == []


def test_stop_is_safe_to_repeat_and_the_first_reason_stands(rig):
    rig.call("g6_improve_stop", folder=str(rig.root), reason="first")
    got = rig.call("g6_improve_stop", folder=str(rig.root), reason="second")
    assert (rig.root / "STOP").read_text(encoding="utf-8") == "first\n" and "first" in got["stop"]
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

def _worked(rig: Rig) -> None:
    rig.enrol()
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
