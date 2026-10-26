# SPDX-License-Identifier: Apache-2.0
"""How far a debate's agents agree: a lexical floor and an optional model judge. Neither is verification.

Agreement between samples of one model is not verification (audit 029, DBT-001/002), so the best state either
result gives is ``qualified-draft``; it is ``blocked-escalated`` when fewer than two agents gave a usable turn.
Each agent votes once, with its last usable turn (DBT-005). Mock, canned and failed turns are ignored (DBT-011).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from g6.core.llm import DEFAULT_MAX_TOKENS, LLMCaller, extract_json
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.debate.debate import Transcript, error_text

LEVELS = ("none", "weak", "moderate", "strong")

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_STOP_WORDS = frozenset({
    "about", "above", "after", "again", "against", "approach", "because", "being", "between", "could", "debate",
    "from", "have", "into", "known", "more", "must", "only", "other", "over", "should", "that", "their", "there",
    "these", "this", "those", "what", "when", "where", "which", "while", "with", "would",
})
_POSITION_CHARS = 600
_LEXICAL_NOTE = ("Lexical overlap only: it misses paraphrase, and opposing stances that repeat the topic's words "
                 "read as agreement. Not a confidence and not verification.")


def _key_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 3 and w not in _STOP_WORDS}


def _overlaps(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    shared = len(left & right)
    return shared >= 2 and shared / min(len(left), len(right)) >= 0.25


def _level(fraction: float) -> str:
    # Fixed cut-offs carried over from the archive; never calibrated (DBT-004).
    return "strong" if fraction >= 0.8 else "moderate" if fraction >= 0.5 else "weak" if fraction >= 0.3 else "none"


def _final_positions(transcript: Transcript) -> tuple[list[tuple[str, str, str]], int]:
    """Each agent's last usable turn as ``(agent, role, text)`` in first-seen order, and how many turns were ignored."""
    final: dict[str, tuple[str, str]] = {}
    ignored = 0
    for ex in transcript.exchanges:
        if ex.usable:
            final[ex.agent_name] = (ex.agent_role, ex.response.strip())
        else:
            ignored += 1
    return [(name, role, text) for name, (role, text) in final.items()], ignored


def _envelope(result: Any, code: str, message: str, evidence: list[dict[str, Any]], request_id: str,
              run_id: str) -> dict[str, Any]:
    envelope = envelope_fields(reliability_label=result.completion_state, warning_card=warning_card(code, message),
                               evidence=evidence, request_id=request_id, run_id=run_id, degradation_reason=message)
    return {**asdict(result), **envelope}


@dataclass(frozen=True)
class OverlapResult:
    """Lexical overlap among the agents' final turns.

    ``overlap_fraction`` is the share of voting agents in the largest group whose final turns share content words
    with one agent's; a lone position is no group, so it is 0.0 when no two agents overlap. It is not a probability
    or a confidence (DBT-004), and it is high for opposing stances that repeat the same topic words.
    """

    level: str
    overlap_fraction: float
    dominant: str = ""
    dissent: tuple[str, ...] = ()
    voters: int = 0
    ignored_turns: int = 0
    basis: str = field(default="lexical_overlap", init=False)

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.voters >= 2 else "blocked-escalated"

    def _evidence(self) -> dict[str, Any]:
        return {"kind": "debate_overlap", "basis": self.basis, "voters": self.voters,
                "ignored_turns": self.ignored_turns, "overlap_fraction": self.overlap_fraction}

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        """The result as a library envelope (``g6.core.reliability_envelope``) with a ``G6_E_*`` warning card."""
        if self.completion_state == "blocked-escalated":
            code, message = "G6_E_DEBATE_NO_CONTENT", f"{self.voters} agent(s) gave a usable turn; agreement needs two."
        else:
            code, message = "G6_E_DEBATE_LEXICAL_ONLY", _LEXICAL_NOTE
        return _envelope(self, code, message, [self._evidence()], request_id, run_id)


def overlap_agreement(transcript: Transcript) -> OverlapResult:
    """The keyword-overlap floor over each agent's last usable turn. Lexical only (see :class:`OverlapResult`)."""
    positions, ignored = _final_positions(transcript)
    texts = [text for _, _, text in positions]
    n = len(texts)
    words = [_key_words(t) for t in texts]
    counts = [sum(1 for j in range(n) if j != i and _overlaps(words[i], words[j])) for i in range(n)]
    if not counts or max(counts) == 0:
        # DBT-005: the archive counted the dominant turn as agreeing with itself, so two unrelated replies read
        # "moderate" and one agent's repeated rounds read "strong". Agreement needs two agents that overlap.
        return OverlapResult("none", 0.0, "", tuple(texts[:3]) if n >= 2 else (), n, ignored)
    best = counts.index(max(counts))
    group = [i for i in range(n) if i == best or _overlaps(words[best], words[i])]
    fraction = len(group) / n
    dissent = tuple(texts[i] for i in range(n) if i not in group)[:3]
    return OverlapResult(_level(fraction), fraction, texts[best], dissent, n, ignored)


_JUDGE_SYSTEM = (
    "You read the final positions of the agents in a debate and judge whether they agree by meaning, not by "
    "shared words: replies that say the same thing in different words agree; replies that repeat the topic's "
    "words but take opposite stances do not. Reply with one JSON object: {\"agreement_level\": \"strong\" (at "
    "least 80% of the agents share one position), \"moderate\" (at least 50%), \"weak\" (at least 30%) or "
    "\"none\", \"dominant_position\": the shared position or \"\", \"dissenting_views\": [the differing "
    "positions], \"rationale\": one or two sentences}.")


@dataclass(frozen=True)
class JudgeResult:
    """A model's reading of whether the agents' final positions agree, beside the lexical floor.

    ``judge`` is ``applied`` (``level`` is the model's reading), ``degraded`` (the call or its reply failed, and the
    floor's level stands) or ``not_run`` (no caller, or fewer than two agents gave a usable turn).
    """

    level: str
    floor: OverlapResult
    judge: str
    dominant: str = ""
    dissent: tuple[str, ...] = ()
    rationale: str = ""
    diverged_from_floor: bool = False
    error: str = ""

    @property
    def completion_state(self) -> str:
        # audit 029, DBT-001: computed here, never read from the model's reply, and never above qualified-draft.
        return self.floor.completion_state

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        """The result as a library envelope (``g6.core.reliability_envelope``) with a ``G6_E_*`` warning card."""
        if self.completion_state == "blocked-escalated":
            code, message = ("G6_E_DEBATE_NO_CONTENT",
                             f"{self.floor.voters} agent(s) gave a usable turn; agreement needs two.")
        elif self.judge == "degraded":
            code, message = ("G6_E_DEBATE_JUDGE_FAILED",
                             f"The model judge failed ({self.error}); the lexical floor stands.")
        elif self.judge == "not_run":
            code, message = "G6_E_DEBATE_LEXICAL_ONLY", _LEXICAL_NOTE
        elif self.diverged_from_floor:
            code, message = ("G6_E_DEBATE_JUDGE_DIVERGED",
                             f"The model judge read {self.level!r}; the lexical floor read {self.floor.level!r}.")
        else:
            code, message = ("G6_E_DEBATE_MODEL_AGREEMENT",
                             "One model's reading of agreement among samples of one model; not verification.")
        judged = {"kind": "debate_judge", "judge": self.judge, "level": self.level,
                  "diverged_from_floor": self.diverged_from_floor}
        return _envelope(self, code, message, [self.floor._evidence(), judged], request_id, run_id)


def _read_reply(reply: object) -> tuple[str, str, tuple[str, ...], str]:
    """The fields the judge may set. A ``completion_state`` or ``confidence`` in the reply is not read (DBT-001/004)."""
    if not isinstance(reply, dict):
        raise ValueError("the judge's reply is not a JSON object")
    level = str(reply.get("agreement_level") or "").strip().lower()
    if level not in LEVELS:
        raise ValueError(f"agreement_level must be one of {LEVELS}, got {level!r}")
    dominant = str(reply.get("dominant_position") or "").strip()
    rationale = str(reply.get("rationale") or "").strip()
    if not rationale:
        raise ValueError("the judge gave no rationale")
    if level != "none" and not dominant:
        raise ValueError("an agreement level above none needs a dominant_position")
    raw = reply.get("dissenting_views")
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
    return level, dominant, tuple(s for s in (str(v).strip() for v in items) if s)[:3], rationale


def judge_agreement(transcript: Transcript, llm: LLMCaller | None, *,
                    max_tokens: int = DEFAULT_MAX_TOKENS) -> JudgeResult:
    """Ask ``llm`` (one call) whether the agents' final positions agree by meaning; the floor is always computed.

    The model sees the same voters as the floor: one final usable position per agent. It is not asked when fewer
    than two agents gave a usable turn, so it cannot find agreement in a transcript with no usable content. A
    reading that differs from the floor is flagged ``diverged_from_floor``; a failed call or a malformed reply
    leaves the floor standing, marked ``degraded``.
    """
    floor = overlap_agreement(transcript)
    if llm is None or floor.voters < 2:
        return JudgeResult(floor.level, floor, "not_run", floor.dominant, floor.dissent)
    positions, _ = _final_positions(transcript)
    payload = {"question": transcript.question, "context": transcript.context[:2000],
               "positions": [{"agent": a, "role": r, "position": t[:_POSITION_CHARS]} for a, r, t in positions]}
    try:
        reply = extract_json(llm.complete(_JUDGE_SYSTEM, json.dumps(payload), max_tokens=max_tokens))
        level, dominant, dissent, rationale = _read_reply(reply)
    except Exception as exc:  # noqa: BLE001 - any failure leaves the lexical floor standing
        return JudgeResult(floor.level, floor, "degraded", floor.dominant, floor.dissent, error=error_text(exc))
    return JudgeResult(level, floor, "applied", dominant, dissent, rationale, level != floor.level)


__all__ = ["LEVELS", "JudgeResult", "OverlapResult", "judge_agreement", "overlap_agreement"]
