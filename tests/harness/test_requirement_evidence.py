# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The patch review's evidence in the issuer's terms, and the report that renders it (G6 0.3 design, slice 2, items 1
and 6).

``assessment.requirement_evidence`` is the issuer's certificate for the review, each requirement's statement taken from it, and
what the mapping refused. The review's own ``status`` and ``outcome`` keep their meaning. The report shows each
requirement with its evidence kind and the issuer's state, what was not checked, and keeps model results apart.

Development fixtures only; G6's test runs are replaced by recorded results (``Recorded``), so nothing here is confined
and no model is called.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from g6.evidence import mapping as m
from g6.evidence import verify
from g6.harness import patch_report, patch_review, requirement_evidence
from g6.harness.patch_review import review_patch

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_negative():\n    assert add(-2, -3) == -5\n")
ADD, NEGATIVE = "test_calc.py::test_add", "test_calc.py::test_negative"
SUM = [{"id": "R1", "text": "add returns the sum of two positive integers", "checks": [ADD]},
       {"id": "R2", "text": "add returns the sum of two negative integers", "checks": [NEGATIVE]}]
def _banned() -> tuple[str, ...]:
    """The words the report never uses, as the report's own tests list them (one list, read from there)."""
    import ast

    tree = ast.parse(Path(__file__).with_name("test_patch_report.py").read_text(encoding="utf-8"))
    found = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(getattr(t, "id", "") == "BANNED" for t in node.targets))
    return tuple(ast.literal_eval(found))


BANNED = _banned()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text(BAD)
    (tmp_path / "test_calc.py").write_text(TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    return tmp_path


def result(passed=(), failed=()):
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    verdict = "failed" if failed else "passed"
    return {"verdict": verdict, "said": "", "passed": len(passed), "failed": len(failed), "not_passed": 0,
            "total": len(passed) + len(failed), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.25,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": False, "receipt": ""}


def recorded(monkeypatch, in_place, at_base=None, default=None):
    def execute(root, ids, *, timeout_s, change=None, roots=()):
        got = in_place.get(tuple(ids), default)
        assert got is not None, f"no recorded result for {ids}"
        return got

    def execute_at_base(change, ids, *, timeout_s, roots=()):
        return at_base if at_base is not None else {"verdict": "not_run", "said": "no run was recorded"}

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base", execute_at_base)


def review(repo, requirements=SUM):
    return review_patch(repo, task="Repair addition for positive and negative integers.", requirements=requirements)


def _correct(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE])},
             at_base=result(failed=[ADD, NEGATIVE]))
    return review(repo)


def test_each_requirement_carries_the_issuer_s_statement_beside_the_review_s_own_reading(repo, monkeypatch):
    got = _correct(repo, monkeypatch)
    assessment = got["assessment"]
    assert assessment["outcome"] == "supported_within_scope"   # the review's own outcome keeps its meaning
    assert [r["status"] for r in assessment["requirements"]] == ["passed_on_stated_checks"] * 2
    evidence = assessment["requirement_evidence"]
    assert evidence["authority"] == "g6.evidence.certificate" and evidence["refused"] == []
    for found in evidence["requirements"]:
        assert found["kinds"] == {ADD if found["requirement"] == "R1" else NEGATIVE: "independent_executable"}
        # the check ran again on the patch after its control (the run at the base commit): the issuer counts it
        assert found["checked"] is True and found["support"] is True and found["gap"] == ""
    body = evidence["certificate"]
    assert body["state"] == "verified" and body["artifact_sha"] == assessment["scope"]["artifact_digest"]
    assert got["completion_state"] == "qualified-draft"   # the issuer's state is a state; the review's label stands
    # the certificate is the issuer's: decided again from what it records, only the bytes (not at hand) differ
    assert verify(body, b"") == ["the artifact bytes are not the ones the certificate was issued for"]


def test_the_statement_is_the_issuer_s_for_the_same_records_and_nothing_else_decides_it(repo, monkeypatch):
    got = _correct(repo, monkeypatch)
    assessment = got["assessment"]
    body = assessment["requirement_evidence"]["certificate"]
    assert [s["predicate"] for s in body["predicates"]] == ["R1", "R2"]
    for found, statement in zip(assessment["requirement_evidence"]["requirements"], body["predicates"], strict=True):
        assert {k: found[k] for k in statement} == statement


def test_a_check_the_patch_adds_is_the_patch_author_s_and_supports_nothing(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    (repo / "test_new.py").write_text("from calc import add\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n")
    new = "test_new.py::test_zero"
    recorded(monkeypatch, {(new,): result([new])}, at_base=result(failed=[new]), default=result([new]))
    got = review(repo, [{"id": "R0", "text": "add(0, 0) is 0", "checks": [new]}])
    found = got["assessment"]["requirement_evidence"]["requirements"][0]
    assert found["kinds"] == {new: "partial_falsifier"} and found["checked"] is False
    assert "self_authored" in found["gap"] or "frozen_after_artifact" in found["gap"]


def test_the_adapter_records_what_g6_observed_in_the_order_it_observed_it():
    req = {"id": "R1", "text": "t", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": ADD, "provenance": "stated", "verdict": "passed"}]}
    keep = {"id": "K1", "text": "k", "kind": "preserve", "provenance": "stated",
            "checks": [{"id": NEGATIVE, "provenance": "stated", "verdict": "failed"}]}
    runs = [{"tier": 2, "check": requirement_evidence.AT_BASE, "outcomes": {ADD: "failed", NEGATIVE: "passed"}}]
    facts, artefact, records = requirement_evidence.records_for(
        [req, keep], runs, artifact="a" * 64, base_commit="0" * 40, check_sha={ADD: "c" * 64, NEGATIVE: "d" * 64},
        written=lambda _check: False, environment={"python": "3"})
    add, negative = records
    assert (add.frozen_seq, artefact.seq, add.seq, add.controls[0].seq) == (0, 1, 2, 3)
    assert add.controls[0].killed and add.controls[0].generated_by == "g6" and add.positive_seq is None
    assert negative.controls == () and negative.positive_seq == 3   # behaviour to keep: the base is the reference
    assert negative.result == "fail" and facts.predicates == ("R1", "K1")
    out = m.issue_records(facts, artefact, records)
    # the ledger holds what was promoted in the order G6 observed it: the receipt, then the run at the base commit; the
    # check of the behaviour to keep has no negative control, so the mapping refuses it and it never reaches the issuer
    assert [e["kind"] for e in out.ledger.entries() if e["kind"] != m.PROVENANCE_KIND] == ["receipt", "control"]
    assert [(rid, [code for code, _ in codes]) for rid, codes in out.refused] == [(m.id_of("stated-run", NEGATIVE),
                                                                                  [m.NO_CONTROL])]
    statements = {s.predicate: s for s in out.certificate.predicates}
    assert not statements["R1"].support and not statements["K1"].refute   # never a refutation here


def test_a_check_g6_could_not_read_is_refused_with_the_part_it_lacks():
    req = {"id": "R1", "text": "t", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": ADD, "provenance": "stated", "verdict": "passed"}]}
    got = requirement_evidence.assess([req], [], artifact="a" * 64, base_commit="0" * 40, check_sha={},
                                      written=lambda _check: False, environment={})
    codes = [c["code"] for c in got["refused"][0]["codes"]]
    assert m.NO_CHECK in codes and got["requirements"][0]["kinds"] == {ADD: ""}


def _lines(report: str) -> list[str]:
    return report.splitlines()


def test_the_report_shows_each_requirement_s_evidence_kind_and_the_issuer_s_state(repo, monkeypatch):
    got = _correct(repo, monkeypatch)
    report = got["assessment"]["report"]
    shown = report.split("## Demonstrated on the stated checks", 1)[1].split("##", 1)[0]
    for ident in ("R1", "R2"):
        line = next(line for line in _lines(shown) if f"`{ident}`" in line)
        assert "`independent_executable`" in line and "the evidence issuer: checked" in line, line
    assert len(_lines(report)) <= patch_report.MAX_LINES + 2
    words = set(re.findall(r"[a-z]+", report.lower()))
    assert not words & set(BANNED), words & set(BANNED)


def test_the_report_says_what_was_not_checked_and_keeps_model_results_apart(repo, monkeypatch):
    got = _correct(repo, monkeypatch)
    report = got["assessment"]["report"]
    line = next(line for line in _lines(report) if line.startswith("Not checked: "))
    assert all(item in line for item in got["assessment"]["not_checked"])
    assert patch_report.STANDING_NOTE in report


def test_the_report_renders_each_state_the_issuer_can_give():
    base = {"outcome": "insufficient_evidence", "reasons": [], "requirements": [],
            "completion": {"not_established": [{"requirement": r, "text": "t", "reason": "r", "settle_by": "s"}
                                               for r in ("R1", "R2", "R3")]}}
    states = {"R1": {"checked": True, "support": True, "refute": False},
              "R2": {"checked": False, "support": False, "refute": True, "refuted_by": ["c2"]},
              "R3": {"checked": False, "support": True, "refute": True, "refuted_by": ["c3"]}}
    evidence = {"requirements": [{"requirement": r, "kinds": {"c": "independent_executable"}, "gap": "", **s}
                                 for r, s in states.items()]}
    report = patch_report.render({**base, "requirement_evidence": evidence})
    missing = report.split("## Not established", 1)[1].split("##", 1)[0]
    assert "the evidence issuer: checked" in missing
    assert "the evidence issuer: refuted by `c2`" in missing
    assert "the evidence issuer: conflicting evidence" in missing
    # a requirement whose every check the mapping refused says so, and where the codes are
    lacking = {"requirements": [{"requirement": "R1", "kinds": {"c": ""}, "gap": "no_receipt", "checked": False,
                                 "support": False, "refute": False}]}
    said = patch_report.render({**base, "requirement_evidence": lacking})
    assert "the evidence mapping refused each of its checks" in said
    assert "`assessment.requirement_evidence.refused`" in said
    # without assessment.requirement_evidence the report is as it was
    assert "evidence issuer" not in patch_report.render(base)
    # inputs the issuer refused are said, never left silent
    refused = patch_report.render({**base, "requirement_evidence": {"refused_by_issuer": "duplicate check id",
                                                                    "requirements": []}})
    assert refused.count("the evidence issuer refused the review's inputs") == 3


def test_inputs_the_issuer_refuses_leave_the_review_s_fields_standing_and_say_so():
    req = {"id": "R1", "text": "t", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": ADD, "provenance": "stated", "verdict": "passed"}]}
    got = requirement_evidence.assess([req], [], artifact="not a digest", base_commit="0" * 40, check_sha={},
                                      written=lambda _check: False, environment={})
    assert "sha256" in got["refused_by_issuer"] and got["requirements"] == []
