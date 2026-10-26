# SPDX-License-Identifier: Apache-2.0
"""g6.debate — role-named agents answer a question in turn through one model, with two readings of how far they agree.

g6.debate — role-named agents answer a question in turn through one model, with two readings of how far they
agree, neither of which is verification (audit 029 §11).

Capability label: ``implemented``.

    from g6.debate import AgentRole, DebateAgent, judge_agreement, overlap_agreement, run_debate
    agents = [DebateAgent("Ada", AgentRole.PROPOSER), DebateAgent("Bo", AgentRole.CRITIC)]
    transcript = run_debate("Should we adopt the migration plan?", agents, 2, llm)   # 4 calls to llm
    floor = overlap_agreement(transcript)       # lexical: level, overlap_fraction, dissent
    judged = judge_agreement(transcript, llm)   # one more call: a model's reading, beside the floor
    judged.completion_state                     # "qualified-draft" at best, never "verified"

``run_debate`` checks its limits (``MAX_ROUNDS`` = 3, ``MAX_AGENTS`` = 5) before any call. It has no timeout and
reads no environment: the ``LLMCaller`` you pass owns both. A failed turn is kept as ``degraded`` with its error,
never replaced by canned text. ``overlap_agreement`` counts each agent once, by its last usable turn, and ignores
mock, canned and failed turns. ``overlap_fraction`` is a share of agents, not a confidence: the floor misses
paraphrase and reads opposing stances that share topic words as agreement (4 of 13 labelled cases right).
``judge_agreement`` asks the model to judge by meaning; the model never sets the state, which code computes.
``is_mock`` says whether a transcript is anything other than clean model output.

The agents are not independent voters: one model, and each turn sees the earlier ones. No benefit has been
shown: nothing measures whether a debate answers better than one call, or than self-consistency at the same
number of calls (audit 029, DBT-012). To test one, run it through ``g6.reasoning.compare``, the library's
compute-matched comparison gate, against ``g6.reasoning.self_consistency`` as the baseline.

Not ported from the archive's ``debate`` (audit 029):

* ``debate_stepper.py``: it turned keyword overlap into ``solved`` and charged a fixed 8 tokens a call
  (DBT-003, DBT-009).
* ``DebateBlock`` and its eight ops: ``source_capabilities``, which stamped ``verified`` from an environment
  string (DBT-002), ``propose``, which reported a submission it never made (DBT-010), and five static descriptors.
* the ``use_mock_llm`` switch and canned ``[MOCK]`` turns, and the MCP wrappers in ``development/`` and
  ``benchmarks/`` that hard-wired it (DBT-006); the fixed 10 s timeout and the ``G6_LLM_BACKEND`` /
  ``G6_DEBATE_AGENTIC_RUNTIME`` gates (DBT-013).
* the judge's self-reported ``confidence`` and ``completion_state`` (DBT-001, DBT-004), its JSON repair retries,
  and the clamp that acted only on that confidence; the decision-trace summary (``agentic_evidence.py``), whose
  content the results now carry as fields (its path redaction is kept for error text).
* the regex detector over a free-text trace (DBT-011), and the ``patterns.py`` / ``skills.py`` / ``skill/`` /
  ``strategies/`` catalogues, which were documentation.
"""
from g6.debate.agreement import LEVELS, JudgeResult, OverlapResult, judge_agreement, overlap_agreement
from g6.debate.debate import (
    MAX_AGENTS,
    MAX_ROUNDS,
    MODEL_CALL_FAILED,
    MODEL_INFERENCE,
    AgentRole,
    DebateAgent,
    Exchange,
    Transcript,
    is_mock,
    run_debate,
)

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unchallenged_answer",)
__g6_introduces__: tuple[str, ...] = ("excessive_token_use",)

__all__ = ["LEVELS", "MAX_AGENTS", "MAX_ROUNDS", "MODEL_CALL_FAILED", "MODEL_INFERENCE", "AgentRole", "DebateAgent",
           "Exchange", "JudgeResult", "OverlapResult", "Transcript", "is_mock", "judge_agreement", "overlap_agreement",
           "run_debate"]
