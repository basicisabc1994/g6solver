# SPDX-License-Identifier: Apache-2.0
"""``check_claim``: is this claim backed by a sentence in the sources, and is anything contradicting it?"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from g6.core import BLOCKED_ESCALATED, QUALIFIED_DRAFT
from g6.grounding.retrieval import NEGATORS, Passage, Retriever, content_terms, tokens

SUPPORT_THRESHOLD = 0.8   # share of the claim's content terms found in one source sentence
PARTIAL_THRESHOLD = 0.5
STATUSES = ("quoted", "supported", "partial", "conflict", "not_found")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*%?")

# judge(claim, passage_text) -> "entails" | "contradicts" | "neutral"
Judge = Callable[[str, str], str]


@dataclass(frozen=True)
class ClaimCheck:
    claim: str
    status: str                       # one of STATUSES
    completion_state: str             # qualified-draft (quoted/supported) or blocked-escalated
    coverage: float                   # share of claim terms present in the best sentence
    source: str = ""
    sentence: str = ""
    conflicts: tuple[str, ...] = ()
    missing_terms: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    execution_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def split_claims(text: str) -> list[str]:
    """One claim per sentence or bullet; headings and fragments under three words are dropped."""
    parts = re.split(r"(?<=[.!?])\s+|\n+\s*(?:[-*•]|\d+[.)])?\s*", text.strip())
    out = []
    for p in parts:
        p = p.strip().strip("-*• ").rstrip(".")
        if len(p.split()) >= 3 and not p.startswith("#"):
            out.append(p)
    return out


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]


def _negated(text: str) -> bool:
    return any(t in NEGATORS or t.endswith("n't") for t in tokens(text))


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def _normalise(text: str) -> str:
    return " ".join(tokens(text))


def _containing(quote: str, text: str) -> str:
    """The shortest run of ``text``'s sentences (split at sentence punctuation only, so a line break inside a sentence
    does not split it) whose words contain ``quote`` (normalised, padded with spaces), as the source writes it."""
    parts = [s for s in re.split(r"(?<=[.!?;])\s+", text.strip()) if s]
    for size in range(1, len(parts) + 1):
        for start in range(len(parts) - size + 1):
            run = " ".join(parts[start:start + size])
            if quote in f" {_normalise(run)} ":
                return run
    return text.strip()


def _best_sentence(claim: str, passages: list[Passage]) -> tuple[float, str, str, set[str]]:
    want = set(content_terms(claim))
    quote = f" {_normalise(claim)} "
    for p in passages:                       # word for word in a passage, whatever its line breaks
        if quote.strip() and quote in f" {_normalise(p.text)} ":
            # GROUNDING-01: the source's own sentence, so the negation, number and judge checks read what the source
            # says around the quote ("It is not true that ..."), never the claim compared with itself
            return 1.0, _containing(quote, p.text), p.source, set()
    best = (0.0, "", "", want)
    for p in passages:
        for s in _sentences(p.text):
            have = set(content_terms(s))
            cov = len(want & have) / len(want) if want else 0.0
            if cov > best[0]:
                best = (cov, s, p.source, want - have)
    return best


def check_claim(claim: str, sources: Retriever | list[Passage], *, top_k: int = 5,
                judge: Judge | None = None) -> ClaimCheck:
    passages = sources.search(claim, top_k) if isinstance(sources, Retriever) else list(sources)
    coverage, sentence, source, missing = _best_sentence(claim, passages)
    conflicts: list[str] = []
    if sentence:
        if _negated(claim) != _negated(sentence):
            conflicts.append("negation differs: one of the claim and the source says 'not'")
        claim_nums, source_nums = _numbers(claim), _numbers(sentence)
        if claim_nums and source_nums and not claim_nums <= source_nums:
            conflicts.append(f"numbers differ: claim has {', '.join(sorted(claim_nums - source_nums))}, "
                             f"source has {', '.join(sorted(source_nums))}")
        elif claim_nums and not source_nums:
            missing = missing | claim_nums
            coverage = min(coverage, PARTIAL_THRESHOLD)
    judged = None
    judge_failed = False
    judge_error = None
    if judge is not None and sentence:
        try:
            judged = str(judge(claim, sentence)).strip().lower()
            if judged not in ("entails", "contradicts", "neutral"):
                raise ValueError("unknown judge verdict")
        except Exception as exc:
            judged, judge_failed = "neutral", True
            if str(exc).startswith("[G6_E_NLI_NOT_INSTALLED]"):
                judge_error = {"code": "G6_E_NLI_NOT_INSTALLED",
                               "message": "install g6[nli] and provision licensed local weights"}
        if judged == "contradicts":
            conflicts.append("the judge reads the source as contradicting the claim")
    if conflicts and coverage >= PARTIAL_THRESHOLD:
        status = "conflict"
    elif coverage >= SUPPORT_THRESHOLD:
        status = "quoted" if _normalise(claim) in _normalise(sentence) else "supported"
        if judged == "neutral":
            status = "partial"
    elif coverage >= PARTIAL_THRESHOLD:
        status = "partial"
    else:
        status = "not_found"
    state = QUALIFIED_DRAFT if status in ("quoted", "supported") else BLOCKED_ESCALATED
    evidence = {
        "method": "lexical_sentence_overlap",
        "oracle_category": "HEURISTIC_REVIEW",
        "judge": judged,
        "passages_considered": len(passages),
        "not_established": "entailment; the source's own accuracy; text outside the supplied passages",
    }
    if judge_failed:
        evidence["code"] = "G6_E_GROUNDING_JUDGE_FAILED"
        if judge_error is not None:
            evidence["judge_error"] = judge_error
    return ClaimCheck(claim, status, state, round(coverage, 3), source, sentence, tuple(conflicts),
                      tuple(sorted(missing)), evidence)


def _judge_name(judge: Judge | None) -> str | None:
    """Which judge ran: its qualified name, and its injected caller's for a model reviewer; ``None`` for none."""
    if judge is None:
        return None
    named = judge if hasattr(judge, "__qualname__") else type(judge)
    name = f"{named.__module__}.{named.__qualname__}"
    llm = getattr(judge, "llm", None)
    return name if llm is None else f"{name}({type(llm).__module__}.{type(llm).__qualname__})"


def check_claims(text: str, sources: Retriever | list[Passage], **kw: Any) -> list[ClaimCheck]:
    claims = split_claims(text)
    checked = []
    inputs = []
    passage_ids: dict[tuple[str, str, str], str] = {}

    def _passage_id(p: Passage) -> str:
        key = (p.source, p.text, p.provenance)
        if key not in passage_ids:   # each passage hashed once, however many claims it was retrieved for
            passage_ids[key] = hashlib.sha256(json.dumps(key, ensure_ascii=True).encode("ascii")).hexdigest()
        return passage_ids[key]

    for claim in claims:
        passages = sources.search(claim, kw.get("top_k", 5)) if isinstance(sources, Retriever) else list(sources)
        inputs.append([claim, sorted(_passage_id(p) for p in passages)])
        checked.append(check_claim(claim, passages, **kw))
    run = {"judge": _judge_name(kw.get("judge")), "top_k": kw.get("top_k", 5)}
    payload = json.dumps(["grounding-checks-v2", run, inputs], ensure_ascii=True, separators=(",", ":"))
    execution_id = "grounding:" + hashlib.sha256(payload.encode("ascii")).hexdigest()
    return [replace(check, execution_id=execution_id) for check in checked]
