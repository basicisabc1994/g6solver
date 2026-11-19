"""Regressions for reservation identity, publication order and evidence populations."""
import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import LedgerError
from g6.distill import acceptance_gate as g
from g6.distill import records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from . import test_acceptance_repair as repair
from .test_records import bodies
from .test_replacement_gate import replacement_sample


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


BINDINGS = [
    "VALUE = 3", "VALUE: int = 3", "VALUE += 1", "VALUE, other = (3, 4)",
    "for VALUE in []: pass", "with open('x') as VALUE: pass",
    "try:\n pass\nexcept ValueError as VALUE:\n pass",
    "match 1:\n  case VALUE: pass", "match []:\n  case [*VALUE]: pass",
    "match {}:\n  case {**VALUE}: pass", "import thing as VALUE", "from thing import x as VALUE",
    "import VALUE.child", "def VALUE(): pass", "class VALUE: pass", "global VALUE",
    "nonlocal VALUE", "(VALUE := 3)", "[VALUE for VALUE in []]",
    "{VALUE for VALUE in []}", "{VALUE: 1 for VALUE in []}", "(VALUE for VALUE in [])",
    "def inner(VALUE): pass", "del VALUE",
]


@pytest.mark.parametrize("binding", BINDINGS)
@pytest.mark.parametrize("name", ["constant", "function"])
def test_n1_rejects_every_shadow_binding(binding, name):
    declaration = "VALUE = 2" if name == "constant" else "def VALUE(): return 2"
    use = "VALUE" if name == "constant" else "VALUE()"
    body = "\n".join(" " + line for line in binding.splitlines())
    before = f"{declaration}\ndef test_a():\n{body}\n assert {use} == 2\n"
    after = f"{declaration}\ndef test_b():\n{body}\n assert {use} == 2\n"
    ast.parse(before)
    assert not g._normal_equivalent(before, after)


def test_exception_capture_cannot_be_inlined():
    before = ("VALUE=2\ndef test_a():\n try:\n  raise ValueError(answer(1))\n"
              " except ValueError as VALUE:\n  assert str(VALUE)=='2'\n")
    after = before.replace("str(VALUE)", "str(2)")
    assert not g._normal_equivalent(before, after)


def test_add_reservation_cannot_be_hidden_by_replacement_starts(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_look_available", REAL_LOOK)
    monkeypatch.setattr(g, "_spend", REAL_SPEND)
    monkeypatch.setattr(g, "_record", REAL_RECORD)
    def assessment(*args):
        clock[0] += 20000.
        raise ValueError("synthetic add refusal")
    monkeypatch.setattr(g, "_assess", assessment)
    assert g.decide(candidate, Library(), frozen.registration_sha256).verdict == "refuse"
    for _ in range(2):
        answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
        assert answer.verdict in {"refuse", "block"}, answer.reasons
        assert "cumulative cost" in " ".join(answer.reasons)
    rows = g._journal().entries()
    assert sum(row["kind"] == "reserve" for row in rows) == 3


REAL_LOOK, REAL_SPEND, REAL_RECORD = g._look_available, g._spend, g._record
REAL_PERSIST, REAL_LIBRARY_LIST = g._persist, Library.list
REAL_STAGE, REAL_PUBLISH = g._stage_replacement, g._publish_replacement


def persisted_rig(sample, monkeypatch, tmp_path):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(g, "Stores", Stores)
    monkeypatch.setattr(Library, "list", REAL_LIBRARY_LIST)
    monkeypatch.setattr(g, "_persist", REAL_PERSIST)
    monkeypatch.setattr(g, "_stage_replacement", REAL_STAGE)
    monkeypatch.setattr(g, "_publish_replacement", REAL_PUBLISH)
    for cls, body in bodies()[:3]:
        Stores().for_model(cls).put(cls(**body))
    Stores().for_model(r.VerifierCandidate).put(candidate)
    return candidate, frozen, clock


@pytest.mark.parametrize("work", ["persist", "report", "archive"])
def test_activation_work_can_exhaust_savings(sample, monkeypatch, tmp_path, work):
    candidate, frozen, clock = persisted_rig(sample, monkeypatch, tmp_path)
    if work == "persist":
        def persist(*args, **kwargs):
            answer = REAL_PERSIST(*args, **kwargs)
            clock[0] += 20000.
            return answer
        monkeypatch.setattr(g, "_persist", persist)
    elif work == "report":
        original = g._save_report
        def report(data):
            result = original(data)
            clock[0] += 20000.
            return result
        monkeypatch.setattr(g, "_save_report", report)
    else:
        original = Path.open
        def archive(path, *args, **kwargs):
            if path.parent.name == "regression_cases":
                clock[0] += 20000.
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", archive)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict in {"refuse", "block"}, answer.reasons
    assert not g._active(Library().list())


def test_completion_failure_leaves_persisted_library_empty(sample, monkeypatch, tmp_path):
    candidate, frozen, _ = persisted_rig(sample, monkeypatch, tmp_path)
    original = g._append
    def append(journal, kind, body):
        if kind == "replacement_attempt" and body["phase"] == "assessment":
            raise LedgerError("synthetic completion append failure")
        return original(journal, kind, body)
    monkeypatch.setattr(g, "_append", append)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "block", answer.reasons
    assert not g._active(Library().list())


def test_real_loader_keeps_tuning_and_regression_out_of_fresh_evidence(sample, monkeypatch, tmp_path):
    from g6.distill.held_out import oracle, registration_gate, sealed_gate
    candidate, frozen = replacement_sample(sample, monkeypatch)
    monkeypatch.setattr(g, "_load", REAL_LOAD)
    tuning = frozen.acceptance[0].case
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) |
        {"training_case_ids": [tuning.case_id]}))
    fresh = tuple(o for o in frozen.acceptance if o.case.case_id != tuning.case_id)
    receipt = frozen.replacement.model_copy(update={"candidate_id": candidate.candidate_id})
    registration = SimpleNamespace(cases=[SimpleNamespace(case_id=o.case.case_id, split=o.split)
        for o in (*fresh, *frozen.monitor)] + [SimpleNamespace(case_id=tuning.case_id, split="tuning")],
        study={"family": candidate.contract.family, "acceptance_policy_sha256": "p",
               "acceptance_code_sha256": "j", "acceptance_controls": frozen.controls},
        policy={}, operator_digests={"held_out": "c" * 64})
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(g, "_policy", lambda: (g.EvidencePolicy(), {"registrations": {
        frozen.registration_sha256: {"path": "validation/distill/synthetic.json"}}}))
    monkeypatch.setattr(g, "_policy_digest", lambda: "p")
    monkeypatch.setattr(g, "_judging_digest", lambda: "j")
    monkeypatch.setattr(g, "_chronology", lambda *a: None)
    monkeypatch.setattr(g, "_plain", lambda path, root: path)
    monkeypatch.setattr(g, "_registered_replacement", lambda *a, **kw: receipt)
    concept = SimpleNamespace(family=candidate.contract.family, from_failures=[])
    monkeypatch.setattr(g, "Stores", lambda: SimpleNamespace(for_model=lambda cls:
        SimpleNamespace(get=lambda ident: concept)))
    monkeypatch.setattr(registration_gate, "read_registration", lambda *a, **kw: registration)
    cases = {o.case.case_id: o.case for o in (*frozen.acceptance, *frozen.monitor)}
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: g.canonical(cases[path.stem].model_dump(mode="json")).encode()
                        if path.parent.name == "cases" else original(path))
    monkeypatch.setattr(oracle, "label", lambda case, **kw: SimpleNamespace(label="positive",
        model_dump=lambda **kw: {"case_id": case.case_id, "label": "positive"}))
    monkeypatch.setattr(sealed_gate, "acceptance_observations", lambda *a, **kw: [])
    library = SimpleNamespace(list=lambda: [SimpleNamespace(authority="A1",
        regression_suite=[SimpleNamespace(case_ref=tuning.case_id)])])
    loaded = g._load(frozen.registration_sha256, candidate, library, True)
    assert tuning.case_id not in {o.case.case_id for o in (*loaded.acceptance, *loaded.monitor)}
    assert tuning.case_id in {o.case.case_id for o in loaded.regression}
    assert tuning.case_id in {o.case.case_id for o in loaded.tuning}
    monkeypatch.setattr(g, "_headline", lambda *a: (_ for _ in ()).throw(g.Inconclusive("reached fresh evidence")))
    with pytest.raises(g.Inconclusive, match="reached fresh evidence"):
        g._assess_replacement(candidate, [], loaded, 1)


REAL_LOAD = g._load

@pytest.mark.parametrize("failure", ["slow_prepare", "completion"])
def test_activation_never_calls_live_persistence_before_final_cost(sample, monkeypatch, failure):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    published = []
    def persist(c, answer, *args, **kwargs):
        if not kwargs.get("prepare"):
            published.append(c.candidate_id)
        if failure == "slow_prepare":
            clock[0] += 20000.
        return answer
    monkeypatch.setattr(g, "_persist", persist)
    original = g._append
    def append(journal, kind, body):
        if failure == "completion" and kind == "replacement_attempt" and body["phase"] == "assessment":
            raise LedgerError("synthetic completion append failure")
        return original(journal, kind, body)
    monkeypatch.setattr(g, "_append", append)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict in {"refuse", "block"}
    assert published == []


def test_recovered_add_cost_is_bound_and_charged_on_retry(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    journal = g._journal()
    reservation = g._append(journal, "reserve", {"registration": frozen.registration_sha256,
        "candidate_id": candidate.candidate_id, "reuse_index": 1})
    g._append(journal, "decision", {"reservation": reservation, "verdict": "refuse"})
    body = {"registration": frozen.registration_sha256, "candidate_id": candidate.candidate_id,
            "phase": "assessment"}
    meter = g._append(journal, "replacement_attempt_start", body)
    g._append(journal, "replacement_attempt", body | {"attempt": meter, "validation_s": 20000.})
    g._append(journal, "reservation_meter_recovery", {"registration": frozen.registration_sha256,
        "reservation": reservation, "meter": meter})
    monkeypatch.setattr(g, "_look_available", REAL_LOOK)
    monkeypatch.setattr(g, "_spend", REAL_SPEND)
    monkeypatch.setattr(g, "_record", REAL_RECORD)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse", answer.reasons
    assert "cumulative cost" in " ".join(answer.reasons)
    assert g._validation_total(frozen.registration_sha256, None) >= 20000.


@pytest.mark.parametrize("population", ["tuning", "regression"])
def test_training_overlap_guard_only_consumes_fresh_populations(sample, monkeypatch, population):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    training_case = frozen.acceptance[0].case.model_copy(update={"unit_id": "registered-training"})
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) |
        {"training_case_ids": [training_case.case_id]}))
    receipt = frozen.replacement.model_copy(update={"candidate_id": candidate.candidate_id, "rows": [
        *frozen.replacement.rows, g.TeacherObservation(case_id=training_case.case_id, repository="registered-training",
            teacher_verdict="concern", teacher_s=10, maintenance_delta_s=0)]})
    frozen = replace(frozen, replacement=receipt, **{population: (
        replace(frozen.acceptance[0], case=training_case),)})
    monkeypatch.setattr(g, "_headline", lambda *a: (_ for _ in ()).throw(g.Inconclusive("fresh population reached")))
    with pytest.raises(g.Inconclusive, match="fresh population reached"):
        g._assess_replacement(candidate, [], frozen, 1)


def test_missing_registered_activation_allowance_cannot_publish(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    frozen = replace(frozen, replacement=frozen.replacement.model_copy(update={"activation_allowance_s": None}))
    monkeypatch.setattr(g, "_load", lambda *a: frozen)
    monkeypatch.setattr(g, "_persist", lambda *a, **kw: pytest.fail("unregistered activation prepared"))
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse"
    assert "registered activation allowance" in " ".join(answer.reasons)


@pytest.mark.parametrize("work", ["final_prepare", "completion_append"])
def test_tail_overrun_requires_cost_recovery_before_retry(sample, monkeypatch, work):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    calls = [0]
    def persist(c, answer, *args, **kwargs):
        calls[0] += 1
        if work == "final_prepare" and calls[0] == 2:
            clock[0] += 20000.
        return answer
    monkeypatch.setattr(g, "_persist", persist)
    append = g._append
    def delayed(journal, kind, body):
        result = append(journal, kind, body)
        if work == "completion_append" and kind == "replacement_attempt" and body["phase"] == "assessment":
            clock[0] += 20000.
        return result
    monkeypatch.setattr(g, "_append", delayed)
    published = []
    monkeypatch.setattr(g, "_publish_replacement", lambda *a: published.append(True))
    first = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert first.verdict in {"refuse", "block"}, first.reasons
    assert not any(row["kind"] == "replacement_state" and row["body"]["state"] == "active"
                   for row in g._journal().entries())
    second = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert second.verdict in {"refuse", "block"}, second.reasons
    assert "cumulative cost" in " ".join(second.reasons)


def test_activation_follows_completed_outer_meter_and_final_cost(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    published = []
    def persist(c, answer, evidence, own, frozen, data, library, **kwargs):
        clock[0] += .25
        assert data["replacement"]["state"] == "pending"
        return answer
    monkeypatch.setattr(g, "_persist", persist)
    def publish(*args):
        rows = g._journal().entries()
        assert not any(row["kind"] == "registration_meter_closed" for row in rows)
        published.append(candidate.candidate_id)
    monkeypatch.setattr(g, "_publish_replacement", publish)
    append = g._append
    def activate(journal, kind, body):
        if kind == "replacement_state" and body["state"] == "active":
            assert journal.entries()[-1]["kind"] == "registration_meter_closed"
            assert body["report"]["replacement"]["cost"]["upper_95"] < 0
            assert body["report"]["replacement"]["validation_s"] >= g._validation_total(frozen.registration_sha256, None)
        return append(journal, kind, body)
    monkeypatch.setattr(g, "_append", activate)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation", answer.reasons
    assert published == [candidate.candidate_id] * 2


@pytest.mark.parametrize("invalid", ["different_candidate", "shared_meter", "different_registration"])
def test_reservation_requires_its_own_matching_meter(sample, monkeypatch, invalid):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    registration = frozen.registration_sha256
    other = "f" * 64
    body = {"registration": other if invalid == "different_registration" else registration,
            "candidate_id": other if invalid == "different_candidate" else candidate.candidate_id,
            "phase": "assessment"}
    journal = g._journal()
    meter = g._append(journal, "replacement_attempt_start", body)
    g._append(journal, "replacement_attempt", body | {"attempt": meter, "validation_s": 20000.})
    for index in range(2 if invalid == "shared_meter" else 1):
        g._append(journal, "reserve", {"registration": registration, "candidate_id": candidate.candidate_id,
                                      "meter": meter, "reuse_index": index + 1})
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(registration, None)


def test_tuning_still_participates_in_dev_monitor_fpr(sample, monkeypatch):
    candidate, frozen = replacement_sample(sample, monkeypatch)
    tuning, traces = [], list(frozen.replacement.rows)
    for index, original in enumerate(frozen.acceptance[:100]):
        case = original.case.model_copy(update={"unit_id": f"tuning-negative-{index}"})
        observation = replace(original, case=case, split="tuning", label="negative",
            oracle_result=original.oracle_result | {"case_id": case.case_id, "label": "negative"})
        tuning.append(observation)
        traces.append(g.TeacherObservation(case_id=case.case_id, repository=case.unit_id,
            teacher_verdict="no_concern", teacher_s=10, maintenance_delta_s=0))
    frozen = replace(frozen, tuning=tuple(tuning), replacement=frozen.replacement.model_copy(update={"rows": traces}))
    monkeypatch.setattr(g, "_negative_justification", lambda *a: "n1")
    with pytest.raises(ValueError, match="dev plus monitor.*FPR"):
        g._assess_replacement(candidate, [], frozen, 1)


def test_top_level_function_cannot_rebind_inlined_constant():
    before = "VALUE=2\ndef VALUE(): return 3\ndef test_a(): assert VALUE()==3\n"
    after = before.replace("test_a", "test_b")
    assert not g._normal_equivalent(before, after)
