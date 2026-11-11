# SPDX-License-Identifier: Apache-2.0
"""Stateless G6 registry/MCP-safe surface for cognitive learning calculations.

No arbitrary model or tool code crosses this door. Agentic calls and in-memory
ML predictor adapters are deliberately Python-only dependency injection.

The block's outer argument mapping follows the registry's mapping protocol, enumerated inside the bounded walker.
Nested values keep their original kinds. The boundary reads that copy once (:func:`_arguments`:
``snapshot.plain``, an exact :class:`CognitiveInput` by its fields as the caller set them and never serialized first),
and everything after reads only that read: at G6's door,
:meth:`CognitiveBridgeBlock.screen_raw` takes the read and :meth:`CognitiveBridgeBlock.infer` answers from it, refusing
the call when the typed input G6's registry then rebuilt from the caller's objects no longer reads the same; from Python,
``infer`` reads what it is handed. The models are strict: no field is converted from another kind (a flag, numeric text
or null where a number is due is refused, not read as one). ``tests/cognitive_bridge/test_conversion_inventory.py`` lists
every model and every conversion in the package from the code, and ``tests/cognitive_bridge/test_read_inventory.py``
every function between the boundary and typed validation.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, get_args
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from .codes import INPUT, STATES, NotPlainData, refusal, settled, unclaimed, _is_type, _type_mro
from .engine import BayesianUpdater, Hypothesis, InformationGainSelector, Probe, check_beliefs
from .mixed_methods import ClaimIR, MethodRouter
from .metacognition import MetacognitiveMethodSelector, ProblemSignature, MethodVariant
from .assurance import DOCUMENT_FIELDS
from .snapshot import publish, canonical_json, plain, _text_keys, _walk

#: Every model here reads a caller's data, after the boundary has read it raw: strict (no field converted from another
#: kind) and closed (no field it does not declare).
_STRICT = ConfigDict(extra="forbid", strict=True)


def _admit_model_input(cls: type[BaseModel], given: Any) -> Any:
    """The same native admission for direct constructors and model_validate.

    Read the owned model's schema, including when its validator is inherited;
    never give an extension class authority to replace the bridge's limits.
    """
    for base in _type_mro(cls):
        if _is_type(base, (CognitiveInput, *_NESTED)):
            def preflight(key: str, item: Any) -> None:
                _admit_input({key: item}, model=base)
            if any(parent is dict or parent is Mapping for parent in _type_mro(type(given))):
                taken = _walk(given, "an input model", outer=True, preflight=preflight,
                              models=(CognitiveInput, *_NESTED))
            elif _is_type(type(given), (CognitiveInput, *_NESTED)):
                taken = _walk(given, "an input model", models=(CognitiveInput, *_NESTED))
            else:
                raise NotPlainData("an input model", "unmeasured model input is not native object data")
            _admit_input(taken, model=base)
            # Validate the detached tree's real JSON round trip. Keep its exact
            # container kinds for Pydantic's strict schema; it reads only this
            # checked, detached tree, never the caller's value a second time.
            plain(taken, where="an input model")
            return taken
    raise NotPlainData("an input model", "unmeasured model schema cannot admit conversion")


class HypothesisInput(BaseModel):
    model_config = _STRICT
    _admit_size = model_validator(mode='before')(classmethod(_admit_model_input))
    id: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=1500)
    prior: float = Field(ge=0, le=1)


class ProbeInput(BaseModel):
    model_config = _STRICT
    _admit_size = model_validator(mode='before')(classmethod(_admit_model_input))
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=1500)
    tool: str = Field(min_length=1, max_length=150)
    positive_if: dict[str, bool]
    assumed_match_probability: float = Field(ge=0.5, le=1)
    cost: float = Field(default=0, ge=0)
    novelty: float = Field(default=0, ge=0, le=1)
    arguments: dict = Field(default_factory=dict)


class ClaimInput(BaseModel):
    model_config = _STRICT
    _admit_size = model_validator(mode='before')(classmethod(_admit_model_input))
    id: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=3000)
    evidence_needs: list[Literal["graph", "causal", "formal", "predictive", "optimisation"]] = Field(min_length=1, max_length=5)
    models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    origin: Literal["caller", "llm_candidate"] = "caller"


class ProblemSignatureInput(BaseModel):
    model_config = _STRICT
    _admit_size = model_validator(mode='before')(classmethod(_admit_model_input))
    domain: str = Field(min_length=1, max_length=200)
    uncertainty: str = Field(min_length=1, max_length=200)
    claim_kind: str = Field(min_length=1, max_length=200)
    risk: int = Field(ge=0, le=3, strict=True, description="the risk tier's number: 0 to 3 for R0 to R3")
    budget: float = Field(ge=0, allow_inf_nan=False)
    available_evidence: list[str] = Field(default_factory=list, max_length=100)


class MethodVariantInput(BaseModel):
    model_config = _STRICT
    _admit_size = model_validator(mode='before')(classmethod(_admit_model_input))
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
    model_config = _STRICT
    _admit_size = model_validator(mode='before')(classmethod(_admit_model_input))
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


def _answered(fields: dict[str, Any]) -> Result[dict]:
    """What the block hands G6's door: a result a maker made, with the block's own name and op beside it, taken as
    the bridge takes any result it builds (the claim rule, then ``snapshot.plain``)."""
    return Result.ok(plain(unclaimed(fields, where="the block's answer"), where="the block's answer"))


#: The models a :class:`CognitiveInput` holds, each read by its fields as the caller set them (:func:`_as_given`); a
#: value of any other class, a subclass among them, is handed to the boundary as it is, and refused there.
_NESTED: tuple[type[BaseModel], ...] = (HypothesisInput, ProbeInput, ClaimInput, ProblemSignatureInput,
                                        MethodVariantInput)


def _admit_input(given: Any, *, model: type[BaseModel] = CognitiveInput) -> None:
    """Use the owned schema's native bounds before detaching any model field.

    This visits only the fixed model topology: the root and its declared child
    models. Each collection is admitted before its entries are inspected. Unknown
    kinds and malformed leaves are still refused by the common boundary.
    """
    pending = [(model, given, "the block's arguments")]
    while pending:
        cls, value, path = pending.pop()
        if _is_type(type(value), (cls,)):
            values = object.__getattribute__(value, '__dict__')
        elif type(value) is dict:
            values = value
        else:
            continue
        # Raw keys pass the one text-key reader before the schema's names are looked up among them: a lookup hashes
        # and compares, and a foreign key's own ``__eq__`` would run (review 2 of the fourth follow-up, finding 2).
        _text_keys(values, path)
        for name, info in cls.model_fields.items():
            if name not in values:
                continue
            item = values[name]
            where = f'{path}.{name}'
            for rule in info.metadata:
                for bound in ('max_length', 'min_length', 'ge', 'gt', 'le', 'lt'):
                    limit = getattr(rule, bound, None)
                    if limit is None:
                        continue
                    if bound.endswith('length') and _is_type(type(item), (str, list, tuple, dict)):
                        measured = len(item)
                    elif bound in ('ge', 'gt', 'le', 'lt') and _is_type(type(item), (int, float)):
                        measured = item
                    else:
                        continue
                    refused = (measured > limit if bound in ('max_length', 'le') else
                               measured < limit if bound in ('min_length', 'ge') else
                               measured >= limit if bound == 'lt' else measured <= limit)
                    if refused:
                        raise NotPlainData(where, f'outside the declared {bound} bound of {limit}')
            hints = (info.annotation, *get_args(info.annotation))
            child = next((model for model in _NESTED if any(hint is model for hint in hints)), None)
            if child is not None:
                if type(item) is list:
                    pending.extend((child, entry, f'{where}[{index}]') for index, entry in enumerate(item))
                else:
                    pending.append((child, item, where))


def _admit_outer_input(key: str, value: Any) -> None:
    """Admit a protocol's field before the walker detaches that field."""
    _admit_input({key: value})


def _as_given(given: Any) -> Any:
    """What the block was handed, as the boundary reads it: an exact :class:`CognitiveInput` as its fields, each exact
    nested model (:data:`_NESTED`, alone or an item of an exact list) as its fields. An outer mapping is copied by the
    mapping protocol as the bound registry, within the visit budget; only its entries are input data, not its class or extra attributes. Nested
    values are never converted here. Nothing is serialized: ``model_dump`` erased nested mapping subclasses before
    the boundary saw them (review 3 of PR #8's second follow-up, finding 2)."""
    _admit_input(given)
    if type(given) is dict:
        return _walk(given, "the block's arguments")
    if type(given) is not CognitiveInput:
        # Apply the agreed outer mapping protocol directly, as the registry does.
        # A class or instance attribute must not decide whether copying is allowed.
        copied = _walk(given, "the block's arguments", outer=True, preflight=_admit_outer_input)
        _admit_input(copied)
        return copied
    return _walk(given, "the block's arguments", models=(CognitiveInput, *_NESTED))


def _arguments(given: Any) -> Any:
    """The block's arguments as the boundary reads them, once, raw, before any model does: ``snapshot.plain`` of what
    they are as given (:func:`_as_given`), after the common outer mapping copy: a new value that shares nothing with
    the caller's, or :class:`NotPlainData` (a NaN, a set, bytes, a nested subclass, any other object)."""
    return plain(_as_given(given), where="the block's arguments")


def _unchanged(given: CognitiveInput, held: CognitiveInput) -> CognitiveInput:
    """``held``, the typed input validated from what G6's door screened (:meth:`CognitiveBridgeBlock.screen_raw`),
    when ``given``, the typed input the door's registry then rebuilt from the caller's objects and the boundary read in
    ``infer``, reads the same by the bridge's canonical encoder; else the call is refused, since what was screened is
    no longer what was asked (review 3, finding 5: a change between the two reads was what got planned). Each is
    compared, and ``held`` answered from, with every default written out (validated once more from its own fields:
    pydantic leaves a default unvalidated, so ``budget``'s ``0`` reads ``0.0`` once written out, as the registry's own
    typed input carries it)."""
    given = CognitiveInput.model_validate(_arguments(given))
    held = CognitiveInput.model_validate(_arguments(held))
    if canonical_json(_arguments(given)) != canonical_json(_arguments(held)):
        raise ValueError("the block's arguments changed after G6's door screened them; nothing was answered")
    return held


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
        #: What :meth:`screen_raw` read at G6's door, for the call :meth:`infer` answers next (:meth:`_held`), or None.
        self._screened: Any = None

    def screen_raw(self, raw: dict[str, Any]) -> Result[dict] | None:
        """Before typed validation, on every door (``g6.core.registry``): the raw arguments as the boundary reads them
        (:func:`_arguments`), the one read of them. What it refuses is the block's refusal under ``codes.INPUT``;
        otherwise ``None``, and the read is kept for :meth:`infer`, which answers from it (G6's registry, a bound file,
        hands ``infer`` only the typed input it rebuilds from the caller's objects)."""
        self._screened = None
        try:
            screened = _arguments(raw)
        except NotPlainData as exc:
            return publish(self._refused(exc), where="CognitiveBridgeBlock.screen_raw")
        self._screened = screened
        return publish(None, where="CognitiveBridgeBlock.screen_raw")

    def _held(self) -> Any:
        """What :meth:`screen_raw` read for the call :meth:`infer` now answers, taken once (None when it read
        nothing): the boundary's snapshot, which no one else holds."""
        screened, self._screened = getattr(self, "_screened", None), None
        return screened

    def _refused(self, exc: Exception) -> Result[dict]:
        """The block's refusal of its input, under ``codes.INPUT``, saying why."""
        return _answered({"component": self.name, **refusal(INPUT, str(exc))})

    def infer(self, data: CognitiveInput) -> Result[dict]:
        # At G6's door the registry hands a CognitiveInput it rebuilt from the caller's objects after screen_raw read
        # them: the answer is made from screen_raw's read, and the call refused when the two no longer read alike. A
        # Python caller may pass the plain dict or a CognitiveInput: its one read is here. Either way the arguments are
        # read raw by the boundary first, then by the strict models.
        screened = self._held()  # taken first, so a read is never left for another call
        try:
            d = CognitiveInput.model_validate(_arguments(data))
            if screened is not None:
                d = _unchanged(d, CognitiveInput.model_validate(screened))
            if d.op == "ops":
                return publish(_answered({"component": self.name, **settled(
                    None, ops=list(_OPS),
                    note="LLM research and in-memory ML adapters are Python-only; no tools are invoked via this surface.")}), where="CognitiveBridgeBlock.infer")
            if d.op == "help":
                return publish(_answered({"component": self.name, "op": "help", **settled(None, help=list(_OPS.values()))}), where="CognitiveBridgeBlock.infer")
            if d.op == "assurance_schema":
                return publish(_answered({"component": self.name, "op": d.op, **settled(
                    None, documentary_fields=list(DOCUMENT_FIELDS), levels=list(STATES),
                    note="Assurance issuance and historical trial admission require an independently owned Python evaluator; not exposed via MCP.")}), where="CognitiveBridgeBlock.infer")
            if d.op == "rank_methods":
                if d.context is None:
                    raise ValueError("rank_methods requires a typed context")
                context = ProblemSignature(**d.context.model_dump())
                variants = [MethodVariant(**{**v.model_dump(), "expects": tuple(v.expects)}) for v in d.variants]
                decision = MetacognitiveMethodSelector().select(context, variants,
                                     required_methods=d.required_methods)
                return publish(_answered({"component": self.name, "op": d.op, **decision.to_dict(),
                                  "evidence_note": "no past trial data admitted over stateless MCP; cold-start ranking only"}), where="CognitiveBridgeBlock.infer")
            if d.op == "plan_methods":
                claims = [ClaimIR(id=c.id, statement=c.statement,
                                  evidence_needs=tuple(c.evidence_needs), models=c.models,
                                  source_refs=tuple(c.source_refs), origin=c.origin) for c in d.claims]
                return publish(_answered({"component": self.name, "op": d.op,
                                  **MethodRouter().plan(claims).to_dict()}), where="CognitiveBridgeBlock.infer")
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
            return publish(_answered({"component": self.name, "op": d.op, **settled(
                None, result=result, assumptions=["priors are mutually exclusive and exhaustive",
                                                  "likelihoods and predictions are caller-provided assumptions",
                                                  "does not verify the reality of any observations"])}), where="CognitiveBridgeBlock.infer")
        except (ValidationError, ValueError, TypeError, KeyError, OverflowError) as exc:
            return publish(self._refused(exc), where="CognitiveBridgeBlock.infer")
