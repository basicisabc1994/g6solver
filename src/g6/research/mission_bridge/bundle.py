# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The claim and evidence bundle (audit ENG-04): a mission's answer as claims bound to its frozen standard, each derived from
recorded evidence by the ECHA kernel's own claim rule. Research code, default OFF (this flag and the kernel's both).

Nothing here computes a result of its own. An ``Observation`` is what a node actually produced, typed by what kind of evidence
it is, and is written into the kernel's ledger as one of its categories:

- ``process`` (a step ran) becomes ``NO_DISCRIMINATING_EVIDENCE``: that a step ran says nothing about whether its output is true;
- ``model_opinion`` becomes ``HEURISTIC_REVIEW``: a lead, never support, and opinions that share a group are one voice;
- ``executable`` becomes ``INDEPENDENT_EXECUTABLE`` against a sealed fixture, else ``PARTIAL_FALSIFIER`` (author-controlled), and
  counts only with a recorded negative control that failed on its injected defect (otherwise it is a structural protection);
- ``formal`` becomes ``FORMAL_CHECK`` only with a certificate (checker, version, digest) from a formal operation the graph names
  at that node; a formal claim without one is recorded as no evidence and listed in ``rejected``. The kernel's own ``FORMAL_CHECK``
  is a self-declared tag (decision conflict 2 in the source map); this module never lets a tag stand in for a certificate.

A person's decision at a gate is necessary, never sufficient: it completes a claim that already has supporting evidence. Any
refuted claim makes the mission ``blocked-escalated``; everything else is ``qualified-draft`` with each gap named in ``unknowns``.

The mission is never ``verified`` here (RESEARCH-01). Every observation is handed in by the caller: a certificate is checked for
its shape only, a sealed fixture is any digest the caller gives (this module writes its vault entry itself), and nothing in this
run is produced or observed by G6. A claim's ``supported`` is the kernel's rule applied to those declarations, and every mission
names that gap as ``("mission", CALLER_DECLARED)``, even when every material claim is supported.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.research.echa import (
    ClaimRecord,
    EvidenceRecord,
    Ledger,
    LedgerError,
    StandardRef,
    evaluate_claim,
)
from g6.research.echa.records import UNSEALED

from .fixtures import standard_body
from .flag import require_research
from .ir import MissionIR

#: Operations whose certificate may stand behind a formal check (the census's formal A4 op). A certificate from any other
#: operation is not one.
FORMAL_CHECKERS = ("formal_methods.solve",)
KINDS = ("process", "executable", "formal", "model_opinion")
RESULTS = ("pass", "fail", "unknown")
PURPOSES = ("check", "negative_control")
NO_CERTIFICATE = "formal_check_without_a_valid_certificate"
#: The gap every mission names: its evidence, certificates and sealed fixtures are the caller's declarations, none of them
#: produced by G6 in this run, so the mission is at most ``qualified-draft``.
CALLER_DECLARED = "evidence_caller_declared"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class BundleRefused(ValueError):
    """The bundle cannot be built from what it was given, with every problem found."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Observation:
    """What one node produced, and what kind of evidence it is (never what the node's author says it proves)."""

    obs_id: str
    node: str
    predicate: str
    kind: str
    result: str
    scope: str
    source_operation: str
    oracle_version: str = ""
    #: The independence group (the evaluator, the model family): records that share one are one voice.
    group: str = ""
    #: The digest of the sealed fixture an executable check was taken against ("" when the author controls it).
    sealed_fixture: str = ""
    #: The ``obs_id`` of the negative control that shows this check can fail ("" when none was recorded).
    negative_control: str = ""
    certificate: Mapping[str, str] | None = None
    purpose: str = "check"
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        problems = [f"observation {self.obs_id or '?'}: {name} is required" for name in
                    ("obs_id", "node", "predicate", "scope", "source_operation") if not str(getattr(self, name)).strip()]
        if self.kind not in KINDS:
            problems.append(f"observation {self.obs_id}: kind must be one of {KINDS}")
        if self.result not in RESULTS:
            problems.append(f"observation {self.obs_id}: result must be one of {RESULTS}")
        if self.purpose not in PURPOSES:
            problems.append(f"observation {self.obs_id}: purpose must be one of {PURPOSES}")
        if problems:
            raise BundleRefused(problems)


@dataclass(frozen=True)
class ClaimSpec:
    claim_id: str
    predicate: str
    statement: str
    scope: str
    observation_ids: tuple[str, ...]
    breadth: str = "covered_cases"
    material: bool = True
    #: A gate (a person's decision) the claim also needs. A decision completes a claim; it never supports one.
    needs_gate: str = ""


@dataclass(frozen=True)
class Outcome:
    claim_id: str
    predicate: str
    material: bool
    #: ``supported``, ``unsupported`` (refuted by an active check) or ``unknown`` (never a pass).
    state: str
    reasons: tuple[str, ...]
    kernel_result: str


@dataclass(frozen=True)
class Bundle:
    mission_state: str
    outcomes: tuple[Outcome, ...]
    #: Every gap, as (subject, reason): a claim that is not supported, and a predicate nobody made a claim about.
    unknowns: tuple[tuple[str, str], ...]
    #: Observations recorded as no evidence, and why.
    rejected: tuple[tuple[str, str], ...]
    standard: StandardRef
    ledger: Ledger = field(repr=False)

    def evidence_categories(self) -> list[dict[str, Any]]:
        """The evidence records as written to the ledger (each with its oracle category)."""
        return [entry["body"] for entry in self.ledger.entries() if entry["kind"] == "evidence"]


def standard_ref(name: str) -> StandardRef:
    """A fixture's frozen standard as the kernel's record (its text hashes to the one the mission document carries)."""
    body = standard_body(name)
    return StandardRef.from_text(body["standard_id"], body["text"], body["source_provenance"], tuple(body["predicates"]))


def _valid_certificate(obs: Observation) -> bool:
    cert = obs.certificate
    return bool(cert and cert.get("checker") in FORMAL_CHECKERS and cert.get("checker") == obs.source_operation
                and str(cert.get("version", "")).strip() and _HEX64.match(str(cert.get("digest", ""))))


def _record(obs: Observation, standard: StandardRef) -> tuple[EvidenceRecord, str]:
    """The kernel record for an observation, and why it was recorded as no evidence ("" when it was not)."""
    common: dict[str, Any] = {
        "evidence_id": obs.obs_id, "predicate_id": obs.predicate, "oracle_version": obs.oracle_version or obs.source_operation,
        "scope": obs.scope, "known_limitations": obs.limitations, "source_ref": f"{obs.node}:{obs.source_operation}",
        "standard_sha256": standard.standard_sha256, "purpose": obs.purpose,
        "negative_control_ref": obs.negative_control}
    quiet = {**common, "oracle_category": "NO_DISCRIMINATING_EVIDENCE", "fixture_version": UNSEALED, "independent": False,
             "independence_group": "", "independence_rationale": "", "result": "unknown", "negative_control_ref": ""}
    if obs.kind == "process":
        return EvidenceRecord(**quiet), ""
    if obs.kind == "model_opinion":
        return EvidenceRecord(**{**common, "oracle_category": "HEURISTIC_REVIEW", "fixture_version": UNSEALED,
                                 "independent": False, "independence_group": obs.group, "independence_rationale": "",
                                 "result": obs.result, "negative_control_ref": ""}), ""
    if obs.kind == "formal":
        if not _valid_certificate(obs):
            return EvidenceRecord(**quiet), NO_CERTIFICATE
        cert = obs.certificate or {}
        return EvidenceRecord(**{
            **common, "oracle_category": "FORMAL_CHECK", "oracle_version": f"{cert['checker']}@{cert['version']}",
            "fixture_version": cert["digest"], "independent": True, "independence_group": obs.group or cert["checker"],
            "independence_rationale": "a formal checker separate from the producing component; the certificate digest names "
                                      "the exact formalisation it was taken against",
            "result": obs.result, "source_ref": f"{obs.node}:{obs.source_operation}:certificate:{cert['digest']}",
            "negative_control_ref": ""}), ""
    if obs.sealed_fixture:
        return EvidenceRecord(**{**common, "oracle_category": "INDEPENDENT_EXECUTABLE", "fixture_version": obs.sealed_fixture,
                                 "independent": True, "independence_group": obs.group,
                                 "independence_rationale": "taken against a fixture sealed outside the producing process",
                                 "result": obs.result}), ""
    return EvidenceRecord(**{**common, "oracle_category": "PARTIAL_FALSIFIER", "fixture_version": UNSEALED, "independent": False,
                             "independence_group": obs.group, "independence_rationale": "", "result": obs.result}), ""


def _refusals(ir: MissionIR, standard: StandardRef, claims: Sequence[ClaimSpec], observations: Sequence[Observation],
              decisions: Collection[str]) -> list[str]:
    problems: list[str] = []
    if standard.standard_sha256 != ir.standard.sha256:
        problems.append("the standard is not the mission's frozen standard (its hash differs from the mission's)")
    lacking = sorted(set(ir.standard.predicates) - set(standard.mandatory_predicates))
    if lacking:
        problems.append(f"the standard lacks predicate(s) the mission's standard names: {lacking}")
    nodes = {n.id: n for n in ir.nodes}
    gates = {n.id for n in ir.nodes if n.gate}
    known = set(ir.standard.predicates)
    seen: dict[str, Observation] = {}
    for obs in observations:
        if obs.obs_id in seen:
            problems.append(f"duplicate observation id {obs.obs_id!r}")
        seen[obs.obs_id] = obs
        node = nodes.get(obs.node)
        if node is None or node.gate:
            problems.append(f"observation {obs.obs_id!r} names node {obs.node!r}, which the graph does not have as an operation")
        elif obs.source_operation != node.source_operation:
            problems.append(f"observation {obs.obs_id!r} says operation {obs.source_operation!r} but node {obs.node!r} names "
                            f"{node.source_operation!r}")
        if obs.predicate not in known:
            problems.append(f"observation {obs.obs_id!r}: predicate {obs.predicate!r} is not in the standard")
    for obs in observations:
        if obs.negative_control:
            control = seen.get(obs.negative_control)
            if control is None or control.purpose != "negative_control":
                problems.append(f"observation {obs.obs_id!r} names a negative control {obs.negative_control!r} that nobody "
                                "recorded as one")
    ids: set[str] = set()
    for spec in claims:
        if spec.claim_id in ids:
            problems.append(f"duplicate claim id {spec.claim_id!r}")
        ids.add(spec.claim_id)
        if spec.predicate not in known:
            problems.append(f"claim {spec.claim_id!r}: predicate {spec.predicate!r} is not in the standard")
        problems += [f"claim {spec.claim_id!r} cites observation {oid!r}, which nobody recorded"
                     for oid in spec.observation_ids if oid not in seen]
        if spec.needs_gate and spec.needs_gate not in gates:
            problems.append(f"claim {spec.claim_id!r} needs gate {spec.needs_gate!r}, which the graph does not have")
    problems += [f"a decision names {gate!r}, which is no gate of this graph" for gate in sorted(set(decisions) - gates)]
    return problems


def _outcome(spec: ClaimSpec, verdict: dict[str, Any] | None, decided: bool) -> Outcome:
    if verdict is None:
        return Outcome(spec.claim_id, spec.predicate, spec.material, "unknown", ("no_evidence_recorded",), "none")
    kernel = verdict["result"]
    reasons = tuple(verdict["reasons"])
    if kernel == "unsupported":
        return Outcome(spec.claim_id, spec.predicate, spec.material, "unsupported", reasons, kernel)
    if kernel == "supported" and spec.needs_gate and not decided:
        return Outcome(spec.claim_id, spec.predicate, spec.material, "unknown", (*reasons, "human_decision_missing"), kernel)
    return Outcome(spec.claim_id, spec.predicate, spec.material, "supported" if kernel == "supported" else "unknown", reasons,
                   kernel)


def build_bundle(ir: MissionIR, standard: StandardRef, claims: Sequence[ClaimSpec], observations: Sequence[Observation], *,
                 decisions: Collection[str]) -> Bundle:
    """The bundle, or ``BundleRefused`` listing everything wrong with what it was given. ``decisions`` are the gates a person
    decided."""
    require_research()
    problems = _refusals(ir, standard, claims, observations, decisions)
    if problems:
        raise BundleRefused(problems)
    ledger = Ledger()
    ledger.add_standard(standard)
    records: dict[str, EvidenceRecord] = {}
    rejected: list[tuple[str, str]] = []
    refused: list[str] = []
    for obs in observations:
        try:
            records[obs.obs_id], why = _record(obs, standard)
        except ValueError as exc:
            refused.append(f"observation {obs.obs_id!r}: {exc}")
            continue
        if why:
            rejected.append((obs.obs_id, why))
    if refused:
        raise BundleRefused(refused)
    try:
        for digest in sorted({r.fixture_version for r in records.values() if r.fixture_version != UNSEALED}):
            ledger.add_vault(f"vault-{digest[:12]}", digest, files=1, roots=1)
        ordered = sorted(observations, key=lambda o: o.purpose != "negative_control")  # a control precedes the check it backs
        for obs in ordered:
            ledger.add_evidence(records[obs.obs_id])
        outcomes: list[Outcome] = []
        for spec in claims:
            if not spec.observation_ids:
                outcomes.append(_outcome(spec, None, False))
                continue
            ledger.add_claim(ClaimRecord(
                claim_id=spec.claim_id, subject=spec.statement, predicate_id=spec.predicate, scope=spec.scope,
                breadth=spec.breadth, estimand="oracle_scoped", population="the synthetic mission case",
                sample_unit="one mission", provenance=f"mission {ir.mission_id} ({ir.sha256[:12]})",
                assumptions=("the recorded observations are what the named operations produced",),
                known_limitations=("research bundle; the evidence is synthetic",),
                versions={"standard": standard.standard_id, "mission": ir.sha256[:12]},
                evidence_ids=spec.observation_ids, standard_sha256=standard.standard_sha256))
            outcomes.append(_outcome(spec, evaluate_claim(ledger, spec.claim_id), spec.needs_gate in decisions))
    except LedgerError as exc:
        raise BundleRefused([str(exc)]) from None
    return _bundle(ir, standard, tuple(outcomes), tuple(rejected), ledger)


def _bundle(ir: MissionIR, standard: StandardRef, outcomes: tuple[Outcome, ...], rejected: tuple[tuple[str, str], ...],
            ledger: Ledger) -> Bundle:
    covered = {o.predicate for o in outcomes if o.material}
    unknowns = [(o.claim_id, ",".join(o.reasons)) for o in outcomes if o.state == "unknown"]
    unknowns += [(f"predicate:{p}", "predicate_has_no_claim") for p in ir.standard.predicates if p not in covered]
    unknowns.append(("mission", CALLER_DECLARED))   # nothing here was produced by G6 in this run (RESEARCH-01)
    material = [o for o in outcomes if o.material]
    state = "blocked-escalated" if any(o.state == "unsupported" for o in material) else "qualified-draft"
    return Bundle(state, outcomes, tuple(unknowns), rejected, standard, ledger)
