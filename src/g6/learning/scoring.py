# SPDX-License-Identifier: Apache-2.0
"""Instance-keyed scoring of an agent's answer, and the call to the agent itself.

Rewritten from the archive's ``learning_layer/agent_runner.py`` (audit 006, LL-001). The archive looked the expected
answer up by the exact prompt text; once a strategy prefixed a hint the lookup missed, the expected answer became
``""``, and ``score_output`` returned 1.0 for any non-empty answer, so every adapted cycle "passed". Here the scorer
takes the :class:`~g6.learning.schema.TaskInstance` itself and never sees the prompt, and an instance with nothing to
score against scores 0 with ``judge_error="expected_missing"``.

The agent is an injected :class:`g6.core.llm.LLMCaller` or a plain callable ``prompt -> str | AgentReply``. A rubric
instance needs a judge, an injected caller too; without one it scores 0 (``judge_unavailable``), never a guess.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Union

from g6.core.llm import DEFAULT_MAX_TOKENS, LLMCaller, extract_json
from g6.learning.schema import AgentReply, AgentTrace, TaskInstance

#: What a training run calls: a model caller, or any callable from prompt to answer.
Agent = Union[LLMCaller, Callable[[str], Union[str, AgentReply]]]

AGENT_SYSTEM_PROMPT = ("Solve the task directly. Return only the answer or final artifact requested; do not include "
                       "analysis unless the task asks for it.")
_JUDGE_SYSTEM_PROMPT = "You grade answers. Return only a JSON object with keys \"score\" (0.0 to 1.0) and \"reason\"."


def score_output(actual: str, expected: str, mode: str = "normalized_exact") -> float:
    """Deterministic score of ``actual`` against ``expected``. No expected answer scores 0 (LL-001)."""
    actual_norm, expected_norm = _normalize(actual), _normalize(expected)
    if not expected_norm:
        return 0.0  # audit 006, LL-001: the archive returned 1.0 here for any non-empty answer
    if mode == "contains":
        return 1.0 if expected_norm in actual_norm else 0.0
    if mode in {"token_f1", "f1"}:
        return _token_f1(actual_norm, expected_norm)
    return 1.0 if actual_norm == expected_norm else 0.0


def score_instance(instance: TaskInstance, output: str, *, judge: LLMCaller | Callable[[str], str] | None = None
                   ) -> tuple[float, bool, dict[str, Any]]:
    """``(score, passed, signals)`` for ``output`` on ``instance``, using only the instance's own expected answer,
    rubric, ``score_mode`` and ``score_threshold``."""
    meta, mode, rubric = instance.metadata, instance.score_mode, instance.rubric
    signals: dict[str, Any] = {"score_mode": mode, "judge_error": ""}
    if not instance.scorable:
        signals["judge_error"] = "expected_missing"
        return 0.0, False, signals
    if instance.needs_judge:
        if judge is None:
            signals["judge_error"] = "judge_unavailable"
            return 0.0, False, signals
        score, error = _judge(judge, instance.input, output, instance.expected_output, rubric)
        signals["judge_error"] = error
        threshold = float(meta.get("score_threshold", 0.8))
    else:
        score = score_output(output, instance.expected_output, mode)
        threshold = float(meta.get("score_threshold", 1.0 if mode == "normalized_exact" else 0.8))
    signals["score_threshold"] = threshold
    return score, score >= threshold and not signals["judge_error"], signals


def call_agent(agent: Agent, prompt: str) -> AgentReply:
    """Ask ``agent`` for an answer. An agent that raises gave no answer: the reply is empty and the error is traced."""
    start = time.monotonic()
    try:
        if isinstance(agent, LLMCaller):
            out: Any = agent.complete(AGENT_SYSTEM_PROMPT, prompt, max_tokens=DEFAULT_MAX_TOKENS)
        else:
            out = agent(prompt)
    except Exception as exc:  # noqa: BLE001 - any agent failure is recorded as a failed, unanswered task
        return AgentReply("", AgentTrace(num_turns=1, duration_ms=_ms(start), error=f"{type(exc).__name__}: {exc}"))
    if isinstance(out, AgentReply):
        return out
    return AgentReply(str(out), AgentTrace(num_turns=1, duration_ms=_ms(start)))


def _judge(judge: LLMCaller | Callable[[str], str], task: str, actual: str, expected: str,
           rubric: str) -> tuple[float, str]:
    if not expected.strip() and not rubric:
        return 0.0, "expected_missing"  # the archive judged against "[none provided]" with lenient criteria
    prompt = (f"Task:\n{task}\n\nReference answer:\n{expected or '(none: use the rubric)'}\n\n"
              f"Rubric:\n{rubric or 'Score 1.0 only if the answer matches the reference answer.'}\n\n"
              f"Candidate answer:\n{actual}")
    try:
        text = (judge.complete(_JUDGE_SYSTEM_PROMPT, prompt, max_tokens=256) if isinstance(judge, LLMCaller)
                else judge(f"{_JUDGE_SYSTEM_PROMPT}\n\n{prompt}"))
        parsed = extract_json(str(text))
        if not isinstance(parsed, dict):
            raise ValueError("the judge did not return a JSON object")
        return max(0.0, min(1.0, float(parsed.get("score", 0.0)))), ""
    except Exception as exc:  # noqa: BLE001 - a failed judge call scores 0, with the reason
        return 0.0, f"judge_failed: {type(exc).__name__}: {exc}"


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    return re.sub(r"^[`\"']+|[`\"']+$", "", text).strip()


def _token_f1(actual: str, expected: str) -> float:
    actual_tokens, expected_tokens = actual.split(), expected.split()
    if not actual_tokens or not expected_tokens:
        return 0.0
    remaining: dict[str, int] = {}
    for token in expected_tokens:
        remaining[token] = remaining.get(token, 0) + 1
    overlap = 0
    for token in actual_tokens:
        if remaining.get(token, 0):
            overlap += 1
            remaining[token] -= 1
    if not overlap:
        return 0.0
    precision, recall = overlap / len(actual_tokens), overlap / len(expected_tokens)
    return 2 * precision * recall / (precision + recall)


__all__ = ["AGENT_SYSTEM_PROMPT", "Agent", "call_agent", "score_instance", "score_output"]
