# SPDX-License-Identifier: Apache-2.0
"""The beta gate's end-to-end journey (plan revision 8, "Verification"): a fresh install, used over MCP the way a new
person's agent would use it.

A fresh G6_HOME with no sharing grants and no sensitive op allowed; ``fastmcp.Client(build_server())`` in process,
identified as Claude Code; a scripted person who answers and logs each dialog (``kit.Person``); FakeLLM wherever a
model is needed (``tests.agentic.kit``); and an agent that builds each call only from the templates in G6's replies,
filled from the task's text, the repository's folder or what an earlier reply said (``kit.Agent``, ``kit.fill``).

The journey runs once for the module, in order, each step's failure kept and later steps going on where they can; one
test per step reports it, so a failure names its step:

1. ``g6_select`` (plan) for a small coding task;
2. a premortem comes back withheld (G6_E_CLOUD_CONSENT_REQUIRED), its repair reroutes to ``g6_cloud_grant``, the
   person accepts, and the same call succeeds;
3. ``g6_confirm_behaviour`` asks the person one question, built from what the premortem returned;
4. ``g6_select`` (verify), then ``g6_review_patch`` on a tiny git repository with a patch;
5. the review's first call, before the person allowed it, is a sensitive refusal that reroutes to telling the person,
   with no way for the agent to allow it itself;
6. the review's defect_detected result leads to discriminating ``proposed_checks``;
7. a cegis float-hole refusal (G6_E_CEGIS_HOLE_UNSUPPORTED) leads to ``g6_agentic_run``, and a result with the
   original refusal kept beside it.

Two stand-ins, because not every machine the suite runs on has an operating-system boundary ready: the review's test
runs are recorded (``kit.RecordedRuns``) and the override kernel's confined child runs in process
(``tests.agentic.kit.in_process_child``), as the focused suites of both do. Nothing reaches the network.
"""
from __future__ import annotations

import asyncio
import json
import os
import traceback
from typing import Any

import pytest

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from mcp.types import Implementation  # noqa: E402

from g6.mcp import build_server  # noqa: E402
from tests.agentic.kit import FLOAT_PARAMS, Model, configured_model, in_process_child  # noqa: E402
from tests.e2e.kit import CONSENT, TASK, Agent, Person, RecordedRuns, fill, make_repo, placeholder  # noqa: E402

ENVELOPE = ("completion_state", "warning_card", "evidence", "request_id", "run_id")
OUTCOMES = {"supported_within_scope", "defect_detected", "test_integrity_compromised", "insufficient_evidence"}
DENIED = "G6_E_SENSITIVE_COMPONENT_DENIED"
HOLE = "G6_E_CEGIS_HOLE_UNSUPPORTED"
#: The person's allowances, set where the server is registered, only after the agent told them.
REVIEW_ALLOWED = "patch_review.review"
OVERRIDE_ALLOWED = "patch_review.review,cegis.synthesize,agentic.run"
CEGIS_TASK = ("Fill the float hole in the sketch def f(x): return x * ??f so that f matches the cases "
              "f(1) = 0.5, f(2) = 1.0 and f(4) = 2.0, with counterexample-guided synthesis of the sketch.")
CEGIS_DATA = FLOAT_PARAMS            # the task's own data: the sketch and its cases
STEPS = (1, 2, 3, 4, 5, 6, 7, "7b")


def _card(reply: Any) -> str:
    card = reply.get("warning_card") if isinstance(reply, dict) else None
    return card.get("code", "") if isinstance(card, dict) else ""


def _inner(reply: dict) -> dict:
    return reply["result"] if "job_id" in reply and isinstance(reply.get("result"), dict) else reply


def _calls(reply: dict) -> list[dict]:
    """Every call template a g6_select reply offers: its next call and each candidate's."""
    found = [c["call"] for c in reply.get("candidates", ()) if isinstance(c.get("call"), dict)]
    nxt = reply.get("next") or {}
    return found + ([nxt["call"]] if isinstance(nxt.get("call"), dict) else [])


def _predictions(value: Any) -> list[str]:
    """Every ``predicts`` a reply holds, anywhere in it."""
    found: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("predicts"), (str, dict, list)):
            found.append(json.dumps(value["predicts"], sort_keys=True))
        for child in value.values():
            found += _predictions(child)
    elif isinstance(value, list):
        for child in value:
            found += _predictions(child)
    return found


class Journey:
    """The journey's state and each step's outcome: None when it held, else the failure's text."""

    def __init__(self, monkeypatch, repo, person: Person, model: Model, served: list, runs: RecordedRuns) -> None:
        self.monkeypatch, self.repo, self.person, self.model, self.served, self.runs = (
            monkeypatch, repo, person, model, served, runs)
        self.situation = {"task": TASK, "repo": str(repo)}
        self.failed: dict[Any, str | None] = {}
        self.seen: dict[str, Any] = {}

    async def step(self, name, body) -> None:
        try:
            await body()
            self.failed[name] = None
        except Exception:   # noqa: BLE001 - kept for that step's own test; the journey goes on
            self.failed[name] = traceback.format_exc(limit=6)

    async def run(self) -> None:
        async with Client(build_server(), elicitation_handler=self.person,
                          client_info=Implementation(name="claude-code", version="e2e")) as client:
            self.agent = Agent(client, self.person, self.situation)
            from g6.core import cloud_consent
            self.seen["grants_at_start"] = cloud_consent.grants()   # G6's own ledger, read in process
            for name in STEPS:
                await self.step(name, getattr(self, f"step_{name}"))
            self.seen["grants_at_end"] = cloud_consent.grants()

    # 1 ------------------------------------------------------------------------------------------------------------
    async def step_1(self) -> None:
        asked = len(self.person.log)
        chosen = await self.agent.select(TASK, "plan")
        self.seen["plan"] = chosen
        assert all(field in chosen for field in ENVELOPE), sorted(chosen)
        assert chosen["next"]["id"] == "tool:g6_premortem", chosen["next"]
        assert "tool:g6_confirm_behaviour" in {c["id"] for c in chosen["candidates"]}
        assert len(self.person.log) == asked, "g6_select asked the person something"

    # 2 ------------------------------------------------------------------------------------------------------------
    async def step_2(self) -> None:
        tool, arguments = fill(self.seen["plan"]["next"]["call"], self.situation)
        refused = await self.agent.raw(tool, arguments)
        assert refused.get("cloud_consent_code") == CONSENT and _card(refused) == CONSENT, refused
        assert refused["ok"] is False and refused["completion_state"] == "blocked-escalated"
        assert refused["repair"]["call"]["tool"] == "g6_cloud_grant" and refused["repair"]["by"] == "person"
        rerouted = await self.agent.select(TASK, "plan", refused)
        assert rerouted["next"]["id"] == "tool:g6_cloud_grant", rerouted["next"]
        asked = len(self.person.log)
        await self.agent.share(refused)
        shown = self.person.log[asked:]
        assert shown and all(e["kind"] == "share" and "anthropic:claude_code" in e["message"] for e in shown), shown
        again = await self.agent.raw(tool, arguments)
        assert again.get("ok") is True and again["completion_state"] == "qualified-draft", again
        assert again["hypotheses"] and again["model_calls"] == 0
        self.seen["premortem"] = again

    # 3 ------------------------------------------------------------------------------------------------------------
    async def step_3(self) -> None:
        template = next(c["call"] for c in self.seen["plan"]["candidates"]
                        if c["id"] == "tool:g6_confirm_behaviour")
        tool, arguments = fill(template, self.situation)
        # the candidate comes from the premortem's reply, never from the agent's own pen
        question = (self.seen.get("premortem") or {}).get("question") or {}
        if isinstance(question, dict) and isinstance(question.get("candidate"), dict):
            arguments["candidates"] = [question["candidate"]]
        asked = len(self.person.log)
        got = await self.agent.call(tool, arguments)
        self.seen["behaviour"] = got
        behaviour = [e for e in self.person.log[asked:] if e["kind"] == "behaviour"]
        assert got.get("status") == "confirmed" and _card(got) == "G6_E_QUESTION_CONFIRMED", (
            f"g6_confirm_behaviour came back {got.get('status')!r} ({_card(got)}) and asked the person "
            f"{len(behaviour)} question(s): no reply before it offered a behaviour candidate "
            f"(the premortem's reply holds no question.candidate)")
        assert len(behaviour) == 1 and got["answer"]["answered_by"] == "person:" + Person.NAME

    # 4 and 5: the review, first refused (the person had allowed nothing), then allowed by the person -----------------
    async def step_4(self) -> None:
        chosen = await self.agent.select(TASK, "verify")
        self.seen["verify"] = chosen
        assert chosen["next"]["id"] == "tool:g6_review_patch", chosen["next"]
        tool, arguments = fill(chosen["next"]["call"], self.situation)
        first = await self.agent.call(tool, arguments)
        self.seen["review_refused"] = first
        if _card(first) == DENIED:
            await self.step_5_reroute(first)
            self.monkeypatch.setenv("G6_MCP_SENSITIVE", REVIEW_ALLOWED)   # the person allows it where G6 is registered
        else:
            raise AssertionError(f"a fresh install ran g6_review_patch without the person's allowance: {first}")
        job = await self.agent.call(tool, arguments)
        assert job.get("ok") is True and "job_id" in job, job
        done = await self.agent.finished(job)
        self.seen["review"] = done
        assert done.get("status") == "completed", done
        result = _inner(done)
        assert result["completion_state"] == "qualified-draft" and result["assessment"]["outcome"] in OUTCOMES, result
        assert self.runs.calls, "the review ran none of the repository's tests"

    async def step_5_reroute(self, refused: dict) -> None:
        asked = len(self.person.log)
        rerouted = await self.agent.select(TASK, "verify", refused)
        self.seen["denied_reroute"] = rerouted
        self.seen["denied_dialogs"] = len(self.person.log) - asked
        self.seen["allowance_after_reroute"] = os.environ.get("G6_MCP_SENSITIVE")

    async def step_5(self) -> None:
        refused, rerouted = self.seen.get("review_refused"), self.seen.get("denied_reroute")
        assert refused is not None and rerouted is not None, "step 4 never reached the sensitive refusal"
        assert _card(refused) == DENIED and refused["completion_state"] == "blocked-escalated", refused
        assert refused["repair"]["by"] == "person" and refused["repair"]["call"] is None, refused["repair"]
        nxt = rerouted["next"]
        assert nxt["tell_person"] is True and nxt["call"] is None and nxt["id"] is None, nxt
        assert rerouted["remedy"]["fix_by"] == "person" and rerouted["remedy"]["call"] is None, rerouted["remedy"]
        # no self-grant: nothing offered sets an allowance, nothing asked the person, and the server's setting stayed
        assert all(call["tool"] != "g6_cloud_grant" for call in _calls(rerouted))
        assert "G6_MCP_SENSITIVE" not in json.dumps(refused) + json.dumps(rerouted)
        assert self.seen["denied_dialogs"] == 0 and self.seen["allowance_after_reroute"] is None

    # 6 ------------------------------------------------------------------------------------------------------------
    async def step_6(self) -> None:
        done = self.seen.get("review")
        assert done is not None, "step 4's review did not finish"
        assessment = _inner(done)["assessment"]
        assert assessment["outcome"] == "defect_detected", (assessment["outcome"], assessment.get("reasons"),
                                                            self.runs.calls)
        chosen = await self.agent.select(TASK, "diagnose", done)
        self.seen["diagnose"] = chosen
        checks = [call for call in _calls(chosen) if call.get("tool") == "g6_review_patch"
                  and isinstance(call.get("arguments", {}).get("proposed_checks"), list)
                  and call["arguments"]["proposed_checks"]]
        predicted = set(_predictions(chosen))
        assert checks and len(predicted) >= 2, (
            "after defect_detected, g6_select (diagnose) offers no g6_review_patch template with proposed_checks and "
            f"at least two competing explanations predicting different results of it (templates: {_calls(chosen)}; "
            f"predictions: {sorted(predicted)}; remedy: {chosen.get('remedy')})")
        tool, arguments = fill(checks[0], self.situation)
        done = await self.agent.finished(await self.agent.call(tool, arguments))
        assert _inner(done).get("assessment", {}).get("outcome") in OUTCOMES, done

    # 7 ------------------------------------------------------------------------------------------------------------
    async def step_7(self) -> None:
        found = await self.agent.call("g6_find", {"query": CEGIS_TASK})
        component = next(f["component"] for f in found if f.get("via") == "g6_invoke")
        described = await self.agent.call("g6_describe", {"component": component})
        [op] = [o for o in described["ops"] if o in described.get("sensitive_ops", described["ops"])]
        invoke = {"component": component, "op": op, "params_json": json.dumps(CEGIS_DATA)}
        denied = await self.agent.call("g6_invoke", invoke)
        assert _card(denied) == DENIED, denied
        told = await self.agent.select(CEGIS_TASK, "implement", denied)
        assert told["next"]["tell_person"] is True and told["next"]["call"] is None, told["next"]
        self.monkeypatch.setenv("G6_MCP_SENSITIVE", OVERRIDE_ALLOWED)   # the person allows both, where G6 is registered
        refused = await self.agent.call("g6_invoke", invoke)
        self.seen["hole"] = refused
        assert _card(refused) == HOLE and refused["completion_state"] == "blocked-escalated", refused
        rerouted = await self.agent.select(CEGIS_TASK, "implement", refused)
        self.seen["hole_reroute"] = rerouted
        assert rerouted["next"]["id"] == "tool:g6_agentic_run", rerouted["next"]
        template = rerouted["next"]["call"]
        known = {**invoke, **{k: invoke["params_json"] for k, v in template["arguments"].items()
                              if v == "<the same parameters>"}}
        tool, arguments = fill(template, known)
        assert not any(placeholder(v) for v in arguments.values()), arguments
        asked = len(self.person.log)
        done = await self.agent.finished(await self.agent.call(tool, arguments))
        result = _inner(done)
        self.seen["override"] = result
        assert result.get("ok") is True and result["completion_state"] == "qualified-draft", result
        assert _card(result) == "G6_E_AGENTIC_SUBSTITUTE" and result["agentic"]["outcome"] == "substituted", result
        assert result["original"]["warning_card"]["code"] == HOLE, "the original refusal is not kept beside it"
        assert result["result"]["program"] == "def f(x):\n    return x * 0.5\n", result["result"]
        assert result["agentic"]["rechecked"] is True and result["agentic"]["person"]["answer"] == "local model"
        assert self.model.prompts and self.served, "the override used no model or ran no child"
        # owner ruling 2026-11-17: no per-override dialog; its authority is recorded in the reply instead
        assert all(e["kind"] == "share" for e in self.person.log[asked:]), self.person.log[asked:]

    async def step_7b(self) -> None:
        """The hole refusal's own repair, after the person shared what the call held and it was made again, agrees
        with g6_select: g6_agentic_run, by the agent."""
        refused = self.seen.get("hole")
        assert refused is not None, "step 7 never reached the hole refusal"
        repair = refused.get("repair") or {}
        assert (repair.get("call") or {}).get("tool") == "g6_agentic_run" and repair.get("by") == "agent", (
            "after a consent-withheld reply, the same call made again (as the withheld repair says) is counted as a "
            f"refusal sent unchanged, so its repair sends the agent to the person while g6_select names "
            f"g6_agentic_run: {repair}")


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    from tests.conftest import NO_HOST_PROGRAM

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("G6_HOME", str(tmp_path_factory.mktemp("fresh-home")))
        for name in ("G6_CLAUDE", "G6_CODEX"):
            monkeypatch.setenv(name, NO_HOST_PROGRAM)
        for name in ("G6_MCP_SENSITIVE", "G6_LLM_PROVIDER", "G6_LLM_MODEL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                     "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)
        repo = make_repo(tmp_path_factory.mktemp("project"))
        model = Model([0.25, 0.5, 1.0])
        configured_model(monkeypatch, model)
        record = Journey(monkeypatch, repo, Person(), model, in_process_child(monkeypatch), RecordedRuns(monkeypatch))
        asyncio.run(record.run())
        yield record


def _held(journey: Journey, step) -> None:
    failure = journey.failed[step]
    assert failure is None, f"step {step} failed:\n{failure}"


def test_the_home_starts_with_no_grants(journey):
    assert journey.seen["grants_at_start"] == []


def test_step_1_select_plan(journey):
    _held(journey, 1)


def test_step_2_withheld_premortem_reroutes_to_the_persons_grant(journey):
    _held(journey, 2)


def test_step_3_confirm_behaviour_asks_the_person(journey):
    _held(journey, 3)


def test_step_4_select_verify_then_review_patch(journey):
    _held(journey, 4)


def test_step_5_sensitive_refusal_reroutes_to_the_person(journey):
    _held(journey, 5)


def test_step_6_defect_leads_to_discriminating_proposed_checks(journey):
    _held(journey, 6)


def test_step_7_cegis_hole_leads_to_the_override_beside_the_original(journey):
    _held(journey, 7)


def test_step_7b_the_hole_refusals_repair_agrees_with_select_after_a_consent_retry(journey):
    _held(journey, "7b")


def test_every_grant_came_from_a_dialog_the_person_answered(journey):
    kinds = journey.person.kinds()
    assert kinds and set(kinds) <= {"share", "behaviour"}, journey.person.log
    assert all("anthropic:claude_code" in e["message"] for e in journey.person.log if e["kind"] == "share")
    assert len(journey.seen["grants_at_end"]) == kinds.count("share")
