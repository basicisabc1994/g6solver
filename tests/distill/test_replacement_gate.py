# SPDX-License-Identifier: Apache-2.0
"""P2 admission cases with synthetic evaluator observations."""
from dataclasses import replace

import pytest

from g6.distill import acceptance_gate as g
from g6.distill import records as r
from . import test_acceptance_measurements as fixtures


@pytest.fixture
def sample():
    return fixtures.sample.__wrapped__()


def test_add_fixture_is_pinned(sample):
    _, candidate, frozen, _ = sample
    answer, evidence, own, data = g._assess(candidate, [], frozen, 1)
    assert answer.verdict == "admit_probation"
    assert (evidence.n_pos, evidence.n_neg, evidence.tp, evidence.fp, evidence.fn, evidence.abstain) == (100, 100, 100, 0, 0, 0)
    assert own == ["concern"] * 100 + ["no_concern"] * 100
    assert data["meta"]["success"] == {"wins": 100, "losses": 0, "ties": 100, "discordant": 100, "p_one_sided": 0.0}


def test_add_cost_only_tie_is_still_refused(sample, monkeypatch):
    _, candidate, frozen, _ = sample
    monkeypatch.setattr(g, "_library_verdicts", lambda entries, obs: ["concern" if o.label == "positive" else "no_concern" for o in obs])
    with pytest.raises(ValueError, match="no positive utility gain"):
        g._assess(candidate, [], frozen, 1)


def scoped(candidate, predicates):
    contract = r.MethodContract(**(candidate.contract.model_dump(exclude={"content_id"}) | {"applies_when": predicates}))
    return r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {"contract": contract}))


def test_scope_is_evaluated_and_outside_counts_as_abstain(sample):
    _, candidate, frozen, _ = sample
    candidate = scoped(candidate, [{"feature": "diff_hunks", "op": "eq", "value": {"tests/test_case.py": ""}}])
    own, _ = g._measure(candidate, frozen.acceptance, [])
    assert own[:100] == ["abstain"] * 100
    assert own[100:] == ["no_concern"] * 100
    assert g._counts(frozen.acceptance, own)["abstain"] == 100


def test_scope_type_mismatch_abstains(sample):
    _, candidate, frozen, _ = sample
    candidate = scoped(candidate, [{"feature": "assertions", "op": "gt", "value": 1}])
    assert g._run(candidate, frozen.acceptance[0].case, []) == "abstain"


def study(sample, **updates):
    _, _, frozen, _ = sample
    rows = [{"case_id": o.case.case_id, "repository": o.case.unit_id,
             "teacher_verdict": "concern" if o.label == "positive" else "no_concern",
             "teacher_s": 10., "maintenance_delta_s": .01} for o in (*frozen.acceptance, *frozen.monitor)]
    updates = {"scope": [], "task_saving_bounds": (-2., 12.)} | updates
    return g.ReplacementStudy(teacher="c" * 64, candidate_id=sample[1].candidate_id,
                              registration_sha256=frozen.registration_sha256,
                              shadow_seed="e" * 64,
                              fixed_new_s=10., fixed_base_s=0., rows=rows, **updates)


def replacement_sample(sample, monkeypatch, **updates):
    _, candidate, frozen, _ = sample
    def independent(group):
        return tuple(replace(o, case=o.case.model_copy(update={"unit_id": o.case.unit_id + f"-repo-{i}"}))
                     if i else o for label in ("positive", "negative") for i in range(3)
                     for o in group if o.label == label)
    frozen = replace(frozen, acceptance=independent(frozen.acceptance), monitor=independent(frozen.monitor))
    receipt = study((sample[0], candidate, frozen, sample[3]), **updates)
    def labelled(o):
        return replace(o, oracle_result={"case_id": o.case.case_id,
            "label": o.label, "reason": "known_wrong_witness" if o.label == "positive" else "negative",
            "wrong_witnesses": ["d" * 64] if o.label == "positive" else []})
    frozen = replace(frozen, acceptance=tuple(map(labelled, frozen.acceptance)),
                     monitor=tuple(map(labelled, frozen.monitor)), replacement=receipt)
    monkeypatch.setattr(g, "_load", lambda *a: frozen)
    return candidate, frozen


def test_cost_only_replacement_passes_separate_assessment(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    answer, _, _, data = g._assess_replacement(candidate, [], frozen, 1)
    assert answer.verdict == "admit_probation"
    assert data["replacement"]["cost"]["upper_95"] < 0
    assert data["replacement"]["coverage"]["point"] == 1
    assert answer.p_value <= g.EvidencePolicy().threshold


def test_low_coverage_refuses(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    monkeypatch.setattr(g, "_measure", lambda c, obs, e: (["abstain"] * len(obs), [0.] * (3 * len(obs))))
    with pytest.raises(ValueError, match="coverage"):
        g._assess_replacement(candidate, [], frozen, 1)


def test_fn_breach_refuses(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    original = g._measure
    def misses(c, obs, entries):
        verdicts, costs = original(c, obs, entries)
        return ["no_concern" if o.label == "positive" and i % 5 == 1 else v for i, (o, v) in enumerate(zip(obs, verdicts))], costs
    monkeypatch.setattr(g, "_measure", misses)
    with pytest.raises(ValueError, match="FN|non-inferiority"):
        g._assess_replacement(candidate, [], frozen, 1)


@pytest.mark.parametrize("fixed,saving,kind", [(-1, 0, "dominates"), (-1, 1, "dominates"),
    (10, .001, "break_even"), (10, 1, "break_even"), (10, 0, "never"), (10, -1, "never"),
    (-10, -1, "crossover"), (-1000, -1, "crossover"), (0, 0, "dominates"), (0, -1, "never")])
def test_cost_upper_bound_decides_every_sign_case(fixed, saving, kind):
    limit = abs(saving) + 1
    result = g._replacement_cost(fixed, [saving] * 100, value_bounds=(-limit, limit))
    assert result["kind"] == kind
    assert result["point"] == pytest.approx(fixed - 1000 * saving)
    assert result["upper_95"] > result["point"]
    assert result["admissible"] == (result["upper_95"] < 0)


def test_cost_upper_bound_can_refuse_favorable_point():
    result = g._replacement_cost(1, [-10., 10.1] * 50, value_bounds=(-11., 11.))
    assert result["point"] < 0 < result["upper_95"]
    assert not result["admissible"]


def test_evaluator_only_candidate_refused_before_execution(sample, monkeypatch):
    _, candidate, frozen, _ = sample
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "form": "predicate", "body": "def solve(facts):\n import g6.distill.held_out.oracle\n return {'verdict': 'abstain'}"}))
    monkeypatch.setattr(g, "_measure", lambda *a: pytest.fail("evaluator-only candidate executed"))
    with pytest.raises(ValueError, match="evaluator-only"):
        g._assess(candidate, [], frozen, 1)


@pytest.mark.parametrize("before,after,expected", [
    ("def test_a():\n assert 1 == 1\n", "def test_b():\n assert 1 == 1\n", True),
    ("def test_a():\n assert 1 == 1\n", "LIMIT = 1\ndef test_b():\n assert LIMIT == 1\n", True),
    ("def test_a():\n assert 1 == 1\n", "def test_a():\n pass\n", False),
    ("def test_a():\n assert test_a.__name__ == 'test_a'\n", "def test_b():\n assert test_b.__name__ == 'test_a'\n", False),
])
def test_n1_normal_form_is_narrow(before, after, expected):
    assert g._normal_equivalent(before, after) is expected


def test_n2_requires_every_wrong_body_to_settle_identically(sample, monkeypatch):
    _, _, frozen, _ = sample
    o = frozen.acceptance[-1]
    case = o.case.model_copy(update={"before_tests": "def test_old():\n pass\n", "after_tests": "def test_new():\n assert True\n"})
    from g6.distill.held_out import oracle
    monkeypatch.setattr(oracle, "_run", lambda c, source, suite, **kw: "failed")
    assert g._negative_justification(case, None) == "n2"
    monkeypatch.setattr(oracle, "_run", lambda c, source, suite, **kw: "failed" if suite == c.before_tests else "passed")
    assert g._negative_justification(case, None) == "shared_mechanism"
    monkeypatch.setattr(oracle, "_run", lambda *a, **kw: "unknown")
    assert g._negative_justification(case, None) == "shared_mechanism"


def test_inconclusive_has_no_authority(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    sparse = replace(frozen, acceptance=frozen.acceptance[:1] + frozen.acceptance[-1:])
    with pytest.raises(g.Inconclusive, match="minimum"):
        g._assess_replacement(candidate, [], sparse, 1)


def test_v2_probe_is_executed_with_registered_budget(sample, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from g6.distill import probes
    _, candidate, frozen, _ = sample
    contract = candidate.contract.model_dump(exclude={"content_id"}) | {
        "probes": [{"name": "kill_set.v2", "runs": 100, "wall_s": 60}],
        "cost_bound": {"wall_s": 60, "probe_runs": 100, "model_calls": 0}}
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "body": {"template": "kill_set.v2", "bindings": {}}, "contract": contract}))
    calls = []
    def run(*a, **kw):
        calls.append(kw)
        return SimpleNamespace(verdict="concern")
    monkeypatch.setattr(probes, "_run_probe", run)
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    assert g._run(candidate, frozen.acceptance[0].case, []) == "concern"
    assert calls[0]["cost_bound"] == {"wall_s": 60., "probe_runs": 100, "model_calls": 0}
    assert calls[0]["split"] == "acceptance"


def public_rig(candidate, frozen, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    monkeypatch.setattr(g, "_writer", nullcontext)
    monkeypatch.setattr(g, "_record", lambda answer, reservation="": answer)
    monkeypatch.setattr(g, "_load", lambda *a: frozen)
    monkeypatch.setattr(g, "_look_available", lambda *a: (None, 1))
    monkeypatch.setattr(g, "_spend", lambda *a: (1, "reservation"))
    journal = g.ChainLedger()
    monkeypatch.setattr(g, "_journal", lambda: journal)
    monkeypatch.setattr(g, "Stores", lambda: SimpleNamespace(for_model=lambda cls: SimpleNamespace(get=lambda ident: candidate)))
    monkeypatch.setattr(g.Library, "list", lambda self: [])
    monkeypatch.setattr(g, "_persist", lambda *a: pytest.fail("non-admission persisted authority"))


def test_public_inconclusive_differs_from_refuse(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    sparse = replace(frozen, acceptance=frozen.acceptance[:1] + frozen.acceptance[-1:])
    public_rig(candidate, sparse, monkeypatch)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "inconclusive"
    public_rig(candidate, frozen, monkeypatch)
    monkeypatch.setattr(g, "_measure", lambda c, obs, e: (["abstain"] * len(obs), [0.] * (3 * len(obs))))
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "refuse"


def test_registered_narrow_scope_can_admit_scoped(sample, monkeypatch):
    excluded = sample[2].acceptance[10].case
    facts = g._extract_facts({"tests/test_case.py": excluded.before_tests}, {"tests/test_case.py": excluded.after_tests})
    predicates = [r.FeaturePredicate(feature="diff_hunks", op="ne", value=facts["diff_hunks"])]
    candidate, frozen = replacement_sample(sample, monkeypatch, scope=predicates)
    answer, _, own, data = g._assess_replacement(candidate, [], frozen, 1)
    assert answer.verdict == "admit_scoped"
    assert own[10] == "abstain"
    assert data["replacement"]["coverage"]["point"] == .995


def test_missing_replacement_receipt_blocks(sample, monkeypatch):
    _, candidate, frozen, _ = sample
    public_rig(candidate, frozen, monkeypatch)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"


def test_shared_positives_do_not_enter_quality_headline(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    changed = tuple(replace(o, oracle_result=o.oracle_result | {"wrong_witnesses": []})
                    if o.label == "positive" else o for o in frozen.acceptance)
    with pytest.raises(g.Inconclusive, match="positive"):
        g._assess_replacement(candidate, [], replace(frozen, acceptance=changed), 1)


def test_shared_cases_cannot_inflate_coverage(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    own_measure = g._measure
    extra = tuple(replace(o, oracle_result=o.oracle_result | {"wrong_witnesses": []},
        case=o.case.model_copy(update={"unit_id": f"shared-{i}"})) for i, o in enumerate(frozen.acceptance[:300]))
    extra = tuple(replace(o, oracle_result=o.oracle_result | {"case_id": o.case.case_id}) for o in extra)
    rows = frozen.replacement.rows + [g.TeacherObservation(case_id=o.case.case_id, repository=o.case.unit_id,
        teacher_verdict="concern", teacher_s=10, maintenance_delta_s=0) for o in extra]
    frozen = replace(frozen, acceptance=(*frozen.acceptance, *extra),
                     replacement=frozen.replacement.model_copy(update={"rows": rows}))
    def measure(c, observations, entries):
        own, costs = own_measure(c, observations, entries)
        return ["abstain" if o.case.unit_id.startswith("shared-") else v for o, v in zip(observations, own)], costs
    monkeypatch.setattr(g, "_measure", measure)
    _, _, _, data = g._assess_replacement(candidate, [], frozen, 1)
    assert data["replacement"]["coverage"]["point"] == pytest.approx(2/3)


def test_abstention_falls_back_to_teacher_in_whole_graph(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    changed = []
    for i, row in enumerate(frozen.replacement.rows):
        changed.append(row.model_copy(update={"teacher_verdict": "no_concern" if i < 300 and i % 5 == 1 else row.teacher_verdict}))
    frozen = replace(frozen, replacement=frozen.replacement.model_copy(update={"rows": changed}))
    original = g._measure
    def misses(c, observations, entries):
        own, costs = original(c, observations, entries)
        return ["abstain" if o.label == "positive" and i % 5 == 1 else v for i, (o, v) in enumerate(zip(observations, own))], costs
    monkeypatch.setattr(g, "_measure", misses)
    assert g._assess_replacement(candidate, [], frozen, 1)[0].verdict == "admit_probation"


def test_probe_lost_and_gained_kills_abstain(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from g6.distill import probes
    fixed, lost, gained = "def f(): return 0", "def f(): return 1", "def f(): return 2"
    before, after = {"tests/test_case.py": "assert True"}, {"tests/test_case.py": "assert 1"}
    monkeypatch.setattr(probes.mutation_check, "mutants", lambda *a, **kw: [SimpleNamespace(source=s) for s in (lost, gained)])
    def stage(inputs, source, suite, **kw):
        failed = (source == lost and suite == before) or (source == gained and suite == after)
        return {"status": "failed" if failed else "passed", "isolation": {"backend": "synthetic", "network": False}}
    monkeypatch.setattr(probes, "_stage_run", stage)
    monkeypatch.setattr(probes.repo_tests, "established", lambda raw, **kw: SimpleNamespace(of=lambda: raw["status"]))
    instance = probes.ProbeTemplateInstance(template="kill_set.v2", bindings={"limit": 2, "min_settled": 2, "reruns": 1})
    kwargs = {"probe_input": {"files": {"subject.py": fixed}, "target": "subject.py", "changed_lines": [1]},
              "workspace": Path(__file__).resolve().parents[2] / ".build", "cost_bound": {"wall_s": 60, "probe_runs": 6}}
    result = probes._run_probe(instance, before, after, split="acceptance", **kwargs)
    assert result.verdict == "abstain"
    assert result.reason == "kills_lost_and_gained"
    assert result.provenance.split == "acceptance"
    with pytest.raises(ValueError, match="held-out"):
        probes.run_probe(instance, before, after, split="acceptance", **kwargs)


def test_scoped_entry_does_not_fire_outside_admitted_scope(sample, monkeypatch):
    from types import SimpleNamespace
    _, candidate, frozen, _ = sample
    monkeypatch.setattr(g, "_candidate", lambda entry: candidate)
    entry = SimpleNamespace(replacement="R", replacement_scope=[r.FeaturePredicate(
        feature="diff_hunks", op="eq", value={"tests/test_case.py": ""})])
    verdicts, _ = g._measure_entry(entry, frozen.acceptance, [])
    assert verdicts[:100] == ["abstain"] * 100
    assert verdicts[100:] == ["no_concern"] * 100


@pytest.mark.parametrize("before,after", [
    ("def pytest_runtest_setup():\n assert False\n", "def renamed():\n assert False\n"),
    ("def setup_module():\n assert False\n", "def renamed():\n assert False\n"),
])
def test_n1_does_not_rename_test_hooks(before, after):
    assert not g._normal_equivalent(before, after)


def test_variant_successes_cannot_dilute_unit_fn_difference(sample):
    _, _, frozen, _ = sample
    observations = list(frozen.acceptance)
    own = ["no_concern" if i < 10 else "concern" if i < 100 else "no_concern" for i in range(200)]
    for o in frozen.acceptance[:10]:
        observations.extend([o] * 100)
        own.extend(["concern"] * 100)
    teacher = ["concern" if o.label == "positive" else "no_concern" for o in observations]
    rows = {row.case_id: row for row in study(sample).rows}
    with pytest.raises(ValueError, match="FN"):
        g._replacement_quality(observations, rows, teacher, teacher, own, own, [True] * len(own), .01)


def test_base_probe_requires_bound_evaluator_inputs_and_executes(sample, monkeypatch):
    from types import SimpleNamespace
    from g6.distill import probes
    _, candidate, frozen, _ = sample
    contract = candidate.contract.model_dump(exclude={"content_id"}) | {
        "probes": [{"name": "discriminates_at_base.v2", "runs": 5, "wall_s": 60}],
        "cost_bound": {"wall_s": 60, "probe_runs": 5, "model_calls": 0}}
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "body": {"template": "discriminates_at_base.v2", "bindings": {}}, "contract": contract}))
    case = frozen.acceptance[0].case
    assert g._run(candidate, case, []) == "abstain"
    inputs = probes.ProbeInput(files=case.files, target=case.target, changed_lines=case.changed_lines,
        base_implementation=case.known_wrong[0], new_tests={"tests/test_case.py": case.after_tests})
    bound = g.ProbeCase(case, inputs)
    calls = []
    def run(*a, **kw):
        calls.append(kw)
        return SimpleNamespace(verdict="no_concern")
    monkeypatch.setattr(probes, "_run_probe", run)
    assert g._run(candidate, bound, []) == "no_concern"
    assert calls[0]["probe_input"]["base_implementation"] == case.known_wrong[0]


def test_shadow_schedule_seed_is_in_admission_receipt(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    data = g._assess_replacement(candidate, [], frozen, 1)[3]
    assert data["replacement"]["shadow_seed"] == frozen.replacement.shadow_seed


def test_registered_cpu_price_counts_probe_and_validation_cost(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch, cpu_weight=1e12)
    with pytest.raises(ValueError, match="cumulative cost"):
        g._assess_replacement(candidate, [], frozen, 1)


def test_small_unresolved_fn_difference_is_inconclusive(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    original = g._measure
    def measure(c, observations, entries):
        own, costs = original(c, observations, entries)
        own[10] = "no_concern"
        return own, costs
    monkeypatch.setattr(g, "_measure", measure)
    with pytest.raises(g.Inconclusive, match="FN bound"):
        g._assess_replacement(candidate, [], frozen, 1)


def test_public_replacement_passes_bh_without_superiority(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    public_rig(candidate, frozen, monkeypatch)
    captured = []
    def persist(candidate, answer, evidence, own, frozen, data, library):
        captured.append(data)
        return replace(answer, entry_id="f" * 64)
    monkeypatch.setattr(g, "_persist", persist)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation"
    assert answer.entry_id == "f" * 64
    assert captured[0]["replacement"]["teacher"] == "c" * 64
    assert captured[0]["replacement"]["validation_s"] > 0
