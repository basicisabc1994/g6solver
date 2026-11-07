# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""An observed failure always reaches what is stated (G6 0.3, slice 2's follow-up; review 4's finding 2; class
``failure_read_as_success``, met in every round of slice 2).

A stated check that passed at the base commit and failed on the patch had no negative control, so the mapping refused
its record and dropped it before issuance; another check's pass then made the requirement "checked", with no caveat
for the failure G6 saw. Now refusing a record never drops what it observed: every refusal the mapping makes, whatever
its code, hands a refused record's failing or unknown observation on the artefact's bytes to the issuer as a caveat
(``mapping.caveat_of``), the issuer's own input for one: a check with no control, which can neither support nor refute,
and its receipt. The inventory lists, from the code, every place the mapping or the patch review's adapter refuses,
filters or drops a record, and each one is shown handing a failing observation on.

Development fixtures only: no test runs, and no model runs.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

import g6
from g6.evidence import Artifact, Control, MissionFacts, verify
from g6.evidence import mapping as m
from g6.harness import patch_report
from g6.harness import requirement_evidence as evidence

# --- review 4's finding 2, brought in as this change's own ------------------------------------------------------------


def test_finding_2_a_flaky_check_without_its_own_control_still_withholds_checked():
    decisive, flaky = "test_calc.py::test_sum", "test_calc.py::test_edges"
    evaluated = [{
        "id": "R1", "text": "add sums its inputs", "kind": "requirement", "provenance": "stated",
        "checks": [{"id": node, "provenance": "stated", "verdict": "passed"} for node in (decisive, flaky)],
    }]
    runs = [{"check": evidence.AT_BASE, "outcomes": {decisive: "failed", flaky: "passed"}}]

    def assess(later):
        return evidence.assess(
            evaluated, runs, artifact="a" * 64, base_commit="0" * 40,
            check_sha={decisive: "c" * 64, flaky: "d" * 64}, written=lambda _: False,
            environment={"python": "recorded test run"}, again={"verdicts": {decisive: "passed"}},
            observed=[{"run": ["derived"], "verdicts": {decisive: "passed", flaky: later}}],
        )

    assert assess("passed")["requirements"][0]["checked"] is True  # positive control
    got = assess("failed")
    assert got["observed"][0]["verdicts"][flaky] == "failed"
    report = patch_report.render({
        "requirements": evaluated, "requirement_evidence": got,
        "completion": {"demonstrated": [{"requirement": "R1", "checks": [decisive, flaky]}]},
    })
    assert not got["requirements"][0]["checked"], (
        "a stated check failed on the patch, but its missing negative control discarded its caveat; "
        "another check's pass made the whole requirement checked", got, report,
    )
    found = got["requirements"][0]
    assert found["support"] is True and found["refute"] is False and found["caveats"]   # a caveat, never a refutation
    assert "advisory_check_failed" in found["codes"] and "did not (failed)" in found["not_counted"]
    kept = [r for r in got["refused"] if r.get("caveat")]
    assert kept and all(r["check"] == flaky for r in kept)


# --- records, each refused for one part, beside a check that supports the same predicate -----------------------------

A, OTHER, STANDARD, CODE, FLAKY, ENV = "a" * 64, "b" * 64, "5" * 64, "c" * 64, "d" * 64, "e" * 64
SUBJECT = Artifact(A, "agent", 1)
FACTS = MissionFacts("1" * 64, STANDARD, STANDARD, ("R1",), ("R1",))


def control(**over) -> Control:
    return Control(**{"control_id": "control", "check_sha": CODE, "predicate": "R1", "generated_by": "g6", "seq": 2,
                      "killed": True, **over})


def record(**over) -> m.SourceRecord:
    base = {"kind": "independent_executable", "record_id": "decisive-run", "predicates": ("R1",), "result": "pass",
            "artefact_sha256": A, "producer": "agent", "artefact_seq": 1, "standard_sha256": STANDARD,
            "check_id": "decisive", "check_sha256": CODE, "author": "reviewer", "frozen_seq": 0,
            "environment_sha256": ENV, "seq": 3, "scope": "one input", "controls": (control(),)}
    return m.SourceRecord(**{**base, **over})


DECISIVE = record()
#: A failing record of another check for R1, refused for its missing control: the finding's own case.
FLAKY_FAIL = record(record_id="flaky-run", check_id="flaky", check_sha256=FLAKY, result="fail", seq=4, controls=())
BINDING = m.CheckBinding(request_id="req", task_id="task", predicate_id="R1", artifact_sha256=A,
                         standard_sha256=STANDARD, fixture_sha256="f" * 64, evaluator_sha256=FLAKY)
#: Each way a failing record is refused, as (code, the records after the supporting one).
REFUSED = {
    "no-control": (m.NO_CONTROL, [FLAKY_FAIL]),
    "a-lead": (m.NOT_PROMOTED, [replace(FLAKY_FAIL, kind="derived")]),
    "no-row": (m.UNMAPPED, [replace(FLAKY_FAIL, kind="a kind with no row")]),
    "no-check-code": (m.NO_CHECK, [replace(FLAKY_FAIL, check_sha256="")]),
    "no-environment": (m.NO_ENVIRONMENT, [replace(FLAKY_FAIL, environment_sha256="")]),
    "no-author": (m.NO_INDEPENDENCE, [replace(FLAKY_FAIL, author="")]),
    "other-producer": (m.OTHER_PRODUCER, [replace(FLAKY_FAIL, producer="someone else")]),
    "no-position": (m.NO_ORDER, [replace(FLAKY_FAIL, seq=None)]),
    "other-position": (m.OTHER_ORDER, [replace(FLAKY_FAIL, artefact_seq=4)]),
    "no-scope": (m.NO_SCOPE, [replace(FLAKY_FAIL, scope="")]),
    "unreadable-result": (m.NO_RESULT, [replace(FLAKY_FAIL, result="garbled")]),
    "no-model": (m.NO_MODEL, [replace(FLAKY_FAIL, kind="formal_checked")]),
    "other-standard": (m.OTHER_STANDARD, [replace(FLAKY_FAIL, standard_sha256=OTHER)]),
    "binding-disagrees": (m.BINDING_STANDARD, [replace(m.SourceRecord.from_binding(
        BINDING, kind="independent_executable", record_id="flaky-run", result="fail", producer="agent",
        artefact_seq=1, author="reviewer", frozen_seq=0, environment_sha256=ENV, seq=4, scope="one input"),
        standard_sha256=OTHER)]),
    "one-id-two-bodies": (m.CONFLICTING, [replace(DECISIVE, result="fail", seq=4)]),
    "a-control-with-two-bodies": (m.CONFLICTING, [replace(FLAKY_FAIL, controls=(control(killed=False),))]),
    "two-controls-one-id-in-one-record": (m.CONFLICTING, [replace(FLAKY_FAIL, check_sha256=CODE, controls=(
        control(control_id="k2"), control(control_id="k2", killed=False)))]),
    "a-check-with-two-bodies": (m.CONFLICTING, [replace(DECISIVE, record_id="other-run", author="someone",
                                                        result="fail", seq=4)]),
    "a-value-the-issuer-refuses": (m.NO_ORDER, [replace(FLAKY_FAIL, controls=(
        control(control_id="flaky-control", check_sha=FLAKY),), certificate="not a digest")]),
}


def issued(*records: m.SourceRecord, **kwargs) -> m.Issued:
    return m.issue_records(FACTS, SUBJECT, [DECISIVE, *records], **kwargs)


def test_the_supporting_check_alone_is_verified():   # the positive control for every plant below
    assert issued().certificate.verified_predicates == ("R1",)
    assert issued(replace(FLAKY_FAIL, result="pass")).certificate.verified_predicates == ("R1",)


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_every_refusal_hands_a_failing_observation_on_as_a_caveat(case):
    code, records = REFUSED[case]
    out = issued(*records)
    refused = [c for _, found in out.refused for c, _ in found]
    assert code in refused, (case, out.refused)
    statement = out.certificate.predicates[0]
    assert statement.verified is False, (case, "a refused failure was dropped, and another check's pass verified R1")
    assert statement.caveats and not statement.refute, case   # a caveat, never a refutation
    assert out.caveats and {rid for rid, _ in out.caveats} <= {rid for rid, _ in out.refused}
    assert out.certificate.state == "qualified-draft"
    assert verify(out.certificate.as_dict(), b"", ledger=out.ledger) == [
        "the artifact bytes are not the ones the certificate was issued for"]   # it decides again the same way


def test_a_record_its_source_does_not_believe_is_refused_and_its_failure_still_reaches_the_issuer():
    out = issued(FLAKY_FAIL, not_believed="the review found an integrity problem")
    assert [c for _, found in out.refused for c, _ in found] == [m.NOT_BELIEVED, m.NOT_BELIEVED]
    assert out.promoted == () and [r.result for r in out.certificate.receipts] == ["fail"]
    assert out.certificate.predicates[0].caveats and not out.certificate.predicates[0].support


def test_a_refused_pass_withholds_nothing_and_a_failure_on_other_bytes_says_nothing_of_these():
    assert issued(replace(FLAKY_FAIL, result="pass")).caveats == ()
    for elsewhere in (replace(FLAKY_FAIL, artefact_sha256=OTHER), replace(FLAKY_FAIL, artefact_sha256=""),
                      replace(FLAKY_FAIL, binding=m.UnreadableBinding("garbled")),
                      replace(FLAKY_FAIL, predicates=()), replace(FLAKY_FAIL, predicates=("R9",))):
        out = issued(elsewhere)
        assert out.refused and out.caveats == (), elsewhere   # refused, and listed; it names nothing here
        assert out.certificate.verified_predicates == ("R1",)   # as the issuer reads a failure on other bytes


def test_a_caveat_can_neither_support_nor_refute_whatever_the_record_says():
    check, receipt = m.caveat_of(replace(FLAKY_FAIL, controls=(control(check_sha=FLAKY),)), "kept", facts=FACTS,
                                 artifact=SUBJECT)
    assert check.kind == m.CAVEAT_KIND and check.controls == () and check.positive_seq is None
    assert (receipt.result, receipt.artifact_sha, receipt.check_sha) == ("fail", A, check.check_sha)
    unknown = m.caveat_of(replace(FLAKY_FAIL, result="garbled", check_sha256="", seq=None, frozen_seq=None,
                                  author=" "), "kept", facts=FACTS, artifact=SUBJECT)
    assert unknown[1].result == "unknown" and unknown[1].seq == SUBJECT.seq and unknown[0].frozen_seq == 0
    assert m.caveat_of(replace(FLAKY_FAIL, seq=0), "kept", facts=FACTS, artifact=SUBJECT)[1].seq == SUBJECT.seq
    assert m.caveat_of(replace(FLAKY_FAIL, predicates=("R1", "R9", "R1")), "k", facts=FACTS,
                       artifact=SUBJECT)[0].predicates == ("R1",)


def test_the_caveat_is_written_to_the_ledger_with_why_its_record_was_refused():
    out = issued(FLAKY_FAIL)
    (rid, cid), = out.caveats
    assert rid == "flaky-run"
    entry = out.ledger.get("provenance", cid)
    assert entry["kept_as"] == "caveat" and entry["record_id"] == "flaky-run"
    assert [code for code, _ in entry["refused"]] == [m.NO_CONTROL]
    assert out.ledger.entry("receipt", cid)["body"]["result"] == "fail"


def test_the_patch_review_s_integrity_rule_refuses_each_record_and_keeps_its_failure():
    req = {"id": "R1", "text": "t", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": "t::a", "provenance": "stated", "verdict": "failed"}]}
    got = evidence.assess([req], [], artifact=A, base_commit="0" * 40, check_sha={"t::a": CODE},
                          written=lambda _check: False, environment={}, integrity=["runner_configuration_changed"])
    assert [c["code"] for r in got["refused"] for c in r["codes"]] == [m.NOT_BELIEVED]
    assert got["requirements"][0]["checked"] is False and got["requirements"][0]["caveats"]
    assert evidence.INTEGRITY in got["requirements"][0]["not_counted"]


# --- the inventory: every place the mapping or the adapter refuses, filters or drops a record, from the code ----------

SRC = Path(g6.__file__).resolve().parent
#: The functions a record passes through on its way to the issuer.
FUNCTIONS = {"evidence/mapping.py": {"issue_records", "refuse", "promote", "caveat_of"},
             "harness/requirement_evidence.py": {"records_for", "observed_runs", "assess"}}
REFUSE = "refused through refuse(), which hands a failing observation on as a caveat (caveat_of)"
#: Every place a record, or a run that would make one, is refused, filtered or dropped, with how its failure goes on.
DROPS = {
    ("evidence/mapping.py", "issue_records", "if rec.record_id in seen"): (
        "a record id seen before: " + REFUSE + " when its body is another; the same record again is already handed "
        "on, once"),
    ("evidence/mapping.py", "issue_records", "if not_believed"): REFUSE + " (a run its source does not believe)",
    ("evidence/mapping.py", "issue_records", "if any((bodies.get(c.control_id, c) != c for c in _controls(rec)))"): (
        REFUSE + " (a control with two bodies)"),
    ("evidence/mapping.py", "issue_records", "except PromotionRefused"): REFUSE + " (every part the table asks for)",
    ("evidence/mapping.py", "issue_records", "except CertificateRefused"): REFUSE + " (a value the issuer refuses)",
    ("evidence/mapping.py", "issue_records", "if merged is None"): REFUSE + " (a check with two bodies)",
    ("evidence/mapping.py", "caveat_of", "if rec.result == 'pass'"): "a pass: refusing it withholds nothing",
    ("evidence/mapping.py", "caveat_of", "if not isinstance(parts.artefact, str) or parts.artefact != "
                                         "artifact.sha256"): (
        "taken on other bytes, or on none it can name: the issuer reads nothing of these from it, a caveat included"),
    ("evidence/mapping.py", "caveat_of", "if not named"): "it names no predicate of the standard: none it could hold",
    ("harness/requirement_evidence.py", "records_for", "if one['provenance'] != 'stated'"): (
        "the other runs and the run again observe only stated checks: no observation of this one is dropped"),
    ("harness/requirement_evidence.py", "assess", "except (CertificateRefused, ValueError, TypeError)"): (
        "the issuer refuses the inputs as a whole: no requirement is stated at all, so none is checked"),
}


def drops() -> set[tuple[str, str, str]]:
    """(module, function, what) for every branch that skips a record (``continue``), stops on one (an ``except`` that
    continues or returns, a ``return None``), in the functions a record passes through."""
    found = set()
    for rel, names in FUNCTIONS.items():
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for function in (f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name in names):
            for node in ast.walk(function):
                if isinstance(node, ast.If) and any(isinstance(s, (ast.Continue, ast.Return)) and (
                        not isinstance(s, ast.Return) or s.value is None or (
                            isinstance(s.value, ast.Constant) and s.value.value is None)) for s in node.body):
                    found.add((rel, function.name, "if " + ast.unparse(node.test)))
                elif isinstance(node, ast.ExceptHandler) and any(isinstance(s, (ast.Continue, ast.Return))
                                                                 for s in node.body):
                    found.add((rel, function.name, "except " + ast.unparse(node.type)))
    return found


def test_every_place_a_record_is_refused_or_dropped_is_listed_and_every_listed_one_is_still_there():
    found = drops()
    assert sorted(found - set(DROPS)) == [], "a place a record is refused or dropped the inventory does not list"
    assert sorted(set(DROPS) - found) == [], "listed places the scan no longer finds: remove them"


def test_every_refusal_in_the_mapping_goes_through_the_one_step_that_hands_its_failure_on():
    tree = ast.parse((SRC / "evidence/mapping.py").read_text(encoding="utf-8"))
    issue_records = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "issue_records")
    refuse = next(f for f in ast.walk(issue_records) if isinstance(f, ast.FunctionDef) and f.name == "refuse")

    def appends(tree: ast.AST) -> set[int]:
        return {id(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
                and ast.unparse(node.func) == "refused.append"}

    assert appends(issue_records) == appends(refuse) and len(appends(refuse)) == 1   # refused only in refuse()
    assert "caveat_of(" in ast.unparse(refuse)
    for node in ast.walk(issue_records):   # every skip but the same record's second sighting follows a refusal
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for before, statement in zip([None, *body], body, strict=False):
            if isinstance(statement, ast.Continue) and not (isinstance(node, ast.If) and ast.unparse(node.test) in (
                    "rec.record_id in seen", "seen[rec.record_id] != rec")):
                assert before is not None and ast.unparse(before).startswith("refuse("), ast.unparse(node)


#: Each refusal ``issue_records`` makes, by the code it hands ``refuse``, with the plant above that reaches it.
REFUSE_SITES = {"CONFLICTING": {"one-id-two-bodies", "a-control-with-two-bodies", "a-check-with-two-bodies"},
                "NOT_BELIEVED": {"not-believed"}, "exc.codes": {"no-control"},
                "NO_ORDER": {"a-value-the-issuer-refuses"}}
#: The refusal codes ``problems`` gives that leave a record about no bytes or no predicate of the standard here: it is
#: refused and listed, and names nothing a caveat could be held for (``test_a_refused_pass_withholds_nothing...``).
NAMES_NOTHING = {"NO_ARTEFACT", "OTHER_ARTEFACT", "BINDING_UNREADABLE"}


def test_every_refusal_site_and_every_code_the_mapping_refuses_with_is_planted():
    tree = ast.parse((SRC / "evidence/mapping.py").read_text(encoding="utf-8"))
    issue_records = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "issue_records")
    sites: dict[str, int] = {}
    for node in ast.walk(issue_records):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "refuse":
            given = ast.unparse(node.args[1])
            key = next(k for k in REFUSE_SITES if k in given)
            sites[key] = sites.get(key, 0) + 1
    assert sites == {"CONFLICTING": 3, "NOT_BELIEVED": 1, "exc.codes": 1, "NO_ORDER": 1}, sites
    assert all(case in REFUSED for key, cases in REFUSE_SITES.items() if key != "NOT_BELIEVED" for case in cases)
    problems = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "problems")
    given = {node.args[0].elts[0].id for node in ast.walk(problems) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "found.append" and isinstance(node.args[0], ast.Tuple)}
    given |= {"BINDING_" + part for part in ("ARTEFACT", "STANDARD", "PREDICATE", "CHECK", "UNREADABLE")}  # about()'s
    codes = {c for c, _ in REFUSED.values()}
    planted = {name for name in m.__all__ if isinstance(getattr(m, name), str) and getattr(m, name) in codes}
    elsewhere = {"NO_STANDARD", "BINDING_ARTEFACT", "BINDING_PREDICATE", "BINDING_CHECK"}   # their own tests below
    assert given - planted - NAMES_NOTHING - elsewhere == set(), sorted(given - planted - NAMES_NOTHING - elsewhere)


@pytest.mark.parametrize("part", ["artefact_sha256", "predicates", "check_id"])
def test_a_failing_record_whose_field_disagrees_with_its_binding_is_held_by_its_binding(part):
    bound = m.SourceRecord.from_binding(BINDING, kind="independent_executable", record_id="flaky-run", result="fail",
                                        producer="agent", artefact_seq=1, author="reviewer", frozen_seq=0,
                                        environment_sha256=ENV, seq=4, scope="one input")
    other = {"artefact_sha256": OTHER, "predicates": ("R9",), "check_id": "another"}[part]
    out = issued(replace(bound, **{part: other}))
    codes = [c for _, found in out.refused for c, _ in found]
    assert {m.BINDING_ARTEFACT, m.BINDING_PREDICATE, m.BINDING_CHECK} & set(codes), codes
    assert out.caveats and out.certificate.predicates[0].verified is False   # the binding names A and R1


def test_a_failing_record_that_names_no_predicate_is_refused_and_listed():
    out = issued(replace(FLAKY_FAIL, predicates=()))
    assert [c for _, found in out.refused for c, _ in found][:1] == [m.NO_STANDARD] and out.caveats == ()


def test_the_scan_finds_a_planted_drop():
    planted = ast.parse("def issue_records(records):\n    for rec in records:\n        if rec.result == 'fail':\n"
                        "            continue\n")
    found = {("x", f.name, "if " + ast.unparse(n.test)) for f in ast.walk(planted) if isinstance(f, ast.FunctionDef)
             for n in ast.walk(f) if isinstance(n, ast.If) and any(isinstance(s, ast.Continue) for s in n.body)}
    assert found == {("x", "issue_records", "if rec.result == 'fail'")}
