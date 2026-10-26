# SPDX-License-Identifier: Apache-2.0
"""The debate loop: role-named agents answer in turn, through one injected model caller.

Every turn is one call to the caller you pass; each turn's prompt holds the question, the context and the
earlier usable turns. The agents are not independent: they share one model and each sees what came before.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from g6.core.llm import DEFAULT_MAX_TOKENS, LLMCaller, LLMError

# audit 029, DBT-008: the archive advertised 3 rounds and enforced no bound on rounds or agents. Each turn is
# a model call whose prompt grows with the transcript, so both are hard limits, checked before any call.
MAX_ROUNDS = 3
MAX_AGENTS = 5

MODEL_INFERENCE = "model_inference"
MODEL_CALL_FAILED = "model_call_failed"

_LOCAL_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']+|/(?:home|Users|tmp|var|etc|root|mnt|opt|usr|private)/[^\s\"']*")


def error_text(exc: BaseException) -> str:
    """A short error line with local paths redacted: errors reach the envelope's warning card."""
    return _LOCAL_PATH.sub("<path>", f"{type(exc).__name__}: {exc}")[:300]


class AgentRole(str, Enum):
    PROPOSER = "proposer"
    CRITIC = "critic"
    DEVIL_ADVOCATE = "devil_advocate"
    SYNTHESISER = "synthesiser"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class DebateAgent:
    """A name and a role. The role is only words in the prompt: no persona, model or tools of its own."""

    name: str
    role: AgentRole


@dataclass(frozen=True)
class Exchange:
    """One turn. ``evidence_kind`` is ``model_inference`` for a model reply, ``model_call_failed`` otherwise."""

    agent_name: str
    agent_role: str
    round_number: int
    response: str
    evidence_kind: str = MODEL_INFERENCE
    degraded: bool = False
    error: str = ""

    @property
    def usable(self) -> bool:
        """A non-empty model reply. Allow-list: any other evidence kind (mock, canned, failed) is not usable."""
        return self.evidence_kind == MODEL_INFERENCE and not self.degraded and bool(self.response.strip())


@dataclass(frozen=True)
class Transcript:
    question: str
    context: str = ""
    exchanges: tuple[Exchange, ...] = field(default_factory=tuple)

    @property
    def degraded(self) -> bool:
        return is_mock(self)

    def history_text(self) -> str:
        """The prompt history: question, context and usable turns. A failed turn adds nothing."""
        lines = [f"Question: {self.question}"]
        if self.context.strip():
            lines.append(f"Context: {self.context.strip()}")
        lines += [f"[Round {ex.round_number}] {ex.agent_name} ({ex.agent_role}): {ex.response.strip()}"
                  for ex in self.exchanges if ex.usable]
        return "\n".join(lines)


def is_mock(transcript: Transcript) -> bool:
    """True when the transcript is not clean model output: it has no turn, or a turn is mock, canned or failed."""
    return not transcript.exchanges or any(not ex.usable for ex in transcript.exchanges)


def _check(question: str, agents: Sequence[DebateAgent], rounds: int, llm: LLMCaller | None) -> None:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("G6_E_DEBATE_INPUT: question must not be empty")
    if llm is None:
        raise ValueError("G6_E_NO_MODEL: run_debate needs an LLMCaller")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= MAX_ROUNDS:
        raise ValueError(f"G6_E_DEBATE_LIMIT: rounds must be between 1 and {MAX_ROUNDS}, got {rounds!r}")
    if not 1 <= len(agents) <= MAX_AGENTS:
        raise ValueError(f"G6_E_DEBATE_LIMIT: between 1 and {MAX_AGENTS} agents are allowed, got {len(agents)}")
    names = [a.name.strip() for a in agents if isinstance(a, DebateAgent) and isinstance(a.name, str)]
    if len(names) != len(agents) or not all(names):
        raise ValueError("G6_E_DEBATE_INPUT: every agent must be a DebateAgent with a non-empty name")
    if len(set(names)) != len(names):
        # Agreement is counted per agent name, so two agents with one name would be one voter.
        raise ValueError("G6_E_DEBATE_INPUT: agent names must be unique")


def run_debate(question: str, agents: Sequence[DebateAgent], rounds: int, llm: LLMCaller, context: str = "", *,
               max_tokens: int = DEFAULT_MAX_TOKENS) -> Transcript:
    """Run ``rounds`` x ``agents`` turns in order, one ``llm.complete`` call each.

    Limits and inputs are checked before any call (``ValueError`` with a ``G6_E_DEBATE_*`` code). There is no
    timeout or environment switch here: the caller's ``LLMCaller`` owns those (audit 029, DBT-013). A turn that
    raises or returns no text is recorded as ``degraded`` with its error and an empty response; it is never
    replaced with canned text (DBT-006, DBT-013).
    """
    _check(question, agents, rounds, llm)
    transcript = Transcript(question=question.strip(), context=context or "")
    for round_number in range(1, rounds + 1):
        for agent in agents:
            role = agent.role.value
            system = (f"You are {agent.name.strip()}, the {role.replace('_', ' ')} in a structured debate. "
                      "Reply with your position in a few sentences.")
            user = f"{transcript.history_text()}\n\nAs {role}, respond to: {transcript.question}"
            try:
                text = llm.complete(system, user, max_tokens=max_tokens)
                if not isinstance(text, str) or not text.strip():
                    raise LLMError("the model returned no text")
                turn = Exchange(agent.name.strip(), role, round_number, text)
            except Exception as exc:  # noqa: BLE001 - a failed turn is recorded with its error, never hidden
                turn = Exchange(agent.name.strip(), role, round_number, "", MODEL_CALL_FAILED, degraded=True,
                                error=error_text(exc))
            transcript = Transcript(transcript.question, transcript.context, (*transcript.exchanges, turn))
    return transcript


__all__ = ["MAX_AGENTS", "MAX_ROUNDS", "MODEL_CALL_FAILED", "MODEL_INFERENCE", "AgentRole", "DebateAgent", "Exchange",
           "Transcript", "is_mock", "run_debate"]
