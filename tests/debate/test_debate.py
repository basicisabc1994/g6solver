# SPDX-License-Identifier: Apache-2.0
"""g6.debate against audit 029's regression plan (T-01..T-10 where the code was kept) and its survival controls:
agreement among samples of one model never reads as verified, each agent is one voter, mock and failed turns
count for nothing, limits hold before any call, and genuine agreement between distinct agents still reads as
agreement. Fake model callers only; offline."""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import inspect
import json
from pathlib import Path

import pytest

import g6.debate
from g6.core.llm import FakeLLM, NoModel
from g6.debate import (
    MAX_AGENTS,
    MAX_ROUNDS,
    MODEL_CALL_FAILED,
    MODEL_INFERENCE,
    AgentRole,
    DebateAgent,
    Exchange,
    JudgeResult,
    OverlapResult,
    Transcript,
    is_mock,
    judge_agreement,
    overlap_agreement,
    run_debate,
)

SRC = Path(g6.debate.__file__).parent
AGENTS = [DebateAgent("Ada", AgentRole.PROPOSER), DebateAgent("Bo", AgentRole.CRITIC),
          DebateAgent("Cy", AgentRole.SYNTHESISER)]
AGREEING = {"Ada": "The migration plan reduces downtime and the migration plan reduces cost.",
            "Bo": "The migration plan reduces downtime, so adopt the migration plan.",
            "Cy": "Adopt the migration plan because the migration plan reduces downtime and cost."}
OPPOSED = {"Ada": "The plant should be built.", "Bo": "The plant should not be built."}
MOCK = "[MOCK] A critical weakness in this approach is that edge cases are not considered."

# The archive's labelled floor cases (tests/mvp/debate/fixtures/paraphrase_consensus_cases.json): (kind, replies).
CASES = [
    ("paraphrase", ["We should ship the feature now because users are waiting and momentum matters.",
                    "Release it immediately; the customer base expects delivery and we keep our edge.",
                    "Launch without delay so the audience gets value and we stay ahead."]),
    ("paraphrase", ["The bridge will collapse under that load; the steel is inadequate.",
                    "That span cannot bear the weight; its girders are too slender.",
                    "Structural failure is certain because the supporting metal is undersized."]),
    ("paraphrase", ["Lower the price to grow market share quickly.",
                    "Cut the cost so adoption accelerates across buyers.",
                    "Reduce what we charge and volume will climb fast."]),
    ("paraphrase", ["Hire more engineers to clear the backlog.", "Expand the development staff to finish pending work.",
                    "Bring on additional programmers and the queue shrinks."]),
    ("paraphrase", ["The diet caused the weight loss.", "Eating differently drove the reduction in body mass.",
                    "Her slimmer figure came from the changed meals."]),
    ("opposed", ["The nuclear plant should be built because nuclear power is safe and clean.",
                 "The nuclear plant should not be built because nuclear power is dangerous and dirty.",
                 "Cancel the nuclear plant; nuclear power risks catastrophic nuclear accidents."]),
    ("opposed", ["Increase taxes on income to fund public services and reduce income inequality.",
                 "Decrease taxes on income because high income taxes reduce growth and income.",
                 "Income taxes should be cut, not raised, to protect income and investment."]),
    ("opposed", ["The vaccine mandate protects public health and the public should accept it.",
                 "The vaccine mandate harms public freedom and the public should reject it.",
                 "Reject the vaccine mandate; public coercion over the public is wrong."]),
    ("opposed", ["Remote work improves productivity so the company should expand remote work.",
                 "Remote work hurts productivity so the company should end remote work.",
                 "Remote work damages company culture; the remote policy must stop."]),
    ("literal", list(AGREEING.values())),
    ("literal", ["Database sharding improves throughput; database sharding improves latency.",
                 "Database sharding improves throughput and latency for the database.",
                 "The database benefits because database sharding improves throughput."]),
    ("distinct", ["Plant tomatoes in the spring garden for summer harvest.",
                  "Quantum entanglement violates local realism in physics experiments.",
                  "The orchestra rehearsed the symphony before the evening concert."]),
    ("distinct", ["Bake the sourdough at high temperature for a crisp crust.",
                  "The hurricane shifted course toward the northern coastline overnight.",
                  "Compile the kernel module before loading the network driver."]),
]
TRULY_AGREE = {"paraphrase": True, "literal": True, "opposed": False, "distinct": False}


def by_agent(replies: dict[str, str]) -> FakeLLM:
    """A fake model that answers as the agent its system prompt names."""
    def respond(system: str, user: str) -> str:
        return next(text for name, text in replies.items() if system.startswith(f"You are {name},"))
    return FakeLLM(respond)


def hand(*turns: tuple) -> Transcript:
    """A hand-built one-round transcript from ``(agent, reply[, evidence_kind])`` tuples."""
    return Transcript("q", exchanges=tuple(Exchange(t[0], "proposer", 1, t[1], *t[2:]) for t in turns))


def judge_reply(**fields) -> FakeLLM:
    return FakeLLM(default=json.dumps({"dissenting_views": [], "rationale": "All three back the plan.", **fields}))


def code_strings_and_names() -> tuple[set[str], set[str]]:
    """Every string value and every name used by the package's code, docstrings excluded."""
    strings: set[str] = set()
    names: set[str] = set()
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                strings.add(n.value)
            elif isinstance(n, ast.Name):
                names.add(n.id)
            elif isinstance(n, ast.Attribute):
                names.add(n.attr)
            elif isinstance(n, ast.arg):
                names.add(n.arg)
    return strings, names


def test_dbt001_a_judge_saying_verified_never_makes_the_result_verified():
    transcript = run_debate("Adopt the migration plan?", AGENTS, 1, by_agent(AGREEING))
    assert overlap_agreement(transcript).level == "strong"
    judge = judge_reply(agreement_level="strong", dominant_position="Adopt the plan.", confidence=0.95,
                        completion_state="verified")
    result = judge_agreement(transcript, judge)
    # The archive kept the judge's own "verified" whenever its level matched the floor on a clean transcript
    # (agentic_runtime.py:290, debate_block.py:641-645).
    assert result.completion_state == "qualified-draft"
    envelope = result.to_envelope(request_id="req-1", run_id="run-1")
    assert (envelope["completion_state"], envelope["reliability_label"]) == ("qualified-draft", "qualified-draft")
    assert (envelope["request_id"], envelope["run_id"]) == ("req-1", "run-1")
    assert envelope["warning_card"]["code"] == "G6_E_DEBATE_MODEL_AGREEMENT"
    assert "confidence" not in {f.name for f in dataclasses.fields(JudgeResult)}
    assert "verified" not in judge.calls[0][0]          # the judge is not offered the word
    # Survival: distinct agents that agree, and a judge that agrees, still read as agreement.
    assert (result.judge, result.level, result.diverged_from_floor) == ("applied", "strong", False)


def test_dbt002_no_state_comes_from_an_environment_string(monkeypatch):
    monkeypatch.setenv("G6_LLM_BACKEND", "not-a-backend")
    monkeypatch.setenv("G6_DEBATE_AGENTIC_RUNTIME", "1")
    transcript = run_debate("Adopt the migration plan?", AGENTS, 1, by_agent(AGREEING))
    judged = judge_agreement(transcript, judge_reply(agreement_level="strong", dominant_position="Adopt it."))
    # The archive's capability probe returned "verified" for any backend name outside its offline list
    # (debate_block.py:362, :379, :418). Nothing here probes, and no code path holds the word as a value.
    states = {overlap_agreement(transcript).completion_state, judged.completion_state,
              judge_agreement(transcript, None).completion_state}
    assert states == {"qualified-draft"}
    strings, names = code_strings_and_names()
    assert "verified" not in strings
    assert not names & {"environ", "getenv"}
    assert not hasattr(g6.debate, "source_capabilities")


def test_dbt003_dbt009_opposing_stances_are_never_solved():
    transcript = run_debate("Should the plant be built?", AGENTS[:2], 1, by_agent(OPPOSED))
    floor = overlap_agreement(transcript)
    # The lexical floor is fooled by the shared topic words (a known limitation, DBT-004) ...
    assert floor.level == "strong"
    # ... but nothing turns that into an answer. The archive's DebateStepper returned status="solved" with an
    # AnswerArtifact for MODERATE/STRONG overlap and charged 8 tokens a call (debate_stepper.py:35, :71, :102).
    assert floor.completion_state == "qualified-draft"
    assert not any(hasattr(floor, attr) for attr in ("status", "answer"))
    assert importlib.util.find_spec("g6.debate.debate_stepper") is None
    assert "solved" not in code_strings_and_names()[0]


def test_dbt004_the_overlap_is_not_called_confidence_and_says_it_is_lexical():
    assert "confidence" not in {f.name for f in dataclasses.fields(OverlapResult)}
    for kind, replies in CASES:
        if kind != "opposed":
            continue
        result = overlap_agreement(hand(*((f"A{i}", r) for i, r in enumerate(replies))))
        # The archive published this number as DebateBlockOutput.confidence (consensus.py:153, debate_block.py:684).
        # It is still high for opposing stances: pinned so the limitation is not mistaken for fixed.
        assert result.overlap_fraction >= 0.9 and result.basis == "lexical_overlap"
        envelope = result.to_envelope()
        assert envelope["completion_state"] == "qualified-draft"
        assert envelope["warning_card"]["code"] == "G6_E_DEBATE_LEXICAL_ONLY"
        assert envelope["basis"] == "lexical_overlap"


def test_the_floor_is_right_on_4_of_13_labelled_cases():
    right = {kind: 0 for kind in TRULY_AGREE}
    for kind, replies in CASES:
        reads_agree = overlap_agreement(hand(*((f"A{i}", r) for i, r in enumerate(replies)))).overlap_fraction >= 0.5
        right[kind] += reads_agree == TRULY_AGREE[kind]
    # Locked from the archive's own eval (test_debate_floor_eval.py): it misses every paraphrase and is fooled by
    # every opposing stance; it is right only on the controls. A heuristic change must re-baseline this.
    assert right == {"paraphrase": 0, "literal": 2, "opposed": 0, "distinct": 2}


def test_dbt005_one_agents_repeated_rounds_are_one_voter():
    transcript = run_debate("Adopt the plan?", AGENTS[:1], 3, FakeLLM(default=AGREEING["Ada"]))
    assert len(transcript.exchanges) == 3
    result = overlap_agreement(transcript)
    # The archive counted every round as a voter, so three identical turns read STRONG (consensus.py:136-142).
    assert (result.level, result.voters, result.overlap_fraction) == ("none", 1, 0.0)
    assert result.completion_state == "blocked-escalated"


def test_dbt005_a_lone_position_does_not_agree_with_itself():
    unrelated = {"Ada": "Plant tomatoes in the spring garden for summer harvest.",
                 "Bo": "The hurricane shifted course toward the northern coastline overnight."}
    # The archive counted the dominant turn as agreeing with itself: 1 of 2 turns, or Ada's 3 echoing rounds out
    # of 6, read MODERATE (consensus.py:149-153).
    for rounds in (1, 3):
        result = overlap_agreement(run_debate("What now?", AGENTS[:2], rounds, by_agent(unrelated)))
        assert (result.level, result.overlap_fraction, result.voters) == ("none", 0.0, 2)
        assert result.completion_state == "qualified-draft"


def test_dbt005_distinct_agents_that_agree_still_read_strong():
    result = overlap_agreement(run_debate("Adopt the migration plan?", AGENTS, 2, by_agent(AGREEING)))
    assert (result.level, result.overlap_fraction, result.voters, result.ignored_turns) == ("strong", 1.0, 3, 0)
    assert result.dissent == ()


def test_dbt006_there_is_no_mock_switch_and_no_canned_turn():
    assert "use_mock_llm" not in inspect.signature(run_debate).parameters
    transcript = run_debate("Adopt the plan?", AGENTS, 1, NoModel())
    # The archive's two MCP tools ran DebateLoop(use_mock_llm=True) and returned canned "[MOCK]" turns
    # (development/g6_mcp_server_fast.py:331, benchmarks/bbeh/bbeh_mcp_server.py:50).
    for ex in transcript.exchanges:
        assert (ex.degraded, ex.response, ex.evidence_kind) == (True, "", MODEL_CALL_FAILED)
        assert "G6_E_NO_MODEL" in ex.error
    assert is_mock(transcript) and transcript.degraded
    assert overlap_agreement(transcript).completion_state == "blocked-escalated"


def test_dbt007_every_file_is_apache_licensed():
    banned = ("Propri" + "etary", "Copy" + "right")
    for path in [*SRC.rglob("*.py"), *Path(__file__).parent.rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# SPDX-License-Identifier: Apache-2.0"), path
        assert not any(word in text for word in banned), path


def test_dbt008_limits_are_checked_before_any_model_call():
    fake = FakeLLM()
    crowd = [DebateAgent(f"A{i}", AgentRole.CRITIC) for i in range(20)]
    # The archive's block took any max_rounds (max(1, int(value))) and any number of agents (debate_block.py:107-152).
    for agents, rounds in ((crowd, 500), (crowd, 1), (AGENTS, 500), (AGENTS, 0)):
        with pytest.raises(ValueError, match="G6_E_DEBATE_LIMIT"):
            run_debate("Adopt the plan?", agents, rounds, fake)
    assert fake.calls == []
    # Survival: at the limit, every turn is exactly one call through the caller.
    panel = [DebateAgent(f"A{i}", AgentRole.CRITIC) for i in range(MAX_AGENTS)]
    transcript = run_debate("Adopt the plan?", panel, MAX_ROUNDS, fake)
    assert len(fake.calls) == len(transcript.exchanges) == MAX_AGENTS * MAX_ROUNDS


def test_inputs_are_checked_before_any_model_call():
    fake = FakeLLM()
    for question, agents, llm in (("  ", AGENTS, fake), ("Q?", [AGENTS[0], DebateAgent("Ada", AgentRole.CRITIC)], fake),
                                  ("Q?", [DebateAgent(" ", AgentRole.CRITIC)], fake), ("Q?", AGENTS, None)):
        with pytest.raises(ValueError, match="G6_E_"):
            run_debate(question, agents, 1, llm)
    assert fake.calls == []


def test_dbt011_mock_and_failed_turns_are_ignored():
    transcript = hand(("Ada", MOCK, "mock_response"), ("Bo", MOCK, "llm_fallback_mock"), ("Cy", AGREEING["Cy"]))
    # The archive's floor counted the two identical canned turns as agreeing: 2 of 3, MODERATE (consensus.py:86).
    result = overlap_agreement(transcript)
    assert (result.level, result.voters, result.ignored_turns) == ("none", 1, 2)
    assert is_mock(transcript)
    # An evidence kind outside the allow-list is not model output either.
    assert not Exchange("Dee", "critic", 1, AGREEING["Bo"], "cached").usable


def test_dbt011_real_agreement_survives_beside_a_mock_turn():
    transcript = hand(*AGREEING.items(), ("Dee", MOCK, "mock_response"))
    result = overlap_agreement(transcript)
    assert (result.level, result.voters, result.ignored_turns) == ("strong", 3, 1)
    assert is_mock(transcript)                           # the transcript as a whole is still flagged
    judge = judge_reply(agreement_level="strong", dominant_position="Adopt the plan.")
    judge_agreement(transcript, judge)
    positions = json.loads(judge.calls[0][1])["positions"]
    assert [p["agent"] for p in positions] == ["Ada", "Bo", "Cy"] and MOCK not in judge.calls[0][1]


def test_dbt010_there_is_no_propose_op():
    # The archive's propose op said "Proposition submitted" and stored nothing (debate_block.py:778).
    assert "propose" not in g6.debate.__all__ and not hasattr(g6.debate, "propose")


def test_dbt012_the_docs_say_no_benefit_has_been_shown():
    doc = " ".join(g6.debate.__doc__.split())
    assert "No benefit has been shown" in doc and "g6.reasoning.compare" in doc


class Flaky:
    """Fails its first call the way a caller's own timeout would, then answers; records each call's budget."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.budgets: list[int] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        self.prompts.append(user)
        self.budgets.append(max_tokens)
        if len(self.prompts) == 1:
            raise TimeoutError("the caller's own 15 s timeout")
        return AGREEING["Bo"]


def test_dbt013_a_failed_turn_is_recorded_and_the_caller_owns_timeouts():
    flaky = Flaky()
    transcript = run_debate("Adopt the plan?", AGENTS[:2], 2, flaky, context="Budget is tight.", max_tokens=321)
    first, second = transcript.exchanges[:2]
    # The archive hard-coded LLMBlock(timeout=10.0) and swapped a failed turn for canned text (debate.py:95, :187).
    assert (first.degraded, first.response, first.evidence_kind) == (True, "", MODEL_CALL_FAILED)
    assert first.error == "TimeoutError: the caller's own 15 s timeout"
    # Survival: the next turn still runs and is a model turn; the failed turn adds nothing to its prompt.
    assert (second.degraded, second.evidence_kind) == (False, MODEL_INFERENCE)
    assert "Context: Budget is tight." in flaky.prompts[1] and "Ada" not in flaky.prompts[1]
    assert f"[Round 1] Bo (critic): {AGREEING['Bo']}" in flaky.prompts[2]
    assert flaky.budgets == [321] * 4
    assert "timeout" not in code_strings_and_names()[1]


def test_an_error_does_not_carry_a_local_path_into_the_warning_card():
    class Missing:
        def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
            # built at runtime so no home-path literal sits in this file (owner-approved, R-08); same value
            raise FileNotFoundError(2, "No such file", "C:" + r"\Users\someone\models\m.gguf /home/someone/m.gguf")

    transcript = run_debate("Adopt the plan?", AGENTS[:1], 1, Missing())
    assert "<path>" in transcript.exchanges[0].error and "someone" not in transcript.exchanges[0].error
    result = judge_agreement(run_debate("Adopt the migration plan?", AGENTS, 1, by_agent(AGREEING)), Missing())
    card = result.to_envelope()["warning_card"]
    assert result.judge == "degraded" and "someone" not in json.dumps(card)


def test_an_empty_reply_is_a_failed_turn():
    transcript = run_debate("Adopt the plan?", AGENTS[:1], 1, FakeLLM(default="   "))
    assert transcript.exchanges[0].degraded and "no text" in transcript.exchanges[0].error


def test_the_judge_is_not_asked_without_two_usable_agents():
    judge = FakeLLM(default="{}")
    result = judge_agreement(hand(("Ada", MOCK, "mock_response"), ("Bo", AGREEING["Bo"])), judge)
    assert judge.calls == [] and result.judge == "not_run" and result.completion_state == "blocked-escalated"
    envelope = result.to_envelope()
    assert envelope["completion_state"] == "blocked-escalated"
    assert envelope["warning_card"]["code"] == "G6_E_DEBATE_NO_CONTENT"


def test_a_judge_that_reads_opposing_stances_is_flagged_as_diverging():
    transcript = run_debate("Should the plant be built?", AGENTS[:2], 1, by_agent(OPPOSED))
    result = judge_agreement(transcript, judge_reply(agreement_level="none", dominant_position="",
                                                     dissenting_views=["build it", "do not build it"]))
    assert (result.level, result.floor.level, result.diverged_from_floor) == ("none", "strong", True)
    assert result.completion_state == "qualified-draft" and result.dissent == ("build it", "do not build it")
    assert result.to_envelope()["warning_card"]["code"] == "G6_E_DEBATE_JUDGE_DIVERGED"


@pytest.mark.parametrize("llm", [
    NoModel(),
    FakeLLM(default="no json here"),
    FakeLLM(default="[1, 2]"),
    FakeLLM(default=json.dumps({"agreement_level": "total", "rationale": "r", "dominant_position": "x"})),
    FakeLLM(default=json.dumps({"agreement_level": "strong", "dominant_position": "x"})),
    FakeLLM(default=json.dumps({"agreement_level": "strong", "rationale": "r"})),
], ids=["call-fails", "no-json", "not-an-object", "unknown-level", "no-rationale", "no-dominant"])
def test_a_failed_or_malformed_judge_leaves_the_floor_standing(llm):
    transcript = run_debate("Adopt the migration plan?", AGENTS, 1, by_agent(AGREEING))
    result = judge_agreement(transcript, llm)
    assert (result.judge, result.level, result.floor.level) == ("degraded", "strong", "strong")
    assert result.error and result.completion_state == "qualified-draft"
    assert result.to_envelope()["warning_card"]["code"] == "G6_E_DEBATE_JUDGE_FAILED"


def test_every_envelope_passes_the_library_envelope_check() -> None:
    """Integration: the floor, an applied judge (whose reply claims verified) and a blocked result all pass
    ``g6.invariants.check_envelope``, and none is above qualified-draft."""
    from g6.invariants import check_envelope

    class Replies:
        def __init__(self, *texts: str) -> None:
            self.texts = list(texts)

        def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
            return self.texts.pop(0)

    pair = AGENTS[:2]
    transcript = run_debate("Adopt the migration plan?", pair, 1, Replies(AGREEING["Ada"], AGREEING["Bo"]))
    judge = Replies(json.dumps({"agreement_level": "strong", "dominant_position": "adopt", "dissenting_views": [],
                                "rationale": "same position", "completion_state": "verified"}))
    lone = run_debate("Adopt the migration plan?", pair[:1], 1, Replies(AGREEING["Ada"]))
    for result in (overlap_agreement(transcript), judge_agreement(transcript, judge), overlap_agreement(lone)):
        envelope = result.to_envelope(request_id="req-1", run_id="run-1")
        assert check_envelope(envelope) == [], envelope
        assert envelope["completion_state"] in ("qualified-draft", "blocked-escalated")
