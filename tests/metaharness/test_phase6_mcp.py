# SPDX-License-Identifier: Apache-2.0
"""Meta-harness Phase 6: the MCP surface (envelope parity with Python; approval only from a person, through the
client, and never from the session that proposed)."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from g6.invariants import check_envelope
from g6.metaharness import Profile, adapt
from g6.metaharness import schema as S
from g6.metaharness.surface import NOT_CONFIGURED, Surface, as_json

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

from g6.mcp import build_server  # noqa: E402

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

CAREFUL = {"routing": {"answer": "local"}, "prompt": "CAREFUL: add the two numbers digit by digit."}
TOOLS = {"metaharness_inspect", "metaharness_propose", "metaharness_evaluate", "metaharness_decide",
         "metaharness_diagnose", "metaharness_approve", "metaharness_rollback", "metaharness_start",
         "metaharness_confirm", "metaharness_propose_code", "metaharness_evaluate_start", "metaharness_job",
         "metaharness_run"}


def test_rollback_prompt_and_action_are_bound_to_store_head(tmp_path):
    surface = _surface(tmp_path)
    refused, question, binding = surface.rollback_check("arith", 0)
    assert refused is None
    assert binding["profile_digest"] in question and binding["workflow_digest"] in question
    surface.harness("arith").rollback(0, accepted_by="someone else")
    got = surface.rollback("arith", 0, answer="Ada", binding=binding)
    assert got["completion_state"] == "blocked-escalated"


def _surface(tmp_path) -> Surface:
    wf, _ = rig.build(7)
    surface = Surface({"arith": {"workflow": wf, "workspace": tmp_path / "ws", "clock": rig.Clock()}})
    surface.harness("arith").confirm("Ada")
    return surface


def _data(result) -> dict:
    return result.data if isinstance(result.data, dict) else result.structured_content


def test_the_tools_are_registered_beside_the_discovery_tools(tmp_path) -> None:
    names = set(asyncio.run(build_server(_surface(tmp_path)).get_tools()))
    assert TOOLS <= names and "g6_invoke" in names


def test_without_declared_workflows_every_tool_says_so(monkeypatch) -> None:
    monkeypatch.delenv("G6_METAHARNESS", raising=False)
    got = Surface().inspect("anything")
    assert got["warning_card"]["code"] == NOT_CONFIGURED and check_envelope(got) == []


def test_mcp_replies_are_the_python_envelopes_as_json(tmp_path) -> None:
    surface = _surface(tmp_path)
    via_surface = surface.propose("arith", session="s1", profile=CAREFUL, proposer="bo")
    direct = as_json(surface.harness("arith").candidate(Profile.model_validate(CAREFUL), proposer="bo"))
    assert via_surface["warning_card"]["code"] == direct["warning_card"]["code"] == "G6_E_META_PROPOSED"
    assert set(via_surface) == set(direct) and check_envelope(via_surface) == []
    json.dumps(via_surface)   # JSON all the way down


def test_over_mcp_declined_or_missing_client_consent_never_approves(tmp_path) -> None:
    surface = _surface(tmp_path)
    server = build_server(surface)

    async def answer_ada(message, response_type, params, context):
        assert "approve" in message
        return response_type(value="Ada")

    async def decline(message, response_type, params, context):
        return ElicitResult(action="decline")

    async def flow() -> dict:
        out = {}
        async with Client(server) as agent:
            proposed = _data(await agent.call_tool("metaharness_propose", {"workflow": "arith",
                                                                          "profile_json": json.dumps(CAREFUL)}))
            p = proposed["proposal_digest"]
            r = _data(await agent.call_tool("metaharness_evaluate", {"workflow": "arith", "proposal_digest": p}))
            d = _data(await agent.call_tool("metaharness_decide", {"workflow": "arith", "report_digest":
                                                                   r["report_digest"], "proposal_digest": p}))
            out["decided"] = d
            out["self"] = _data(await agent.call_tool("metaharness_approve", {"workflow": "arith", "decision_digest":
                                                                              d["decision_digest"]}))
        async with Client(server, elicitation_handler=decline) as reviewer:
            out["declined"] = _data(await reviewer.call_tool("metaharness_approve", {
                "workflow": "arith", "decision_digest": d["decision_digest"]}))
        async with Client(server, elicitation_handler=answer_ada) as reviewer:
            out["approved"] = _data(await reviewer.call_tool("metaharness_approve", {
                "workflow": "arith", "decision_digest": d["decision_digest"]}))
        return out

    out = asyncio.run(flow())
    assert out["decided"]["warning_card"]["code"] == S.RECOMMENDED
    assert out["self"]["warning_card"]["code"] == S.APPROVAL_REQUIRED
    assert out["declined"]["warning_card"]["code"] == S.APPROVAL_REQUIRED
    assert out["approved"]["head_version"] == 1 and out["approved"]["completion_state"] == "qualified-draft"
    assert surface.harness("arith").store.versions()[-1]["accepted_by"] == "Ada"
    for reply in out.values():
        assert check_envelope(reply) == [] and reply["completion_state"] != "verified"


async def _decided(client) -> dict:
    """Propose, evaluate and decide CAREFUL over MCP; the decision's envelope."""
    proposed = _data(await client.call_tool("metaharness_propose", {"workflow": "arith",
                                                                   "profile_json": json.dumps(CAREFUL)}))
    p = proposed["proposal_digest"]
    r = _data(await client.call_tool("metaharness_evaluate", {"workflow": "arith", "proposal_digest": p}))
    return _data(await client.call_tool("metaharness_decide", {"workflow": "arith", "report_digest": r["report_digest"],
                                                              "proposal_digest": p}))


def test_proposing_session_can_present_digest_bound_client_consent(tmp_path) -> None:
    surface = _surface(tmp_path)
    asked = []

    async def answer(message, response_type, params, context):
        asked.append(message)
        return response_type(value="Ada")

    async def flow() -> tuple[dict, dict]:
        async with Client(build_server(surface), elicitation_handler=answer) as agent:
            decided = await _decided(agent)
            return decided, _data(await agent.call_tool("metaharness_approve", {
                "workflow": "arith", "decision_digest": decided["decision_digest"]}))

    decided, approved = asyncio.run(flow())
    assert decided["warning_card"]["code"] == S.RECOMMENDED
    assert approved["head_version"] == 1 and check_envelope(approved) == []
    assert len(asked) == 1 and decided["decision_digest"] in asked[0]
    assert decided["decision"]["candidate_digest"] in asked[0]
    consent = approved["consent"]
    assert consent["method"] == "mcp-elicitation"
    assert consent["identity_authenticated"] is False
    assert consent["decision_digest"] == decided["decision_digest"]
    assert len(surface.harness("arith").store.versions()) == 2


def test_a_change_proposed_over_mcp_is_approved_by_digest_from_python_in_another_process(tmp_path) -> None:
    surface = _surface(tmp_path)

    async def flow() -> dict:
        async with Client(build_server(surface)) as agent:
            return await _decided(agent)

    decided = asyncio.run(flow())
    wf, _ = rig.build(7)
    elsewhere = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())   # a fresh process: nothing in memory
    card = elsewhere.activate(decided["decision_digest"], accepted_by="Ada")["card"]   # the person reads it first
    done = elsewhere.activate(decided["decision_digest"], accepted_by="Ada", candidate_digest=card["candidate_digest"])
    assert (done["warning_card"]["code"], done["head_version"]) == ("G6_E_META_ACTIVATED", 1), done["message"]
    assert elsewhere.store.versions()[-1]["accepted_by"] == "Ada"
    assert elsewhere.store.versions()[-1]["decision_digest"] == decided["decision_digest"]
    # the MCP server reads the workspace again once another process wrote it, so it never acts on a stale head
    assert surface.inspect("arith")["versions"][-1]["version"] == 1
    assert elsewhere.activate(decided["decision_digest"], accepted_by="Bo")["warning_card"]["code"] == S.STANDARD_CHANGED


def test_another_server_process_can_present_the_recorded_proposal_for_consent(tmp_path) -> None:
    first = _surface(tmp_path)

    async def answer_ada(message, response_type, params, context):
        return response_type(value="Ada")

    async def propose() -> dict:
        async with Client(build_server(first)) as agent:
            return await _decided(agent)

    decided = asyncio.run(propose())
    proposer, = {r["session"] for r in first.harness("arith").ledger.rows("proposed_by")}
    wf, _ = rig.build(7)
    second = Surface({"arith": {"workflow": wf, "workspace": tmp_path / "ws", "clock": rig.Clock()}})
    refused, question = second.approval_check("arith", decided["decision_digest"], session=proposer)
    assert refused is None and decided["decision_digest"] in question

    async def review() -> dict:
        async with Client(build_server(second), elicitation_handler=answer_ada) as reviewer:
            return _data(await reviewer.call_tool("metaharness_approve", {
                "workflow": "arith", "decision_digest": decided["decision_digest"]}))

    approved = asyncio.run(review())
    assert approved["head_version"] == 1 and approved["completion_state"] == "qualified-draft"
    assert second.harness("arith").store.versions()[-1]["accepted_by"] == "Ada"


def test_a_recorded_decision_that_does_not_match_what_it_names_is_never_activated(tmp_path) -> None:
    from g6.core.ledger import digest

    surface = _surface(tmp_path)

    async def flow() -> dict:
        async with Client(build_server(surface)) as agent:
            return await _decided(agent)

    decided = asyncio.run(flow())
    ledger = surface.harness("arith").ledger
    real = next(r for r in ledger.rows("decision") if r["_id"] == decided["decision_digest"])
    body = {k: v for k, v in real.items() if not k.startswith("_")}
    other_candidate = {**body, "candidate_digest": "0" * 64}            # names a candidate its report never measured
    missing_report = {**body, "report_digest": "1" * 64}                # names a report the workspace does not hold
    ledger.append("decision", digest(other_candidate), other_candidate)
    ledger.append("decision", digest(missing_report), missing_report)
    ledger.append("decision", "f" * 64, body)                           # filed under a digest that is not its own
    wf, _ = rig.build(7)
    elsewhere = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock())
    for ident in (digest(other_candidate), digest(missing_report), "f" * 64, "e" * 64):
        refused = elsewhere.activate(ident, accepted_by="Ada")
        assert refused["warning_card"]["code"] == S.APPROVAL_REQUIRED, ident
        assert refused["completion_state"] == "blocked-escalated"
    assert len(elsewhere.store.versions()) == 1   # survival: the real one still activates
    candidate = decided["decision"]["candidate_digest"]
    assert elsewhere.activate(decided["decision_digest"], accepted_by="Ada", candidate_digest=candidate)[
        "head_version"] == 1

def test_no_metaharness_mcp_operation_reaches_promotion_policy(tmp_path, monkeypatch):
    from dataclasses import replace
    import re
    from g6.core.llm import FakeLLM
    from g6.metaharness import Model, PromotionPolicy
    from g6.metaharness import promotion
    wf, _ = rig.build(7)
    def answer(system, user):
        a, b = map(int, re.search(r"(\d+)\+(\d+)", user).groups())
        return str(a + b if "CAREFUL" in system or a % 2 == 0 else a + b + 1)
    wf = replace(wf, models={"local": Model(FakeLLM(answer))},
                 promotion_policy=PromotionPolicy(draft=0.4, assisted=0.5, autopilot=0.9))
    surface = Surface({"arith": {"workflow": wf, "workspace": tmp_path / "ws", "clock": rig.Clock()}})
    mh = surface.harness("arith")
    mh.confirm("Ada")
    reached = []
    def forbidden(*args, **kwargs):
        reached.append(True)
        raise AssertionError("MCP must not reach the Python-only promotion policy")
    monkeypatch.setattr(promotion, "assess", forbidden)
    server = build_server(surface)
    async def answer_ada(message, response_type, params, context):
        return response_type(value="Ada")
    async def flow():
        names = set(await server.get_tools())
        assert {n for n in names if n.startswith("metaharness_")} == TOOLS
        async with Client(server) as agent:
            inspected = _data(await agent.call_tool("metaharness_inspect", {"workflow": "arith"}))
            diagnosed = _data(await agent.call_tool("metaharness_diagnose", {"workflow": "arith"}))
            decided = await _decided(agent)  # propose, evaluate and decide
        async with Client(server, elicitation_handler=answer_ada) as person:
            approved = _data(await person.call_tool("metaharness_approve", {
                "workflow": "arith", "decision_digest": decided["decision_digest"]}))
            rolled_back = _data(await person.call_tool("metaharness_rollback", {"workflow": "arith", "version": 0}))
        return inspected, diagnosed, decided, approved, rolled_back
    replies = asyncio.run(flow())
    assert all(r["completion_state"] == "qualified-draft" for r in replies)
    assert replies[-2]["head_version"] == 1 and replies[-1]["head_version"] == 2
    assert reached == [] and not any(r.get("kind") == "promotion" for r in mh.ledger.rows("ticket"))
    # PIN: removing the `server.tool()(tool)` registration fails the seven-op coverage; routing any op to assess fails too.
