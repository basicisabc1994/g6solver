# SPDX-License-Identifier: Apache-2.0
"""Stateless G6 registry/MCP-safe surface for cognitive learning calculations.

No arbitrary model or tool code crosses this door. Agentic calls and in-memory
ML predictor adapters are deliberately Python-only dependency injection.
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from .codes import INPUT, STATES, refusal
from .engine import BayesianUpdater, Hypothesis, InformationGainSelector, Probe, check_beliefs
from .mixed_methods import ClaimIR, MethodRouter
from .metacognition import MetacognitiveMethodSelector, ProblemSignature, MethodVariant
from .assurance import DOCUMENT_FIELDS


class HypothesisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=1500)
    prior: float = Field(ge=0, le=1)


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=1500)
    tool: str = Field(min_length=1, max_length=150)
    positive_if: dict[str, bool]
    assumed_match_probability: float = Field(ge=0.5, le=1)
    cost: float = Field(default=0, ge=0)
    novelty: float = Field(default=0, ge=0, le=1)
    arguments: dict = Field(default_factory=dict)


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=3000)
    evidence_needs: list[Literal["graph", "causal", "formal", "predictive", "optimisation"]] = Field(min_length=1, max_length=5)
    models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    origin: Literal["caller", "llm_candidate"] = "caller"


class ProblemSignatureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=1, max_length=200)
    uncertainty: str = Field(min_length=1, max_length=200)
    claim_kind: str = Field(min_length=1, max_length=200)
    risk: int = Field(ge=0, le=3, strict=True, description="the risk tier's number: 0 to 3 for R0 to R3")
    budget: float = Field(ge=0, allow_inf_nan=False)
    available_evidence: list[str] = Field(default_factory=list, max_length=100)


class MethodVariantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    method: Literal["graph", "causal", "formal", "predictive", "optimisation"]
    representation: str = Field(min_length=1, max_length=100)
    engine: str = Field(min_length=1, max_length=100)
    op: str = Field(min_length=1, max_length=100)
    estimated_cost: float = Field(ge=0, allow_inf_nan=False)
    expects: list[str] = Field(default_factory=list, max_length=50)
    # Over MCP a variant is a G6 solver op; reviewer and arm variants are ranked from Python, with their trials.
    kind: Literal["engine"] = "engine"


#: Each op the block answers, with what it does (its ``help`` answer).
_OPS: dict[str, str] = {
    "rank": "rank(hypotheses, probes, budget): the affordable probes by expected information gain, best first",
    "update": "update(hypotheses, probes, probe_id, observed_positive): the posterior after one observed probe",
    "plan_methods": "plan_methods(claims): the typed G6 solver steps each claim's evidence needs; nothing runs",
    "rank_methods": "rank_methods(context, variants, required_methods): a cold-start ranking of method variants",
    "assurance_schema": "assurance_schema(): the documentary fields an assurance dossier must fill",
}


class CognitiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["ops", "help", "rank", "update", "plan_methods", "rank_methods", "assurance_schema"] = "ops"
    claims: list[ClaimInput] = Field(default_factory=list, max_length=20)
    context: ProblemSignatureInput | None = None
    variants: list[MethodVariantInput] = Field(default_factory=list, max_length=30)
    required_methods: list[Literal["graph", "causal", "formal", "predictive", "optimisation"]] = Field(default_factory=list, max_length=5)
    hypotheses: list[HypothesisInput] = Field(default_factory=list, max_length=32)
    probes: list[ProbeInput] = Field(default_factory=list, max_length=100)
    budget: float = Field(default=0, ge=0)
    probe_id: str = ""
    observed_positive: bool | None = None


@component_maturity("beta", reason="new typed bridge; its proposals, likelihoods and model fidelity are not "
                                   "checked by anyone outside it")
@block_contract(
    purpose="rank discriminating research probes and revise explicit hypotheses with auditable Bayesian arithmetic",
    mitigates_failure_modes=("ad_hoc_choice",),
    verification_method="deterministic computations on caller-supplied priors and assumed likelihoods; no independent source check",
)
class CognitiveBridgeBlock(AIBlock[CognitiveInput, dict, dict]):
    def __init__(self, name: str = "cognitive_bridge", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: CognitiveInput) -> Result[dict]:
        # The registry hands a validated CognitiveInput; a Python caller may still pass the plain dict.
        try:
            d = data if isinstance(data, CognitiveInput) else CognitiveInput.model_validate(data)
            if d.op == "ops":
                return Result.ok({"component": self.name, "completion_state": "qualified-draft",
                                  "ops": list(_OPS),
                                  "note": "LLM research and in-memory ML adapters are Python-only; no tools are invoked via this surface."})
            if d.op == "help":
                return Result.ok({"component": self.name, "op": "help", "completion_state": "qualified-draft",
                                  "help": list(_OPS.values())})
            if d.op == "assurance_schema":
                return Result.ok({"component": self.name, "op": d.op,
                                  "completion_state": "qualified-draft",
                                  "documentary_fields": list(DOCUMENT_FIELDS),
                                  "levels": list(STATES),
                                  "note": "Assurance issuance and historical trial admission require an independently owned Python evaluator; not exposed via MCP."})
            if d.op == "rank_methods":
                if d.context is None:
                    raise ValueError("rank_methods requires a typed context")
                context = ProblemSignature(**d.context.model_dump())
                variants = [MethodVariant(**{**v.model_dump(), "expects": tuple(v.expects)}) for v in d.variants]
                decision = MetacognitiveMethodSelector().select(context, variants,
                                     required_methods=d.required_methods)
                return Result.ok({"component": self.name, "op": d.op, **decision.to_dict(),
                                  "evidence_note": "no past trial data admitted over stateless MCP; cold-start ranking only"})
            if d.op == "plan_methods":
                claims = [ClaimIR(id=c.id, statement=c.statement,
                                  evidence_needs=tuple(c.evidence_needs), models=c.models,
                                  source_refs=tuple(c.source_refs), origin=c.origin) for c in d.claims]
                return Result.ok({"component": self.name, "op": d.op,
                                  **MethodRouter().plan(claims).to_dict()})
            hypotheses = [Hypothesis(**h.model_dump()) for h in d.hypotheses]
            beliefs = check_beliefs(hypotheses)
            probes = [Probe(**p.model_dump()) for p in d.probes]
            if len({p.id for p in probes}) != len(probes):
                raise ValueError("duplicate probe ids")
            if d.op == "rank":
                result = InformationGainSelector().rank(beliefs, probes, d.budget)
            else:
                if d.observed_positive is None:
                    raise ValueError("update requires observed_positive")
                p = next((p for p in probes if p.id == d.probe_id), None)
                if p is None:
                    raise ValueError("update probe_id not among probes")
                result = BayesianUpdater().update(beliefs, p, d.observed_positive)
            return Result.ok({"component": self.name, "op": d.op,
                              "completion_state": "qualified-draft", "result": result,
                              "assumptions": ["priors are mutually exclusive and exhaustive",
                                              "likelihoods and predictions are caller-provided assumptions",
                                              "does not verify the reality of any observations"]})
        except (ValidationError, ValueError, TypeError, KeyError, OverflowError) as exc:
            return Result.ok({"component": self.name, **refusal(INPUT, str(exc))})
