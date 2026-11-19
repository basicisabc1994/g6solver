"""Regression cases for the P2 review classes."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from g6.distill import acceptance_gate as g
from . import test_replacement_gate as replacement_fixtures
from .test_replacement_gate import public_rig, replacement_sample


@pytest.fixture
def sample():
    return replacement_fixtures.sample.__wrapped__()


def clustered(groups):
    observations, rows, values = [], {}, []
    for repository, group in enumerate(groups):
        for value in group:
            ident = str(len(values))
            observations.append(SimpleNamespace(case=SimpleNamespace(case_id=ident, unit_id=ident), label="positive"))
            rows[ident] = SimpleNamespace(repository=str(repository))
            values.append(value)
    return observations, rows, values


def test_interval_keeps_bug_weights_with_unequal_repositories():
    observations, rows, values = clustered([[1.] * 100] + [[0.]] * 199)
    bound = g._margin_bound(g._cluster_differences(observations, rows, values), .02, .05)
    assert bound["point"] == pytest.approx(100 / 299)
    assert bound["upper"] >= bound["point"] > .02


def test_quality_rejects_unequal_repository_fn_breach():
    observations, rows, values = clustered([[1.] * 100] + [[0.]] * 199 + [[0.]] * 300)
    for o in observations[299:]:
        o.label = "negative"
    teacher = ["concern"] * 299 + ["no_concern"] * 300
    own = ["no_concern"] * 100 + teacher[100:]
    with pytest.raises(ValueError, match="FN difference breach"):
        g._replacement_quality(observations, rows, teacher, teacher, own, own, [True] * 599, .01)


def test_cost_keeps_task_weights_with_concentrated_expense():
    observations, rows, values = clustered([[-10.] * 1000] + [[9.5]] * 199)
    result = g._replacement_cost(0., g._cluster_differences(observations, rows, values), value_bounds=(-10., 10.))
    assert result["point"] == pytest.approx(1000 * 8109.5 / 1199)
    assert not result["admissible"]


def test_repository_replication_retains_fpr_uncertainty():
    observations, rows, values = clustered([[1.]] * 3 + [[0.]] * 197)
    initial = g._margin_bound(g._cluster_differences(observations, rows, values), .02, .05)
    many_o, many_r, many_v = clustered([[1.] * 100] * 3 + [[0.] * 100] * 197)
    repeated = g._margin_bound(g._cluster_differences(many_o, many_r, many_v), .02, .05)
    assert repeated["point"] == pytest.approx(.015)
    assert repeated["upper"] == pytest.approx(initial["upper"])
    assert repeated["upper"] > .026


@pytest.mark.parametrize("stage", ["admission", "monitor"])
def test_replacement_fpr_never_uses_unit_independence(sample, monkeypatch, stage):
    candidate, frozen = replacement_sample(sample, monkeypatch, scope=[])
    monkeypatch.setattr(g, "_replacement_quality", lambda *a: ({}, .001))
    calls = [0]
    paired = g._paired
    def independent(*a):
        calls[0] += 1
        if stage == "admission" or calls[0] == 2:
            pytest.fail(f"{stage} FPR used unit independence")
        return paired(*a)
    monkeypatch.setattr(g, "_paired", independent)
    try:
        g._assess_replacement(candidate, [], frozen, 1)
    except g.Inconclusive:
        pass


def test_zero_differences_cannot_exclude_two_percent_null():
    bound = g._margin_bound([0.] * 100, .02, .05)
    assert bound["upper"] > .02
    assert bound["p_value"] >= .98 ** 100
    assert bound["lower"] < bound["upper"]


def test_two_repositories_are_inconclusive():
    with pytest.raises(g.Inconclusive, match="repositories"):
        g._margin_bound([0., 0.], .02, .05)


@pytest.mark.parametrize("binding", ["from subject import value as VALUE", "import subject as VALUE",
                                    "import VALUE.child", "from subject import *"])
def test_n1_rejects_import_shadowed_constant(binding):
    before = f"VALUE=1\n{binding}\ndef test_a(): assert VALUE==1\n"
    after = f"{binding}\ndef test_a(): assert 1==1\n"
    assert not g._normal_equivalent(before, after)


def test_missing_scope_refuses_before_measurement(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    frozen = replace(frozen, replacement=frozen.replacement.model_copy(update={"scope": None}))
    monkeypatch.setattr(g, "_headline", lambda *a: pytest.fail("admission labels read without scope"))
    with pytest.raises(ValueError, match="explicit.*scope"):
        g._assess_replacement(candidate, [], frozen, 1)


def test_loader_checks_registered_scope_before_case_access(sample, monkeypatch):
    from pathlib import Path
    from g6.distill.held_out import registration_gate
    _, candidate, base, _ = sample
    # Keep the real loader; only registration storage is synthetic here.
    from .test_replacement_gate import study
    receipt = study(sample, scope=None)
    protocol_keys = {"teacher", "teacher_model", "topology", "normalization", "horizon", "shadow_rate",
                     "shadow_seed", "scope", "cpu_weight", "teacher_weight", "task_saving_bounds", "activation_allowance_s"}
    registration = SimpleNamespace(cases=[SimpleNamespace(split="tuning", case_id=ident)
                                         for ident in candidate.training_case_ids], study={"family": candidate.contract.family,
        "acceptance_policy_sha256": "p", "acceptance_code_sha256": "j",
        "replacement_protocol_sha256": g.digest(receipt.model_dump(mode="json", include=protocol_keys))})
    monkeypatch.setattr(g, "_policy", lambda: (g.EvidencePolicy(), {"registrations": {
        base.registration_sha256: {"path": "validation/distill/synthetic-registration.json"}}}))
    monkeypatch.setattr(g, "_policy_digest", lambda: "p")
    monkeypatch.setattr(g, "_judging_digest", lambda: "j")
    monkeypatch.setattr(g, "_chronology", lambda *a: None)
    monkeypatch.setattr(g, "_plain", lambda path, root: path)
    concept = SimpleNamespace(family=candidate.contract.family, from_failures=[])
    monkeypatch.setattr(g, "Stores", lambda: SimpleNamespace(for_model=lambda cls: SimpleNamespace(get=lambda ident: concept)))
    monkeypatch.setattr(registration_gate, "read_registration", lambda *a, **kw: registration)
    def read(path):
        if path.name == candidate.candidate_id + ".json" and path.parent.name == "replacement":
            return g.canonical(receipt.model_dump(mode="json")).encode("utf-8")
        pytest.fail("loader touched admission storage without registered scope")
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ValueError, match="explicit.*scope"):
        g._load(base.registration_sha256, candidate, SimpleNamespace(list=lambda: []), True)


def meter_rig(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch, scope=[])
    public_rig(candidate, frozen, monkeypatch)
    journal = g.ChainLedger()
    monkeypatch.setattr(g, "_journal", lambda: journal)
    clock = [0.]
    monkeypatch.setattr(g.time, "monotonic", lambda: clock[0])
    def measure(c, observations, entries):
        return ["concern" if o.label == "positive" else "no_concern" for o in observations], [.001] * (3 * len(observations))
    monkeypatch.setattr(g, "_measure", measure)
    # These tests isolate metering from the independently tested intervals.
    monkeypatch.setattr(g, "_replacement_quality", lambda *a: ({}, .001))
    monkeypatch.setattr(g, "_persist", lambda c, answer, *a, **kw: answer)
    return candidate, frozen, clock


def test_oracle_load_time_is_charged(sample, monkeypatch):
    candidate, frozen, clock = meter_rig(sample, monkeypatch)
    def load(*a):
        clock[0] += 20000.
        return frozen
    monkeypatch.setattr(g, "_load", load)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse", answer.reasons
    assert "cost" in " ".join(answer.reasons)
    costs = [r["body"] for r in g._journal().entries() if r["kind"] == "replacement_attempt"]
    assert sum(r["validation_s"] for r in costs) == 20000.


def test_refused_attempt_cost_survives_retry(sample, monkeypatch):
    candidate, frozen, clock = meter_rig(sample, monkeypatch)
    calls = [0]
    def load(*a):
        calls[0] += 1
        if calls[0] == 1:
            clock[0] += 20000.
            raise ValueError("synthetic label refusal")
        return frozen
    monkeypatch.setattr(g, "_load", load)
    first = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert first.verdict in {"refuse", "block"}
    second = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert second.verdict == "refuse", second.reasons
    assert "cost" in " ".join(second.reasons)
    costs = [r["body"] for r in g._journal().entries() if r["kind"] == "replacement_attempt"]
    assert sum(r["validation_s"] for r in costs) == 20000.


def test_exception_persists_attempt_cost(sample, monkeypatch):
    candidate, frozen, clock = meter_rig(sample, monkeypatch)
    def load(*a):
        clock[0] += 7.
        raise RuntimeError("synthetic interrupted load")
    monkeypatch.setattr(g, "_load", load)
    with pytest.raises(RuntimeError, match="interrupted load"):
        g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    costs = [r["body"] for r in g._journal().entries() if r["kind"] == "replacement_attempt"]
    assert [r["validation_s"] for r in costs] == [7.]


def test_activation_reconciles_both_measurements_once(sample, monkeypatch):
    candidate, frozen, clock = meter_rig(sample, monkeypatch)
    measure = g._measure
    def timed_measure(*args):
        clock[0] += 2.
        return measure(*args)
    monkeypatch.setattr(g, "_measure", timed_measure)
    captured = []
    def persist(c, answer, evidence, own, frozen, data, library, **kw):
        captured.append(data["replacement"])
        return answer
    monkeypatch.setattr(g, "_persist", persist)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation", answer.reasons
    receipts = [row["body"] for row in g._journal().entries() if row["kind"] == "replacement_state"]
    assert receipts[-1]["report"]["replacement"]["validation_s"] == 10.
    costs = [r["body"] for r in g._journal().entries() if r["kind"] == "replacement_attempt"]
    assert [r["validation_s"] for r in costs] == [8.]


def test_attempt_completion_uses_current_journal_after_decision_rows(sample, monkeypatch):
    candidate, frozen, _ = meter_rig(sample, monkeypatch)
    backing = g.ChainLedger()
    class Snapshot:
        def __init__(self):
            self.rows = backing.entries()

        def entries(self):
            return self.rows[:]

        def append(self, kind, ident, body):
            if self.rows != backing.entries():
                raise ValueError("journal changed since this view opened")
            backing.append(kind, ident, body)
            self.rows = backing.entries()

    monkeypatch.setattr(g, "_journal", Snapshot)
    def spend(registration, c, meter=None):
        ident = g._append(g._journal(), "reserve", {"registration": registration,
            "candidate_id": c.candidate_id, "meter": meter})
        return 1, ident
    def record(answer, reservation=""):
        g._append(g._journal(), "decision", {"reservation": reservation, "verdict": answer.verdict})
        return answer
    monkeypatch.setattr(g, "_spend", spend)
    monkeypatch.setattr(g, "_record", record)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation", answer.reasons
    assert len([r for r in backing.entries() if r["kind"] == "replacement_attempt"]) == 1


def test_unclosed_meter_blocks_cost_reconciliation(sample, monkeypatch):
    candidate, frozen, _ = meter_rig(sample, monkeypatch)
    g._append(g._journal(), "replacement_attempt_start", {"registration": frozen.registration_sha256,
        "candidate_id": candidate.candidate_id, "phase": "assessment"})
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse", answer.reasons
    assert "owner cost recovery" in " ".join(answer.reasons)


def test_task_cost_counts_each_task_within_a_bug():
    observations, rows, values = clustered([[1., -1.]] + [[1.]] * 99)
    observations[1].case.unit_id = observations[0].case.unit_id
    result = g._replacement_cost(0., g._cluster_differences(observations, rows, values), value_bounds=(-2., 2.))
    assert result["per_task_saving"] == pytest.approx(99 / 101)


def test_balanced_accuracy_retains_equal_class_weights_across_repositories():
    observations, rows, _ = clustered([[0.]] * 200 + [[0.]] * 400)
    for o in observations[200:]:
        o.label = "negative"
    # Within-scope quality is unchanged; graph loss is 10/200 positives and 4/400 negatives.
    teacher = ["concern"] * 200 + ["no_concern"] * 400
    before = ["no_concern"] * 200 + ["concern"] * 400
    after = before[:]
    after[:10] = ["concern"] * 10
    after[200:204] = ["no_concern"] * 4
    bounds, _ = g._replacement_quality(observations, rows, teacher, before, after, teacher, [True] * 600, .01)
    assert bounds["graph_loss"]["point"] == pytest.approx(-.03)


def test_add_certificate_body_keeps_original_bytes(sample, monkeypatch):
    _, candidate, frozen, _ = sample
    answer, evidence, own, data = g._assess(candidate, [], frozen, 1)
    class Store:
        def get(self, ident):
            raise KeyError(ident)

        def put(self, value):
            pass

        def list(self):
            return []

    monkeypatch.setattr(g, "Stores", lambda: SimpleNamespace(for_model=lambda cls: Store()))
    reports = []
    def save(body):
        reports.append(g.canonical(body) + "\n")
        return g.digest(body)
    monkeypatch.setattr(g, "_save_report", save)
    g._persist(candidate, answer, evidence, own, replace(frozen, acceptance=()), data,
               SimpleNamespace(list=lambda: []))
    expected = ('{"authority":"A1","candidate":"' + candidate.candidate_id
                + '","gate":"distill-acceptance-v1","report":"' + answer.report_sha256
                + '","scope":"checked on the registered held-out cases; not proof"}\n')
    assert reports[-1].encode("utf-8") == expected.encode("utf-8")
