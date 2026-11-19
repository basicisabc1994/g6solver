# SPDX-License-Identifier: Apache-2.0
"""Pure gate measurements, independently runnable without OS temp/containment fixtures."""
from dataclasses import replace

import pytest

from g6.core.ledger import digest
from g6.distill import records as r
from .test_records import bodies


@pytest.fixture
def sample():
    from g6.distill import acceptance_gate as g
    from validation.distill.held_out.case_gate import CaseRecord, Provenance

    def observation(i, positive, *, false_alarm=False, split="acceptance"):
        before = f"def test_acceptance_{i}():\n    assert answer(1) == 2\n"
        after = f"def test_acceptance_{i}():\n    pass\n" if positive or false_alarm else before
        case = CaseRecord(unit_id=f"{split}-{i}", provenance=Provenance(source="maintainer", cohort="development",
            source_ref="synthetic-only", source_sha256=digest(i)), files={"answer.py": "def answer(x):\n return x+1\n"},
            target="answer.py", changed_lines=(2,), before_tests=before, after_tests=after,
            known_wrong=("def answer(x):\n return x\n",))
        return g.Observation(case, "positive" if positive else "negative", split)

    positive = tuple(observation(i, True) for i in range(100))
    negative = tuple(observation(i, False) for i in range(100, 200))
    monitor = tuple(observation(i, i < 300, split="monitor") for i in range(200, 400))
    frozen = g.EvaluationSet("a" * 64, "b" * 64, positive + negative, monitor, (),
                              (positive[0].case.case_id, negative[0].case.case_id))
    candidate = r.VerifierCandidate(**(bodies()[3][1] | {"form": "template_instance",
        "body": {"template": "assert_strength.v1", "bindings": {}}}))
    return g, candidate, frozen, observation


def test_real_template_measurement_earns_probation(sample):
    g, candidate, frozen, _ = sample
    result, evidence, _, report = g._assess(candidate, [], frozen, 1)
    assert result.verdict == "admit_probation"
    assert evidence.tp == 100 and evidence.fp == 0
    assert evidence.precision.lo95 > .90
    assert report["meta"]["n"] == 200
    assert report["resolution"]["minimum_detectable_precision_difference"] == result.minimum_detectable_precision_difference
    assert result.p_value <= g.EvidencePolicy().threshold


@pytest.mark.parametrize("verdict", ["concern", "no_concern"])
def test_both_constant_candidates_fail(sample, monkeypatch, verdict):
    g, candidate, frozen, _ = sample
    monkeypatch.setattr(g, "_run", lambda *a: verdict)
    with pytest.raises(ValueError, match="FPR|utility|control"):
        g._assess(candidate, [], frozen, 1)


def test_leaked_held_out_test_literal_refused_before_run(sample, monkeypatch):
    g, candidate, frozen, _ = sample
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "form": "predicate", "body": "def solve(facts):\n secret = 'test_acceptance_0'\n return {'verdict':'concern'}"}))
    monkeypatch.setattr(g, "_run", lambda *a: pytest.fail("leaked body executed"))
    with pytest.raises(ValueError, match="leak"):
        g._assess(candidate, [], frozen, 1)


def test_positive_utility_does_not_override_fpr_margin(sample):
    g, candidate, frozen, make = sample
    changed = frozen.acceptance[:100] + tuple(make(i, False, false_alarm=i < 110) for i in range(100, 200))
    with pytest.raises(ValueError, match="FPR"):
        g._assess(candidate, [], replace(frozen, acceptance=changed), 1)


def test_monitor_interference_refuses_even_after_acceptance_gain(sample):
    g, candidate, frozen, make = sample
    monitor = tuple(make(i, False, false_alarm=True, split="monitor") for i in range(100, 200))
    with pytest.raises(ValueError, match="monitor library FPR"):
        g._assess(candidate, [], replace(frozen, monitor=monitor), 1)


def test_spent_split_uses_existing_metaharness_limit(sample):
    g, candidate, frozen, _ = sample
    with pytest.raises(ValueError, match="metaharness refused.*HOLDOUT_REUSED"):
        g._assess(candidate, [], frozen, 6)


def test_case_count_is_by_bug_unit_not_operator_count(sample):
    g, candidate, frozen, _ = sample
    observations = tuple(replace(o, case=o.case.model_copy(update={"unit_id": "one-bug"})) for o in frozen.acceptance)
    with pytest.raises(ValueError, match="controls|INSUFFICIENT_EVIDENCE"):
        g._assess(candidate, [], replace(frozen, acceptance=observations), 1)


def test_flaky_detector_refused(sample, monkeypatch):
    g, candidate, frozen, _ = sample
    verdicts = iter(["concern", "no_concern", "concern"])
    monkeypatch.setattr(g, "_run", lambda *a: next(verdicts))
    with pytest.raises(ValueError, match="flaky"):
        g._measure(candidate, frozen.acceptance[:1], [])


def test_abstention_never_counts_as_negative_success(sample, monkeypatch):
    g, candidate, frozen, _ = sample
    monkeypatch.setattr(g, "_run", lambda *a: "abstain")
    with pytest.raises(ValueError, match="utility|control"):
        g._assess(candidate, [], frozen, 1)


def test_wilson_unknown_is_wide_and_promotion_bars_are_not_point_estimates(sample):
    g, _, _, _ = sample
    assert g._interval(0, 0).hi95 == 1
    assert g._interval(20, 20).lo95 < .85
    assert g._interval(30, 30).lo95 >= .85
    assert g._interval(0, 10).hi95 > .05


def test_predictive_fields_have_no_gate_evidence_parameter(sample):
    import inspect
    g, _, _, _ = sample
    assert set(inspect.signature(g.decide).parameters) == {"candidate", "library", "registration"}
    assert set(inspect.signature(g.promote).parameters) == {"entry_id", "library", "registration"}


def test_sealed_observation_reader_refuses_non_evaluator_before_input_access():
    from validation.distill.held_out import sealed_gate
    with pytest.raises(PermissionError, match="SEALED_ACCESS"):
        sealed_gate.acceptance_observations(None, None, None, policy=None, workspace=None, registration=None)


def test_predicate_imports_cannot_reach_gate_or_policy_writer():
    from g6.distillation.sandbox import check_source
    assert check_source("import g6.distill.acceptance_gate\ndef solve(facts):\n return None\n")
    assert check_source("def solve(facts):\n return open('validation/distill/held_out/policy.json', 'w')\n")


def test_variant_replication_cannot_narrow_precision_intervals(sample):
    g, _, frozen, _ = sample
    one = frozen.acceptance[0]
    # Many operator variants of the same bug are one independent observation.
    counts = g._counts((one,) * 100, ["concern"] * 100)
    assert counts["tp"] == 1
    assert g._interval(counts["tp"], counts["tp"] + counts["fp"]).lo95 < .90


def test_two_sided_rate_interval_contains_zero_and_one(sample):
    g, _, _, _ = sample
    for n in (1, 10, 30, 100, 1000):
        assert g._interval(0, n).lo95 == 0
        assert g._interval(n, n).hi95 == 1


def test_authority_thresholds_require_field_evidence_and_exact_demotion(sample):
    g, candidate, frozen, _ = sample
    evidence = g._assess(candidate, [], frozen, 1)[1]
    empty = r.FieldEvidence(fired=0, confirmed=0, refuted=0, unlabelled=0, precision=None)
    with pytest.raises(ValueError, match="field"):
        g._a2_bar(evidence, empty)
    assert g._demotion(2, 18, g.EvidencePolicy())
    assert not g._demotion(30, 0, g.EvidencePolicy())
    # Boundary: zero confirmations in one fire is p=.15; two is p=.0225.
    assert not g._demotion(0, 1, g.EvidencePolicy())
    assert g._demotion(0, 2, g.EvidencePolicy())


def test_batch_bh_uses_every_submitted_candidate_as_denominator(sample):
    g, _, _, _ = sample
    assert g._bh(((.01, "good"),), 10) == set()
    assert g._bh(((.001, "first"), (.009, "second")), 10) == {"first", "second"}


def test_field_prediction_and_unlabelled_payloads_cannot_become_labels(sample):
    g, _, _, _ = sample
    from pydantic import ValidationError
    for source in ("proposer", "self_model", "prediction"):
        with pytest.raises(ValidationError):
            g.FieldOutcome(candidate_id="a" * 64, fire_id="f", source=source,
                           label="confirmed", receipt_sha256="b" * 64)


def test_health_withdrawal_requires_independent_observation_and_prior_condition(sample):
    g, candidate, _, _ = sample
    # A cost breach is measured over the current week's actual runs only.
    assert g._health_bar(candidate, [2.] * 11 + [.1] * 89, ()) == "weekly cost bound exceeded"
    assert g._health_bar(candidate, [2.] * 10 + [.1] * 90, ()) is None
    assert g._health_bar(candidate, [], ("regression",)) == "pre-stated withdrawal condition observed"
    with pytest.raises(ValueError, match="pre-stated"):
        g._health_bar(candidate, [], ("invented",))


def test_registration_digest_is_independent_of_validation_import_spelling():
    import importlib.util
    from validation.distill.held_out import registration_gate as source
    # The cases slice's CLI/tests import distill from the validation path; the
    # product gate imports validation.distill from the checkout root.
    if importlib.util.find_spec("distill") is None:
        pytest.skip("validation package alias is installed by tests/validation/conftest.py")
    from distill.held_out import registration_gate as study
    assert source.oracle_digest() == study.oracle_digest()


def test_existing_entry_interference_changes_a_frozen_negative_verdict(sample, monkeypatch):
    g, candidate, frozen, make = sample
    old = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "body": r.TemplateInstance(template="skip_gate.v1", bindings={})}))
    negative = make(999, False, false_alarm=True, split="regression")
    entry = r.LibraryEntry(**(bodies()[4][1] | {"candidate_id": old.candidate_id,
        "status": "probation", "authority": "A1",
        "regression_suite": [{"case_ref": negative.case.case_id, "verdict": "no_concern"}]}))
    monkeypatch.setattr(g, "_candidate", lambda e: old)
    g._regress([entry], (negative,))
    with pytest.raises(ValueError, match="whole-library regression verdict changed"):
        g._regress([entry], (negative,), candidate)


def test_cost_breach_is_strictly_more_than_ten_percent(sample, monkeypatch):
    g, candidate, frozen, _ = sample
    # Each measurement has three runs. Exactly one run in ten cases (1/30)
    # exceeds the bound here; four runs (4/30) cross the 10% rule.
    for over, refused in ((3, False), (4, True)):
        ticks = iter(t for i in range(30) for t in (0., 2. if i < over else .1))
        monkeypatch.setattr(g.time, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(g, "_run", lambda *a: "concern")
        if refused:
            with pytest.raises(ValueError, match="cost bound exceeded"):
                g._measure(candidate, frozen.acceptance[:10], [])
        else:
            g._measure(candidate, frozen.acceptance[:10], [])


def test_training_case_is_never_accepted_as_held_out(sample):
    g, candidate, frozen, _ = sample
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "training_case_ids": [frozen.acceptance[0].case.case_id]}))
    with pytest.raises(ValueError, match="training"):
        g._assess(candidate, [], frozen, 1)


def test_registration_loader_blocks_unknown_anchor_before_touching_cases(sample):
    g, candidate, _, _ = sample
    with pytest.raises(ValueError, match="owner registration anchor"):
        g._load("f" * 64, candidate, None)


def test_proposer_import_graph_never_reaches_evaluator_or_threshold_writer():
    import ast
    from importlib.util import resolve_name
    from pathlib import Path
    from g6.core.adaptation_gate import protected_hits

    folder = Path(__file__).resolve().parents[2] / "src/g6/distill"
    graph = {}
    for path in folder.glob("*.py"):
        module = "g6.distill" if path.stem == "__init__" else f"g6.distill.{path.stem}"
        edges = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                edges.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = resolve_name("." * node.level + (node.module or ""), "g6.distill") if node.level else node.module
                edges.add(target)
                edges.update(f"{target}.{alias.name}" for alias in node.names)
        graph[module] = edges
    judging = {"g6.distill.acceptance_gate", "g6.distill.pilot_gate"}
    starts = set(graph) - judging
    reached = set()
    while starts:
        current = starts.pop()
        if current in reached:
            continue
        reached.add(current)
        starts.update(graph.get(current, set()))
    assert not judging.intersection(reached)
    assert not any("held_out" in name for name in reached)
    assert protected_hits(("src/g6/distill/acceptance_gate.py", "validation/distill/held_out/policy.json"))


def test_delayed_field_label_replaces_unlabelled_fire_without_double_counting(sample):
    g, candidate, _, _ = sample
    body = {"candidate_id": candidate.candidate_id, "fire_id": "fire-1", "source": "person",
            "label": "unlabelled", "receipt_sha256": "a" * 64}
    rows = [{"kind": "field", "body": body}, {"kind": "field", "body": body | {"label": "confirmed"}}]
    assert g._field_counts(rows, candidate.candidate_id) == (1, 0, 0)
    with pytest.raises(ValueError, match="field"):
        g._field_counts(rows + [rows[-1]], candidate.candidate_id)


def test_bypassed_candidate_validation_blocks_before_any_storage(sample):
    g, candidate, _, _ = sample
    forged = candidate.model_copy(update={"form": "predicate", "body": "not valid Python"})
    assert g.decide(forged, None, "a" * 64).verdict == "block"
