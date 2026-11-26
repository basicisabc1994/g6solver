# SPDX-License-Identifier: Apache-2.0
"""Synthetic independent evaluator fixtures; no model, corpus or child execution."""
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.distill import records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from .test_records import bodies


@pytest.fixture
def rig(monkeypatch, tmp_path):
    from g6.distill import acceptance_gate as gate
    from validation.distill.held_out.case_gate import CaseRecord, Provenance

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    stores = Stores()
    for cls, body in bodies()[:3]:
        stores.for_model(cls).put(cls(**body))
    contract = r.MethodContract(**bodies()[2][1])
    candidate = r.VerifierCandidate(**(bodies()[3][1] | {
        "form": "template_instance", "body": {"template": "assert_strength.v1", "bindings": {}}}))
    stores.for_model(r.VerifierCandidate).put(candidate)

    def case(i, positive, split="acceptance", false_alarm=False):
        before = f"def test_example_{i}():\n    assert answer(1) == 2\n"
        after = f"def test_example_{i}():\n    pass\n" if positive or false_alarm else before
        value = CaseRecord(unit_id=f"unit-{split}-{i}", provenance=Provenance(
            source="maintainer", cohort="development", source_ref="synthetic",
            source_sha256=digest(i)), files={"answer.py": "def answer(x):\n    return x + 1\n"},
            target="answer.py", changed_lines=(2,), before_tests=before, after_tests=after,
            known_wrong=("def answer(x):\n    return x\n",))
        return gate.Observation(value, "positive" if positive else "negative", split)

    observations = tuple(case(i, i < 100) for i in range(200))
    monitor = tuple(case(i + 200, i < 100, "monitor") for i in range(200))
    frozen = gate.EvaluationSet("a" * 64, "b" * 64, observations, monitor, (),
                                (observations[0].case.case_id, observations[-1].case.case_id))
    policy, settings = gate._policy()
    monkeypatch.setattr(gate, '_policy', lambda: (policy, settings | {
        'registrations': {'a' * 64: {'path': 'unused'}}}))
    monkeypatch.setattr(gate, "_load", lambda *args: frozen)
    return gate, candidate, contract, frozen, case


def test_protected_gate_exists_before_admission():
    import importlib.util
    assert importlib.util.find_spec("g6.distill.acceptance_gate") is not None, "independent acceptance gate missing"


def test_independent_measurements_earn_only_probation(rig):
    gate, candidate, _, _, _ = rig
    decision = gate.decide(candidate, Library(), "a" * 64)
    assert decision.verdict == "admit_probation", decision.reasons
    entry = Library().by_status("probation")[0]
    assert entry.authority == "A1"
    assert entry.evidence.tp == 100 and entry.evidence.fp == 0
    assert entry.evidence.precision.lo95 > .90
    assert entry.evidence.false_rejection.hi95 < .05
    assert entry.regression_suite
    assert decision.report_sha256 == entry.evidence.acceptance_report_sha256
    assert decision.minimum_detectable_gain > 0


@pytest.mark.parametrize("verdict", ["concern", "no_concern"])
def test_trivial_candidates_are_refused(rig, monkeypatch, verdict):
    gate, candidate, _, _, _ = rig
    from g6.distillation.sandbox import Run
    raw = candidate.model_dump(mode="json", exclude={"candidate_id"}) | {
        "form": "predicate", "body": f"def solve(facts):\n    return {{'verdict': '{verdict}'}}\n"}
    candidate = r.VerifierCandidate(**raw)
    Stores().for_model(r.VerifierCandidate).put(candidate)
    monkeypatch.setattr(gate, "run_batch", lambda source, values, **kw: [
        Run("ok", {"verdict": verdict}) for _ in values])
    answer = gate.decide(candidate, Library(), "a" * 64)
    assert answer.verdict == "refuse"
    assert Library().list() == []


def test_held_out_literal_leak_refuses_before_execution(rig, monkeypatch):
    gate, candidate, _, frozen, _ = rig
    body = "def solve(facts):\n    secret = 'test_example_0'\n    return {'verdict': 'concern'}\n"
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "form": "predicate", "body": body}))
    Stores().for_model(r.VerifierCandidate).put(candidate)
    monkeypatch.setattr(gate, "run_batch", lambda *a, **kw: pytest.fail("leaked code executed"))
    result = gate.decide(candidate, Library(), frozen.registration_sha256)
    assert result.verdict == "refuse" and "leak" in " ".join(result.reasons).lower()


def test_fpr_margin_breach_refuses(rig, monkeypatch):
    gate, candidate, _, frozen, case = rig
    changed = frozen.acceptance[:100] + tuple(case(i, False, false_alarm=i < 110) for i in range(100, 200))
    monkeypatch.setattr(gate, "_load", lambda *a: replace(frozen, acceptance=changed))
    result = gate.decide(candidate, Library(), "a" * 64)
    assert result.verdict == "refuse" and "FPR" in " ".join(result.reasons)


def test_existing_entry_interference_flips_admission_to_refusal(rig, monkeypatch):
    gate, candidate, _, frozen, case = rig
    old = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "body": r.TemplateInstance(template="skip_gate.v1", bindings={})}))
    Stores().for_model(r.VerifierCandidate).put(old)
    regression = case(999, False, "regression", false_alarm=True)
    entry_body = bodies()[4][1] | {"candidate_id": old.candidate_id, "status": "probation", "authority": "A1",
        "regression_suite": [{"case_ref": regression.case.case_id, "verdict": "no_concern"}]}
    Stores().for_model(r.LibraryEntry).put(r.LibraryEntry(**entry_body))
    monkeypatch.setattr(gate, "_load", lambda *a: replace(frozen, regression=(regression,)))
    result = gate.decide(candidate, Library(), "a" * 64)
    assert result.verdict == "refuse" and "regression" in " ".join(result.reasons)


def test_a2_needs_independent_labelled_field_fires_and_fresh_regression(rig, monkeypatch):
    gate, candidate, _, _, _ = rig
    gate.decide(candidate, Library(), "a" * 64)
    entry = Library().list()[0]
    assert gate.promote(entry.entry_id, Library(), "a" * 64).verdict == "refuse"
    monkeypatch.setattr(gate, "_field", lambda *a: (30, 0, 3))
    promoted = gate.promote(entry.entry_id, Library(), "a" * 64)
    assert promoted.verdict == "admit"
    current = Library().by_status("admitted")[0]
    assert current.authority == "A2" and current.field.confirmed == 30 and current.field.unlabelled == 3
    assert current.field.precision.lo95 >= .85


def test_regression_rechecked_for_a2(rig, monkeypatch):
    gate, candidate, _, frozen, case = rig
    gate.decide(candidate, Library(), "a" * 64)
    entry = Library().list()[0]
    monkeypatch.setattr(gate, "_field", lambda *a: (30, 0, 0))
    # The frozen acceptance regression body no longer reproduces its recorded verdict.
    altered = case(0, False)
    altered = replace(altered, case=altered.case.model_copy(update={"after_tests": altered.case.before_tests}))
    monkeypatch.setattr(gate, "_load", lambda *a: replace(frozen, acceptance=(altered,) + frozen.acceptance[1:]))
    assert gate.promote(entry.entry_id, Library(), "a" * 64).verdict != "admit"


def test_demote_at_policy_demote_alpha(rig, monkeypatch):
    gate, candidate, _, _, _ = rig
    gate.decide(candidate, Library(), "a" * 64)
    monkeypatch.setattr(gate, "_field", lambda *a: (30, 0, 0))
    gate.promote(Library().list()[0].entry_id, Library(), "a" * 64)
    admitted = Library().list()[0]
    monkeypatch.setattr(gate, "_field", lambda *a: (2, 18, 0))
    decision = gate.monitor(admitted.entry_id, Library())
    assert decision.verdict == "demote"
    assert Library().list()[0].authority == "A1"
    assert decision.p_value_upper_bound <= gate.EvidencePolicy().demote_alpha


def test_withdrawal_is_append_only_and_hides_admitted_view(rig, monkeypatch):
    gate, candidate, _, _, _ = rig
    gate.decide(candidate, Library(), "a" * 64)
    monkeypatch.setattr(gate, "_field", lambda *a: (30, 0, 0))
    gate.promote(Library().list()[0].entry_id, Library(), "a" * 64)
    old = Library().list()[0]
    assert gate.withdraw(old.entry_id, Library(), reason="owner withdrawal", person="Fixture person").verdict == "withdraw"
    assert Library().by_status("admitted") == []
    assert Library().list()[0].authority == "A0"
    assert len(Stores().for_model(r.LibraryEntry).list()) == 3


def test_split_spending_survives_multiple_decisions(rig):
    gate, candidate, _, _, _ = rig
    for _ in range(gate.EvidencePolicy().max_evaluations):
        gate.decide(candidate, Library(), "a" * 64)
    result = gate.decide(candidate, Library(), "a" * 64)
    assert result.verdict == "refuse" and "spent" in " ".join(result.reasons)


def test_withdrawal_cascades_through_compositions_without_deleting_bodies(rig):
    gate, candidate, _, _, _ = rig
    gate.decide(candidate, Library(), "a" * 64)
    parent = Library().list()[0]
    last = parent
    dependants = []
    for _ in range(2):
        composite = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
            "form": "composition", "body": {"op": "any", "entries": [last.entry_id], "k": None},
            "lineage": [last.entry_id]}))
        Stores().for_model(r.VerifierCandidate).put(composite)
        entry = r.LibraryEntry(**(parent.model_dump(exclude={"entry_id"}) | {
            "candidate_id": composite.candidate_id, "parents": [last.entry_id]}))
        Stores().for_model(r.LibraryEntry).put(entry)
        dependants.append(entry)
        last = entry
    result = gate.withdraw(parent.entry_id, Library(), reason="upheld owner dispute", person="Fixture person")
    assert result.verdict == "withdraw"
    current = {e.candidate_id: e for e in Library().list()}
    for old in dependants:
        assert current[old.candidate_id].status == "demoted"
        assert current[old.candidate_id].authority == "A0"
        assert Stores().for_model(r.LibraryEntry).get(old.entry_id) == old


def test_observed_health_defeater_withdraws_probation(rig, monkeypatch):
    gate, candidate, _, _, _ = rig
    gate.decide(candidate, Library(), "a" * 64)
    entry = Library().list()[0]
    monkeypatch.setattr(gate, "_health", lambda candidate: "weekly cost bound exceeded")
    result = gate.recheck(entry.entry_id, Library(), "a" * 64)
    assert result.verdict == "withdraw"
    assert Library().list()[0].status == "withdrawn"
    assert Library().list()[0].authority == "A0"


def test_own_suite_drift_withdraws_instead_of_retaining_authority(rig, monkeypatch):
    gate, candidate, _, _, _ = rig
    gate.decide(candidate, Library(), "a" * 64)
    entry = Library().list()[0]
    monkeypatch.setattr(gate, "_run", lambda *a: "no_concern")
    assert gate.recheck(entry.entry_id, Library(), "a" * 64).verdict == "withdraw"
    assert Library().by_status("probation") == []


@pytest.mark.parametrize("evidence", ["self_model_prediction", "proposer_prediction"])
def test_predictions_cannot_be_offered_as_gate_evidence(rig, evidence):
    gate, candidate, _, _, _ = rig
    with pytest.raises(TypeError):
        gate.decide(candidate, Library(), "a" * 64, evidence={"kind": evidence, "success": True})


def test_missing_owner_anchor_blocks_without_case_access(monkeypatch):
    from g6.distill import acceptance_gate as gate
    candidate = r.VerifierCandidate(**bodies()[3][1])
    result = gate.decide(candidate, Library(), "f" * 64)
    assert result.verdict == "block" and "anchor" in " ".join(result.reasons)


def test_judging_paths_and_proposer_import_graph_are_protected():
    import ast
    from g6.core.adaptation_gate import protected_hits
    root = Path(__file__).resolve().parents[2]
    judging = ["src/g6/distill/acceptance_gate.py", "validation/distill/held_out/policy.json"]
    assert protected_hits(judging) == judging
    for path in (root / "src/g6/distill").glob("*.py"):
        if path.name == "acceptance_gate.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "acceptance_gate" not in (node.module or "")
                assert all(a.name != "acceptance_gate" for a in node.names)
    tree = ast.parse((root / judging[0]).read_text(encoding="utf-8"))
    imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any("self_model" in n or n.endswith("lessons") for n in imports)
