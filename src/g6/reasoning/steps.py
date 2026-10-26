# SPDX-License-Identifier: Apache-2.0
"""Baseline steppers and model-output parsing."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from g6.core.llm import LLMCaller, LLMError
from g6.reasoning.vote import Candidate, VoteResult, weighted_vote


def _escape_lone_backslashes(blob: str) -> str:
    """Keep LaTeX (``\\frac``, ``\\boxed``) verbatim inside JSON strings; keep real escapes intact."""
    out, i, n = [], 0, len(blob)
    while i < n:
        if blob[i] == "\\" and i + 1 < n:
            nxt = blob[i + 1]
            if nxt in ('"', "\\", "/"):
                out.append(blob[i:i + 2])
                i += 2
                continue
            if nxt == "u" and i + 6 <= n and all(c in "0123456789abcdefABCDEF" for c in blob[i + 2:i + 6]):
                out.append(blob[i:i + 6])
                i += 6
                continue
            out.append("\\\\")
            i += 1
            continue
        out.append(blob[i])
        i += 1
    return "".join(out)


def first_json_object(text: str) -> dict:
    """The first balanced ``{...}`` in ``text`` that parses as a JSON object. Raises ``ValueError``."""
    if not text:
        raise ValueError("empty response")
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(_escape_lone_backslashes(text[start:i + 1]))
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in response")


SYSTEM = ("Solve the problem. Reply with one JSON object: "
          '{"reasoning": "<brief working>", "answer": "<final answer only>"}')


@dataclass
class DirectStep:
    """One model call per problem: the baseline every multi-call method must beat."""

    llm: LLMCaller
    system: str = SYSTEM
    max_tokens: int = 4096

    def solve(self, problem: str) -> str:
        """The answer text, or ``""`` when the call failed or the reply has no usable answer (an abstention)."""
        try:
            reply = self.llm.complete(self.system, problem, max_tokens=self.max_tokens)
            answer = first_json_object(reply).get("answer", "")
        except (LLMError, ValueError):
            return ""
        return str(answer).strip() if answer is not None else ""


def self_consistency(llm: LLMCaller, problem: str, n: int, *,
                     equivalent: Callable[[str, str], bool] | None = None) -> VoteResult:
    """``n`` independent direct answers and a plain vote: the compute-matched baseline for ``n`` calls.

    A call that failed or gave no usable answer is an abstention (``VoteResult.abstained``), never an
    answer, and it still counts against a majority of the ``n`` (audit 023 RH-004)."""
    if n < 1:
        raise ValueError("n must be at least 1")
    step = DirectStep(llm)
    return weighted_vote([Candidate(step.solve(problem)) for _ in range(n)], equivalent=equivalent)
