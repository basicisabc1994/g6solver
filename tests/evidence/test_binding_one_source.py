# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A record says what it is about in one place (G6 0.3, slice 2, repair 1; review 1's findings 1, 2, 7 and 8).

A record carries its artefact, standard, predicate, request, check, environment and scope; a bound one carries most of
them twice, in its custody binding and in its own fields. Every reader takes them from one accessor
(``g6.evidence.mapping.about``, and ECHA's ``custody.about`` built on the same ``bound_parts``): from the binding when
there is one, with a field that repeats a bound part and disagrees refused by the code that names the part.

- the mapping promotes nothing a field retargets (finding 1), and ECHA's claim rule counts nothing a field retargets to
  another standard or predicate (finding 8);
- a record id is one record: reused with any other part, environment and scope included, it is refused (finding 2);
- a bound check's id covers every part of its binding, so receipts for two predicates are never one check, and a
  check id another record gives another check is refused, never merged (finding 7);
- the inventory, built from the code, lists every place a record's part is read and fails on any read not through the
  accessor.
"""
from __future__ import annotations

import ast
from dataclasses import fields, replace
from pathlib import Path

import pytest

import g6
from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Control, MissionFacts
from g6.evidence import mapping as m
from g6.research.echa import ClaimRecord, EvidenceRecord, Ledger, StandardRef, evaluate_claim
from g6.research.echa.records import canonical

A, OTHER, STANDARD, CODE, FIXTURE, ENV = "a" * 64, "b" * 64, "5" * 64, "c" * 64, "f" * 64, "e" * 64


def facts(*predicates: str) -> MissionFacts:
    named = predicates or ("R1",)
    return MissionFacts("1" * 64, STANDARD, STANDARD, named, named)


def binding(predicate: str = "R1", **over) -> m.CheckBinding:
    return m.CheckBinding(**{"request_id": "request", "task_id": "task", "predicate_id": predicate,
                             "artifact_sha256": A, "standard_sha256": STANDARD, "fixture_sha256": FIXTURE,
                             "evaluator_sha256": CODE, **over})


def bound(predicate: str = "R1", result: str = "pass", record_id: str = "run", **over) -> m.SourceRecord:
    return m.SourceRecord.from_binding(
        binding(predicate, **over), kind="independent_executable", record_id=record_id, result=result,
        producer="agent", artefact_seq=1, author="reviewer", frozen_seq=0, environment_sha256=ENV, seq=3,
        scope="one case", controls=(Control("control-" + predicate, CODE, predicate, "g6", 2, True),), positive_seq=1)


def plain(**over) -> m.SourceRecord:
    base = {"kind": "independent_executable", "record_id": "run", "predicates": ("R1",), "result": "pass",
            "artefact_sha256": A, "producer": "agent", "artefact_seq": 1, "standard_sha256": STANDARD,
            "check_id": "check", "check_sha256": CODE, "author": "reviewer", "frozen_seq": 0,
            "environment_sha256": ENV, "seq": 3, "scope": "one input",
            "controls": (Control("control", CODE, "R1", "g6", 2, True),)}
    return m.SourceRecord(**{**base, **over})


def refused_codes(issued: m.Issued) -> list[str]:
    return [code for _, found in issued.refused for code, _ in found]


# --- review 1's findings, brought in as this change's own -------------------------------------------------------------


def test_finding_1_a_custody_binding_cannot_be_retargeted_by_its_redundant_fields():
    rec = bound()
    assert m.issue_records(facts(), Artifact(A, "agent", 1), [rec]).certificate.verified_predicates == ("R1",)
    retargeted = replace(rec, artefact_sha256=OTHER)
    issued = m.issue_records(facts(), Artifact(OTHER, "agent", 1), [retargeted])
    assert issued.certificate.verified_predicates == (), (
        "mapping counted a receipt whose custody binding names another artefact", issued.certificate.verified_predicates)
    assert m.BINDING_ARTEFACT in refused_codes(issued)


def test_finding_2_a_repeated_record_id_with_another_scope_and_environment_is_refused():
    rec = plain()
    clash = replace(rec, environment_sha256="f" * 64, scope="all inputs")
    issued = m.issue_records(facts(), Artifact(A, "agent", 1), [rec, clash])
    assert any(m.CONFLICTING in [code for code, _ in codes] for _, codes in issued.refused), (
        "same id silently replaced its provenance body", issued.refused, issued.ledger.get(m.PROVENANCE_KIND, "run"))
    kept = issued.ledger.get(m.PROVENANCE_KIND, "run")
    assert (kept["environment_sha256"], kept["scope"]) == (ENV, "one input")   # the first body, never overwritten


def test_finding_7_receipts_for_separate_bound_predicates_keep_their_own_results():
    issued = m.issue_records(facts("R1", "R2"), Artifact(A, "agent", 1),
                             [bound("R1", "pass", "run-R1", request_id="request-R1"),
                              bound("R2", "fail", "run-R2", request_id="request-R2")])
    states = [(s.support, s.refute) for s in issued.certificate.predicates]
    assert not states[0][1] and not states[1][0], ("each predicate received the other bound predicate's result", states)
    assert states == [(True, False), (False, True)]   # and each kept its own


def _echa(standard: StandardRef, predicate: str, original: StandardRef, other: StandardRef) -> dict:
    fixture = "f" * 64
    pinned = binding("R1", standard_sha256=original.standard_sha256, fixture_sha256=fixture)
    control = EvidenceRecord(
        evidence_id="control", predicate_id="R1", oracle_category="INDEPENDENT_EXECUTABLE",
        oracle_version="check@1", fixture_version=fixture, independent=True, independence_group="reviewer",
        independence_rationale="separate evaluator", result="fail", scope="one case", known_limitations=(),
        source_ref=canonical(pinned.to_dict()), standard_sha256=original.standard_sha256, purpose="negative_control")
    passed = replace(control, evidence_id="pass", result="pass", purpose="check", negative_control_ref="control")
    ledger = Ledger()
    ledger.add_standard(original)
    ledger.add_standard(other)
    ledger.add_vault("fixture", fixture, files=1, roots=1)
    ledger.add_evidence(replace(control, predicate_id=predicate, standard_sha256=standard.standard_sha256))
    ledger.add_evidence(replace(passed, predicate_id=predicate, standard_sha256=standard.standard_sha256))
    ledger.add_claim(ClaimRecord(
        claim_id="claim", subject="sha256:" + A, predicate_id=predicate, scope="one case", breadth="covered_cases",
        estimand="oracle_scoped", population="one", sample_unit="one", provenance="test",
        assumptions=("the checker ran",), known_limitations=("one case",), versions={"checker": "1"},
        evidence_ids=("pass",), standard_sha256=standard.standard_sha256))
    return evaluate_claim(ledger, "claim")


ORIGINAL = StandardRef.from_text("original", "R1 holds", "task", ("R1",))
ELSEWHERE = StandardRef.from_text("other", "R2 holds", "task", ("R2", "R1"))


def test_finding_8_echa_does_not_retarget_a_bound_receipt_to_another_standard_and_predicate():
    assert _echa(ORIGINAL, "R1", ORIGINAL, ELSEWHERE)["result"] == "supported"   # positive control
    verdict = _echa(ELSEWHERE, "R2", ORIGINAL, ELSEWHERE)
    assert verdict["result"] != "supported", (
        "the claim rule ignored the original standard and predicate in the custody binding", verdict["result"])
    assert {m.BINDING_STANDARD, m.BINDING_PREDICATE} <= set(verdict["reasons"])


@pytest.mark.parametrize(("standard", "predicate", "code"), [
    (ELSEWHERE, "R1", m.BINDING_STANDARD),   # the standard alone
    (ORIGINAL, "R2", m.BINDING_PREDICATE),   # the predicate alone
])
def test_echa_refuses_each_part_a_field_retargets_alone_with_the_code_that_names_it(standard, predicate, code):
    verdict = _echa(standard, predicate, ORIGINAL, ELSEWHERE)
    assert verdict["result"] != "supported" and code in verdict["reasons"], verdict


# --- each part replaced alone ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("field_name", "value", "code"), [
    ("artefact_sha256", OTHER, m.BINDING_ARTEFACT),
    ("standard_sha256", "6" * 64, m.BINDING_STANDARD),
    ("predicates", ("R2",), m.BINDING_PREDICATE),
    ("check_id", "task:another", m.BINDING_CHECK),
    ("check_sha256", "9" * 64, m.BINDING_CHECK),
])
def test_a_field_that_repeats_a_bound_part_with_another_value_is_refused_with_the_code_naming_it(field_name, value,
                                                                                               code):
    rec = bound()
    assert m.problems(rec, facts=facts("R1", "R2"), artifact=Artifact(A, "agent", 1)) == []
    changed = replace(rec, **{field_name: value})
    found = [c for c, _ in m.problems(changed, facts=facts("R1", "R2"), artifact=Artifact(A, "agent", 1))]
    assert found and found[0] == code, found
    issued = m.issue_records(facts("R1", "R2"), Artifact(A, "agent", 1), [changed])
    assert issued.promoted == () and code in refused_codes(issued)
    assert not any(s.support or s.refute for s in issued.certificate.predicates)


@pytest.mark.parametrize("part", ["request_id", "task_id", "fixture_sha256", "predicate_id", "artifact_sha256",
                                  "standard_sha256", "evaluator_sha256"])
def test_a_binding_replaced_under_its_record_s_fields_is_refused_never_read_from_the_fields(part):
    rec = bound()
    value = {"request_id": "elsewhere", "task_id": "elsewhere", "predicate_id": "R2"}.get(part, "9" * 64)
    moved = replace(rec, binding=replace(rec.binding, **{part: value}))
    issued = m.issue_records(facts("R1", "R2"), Artifact(A, "agent", 1), [moved])
    assert issued.promoted == () and m.BINDING_CHECK in refused_codes(issued), issued.refused


def test_a_binding_that_does_not_read_as_one_is_refused():
    broken = replace(bound(), binding={"request_id": "request"})   # malformed: a dict where a binding belongs
    found = [c for c, _ in m.problems(broken, facts=facts(), artifact=Artifact(A, "agent", 1))]
    assert found[0] == m.BINDING_UNREADABLE
    assert m.issue_records(facts(), Artifact(A, "agent", 1), [broken]).certificate.verified_predicates == ()


# --- one id, one record; one check id, one check --------------------------------------------------------------------


@pytest.mark.parametrize("over", [
    {"environment_sha256": "f" * 64}, {"scope": "all inputs"}, {"result": "fail"}, {"seq": 4},
    {"author": "someone else"}, {"positive_seq": 1}, {"controls": ()}, {"predicates": ("R2",)},
    {"check_sha256": "9" * 64}, {"binding": binding()},
])
def test_a_duplicate_record_id_differing_in_any_part_is_refused_and_the_first_stands(over):
    first = plain()
    issued = m.issue_records(facts("R1", "R2"), Artifact(A, "agent", 1), [first, replace(first, **over)])
    assert [rid for rid, _ in issued.refused] == ["run"] and refused_codes(issued) == [m.CONFLICTING], issued.refused
    assert issued.promoted == (("run", "independent_executable", "independent_executable"),)
    # slice 2's follow-up (review 4's finding 2): refusing a record never drops what it observed, so a duplicate that
    # says the run failed is kept as a caveat, and the first record's pass no longer makes R1 verified by itself
    failed = over.get("result") == "fail"
    assert issued.certificate.verified_predicates == (() if failed else ("R1",))
    assert bool(issued.caveats) is failed and not issued.certificate.predicates[0].refute


def test_the_same_record_twice_is_one_record():
    issued = m.issue_records(facts(), Artifact(A, "agent", 1), [plain(), plain()])
    assert issued.refused == () and len(issued.certificate.receipts) == 1


def test_two_predicates_sharing_an_unbound_check_id_are_never_merged():
    one = plain(record_id="r1")
    other = plain(record_id="r2", predicates=("R2",), result="fail", positive_seq=1,
                  controls=(Control("control-2", CODE, "R2", "g6", 2, True),))
    issued = m.issue_records(facts("R1", "R2"), Artifact(A, "agent", 1), [one, other])
    assert [(rid, [c for c, _ in found]) for rid, found in issued.refused] == [("r2", [m.CONFLICTING])]
    states = {s.predicate: (s.support, s.refute) for s in issued.certificate.predicates}
    assert states == {"R1": (True, False), "R2": (False, False)}   # neither got the other's result


def test_two_receipts_of_one_bound_check_are_one_check_with_both_receipts():
    issued = m.issue_records(facts(), Artifact(A, "agent", 1), [bound(record_id="first"),
                                                              bound(result="fail", record_id="later")])
    assert len(issued.certificate.checks) == 1 and len(issued.certificate.receipts) == 2
    statement = issued.certificate.predicates[0]
    assert statement.support and statement.refute   # a conflict stays a fourth state


def test_a_bound_check_id_covers_every_part_of_its_binding():
    ids = {m.check_id_of(binding(**{part: value}))
           for part, value in [("request_id", "request"), ("request_id", "r2"), ("task_id", "t2"),
                               ("predicate_id", "R2"), ("artifact_sha256", OTHER), ("standard_sha256", "6" * 64),
                               ("fixture_sha256", "7" * 64), ("evaluator_sha256", "9" * 64)]}
    assert len(ids) == 8


def test_the_provenance_kept_beside_a_receipt_survives_a_restart(tmp_path):
    issued = m.issue_records(facts(), Artifact(A, "agent", 1), [bound()])
    path = tmp_path / "ledger.jsonl"
    kept = ChainLedger(path)
    for entry in issued.ledger.entries():
        kept.append(entry["kind"], entry["id"], entry["body"])
    reopened = ChainLedger(path, expect_head=kept.head())
    provenance = reopened.get(m.PROVENANCE_KIND, "run")
    assert provenance["binding"] == binding().to_dict()
    assert (provenance["environment_sha256"], provenance["scope"]) == (ENV, "one case")


def test_the_patch_review_adapter_reads_each_refused_record_s_check_through_the_accessor():
    from g6.harness import requirement_evidence

    req = {"id": "R1", "text": "t", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": "tests/test_x.py::test_a", "provenance": "stated", "verdict": "passed"}]}
    got = requirement_evidence.assess([req], [], artifact=A, base_commit="0" * 40, check_sha={},
                                      written=lambda _check: False, environment={})
    assert [(r["check"], r["record"]) for r in got["refused"]] == [("tests/test_x.py::test_a",
                                                                     m.id_of("stated-run", "tests/test_x.py::test_a"))]


# --- the inventory: every place a record's part is read, from the code ----------------------------------------------

SRC = Path(g6.__file__).resolve().parent
#: Where a record's parts are defined and read: the mapping, and ECHA's records and custody.
HOMES = ("g6.evidence.mapping", "g6.research.echa.records", "g6.research.echa.custody")
#: The names that make a module a reader of records when it imports them from a home (or ECHA's package or view).
RECORD_NAMES = frozenset({"SourceRecord", "CheckBinding", "EvidenceRecord", "about", "bound_parts", "binding_of",
                          "source_record"})
#: The one accessor in each home: the only functions that may read a record's own fields for a part it is about.
ACCESSORS = frozenset({("evidence/mapping.py", "about"), ("research/echa/custody.py", "about")})


def _module(rel: str) -> str:
    parts = rel.removesuffix(".py").split("/")
    return ".".join(["g6", *parts[:-1]] if parts[-1] == "__init__" else ["g6", *parts])


def _imports(rel: str, tree: ast.Module) -> list[tuple[str, set[str]]]:
    package = _module(rel).split(".") if rel.endswith("__init__.py") else _module(rel).split(".")[:-1]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
            found.append((base, {alias.name for alias in node.names}))
    return found


def _accessor_fields() -> frozenset[str]:
    """The fields each accessor reads off its record (``rec``), with every field of the custody binding: the names a
    read of a record's part is made by."""
    names = {f.name for f in fields(m.CheckBinding)}
    for rel, function in ACCESSORS:
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        body = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
        names |= {node.attr for node in ast.walk(body) if isinstance(node, ast.Attribute)
                  and isinstance(node.value, ast.Name) and node.value.id == "rec" and node.attr != "binding"}
    return frozenset(names)


PART_FIELDS = _accessor_fields()


def readers(trees: dict[str, ast.Module]) -> list[str]:
    """The modules that hold or read records: a home, or one that imports a record's name from one (or from ECHA's
    package or view, which re-export them)."""
    sources = {*HOMES, "g6.research.echa", "g6.research.echa.view"}
    return sorted(rel for rel, tree in trees.items() if _module(rel) in HOMES or any(
        base in sources and names & RECORD_NAMES for base, names in _imports(rel, tree)))


def reads(tree: ast.Module) -> set[tuple[str, str, str]]:
    """(function, receiver, field) for every read of a part's field in a module."""
    found: set[tuple[str, str, str]] = set()

    def visit(node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*where, child.name])
                continue
            if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load) and child.attr in PART_FIELDS:
                found.add((".".join(where) or "<module>", ast.unparse(child.value), child.attr))
            visit(child, where)

    visit(tree, [])
    return found


BINDING = "a custody binding's own part (CheckBinding)"
THROUGH = "through the accessor: an About the accessor returned"
ISSUER = "the issuer's own value (Check, Receipt, Certificate, MissionFacts), not a record"
FORMAL = "the formal path's evidence (FormalEvidence), from which it builds the record; the mapping reads that record"
#: Every (module, function, receiver) that reads a part's field, with its role and what the receiver is.
PLACES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("evidence/mapping.py", "check_id_of", "binding"): ("binding", BINDING),
    ("evidence/mapping.py", "bound_parts", "binding"): ("binding", BINDING),
    ("evidence/mapping.py", "about", "rec"): ("accessor", "the one accessor: compares the fields with the binding"),
    ("evidence/mapping.py", "SourceRecord.from_binding", "parts"): ("through", THROUGH),
    ("evidence/mapping.py", "problems", "parts"): ("through", THROUGH),
    ("evidence/mapping.py", "promote", "parts"): ("through", THROUGH),
    ("evidence/mapping.py", "issue_records", "parts"): ("through", THROUGH),
    ("evidence/mapping.py", "_merged", "old"): ("other", ISSUER),
    ("evidence/mapping.py", "_merged", "new"): ("other", ISSUER),
    ("evidence/mapping.py", "problems", "facts"): ("other", ISSUER),
    ("evidence/mapping.py", "issue_records", "check"): ("other", ISSUER),
    ("evidence/mapping.py", "issue_records", "receipt"): ("other", ISSUER),
    # slice 2's follow-up (review 4's findings 2 and 3): a refused record's caveat, and one receipt read alone
    ("evidence/mapping.py", "caveat_of", "parts"): ("through", THROUGH),
    ("evidence/mapping.py", "caveat_of", "facts"): ("other", ISSUER),
    ("evidence/mapping.py", "receipt_roles", "check"): ("other", ISSUER + ": a check the certificate records"),
    ("evidence/mapping.py", "receipt_roles", "receipt"): ("other", ISSUER + ": a receipt the certificate records"),
    ("evidence/mapping.py", "receipt_roles", "alone"): ("other", ISSUER + ": its certificate for one receipt"),
    ("harness/requirement_evidence.py", "codes", "check"): ("other", ISSUER),
    ("harness/requirement_evidence.py", "codes", "receipt"): ("other", ISSUER),
    ("harness/requirement_evidence.py", "codes", "r"): ("other", ISSUER),
    ("harness/requirement_evidence.py", "codes", "c"): ("other", ISSUER),
    ("harness/requirement_evidence.py", "assess", "check"): ("other", ISSUER),
    ("harness/requirement_evidence.py", "assess", "certificate"): ("other", ISSUER),
    ("harness/requirement_evidence.py", "assess", "about(rec)[0]"): ("through", THROUGH),
    # G6 0.3, slice 3: the issuer's statement for the confirmed expectations the review enforced
    ("harness/requirement_evidence.py", "confirmed_evidence", "certificate"): ("other", ISSUER),
    ("harness/requirement_evidence.py", "confirmed_evidence", "one"): (
        "other", "a confirmed expectation's own value (slice 2's record, derived from the question's bytes), from "
                 "which the record is built; not a record"),
    ("research/echa/custody.py", "about", "rec"): ("accessor", "ECHA's accessor: compares the fields with the binding"),
    ("research/echa/custody.py", "checked_evidence", "expected"): ("binding", BINDING),
    ("research/echa/view.py", "source_record", "parts"): ("through", THROUGH),
    ("research/echa/claims.py", "_control_holds", "of_control"): ("through", THROUGH),
    ("research/echa/claims.py", "_control_holds", "of_check"): ("through", THROUGH),
    ("research/echa/claims.py", "evaluate_claim", "parts"): ("through", THROUGH),
    ("research/echa/claims.py", "evaluate_claim", "about(rec)[0]"): ("through", THROUGH),
    ("research/echa/claims.py", "evaluate_claim", "claim"): ("other", "the claim's own (ClaimRecord), not a record"),
    ("research/echa/ledger.py", "_semantic", "parts"): ("through", THROUGH),
    ("research/echa/ledger.py", "_semantic", "of_control"): ("through", THROUGH),
    ("research/echa/ledger.py", "_semantic", "claim"): ("other", "the claim's own (ClaimRecord), not a record"),
    ("research/echa/records.py", "EvidenceRecord.__post_init__", "self"): (
        "other", "the record checks the form of its own fields when it is built; it reads no part for a decision"),
    ("research/echa/records.py", "ClaimRecord.__post_init__", "self"): ("other", "a claim's own form, not a record"),
    ("research/echa/records.py", "StandardRef.__post_init__", "self"): ("other", "a standard's own form"),
    ("research/echa/example.py", "_evidence", "STANDARD"): ("other", "a standard (StandardRef), not a record"),
    ("research/echa/example.py", "build", "STANDARD"): ("other", "a standard (StandardRef), not a record"),
    ("research/mission_bridge/bundle.py", "_record", "obs"): (
        "other", "the bundle's observation, from which it builds an ECHA record; the claim rule reads that record"),
    ("research/mission_bridge/bundle.py", "_record", "standard"): ("other", "a standard (StandardRef)"),
    ("research/mission_bridge/bundle.py", "_refusals", "standard"): ("other", "a standard (StandardRef)"),
    ("research/mission_bridge/bundle.py", "_refusals", "ir.standard"): ("other", "the mission IR's standard"),
    ("research/mission_bridge/bundle.py", "build_bundle", "spec"): ("other", "a claim's specification"),
    ("research/mission_bridge/bundle.py", "build_bundle", "standard"): ("other", "a standard (StandardRef)"),
    ("research/mission_bridge/bundle.py", "_bundle", "ir.standard"): ("other", "the mission IR's standard"),
    # the formal path (design section 15): what a formal result rests on, before it is made a record
    ("harness/formalisation/receipts.py", "certificate_sha256", "found"): ("other", FORMAL),
    ("harness/formalisation/receipts.py", "problems", "found"): ("other", FORMAL),
    ("harness/formalisation/receipts.py", "problems", "scope"): (
        "other", "the acceptance's scope binding (ScopeBinding), which names the standard it was bound to"),
    ("harness/formalisation/receipts.py", "source_record", "found"): ("other", FORMAL),
}
ROLES = frozenset({"accessor", "binding", "through", "other"})


def _trees() -> dict[str, ast.Module]:
    return {path.relative_to(SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
            for path in sorted(SRC.rglob("*.py")) if "vendor" not in path.parts}


TREES = _trees()
FOUND = {(rel, function, receiver) for rel in readers(TREES) for function, receiver, _ in reads(TREES[rel])}


def test_every_read_of_a_record_s_part_is_listed_and_every_listed_one_is_still_there():
    assert sorted(FOUND - set(PLACES)) == [], "unlisted reads: read the part through the accessor, or say what it is"
    assert sorted(set(PLACES) - FOUND) == [], "listed reads the scan no longer finds: remove them"
    assert {role for role, _ in PLACES.values()} <= ROLES and all(why.strip() for _, why in PLACES.values())


def test_only_the_accessors_read_a_record_s_own_fields():
    assert {(rel, function) for (rel, function, _), (role, _) in PLACES.items() if role == "accessor"} == ACCESSORS


def test_the_readers_the_brief_names_are_scanned():
    found = readers(TREES)
    for rel in ("evidence/mapping.py", "harness/requirement_evidence.py", "research/echa/claims.py",
                "research/echa/view.py", "research/echa/ledger.py", "research/mission_bridge/bundle.py"):
        assert rel in found, rel


def test_the_scan_finds_a_planted_read_not_through_the_accessor():
    # survival control: a reader that takes the artefact from a record's field is found, and is not allowed
    planted = ast.parse("from g6.evidence.mapping import SourceRecord\n\n\ndef leak(rec):\n"
                        "    return rec.artefact_sha256, rec.predicate_id\n")
    assert readers({"planted.py": planted}) == ["planted.py"]
    assert reads(planted) == {("leak", "rec", "artefact_sha256"), ("leak", "rec", "predicate_id")}
    assert ("planted.py", "leak", "rec") not in PLACES
    assert {"artefact_sha256", "standard_sha256", "predicates", "check_id", "check_sha256", "environment_sha256",
            "scope", "predicate_id"} <= PART_FIELDS
