# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A formal result as the mapping table's record (G6 0.3 design, section 15.3, "before promotion").

This module never sets a requirement's status: it hands ``g6.evidence.mapping.promote`` a record, or a lead. A
result is the kind ``formal_accepted`` only when all of this holds, and ``formal_model_result`` (a lead, never
promoted) otherwise:

- a person's acceptance of the exact statement resolves: the statement, its definitions, its domain and assumptions,
  its adapter, its rendering, the acceptance record and the standard each resolve to their bytes, and those bytes
  are the ones the acceptance's scope bound (``G6_E_EVIDENCE_NO_ACCEPTANCE`` names the part that does not);
- correspondence to the code was shown, on the real code (``G6_E_EVIDENCE_NO_CORRESPONDENCE``): a model result
  supports only its modelled claim;
- a negative control was rejected by this same check: a seeded function that returns an outcome the person rejected.
  Without one the record says "negative control unavailable; enforcement not established";
- the author is the drafting model's family. A person's confirmation is normative acceptance of the displayed rule
  and scope; it never makes a model-written statement person-authored.

The confirmed examples keep their own ``person_confirmed`` records whatever happens here.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from g6.core.ledger import canonical
from g6.evidence import mapping
from g6.evidence.certificate import Control
from g6.evidence.mapping import SourceRecord, id_of
from g6.harness.formalisation.adapters import ADAPTERS, ExampleObservation
from g6.harness.formalisation import smt_ir
from g6.harness.formalisation.code_check import CodeResult, decide, read_function
from g6.harness.formalisation.render import Unrenderable, render
from g6.harness.formalisation.scope import ScopeBinding
from g6.harness.formalisation.statement import FormalStatement

#: The two kinds this module gives a record, each a row of the mapping table.
KINDS = ("formal_model_result", "formal_accepted")
NO_CONTROL_TEXT = "negative control unavailable; enforcement not established"
CHECK_VERSION = "formalisation.code_check.v1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class FormalEvidence:
    """Everything one formal result on code rests on. ``acceptance_sha256`` is "" until a person has accepted the
    statement; ``author`` is ``model:<family>``, the family that drafted it."""

    acceptance_sha256: str
    statement: FormalStatement
    scope: ScopeBinding
    assessment_sha256: str
    code: CodeResult
    controls: tuple[Control, ...]
    author: str
    engine: str
    engine_version: str
    environment_sha256: str
    artefact_sha256: str
    producer: str
    artefact_seq: int
    frozen_seq: int
    seq: int
    standard_sha256: str
    predicates: tuple[str, ...]


def check_sha256(statement: FormalStatement, scope: ScopeBinding) -> str:
    """The frozen check's identity: the statement, its scope binding and the correspondence check's version."""
    return _sha(canonical([statement.sha256, scope.sha256, CHECK_VERSION]).encode("ascii"))


def certificate_sha256(found: FormalEvidence) -> str:
    """The digest of what the formal check produced: the acceptance, the assessment, the engine's query and verdict,
    and the replays on the real code."""
    query = found.code.query
    return _sha(canonical({
        "acceptance": found.acceptance_sha256, "assessment": found.assessment_sha256, "scope": found.scope.sha256,
        "engine": found.engine, "engine_version": found.engine_version,
        "query": "" if query is None else query.query.native_query, "native": "" if query is None else query.native,
        "reading": "" if query is None else query.reading, "result": found.code.result,
        "replayed": found.code.replayed,
        "replays": [json.loads(canonical(dict(one))) for one in found.code.replays]}).encode("ascii"))


def held_parts(statement: FormalStatement, rendering, scope: ScopeBinding, standard) -> dict[str, bytes]:
    """Each digest an acceptance's scope binds, with the bytes it is the digest of: what a store keeps so that
    ``problems`` can resolve them."""
    return {statement.sha256: statement.record(), statement.definitions_sha256: statement.definitions_record(),
            statement.assumptions_sha256: statement.assumptions_record(), rendering.sha256: rendering.record(),
            scope.sha256: scope.record(), standard.sha256: standard_record(standard)}


def standard_record(standard) -> bytes:
    """The bytes a standard's digest is taken over (``ExpectationStandard.sha256``)."""
    return canonical({"answers": [one.answer_sha256 for one in standard.expectations], "revision": standard.revision,
                      "previous": standard.previous, "replaced": list(standard.replaced)}).encode("utf-8")


def seeded_control(statement: FormalStatement, case: ExampleObservation, *, source: str, fn_name: str,
                   run: Callable[[dict], dict] | None, check_sha: str, predicate: str, seq: int,
                   timeout: float = 10.0) -> Control | None:
    """The negative control for one confirmed example: the function under review, made to return an outcome the
    person rejected at that example's input and left as it is everywhere else, given to the same correspondence
    check at that input. ``killed`` only when the check rejects it there with a counterexample reproduced on it. A
    statement that permits the rejected outcome does not reject it, so the control is not killed and nothing is
    promoted. ``None`` when the function is not readable, or no rejected outcome is a return this adapter binds:
    negative control unavailable."""
    adapter = ADAPTERS.get(statement.adapter)
    term, _ = read_function(statement, source, fn_name)
    if adapter is None or term is None:
        return None
    names = [name for name, _ in statement.observation.inputs]
    given, why = adapter.values(statement, case)
    if why or not names:
        return None
    fixed = [given[name] for name in names]
    for rejected in case.rejected:
        bound = adapter.bind(statement, case, rejected)
        if set(rejected) != {"returns"} or bound.unsupported:
            continue
        value = rejected["returns"]
        here = bound.conjuncts[:len(names)]   # the conjuncts that fix each parameter to the example's value
        at = smt_ir.read(here[0] if len(here) == 1 else "(and " + " ".join(here) + ")",
                         dict(statement.observation.inputs))
        literal = smt_ir.BoolLit(value) if type(value) is bool else smt_ir.IntLit(value)
        mutant = smt_ir.Ite(at, literal, term)

        def seeded(call, value=value, fixed=fixed):
            if list(call["args"]) == fixed and not call["kwargs"]:
                return {"returns": value}
            return run(call) if run is not None else {"unavailable": "no runner"}

        found = decide(statement, mutant, fn_name, run=seeded, timeout=timeout, at=here)
        return Control(control_id=id_of("formal-control", check_sha, predicate, case.expectation_id, repr(value),
                                        digest=True),
                       check_sha=check_sha, predicate=predicate, generated_by="g6", seq=seq,
                       killed=found.result == "fail" and found.replayed == "reproduced")
    return None


def _resolved(resolve: Callable[[str], bytes | None], sha: str, expected: bytes | None = None) -> bytes | None:
    """The bytes ``sha`` resolves to, only when they are its bytes (and, when given, the expected ones)."""
    try:
        data = resolve(sha) if sha else None
    except Exception:   # noqa: BLE001 - a store that cannot answer resolves nothing
        return None
    if not isinstance(data, bytes) or _sha(data) != sha or (expected is not None and data != expected):
        return None
    return data


def problems(found: FormalEvidence, *, resolve: Callable[[str], bytes | None]) -> list[tuple[str, str]]:
    """Each thing ``found`` lacks to be a ``formal_accepted`` record, as (code, what is missing, the part named
    first); empty when it lacks nothing."""
    out: list[tuple[str, str]] = []
    statement, scope = found.statement, found.scope

    def missing(part: str, why: str) -> None:
        out.append((mapping.NO_ACCEPTANCE, f"{part}: {why}"))

    for part, bound, now, record in (
            ("statement", scope.statement_sha256, statement.sha256, statement.record()),
            ("definitions", scope.definitions_sha256, statement.definitions_sha256, statement.definitions_record()),
            ("assumptions", scope.assumptions_sha256, statement.assumptions_sha256, statement.assumptions_record())):
        if bound != now:
            missing(part, "the acceptance's scope binds other bytes than this statement's")
        elif _resolved(resolve, bound, record) is None:
            missing(part, "its digest does not resolve to its bytes")
    if scope.adapter != statement.adapter or scope.adapter not in ADAPTERS:
        missing("adapter", f"{scope.adapter!r} is not the statement's registered adapter")
    try:
        shown = render(statement)
    except Unrenderable:
        shown = None
    if shown is None or shown.sha256 != scope.rendering_sha256:
        missing("rendering", "the bound rendering is not this statement's rendering")
    elif _resolved(resolve, scope.rendering_sha256, shown.record()) is None:
        missing("rendering", "its digest does not resolve to its bytes")
    if found.standard_sha256 != scope.standard_sha256:
        missing("standard", "the evidence names another standard than the acceptance was bound to")
    elif _resolved(resolve, scope.standard_sha256) is None:
        missing("standard", "its digest does not resolve to its bytes")
    accepted = _resolved(resolve, found.acceptance_sha256)
    if not found.acceptance_sha256:
        missing("acceptance", "no person's acceptance of this statement is recorded")
    elif accepted is None:
        missing("acceptance", "its digest does not resolve to its bytes")
    else:
        try:
            held = json.loads(accepted)
        except ValueError:
            held = None
        if (not isinstance(held, dict) or held.get("scope_sha256") != scope.sha256
                or held.get("assessment_sha256") != found.assessment_sha256
                or not str(held.get("accepted_by", "")).startswith("person:")):
            missing("acceptance", "it is not a person's acceptance of this scope and this assessment")
    code = found.code
    if not (code.replayed == "reproduced" and code.result in ("pass", "fail")):
        out.append((mapping.NO_CORRESPONDENCE, f"correspondence to the code is not established: {code.why}"))
    sha = check_sha256(statement, scope)
    for predicate in found.predicates:
        if not any(c.check_sha == sha and c.predicate == predicate and c.killed for c in found.controls):
            out.append((mapping.NO_CONTROL, f"{predicate}: {NO_CONTROL_TEXT}"))
    if not found.predicates:
        out.append((mapping.NO_STANDARD, "it names no predicate of the standard"))
    if not isinstance(found.author, str) or not found.author.startswith("model:") or len(found.author) <= 6:
        out.append((mapping.NO_INDEPENDENCE, "the author of a formal statement is the model family that drafted it "
                                             "(model:<family>); a person's confirmation is not authorship"))
    return out


def source_record(found: FormalEvidence, *, resolve: Callable[[str], bytes | None]) -> SourceRecord:
    """The mapping table's record of ``found``: ``formal_accepted`` when ``problems`` finds nothing, else the lead
    ``formal_model_result``. Either way it states what it is about; the issuer alone sets a requirement's status."""
    kind = "formal_model_result" if problems(found, resolve=resolve) else "formal_accepted"
    code = found.code
    sha = check_sha256(found.statement, found.scope)
    result = code.result if code.replayed == "reproduced" and code.result in ("pass", "fail") else "unknown"
    scope = (f"{found.scope.covers}; formal statement {found.statement.sha256[:16]} of fragment "
             f"{found.statement.fragment}; the code only as the restricted return reader reads it and the replays "
             "showed it" + ("" if kind == "formal_accepted" else f"; a result about the model only: {NO_CONTROL_TEXT}"
                            if any(c == mapping.NO_CONTROL for c, _ in problems(found, resolve=resolve)) else
                            "; a result about the model only"))
    return SourceRecord(
        kind=kind, record_id=id_of("formal", kind, found.statement.sha256, found.artefact_sha256, sha, digest=True),
        predicates=tuple(found.predicates), result=result, artefact_sha256=found.artefact_sha256,
        producer=found.producer, artefact_seq=found.artefact_seq, standard_sha256=found.standard_sha256,
        check_id=id_of("formal-check", found.statement.sha256, found.scope.sha256, digest=True), check_sha256=sha,
        author=found.author, frozen_seq=found.frozen_seq, environment_sha256=found.environment_sha256, seq=found.seq,
        scope=scope, model_sha256=found.statement.sha256, certificate=certificate_sha256(found),
        controls=tuple(found.controls))


def acceptance_record(*, scope_sha256: str, assessment_sha256: str, accepted_by: str,
                      **more: Mapping | str | list) -> bytes:
    """The bytes of an acceptance record as ``problems`` reads one: it names the scope and the assessment it
    accepts and the person who accepted. The acceptance store writes its records through this."""
    return canonical({"scope_sha256": scope_sha256, "assessment_sha256": assessment_sha256,
                      "accepted_by": accepted_by, **more}).encode("ascii")


__all__ = ["CHECK_VERSION", "KINDS", "NO_CONTROL_TEXT", "FormalEvidence", "acceptance_record", "certificate_sha256",
           "check_sha256", "held_parts", "problems", "seeded_control", "source_record", "standard_record"]
