# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_autogen: AutoGen teams on the family's runtime, bounded by the trusted ceilings (D1,
unbounded_agent_workflow): a team that loops or agrees with itself is ended at its step limit and is never reported as
finished. Every agent talks to the model passed in, through G6's chat client (AGENT_AUTOGEN-04), and every tool call
goes through the runtime's gate; a team G6 cannot see into, or one that would run code, is refused before it starts
(AGENT_AUTOGEN-01). What the team says is a claim (AGENT_AUTOGEN-02). Round robin, selector and swarm teams run as the
spec describes (AGENT_AUTOGEN-03). Tests use FakeLLM; no model, network or paid service."""
from __future__ import annotations

import json
import sys
import threading

import pytest

pytest.importorskip("autogen_agentchat", reason="AutoGen is not installed (pip install 'g6[agents-autogen]')")

import g6.agents.agent_autogen as agent_autogen_pkg  # noqa: E402
from g6.action_gating.audit import ActionGateAuditLogger  # noqa: E402
from g6.agents._runtime import AgentRuntime, CircuitBreaker, CircuitState, codes, run_agent  # noqa: E402
from g6.agents.agent_autogen import AutoGenRuntime, G6ChatClient, GatedTool, TeamSpec  # noqa: E402
from g6.agents.agent_autogen.agent_autogen_block import SENSITIVE_OPS  # noqa: E402
from g6.core import stop as core_stop  # noqa: E402
from g6.core.llm import FakeLLM  # noqa: E402
from g6.core.registry import get_registry  # noqa: E402
from g6.mcp import invoke_reply  # noqa: E402
from g6.safety_engine.schema import RuntimePolicy  # noqa: E402

MEMORY = ActionGateAuditLogger()
PAIR = {"pattern": "round_robin", "max_messages": 20,
        "agents": [{"name": "writer", "system_message": "You are the writer."},
                   {"name": "critic", "system_message": "You are the critic."}]}


def team(**changes: object) -> TeamSpec:
    return TeamSpec.model_validate({**PAIR, **changes})


def speaker(system: str) -> str:
    return "writer" if "the writer" in system else "critic" if "the critic" in system else "selector"


def run(runtime: AutoGenRuntime, **kw: object):
    return run_agent(runtime, runtime.task(), audit_logger=MEMORY, **kw)


def live(runtime: AutoGenRuntime, **kw: object):
    """A run that is not a dry run: the team talks to its model."""
    return run(runtime, dry_run=False, **kw)


# --- D1: a team that loops or agrees with itself is ended at the trusted ceiling ---------------------------------------

def test_d1_a_team_that_agrees_with_itself_for_ever_is_ended_at_the_trusted_ceiling() -> None:
    llm = FakeLLM(default="I agree with everything said so far.")    # nobody ever says the stop text
    outcome = live(AutoGenRuntime(team(max_messages=100), task="Plan the release", llm=llm),
                   policy=RuntimePolicy(max_steps=4))                 # the caller asked for 100; the policy says 4
    assert outcome.state == "blocked-escalated" and outcome.code == codes.STEP_LIMIT, (outcome.code, outcome.message)
    assert len(llm.calls) <= 5
    assert outcome.claimed_status == ""                                # never reported as finished


def test_d1_a_team_that_uses_its_own_message_limit_is_not_reported_as_finished() -> None:
    llm = FakeLLM(default="Let us keep discussing.")
    outcome = live(AutoGenRuntime(team(max_messages=3), task="Plan the release", llm=llm))
    assert outcome.state == "blocked-escalated" and outcome.code == codes.STEP_LIMIT
    assert "3" in outcome.message and len(llm.calls) <= 3


# --- AGENT_AUTOGEN-02: the team's word is a claim -------------------------------------------------------------------

def test_agent_autogen_02_a_team_that_says_verified_is_a_qualified_draft() -> None:
    def reply(system: str, user: str) -> str:
        return "Draft: ship on Friday." if speaker(system) == "writer" else "Verified and safe. TERMINATE"

    outcome = live(AutoGenRuntime(team(), task="Plan the release", llm=FakeLLM(reply)))
    assert outcome.state == "qualified-draft" and outcome.code == codes.SELF_REPORTED, outcome.message
    assert outcome.claimed_status.startswith("Verified and safe")
    assert [m["source"] for m in outcome.detail["transcript"]] == ["user", "writer", "critic"]
    assert outcome.envelope()["evidence"][0]["independent_check"] is None


# --- AGENT_AUTOGEN-03: the team the spec describes, in each pattern --------------------------------------------------

def test_agent_autogen_03_a_selector_team_speaks_in_the_order_its_selector_model_picks() -> None:
    picks = iter(["critic", "writer"])

    def reply(system: str, user: str) -> str:
        if "select the next role" in system + user:
            return next(picks)
        return "Looks fine." if speaker(system) == "critic" else "Done. TERMINATE"

    outcome = live(AutoGenRuntime(team(pattern="selector"), task="Plan the release", llm=FakeLLM(reply)))
    assert outcome.state == "qualified-draft", outcome.message
    assert [m["source"] for m in outcome.detail["transcript"]][1:] == ["critic", "writer"]


def test_agent_autogen_03_a_swarm_hands_the_task_over_as_its_agents_say() -> None:
    def reply(system: str, user: str) -> str:
        if speaker(system) == "writer" and "transfer_to_critic" in system and "Transferred" not in user:
            return json.dumps({"tool_calls": [{"name": "transfer_to_critic", "arguments": {}}]})
        return "Reviewed. TERMINATE"

    swarm = team(pattern="swarm", agents=[{**PAIR["agents"][0], "handoffs": ["critic"]}, PAIR["agents"][1]])
    outcome = live(AutoGenRuntime(swarm, task="Plan the release", llm=FakeLLM(reply)))
    assert outcome.state == "qualified-draft", outcome.message
    kinds = [(m["source"], m["type"]) for m in outcome.detail["transcript"]]
    assert ("writer", "HandoffMessage") in kinds and kinds[-1] == ("critic", "TextMessage")


# --- tools: every call through the gate ----------------------------------------------------------------------------

def _with_tool(tool: str, effect: str = "none", network: bool = False) -> TeamSpec:
    return team(agents=[{**PAIR["agents"][0], "tools": [tool]}, PAIR["agents"][1]],
                tools=[{"name": tool, "effect": effect}], permissions={"network": network})


def _calls_once(tool: str, arguments: dict):
    def reply(system: str, user: str) -> str:
        if speaker(system) == "writer" and "[tool result" not in user:
            return json.dumps({"tool_calls": [{"name": tool, "arguments": arguments}]})
        return "Done. TERMINATE"
    return reply


def test_a_python_tool_runs_once_after_the_gate_lets_it() -> None:
    got: list[dict] = []
    runtime = AutoGenRuntime(_with_tool("lookup"), task="Find it", llm=FakeLLM(_calls_once("lookup", {"q": "x"})),
                             tools={"lookup": lambda **a: got.append(a) or "found x"})
    outcome = live(runtime)
    assert got == [{"q": "x"}] and [d.verdict for d in outcome.decisions] == ["run"]
    assert [e["kind"] for e in outcome.events].count("tool_result") == 1
    assert "found x" in json.dumps(outcome.detail["transcript"])


def test_a_dry_run_the_default_returns_the_plan_and_asks_no_model() -> None:
    sent: list[dict] = []
    llm = FakeLLM(_calls_once("email", {"to": "x@example.com"}))
    runtime = AutoGenRuntime(_with_tool("email", effect="send", network=True), task="Tell them", llm=llm,
                             tools={"email": lambda **a: sent.append(a)})
    outcome = run(runtime)
    assert llm.calls == [] and sent == []                      # zero model calls, and no tool ran
    assert outcome.state == "qualified-draft" and outcome.code == codes.DRY_RUN, (outcome.code, outcome.message)
    plan = outcome.detail["plan"]
    assert plan["agents"] == ["writer", "critic"] and plan["pattern"] == "round_robin" and plan["model"] == "FakeLLM"
    assert plan["max_steps"] == 20 and [t["name"] for t in plan["tools"]] == ["email"] and plan["tools"][0]["outward"]


@pytest.mark.parametrize(("tool", "verdict"), [("agent_runtime.ops", "ran"), ("agent_codex.run", "refused")])
def test_a_g6_component_op_goes_through_the_registry_door_for_untrusted_callers(tool: str, verdict: str) -> None:
    runtime = AutoGenRuntime(_with_tool(tool), task="Look", llm=FakeLLM(_calls_once(tool, {})))
    said = json.dumps(run(runtime, dry_run=False).detail["transcript"])
    assert ("check_task" in said) if verdict == "ran" else ("G6_E_SENSITIVE_COMPONENT_DENIED" in said)


# --- AGENT_AUTOGEN-01 and -04: a team G6 cannot see into is refused ---------------------------------------------------

def _own_team(*agents):
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.teams import RoundRobinGroupChat

    return RoundRobinGroupChat(list(agents), termination_condition=TextMentionTermination("TERMINATE"))


def test_d5_a_team_of_your_own_with_g6s_model_and_gated_tools_runs() -> None:
    from autogen_agentchat.agents import AssistantAgent

    llm = FakeLLM(_calls_once("lookup", {"q": "y"}))
    got: list[dict] = []
    writer = AssistantAgent("writer", G6ChatClient(llm), system_message="You are the writer.",
                            tools=[GatedTool("lookup", lambda **a: got.append(a) or "ok", "look something up")])
    critic = AssistantAgent("critic", G6ChatClient(llm), system_message="You are the critic.")
    runtime = AutoGenRuntime.from_team(_own_team(writer, critic), team_id="mine", task="Find it",
                                       tool_specs=[{"name": "lookup", "effect": "none"}])
    outcome = live(runtime)
    assert outcome.state == "qualified-draft" and got == [{"q": "y"}], outcome.message



def test_agent_autogen_04_an_agent_with_a_model_client_that_is_not_g6s_is_refused_before_anything_runs() -> None:
    from autogen_agentchat.agents import AssistantAgent

    llm = FakeLLM()
    good = AssistantAgent("writer", G6ChatClient(llm))
    foreign = AssistantAgent.__new__(AssistantAgent)
    foreign.__dict__.update(good.__dict__ | {"_model_client": object(), "_name": "critic"})
    outcome = run(AutoGenRuntime.from_team(_own_team(good, foreign), team_id="foreign", task="x"))
    assert outcome.state == "blocked-escalated" and outcome.code == "G6_E_AGENT_AUTOGEN_UNCHECKED_TEAM"
    assert llm.calls == []


def test_agent_autogen_01_a_team_that_could_run_code_or_wait_on_a_person_is_refused() -> None:
    from autogen_agentchat.agents import AssistantAgent, UserProxyAgent

    llm = FakeLLM()
    for other in (UserProxyAgent("person"),):
        outcome = run(AutoGenRuntime.from_team(_own_team(AssistantAgent("writer", G6ChatClient(llm)), other),
                                               team_id="t", task="x"))
        assert outcome.code == "G6_E_AGENT_AUTOGEN_UNCHECKED_TEAM", type(other).__name__
    ungated = AssistantAgent("writer", G6ChatClient(llm), tools=[lambda: "ran outside the gate"])
    assert run(AutoGenRuntime.from_team(_own_team(ungated), team_id="t", task="x")).code == \
        "G6_E_AGENT_AUTOGEN_UNCHECKED_TEAM"
    assert llm.calls == []


def test_agent_autogen_04_the_package_reads_no_key_and_names_no_provider() -> None:
    import re
    from pathlib import Path

    for path in sorted(Path(agent_autogen_pkg.__file__).parent.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"openrouter|import litellm|from litellm|API_KEY|getenv|autogen_ext|import openai", text, re.I), path.name


# --- refusals, stop, the block -----------------------------------------------------------------------------------------

def test_with_no_model_or_models_disabled_a_team_never_starts(monkeypatch) -> None:
    assert run(AutoGenRuntime(team(), task="x")).code == "G6_E_AGENT_AUTOGEN_NO_MODEL"
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    llm = FakeLLM()
    assert live(AutoGenRuntime(team(), task="x", llm=llm)).code == "G6_E_LLM_DISABLED" and llm.calls == []


def test_g4_a_stop_engaged_mid_run_ends_the_team() -> None:
    seen: list[int] = []

    def stop() -> str | None:
        seen.append(1)
        return "stopped by the person" if len(seen) > 3 else None

    llm = FakeLLM(default="More to say.")
    outcome = live(AutoGenRuntime(team(), task="x", llm=llm), stop=stop)
    assert outcome.code == codes.STOPPED and len(llm.calls) <= 3


def test_g4_a_team_thread_that_does_not_stop_is_reported_counts_as_a_failure_and_blocks_the_next_run(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.agents.agent_autogen import adapter

    monkeypatch.setattr(adapter, "_JOIN_S", 0.5)
    release = threading.Event()

    def stuck(system: str, user: str) -> str:
        release.wait(60)                       # a model call that does not come back while the run is ending
        return "Late. TERMINATE"

    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=60)
    try:
        first = live(AutoGenRuntime(team(), task="x", llm=FakeLLM(stuck), deadline_s=2), breaker=breaker)
        assert first.state == "blocked-escalated" and first.code == adapter.WORKER_ALIVE, (first.code, first.message)
        assert first.detail["worker_stopped"] is False and "did not stop" in first.message
        assert breaker.state is CircuitState.OPEN                         # an unconfirmed stop is a failure
        waiting = FakeLLM(default="Done. TERMINATE")
        second = live(AutoGenRuntime(team(), task="x", llm=waiting))
        assert second.code == adapter.WORKER_ALIVE and waiting.calls == []  # nothing new while it is alive
    finally:
        release.set()
    for worker in list(adapter._LIVE):
        worker.join(10)
    after = live(AutoGenRuntime(team(), task="x", llm=FakeLLM(default="Done. TERMINATE")))
    assert after.state == "qualified-draft" and after.detail["worker_stopped"] is True, (after.code, after.message)


def test_g4_an_engaged_stop_starts_nothing() -> None:
    llm = FakeLLM()
    core_stop.engage("test stop")
    try:
        assert run(AutoGenRuntime(team(), task="x", llm=llm)).code == codes.STOPPED
    finally:
        core_stop.release()
    assert llm.calls == []


def test_check_team_shows_the_bounds_and_the_tools_and_runs_nothing() -> None:
    params = {"team": {**PAIR, "max_messages": 100, "agents": [{**PAIR["agents"][0], "tools": ["email"]},
                                                               PAIR["agents"][1]],
                       "tools": [{"name": "email", "effect": "send"}]}, "task": "x"}
    reply = invoke_reply("agent_autogen", "check_team", json.dumps(params))
    result = reply["result"]
    assert reply["ok"] and result["completion_state"] == "qualified-draft"
    assert result["limits"]["max_steps"] <= RuntimePolicy().max_steps
    (email,) = result["tools"]
    assert email["outward"] and not email["permitted"]


def test_g5_run_team_starts_model_calls_so_it_is_sensitive_and_check_team_is_not() -> None:
    assert SENSITIVE_OPS == ("run_team",)
    result = get_registry().invoke("agent_autogen", "run_team", {}, block_sensitive=True)
    assert str(result.failure()).startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")
    assert invoke_reply("agent_autogen", "check_team", "{}")["ok"]


def test_run_team_from_python_uses_the_configured_model_and_with_none_says_so(monkeypatch) -> None:
    monkeypatch.delenv("G6_LLM_PROVIDER", raising=False)
    reply = get_registry().invoke("agent_autogen", "run_team", {"team": PAIR, "task": "x"}).unwrap()
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == "G6_E_AGENT_AUTOGEN_NO_MODEL"


def test_g3_an_approval_cannot_be_written_into_the_input() -> None:
    for extra in ({"approved": True}, {"reviewer": "the owner"}):
        reply = invoke_reply("agent_autogen", "check_team", json.dumps({"team": PAIR, "task": "x", **extra}))
        assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"]
    bad = {**PAIR, "agents": [{"name": "writer", "tools": ["undeclared"]}, PAIR["agents"][1]]}
    assert not invoke_reply("agent_autogen", "check_team", json.dumps({"team": bad, "task": "x"}))["ok"]


def test_without_autogen_a_run_says_how_to_install_it(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "autogen_agentchat", None)
    outcome = run(AutoGenRuntime(team(), task="x", llm=FakeLLM()))
    assert outcome.code == "G6_E_AGENT_AUTOGEN_NOT_INSTALLED" and "pip install" in outcome.message
    reply = invoke_reply("agent_autogen", "check_team", json.dumps({"team": PAIR, "task": "x"}))
    assert reply["result"]["autogen_installed"] is False


def test_the_adapter_implements_the_one_runtime_interface() -> None:
    assert isinstance(AutoGenRuntime(team(), task="x"), AgentRuntime)
    assert invoke_reply("agent_autogen", "ops", "{}")["result"]["ops"] == ["run_team", "check_team"]
    assert agent_autogen_pkg.__g6_extras__ == {"agents-autogen": ("autogen_agentchat", "autogen_core")}