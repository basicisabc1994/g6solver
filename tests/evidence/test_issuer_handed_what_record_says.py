# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What the issuer is handed is what the record says, and one id has one body wherever it appears (G6 0.3, slice 2,
repair 2; review 2's findings 1 and 7; class ``guard_covers_less_than_it_guards``, met in repair 1).

- two controls with one id and two bodies inside one source record are refused, never collapsed to the last body
  (finding 1);
- a record's stated artefact position (and producer) must equal the artefact the issuer judges it against, or the
  record is refused with the code that names the part (finding 7);
- the inventory, built from the code, lists every value the mapping passes to ``issue`` and where it came from, and
  fails on any value taken from a second source without a check; it plants a duplicate of each id kind inside one record
  and across two, and across two calls.
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import fields, replace

import pytest

from g6.core.ledger import ChainLedger, LedgerError
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt
from g6.evidence import mapping as m

A, OTHER, STANDARD, CODE, ENV, FIXTURE = "a" * 64, "b" * 64, "5" * 64, "c" * 64, "e" * 64, "f" * 64
SUBJECT = Artifact(A, "agent", 1)


def facts(*predicates: str) -> MissionFacts:
    named = predicates or ("R1",)
    return MissionFacts("1" * 64, STANDARD, STANDARD, named, named)


def control(**over) -> Control:
    return Control(**{"control_id": "control", "check_sha": CODE, "predicate": "R1", "generated_by": "g6", "seq": 2,
                      "killed": True, **over})


def plain(**over) -> m.SourceRecord:
    base = {"kind": "independent_executable", "record_id": "run", "predicates": ("R1",), "result": "pass",
            "artefact_sha256": A, "producer": "agent", "artefact_seq": 1, "standard_sha256": STANDARD,
            "check_id": "check", "check_sha256": CODE, "author": "reviewer", "frozen_seq": 0,
            "environment_sha256": ENV, "seq": 3, "scope": "one input", "controls": (control(),)}
    return m.SourceRecord(**{**base, **over})


def codes_of(issued: m.Issued) -> list[str]:
    return [code for _, found in issued.refused for code, _ in found]


# --- review 2's findings, brought in as this change's own ------------------------------------------------------------


def test_finding_1_two_controls_with_one_id_inside_one_record_are_refused_never_collapsed():
    uncaught = control(killed=False)
    killed = replace(uncaught, killed=True)
    assert m.issue_records(facts(), SUBJECT, [plain(controls=(uncaught,))]).certificate.verified_predicates == ()
    assert m.issue_records(facts(), SUBJECT, [plain(controls=(killed,))]).certificate.verified_predicates == ("R1",)
    issued = m.issue_records(facts(), SUBJECT, [plain(controls=(uncaught, killed))])
    assert issued.certificate.verified_predicates == (), (
        "conflicting controls with one id were collapsed to the last, killed=True body", issued.refused)
    assert codes_of(issued) == [m.CONFLICTING] and issued.promoted == ()
    with pytest.raises(KeyError):   # nothing of it reached the issuer's ledger
        issued.ledger.get("control", "control")


def test_finding_7_a_record_s_artefact_position_must_be_the_artefact_s_own():
    assert m.issue_records(facts(), SUBJECT, [plain()]).certificate.verified_predicates == ("R1",)
    inconsistent = plain(artefact_seq=4)   # the receipt at 3 on an artefact the record says only existed at 4
    issued = m.issue_records(facts(), SUBJECT, [inconsistent])
    assert issued.certificate.verified_predicates == (), (
        "issuance silently substituted Artifact.seq for the contradictory position in the source record",
        issued.refused)
    assert codes_of(issued) == [m.OTHER_ORDER]


def test_the_same_control_twice_inside_one_record_is_one_control():
    issued = m.issue_records(facts(), SUBJECT, [plain(controls=(control(), control()))])
    assert issued.refused == () and issued.certificate.verified_predicates == ("R1",)


@pytest.mark.parametrize(("issuer_side", "over"), [
    ("artifact", {"artefact_sha256": OTHER}),
    ("artifact", {"producer": "someone else"}),
    ("artifact", {"artefact_seq": 0}),
    ("artifact", {"artefact_seq": 4}),
    ("facts", {"standard_sha256": "6" * 64}),
    ("facts", {"predicates": ("R9",), "controls": (control(predicate="R9"),)}),
])
def test_each_value_the_issuer_is_handed_beside_a_record_is_checked_against_the_record(issuer_side, over):
    rec = plain(**over)
    issued = m.issue_records(facts(), SUBJECT, [rec])
    assert issued.promoted == () and issued.certificate.verified_predicates == ()
    field_name = next(iter(over))
    expected = next(code for side, _, record_field, code in m.ISSUER_SIDE
                    if side == issuer_side and record_field == field_name)
    assert codes_of(issued) == [expected]


def test_a_producer_the_record_names_otherwise_is_refused_whichever_way_it_would_have_gone():
    # the record says its check is independent of the producer it names; the artefact's producer wrote it
    hidden = plain(author="agent", producer="someone else")
    assert codes_of(m.issue_records(facts(), SUBJECT, [hidden])) == [m.OTHER_PRODUCER]
    # and the other way: the mapping would call it self-authored by a producer the artefact does not have
    other = plain(author="someone else", producer="someone else")
    assert codes_of(m.issue_records(facts(), SUBJECT, [other])) == [m.OTHER_PRODUCER]


# --- one id, one body: each id kind, inside one record, across two, and across calls --------------------------------


def test_a_control_id_given_another_body_by_another_record_is_refused_even_when_no_check_rests_on_it():
    beside = control(control_id="beside", check_sha="9" * 64)   # a control for other code: the check does not use it
    first = plain(record_id="r1", controls=(control(), beside))
    second = plain(record_id="r2", result="pass", controls=(control(), replace(beside, killed=False)))
    issued = m.issue_records(facts(), SUBJECT, [first, second])
    assert [rid for rid, _ in issued.refused] == ["r2"] and codes_of(issued) == [m.CONFLICTING]


@pytest.mark.parametrize("kind", ["record", "control", "check"])
def test_each_id_kind_with_two_bodies_across_two_records_is_refused_and_the_first_stands(kind):
    first = plain(record_id="r1")
    second = {"record": replace(first, scope="all inputs"),
              "control": plain(record_id="r2", controls=(control(seq=1),)),
              "check": plain(record_id="r2", check_sha256="9" * 64, controls=(control(control_id="k2",
                                                                                      check_sha="9" * 64),))}[kind]
    issued = m.issue_records(facts(), SUBJECT, [first, second])
    assert codes_of(issued) == [m.CONFLICTING], issued.refused
    assert issued.certificate.verified_predicates == ("R1",)   # the first record stands


@pytest.mark.parametrize("kind", ["control", "check"])
def test_each_id_kind_with_two_bodies_inside_one_record_is_refused(kind):
    # a record states one record id and one check id; a second body for its check id inside it can only come from
    # its custody binding, and a control id is the one it can repeat itself
    if kind == "control":
        rec = plain(controls=(control(), control(killed=False)))
        expected = m.CONFLICTING
    else:
        binding = m.CheckBinding("request", "task", "R1", A, STANDARD, FIXTURE, CODE)
        bound = m.SourceRecord.from_binding(binding, kind="independent_executable", record_id="run", result="pass",
                                            producer="agent", artefact_seq=1, author="reviewer", frozen_seq=0,
                                            environment_sha256=ENV, seq=3, scope="one input", controls=(control(),))
        rec = replace(bound, check_id="check")   # its field gives the bound check another id
        expected = m.BINDING_CHECK
    issued = m.issue_records(facts(), SUBJECT, [rec])
    assert issued.promoted == () and codes_of(issued) == [expected]


def test_ids_from_two_calls_meet_only_in_a_ledger_both_are_written_to_and_it_refuses_the_second_body(tmp_path):
    one = m.issue_records(facts(), SUBJECT, [plain()])
    two = m.issue_records(facts(), SUBJECT, [plain(controls=(control(seq=1),))])
    assert one.ledger.get("control", "control")["seq"] == 2 and two.ledger.get("control", "control")["seq"] == 1
    kept = ChainLedger(tmp_path / "ledger.jsonl")
    for entry in one.ledger.entries():
        kept.append(entry["kind"], entry["id"], entry["body"])
    with pytest.raises(LedgerError, match="already"):
        for entry in two.ledger.entries():
            kept.append(entry["kind"], entry["id"], entry["body"])


# --- the inventory: every value the mapping passes to the issuer, and where it came from, from the code ---------------

#: The mission's own facts, which no record states, so there is nothing of a record's to check them against.
MISSION_OWN = {
    "mission_sha": "the mission's own hash",
    "mission_standard_sha": "the hash the mission cites; the issuer refuses it unless it is the standard's",
    "standard_predicates": "the frozen standard's predicates; the issuer refuses them unless they are the mission's",
    "gates": "the mission's human gates",
    "violations": "the soundness obligations the mission breaks",
    "cost_cents": "the mission's cost bound",
    "catalogue_sha": "the catalogue the mission was judged against",
}
#: Where each argument of the issuer's records may come from in ``promote``: the record's own statement only.
RECORD_ROOTS = {"parts": "the record's statement, through the accessor (about)",
                "rec": "the record's own field",
                "controls": "the record's controls (_controls(rec)) for this check and its predicates",
                "issuer_kind": "the mapping table's row, with the record's own author and producer",
                "int": "a conversion of the record's own position", "tuple": "a conversion of the record's own value"}


def _function(name: str) -> ast.FunctionDef:
    return next(node for node in ast.parse(inspect.getsource(m)).body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _arguments(call: ast.Call, cls: type) -> dict[str, ast.expr]:
    names = [f.name for f in fields(cls)]
    return {**dict(zip(names, call.args)), **{k.arg: k.value for k in call.keywords}}


def handed() -> dict[str, tuple[str, ...]]:
    """Every field of the issuer's ``Check`` and ``Receipt`` that ``promote`` builds, with the names its value is
    built from."""
    found = {}
    for node in ast.walk(_function("promote")):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("Check", "Receipt"):
            cls = {"Check": Check, "Receipt": Receipt}[node.func.id]
            for name, value in _arguments(node, cls).items():
                found[f"{node.func.id}.{name}"] = tuple(sorted({n.id for n in ast.walk(value)
                                                                if isinstance(n, ast.Name)}))
    return found


def beside() -> list[tuple[str, int]]:
    """The arguments ``issue_records`` passes to ``issue`` beside the records it promoted: (name, position)."""
    call = next(node for node in ast.walk(_function("issue_records")) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "issue")
    return [(arg.id, i) for i, arg in enumerate(call.args) if isinstance(arg, ast.Name)]


def test_every_field_of_the_issuer_s_check_and_receipt_is_read_from_the_record_s_own_statement():
    found = handed()
    assert set(found) == {f"Check.{f.name}" for f in fields(Check)} | {f"Receipt.{f.name}" for f in fields(Receipt)}
    second = {field: names for field, names in found.items() if not set(names) <= set(RECORD_ROOTS)}
    assert second == {}, "a value handed to the issuer from a second source: read it from the record, or check it"
    # the controls promote hands on are the record's, filtered by what the record is about
    assign = next(node for node in ast.walk(_function("promote")) if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "controls")
    assert {n.id for n in ast.walk(assign.value) if isinstance(n, ast.Name)} <= {"c", "_controls", "rec", "parts",
                                                                                   "tuple"}


def test_every_input_issue_is_handed_beside_the_records_is_checked_against_each_record_that_states_it():
    assert beside() == [("facts", 0), ("artifact", 1)]   # the mission and the artefact, as issue_records got them
    promote_call = next(node for node in ast.walk(_function("issue_records")) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "promote")
    assert {k.arg: ast.unparse(k.value) for k in promote_call.keywords} == {"facts": "facts", "artifact": "artifact"}
    stated = {(side, name) for side, name, _, _ in m.ISSUER_SIDE}
    assert {("artifact", f.name) for f in fields(Artifact)} <= stated, "an artefact field no record is checked on"
    facts_fields = {f.name for f in fields(MissionFacts)}
    assert facts_fields == {name for side, name in stated if side == "facts"} | set(MISSION_OWN), (
        "a mission fact neither checked against the record that states it nor the mission's own")
    record_fields = {f.name for f in fields(m.SourceRecord)}
    assert {record_field for _, _, record_field, _ in m.ISSUER_SIDE} <= record_fields


def test_problems_compares_each_issuer_side_value_with_the_record_s():
    other = {"sha256": OTHER, "producer": "someone else", "seq": 5, "standard_sha": "6" * 64, "predicates": ("R9",)}
    assert m.problems(plain(), facts=facts(), artifact=SUBJECT) == []   # the positive control
    for side, name, record_field, code in m.ISSUER_SIDE:
        subject, mission = SUBJECT, facts()
        if side == "artifact":
            subject = replace(SUBJECT, **{name: other[name]})
        else:
            mission = replace(mission, **{name: other[name]}, **({"mission_standard_sha": other[name]}
                                                                 if name == "standard_sha" else
                                                                 {"standard_predicates": other[name]}))
        found = [c for c, _ in m.problems(plain(), facts=mission, artifact=subject)]
        assert found == [code], (side, name, record_field, found)


def test_the_scan_finds_a_value_planted_from_a_second_source():
    # survival control: a promote that took the artefact's position from the subject would be found
    planted = ast.parse("def promote(rec, *, facts, artifact):\n"
                        "    parts = 1\n"
                        "    return Receipt(rec.record_id, parts.check_id, parts.check_sha, artifact.sha256, "
                        "rec.result, artifact.seq)\n")
    call = next(node for node in ast.walk(planted) if isinstance(node, ast.Call) and getattr(node.func, "id", "")
                == "Receipt")
    names = {name: {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
             for name, value in _arguments(call, Receipt).items()}
    assert {name for name, roots in names.items() if not roots <= set(RECORD_ROOTS)} == {"artifact_sha", "seq"}
