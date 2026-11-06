# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The mapping table (G6 0.3 design, section 3.1; slice 2, item 2), fixed before anything converts records.

- Every evidence kind the code produces has exactly one row, and every row is a kind the code produces: the issuer's
  kinds, ECHA's oracle categories, the patch review's check provenance, a person's confirmed expectation, and every kind
  a ``Check`` is built with anywhere in ``src/g6``.
- Each row fixes every part: the artefact, model, standard, check and environment, the negative control, the author's
  independence, the ordering and scope, and how conflicting evidence is kept.
- A record that lacks a part is refused promotion with the stable code that names the part, and only what the mapping
  promotes reaches the issuer; conflicting records are kept apart, never merged.
- The page in the design's docs folder is the table, rendered from the code.
"""
from __future__ import annotations

import ast
import typing
from dataclasses import replace
from pathlib import Path

import pytest

import g6
from g6.evidence import Artifact, Control, MissionFacts, verify
from g6.evidence import certificate as issuer
from g6.evidence import mapping as m
from g6.harness import patch_review
from g6.harness.confirmed_expectation import PERSON_CONFIRMED
from g6.research.echa.records import ORACLE_CATEGORIES

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "docs" / "superpowers" / "specs" / "2026-11-06-evidence-mapping-table.md"
A, STANDARD, CODE, ENV, CERT, MODEL = "a" * 64, "5" * 64, "c" * 64, "f" * 64, "d" * 64, "e" * 64
P = "p"


_ISSUER_MODULES = ("g6.evidence", "g6.evidence.certificate", "g6.research.mission_bridge.csir.certificate")


def _issuer_checks(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names a module binds to the issuer's ``Check`` class, and to a module that holds it."""
    classes, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _ISSUER_MODULES:
            classes |= {alias.asname or alias.name for alias in node.names if alias.name == "Check"}
            if node.module == "g6.evidence":
                modules |= {alias.asname or alias.name for alias in node.names if alias.name == "certificate"}
        elif isinstance(node, ast.Import):
            modules |= {alias.asname for alias in node.names if alias.name in _ISSUER_MODULES and alias.asname}
    return classes, modules


def _kinds_in(tree: ast.Module) -> set[str]:
    """Every text the issuer's ``Check`` is built with as its kind in one module (keyword ``kind`` or the fourth
    positional argument), wherever the module imports that class (under any name) or its module."""
    classes, modules = _issuer_checks(tree)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not ((isinstance(func, ast.Name) and func.id in classes) or (
                isinstance(func, ast.Attribute) and func.attr == "Check" and isinstance(func.value, ast.Name)
                and func.value.id in modules)):
            continue
        given = [k.value for k in node.keywords if k.arg == "kind"] + node.args[3:4]
        found |= {g.value for g in given if isinstance(g, ast.Constant) and isinstance(g.value, str)}
    return found


def _check_kinds_in_code() -> set[str]:
    return {kind for path in Path(g6.__file__).resolve().parent.rglob("*.py")
            for kind in _kinds_in(ast.parse(path.read_text(encoding="utf-8")))}


def kinds_in_code() -> set[str]:
    return {*issuer.EVIDENCE_KINDS, *ORACLE_CATEGORIES, *typing.get_args(patch_review.CheckProvenance),
            PERSON_CONFIRMED, *_check_kinds_in_code()}


def test_every_evidence_kind_in_the_code_has_one_row_and_every_row_is_a_kind_in_the_code():
    assert sorted(kinds_in_code() - set(m.MAPPING)) == []
    assert sorted(set(m.MAPPING) - kinds_in_code()) == []
    assert all(key == row.kind for key, row in m.MAPPING.items())


def test_the_kinds_a_check_is_built_with_are_found_so_a_new_one_would_fail_the_test_above():
    # survival control: the scan reads both forms of a Check's kind, under any name, and only the issuer's Check
    planted = ast.parse("from g6.evidence import Check as C\nfrom g6.evidence import certificate as cert\n"
                        "from g6.intent_engine.checks import Check\n"
                        "C('c', ('p',), 'a', 'a_new_kind', 's', 0)\ncert.Check(kind='another_kind')\n"
                        "Check(kind='contains')\n")
    assert _kinds_in(planted) == {"a_new_kind", "another_kind"}
    assert "independent_executable" in _check_kinds_in_code()   # the confirmed expectation's check


@pytest.mark.parametrize("kind", sorted(m.MAPPING))
def test_each_row_fixes_every_part(kind):
    row = m.MAPPING[kind]
    for part in ("artefact", "model", "standard", "check", "environment", "negative_control", "independence",
                 "ordering", "conflicts"):
        assert str(getattr(row, part)).strip(), (kind, part)
    assert row.source in ("issuer", "echa", "patch_review")
    assert row.issuer_kind in ("", *issuer.EVIDENCE_KINDS)
    assert row.issuer_kind not in ("process", "heuristic_review", "no_evidence")   # a lead is never promoted


def test_only_kinds_that_can_be_shown_to_fail_are_promoted():
    promoted = {kind for kind, row in m.MAPPING.items() if row.issuer_kind}
    assert promoted == {"independent_executable", "formal_checked", "partial_falsifier", "FORMAL_CHECK",
                        "INDEPENDENT_EXECUTABLE", "PARTIAL_FALSIFIER", "stated", "person_confirmed"}


def facts() -> MissionFacts:
    return MissionFacts(mission_sha="1" * 64, mission_standard_sha=STANDARD, standard_sha=STANDARD, predicates=(P,),
                        standard_predicates=(P,))


def control(**over) -> Control:
    return Control(**{"control_id": "k1", "check_sha": CODE, "predicate": P, "generated_by": "g6", "seq": 2,
                      "killed": True, **over})


def rec(**over) -> m.SourceRecord:
    base = {"kind": "independent_executable", "record_id": "r1", "predicates": (P,), "result": "pass",
            "artefact_sha256": A, "producer": "agent", "artefact_seq": 1, "standard_sha256": STANDARD,
            "check_id": "c1", "check_sha256": CODE, "author": "evaluator", "frozen_seq": 0,
            "environment_sha256": ENV, "seq": 3, "scope": "the inputs it ran", "controls": (control(),)}
    return m.SourceRecord(**{**base, **over})


def codes(record: m.SourceRecord) -> list[str]:
    return [code for code, _ in m.problems(record, artefact_sha256=A, standard_sha256=STANDARD, predicates=(P,))]


def test_a_complete_record_is_promoted_and_the_issuer_counts_it():
    assert codes(rec()) == []   # the positive control for every refusal below
    out = m.issue_records(facts(), Artifact(A, "agent", 1), [rec()])
    assert out.refused == () and out.promoted == (("r1", "independent_executable", "independent_executable"),)
    assert out.certificate.verified_predicates == (P,)


@pytest.mark.parametrize(("over", "code"), [
    ({"kind": "a_kind_nobody_mapped"}, m.UNMAPPED),
    ({"kind": "heuristic_review"}, m.NOT_PROMOTED),
    ({"kind": "proposed"}, m.NOT_PROMOTED),
    ({"artefact_sha256": ""}, m.NO_ARTEFACT),
    ({"artefact_sha256": "b" * 64}, m.OTHER_ARTEFACT),
    ({"standard_sha256": ""}, m.NO_STANDARD),
    ({"standard_sha256": "6" * 64}, m.OTHER_STANDARD),
    ({"predicates": ("elsewhere",), "controls": (control(predicate="elsewhere"),)}, m.OTHER_STANDARD),
    ({"check_sha256": "not a digest", "controls": (control(check_sha="not a digest"),)}, m.NO_CHECK),
    ({"check_id": " "}, m.NO_CHECK),
    ({"environment_sha256": ""}, m.NO_ENVIRONMENT),
    ({"controls": ()}, m.NO_CONTROL),
    ({"controls": (control(check_sha="0" * 64),)}, m.NO_CONTROL),
    ({"controls": (control(predicate="other"),)}, m.NO_CONTROL),
    ({"author": ""}, m.NO_INDEPENDENCE),
    ({"producer": " "}, m.NO_INDEPENDENCE),
    ({"seq": None}, m.NO_ORDER),
    ({"frozen_seq": -1}, m.NO_ORDER),
    ({"artefact_seq": True}, m.NO_ORDER),
    ({"scope": ""}, m.NO_SCOPE),
    ({"result": "verified"}, m.NO_RESULT),
])
def test_a_record_that_lacks_a_part_is_refused_with_the_code_that_names_it(over, code):
    assert codes(rec(**over)) == [code]
    with pytest.raises(m.PromotionRefused) as refused:
        m.promote(rec(**over), artefact_sha256=A, standard_sha256=STANDARD, predicates=(P,))
    assert [c for c, _ in refused.value.codes] == [code] and code in str(refused.value)
    out = m.issue_records(facts(), Artifact(A, "agent", 1), [rec(**over)])
    assert out.promoted == () and [c for c, _ in out.refused[0][1]] == [code]
    assert out.certificate.verified_predicates == ()


def test_a_formal_record_names_its_model_and_its_certificate():
    formal = rec(kind="formal_checked", model_sha256=MODEL, certificate=CERT)
    assert codes(formal) == []
    assert codes(replace(formal, model_sha256="")) == [m.NO_MODEL]
    assert codes(replace(formal, certificate="")) == [m.NO_CHECK]


def test_every_refusal_code_is_stable_and_named():
    assert len(set(m.CODES)) == len(m.CODES) and all(code.startswith("G6_E_EVIDENCE_") for code in m.CODES)


def test_the_issuer_kind_follows_the_author_whatever_the_source_calls_it():
    own = rec(author="agent")   # the artefact's producer wrote the check
    assert m.issuer_kind(own) == "partial_falsifier"
    out = m.issue_records(facts(), Artifact(A, "agent", 1), [own])
    assert out.promoted[0][2] == "partial_falsifier" and out.certificate.verified_predicates == ()


def test_conflicting_records_are_kept_apart_never_merged_and_a_conflict_stays_a_fourth_state():
    clash = rec(record_id="r2", check_sha256="9" * 64, controls=(control(check_sha="9" * 64),))   # same check id
    out = m.issue_records(facts(), Artifact(A, "agent", 1), [rec(), clash])
    assert [rid for rid, _ in out.refused] == ["r2"] and out.refused[0][1][0][0] == m.CONFLICTING
    twin = rec(record_id="r3", check_id="c2", controls=(control(control_id="k1", seq=1),))   # one control, two bodies
    assert [rid for rid, _ in m.issue_records(facts(), Artifact(A, "agent", 1), [rec(), twin]).refused] == ["r3"]
    # one check supports, another refutes (it was shown able to fail and to pass a known-good reference first)
    fails = rec(record_id="r4", check_id="c4", check_sha256="8" * 64, result="fail", positive_seq=2,
                controls=(control(control_id="k4", check_sha="8" * 64),))
    both = m.issue_records(facts(), Artifact(A, "agent", 1), [rec(), fails])
    statement = both.certificate.predicates[0]
    assert statement.support and statement.refute and both.certificate.state == "blocked-escalated"


def test_what_the_mapping_promotes_rests_on_g6_s_ledger_and_is_decided_again_from_it():
    out = m.issue_records(facts(), Artifact(A, "agent", 1), [rec()], extra={"run": "unit"})
    kinds = [entry["kind"] for entry in out.ledger.entries()]
    assert kinds == ["control", "receipt", m.PROVENANCE_KIND]
    kept = out.ledger.get(m.PROVENANCE_KIND, "r1")
    assert kept["environment_sha256"] == ENV and kept["scope"] == "the inputs it ran" and kept["run"] == "unit"
    assert issuer.verify(out.certificate.as_dict(), b"", ledger=out.ledger) == [
        "the artifact bytes are not the ones the certificate was issued for"]


def test_a_custody_binding_gives_a_receipt_its_artefact_standard_and_check():
    binding = m.CheckBinding(request_id="r-1", task_id="t-1", predicate_id=P, artifact_sha256=A,
                             standard_sha256=STANDARD, fixture_sha256="b" * 64, evaluator_sha256=CODE)
    bound = m.SourceRecord.from_binding(binding, kind="independent_executable", record_id="r1", result="pass",
                                        producer="agent", artefact_seq=1, author="evaluator", frozen_seq=0,
                                        environment_sha256=ENV, seq=3, scope="the bound predicate",
                                        controls=(control(),))
    assert (bound.artefact_sha256, bound.standard_sha256, bound.check_sha256, bound.predicates) == (A, STANDARD, CODE,
                                                                                                   (P,))
    out = m.issue_records(facts(), Artifact(A, "agent", 1), [bound])
    assert out.certificate.verified_predicates == (P,)
    assert out.ledger.get(m.PROVENANCE_KIND, "r1")["binding"] == binding.to_dict()
    with pytest.raises(ValueError):
        replace(binding, artifact_sha256="not a digest")


def test_verify_needs_the_bytes_the_records_were_taken_on():
    data = b"the artefact"
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    out = m.issue_records(facts(), Artifact(sha, "agent", 1), [rec(artefact_sha256=sha)])
    assert verify(out.certificate.as_dict(), data, ledger=out.ledger) == []


def test_the_page_in_the_design_s_docs_folder_is_the_table_rendered_from_the_code():
    assert PAGE.read_text(encoding="utf-8") == m.render_markdown()
    for row in m.MAPPING.values():
        assert f"| {row.kind} |" in m.render_markdown()
