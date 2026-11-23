# SPDX-License-Identifier: Apache-2.0
"""GroundingBlock — check claims against source text the caller supplies. No retrieval service, no network."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import warning_card
from g6.core.result import Result
from g6.core import semantic
from g6.grounding.claims import check_claim, check_claims
from g6.grounding.coverage import coverage
from g6.grounding.model_reading import _model_enabled, model_reading
from g6.grounding.retrieval import TextRetriever

#: Refusal codes.
E_SOURCES_REQUIRED = "G6_E_GROUNDING_SOURCES_REQUIRED"
E_SOURCES_TOO_LARGE = "G6_E_GROUNDING_SOURCES_TOO_LARGE"
E_CLAIM_REQUIRED = "G6_E_GROUNDING_CLAIM_REQUIRED"
E_NO_CHECKABLE_SENTENCE = "G6_E_GROUNDING_NO_CHECKABLE_SENTENCE"
#: A blocked-escalated result's code, from the worst status among its checks (this order, worst first).
_BLOCKED_CODES = {
    "conflict": "G6_E_GROUNDING_CONFLICT",
    "not_found": "G6_E_GROUNDING_NOT_FOUND",
    "partial": "G6_E_GROUNDING_PARTIAL",
}
_MAX_SOURCE_CHARS = 2_000_000
E_LEXICAL_ONLY = "G6_E_GROUNDING_LEXICAL_ONLY"
_OPT_IN = 'pip install ".[langextract]"'
_LEXICAL_MESSAGE = (
    "G6 matched the claims to the sources by strict text matching, which misses paraphrases. "
    "In G6's V1 validation, lexical matching found 19 of 100 paraphrased claims "
    "(docs/validation/results/V1/README.md).")
_RECOMMENDATION = f"Google's LangExtract is recommended; opt in from the G6 repository: {_OPT_IN}"

_OPS: dict[str, str] = {
    "check_claim": "check_claim(claim, sources={name: text}): quoted/supported/partial/conflict/not_found",
    "check_claims": "check_claims(text, sources={name: text}): one check per sentence of text",
    "coverage": "coverage(text, sources={name: text}, weights={name: number}): per claim, the independent source "
                "lineages (web host, else one for text handed in together) that support or contradict it; covered "
                "only with two or more and none against; weights are recorded as the caller's claim",
}


class GroundingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt parameter is refused, never dropped

    op: Literal["check_claim", "check_claims", "coverage", "ops", "help"] = "check_claim"
    claim: str = ""
    text: str = ""
    sources: dict[str, str] = Field(default_factory=dict)
    top_k: int = Field(5, ge=1, le=50)
    judge: Literal["lexical", "model"] = Field(
        "model", description="Advisory reading on check_claim/check_claims; lexical disables it; status is unchanged")
    patterns: dict[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")],
                   Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=dict, max_length=30,
        description="Optional ctx_langextract regex fields beside model passages")
    # coverage only; the caller's claim, recorded: finite numbers, one per source at most
    weights: dict[str, Annotated[float, Field(allow_inf_nan=False)]] = Field(default_factory=dict, max_length=1_000)


@component_maturity(
    "beta",
    reason="lexical sentence support with negation/number conflict checks; not entailment; never verified",
)
class GroundingBlock(AIBlock[GroundingInput, dict, dict]):
    __g6_block_contract__ = BlockContract(
        name="grounding", purpose="Check claims against supplied sources; lexical support is not entailment.",
        inputs_required_per_op={"check_claim": ("claim", "sources"), "check_claims": ("text", "sources"),
                                "coverage": ("text", "sources")},
        outputs_per_op={"check_claim": ("check", "completion_state"),
                        "check_claims": ("checks", "completion_state"), "coverage": ("coverage", "completion_state")},
        evidence_outputs_per_op={"check_claims": {"checks": "source-support-assessment"}},
        verification_method="lexical_sentence_overlap", bad_at=("omissions", "stale sources", "proving behavioural correctness"),
        guarantees=("reports support against supplied source text only",))

    def __init__(self, name: str = "grounding", state: dict | None = None, *, llm=None,
                 langextract_module=None) -> None:
        super().__init__(name=name, state=state or {})
        self.llm = llm
        self.langextract_module = langextract_module

    def infer(self, data: GroundingInput) -> Result[dict]:
        # Retrieval also has a semantic reader. The lexical floor must never
        # depend on that model, even inside a request carrying an injected caller.
        with semantic.request(llm=None):
            result = self._lexical_infer(data)
        if result.is_fail() or data.op in ("ops", "help"):
            return result
        payload = result.value
        readings = []
        if data.op in ("check_claim", "check_claims") and data.judge == "model" and _model_enabled():
            checks = [payload["check"]] if data.op == "check_claim" else payload["checks"]
            scope = semantic.use_request() if self.llm is None else semantic.use_request(llm=self.llm)
            with scope:
                for check in checks:
                    reading = model_reading(check["claim"], data.sources, patterns=data.patterns,
                                            langextract_module=self.langextract_module)
                    check["model_reading"] = reading
                    readings.append(reading)
        _engine_guidance(payload, readings)
        return result

    def _lexical_infer(self, data: GroundingInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if not data.sources:
            return Result.fail(f"[{E_SOURCES_REQUIRED}] {data.op}: sources is required ({{name: text}})")
        if sum(len(t) for t in data.sources.values()) > _MAX_SOURCE_CHARS:
            return Result.fail(f"[{E_SOURCES_TOO_LARGE}] {data.op}: sources exceed {_MAX_SOURCE_CHARS} characters")
        retriever = TextRetriever.from_texts(data.sources)
        if data.op == "check_claim":
            if not data.claim.strip():
                return Result.fail(f"[{E_CLAIM_REQUIRED}] check_claim: claim is required")
            # The status judge is always lexical; model selection only controls
            # the advisory sidecar added after this operation returns.
            check = check_claim(data.claim, retriever, top_k=data.top_k, judge=None)
            return Result.ok(_coded({"component": self.name, "op": data.op, "completion_state": check.completion_state,
                                     "check": check.to_dict()}, [check]))
        if data.op == "coverage":
            report = coverage(data.text, retriever, top_k=data.top_k, judge=None, weights=data.weights)
            if not report.claims and report.warning_card is not None:   # refused before any check: no draft, or too big
                return Result.fail(f"[{report.warning_card['code']}] coverage: {report.warning_card['message']}")
            payload = {"component": self.name, "op": data.op, "completion_state": report.completion_state,
                       "coverage": report.to_dict()}
            if report.warning_card is not None:
                payload["warning_card"] = report.warning_card
            return Result.ok(payload)
        checks = check_claims(data.text, retriever, top_k=data.top_k, judge=None)
        if not checks:
            return Result.fail(f"[{E_NO_CHECKABLE_SENTENCE}] check_claims: text has no checkable sentence")
        states = [c.completion_state for c in checks]
        overall = "blocked-escalated" if "blocked-escalated" in states else "qualified-draft"
        return Result.ok(_coded({"component": self.name, "op": data.op, "completion_state": overall,
                                 "execution_id": checks[0].execution_id, "checks": [c.to_dict() for c in checks]}, checks))


def _engine_guidance(payload: dict, readings: list[dict]) -> None:
    """Compose limitations without replacing a blocking code or interpreting prose."""
    if not readings or any(r.get("model_ran") is not True or r.get("engine") not in ("langextract", "ctx_langextract")
                           for r in readings):
        limitation = warning_card(E_LEXICAL_ONLY, _LEXICAL_MESSAGE, _RECOMMENDATION)
        # Supply the card's limitation before the shared door fills missing
        # envelope fields, so direct and MCP consumers receive the same card.
        limitation["risks"] = [_LEXICAL_MESSAGE]
        existing = payload.get("warning_card")
        if existing is None:
            payload["warning_card"] = limitation
        else:
            # One envelope field; keep the actionable grounding code and both cards.
            # Coverage also retains this card in its lexical report. Compose a
            # new envelope card without changing that original result record.
            existing = dict(existing)
            existing["warnings"] = [*existing.get("warnings", []), limitation]
            for key in ("message", "why"):
                existing[key] = existing.get(key, "") + " " + _LEXICAL_MESSAGE
            for key in ("suggested_action", "next_step"):
                existing[key] = existing.get(key, "") + " " + _RECOMMENDATION
            if not existing.get("risks"):
                existing["risks"] = [existing["message"]]
            payload["warning_card"] = existing
    elif any(r.get("engine") == "ctx_langextract" for r in readings):
        payload["engine_note"] = "Advisory reading used G6's ctx_langextract model backend. " + _RECOMMENDATION


def _coded(payload: dict, checks: list) -> dict:
    """A blocked-escalated result carries a warning card with the code of the worst status among its checks."""
    if payload["completion_state"] == "blocked-escalated":
        statuses = [c.status for c in checks]
        worst = next(status for status in _BLOCKED_CODES if status in statuses)
        payload["warning_card"] = warning_card(
            _BLOCKED_CODES[worst], f"{statuses.count(worst)} claim(s) read {worst} against the sources",
            "Do not send the claim as it stands: correct it from the source sentence, or find a source that supports it.")
    return payload


__all__ = ["GroundingBlock", "GroundingInput"]
