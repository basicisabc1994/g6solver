"""A fresh MCP server reaches each distill tool family: a result, or the one documented refusal with its remedy.

Every call goes through a real in-memory fastmcp session (tests.workflow.kit.call). Declarations are the test-only
H1 stand-in (wire_declarations.issued), never a runtime substitute for owner issuance.
"""
import json
import time

import pytest

from .wire_declarations import issued


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _server():
    from g6.mcp import build_server
    return build_server()


def _call(server, tool, arguments, **kwargs):
    from tests.workflow.kit import call
    return call(server, tool, arguments, **kwargs)


def _grant(*sources, provider="openai:codex"):
    """The person shares these sources with the calling client's provider in their own dialog."""
    import asyncio
    from types import SimpleNamespace
    from g6.mcp.cloud_consent import request_grant

    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action="accept", content={"value": "Person"})
    for source in sources:
        reply = asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id="grant"), source, provider))
        assert reply["status"] == "granted", reply


@pytest.mark.parametrize("argv", [["library", "ls"], ["library", "show", "a" * 64], ["research", "status"],
                                  ["self-model"]])
def test_cli_offers_no_distill_or_self_model_commands(argv, capsys):
    # The TUI/CLI is frozen (CLAUDE.md): these routes live on MCP only.
    from g6.cli import main
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


def _action(**extra):
    from g6.self_model.model import Field, NamedField
    from g6.self_model.simulate import Action, Cost

    now = time.time()

    def measured(value):
        return Field.measured(value, source="client.baseline", observed_at=now, ttl_s=3600.)

    action = Action(action_id="change-distill", kind="code_change", components=("g6.distill",),
                    touched_paths=("docs/research-runner.md",),
                    measures=(NamedField(name="refusal_rate", field=measured(.1)),),
                    cost=Cost(model_calls=measured(0), wall_time_s=measured(60.), allowance_share=measured(.01)))
    return json.loads(action.model_dump_json(exclude={"sha256"})) | extra


def test_fresh_server_reads_its_self_model_and_simulates_from_measured_state(home):
    with issued():
        server = _server()
        reading = _call(server, "g6_self_model", {})
        result = _call(server, "g6_simulate", {"action_json": json.dumps(_action())})
    assert reading["authority"]["level"] == "off" and reading["state"]["stop"] is False
    assert result.get("ok") is not False, result
    assert result["origin"] == "self_prediction"
    # The import graph is read from the installed package: g6.mcp imports g6.distill.
    assert {"g6.distill", "g6.mcp"} <= set(result["affected"])
    # No earlier actions of this kind are recorded on a fresh server, so no forecast is invented.
    [effect] = result["effects"]
    assert effect["prediction"] is None and effect["reference_count"] == 0
    assert result["risk"]["decision"] in {"more_evidence", "owner"}


def test_simulation_refuses_gate_readouts_supplied_by_the_caller(home):
    from g6.self_model.model import Field
    readout = Field.measured("allow", source="g6.action_gating.evaluate_action", observed_at=time.time(),
                             ttl_s=60.).model_dump(mode="json")
    gate = {"action_sha256": "0" * 64, "action_id": "change-distill", "decision": readout,
            "reversibility": readout, "external_effect": readout}
    with issued():
        result = _call(_server(), "g6_simulate", {"action_json": json.dumps(_action(gate=gate))})
    assert result["warning_card"]["code"] == "G6_E_DISTILL_INPUT"
    assert "measured by G6" in result["error"]


def test_fresh_server_reads_research_state_and_names_the_owner_remedy(home):
    with issued():
        server = _server()
        # Research state is improvement data: a fresh client sees it once the person shares that source.
        withheld = _call(server, "g6_research_status", {})
        assert withheld["warning_card"]["code"] == "G6_E_CLOUD_CONSENT_REQUIRED"
        assert "improvement" in withheld["error"]
        _grant("improvement")
        status = _call(server, "g6_research_status", {})
        started = _call(server, "g6_research_start", {})
        digest = _call(server, "g6_research_digest", {})
    assert status == {"authority": "off", "host_configured": False, "status": "off"}
    assert started["warning_card"]["code"] == "G6_E_RESEARCH_AUTHORITY"
    assert "autonomy.json" in started["error"] and "issuance" in started["error"]
    assert digest["ticks"] == 0


def test_research_with_authority_but_no_host_runner_names_the_host_service():
    from g6.mcp.distill_backend import Service
    from g6.self_training.autonomy_gate import Autonomy
    result = Service(config=Autonomy(level="draft_pr", research=True)).research_start()
    assert result["warning_card"]["code"] == "G6_E_RESEARCH_HOST_REQUIRED"
    assert "research runner" in result["error"] and "MCP call" in result["error"]


CASE = {
    "unit_id": "client-unit-1",
    "provenance": {"source": "maintainer", "cohort": "development", "source_ref": "client/repo@abc",
                   "source_sha256": "1" * 64},
    "files": {"pkg/calc.py": "def answer(x):\n    return x + 1\n"},
    "target": "pkg/calc.py",
    "changed_lines": [2],
    "before_tests": "from pkg.calc import answer\n\ndef test_answer():\n    assert answer(2) == 3\n",
    "after_tests": "from pkg.calc import answer\n\ndef test_answer():\n    assert answer(2) is not None\n",
    "known_wrong": ["def answer(x):\n    return x\n"],
}


def _session(server, steps):
    """Run ``steps(call)`` in one client session, as a client that starts a job and polls it does."""
    import asyncio
    from fastmcp import Client
    from mcp.types import Implementation

    async def run():
        async with Client(server, client_info=Implementation(name="codex", version="test")) as session:
            async def call(tool, arguments):
                reply = await session.call_tool(tool, arguments, raise_on_error=False)
                return reply.structured_content
            return await steps(call)
    return asyncio.run(run())


async def _poll(call, job_id):
    import asyncio
    for _ in range(200):
        reply = await call("g6_distill_job", {"job_id": job_id, "detail": "full"})
        if reply.get("status") not in ("pending", "running"):
            return reply
        await asyncio.sleep(.05)
    pytest.fail("distill job did not finish")


def test_fresh_server_labels_a_recorded_case_by_its_own_oracle_and_starts_synthesis(home, monkeypatch):
    from g6.distill.held_out import oracle
    from g6.distill.ledger import Stores
    from g6.distill.records import FailureRecord
    monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.distill_start")
    labelled = []

    def label(case, **kwargs):
        # The real oracle runs confined; its own tests are tests/validation/test_distill_oracle.py.
        labelled.append(case)
        return oracle.OracleResult(case_id=case.case_id, label="positive", reason="known_wrong_witness",
                                   wrong_witnesses=("w",), policy_sha256="0" * 64)
    monkeypatch.setattr(oracle, "label", label)
    # The job reads stored records and the library (component data) and host configuration; its reply is a job's.
    _grant("component_data", "host_configuration", "jobs")

    async def steps(call):
        recorded = await call("g6_failure_record", {"kind": "missed_defect", "family": "test_weakening",
                                                    "case_json": json.dumps(CASE), "note": "assertion loosened"})
        started = await call("g6_distill_start", {"family": "test_weakening"})
        assert started.get("job_id"), started
        return recorded, await _poll(call, started["job_id"])
    with issued():
        recorded, finished = _session(_server(), steps)
    assert recorded["observed_by"] == "self"
    [case] = labelled
    rows = {row.observed_by: row for row in Stores().for_model(FailureRecord).list()}
    derived = rows["independent"]
    assert (derived.split, derived.source, derived.case_ref) == ("training", "experiment", case.case_id)
    assert derived.provenance["labelled_from"] == recorded["record_id"]
    assert [e.kind for e in derived.evidence] == ["oracle"]
    # The training-only refusal is gone; the next stage needs a host model caller, which a fresh server lacks.
    result = finished["result"]
    assert result["warning_card"]["code"] == "G6_E_SYNTH_NO_CALLER", result
    assert result["labels"] == [{"record_id": recorded["record_id"], "label": "positive",
                                   "reason": "known_wrong_witness", "training_record_id": derived.record_id}]


def test_a_record_without_a_runnable_case_is_refused_with_the_case_it_needs(home, monkeypatch):
    monkeypatch.setenv("G6_MCP_SENSITIVE", "metaharness.distill_start")
    with issued():
        server = _server()
        _call(server, "g6_failure_record", {"kind": "missed_defect", "family": "test_weakening",
                                            "case_json": json.dumps({"example": "x"}), "note": "weak"})
        _grant("component_data", "host_configuration")
        result = _call(server, "g6_distill_start", {"family": "test_weakening"})
    assert result["warning_card"]["code"] == "G6_E_SYNTH_TRAINING_ONLY"
    for word in ("g6_failure_record", "before_tests", "after_tests", "known_wrong"):
        assert word in result["error"]

def test_oracle_without_an_isolation_boundary_labels_nothing_and_keeps_its_code(home, monkeypatch):
    from g6.distill import surface
    from g6.distill.held_out import oracle
    from g6.distill.ledger import Stores
    from g6.distill.records import FailureRecord
    from g6.mcp.distill_backend import Service
    from g6.workforce.repo_tests import RepoRefused

    def unconfined(case, **kwargs):
        raise RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "no boundary")
    monkeypatch.setattr(oracle, "label", unconfined)

    class Jobs:
        def start(self, operation):
            return {"result": operation()}
    surface.failure_record("missed_defect", "test_weakening", json.dumps(CASE), "weak")
    result = Service(jobs=Jobs()).distill_start("test_weakening")["result"]
    assert result["warning_card"]["code"] == "G6_E_ISOLATION_UNAVAILABLE"
    assert result["labels"] == []
    assert {row.observed_by for row in Stores().for_model(FailureRecord).list()} == {"self"}
