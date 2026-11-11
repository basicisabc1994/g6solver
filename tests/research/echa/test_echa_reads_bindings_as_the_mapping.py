# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA reads a binding as the mapping does, and matches by bytes, not by name (G6 0.3, slice 2, repair 2; review 2's
findings 3 and 4; classes ``failure_read_as_success`` and ``identity_from_rendering``, both met before, and
``one_fact_two_rules`` with repair 1's accessor).

- every binding ECHA reads goes through the mapping's one reader (``g6.evidence.mapping.read_binding`` and
  ``read_about``): no binding and a binding that does not read as one are different results, and the second is refused
  with ``G6_E_EVIDENCE_BINDING_UNREADABLE``, at the ledger and in the claim rule (finding 3);
- every identity ECHA compares (the evaluator, the check, the artefact) is the digest the accessor gives: a control
  taken by another evaluator's bytes never activates a check, whatever its label (finding 4);
- the inventory, built from the code, lists every binding read and every identity comparison in ``research/echa``.
"""
from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

import g6
from g6.core.ledger import LedgerError
from g6.evidence import mapping as m
from g6.research.echa import ClaimRecord, EvidenceRecord, Ledger, StandardRef, evaluate_claim
from g6.research.echa import custody
from g6.research.echa.records import canonical

A, DEFECT, FIXTURE, CODE, OTHER_CODE = "a" * 64, "b" * 64, "f" * 64, "c" * 64, "d" * 64
STANDARD = StandardRef.from_text("standard", "R1 holds", "task", ("R1",))


def binding(**over) -> m.CheckBinding:
    return m.CheckBinding(**{"request_id": "request", "task_id": "task", "predicate_id": "R1", "artifact_sha256": A,
                             "standard_sha256": STANDARD.standard_sha256, "fixture_sha256": FIXTURE,
                             "evaluator_sha256": CODE, **over})


def evidence(evidence_id: str, source_ref: str, **over) -> EvidenceRecord:
    base = {"evidence_id": evidence_id, "predicate_id": "R1", "oracle_category": "INDEPENDENT_EXECUTABLE",
            "oracle_version": "check@1", "fixture_version": FIXTURE, "independent": True,
            "independence_group": "reviewer", "independence_rationale": "separate evaluator", "result": "pass",
            "scope": "one input", "known_limitations": (), "source_ref": source_ref,
            "standard_sha256": STANDARD.standard_sha256, "purpose": "check"}
    return EvidenceRecord(**{**base, **over})


def claim(subject: str = "sha256:" + A, evidence_ids=("pass",), **over) -> ClaimRecord:
    base = {"claim_id": "claim", "subject": subject, "predicate_id": "R1", "scope": "one input",
            "breadth": "covered_cases", "estimand": "oracle_scoped", "population": "one", "sample_unit": "one",
            "provenance": "test", "assumptions": ("check ran",), "known_limitations": ("one case",),
            "versions": {"checker": "1"}, "evidence_ids": tuple(evidence_ids),
            "standard_sha256": STANDARD.standard_sha256}
    return ClaimRecord(**{**base, **over})


class Held:
    """A ledger-like store the claim rule reads, which holds whatever it is given: the claim rule's own reading of a
    record is tested apart from the ledger's refusal at insertion."""

    def __init__(self, *records: EvidenceRecord, made: ClaimRecord) -> None:
        self._held = {("evidence", r.evidence_id): r.to_dict() for r in records} | {("claim", "claim"): made.to_dict()}

    def get(self, kind: str, ident: str) -> dict:
        return dict(self._held[(kind, ident)])

    def contaminated_fixtures(self) -> set[str]:
        return set()


def ledger() -> Ledger:
    led = Ledger()
    led.add_standard(STANDARD)
    led.add_vault("fixture", FIXTURE, files=1, roots=1)
    return led


def bound_pair(control_binding: m.CheckBinding, check_binding: m.CheckBinding, **check_over):
    control = evidence("control", canonical(control_binding.to_dict()), result="fail", purpose="negative_control")
    passed = evidence("pass", canonical(check_binding.to_dict()), negative_control_ref="control", **check_over)
    return control, passed


# --- finding 3: an unreadable binding is refused, never read as no binding -------------------------------------------


def _malformed() -> list[str]:
    good = canonical(binding().to_dict())
    return [
        json.dumps({**binding().to_dict(), "request_id": ""}),                     # finding 3: a field not valid
        good[:-1] + ',"artifact_sha256":"' + DEFECT + '"}',                       # a repeated key
        good.replace('"fixture_sha256":"' + FIXTURE + '"', '"fixture_sha256":NaN'),  # a number that is not one
        json.dumps({k: v for k, v in binding().to_dict().items() if k != "task_id"}),  # a field missing
        json.dumps({**binding().to_dict(), "verified": True}),                    # a field more
        good[: len(good) // 2],                                                   # cut short
        "  " + good[:-1],                                                         # an object never closed
    ]


MALFORMED = _malformed()


def test_finding_3_an_unreadable_custody_binding_is_not_downgraded_to_unbound_evidence():
    malformed = json.dumps({**binding().to_dict(), "request_id": ""})   # its artefact, standard and predicate are there
    record = evidence("control", malformed, result="fail", purpose="negative_control")
    passed = replace(record, evidence_id="pass", result="pass", purpose="check", negative_control_ref="control")
    # the claim rule, reading a store that holds both, counts nothing of them
    verdict = evaluate_claim(Held(record, passed, made=claim(subject="the proposed patch")), "claim")
    assert verdict["result"] != "supported", (
        "an unreadable binding was treated as absent and its evidence counted", verdict)
    assert m.BINDING_UNREADABLE in verdict["reasons"]
    assert m.BINDING_UNREADABLE in {code for code, _ in custody.about(record)[1]}
    led = ledger()
    with pytest.raises(LedgerError, match=m.BINDING_UNREADABLE):   # and the ledger refuses it at insertion
        led.add_evidence(record)


@pytest.mark.parametrize("text", MALFORMED, ids=["invalid-field", "repeated-key", "nan", "missing-field",
                                                  "extra-field", "cut-short", "never-closed"])
def test_each_binding_that_does_not_read_as_one_is_unreadable_never_none(text):
    found = custody.binding_of(evidence("e", text))
    assert isinstance(found, m.UnreadableBinding) and found.why
    parts, refused = custody.about(evidence("e", text))
    assert [code for code, _ in refused] == [m.BINDING_UNREADABLE] and parts.standard == "" and parts.predicates == ()
    with pytest.raises(ValueError, match="names no evaluator"):
        custody.evaluator_of(evidence("e", text))


@pytest.mark.parametrize("text", ["log:1", "synthetic:chk", "node:op:certificate:" + CODE, "[1, 2]", "42"])
def test_a_source_ref_that_is_not_a_json_object_holds_no_binding_and_is_read_as_declared(text):
    assert custody.binding_of(evidence("e", text)) is None
    parts, refused = custody.about(evidence("e", text))
    assert refused == () and parts.standard == STANDARD.standard_sha256 and parts.predicates == ("R1",)
    assert custody.evaluator_of(evidence("e", text)) == ("declared", "check@1")


def test_a_binding_that_reads_is_the_mapping_s_and_echa_reads_it_with_the_mapping_s_reader():
    text = canonical(binding().to_dict())
    assert custody.binding_of(evidence("e", text)) == m.read_binding(text) == binding()
    assert custody.about(evidence("e", text)) == m.read_about(binding(), scope="one input",
                                                              standard=STANDARD.standard_sha256, predicates=("R1",))
    assert custody.evaluator_of(evidence("e", text)) == ("sha256", CODE)


def test_the_mapping_refuses_an_echa_record_whose_binding_does_not_read_as_one():
    from g6.research.echa.view import source_record

    source = source_record(evidence("e", MALFORMED[0]))
    assert isinstance(source.binding, m.UnreadableBinding)
    assert m.BINDING_UNREADABLE in [code for code, _ in m.about(source)[1]]


def test_a_ledger_file_holding_an_unreadable_binding_is_reported_by_its_audit(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    led.add_standard(STANDARD)
    led.add_vault("fixture", FIXTURE, files=1, roots=1)
    led.add_evidence(evidence("fine", canonical(binding().to_dict())))
    from g6.core.ledger import ChainLedger

    raw = ChainLedger(path)   # written without ECHA's rules, as another writer could
    raw.append("evidence", "broken", evidence("broken", MALFORMED[0]).to_dict())
    assert any(m.BINDING_UNREADABLE in problem for problem in Ledger.audit(path))


# --- finding 4: a control is matched by the evaluator's bytes, never by its label -------------------------------------


def test_finding_4_a_control_for_other_evaluator_bytes_does_not_activate_a_bound_check():
    def run(check_code: str) -> str:
        control, passed = bound_pair(binding(request_id="control-request", artifact_sha256=DEFECT),
                                     binding(request_id="check-request", evaluator_sha256=check_code))
        led = ledger()
        try:
            led.add_evidence(control)
            led.add_evidence(passed)
        except ValueError:
            return "refused"
        led.add_claim(claim())
        return evaluate_claim(led, "claim")["result"]

    assert run(CODE) == "supported"   # a control on a defect, by this check's bytes
    assert run(OTHER_CODE) != "supported", "a control executed by another evaluator's bytes activated this check"


def test_the_claim_rule_itself_matches_a_control_by_bytes_on_a_store_that_holds_both():
    control, passed = bound_pair(binding(request_id="control-request", artifact_sha256=DEFECT),
                                 binding(request_id="check-request", evaluator_sha256=OTHER_CODE))
    verdict = evaluate_claim(Held(control, passed, made=claim()), "claim")
    assert verdict["result"] != "supported" and verdict["structural"] == ["pass"]
    same, kept = bound_pair(binding(request_id="control-request", artifact_sha256=DEFECT),
                            binding(request_id="check-request"))
    assert evaluate_claim(Held(same, kept, made=claim()), "claim")["result"] == "supported"   # positive control


def test_one_evaluator_s_bytes_under_two_labels_are_one_evaluator():
    control, passed = bound_pair(binding(request_id="control-request", artifact_sha256=DEFECT),
                                 binding(request_id="check-request"), oracle_version="check@2")
    assert evaluate_claim(Held(control, passed, made=claim()), "claim")["result"] == "supported"


def test_a_declared_label_never_matches_an_evaluator_named_by_its_bytes():
    control = evidence("control", "log:control", result="fail", purpose="negative_control",
                       oracle_version="sha256:" + CODE)
    passed = evidence("pass", canonical(binding().to_dict()), negative_control_ref="control",
                      oracle_version="sha256:" + CODE)
    verdict = evaluate_claim(Held(control, passed, made=claim()), "claim")
    assert verdict["result"] != "supported" and verdict["structural"] == ["pass"]
    with pytest.raises(LedgerError, match="negative_control_ref"):
        led = ledger()
        led.add_evidence(control)
        led.add_evidence(passed)


def test_independent_voices_are_counted_by_evaluator_bytes():
    def voices(*codes_and_labels: tuple[str, str]) -> int:
        records = []
        for i, (code, label) in enumerate(codes_and_labels):
            control, passed = bound_pair(binding(request_id=f"control-{i}", artifact_sha256=DEFECT,
                                                 evaluator_sha256=code),
                                         binding(request_id=f"check-{i}", evaluator_sha256=code),
                                         independence_group=f"group-{i}", oracle_version=label)
            records += [replace(control, evidence_id=f"control-{i}", oracle_version=label),
                        replace(passed, evidence_id=f"pass-{i}", negative_control_ref=f"control-{i}")]
        made = claim(evidence_ids=tuple(f"pass-{i}" for i in range(len(codes_and_labels))))
        return evaluate_claim(Held(*records, made=made), "claim")["independent_groups"]

    assert voices((CODE, "one"), (CODE, "two")) == 1        # two labels over one evaluator's bytes: one voice
    assert voices((CODE, "check@1"), (OTHER_CODE, "check@1")) == 2   # two evaluators under one label: two


# --- the inventory: every binding read and every identity comparison in research/echa, from the code ----------------

ECHA = Path(g6.__file__).resolve().parent / "research" / "echa"
#: The calls that read a binding or what a record is about.
READERS = frozenset({"binding_of", "read_binding", "about", "read_about", "bound_parts", "evaluator_of",
                     "strict_json", "_strict_json", "loads"})
#: The names an identity is compared by: the evaluator, the check and the artefact, and their digests.
IDENTITIES = frozenset({"oracle_version", "evaluator_sha256", "check_sha", "check_id", "artefact", "artifact_sha256",
                        "artifact_sha", "subject"})


def _named(node: ast.AST) -> set[str]:
    """The identities an expression names: an attribute, a name, a key or ``.get`` of one, or a call that gives one."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in IDENTITIES:
            found.add(sub.attr)
        elif isinstance(sub, ast.Name) and sub.id in IDENTITIES:
            found.add(sub.id)
        elif isinstance(sub, ast.Constant) and sub.value in IDENTITIES:
            found.add(str(sub.value))
        elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "evaluator_of":
            found.add("evaluator_of")
    return found


def scan(tree: ast.Module) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(function, call) for every binding read, and (function, expression) for every identity comparison: a comparison
    (``==``, ``!=``, ``in``, ...) or a set of distinct values that names an identity."""
    reads, compares = set(), set()

    def visit(node: ast.AST, where: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.ClassDef)):
                visit(child, [*where, child.name])
                continue
            here = ".".join(where) or "<module>"
            if isinstance(child, ast.Call):
                name = child.func.id if isinstance(child.func, ast.Name) else (
                    child.func.attr if isinstance(child.func, ast.Attribute) else "")
                if name in READERS:
                    reads.add((here, ast.unparse(child)))
            if isinstance(child, ast.Attribute) and child.attr == "source_ref" and isinstance(child.ctx, ast.Load):
                reads.add((here, ast.unparse(child)))
            if isinstance(child, (ast.Compare, ast.SetComp)) and _named(child):
                compares.add((here, ast.unparse(child)))
            visit(child, where)

    visit(tree, [])
    return reads, compares


def _found() -> tuple[set, set]:
    reads, compares = set(), set()
    for path in sorted(ECHA.glob("*.py")):
        r, c = scan(ast.parse(path.read_text(encoding="utf-8")))
        reads |= {(path.name, *one) for one in r}
        compares |= {(path.name, *one) for one in c}
    return reads, compares


THROUGH = "through the mapping's one reader (read_binding / read_about), so unreadable is refused, never none"
REFUSED = "its refusal codes are read: an unreadable binding is refused here"
AFTER = "on a record whose binding a reader already refused or accepted (its codes were read first)"
#: Every binding read in research/echa, with what it is.
BINDING_READS = {
    ("custody.py", "binding_of", "read_binding(rec.source_ref)"): THROUGH,
    ("custody.py", "binding_of", "rec.source_ref"): "the one place a record's source_ref is read for a binding: by read_binding",
    ("custody.py", "about", "binding_of(rec)"): THROUGH,
    ("records.py", "EvidenceRecord.__post_init__", "self.source_ref"): (
        "the record checks its own field is text when it is made; it reads no binding"),
    ("custody.py", "about", "read_about(binding_of(rec), scope=rec.scope, standard=rec.standard_sha256, "
                            "predicates=(rec.predicate_id,))"): THROUGH,
    ("custody.py", "evaluator_of", "about(rec)"): REFUSED,
    ("custody.py", "_decode", "_strict_json(raw.decode('utf-8'))"): (
        "an evaluator's receipt bytes, read by the one strict JSON rule; not a binding"),
    ("claims.py", "_control_holds", "about(control)"): REFUSED,
    ("claims.py", "_control_holds", "about(rec)"): AFTER,
    ("claims.py", "_control_holds", "evaluator_of(control)"): AFTER,
    ("claims.py", "_control_holds", "evaluator_of(rec)"): AFTER,
    ("claims.py", "evaluate_claim", "about(rec)"): REFUSED + "; its later read, for the limits, is of an active record",
    ("claims.py", "evaluate_claim", "evaluator_of(r)"): AFTER,
    ("claims.py", "evaluate_claim", "evaluator_of(rec)"): AFTER,
    ("ledger.py", "_semantic", "about(rec)"): REFUSED,
    ("ledger.py", "_semantic", "about(held)"): AFTER + ": the control, refused at its own insertion if unreadable",
    ("ledger.py", "_semantic", "evaluator_of(held)"): AFTER,
    ("ledger.py", "_semantic", "evaluator_of(rec)"): AFTER,
    ("view.py", "source_record", "about(rec)"): (
        "what it is about, handed on with its binding as read; the mapping refuses an unreadable one"),
    ("view.py", "source_record", "binding_of(rec)"): THROUGH,
    # slice 2's follow-up (review 4's finding 3): what one of the issuer's receipts is bound to, for the view
    ("custody.py", "receipt_about", "read_binding(canonical(held))"): THROUGH + ": a receipt's provenance binding",
    ("custody.py", "receipt_about", "read_about(binding, artefact=receipt.get('artifact_sha'))"): THROUGH,
}
BYTES = "compared by the digest the accessor gives (About, or evaluator_of)"
#: Every identity comparison in research/echa, with what it compares.
COMPARISONS = {
    ("claims.py", "_control_holds", "evaluator_of(control) == evaluator_of(rec)"): BYTES,
    ("claims.py", "evaluate_claim", "bound != subject"): BYTES + ": the record's artefact and the claim's subject",
    ("claims.py", "evaluate_claim", "subject is not None"): "whether the claim names an artefact digest",
    ("claims.py", "evaluate_claim", "{evaluator_of(r) for r in passing if r.independent}"): BYTES + ": distinct ones",
    ("ledger.py", "_semantic", "evaluator_of(held) != evaluator_of(rec)"): BYTES,
    ("view.py", "_bound", "parts.artefact == artifact_sha256"): (
        BYTES + ": the receipt's bound artefact (custody.receipt_about) and the subject's digest"),
    ("view.py", "_view", "{e['check_id'] for e in evidence}"): "the issuer's own check ids, from its certificate",
    ("view.py", "certificate_view", "body.get('artifact_sha') != artifact_sha256"): (
        "the certificate's artefact digest against the subject's digest"),
    ("competence.py", "_exclusion", "record.header.get('oracle_version') not in policy.admissible_oracles"): (
        "an episode header's oracle: episodes wait for slice B, unchanged, and competence is retired for selection"),
}
FOUND_READS, FOUND_COMPARES = _found()


def test_every_binding_read_in_echa_is_listed_and_every_listed_one_is_still_there():
    assert sorted(FOUND_READS - set(BINDING_READS)) == [], "an unlisted binding read: read it through the accessor"
    assert sorted(set(BINDING_READS) - FOUND_READS) == [], "listed reads the scan no longer finds: remove them"


def test_every_identity_comparison_in_echa_is_listed_and_none_compares_a_label():
    assert sorted(FOUND_COMPARES - set(COMPARISONS)) == [], "an unlisted identity comparison"
    assert sorted(set(COMPARISONS) - FOUND_COMPARES) == [], "listed comparisons the scan no longer finds: remove them"
    labels = {key for key in COMPARISONS if "oracle_version" in key[2] and key[0] != "competence.py"}
    assert labels == set(), "an evaluator compared by its label"


def test_the_scan_finds_a_planted_label_comparison_and_a_planted_binding_read():
    planted = ast.parse("def holds(control, rec):\n"
                        "    found = json.loads(rec.source_ref)\n"
                        "    return control.oracle_version == rec.oracle_version\n")
    reads, compares = scan(planted)
    assert ("holds", "json.loads(rec.source_ref)") in reads and ("holds", "rec.source_ref") in reads
    assert compares == {("holds", "control.oracle_version == rec.oracle_version")}
