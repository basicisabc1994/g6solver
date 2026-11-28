"""Review 3 reproductions, using real content-addressed records on disk."""
import builtins
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core.ledger import canonical, digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.held_out.case_gate import CaseRecord
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.core.source_check import SAFE_BUILTIN_NAMES
from g6.distillation.sandbox import check_source
from tests.distill.test_records import bodies
from tests.distill.test_review2_repairs import CASE


@pytest.fixture
def tmp_path():
    # Avoid pytest's recursive basetemp cleanup, denied by this Windows sandbox.
    path = Path(__file__).resolve().parents[2] / ".bt_rev_dfu3" / uuid4().hex
    path.mkdir(parents=True)
    return path


def disk_library(monkeypatch, tmp_path, source, *, status="withdrawn", family="test_weakening", form="predicate"):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    monkeypatch.setenv("G6_CLAUDE", r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setenv("G6_CODEX", r"C:\nonexistent\g6-no-host.exe")
    failure = r.FailureRecord(**(bodies()[0][1] | {"family": family}))
    concept = r.Concept(**(bodies()[1][1] | {"family": family, "from_failures": [failure.record_id]}))
    contract = r.MethodContract(**(bodies()[2][1] | {"family": family}))
    candidate = r.VerifierCandidate(**(bodies()[3][1] | {
        "concept_id": concept.concept_id, "contract": contract, "body": source, "form": form}))
    case = CaseRecord.model_validate(CASE)
    entry = r.LibraryEntry(**(bodies()[4][1] | {
        "candidate_id": candidate.candidate_id, "concept_id": concept.concept_id,
        "contract_sha256": contract.content_id, "family": family, "status": status,
        "authority": "A2" if status == "admitted" else "A0",
        "regression_suite": [{"case_ref": case.case_id, "verdict": "concern"}]}))
    for value in (failure, concept, contract, candidate, entry):
        Stores().for_model(type(value)).put(value)
    archive = tmp_path / "home/distill/held_out/regression_cases"
    archive.mkdir(parents=True)
    (archive / f"{case.case_id}.json").write_text(canonical(case.model_dump(mode="json")) + "\n", encoding="utf-8")
    return candidate, entry, case


def proposal(first, **updates):
    value = r.VerifierCandidate(**(first.model_dump(mode="json", exclude={"candidate_id"}) | updates))
    Stores().for_model(r.VerifierCandidate).put(value)
    return value


def load_boundary(monkeypatch):
    reached = []
    monkeypatch.setattr(gate, "_policy", lambda: None)
    monkeypatch.setattr(gate.stop, "engaged", lambda: None)

    def load(registration, candidate, library):
        reached.append(candidate.candidate_id)
        raise ValueError("review stops at registration boundary")

    monkeypatch.setattr(gate, "_load", load)
    return reached


@pytest.mark.parametrize("status", ["withdrawn", "held"])
def test_observable_module_docstring_is_not_an_exact_match(monkeypatch, tmp_path, status):
    first_source = "'concern'\ndef solve(facts):\n return {'verdict': __doc__}\n"
    other_source = first_source.replace("'concern'", "'no_concern'")
    # A semantic witness with exactly the runner's builtin table. This does not
    # replace the gate's runner; the erroneous fast path never calls that runner.
    for source, verdict in ((first_source, "concern"), (other_source, "no_concern")):
        assert check_source(source) == []
        namespace = {"__builtins__": {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}}
        exec(compile(source, "candidate.py", "exec"), namespace)
        assert namespace["solve"]({}) == {"verdict": verdict}
    first, _, _ = disk_library(monkeypatch, tmp_path, first_source, status=status)
    other = proposal(first, body=other_source)
    assert not gate._inactive_match(other, Library().list(), status), (
        "exact identity erased __doc__, although the recorded-case verdict changed from concern to no_concern")


def test_recurrence_hold_after_identity_check_must_prevent_load(monkeypatch, tmp_path):
    from g6.distill.rectify_gate import Rectifier

    first, entry, case = disk_library(monkeypatch, tmp_path,
        "def solve(facts):\n return {'verdict': 'concern'}", status="admitted", family="g6_self")
    other = proposal(first, body=first.body.replace("facts", "data"))
    failure = Stores().for_model(r.FailureRecord).list()[0]
    rect = r.Rectification(**(bodies()[7][1] | {
        "class_removed": True, "outcome": "held",
        "pinning": {"test_ref": f"library:{entry.entry_id}", "replayed": [failure.record_id],
                    "fixed_passed": True, "receipt_sha256": digest("removal")}}))
    Rectifier().submit(failure.record_id, rect)
    recurrence = r.FailureRecord(**(failure.model_dump(mode="json", exclude={"record_id"}) | {
        "observed_by": "self", "split": "field", "case_ref": case.case_id,
        "observed_at": "2026-11-23", "provenance": {"case": case.model_dump(mode="json")}}))
    reached = load_boundary(monkeypatch)
    original = gate._look_available
    transitions = []

    def recurrence_at_boundary(registration):
        # The real recurrence writer does not acquire acceptance_gate._writer.
        # This is the schedule of a second MCP failure-record request while the
        # admission request is between its identity check and registration load.
        Stores().for_model(r.FailureRecord).put(recurrence)
        Rectifier().reconcile()
        transitions.append(Library().list()[0].status)
        return original(registration)

    monkeypatch.setattr(gate, "_look_available", recurrence_at_boundary)
    decision = gate.decide(other, Library(), digest("registration"))
    assert transitions == ["held"]
    assert reached == [], f"held equivalent reached _load: {decision.reasons}"


def test_library_application_cannot_revive_a_withdrawn_exact_alias(monkeypatch, tmp_path):
    from g6.harness import library_review_gate

    first, entry, _ = disk_library(monkeypatch, tmp_path,
        {"template": "swallow.v1", "bindings": {}}, status="admitted", form="template_instance")
    other = proposal(first, body={"template": "self_reference.v1", "bindings": {}})
    admitted = r.LibraryEntry(**(entry.model_dump(mode="json", exclude={"entry_id"}) | {
        "candidate_id": other.candidate_id}))
    Stores().for_model(r.LibraryEntry).put(admitted)
    assert gate._method_identity(first) == gate._method_identity(other)
    # A real owner withdrawal of one persisted method. The alias is an existing
    # admitted row, such as one retained in a restored library, not a new proposal.
    decision = gate.withdraw(entry.entry_id, Library(), reason="retire this method", person="Owner")
    assert decision.verdict == "withdraw"
    findings = library_review_gate.check(
        {"tests/test_value.py": "def test_value():\n assert 1 == 1\n"},
        {"tests/test_value.py": "def test_value():\n pass\n"})
    assert not findings, f"withdrawn exact alias still ran and reported: {findings}"
