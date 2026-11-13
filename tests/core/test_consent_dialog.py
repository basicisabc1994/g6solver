# SPDX-License-Identifier: Apache-2.0
"""A person's consent comes only through the client's dialog (``g6.mcp.consent``): no terminal fallback, no command line,
no setting and no agent answer. A client with no dialog, a decline, a cancel, an agreement with no name and a digest that
changed are each refused with a code of their own and a reply that says what the person can do. FakeLLM only."""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures('declared_mcp_grants')   # replies go to an identified, granted client

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

import g6  # noqa: E402
from g6.cli import main as g6_main  # noqa: E402
from g6.invariants import check_envelope  # noqa: E402
from g6.mcp import build_server  # noqa: E402
from g6.mcp.consent import Consent, refusal, request_consent  # noqa: E402
from g6.metaharness.surface import Surface  # noqa: E402

SPEC = {"name": "consent", "task": "Label {input}",
        "cases": [{"input": "red", "expected": "warm"}, {"input": "blue", "expected": "cool"}],
        "baseline_code": {"main.py": "def solve(task):\n    return 'unknown'\n"}}
#: Each way a dialog can end without a person's agreement, and the code that says so.
CODES = {"unavailable": "G6_E_CONSENT_NO_DIALOG", "declined": "G6_E_CONSENT_DECLINED",
         "cancelled": "G6_E_CONSENT_CANCELLED", "invalid": "G6_E_CONSENT_NO_NAME"}
#: What a reply must never point the person or the agent to: another way to agree.
ELSEWHERE = ("g6 workflow confirm", "g6 workflow approve", "--as", "terminal", "g6.metaharness.loop enrol",
             "g6.metaharness.loop resume", "python -P -m", "Python process")


async def declined(message, response_type, params, context):
    return ElicitResult(action="decline")


async def cancelled(message, response_type, params, context):
    return ElicitResult(action="cancel")


async def nameless(message, response_type, params, context):
    return response_type(value="   ")


HANDLERS = {"unavailable": None, "declined": declined, "cancelled": cancelled, "invalid": nameless}


def _says(reply: dict) -> str:
    return json.dumps(reply)


def _nowhere_else(reply: dict) -> None:
    text = _says(reply)
    assert not [word for word in ELSEWHERE if word in text], text


def _surface(tmp_path, monkeypatch) -> tuple[Surface, str]:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    surface = Surface(workflow_root=tmp_path / "workflows")
    return surface, surface.start(SPEC)["workflow_digest"]


def _confirm(surface: Surface, digest: str, handler) -> dict:
    async def run():
        options = {"elicitation_handler": handler} if handler else {}
        async with Client(build_server(surface), **options) as client:
            reply = await client.call_tool("metaharness_confirm", {"workflow": "consent", "workflow_digest": digest})
            return json.loads(reply.content[0].text)

    return asyncio.run(run())


# The one refusal -------------------------------------------------------------------------------------------------------

def test_each_way_a_dialog_ends_without_agreement_has_a_code_of_its_own():
    """Fails on 6e7b7ed: every one of them was G6_E_META_APPROVAL_REQUIRED."""
    replies = {status: refusal(Consent(status)) for status in CODES}
    assert {status: reply["warning_card"]["code"] for status, reply in replies.items()} == CODES
    for status, reply in replies.items():
        assert reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
        assert reply["consent_status"] == status                    # the persisted field stays as it was
        _nowhere_else(reply)
        assert "no other way" in reply["next_action"], reply["next_action"]


def test_a_client_with_no_dialog_is_told_what_the_person_can_do():
    reply = refusal(Consent("unavailable"))
    said = reply["next_action"].lower()
    assert "client" in said and "dialog" in said and "session" in said, said


def test_a_decline_or_a_cancel_tells_the_agent_to_ask_the_person():
    for status in ("declined", "cancelled"):
        assert "ask the person" in refusal(Consent(status))["next_action"].lower()


def test_an_agreement_with_no_name_says_the_name_is_what_counts():
    assert "name" in refusal(Consent("invalid"))["next_action"].lower()


def test_what_did_not_happen_is_said_before_what_the_person_can_do():
    reply = refusal(Consent("declined"), nothing="Nothing started.")
    assert reply["next_action"].startswith("Nothing started.") and "ask the person" in reply["next_action"].lower()


def test_a_session_object_with_no_dialog_is_no_dialog_and_never_an_answer():
    assert asyncio.run(request_consent(SimpleNamespace(), "Start?")).status == "unavailable"


# metaharness_confirm -----------------------------------------------------------------------------------------------------

def test_confirm_with_no_dialog_offers_no_terminal_and_confirms_nothing(tmp_path, monkeypatch):
    """Fails on 6e7b7ed: the reply sent the person to ``g6 workflow confirm ... --as``."""
    surface, digest = _surface(tmp_path, monkeypatch)
    reply = _confirm(surface, digest, None)
    assert reply["consent_status"] == "unavailable" and reply["warning_card"]["code"] == CODES["unavailable"]
    _nowhere_else(reply)
    assert surface.inspect("consent")["confirmed"] is None


@pytest.mark.parametrize("status", ["declined", "cancelled", "invalid"])
def test_confirm_refuses_each_end_of_the_dialog_with_its_own_code(tmp_path, monkeypatch, status):
    surface, digest = _surface(tmp_path, monkeypatch)
    reply = _confirm(surface, digest, HANDLERS[status])
    assert reply["warning_card"]["code"] == CODES[status] and reply["consent_status"] == status
    assert check_envelope(reply) == []
    _nowhere_else(reply)
    assert surface.inspect("consent")["confirmed"] is None


def test_a_digest_that_changed_before_the_dialog_is_refused_with_its_own_code(tmp_path, monkeypatch):
    surface, _ = _surface(tmp_path, monkeypatch)
    asked = []

    async def handler(message, response_type, params, context):
        asked.append(message)
        return response_type(value="Ada")

    reply = _confirm(surface, "0" * 64, handler)
    assert reply["warning_card"]["code"] == "G6_E_META_WORKFLOW_CHANGED" and reply["consent_status"] == "workflow_changed"
    assert asked == [] and surface.inspect("consent")["confirmed"] is None
    _nowhere_else(reply)


def test_a_workflow_that_changed_while_the_dialog_was_open_is_not_confirmed(tmp_path, monkeypatch):
    surface, digest = _surface(tmp_path, monkeypatch)
    spec = tmp_path / "workflows" / "consent" / "workflow.json"

    async def changed_while_asked(message, response_type, params, context):
        held = json.loads(spec.read_text(encoding="utf-8"))
        spec.write_text(json.dumps({**held, "task": "Label {input} at once"}), encoding="utf-8")
        return response_type(value="Ada")

    reply = _confirm(surface, digest, changed_while_asked)
    assert reply["warning_card"]["code"] == "G6_E_META_WORKFLOW_CHANGED", reply
    assert surface.inspect("consent")["confirmed"] is None


def test_the_person_confirms_in_the_dialog_bound_to_the_digest_they_read(tmp_path, monkeypatch):
    surface, digest = _surface(tmp_path, monkeypatch)
    asked = []

    async def named(message, response_type, params, context):
        asked.append(message)
        return response_type(value="Ada")

    reply = _confirm(surface, digest, named)
    assert reply["warning_card"]["code"] == "G6_E_META_CONFIRMED" and digest in asked[0]


def test_the_library_asks_for_no_terminal_when_no_name_came_from_a_dialog(tmp_path, monkeypatch):
    """Fails on 6e7b7ed: ``Surface.confirm`` with no answer named the terminal command."""
    surface, digest = _surface(tmp_path, monkeypatch)
    reply = surface.confirm("consent", digest, answer=None)
    assert reply["completion_state"] == "blocked-escalated" and "metaharness_confirm" in reply["next_action"]
    _nowhere_else(reply)


def test_no_tool_takes_an_argument_that_could_answer_for_the_person(tmp_path, monkeypatch):
    surface, _ = _surface(tmp_path, monkeypatch)
    answers = {"who", "name", "answer", "approver", "approved", "accepted_by", "confirmed_by", "asked_by", "person",
               "consent", "by", "as", "agreed", "decided_by"}
    for name, tool in asyncio.run(build_server(surface).get_tools()).items():
        assert not answers & set(tool.parameters.get("properties", {})), name


# The command line ----------------------------------------------------------------------------------------------------------

def _in_the_project(tmp_path, monkeypatch) -> str:
    """A workflow where ``g6 workflow`` finds it: the project's own ``.g6`` folder, as the command line reads it."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return Surface().start(SPEC)["workflow_digest"]


def test_the_command_line_confirms_nothing_and_says_it_is_done_in_the_dialog(tmp_path, monkeypatch, capsys):
    """Fails on 6e7b7ed: ``g6 workflow confirm <name> <digest> --as Ada`` confirmed, with no dialog and no terminal check."""
    digest = _in_the_project(tmp_path, monkeypatch)
    assert g6_main(["workflow", "confirm", "consent", digest, "--as", "Ada"]) == 1
    reply = json.loads(capsys.readouterr().out)
    assert reply["warning_card"]["code"] == "G6_E_CONSENT_DIALOG_ONLY" and "metaharness_confirm" in reply["next_action"]
    assert check_envelope(reply) == [] and Surface().inspect("consent")["confirmed"] is None


def test_the_command_line_approves_nothing_and_says_it_is_done_in_the_dialog(tmp_path, monkeypatch, capsys):
    _in_the_project(tmp_path, monkeypatch)
    assert g6_main(["workflow", "approve", "consent", "d" * 64, "--candidate", "c" * 64, "--as", "Ada"]) == 1
    reply = json.loads(capsys.readouterr().out)
    assert reply["warning_card"]["code"] == "G6_E_CONSENT_DIALOG_ONLY" and "metaharness_approve" in reply["next_action"]


def test_the_command_line_still_inspects(tmp_path, monkeypatch, capsys):
    _in_the_project(tmp_path, monkeypatch)
    assert g6_main(["workflow", "inspect", "consent"]) == 0


# The class, at its root ----------------------------------------------------------------------------------------------------

def _calls(path: Path) -> list[str]:
    """Each call in ``path`` to the built-in ``input`` or to an ``isatty`` method: a terminal asked for a person's answer."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "input":
                found.append(f"{path.name}:{node.lineno} input()")
            if isinstance(func, ast.Attribute) and func.attr == "isatty":
                found.append(f"{path.name}:{node.lineno} isatty()")
    return found


def test_no_consent_surface_asks_a_terminal_for_an_answer():
    """Fails on 6e7b7ed: ``g6.metaharness.runner`` read the domain's name typed back at a terminal with ``input()``."""
    source = Path(g6.__file__).resolve().parent
    found = [call for folder in ("mcp", "metaharness", "cli") for path in sorted((source / folder).rglob("*.py"))
             for call in _calls(path)]
    assert found == []
