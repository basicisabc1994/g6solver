# SPDX-License-Identifier: Apache-2.0
"""Section 3.1 data contracts; identities hash the canonical body without its identity field.

Records carry decisions; constructing one does not earn authority or execute a method.
Prediction mirrors metaharness.lessons.Prediction without importing the protected layer.
"""
from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_serializer, model_validator

from g6.core.ledger import canonical, digest

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, strict=True)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[Finite, Field(gt=0)]
Rate = Annotated[Finite, Field(ge=0, le=1)]
Verdict = Literal["concern", "abstain", "no_concern"]
Split = Literal["training", "dev", "monitor", "field"]
Status = Literal["probation", "admitted", "held", "demoted", "withdrawn", "superseded"]
FindingClass = Literal["identity_from_rendering", "one_fact_two_rules", "own_parser_for_foreign_format",
                       "failure_read_as_success", "check_then_act_race", "state_not_durable",
                       "untrusted_input_given_authority", "boundary_case", "guard_covers_less_than_it_guards", "other"]
FACT_NAMES = frozenset({"assertions", "compared_literals", "markers", "mocks", "try_blocks", "parametrize_counts",
                        "calls", "diff_hunks", "probe_outcomes"})
PROBE_NAMES = frozenset({"discriminates_at_base.v1", "kill_set.v1"})


def _stored_text(info) -> bool:
    """Only text canonicalizers consume the stored-body validation context."""
    return isinstance(info.context, dict) and info.context.get('distill_stored_record') is True


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Record(Model):
    identity_field: ClassVar[str | None] = None

    @property
    def content_id(self) -> str:
        return digest(self.model_dump(mode="json", exclude={self.identity_field} if self.identity_field else set()))

    @model_validator(mode="after")
    def identify(self) -> Self:
        if self.identity_field:
            actual = self.content_id
            given = getattr(self, self.identity_field)
            if given is not None and given != actual:
                raise ValueError("identity does not match the canonical body digest")
            object.__setattr__(self, self.identity_field, actual)
        return self


class Evidence(Model):
    kind: str
    digest: Sha256
    receipt: str


class Prediction(Model):
    prediction_id: str
    measure: str
    before: Finite
    expected: Finite

    def as_domain(self, prediction_type):
        """Adapt with the caller's domain type, without importing the meta layer."""
        return prediction_type(**self.model_dump())

    @model_validator(mode="after")
    def direction(self) -> Self:
        if self.before == self.expected:
            raise ValueError("a prediction needs a direction")
        return self


class FailureRecord(Record):
    identity_field = "record_id"
    record_id: Sha256 | None = None
    kind: Literal["missed_defect", "false_alarm", "g6_refusal", "g6_crash", "capability_gap",
                  "self_model_discrepancy", "prediction_failed", "regression", "user_request"]
    family: str
    source: Literal["review", "k_loop", "outcome_history", "runner", "self_repair", "ci", "person", "experiment"]
    observed_by: Literal["independent", "self"]
    case_ref: Sha256
    split: Split
    evidence: list[Evidence]
    finding_class: FindingClass
    met_before: list[Sha256]
    g6_commit: str
    observed_at: str
    provenance: dict[str, JsonValue]

    def as_domain(self, failure_type):
        """Adapt with the caller's domain type, preserving independent observation."""
        return failure_type(case_id=self.case_ref, cause=self.kind, note=self.family,
                            independently_observed=self.observed_by == "independent")


class Proposer(Model):
    provider: str
    model: str
    prompt_sha256: Sha256


class Concept(Record):
    identity_field = "concept_id"
    concept_id: Sha256 | None = None
    statement: Annotated[str, Field(min_length=1, max_length=600)]
    family: str
    from_failures: Annotated[list[Sha256], Field(min_length=1)]
    observable_features: list[str]
    falsifier: str
    prediction: Prediction
    proposer: Proposer
    origin: Literal["model_proposed"]
    worth: Rate
    status: Literal["proposed", "compiled", "rejected", "parked"]

    @field_validator('statement', 'falsifier', mode='before')
    @classmethod
    def canonical_text(cls, value, info):
        # concept_id precedes these fields, so an explicit identity protects
        # exactly the supplied text until identify checks its original digest.
        if _stored_text(info) or info.data.get('concept_id') is not None:
            return value
        return ' '.join(value.split()) if isinstance(value, str) else value

    @model_validator(mode="after")
    def features(self) -> Self:
        if set(self.observable_features) - FACT_NAMES:
            raise ValueError("unknown facts catalogue feature")
        return self


class FeaturePredicate(Model):
    feature: str
    op: Literal["eq", "ne", "gt", "ge", "lt", "le", "contains"]
    value: JsonValue

    @model_validator(mode="after")
    def known(self) -> Self:
        if self.feature not in FACT_NAMES:
            raise ValueError("unknown facts catalogue feature")
        return self


class Probe(Model):
    name: Literal["discriminates_at_base.v1", "kill_set.v1"]
    runs: Count
    wall_s: Positive


class CostBound(Model):
    wall_s: Positive
    probe_runs: Count
    model_calls: Literal[0]


class MethodContract(Record):
    family: str
    languages: list[str]
    frameworks: list[str]
    applies_when: list[FeaturePredicate]
    assumptions: list[str]
    inputs: list[str]
    probes: list[Probe]
    verdict_kinds: Annotated[list[Verdict], Field(min_length=1)]
    cost_bound: CostBound
    escape_family: str
    withdrawal_conditions: list[str]

    @field_validator('escape_family', mode='before')
    @classmethod
    def canonical_text(cls, value, info):
        return ' '.join(value.split()) if isinstance(value, str) and not _stored_text(info) else value

    @field_validator('assumptions', 'withdrawal_conditions', mode='before')
    @classmethod
    def canonical_text_list(cls, values, info):
        if isinstance(values, (list, tuple)) and not _stored_text(info):
            return [' '.join(value.split()) if isinstance(value, str) else value for value in values]
        return values

    @model_validator(mode="after")
    def catalogue(self) -> Self:
        if set(self.inputs) - FACT_NAMES:
            raise ValueError("unknown facts catalogue input")
        if sum(p.runs for p in self.probes) > self.cost_bound.probe_runs:
            raise ValueError("probe runs exceed cost bound")
        return self


class TemplateInstance(Model):
    template: Literal["weakening.v1", "assert_strength.v1", "skip_gate.v1", "swallow.v1", "self_reference.v1",
                      "mock_target.v1", "discriminates_at_base.v1", "kill_set.v1"]
    bindings: dict[str, JsonValue]


class Composition(Model):
    op: Literal["any", "all", "k_of_n", "sequence"]
    entries: Annotated[list[Sha256], Field(min_length=1)]
    k: Count | None = None

    @model_validator(mode="after")
    def threshold(self) -> Self:
        if self.op == "k_of_n":
            if self.k is None or not 1 <= self.k <= len(self.entries):
                raise ValueError("k must be between one and the entry count")
        elif self.k is not None:
            raise ValueError("k only applies to k_of_n")
        return self


class SynthesisedBy(Model):
    kind: Literal["template", "composition", "model"]
    exchange_sha256: Sha256 | None

    @model_validator(mode="after")
    def exchange(self) -> Self:
        if self.kind == "model" and self.exchange_sha256 is None:
            raise ValueError("model synthesis needs an exchange digest")
        return self


class VerifierCandidate(Record):
    identity_field = "candidate_id"
    candidate_id: Sha256 | None = None
    concept_id: Sha256
    form: Literal["reuse", "composition", "template_instance", "predicate"]
    body: str | Composition | TemplateInstance
    contract: MethodContract
    lineage: list[Sha256]
    synthesised_by: SynthesisedBy
    training_case_ids: list[Sha256]
    authority: Literal["proposed"] = "proposed"
    model_calls: Annotated[int, Field(ge=0, le=4, strict=True)] = 0

    @field_validator('contract', mode='before')
    @classmethod
    def stored_contract(cls, value, info):
        # Explicit candidate identities also bind the nested historical text.
        # Parse just this field in its original mode, with the text context;
        # never wrap Record validation or change nested strict type semantics.
        if isinstance(value, dict):
            context = info.context
            if info.data.get('candidate_id') is not None:
                context = {**(context if isinstance(context, dict) else {}), 'distill_stored_record': True}
            if info.mode == 'json':
                return MethodContract.model_validate_json(canonical(value), context=context)
            return MethodContract.model_validate(value, context=context)
        return value

    @model_serializer(mode="wrap")
    def provenance(self, handler):
        # Preserve old content-addressed bodies; S explicitly sets both fields.
        body = handler(self)
        for name in ("authority", "model_calls"):
            if name not in self.model_fields_set:
                body.pop(name, None)
        return body

    @model_validator(mode="after")
    def shape(self) -> Self:
        import ast
        import re
        if self.form == "reuse":
            if not isinstance(self.body, str) or not re.fullmatch(r"[0-9a-f]{64}", self.body):
                raise ValueError("reuse requires an entry digest")
        elif self.form == "composition":
            if not isinstance(self.body, Composition):
                raise ValueError("composition requires a combinator")
        elif self.form == "template_instance":
            if not isinstance(self.body, TemplateInstance):
                raise ValueError("template_instance requires a TemplateInstance")
        else:
            if not isinstance(self.body, str) or len(self.body.encode("utf-8")) > 8192:
                raise ValueError("predicate source exceeds 8 KiB")
            try:
                tree = ast.parse(self.body)
            except SyntaxError as error:
                raise ValueError("invalid predicate source") from error
            if not any(isinstance(n, ast.FunctionDef) and n.name == "solve" and len(n.args.args) == 1
                       for n in tree.body):
                raise ValueError("predicate needs solve(facts)")
        return self


class Interval(Model):
    point: Rate
    lo95: Rate
    hi95: Rate
    method: Literal["wilson"]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.lo95 <= self.point <= self.hi95:
            raise ValueError("interval must contain its point")
        return self


class Cost(Model):
    median_s: Annotated[Finite, Field(ge=0)]
    p95_s: Annotated[Finite, Field(ge=0)]


class AcceptanceEvidence(Model):
    acceptance_report_sha256: Sha256
    policy_sha256: Sha256
    split_sha256: Sha256
    reuse_index: Count
    n_pos: Count
    n_neg: Count
    tp: Count
    fp: Count
    tn: Count
    fn: Count
    abstain: Count
    precision: Interval
    recall: Interval
    false_rejection: Interval
    cost: Cost
    n_units: Count | None = None

    @model_serializer(mode='wrap')
    def compatible_body(self, handler):
        body = handler(self)
        if body.get('n_units') is None:
            body.pop('n_units', None)
        return body


class RegressionCase(Model):
    case_ref: Sha256
    verdict: Verdict


class FieldEvidence(Model):
    fired: Count
    confirmed: Count
    refuted: Count
    unlabelled: Count
    precision: Interval | None


class DecidedBy(Model):
    gate_version: str
    person: str | None


class LibraryEntry(Record):
    identity_field = "entry_id"
    entry_id: Sha256 | None = None
    candidate_id: Sha256
    contract_sha256: Sha256
    concept_id: Sha256
    family: str
    version: Annotated[int, Field(ge=1, strict=True)]
    parents: list[Sha256]
    status: Status
    authority: Literal["A0", "A1", "A2"]
    evidence: AcceptanceEvidence
    regression_suite: list[RegressionCase]
    field: FieldEvidence
    decided_by: DecidedBy
    certificate_sha256: Sha256
    created_at: str
    changed_at: str
    reason: str
    held_recurrences: list[Sha256] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def compatible_body(self, handler):
        body = handler(self)
        if not body.get("held_recurrences"):
            body.pop("held_recurrences", None)
        return body

    @model_validator(mode="after")
    def authority_shape(self) -> Self:
        allowed = {"probation": {"A1"}, "admitted": {"A2"}, "demoted": {"A0", "A1"},
                   "held": {"A0"}, "withdrawn": {"A0"}, "superseded": {"A0"}}
        if self.authority not in allowed[self.status]:
            raise ValueError("authority does not match the recorded status")
        return self


class CapabilityExtension(Record):
    identity_field = "ext_id"
    ext_id: Sha256 | None = None
    kind: Literal["method", "template", "extractor", "probe", "component_op", "routing", "user_requested"]
    requested_by: Literal["person", "runner", "M"]
    gap_record: Sha256
    summary: str
    artefact_ref: str
    evidence_ref: Sha256
    certificate_ref: Sha256
    status: str
    scope: str


class FailureMode(Record):
    mode_code: str
    first_seen: str
    occurrences: Count
    met_before: bool
    removing_check: Sha256 | None
    recurrences_after_removal: Count

    @model_validator(mode="after")
    def registered_code(self) -> Self:
        from typing import get_args
        from g6.core.failure_modes import FailureModeRegistry
        named_other = self.mode_code.startswith("other: ") and bool(self.mode_code[7:].strip())
        if (self.mode_code not in get_args(FindingClass) and not named_other
                and not FailureModeRegistry.is_registered(self.mode_code)):
            raise ValueError("unknown failure mode code or finding class")
        return self


class Change(Model):
    kind: Literal["library", "code", "config", "data"]
    branch: str | None
    commit: str | None
    pr: str | None


class RemovalCheck(Model):
    """Gate-supplied replay receipt, not a test executor or an authenticated assertion.

    `replayed` names failures on which this check failed before the change. The same
    check must pass the fixed code. The independent gate owns the receipt bytes.
    """
    test_ref: Annotated[str, Field(min_length=1, pattern=r"\S")]
    replayed: list[Sha256]
    fixed_passed: Annotated[bool, Field(strict=True)]
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.replayed)) != len(self.replayed):
            raise ValueError("duplicate replay evidence")
        return self


class Rectification(Record):
    identity_field = "rect_id"
    rect_id: Sha256 | None = None
    failure_mode: str
    instance_or_class: Literal["instance", "class"]
    change: Change
    prediction: Prediction
    gate_decision_sha256: Sha256
    class_removed: bool
    outcome: Literal["held", "failed", "open"]
    rollback_ref: str | None
    removal: str | None = None
    pinning: RemovalCheck | None = None

    @model_validator(mode="after")
    def pinned_claim(self) -> Self:
        if self.class_removed and (self.pinning is None or not self.removal or not self.removal.strip()):
            raise ValueError("class removal requires a removal and pinning test")
        return self

    @model_serializer(mode="wrap")
    def compatible_body(self, handler):
        # Older open rows retain their original content ids; new workflow rows
        # carry the removal and test in their hashed body.
        body = handler(self)
        for name in ("removal", "pinning"):
            if body.get(name) is None:
                body.pop(name, None)
        return body


RECORD_KINDS = {"failure": FailureRecord, "concept": Concept, "contract": MethodContract,
                "candidate": VerifierCandidate, "library": LibraryEntry, "extension": CapabilityExtension,
                "failure_mode": FailureMode, "rectification": Rectification}
