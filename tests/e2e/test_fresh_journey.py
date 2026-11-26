# SPDX-License-Identifier: Apache-2.0
"""A fresh person's first session with G6 over MCP: the 0.3.0 beta gate's end-to-end journey.

One person, one repository, G6's own MCP server built in process (``fastmcp.Client(build_server())``), a client that
names itself Claude Code, and a G6_HOME that holds nothing: no grant, no sensitive op allowed, no model, no record. No
grant fixture of tests/conftest.py is used. A scripted person answers every dialog the client is asked to show, and
each dialog is logged and checked. The person's model is a FakeLLM; G6_CLAUDE and G6_CODEX name a program that does
not exist, so no real model or host program can start. The agent knows only what G6 tells it: each call after the
first is built from an earlier reply (the server's instructions, a tool list, a call template, a refusal's repair
block or the source identifiers a withheld reply names), never from knowledge of G6 where the server offers a template.

The journey, as the release candidate ships it:

1. The server's instructions say when to reach for G6, and every tool they name is listed.
2. ``g6_select`` answers a client that granted nothing (built-in components are exempt from grants) and ranks a review
   tool in its top two for "review my patch before I say done".
3. A component op's reply is withheld with ``G6_E_CLOUD_CONSENT_REQUIRED`` and a repair block that reroutes to
   ``g6_cloud_grant``; ``g6_select`` given that code reroutes there too. The person accepts each grant in the
   client's dialog, and the same call, made again, is answered.
6. ``g6_review_patch`` on a fresh server is refused with ``G6_E_SENSITIVE_COMPONENT_DENIED``: the reply names the op
   and says the person decides. The person then allows the op the selector named (``needs.sensitive``) where the
   server is registered, and turns the workflow record on.
4. A tiny git repository: a function, its tests, and an agent's patch that breaks a stated requirement. Where an
   operating-system boundary can confine the run, the review reports ``defect_detected`` with the command that
   reproduces it; where none can, it is refused with ``G6_E_ISOLATION_UNAVAILABLE`` and the reproduced-defect test
   skips, naming the backend (on CI's Linux jobs, with ``G6_EXPECT_BACKEND=bubblewrap``, it fails instead).
5. A correct patch is ``qualified-draft`` at best, never ``verified``.
7. ``g6_workflow_export`` reads back the session's recorded events once the person shares the record.

Not in 0.3.0 (the release candidate lacks these, so the journey records them rather than asserting them):

- The workflow record on by default (owner ruling 2026-11-25): it is off until the person sets
  ``G6_WORKFLOW_RECORD=on`` where the server is registered, so the person turns it on in this journey.
- ``g6_record``: the record is read with ``g6_workflow_export``, which writes ``inspection.json`` for the person.
- The end-user "done" card (checked / caught / not checked / needs you): the review's ``assessment.report`` (reproduced
  findings, not established, what the person decides) is the closest shipped form, and is what is checked here.
- A sensitive-op refusal that tells the agent the person's exact step: the reply says the person decides and stops;
  the step itself (``g6 setup --allow <name>``) is only in the server's instructions.

Product defect found (``test_the_record_s_withheld_reply_names_an_identifier_that_releases_it``, strict xfail): the
withheld reply of ``g6_workflow_export`` names its workflow source only as an opaque ``source:<sha256>`` identifier.
Granting that identifier, as the reply's own repair template says, records a grant the person accepted in a dialog
but does not release the reply, so an agent that follows the reply asks the person again and again. The record is
reached here only through the sentence the reply carries (``workflow:`` and the repository's identifier, which an
earlier withheld reply named).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from mcp.types import Implementation  # noqa: E402

from g6.mcp import build_server  # noqa: E402

#: A program path that does not exist: its folder is never made (the same path tests/conftest.py uses).
NO_HOST_PROGRAM = os.path.join(tempfile.gettempdir(), "g6-tests-no-host-program", "no-host-program")
#: Set on CI's Linux jobs (tools/ci_bubblewrap.sh): a review refused for want of a boundary then fails, never skips.
EXPECT_BACKEND = "G6_EXPECT_BACKEND"
CLIENT = "claude-code"
PERSON = "Fresh Person"
CONSENT = "G6_E_CLOUD_CONSENT_REQUIRED"
DENIED = "G6_E_SENSITIVE_COMPONENT_DENIED"
NO_BOUNDARY = "G6_E_ISOLATION_UNAVAILABLE"
STATES = {"verified", "qualified-draft", "blocked-escalated"}
WITHHELD = re.compile(r"Withheld source identifiers: (?P<sources>[^ ]+(?:, [^ ]+)*)\. Provider: (?P<provider>\S+)\.")
FULL_FORM = re.compile(r'(?P<tool>\w+)\(job_id="(?P<job_id>\w+)", detail="(?P<detail>\w+)"\)')
TOOL_NAME = re.compile(r"\b(?:g6|metaharness)_[a-z_]+\b")

BASE = "def add(a, b):\n    return a + b\n"
BROKEN = "def add(a, b):\n    return a + b if a > 0 else a - b\n"      # the agent's patch: negatives now subtract
FIXED = "def add(a, b):\n    \"\"\"The sum of a and b.\"\"\"\n    return a + b\n"
TESTS = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_negative():\n    assert add(-2, -3) == -5\n")
TASK = "Document add; it must still return the sum of two integers, negative ones included."
REQUIREMENTS = [{"id": "R1", "text": "add returns the sum of two positive integers", "checks": ["test_calc.py::test_add"]},
                {"id": "R2", "text": "add returns the sum of two negative integers",
                 "checks": ["test_calc.py::test_negative"]}]


class ScriptedPerson:
    """Answers every dialog the client is asked to show: accepts, typing their name. Each dialog is logged."""

    def __init__(self) -> None:
        self.dialogs: list[dict] = []

    async def __call__(self, message, response_type, params, context):
        schema = getattr(params, "requestedSchema", None) or {}
        self.dialogs.append({"message": message, "fields": sorted(schema.get("properties", {}))})
        return response_type(value=PERSON)


def _data(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


def _withheld(reply: dict) -> dict | None:
    """The withheld reply inside ``reply`` (itself, or the result of a finished job), or None."""
    for one in (reply, reply.get("result")):
        if isinstance(one, dict) and one.get("cloud_consent_code") == CONSENT:
            return one
    return None


class Agent:
    """A coding agent that knows only what G6 replies. Every call and its reply are kept in ``calls``."""

    def __init__(self, session: Client) -> None:
        self.session = session
        self.calls: list[tuple[str, dict, dict]] = []
        self.grants: list[dict] = []
        self.named: set[str] = set()       # every source identifier a withheld reply named

    async def call(self, tool: str, arguments: dict) -> dict:
        reply = _data(await self.session.call_tool(tool, arguments, raise_on_error=False))
        self.calls.append((tool, arguments, reply))
        return reply

    async def grant_from(self, withheld: dict, sources: list[str] | None = None) -> None:
        """Ask the person for each source the withheld reply names, with the reply's own repair template."""
        found = WITHHELD.search(withheld["error"])
        template = withheld["repair"]["call"]
        assert found and template["tool"] == "g6_cloud_grant", withheld
        named = found["sources"].split(", ")
        self.named.update(named)
        for source in sources or named:
            arguments = {**template["arguments"], "source": source, "provider": found["provider"]}
            assert set(arguments) == set(template["arguments"]), (template, arguments)
            granted = await self.call(template["tool"], arguments)
            self.grants.append({"source": source, "reply": granted})

    async def shared(self, tool: str, arguments: dict, rounds: int = 3) -> tuple[dict, list[dict]]:
        """``tool`` called until its reply is no longer withheld, the person granting what each withheld reply names;
        at most ``rounds`` rounds. The last reply, and every withheld reply on the way."""
        held = []
        reply = await self.call(tool, arguments)
        while (withheld := _withheld(reply)) is not None and len(held) < rounds:
            held.append(withheld)
            await self.grant_from(withheld)
            reply = await self.call(tool, arguments)
        return reply, held

    async def finished(self, job: dict) -> dict:
        """A job polled with the tool its reply names until it completes."""
        listed = await _tool_names(self.session)
        poll = next(name for name in TOOL_NAME.findall(job["note"]) if name in listed)
        for _ in range(3000):
            reply, _ = await self.shared(poll, {"job_id": job["job_id"]})
            if reply.get("status") in ("completed", "failed") or _withheld(reply):
                return reply
            await asyncio.sleep(0.1)
        raise AssertionError(f"the job {job['job_id']} did not finish")


async def _tool_names(session: Client) -> set[str]:
    return {tool.name for tool in await session.list_tools()}


def _fill(template: dict, schema: dict, **values) -> dict:
    """A call template from G6 with each of its <placeholder> fields given, and fields the tool's listed schema has."""
    arguments = dict(template["arguments"])
    for name, value in values.items():
        assert name in schema["properties"], (name, sorted(schema["properties"]))
        if name in arguments:
            assert str(arguments[name]).startswith("<"), (name, template)
        arguments[name] = value
    assert not any(isinstance(v, str) and v.startswith("<") for v in arguments.values()), arguments
    return {"tool": template["tool"], "arguments": arguments}


def _fill_params(step: dict, **values) -> dict:
    """An op's call template (``g6_invoke`` with ``params_json``) with exactly the fields the step says to fill."""
    assert sorted(values) == sorted(step["fill"]), (sorted(values), step["fill"])
    params = {**json.loads(step["call"]["arguments"]["params_json"]), **values}
    return {"tool": step["call"]["tool"], "arguments": {**step["call"]["arguments"], "params_json": json.dumps(params)}}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repository(root: Path) -> Path:
    root.mkdir()
    (root / "calc.py").write_text(BASE, encoding="utf-8")
    (root / "test_calc.py").write_text(TESTS, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    return root


def _backend() -> str:
    from g6.core import confine

    chosen, why = confine._select()
    return f"{chosen.name if chosen is not None else 'none'}{f' ({why})' if why else ''}"


async def _first_session(person: ScriptedPerson, repo: Path) -> dict:
    """The person registered the server as it ships: no sensitive op allowed, no record."""
    seen: dict = {}
    async with Client(build_server(), client_info=Implementation(name=CLIENT, version="test"),
                      elicitation_handler=person) as session:
        agent = Agent(session)
        # 1. the instructions, then the tools they name
        seen["instructions"] = session.initialize_result.instructions or ""
        schemas = {tool.name: tool.inputSchema for tool in await session.list_tools()}
        seen["tools"] = set(schemas)
        # 2. discovery, as the instructions say: g6_select with the task and the phase
        task = "review my patch before I say done"
        seen["select"] = await agent.call("g6_select", {"task": task, "phase": "verify"})
        # 3. a component op, from the selector's template for a claim check
        claims = await agent.call("g6_select", {"task": "check that the summary's claims are supported by the sources",
                                                "phase": "verify"})
        op = _fill_params(claims["next"], claim="add(-2, -3) == -5",
                          sources={"test_calc.py": TESTS})
        seen["op_call"] = op
        seen["op_first"] = await agent.call(op["tool"], op["arguments"])
        held = _withheld(seen["op_first"])
        seen["op_reroute"] = await agent.call("g6_select", {"task": task, "phase": "verify",
                                                            "last_outcome": held["cloud_consent_code"] if held else ""})
        dialogs_before = len(person.dialogs)
        if held is not None:
            await agent.grant_from(held)
        seen["op_dialogs"] = person.dialogs[dialogs_before:]
        seen["op_grants"] = list(agent.grants)
        seen["op_retry"] = await agent.call(op["tool"], op["arguments"])
        # 6. the review the selector ranked, built from its template: sensitive, so refused on this server
        review = next(c for c in seen["select"]["candidates"] if c["call"].get("tool") == "g6_review_patch")
        seen["review_candidate"] = review
        call = _fill(review["call"], schemas[review["call"]["tool"]], repo=str(repo), task=TASK,
                     requirements=REQUIREMENTS)
        seen["review_call"] = call
        seen["denied"] = await agent.call(call["tool"], call["arguments"])
        seen["denied_reroute"] = await agent.call("g6_select", {
            "task": task, "phase": "verify", "last_outcome": seen["denied"].get("warning_card", {}).get("code", "")})
        seen["calls_1"] = agent.calls
        seen["named_1"] = set(agent.named)
    return seen


async def _second_session(person: ScriptedPerson, repo: Path, seen: dict) -> dict:
    """The person allowed the op and turned the record on, where the server is registered, and started it again."""
    async with Client(build_server(), client_info=Implementation(name=CLIENT, version="test"),
                      elicitation_handler=person) as session:
        agent = Agent(session)
        call = seen["review_call"]
        # 4. the agent's patch breaks R2
        (repo / "calc.py").write_text(BROKEN, encoding="utf-8")
        job = await agent.call(call["tool"], call["arguments"])
        seen["broken_job"] = job
        seen["broken"] = await agent.finished(job)
        full = FULL_FORM.search(str(seen["broken"].get("result", {}).get("full_assessment", {}).get("detail", "")))
        if full is not None:   # the compact reply says how to read the whole assessment, reproductions included
            seen["broken_full"], _ = await agent.shared(full["tool"], {"job_id": full["job_id"], "detail": full["detail"]})
        # 5. the agent fixes it
        (repo / "calc.py").write_text(FIXED, encoding="utf-8")
        job = await agent.call(call["tool"], call["arguments"])
        seen["fixed_job"] = job
        seen["fixed"] = await agent.finished(job)
        # 7. the record of this work, read back
        export = {"tool": "g6_workflow_export", "arguments": {"repo": str(repo)}}
        assert export["tool"] in await _tool_names(session)
        seen["export_by_reply"], seen["export_held"] = await agent.shared(export["tool"], export["arguments"], rounds=2)
        held = _withheld(seen["export_by_reply"])
        seen["export_hint"] = held.get("workflow_files", "") if held else ""
        if held is not None:
            # the sentence the withheld reply carries: the source named workflow: and this repository's identifier,
            # which a withheld reply of this journey named as repository:<identifier>
            named = seen["named_1"] | agent.named
            repository = next(s for s in sorted(named) if s.startswith("repository:"))
            assert "workflow:" in seen["export_hint"] and "repository's identifier" in seen["export_hint"]
            await agent.grant_from(held, ["workflow:" + repository.split(":", 1)[1]])
        seen["export"] = await agent.call(export["tool"], export["arguments"])
        seen["calls_2"] = agent.calls
        seen["grants_2"] = agent.grants
    return seen


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    """The whole journey, run once; what each step replied, every dialog, and every model call."""
    from g6.core import cloud_consent, confine, llm

    root = tmp_path_factory.mktemp("fresh-journey")
    person, model = ScriptedPerson(), llm.FakeLLM(default="unused")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("G6_HOME", str(root / "g6-home"))
        for name in ("G6_CLAUDE", "G6_CODEX"):
            mp.setenv(name, NO_HOST_PROGRAM)
        for name in ("G6_MCP_SENSITIVE", "G6_WORKFLOW_RECORD", "G6_LLM_PROVIDER", "G6_LLM_MODEL", confine.ALLOW):
            mp.delenv(name, raising=False)
        mp.setattr(llm, "provider_from_env", lambda *a, **k: model)     # the person's model: a fake, never asked
        repo = _repository(root / "repo")
        grants_before = cloud_consent.grants()
        seen = asyncio.run(_first_session(person, repo))
        seen["grants_before"] = grants_before
        # what the person does between sessions, where the server is registered
        mp.setenv("G6_MCP_SENSITIVE", seen["review_candidate"]["needs"]["sensitive"])
        mp.setenv("G6_WORKFLOW_RECORD", "on")
        seen = asyncio.run(_second_session(person, repo, seen))
    seen["dialogs"], seen["model_calls"] = person.dialogs, model.calls
    seen["backend"] = _backend()
    return seen


def _confined(reply: dict) -> bool:
    """Whether the finished review ran (an assessment) rather than being refused for want of a boundary."""
    result = reply["result"]
    if "assessment" in result:
        return True
    assert result["completion_state"] == "blocked-escalated" and result["warning_card"]["code"] == NO_BOUNDARY, result
    return False


def _states(value) -> set[str]:
    """Every completion state anywhere in ``value``."""
    if isinstance(value, dict):
        found = {value["completion_state"]} if isinstance(value.get("completion_state"), str) else set()
        return found.union(*(_states(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_states(v) for v in value))
    return set()


def test_a_fresh_person_s_journey_over_mcp(journey) -> None:
    # 1. the instructions say when to use G6, and name only tools the server lists
    said = journey["instructions"]
    assert "g6_review_patch" in said and "g6_select" in said and CONSENT in said and "g6_cloud_grant" in said, said
    assert set(TOOL_NAME.findall(said)) <= journey["tools"], set(TOOL_NAME.findall(said)) - journey["tools"]
    assert journey["grants_before"] == []                                   # a fresh G6_HOME: nothing granted

    # 2. discovery answers a client that granted nothing, and ranks a review tool in its top two
    select = journey["select"]
    assert select["ok"] is True and "cloud_consent_code" not in select and select["completion_state"] == "qualified-draft"
    top = [c["call"].get("tool") for c in select["candidates"][:2]]
    assert {"g6_review_patch", "g6_review_until_clean"} & set(top), top

    # 3. a component op withheld for consent, rerouted to the grant, granted by the person, then answered
    first = journey["op_first"]
    assert first["ok"] is False and first["completion_state"] == "blocked-escalated", first
    assert first["cloud_consent_code"] == CONSENT and first["warning_card"]["code"] == CONSENT
    assert first["repair"]["by"] == "person" and first["repair"]["call"]["tool"] == "g6_cloud_grant", first["repair"]
    assert journey["op_reroute"]["next"]["call"]["tool"] == "g6_cloud_grant", journey["op_reroute"]["next"]
    granted = journey["op_grants"]
    assert granted and all(g["reply"]["ok"] is True and g["reply"]["result"]["status"] == "granted" for g in granted)
    dialogs = journey["op_dialogs"]
    assert len(dialogs) == len(granted), (dialogs, granted)
    for dialog, grant in zip(dialogs, granted):
        assert grant["source"] in dialog["message"] and "anthropic:claude_code" in dialog["message"], dialog
        assert "cannot be recalled" in dialog["message"] and dialog["fields"] == ["value"], dialog
    retry = journey["op_retry"]
    assert "cloud_consent_code" not in retry and retry["completion_state"] in STATES, retry
    assert retry["ok"] is True and retry["result"], retry

    # 6. a sensitive op on a fresh server: refused with its code, the op named, and the person's to allow
    denied = journey["denied"]
    assert denied["ok"] is False and denied["completion_state"] == "blocked-escalated", denied
    assert denied["warning_card"]["code"] == DENIED and f"[{DENIED}]" in denied["error"], denied
    assert journey["review_candidate"]["needs"]["sensitive"] in denied["error"], denied["error"]
    repair = denied["repair"]
    assert repair["by"] == "person" and repair["call"] is None and "person" in repair["step"], repair
    reroute = journey["denied_reroute"]["next"]
    assert reroute["call"] is None and reroute["tell_person"] is True, reroute
    assert "g6 setup --allow" in said                       # the person's own step is in the instructions only

    # 4 and 5. both reviews finished; neither reply reaches the top state, whatever ran
    for name in ("broken", "fixed"):
        reply = journey[name]
        assert reply["status"] == "completed" and _withheld(reply) is None, reply
        assert "verified" not in _states(reply) and _states(reply) <= STATES, _states(reply)
        if not _confined(reply):
            assert "g6 doctor" in reply["result"]["warning_card"]["message"]
    fixed = journey["fixed"]["result"]
    if _confined(journey["fixed"]):
        # qualified-draft at best: its stated checks pass at the base commit too, so G6 counts nothing for it
        assert fixed["completion_state"] == "qualified-draft" and fixed["ok"] is True, fixed
        assessment = fixed["assessment"]
        assert assessment["outcome"] in ("supported_within_scope", "insufficient_evidence"), assessment
        assert fixed["merge"]["status"] == "requires_separate_approval"
        # the report's parts in place of the done card: caught, checked, not checked, and what needs the person
        for part in ("## Reproduced findings", "## Demonstrated on the stated checks", "## Not established",
                     "Not checked:", "## For you to decide"):
            assert part in assessment["report"], (part, assessment["report"])

    # 7. the record of this session's review work, read back once the person shared it
    export = journey["export"]
    assert export["ok"] is True and "cloud_consent_code" not in export, export
    result = export["result"]
    assert result["status"] == "written" and result["recording"] is True and result["tasks"] >= 1, result
    assert result["events"] >= 2 and result["runs"] >= 1, result
    inspection = json.loads(Path(result["file"]).read_text(encoding="utf-8"))   # the person opens the file
    observed = [row for task in inspection["tasks"] for run in task["runs"] for row in run["observed"]]
    assert observed, inspection
    assert TASK not in json.dumps(inspection) and BROKEN not in json.dumps(inspection)   # codes and digests only

    # every dialog was a sharing question the person answered with their name; no model was asked
    assert journey["dialogs"] and all("cannot be recalled" in d["message"] for d in journey["dialogs"])
    assert journey["model_calls"] == []
    # every state any reply carried is one of the three (a running job's poll carries none), never the top one
    for tool, _, reply in journey["calls_1"] + journey["calls_2"]:
        assert reply.get("status") in ("pending", "running") or _states(reply), (tool, reply)
        assert _states(reply) <= STATES - {"verified"}, (tool, reply)


def test_the_journey_s_review_reproduces_the_broken_requirement(journey) -> None:
    broken = journey["broken"]
    if not _confined(broken):
        if os.environ.get(EXPECT_BACKEND, "").strip():
            pytest.fail(f"{EXPECT_BACKEND} is set, yet the review was refused for want of a boundary: "
                        f"{journey['backend']}")
        pytest.skip(f"no operating-system boundary can confine the review here ({NO_BOUNDARY}); "
                    f"isolation backend: {journey['backend']}")
    result = broken["result"]
    assessment = result["assessment"]
    assert result["completion_state"] == "qualified-draft" and assessment["outcome"] == "defect_detected", assessment
    assert [(r["code"], r["detail"].split(": ")[0]) for r in assessment["reasons"]] == [
        ("check_failed", "test_calc.py::test_negative")], assessment["reasons"]
    found = assessment["report"].split("## Reproduced findings", 1)[1].split("## ", 1)[0]
    assert "`R2`" in found and "`R1`" not in found, found
    assert "Reproduce: `python -m pytest test_calc.py::test_negative`" in found, found
    assert result["merge"]["status"] == "requires_separate_approval"
    # the whole assessment, read as the compact reply says, holds the reproduction G6 ran
    full = journey["broken_full"]
    assert full["status"] == "completed" and _withheld(full) is None, full
    reproduced = full["result"]["assessment"]["completion"]["defects_reproduced"]
    rows = reproduced["stated_and_repository_checks"]
    assert [(row["check"], row["requirements"], row["code"]) for row in rows] == [
        ("test_calc.py::test_negative", ["R2"], "check_failed")], rows
    command = rows[0]["reproduction"]["command"]
    assert command.startswith("python -m pytest ") and command.endswith(" test_calc.py::test_negative"), command


@pytest.mark.xfail(strict=True, reason="product defect: g6_workflow_export's withheld reply names its workflow source "
                                       "only as an opaque source:<sha256>; granting that identifier, as the reply's "
                                       "repair template says, does not release the reply")
def test_the_record_s_withheld_reply_names_an_identifier_that_releases_it(journey) -> None:
    held, reply = journey["export_held"], journey["export_by_reply"]
    assert held, "the export was answered before any grant"
    assert _withheld(reply) is None, ("the reply is still withheld after granting what it named",
                                      WITHHELD.search(_withheld(reply)["error"])["sources"])
