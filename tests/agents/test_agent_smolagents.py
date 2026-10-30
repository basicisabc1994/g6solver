# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_smolagents: smolagents' agents on the family's runtime. Model-written code never runs in G6's
process or unbounded (D1, unbounded_code_execution): each code step runs in g6.core.sandbox, in another process, with
a time limit, no network, a fixed import allow-list and a screen that refuses names and attributes that reach past
it (AGENT_SMOLAGENTS-02). No tool runs from code a caller sends (AGENT_SMOLAGENTS-01); the model is the one passed in
(AGENT_SMOLAGENTS-04); an agent's answer is a draft (AGENT_SMOLAGENTS-03) and a reviewer's signature is a claim that
opens nothing (AGENT_SMOLAGENTS-05). Tests use FakeLLM; no model, network or paid service."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("smolagents", reason="smolagents is not installed (pip install 'g6[agents-smolagents] @ git+https://github.com/basicisabc1994/g6solver')")

import g6.agents.agent_smolagents as agent_smolagents_pkg  # noqa: E402
from g6.action_gating.audit import ActionGateAuditLogger  # noqa: E402
from g6.agents._runtime import AgentRuntime, codes, run_agent  # noqa: E402
from g6.agents.agent_smolagents import AgentSpec, SmolagentsRuntime  # noqa: E402
from g6.agents.agent_smolagents.agent_smolagents_block import SENSITIVE_OPS  # noqa: E402
import g6.agents.agent_smolagents.sandbox as sandbox_mod  # noqa: E402
from g6.agents.agent_smolagents.sandbox import (  # noqa: E402
    ALLOWED_IMPORTS,
    ALLOWED_SYNTAX,
    CODE_REFUSED,
    REFUSED_EVENTS,
    CodeRefused,
    SandboxSession,
    refuse_event,
    screen,
)
from g6.core import confine  # noqa: E402
from g6.core import stop as core_stop  # noqa: E402
from g6.core.llm import FakeLLM  # noqa: E402
from g6.core.registry import get_registry  # noqa: E402
from g6.mcp import invoke_reply  # noqa: E402

MEMORY = ActionGateAuditLogger()
CODE = {"agent_type": "code", "max_steps": 4, "code_timeout_s": 20}


def spec(**changes: object) -> AgentSpec:
    return AgentSpec.model_validate({**CODE, **changes})


def run(runtime: SmolagentsRuntime, **kw: object):
    return run_agent(runtime, runtime.task(), audit_logger=MEMORY, **kw)


def steps(*replies: str):
    """A model that gives each reply in turn, then the last one for ever."""
    said = list(replies)

    def reply(system: str, user: str) -> str:
        return said.pop(0) if len(said) > 1 else said[0]
    return reply


def code(body: str) -> str:
    return f"Thought: I will work it out.\n<code>\n{body}\n</code>"


def observations(outcome) -> str:
    return json.dumps(outcome.detail.get("steps", []))


# --- D1: model-written code runs in the sandbox, bounded, never in G6's process ----------------------------------------

def test_d1_model_written_code_runs_in_another_process_never_in_g6s(monkeypatch) -> None:
    import statistics

    monkeypatch.setattr(statistics, "g6_probe", "set in G6's own process", raising=False)
    llm = FakeLLM(steps(code("import statistics\nprint('probe seen:', hasattr(statistics, 'g6_probe'))\n"
                             "print('mean:', statistics.mean([1, 2, 3]))\nx = 6 * 7\nprint(x)"),
                        code("final_answer(x)")))
    outcome = run(SmolagentsRuntime(spec(), task="What is six times seven?", llm=llm), dry_run=False)
    assert outcome.state == "qualified-draft", (outcome.code, outcome.message, observations(outcome)[:800])
    assert "probe seen: False" in observations(outcome)           # G6's own statistics module was not the one used
    assert "mean: 2" in observations(outcome)
    assert "42" in observations(outcome) and outcome.output == "42"   # x carried into the next step


def test_d1_a_runs_record_says_how_its_code_steps_ran_and_an_unconfined_run_stays_a_draft(monkeypatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    llm = FakeLLM(steps(code("x = 6 * 7"), code("final_answer(x)")))
    outcome = run(SmolagentsRuntime(spec(), task="What is six times seven?", llm=llm), dry_run=False)
    sandbox = outcome.detail["sandbox"]
    # fails if SmolagentsRuntime.run stops putting session.record() in its detail (adapter.py, detail["sandbox"])
    assert sandbox["os_sandbox"] is False and sandbox["isolation"]["backend"] is None
    assert sandbox["isolation"]["why"] == "unavailable: switched off by this test" and len(sandbox["runs"]) == 2
    assert outcome.state == "qualified-draft" and outcome.code == codes.SELF_REPORTED   # unconfined: a draft, no more


def test_d1_code_that_runs_for_ever_is_ended_at_its_time_limit_and_the_agent_goes_on() -> None:
    llm = FakeLLM(steps(code("while True:\n    pass"), code("final_answer('gave up looping')")))
    started = time.monotonic()
    outcome = run(SmolagentsRuntime(spec(code_timeout_s=2), task="Loop", llm=llm), dry_run=False)
    assert time.monotonic() - started < 30
    assert "longer than 2s" in observations(outcome) and outcome.output == "gave up looping"


def _never_run(monkeypatch: pytest.MonkeyPatch) -> list:
    """run_script replaced by a recorder: code the restricted syntax refuses must never reach it."""
    called: list = []

    def recorder(*args: object, **kwargs: object) -> None:
        called.append(args)
        pytest.fail("run_script was called for code the restricted syntax should have refused")
    monkeypatch.setattr(sandbox_mod, "run_script", recorder)
    return called


def test_replaying_earlier_steps_gives_the_random_value_the_model_already_saw() -> None:
    llm = FakeLLM(steps(code("import random\nx = random.random()\nprint('drew', x)"), code("print('again', x)"),
                        code("print('and again', x)"), code("final_answer(x)")))
    outcome = run(SmolagentsRuntime(spec(max_steps=6), task="Draw a number", llm=llm), dry_run=False)
    assert outcome.state == "qualified-draft", (outcome.code, outcome.message, observations(outcome)[:600])
    said = [step["observations"] for step in outcome.detail["steps"]]
    drew = re.search(r"drew (\S+)", said[0]).group(1)
    assert f"again {drew}" in said[1] and f"and again {drew}" in said[2], said    # two replays, the same value
    assert outcome.output == drew and isinstance(outcome.detail["random_seed"], int)   # the seed is in the evidence


def test_d1_a_short_run_deadline_caps_a_code_step_whatever_its_own_limit() -> None:
    llm = FakeLLM(steps(code("while True:\n    pass"), code("final_answer('late')")))
    started = time.monotonic()
    outcome = run(SmolagentsRuntime(spec(code_timeout_s=25), task="Loop", llm=llm, deadline_s=3), dry_run=False)
    took = time.monotonic() - started
    assert took < 15, took                                     # the step's own limit was 25 seconds; the run had 3
    assert outcome.state == "blocked-escalated" and outcome.code == codes.DEADLINE, (outcome.code, outcome.message)


@pytest.mark.parametrize(("source", "construct", "line"), [
    ("point = (1, 2)\nmatch point:\n    case Point(x=0):\n        pass", "match", 2),   # a class pattern reads x by name
    ("total = 0\ndef add():\n    global total\n    total = 1", "global", 3),
    ("class Box:\n    pass", "class", 1),
])
def test_g2_the_restricted_syntax_refuses_a_construct_off_its_list_before_anything_runs(
        source: str, construct: str, line: int, monkeypatch: pytest.MonkeyPatch) -> None:
    called = _never_run(monkeypatch)
    with pytest.raises(CodeRefused) as refused:
        SandboxSession().run(source)
    said = str(refused.value)
    assert CODE_REFUSED in said and f"{construct} " in said and f"line {line}" in said, said
    assert called == []


@pytest.mark.parametrize(("source", "named"), [
    ("import os\nos.system('echo hi')", "importing os"),
    ("open('notes.txt')", "the name open"),
    ("getattr(math, 'pi')", "the name getattr"),
    ("n = 1\nprint(n.__class__)", "the attribute __class__"),
    ("gen = (n for n in [1])\nprint(gen.gi_frame)", "the attribute gi_frame"),
    ("@print\ndef f():\n    pass", "a decorator"),
])
def test_g2_the_name_attribute_and_import_rules_still_hold_on_top(source: str, named: str,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    called = _never_run(monkeypatch)
    with pytest.raises(CodeRefused) as refused:
        SandboxSession().run(source)
    assert CODE_REFUSED in str(refused.value) and named in str(refused.value), str(refused.value)
    assert called == []


def test_g2_the_restricted_syntax_is_a_short_list_pinned_here() -> None:
    assert ALLOWED_SYNTAX == frozenset({
        "Module", "Expr", "Assign", "AugAssign", "For", "While", "If", "Break", "Continue", "Pass", "Import",
        "ImportFrom", "alias", "FunctionDef", "Return", "arguments", "arg", "Lambda", "Try", "ExceptHandler", "Raise",
        "BoolOp", "BinOp", "UnaryOp", "Compare", "Call", "keyword", "IfExp", "Dict", "Set", "List", "Tuple",
        "ListComp", "SetComp", "DictComp", "GeneratorExp", "comprehension", "Constant", "Name", "Attribute",
        "Subscript", "Slice", "Starred", "JoinedStr", "FormattedValue", "Load", "Store", "Del", "And", "Or", "Add",
        "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow", "LShift", "RShift", "BitOr", "BitXor", "BitAnd", "Invert",
        "Not", "UAdd", "USub", "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"})
    for off in ("Match", "Global", "Nonlocal", "ClassDef", "With", "AsyncFunctionDef", "Await", "Yield", "NamedExpr"):
        assert off not in ALLOWED_SYNTAX, off


@pytest.mark.parametrize("event", ["subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
                                   "ctypes.dlopen", "ctypes.call_function", "socket.connect"])
def test_g2_the_childs_audit_hook_refuses_each_named_event(event: str) -> None:
    with pytest.raises(RuntimeError, match="G6 refused"):
        refuse_event(event, ())


def test_g2_the_audit_hook_lets_ordinary_events_pass_and_the_prelude_installs_it() -> None:
    for event in ("open", "import", "compile", "exec", "os.listdir"):
        assert refuse_event(event, ()) is None, event
    assert {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "socket.connect"} <= REFUSED_EVENTS
    assert "addaudithook(refuse_event)" in sandbox_mod._PRELUDE and "%(hook)s" in sandbox_mod._PRELUDE
    assert sandbox_mod._HOOK.startswith("def refuse_event(")       # the very function tested above


def test_d1_the_screen_names_what_it_refuses() -> None:
    assert screen("import math\nprint(math.sqrt(4))") == ""
    assert "os" in screen("import os") and "eval" in screen("eval('1')") and "_os" in screen("random._os")
    assert set(ALLOWED_IMPORTS) >= {"math", "statistics", "re", "datetime"} and "os" not in ALLOWED_IMPORTS


# --- AGENT_SMOLAGENTS-03 / -05: a draft; a signature is a claim ------------------------------------------------------

def test_agent_smolagents_03_an_answer_that_says_verified_is_a_qualified_draft() -> None:
    llm = FakeLLM(steps(code("final_answer('Verified: the answer is 42.')")))
    outcome = run(SmolagentsRuntime(spec(), task="x", llm=llm), dry_run=False)
    assert outcome.state == "qualified-draft" and outcome.code == codes.SELF_REPORTED
    assert outcome.claimed_status.startswith("Verified") and outcome.envelope()["evidence"][0]["independent_check"] is None


def test_agent_smolagents_05_a_reviewer_signature_or_a_hosted_executor_cannot_be_written_in() -> None:
    for extra in ({"reviewer_signature": "the owner"}, {"executor_type": "e2b"}, {"additional_authorized_imports": ["os"]},
                  {"tool_code": "def f(): pass"}):
        reply = invoke_reply("agent_smolagents", "check", json.dumps({"agent": {**CODE, **extra}, "task": "x"}))
        assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"], extra


# --- tool-calling agents: every call through the gate --------------------------------------------------------------

def _lookup(got: list):
    from smolagents import Tool

    class Lookup(Tool):
        name = "lookup"
        description = "Look something up."
        inputs = {"q": {"type": "string", "description": "what to look up"}}
        output_type = "string"

        def forward(self, q: str) -> str:
            got.append(q)
            return f"found {q}"

    return Lookup()


def _action(name: str, arguments: dict) -> str:
    return "Action:\n" + json.dumps({"name": name, "arguments": arguments})


def _tool_spec(tool: str, effect: str = "none", network: bool = False) -> AgentSpec:
    return AgentSpec.model_validate({"agent_type": "toolcalling", "max_steps": 4,
                                     "tools": [{"name": tool, "effect": effect}], "permissions": {"network": network}})


def test_agent_smolagents_01_a_tool_is_one_you_pass_in_and_runs_once_the_gate_lets_it() -> None:
    got: list[str] = []
    llm = FakeLLM(steps(_action("lookup", {"q": "x"}), _action("final_answer", {"answer": "done"})))
    outcome = run(SmolagentsRuntime(_tool_spec("lookup"), task="Find x", llm=llm, tools={"lookup": _lookup(got)}),
                  dry_run=False)
    assert outcome.state == "qualified-draft", outcome.message
    assert got == ["x"] and [d.verdict for d in outcome.decisions] == ["run"]
    assert [e["kind"] for e in outcome.events].count("tool_result") == 1 and "found x" in observations(outcome)


@pytest.mark.parametrize("kind", ["code", "toolcalling"])
def test_a_dry_run_the_default_returns_the_plan_and_asks_no_model(kind: str) -> None:
    got: list[str] = []
    llm = FakeLLM(steps(_action("lookup", {"q": "x"}), _action("final_answer", {"answer": "done"})))
    agent = spec() if kind == "code" else _tool_spec("lookup", effect="send", network=True)
    outcome = run(SmolagentsRuntime(agent, task="Send x", llm=llm, tools={"lookup": _lookup(got)}))
    assert llm.calls == [] and got == []                       # zero model calls, and no tool ran
    assert outcome.state == "qualified-draft" and outcome.code == codes.DRY_RUN, (outcome.code, outcome.message)
    plan = outcome.detail["plan"]
    assert plan["agent_type"] == kind and plan["max_steps"] == 4 and plan["model"] == "FakeLLM"
    assert [t["name"] for t in plan["tools"]] == ([] if kind == "code" else ["lookup"])
    assert outcome.detail["code_runs"] == 0


@pytest.mark.parametrize(("tool", "said"), [("agent_runtime.ops", "check_task"),
                                            ("agent_codex.run", "G6_E_SENSITIVE_COMPONENT_DENIED")])
def test_a_g6_component_op_goes_through_the_registry_door_for_untrusted_callers(tool: str, said: str) -> None:
    name = tool.replace(".", "__")
    llm = FakeLLM(steps(_action(name, {"params": {}}), _action("final_answer", {"answer": "done"})))
    outcome = run(SmolagentsRuntime(_tool_spec(tool), task="Look", llm=llm), dry_run=False)
    assert said in observations(outcome)


def test_a_declared_tool_that_was_not_passed_in_stops_the_run_before_it_starts() -> None:
    llm = FakeLLM()
    outcome = run(SmolagentsRuntime(_tool_spec("lookup"), task="x", llm=llm))
    assert outcome.code == "G6_E_AGENT_SMOLAGENTS_TOOL_MISSING" and llm.calls == []


# --- AGENT_SMOLAGENTS-04: the model passed in, and nothing else ------------------------------------------------------

def test_agent_smolagents_04_with_no_model_or_models_disabled_nothing_runs(monkeypatch) -> None:
    assert run(SmolagentsRuntime(spec(), task="x")).code == "G6_E_AGENT_SMOLAGENTS_NO_MODEL"
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    llm = FakeLLM()
    assert run(SmolagentsRuntime(spec(), task="x", llm=llm), dry_run=False).code == "G6_E_LLM_DISABLED"
    assert llm.calls == []


def test_agent_smolagents_04_the_package_reads_no_key_and_uses_no_model_class_of_smolagents() -> None:
    for path in sorted(Path(agent_smolagents_pkg.__file__).parent.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"LiteLLM|InferenceClient|OpenAIServer|import litellm|API_KEY|getenv|E2B|Modal\w*Executor",
                             text), path.name


def test_run_from_the_registry_uses_the_configured_model_and_with_none_says_so(monkeypatch) -> None:
    monkeypatch.delenv("G6_LLM_PROVIDER", raising=False)
    reply = get_registry().invoke("agent_smolagents", "run", {"agent": CODE, "task": "x"}).unwrap()
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == "G6_E_AGENT_SMOLAGENTS_NO_MODEL"


# --- limits, stop, the block -----------------------------------------------------------------------------------------

def test_an_agent_that_never_answers_is_ended_at_its_step_limit() -> None:
    llm = FakeLLM(default=code("print('still thinking')"))
    outcome = run(SmolagentsRuntime(spec(max_steps=3), task="x", llm=llm), dry_run=False)
    assert outcome.state == "blocked-escalated" and outcome.code == codes.STEP_LIMIT
    assert len(llm.calls) <= 4


def test_g4_an_engaged_stop_starts_nothing() -> None:
    llm = FakeLLM()
    core_stop.engage("test stop")
    try:
        assert run(SmolagentsRuntime(spec(), task="x", llm=llm)).code == codes.STOPPED
    finally:
        core_stop.release()
    assert llm.calls == []


def test_check_shows_the_sandbox_and_the_allow_list_and_runs_nothing() -> None:
    reply = invoke_reply("agent_smolagents", "check", json.dumps({"agent": CODE, "task": "x"}))
    result = reply["result"]
    assert reply["ok"] and result["completion_state"] == "qualified-draft"
    assert result["sandbox"]["network"] == "refused" and result["allowed_imports"] == list(ALLOWED_IMPORTS)
    assert result["sandbox"]["timeout_s"] == 20


def test_check_and_the_skill_say_exactly_what_holds_and_that_it_is_not_a_security_boundary() -> None:
    result = invoke_reply("agent_smolagents", "check", json.dumps({"agent": CODE, "task": "x"}))["result"]
    holds = "a restricted syntax plus a bounded child process with a clean environment and a deadline"
    boundary = "not a security boundary: run agents you do not trust inside your own sandbox (a container or VM)"
    assert result["sandbox"]["holds"] == holds and result["sandbox"]["security_boundary"] is False
    assert holds in result["message"] and boundary in result["message"]
    text = " ".join((Path(agent_smolagents_pkg.__file__).parent / "skill" / "SKILL.md").read_text("utf-8").split())
    assert holds in text and boundary in text


def test_g5_run_asks_a_model_so_it_is_sensitive_and_check_is_not() -> None:
    assert SENSITIVE_OPS == ("run",)
    result = get_registry().invoke("agent_smolagents", "run", {}, block_sensitive=True)
    assert str(result.failure()).startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")


def test_a_code_agent_takes_no_tools_because_code_cannot_reach_the_gate() -> None:
    reply = invoke_reply("agent_smolagents", "check", json.dumps({
        "agent": {**CODE, "tools": [{"name": "lookup", "effect": "none"}]}, "task": "x"}))
    assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"]


def test_without_smolagents_a_run_says_how_to_install_it(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "smolagents", None)
    outcome = run(SmolagentsRuntime(spec(), task="x", llm=FakeLLM()))
    assert outcome.code == "G6_E_AGENT_SMOLAGENTS_NOT_INSTALLED" and "pip install" in outcome.message


def test_the_adapter_implements_the_one_runtime_interface() -> None:
    assert isinstance(SmolagentsRuntime(spec(), task="x"), AgentRuntime)
    assert invoke_reply("agent_smolagents", "ops", "{}")["result"]["ops"] == ["run", "check"]
    assert agent_smolagents_pkg.__g6_extras__ == {"agents-smolagents": ("smolagents",)}