# SPDX-License-Identifier: Apache-2.0
"""Enrolling, lifting a pause and taking a generation back are a person's, and a person gives them only in the client's
dialog (``g6_improve_enrol``, ``g6_improve_resume``, ``g6_improve_demote``), bound to what the dialog showed. The command
line records none of them and says where they are done. The stop needs no dialog. FakeLLM only; the act runs in this
process except in the last test, which starts the real one."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures('declared_mcp_grants')   # replies go to an identified, granted client

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

from g6.mcp import build_server, consent, improve  # noqa: E402
from g6.metaharness import runner  # noqa: E402
from g6.metaharness.runner import main, open_loop  # noqa: E402
from g6.metaharness.shadow import lineage  # noqa: E402
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, file_digest  # noqa: E402

RIG = Path(__file__).resolve().parents[1] / "metaharness" / "_loop_domains.py"
START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
DOMAIN = """
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
CODES = {"unavailable": "G6_E_CONSENT_NO_DIALOG", "declined": "G6_E_CONSENT_DECLINED",
         "cancelled": "G6_E_CONSENT_CANCELLED", "invalid": "G6_E_CONSENT_NO_NAME"}
TERMINAL = ("g6.metaharness.loop enrol", "g6.metaharness.loop resume", "g6.metaharness.loop demote", "--as",
            "at your terminal", "at their own terminal")


def data(reply) -> dict:
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


async def named(message, response_type, params, context):
    return response_type(value="Ada")


async def declined(message, response_type, params, context):
    return ElicitResult(action="decline")


async def cancelled(message, response_type, params, context):
    return ElicitResult(action="cancel")


async def nameless(message, response_type, params, context):
    return response_type(value=" ")


HANDLERS = {"unavailable": None, "declined": declined, "cancelled": cancelled, "invalid": nameless}
_real_request = consent.request_consent


class Rig:
    """A loop's folder on the word-problems domain, a fresh G6 home and a server; the act runs in this process."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.improve")
        self.tmp, self.domain_file, self.root = tmp_path, tmp_path / "domain.py", tmp_path / "loop"
        self.domain_file.write_text(DOMAIN.format(rig=str(RIG)), encoding="utf-8")
        self.root.mkdir()
        (self.root / "settings.json").write_text(json.dumps({
            "domain_file": str(self.domain_file), "read_account": False,
            "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": START.isoformat()}]}), encoding="utf-8")
        self.asked: list[str] = []
        self.acts: list[dict] = []
        self.server = build_server()

        def in_this_process(root: Path, settings: dict, request: dict) -> dict:
            self.acts.append(request)
            return runner.act(request)

        monkeypatch.setattr(improve, "_run_act", in_this_process)

    def call(self, tool: str, handler=named, **arguments) -> dict:
        async def seen(message, response_type, params, context):
            self.asked.append(message)
            return await handler(message, response_type, params, context)

        async def flow():
            client = Client(self.server, elicitation_handler=seen) if handler else Client(self.server)
            async with client:
                return data(await client.call_tool(tool, {"folder": str(self.root), **arguments}))

        return asyncio.run(flow())

    def log(self) -> CycleLog:
        return CycleLog(self.root / CYCLE_LOG)

    def enrolled(self, by: str = "Bo"):
        loop, _ = open_loop(self.root, now=lambda: START)
        assert loop.enrol(by)["completion_state"] == "qualified-draft"
        return loop

    def files(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()}


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def _no_terminal(reply: dict) -> None:
    text = json.dumps(reply)
    assert not [word for word in TERMINAL if word in text], text


# The tools ---------------------------------------------------------------------------------------------------------------

def test_each_act_is_a_tool_that_takes_no_answer(rig):
    """Fails on 6e7b7ed: there was no such tool, so a person enrolled at a terminal."""
    tools = asyncio.run(rig.server.get_tools())
    assert set(tools["g6_improve_enrol"].parameters["properties"]) == {"folder"}
    assert set(tools["g6_improve_resume"].parameters["properties"]) == {"folder"}
    assert set(tools["g6_improve_demote"].parameters["properties"]) == {"folder", "generation"}


@pytest.mark.parametrize("tool", ["g6_improve_enrol", "g6_improve_resume"])
def test_the_host_must_allow_it_and_nothing_is_asked_otherwise(rig, monkeypatch, tool):
    monkeypatch.setenv("G6_MCP_SENSITIVE", "workforce.mission")
    got = rig.call(tool)
    assert _code(got) == "G6_E_SENSITIVE_COMPONENT_DENIED" and rig.asked == [] and rig.acts == []


# Enrol -------------------------------------------------------------------------------------------------------------------

def test_the_person_enrols_in_the_dialog_and_reads_the_file_that_will_run(rig):
    got = rig.call("g6_improve_enrol")
    [question] = rig.asked
    assert str(rig.root) in question and str(rig.domain_file) in question and file_digest(rig.domain_file) in question
    assert "not authenticated" in question and "as you" in question
    assert _code(got) == "G6_E_LOOP_ENROLLED", got
    assert rig.log().enrolled()["by"] == "Ada" and rig.log().enrolled()["frozen"]["domain_file"] == file_digest(rig.domain_file)


@pytest.mark.parametrize("status", list(CODES))
def test_no_dialog_a_decline_a_cancel_or_no_name_enrols_nothing(rig, status):
    before = rig.files()
    got = rig.call("g6_improve_enrol", HANDLERS[status])
    assert _code(got) == CODES[status] and got["consent_status"] == status and got["completion_state"] == "blocked-escalated"
    assert rig.acts == [] and rig.files() == before
    _no_terminal(got)


def _while_asked(change):
    async def handler(message, response_type, params, context):
        change()
        return response_type(value="Ada")

    return handler


def test_a_domain_file_changed_while_the_dialog_was_open_is_not_enrolled_and_does_not_run(rig):
    marker = rig.tmp / "ran"
    got = rig.call("g6_improve_enrol", _while_asked(lambda: rig.domain_file.write_text(
        f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")))
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and not marker.exists() and rig.acts == []
    assert not (rig.root / CYCLE_LOG).is_file()


def test_settings_changed_while_the_dialog_was_open_are_not_enrolled(rig):
    path = rig.root / "settings.json"
    grown = json.loads(path.read_text(encoding="utf-8"))
    grown["windows"][0]["allowance"] = 2_000_000
    got = rig.call("g6_improve_enrol", _while_asked(lambda: path.write_text(json.dumps(grown), encoding="utf-8")))
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and rig.acts == []


def test_a_file_swapped_after_the_last_check_and_before_the_act_reads_it_does_not_run(rig, monkeypatch):
    """The act runs the bytes the dialog named, by digest, or none: a swap in the gap is refused before it runs."""
    marker = rig.tmp / "ran"

    def swapped(root, settings, request):
        rig.domain_file.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        return runner.act(request)

    monkeypatch.setattr(improve, "_run_act", swapped)
    got = rig.call("g6_improve_enrol")
    assert got["completion_state"] == "blocked-escalated" and not marker.exists()
    assert not (rig.root / CYCLE_LOG).is_file() or rig.log().enrolled() is None


def test_an_act_that_cannot_record_says_so_and_a_new_agreement_enrols_once(rig, monkeypatch):
    """A stop between the shadow store's confirmation and the enrolment's row: nothing counts, and the next one does."""
    real = CycleLog.enrol

    def lost(self, *args, **kwargs):
        raise OSError("the disk went away")

    monkeypatch.setattr(CycleLog, "enrol", lost)
    got = rig.call("g6_improve_enrol")
    assert got["completion_state"] == "blocked-escalated"
    assert not (rig.root / CYCLE_LOG).is_file() or rig.log().enrolled() is None
    monkeypatch.setattr(CycleLog, "enrol", real)
    assert _code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_ENROLLED" and len(rig.log().enrolments()) == 1


def test_an_act_whose_process_says_nothing_readable_is_a_failure_not_an_enrolment(rig, monkeypatch):
    monkeypatch.setattr(improve, "_run_act", lambda root, settings, request: improve._read_act(1, "Traceback: boom\n"))
    got = rig.call("g6_improve_enrol")
    assert _code(got) == "G6_E_LOOP_ACT_FAILED" and got["completion_state"] == "blocked-escalated"


def test_malformed_settings_and_records_are_refused_before_anything_is_asked(rig):
    (rig.root / "settings.json").write_text("{not json", encoding="utf-8")
    assert _code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_BAD_SETTINGS" and rig.asked == []
    rig2_settings = {"domain_file": str(rig.domain_file), "read_account": False,
                     "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": START.isoformat()}]}
    (rig.root / "settings.json").write_text(json.dumps(rig2_settings), encoding="utf-8")
    rig.enrolled()
    path = rig.root / CYCLE_LOG
    path.write_text(path.read_text(encoding="utf-8").replace('"Bo"', '"Cy"'), encoding="utf-8")
    assert _code(rig.call("g6_improve_enrol")) == "G6_E_LOOP_RECORDS_UNREADABLE" and rig.asked == []


def test_a_start_with_no_enrolment_names_the_dialog_and_no_terminal(rig):
    """Fails on 6e7b7ed: the refusal sent the person to ``python -P -m g6.metaharness.loop enrol ... --as``."""
    got = rig.call("g6_improve_start")
    assert _code(got) == "G6_E_LOOP_NOT_ENROLLED" and "g6_improve_enrol" in got["next_action"] and rig.asked == []
    _no_terminal(got)


# Resume ------------------------------------------------------------------------------------------------------------------

def test_a_pause_is_lifted_in_the_dialog_that_names_it(rig):
    rig.enrolled().log.pause("three failed cycles in a row")
    got = rig.call("g6_improve_resume")
    [question] = rig.asked
    assert "three failed cycles in a row" in question and "not authenticated" in question
    assert _code(got) == "G6_E_LOOP_RESUMED" and rig.log().paused() == ""


@pytest.mark.parametrize("status", list(CODES))
def test_no_agreement_leaves_the_pause(rig, status):
    rig.enrolled().log.pause("three failed cycles in a row")
    got = rig.call("g6_improve_resume", HANDLERS[status])
    assert _code(got) == CODES[status] and rig.log().paused() and rig.acts == []


def test_nothing_to_lift_and_no_enrolment_are_refused_before_anything_is_asked(rig):
    got = rig.call("g6_improve_resume")
    assert _code(got) == "G6_E_LOOP_NOT_ENROLLED" and "g6_improve_enrol" in got["next_action"]
    rig.enrolled()
    assert _code(rig.call("g6_improve_resume")) == "G6_E_LOOP_NOT_PAUSED" and rig.asked == []


def test_a_new_reason_to_pause_while_the_dialog_was_open_lifts_nothing(rig):
    loop = rig.enrolled()
    loop.log.pause("three failed cycles in a row")
    got = rig.call("g6_improve_resume", _while_asked(lambda: loop.log.pause("a sealed case was read")))
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and rig.log().paused()


def test_a_domain_file_changed_since_enrolment_is_refused_before_it_could_run(rig):
    rig.enrolled().log.pause("three failed cycles in a row")
    marker = rig.tmp / "ran"
    rig.domain_file.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    got = rig.call("g6_improve_resume")
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and rig.asked == [] and not marker.exists()


def test_the_stop_is_not_consent_it_needs_no_dialog_and_a_resume_does_not_lift_it(rig, monkeypatch):
    rig.enrolled().log.pause("three failed cycles in a row")

    async def never(*args, **kwargs):
        raise AssertionError("a stop asked for consent")

    monkeypatch.setattr(consent, "request_consent", never)
    stopped = rig.call("g6_improve_stop", None, reason="enough")
    assert _code(stopped) == improve.STOP_ASKED and (rig.root / "STOP").is_file()
    monkeypatch.setattr(consent, "request_consent", _real_request)
    assert _code(rig.call("g6_improve_resume")) == "G6_E_LOOP_RESUMED" and (rig.root / "STOP").is_file()


# Demote ------------------------------------------------------------------------------------------------------------------

def _adopted(rig: Rig) -> None:
    loop = rig.enrolled()
    assert open_loop(rig.root, now=lambda: START + timedelta(days=2))[0].tick()["outcome"] == "adopted"
    assert loop is not None


def test_a_generation_is_taken_back_in_the_dialog_that_names_it(rig):
    _adopted(rig)
    got = rig.call("g6_improve_demote", generation=1)
    [question] = rig.asked
    assert "generation 1" in question.lower() and "not authenticated" in question
    assert _code(got) == "G6_E_LOOP_DEMOTED" or got["completion_state"] == "qualified-draft", got
    assert lineage(rig.root / "workspace")["recent"][-1]["asked_by"] == "Ada"


def test_a_generation_that_is_not_the_head_is_refused_before_anything_is_asked(rig):
    _adopted(rig)
    got = rig.call("g6_improve_demote", generation=5)
    assert got["completion_state"] == "blocked-escalated" and rig.asked == [] and rig.acts == []


def test_a_head_that_moved_while_the_dialog_was_open_is_not_taken_back(rig):
    _adopted(rig)

    def moved():
        open_loop(rig.root, now=lambda: START)[0].demote(1, "Cy")

    got = rig.call("g6_improve_demote", _while_asked(moved), generation=1)
    # refused before the act: generation 1 is no longer the head the person read, generation 2 is. What they agreed to
    # is compared first once they answer, so the moved head is a changed agreement (review cons4's finding 3), where
    # this once expected the check that runs before any dialog, G6_E_LOOP_NOT_DEMOTABLE.
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and rig.acts == []
    assert len(lineage(rig.root / "workspace")["recent"]) == 2


def test_a_head_that_moved_after_the_servers_check_is_not_taken_back_by_the_act(rig, monkeypatch):
    """The act's own check, under the folder's lock: the chain moved between the server's check and the act."""
    _adopted(rig)

    def moved_first(root, settings, request):
        open_loop(rig.root, now=lambda: START)[0].demote(1, "Cy")
        return runner.act(request)

    monkeypatch.setattr(improve, "_run_act", moved_first)
    got = rig.call("g6_improve_demote", generation=1)
    assert _code(got) == "G6_E_LOOP_TERMS_CHANGED" and len(lineage(rig.root / "workspace")["recent"]) == 2


# The command line --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv, tool", [(["enrol"], "g6_improve_enrol"), (["resume"], "g6_improve_resume"),
                                        (["demote", "1"], "g6_improve_demote")])
def test_the_command_line_records_nothing_even_at_a_terminal_with_the_name_typed(rig, monkeypatch, capsys, argv, tool):
    """Fails on 6e7b7ed: at a terminal, with the domain's name typed back, the command line enrolled."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "word-problems")
    before = rig.files()
    assert main([argv[0], str(rig.root), *argv[1:], "--as", "Ada"]) == 1
    said = capsys.readouterr().err
    assert tool in said and "client session over MCP" in said and rig.files() == before
    assert main([argv[0], str(rig.root), *argv[1:]]) == 1               # no name at all: the same answer, not a usage error


def test_the_days_page_names_the_dialog_to_lift_a_pause(rig):
    """Fails on 6e7b7ed: the page said to lift it at a terminal with ``resume --as``."""
    loop = rig.enrolled()
    loop.log.pause("three failed cycles in a row")
    reply = loop.tick()
    held = runner.Runner(loop, now=lambda: START, sleep=lambda s: None)
    held.last = reply
    decisions = " ".join(held._decisions())
    assert "g6_improve_resume" in decisions and "--as" not in decisions and "terminal" not in decisions


def test_a_tick_refused_for_no_enrolment_or_a_pause_names_the_tools(rig):
    loop, _ = open_loop(rig.root, now=lambda: START)
    assert "g6_improve_enrol" in loop.tick()["next_action"]
    loop.enrol("Bo")
    loop.log.pause("three failed cycles in a row")
    assert "g6_improve_resume" in loop.tick()["next_action"]


# A real process ----------------------------------------------------------------------------------------------------------

def test_a_real_act_runs_on_the_runners_python_and_enrols(rig, monkeypatch):
    """The whole path of ``_run_act``: the interpreter the runner would use, its request on stdin, its reply on stdout."""
    monkeypatch.undo()
    monkeypatch.setenv("G6_HOME", str(rig.tmp / "home"))
    monkeypatch.setenv("PYTHONPATH", str(Path(improve.__file__).resolve().parents[2]))
    found = improve.asked_act("enrol", str(rig.root))
    root, _, shown = found
    got = improve.act("enrol", root, "Ada", shown)
    assert _code(got) == "G6_E_LOOP_ENROLLED", got
    assert CycleLog(rig.root / CYCLE_LOG).enrolled()["by"] == "Ada"
    assert importlib.util.find_spec("g6") is not None
