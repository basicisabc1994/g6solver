# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""An id the mapping makes never collides with one a record states (G6 0.3, review 1 of slice 2's follow-up, finding 3;
class ``guard_covers_less_than_it_guards``, met before).

``refuse`` made a caveat's id without checking it against the accepted check and receipt ids, so a record that named
its check or itself as a caveat is named (``["caveat",0,"failed-run"]``) stopped issuance with a duplicate id. Now each
caveat's id is checked, once every record is read, against every accepted check and receipt id, and made anew from
itself and a count where one has it (``mapping._fresh``): issuance returns the refusal and the caveat, whatever a record
names itself. The inventory lists, from the code, every place ``mapping``
makes an id or a digest, each checked against the accepted ids, accepted itself, or no id at all.

Development fixtures only: no model runs, and no test runs.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import g6
from g6.evidence import Artifact, Control, MissionFacts
from g6.evidence import mapping as m

FACTS = MissionFacts("1" * 64, "5" * 64, "5" * 64, ("R1",), ("R1",))
SUBJECT = Artifact("a" * 64, "agent", 1)
COMMON = dict(kind="independent_executable", predicates=("R1",), artefact_sha256="a" * 64, producer="agent",
              artefact_seq=1, standard_sha256="5" * 64, check_sha256="c" * 64, author="reviewer", frozen_seq=0,
              environment_sha256="e" * 64, seq=3, scope="one input")
CONTROL = Control("control", "c" * 64, "R1", "g6", 2, True)


def record(record_id: str, check_id: str, result: str = "pass", **over) -> m.SourceRecord:
    return m.SourceRecord(record_id=record_id, check_id=check_id, result=result,
                          **{**COMMON, "controls": (CONTROL,) if result == "pass" else (), **over})


# --- review 1's finding 3, brought in as this change's own ------------------------------------------------------------


@pytest.mark.parametrize("field", ["check_id", "record_id"])
def test_finding_3_a_promoted_check_cannot_collide_with_the_generated_caveat_id(field):
    facts = MissionFacts("1" * 64, "5" * 64, "5" * 64, ("R1",), ("R1",))
    artifact = Artifact("a" * 64, "agent", 1)
    caveat_id = m.id_of("caveat", 0, "failed-run")
    common = dict(kind="independent_executable", predicates=("R1",), artefact_sha256="a" * 64,
                  producer="agent", artefact_seq=1, standard_sha256="5" * 64, check_sha256="c" * 64,
                  author="reviewer", frozen_seq=0, environment_sha256="e" * 64, seq=3, scope="one input")
    good = m.SourceRecord(record_id=caveat_id if field == "record_id" else "good-run",
                          check_id=caveat_id if field == "check_id" else "good-check", result="pass",
                          controls=(Control("control", "c" * 64, "R1", "g6", 2, True),), **common)
    bad = m.SourceRecord(record_id="failed-run", check_id="failed-check", result="fail", **common)
    assert m.issue_records(facts, artifact, [good]).certificate.verified_predicates == ("R1",)
    try:
        got = m.issue_records(facts, artifact, [good, bad])
    except ValueError as exc:
        raise AssertionError("a generated caveat ID collided with an accepted source ID, aborting issuance") from exc
    assert got.refused and got.caveats
    assert got.certificate.predicates[0].caveats


# --- plants: every accepted id a caveat's id could take, in either order, and many at once ----------------------------


def ids(out: m.Issued) -> tuple[list[str], list[str]]:
    body = out.certificate.as_dict()
    return [c["check_id"] for c in body["checks"]], [r["receipt_id"] for r in body["receipts"]]


def caveat_ids(n: int, record_id: str, tries: int = 3) -> list[str]:
    """The ids the ``n``-th caveat, of ``record_id``, would be given, in the order ``_fresh`` tries them."""
    first = m.id_of("caveat", n, record_id)
    return [first, *(m.id_of(first, k) for k in range(1, tries))]


@pytest.mark.parametrize("order", ["accepted-first", "refused-first"])
@pytest.mark.parametrize("field", ["check_id", "record_id", "both"])
def test_an_accepted_id_spelled_as_a_caveat_s_id_never_stops_issuance(order, field):
    taken = caveat_ids(0, "failed-run")[0]
    good = record(taken if field in ("record_id", "both") else "good-run",
                  taken if field in ("check_id", "both") else "good-check")
    bad = record("failed-run", "failed-check", "fail")
    out = m.issue_records(FACTS, SUBJECT, [good, bad] if order == "accepted-first" else [bad, good])
    checks, receipts = ids(out)
    assert len(checks) == len(set(checks)) and len(receipts) == len(set(receipts))
    (rid, caveat), = out.caveats
    assert rid == "failed-run" and caveat not in {good.check_id, good.record_id}
    assert out.certificate.predicates[0].caveats == (caveat,) and out.certificate.predicates[0].support


def test_a_caveat_skips_every_accepted_id_it_would_try_and_two_caveats_never_share_one():
    tried = caveat_ids(0, "failed-run") + caveat_ids(1, "other-run")
    good = [record(f"good-{n}", taken, seq=3 + n) for n, taken in enumerate(tried)]
    bad = [record("failed-run", "failed-check", "fail", seq=9), record("other-run", "other-check", "fail", seq=10)]
    out = m.issue_records(FACTS, SUBJECT, [*good, *bad])
    checks, receipts = ids(out)
    assert len(checks) == len(set(checks)) and len(receipts) == len(set(receipts))
    made = [cid for _, cid in out.caveats]
    assert len(made) == 2 and len(set(made)) == 2 and not set(made) & set(tried)
    assert made[0] == m.id_of(m.id_of("caveat", 0, "failed-run"), 3)   # the first it tried that nothing had taken


def test_with_nothing_taken_a_caveat_keeps_the_id_it_always_had():
    out = m.issue_records(FACTS, SUBJECT, [record("good-run", "good-check"), record("failed-run", "x", "fail")])
    assert out.caveats == (("failed-run", m.id_of("caveat", 0, "failed-run")),)


def test_the_caveat_and_its_ledger_entries_are_written_under_the_id_it_was_given():
    taken = caveat_ids(0, "failed-run")[0]
    out = m.issue_records(FACTS, SUBJECT, [record("good-run", taken), record("failed-run", "failed-check", "fail")])
    (_, caveat), = out.caveats
    assert out.ledger.get(m.PROVENANCE_KIND, caveat)["kept_as"] == "caveat"
    assert out.ledger.get("receipt", caveat)["result"] == "fail"
    assert [r.check_id for r in out.certificate.receipts if r.receipt_id == caveat] == [caveat]


def test_a_bound_record_s_derived_check_id_stated_by_another_record_is_refused_never_an_abort():
    binding = m.CheckBinding(request_id="req", task_id="task", predicate_id="R1", artifact_sha256="a" * 64,
                             standard_sha256="5" * 64, fixture_sha256="f" * 64, evaluator_sha256="c" * 64)
    bound = m.SourceRecord.from_binding(binding, kind="independent_executable", record_id="bound", result="pass",
                                        producer="agent", artefact_seq=1, author="reviewer", frozen_seq=0,
                                        environment_sha256="e" * 64, seq=3, scope="one input", controls=(CONTROL,))
    other = record("unbound", m.check_id_of(binding), author="someone else", seq=4)
    out = m.issue_records(FACTS, SUBJECT, [bound, other])
    assert [code for rid, found in out.refused for code, _ in found if rid == "unbound"] == [m.CONFLICTING]
    assert out.certificate.verified_predicates == ("R1",)


# --- the inventory: every id and digest the mapping makes, from the code ----------------------------------------------

MAPPING = Path(g6.__file__).resolve().parent / "evidence" / "mapping.py"
#: The calls that make an id or a digest.
MAKERS = frozenset({"id_of", "_digest", "_canonical", "check_id_of", "_fresh"})
CHECKED = "made, then checked against every accepted check and receipt id and every id made before it (_fresh)"
ACCEPTED = ("a record's own check id, read from its custody binding (about): it is an accepted id, held to one body "
            "with every other record's (issue_records refuses another body as CONFLICTING), and every caveat id is "
            "checked against it")
NOT_AN_ID = "a digest of code, an environment or a binding, never an id a check or a receipt is given"
#: Every call that makes an id or a digest in ``mapping``, by the function it is in, with what keeps it apart.
MADE = {
    ("id_of", "_digest(list(parts))"): "the one way an id is made from parts (as a digest): each call is listed here",
    ("id_of", "_canonical(list(parts))"): "the one way an id is made from parts: each call is listed here",
    ("check_id_of", "id_of(binding.task_id, _digest(binding.to_dict()))"): ACCEPTED,
    ("check_id_of", "_digest(binding.to_dict())"): NOT_AN_ID + " (the binding's, inside its check id)",
    ("bound_parts", "check_id_of(binding)"): ACCEPTED,
    ("caveat_of", "_digest([str(rec.record_id), str(parts.check_id)])"): NOT_AN_ID + (
        " (a caveat's check code, where the record names none)"),
    ("refuse", "id_of('caveat', len(kept), str(rec.record_id))"): CHECKED + ": the id it is given if nothing has it",
    ("issue_records", "id_of('caveat', len(kept), str(rec.record_id))"): CHECKED + " (refuse(), inside it)",
    ("_fresh", "id_of(ident, count)"): CHECKED,
    ("issue_records", "_fresh(taken, check.check_id)"): CHECKED,
    ("environment_sha256", "_digest(dict(environment))"): NOT_AN_ID,
}


def made() -> set[tuple[str, str]]:
    tree = ast.parse(MAPPING.read_text(encoding="utf-8"))
    found = set()
    for function in (f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)):
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in MAKERS:
                found.add((function.name, ast.unparse(node)))
    return found


def test_every_id_the_mapping_makes_is_listed_and_every_listed_one_is_still_there():
    found = made()
    assert sorted(found - set(MADE)) == [], "an id or digest the mapping makes that the inventory does not name"
    assert sorted(set(MADE) - found) == [], "listed ones the scan no longer finds: remove them"


def test_every_id_made_for_a_check_or_a_receipt_is_checked_against_the_accepted_ones():
    tree = ast.parse(MAPPING.read_text(encoding="utf-8"))
    functions = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
    fresh = ast.unparse(functions["_fresh"])
    assert "while found in taken" in fresh and "taken.add(found)" in fresh
    issuing = ast.unparse(functions["issue_records"])
    assert "taken = {*checks, *receipts}" in issuing   # every accepted check id and receipt id
    # every caveat's id is checked after the last record is read, and every caveat goes through it
    assert issuing.index("taken = {*checks, *receipts}") > issuing.index("for rec in records:")
    assert ("kept = [(*_named(check, receipt, _fresh(taken, check.check_id)), rec, codes) for check, receipt, rec, "
            "codes in kept]") in issuing
    assert issuing.index("_fresh(taken") < issuing.index("ledger = ChainLedger()")   # before anything is written
    assert {where for where, why in MADE.items() if why.startswith(CHECKED)} == {
        key for key in made() if key[0] in ("_fresh", "issue_records", "refuse")}


def test_the_scan_finds_a_planted_id():
    planted = ast.parse("def refuse(rec):\n    return id_of('caveat', 0, rec.record_id)\n")
    assert {ast.unparse(n) for n in ast.walk(planted) if isinstance(n, ast.Call) and ast.unparse(n.func) in MAKERS} == {
        "id_of('caveat', 0, rec.record_id)"}
