"""Registration boundary and authority regressions."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from g6.distill import acceptance_gate as g
from . import test_acceptance_repair as repair


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


@pytest.mark.parametrize("stage", ["writer", "post_seal", "release"])
def test_interval_charges_boundary_delay_and_retry(sample, monkeypatch, stage):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    calls = [0]
    @contextmanager
    def writer():
        calls[0] += 1
        if stage == "writer" and calls[0] == 1:
            clock[0] += 20000.
            raise ValueError("delayed writer acquisition")
        try:
            yield {"id": "lease", "retain": False}
        finally:
            if stage == "release" and calls[0] == 1:
                clock[0] += 20000.
    monkeypatch.setattr(g, "_writer", writer)
    total = g._validation_total
    delayed = [False]
    def read(registration, active):
        rows = g._journal().entries()
        if stage == "post_seal" and not delayed[0] and any(
                r["kind"] == "registration_meter_closed" for r in rows):
            delayed[0] = True
            clock[0] += 20000.
            raise ValueError("delayed post-seal read")
        return total(registration, active)
    monkeypatch.setattr(g, "_validation_total", read)
    first = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert first.verdict in {"block", "refuse"}, first.reasons
    assert total(frozen.registration_sha256, None) >= 20000.
    second = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert second.verdict in {"block", "refuse"}, second.reasons
    assert "cost" in " ".join(second.reasons)


def test_r4_accepts_exact_zero():
    cost = g._replacement_cost(0., [1.] * 100, value_bounds=(-2., 2.))
    fixed = -cost["upper_95"]
    at_zero = g._replacement_cost(fixed, [1.] * 100, value_bounds=(-2., 2.))
    assert at_zero["upper_95"] == 0.
    assert at_zero["admissible"]
    data = {"replacement": {"cost": at_zero}}
    study = SimpleNamespace(fixed_new_s=fixed, fixed_base_s=0., cpu_weight=1.)
    assert g._final_replacement_cost(data, study, 0.)["replacement"]["cost"]["admissible"]


@pytest.mark.parametrize("expression", ["hasattr(sys.modules[__name__], 'renamed')",
    "sys.modules", "globals()", "locals()", "vars(sys)", "getattr(sys, name)",
    "hasattr(sys, name)", "setattr(sys, name, 1)", "sys.__dict__", "inspect.currentframe()",
    "lookup(sys, 'renamed')"])
def test_n1_refuses_namespace_introspection(expression):
    imports = "import sys\n"
    if expression.startswith("inspect"):
        imports += "import inspect\n"
    if expression.startswith("lookup"):
        imports += "from builtins import hasattr as lookup\n"
    before = (imports +
              "def helper(): return 2\ndef test_a():\n"
              f" if answer(1)==0:\n  assert {expression}\n assert helper()==2\n")
    after = before.replace("def helper()", "def renamed()").replace("assert helper()", "assert renamed()")
    assert not g._normal_equivalent(before, after)


def test_regression_replays_guarded_candidate(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    observation = frozen.acceptance[0]
    entry = SimpleNamespace(authority="A2", status="admitted", replacement="none",
        regression_suite=[SimpleNamespace(case_ref=observation.case.case_id, verdict="no_concern")])
    monkeypatch.setattr(g, "_measure_entry", lambda *a: (["no_concern"], [0.]*3))
    monkeypatch.setattr(g, "_case_in_scope", lambda *a: False)
    monkeypatch.setattr(g, "_measure", lambda *a: (["concern"], [0.]*3))
    g._regress([entry], (observation,), candidate, scope=[])


@pytest.mark.parametrize("failure", ["delay", "raise"])
def test_add_meter_failure_preserves_published_decision(sample, monkeypatch, failure):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    published = []
    real_assess = g._assess
    monkeypatch.setattr(g, "_assess", lambda *a: real_assess(*a))
    def persist(c, answer, *args, **kwargs):
        published.append(answer)
        return answer
    monkeypatch.setattr(g, "_persist", persist)
    append = g._append
    def failing(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "registration_meter_closed":
            clock[0] += 20000.
            if failure == "raise":
                raise ValueError("add meter write failed")
        return result
    monkeypatch.setattr(g, "_append", failing)
    answer = g.decide(candidate, g.Library(), frozen.registration_sha256)
    assert published
    assert answer == published[-1]
    assert g._validation_total(frozen.registration_sha256, None) >= 20000.


@pytest.mark.parametrize("metadata", ["absent", "missing", "malformed"])
def test_add_loader_ignores_replacement_only_evidence(sample, monkeypatch, metadata):
    from pathlib import Path
    from g6.distill.held_out import registration_gate
    from g6.distill import records as r
    candidate = r.VerifierCandidate(**(sample[1].model_dump(exclude={"candidate_id"}) | {"training_case_ids": []}))
    study = {"family": candidate.contract.family, "acceptance_policy_sha256": "p",
             "acceptance_code_sha256": "j", "acceptance_controls": ["c", "d"]}
    if metadata != "absent":
        study["replacement_protocol_sha256"] = "f" * 64 if metadata == "missing" else "bad"
    frozen = SimpleNamespace(study=study, cases=[], policy={}, operator_digests={})
    monkeypatch.setattr(g, "_policy", lambda: (g.EvidencePolicy(), {"registrations": {
        "a" * 64: {"path": "validation/distill/synthetic.json"}}}))
    monkeypatch.setattr(g, "_policy_digest", lambda: "p")
    monkeypatch.setattr(g, "_judging_digest", lambda: "j")
    monkeypatch.setattr(g, "_chronology", lambda *a: None)
    monkeypatch.setattr(g, "_plain", lambda path, root: path)
    monkeypatch.setattr(registration_gate, "read_registration", lambda *a, **kw: frozen)
    concept = SimpleNamespace(family=candidate.contract.family, from_failures=[])
    monkeypatch.setattr(g, "Stores", lambda: SimpleNamespace(for_model=lambda cls:
        SimpleNamespace(get=lambda ident: concept)))
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: None)
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("add read replacement storage"))
    loaded = g._load("a" * 64, candidate, SimpleNamespace(list=lambda: []))
    assert loaded.replacement is None
    assert loaded.acceptance == ()
    assert r.VerifierCandidate.model_validate(candidate.model_dump(mode="json")) == candidate


@pytest.mark.parametrize("kind", ["registration_meter_closed", "replacement_attempt", "registration_closed"])
def test_add_journal_runtime_failure_keeps_published_answer(sample, monkeypatch, kind):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    published = []
    def persist(c, answer, *args, **kwargs):
        published.append(answer)
        return answer
    monkeypatch.setattr(g, "_persist", persist)
    append = g._append
    def interrupted(journal, event, body):
        if event == kind:
            raise RuntimeError("meter interrupted")
        return append(journal, event, body)
    monkeypatch.setattr(g, "_append", interrupted)
    answer = g.decide(candidate, g.Library(), frozen.registration_sha256)
    assert answer == published[-1]
    assert any(row["kind"] in {"registration_meter_failure", "registration_close_failed"}
               for row in g._journal().entries())


def test_reader_requires_final_close_and_accepts_exact_zero(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    registration = frozen.registration_sha256
    opening = {"registration": registration, "interval_version": 1, "phase": "assessment",
               "monotonic_start": 0., "wall_start": 0.}
    attempt = g._append(g._journal(), "replacement_attempt_start", opening)
    entry = SimpleNamespace(entry_id="e"*64, candidate_id=candidate.candidate_id,
        noninferiority="f"*64, teacher="c"*64, replacement_scope=[])
    replacement = {"state": "active", "teacher": entry.teacher, "scope": [],
                   "cpu_weight": 1., "validation_s": 20., "cost": {"upper_95": 0.}}
    receipt = g._append(g._journal(), "replacement_state", {"state": "active",
        "entry_id": entry.entry_id, "candidate_id": candidate.candidate_id, "attempt": attempt,
        "pending_receipt": entry.noninferiority, "report": {"registration": registration,
        "candidate_id": candidate.candidate_id, "replacement": replacement}})
    clock[0] = 20.
    assert not g._replacement_authorized(entry)
    g._append(g._journal(), "registration_closed", opening | {"attempt": attempt,
        "validation_s": 20., "monotonic_end": 20., "wall_end": 20.,
        "candidate_ids": [candidate.candidate_id], "activation_receipts": [receipt]})
    assert g._replacement_authorized(entry)
    next_attempt = g._append(g._journal(), "replacement_attempt_start", opening | {"monotonic_start": 20.})
    assert not g._replacement_authorized(entry)
    g._append(g._journal(), "registration_closed", opening | {"attempt": next_attempt,
        "validation_s": 1., "monotonic_end": 21., "candidate_ids": [], "activation_receipts": []})
    assert not g._replacement_authorized(entry)


def test_interval_rows_bracket_writer_and_lease_release(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    @contextmanager
    def writer():
        rows = g._journal().entries()
        assert len(rows) == 1 and rows[0]["body"]["state"] == "opened"
        clock[0] += 2.
        try:
            yield
        finally:
            assert not any(row["kind"] == "registration_closed" for row in g._journal().entries())
            clock[0] += 3.
    monkeypatch.setattr(g, "_writer", writer)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "admit_probation"
    rows = g._journal().entries()
    assert rows[-1]["kind"] == "registration_closed"
    assert rows[-1]["body"]["validation_s"] == 5.
    assert g._validation_total(frozen.registration_sha256, None) == 5.


@pytest.mark.parametrize("bound", [True, False])
def test_failed_close_requires_bound_cost_recovery(sample, monkeypatch, bound):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    append = g._append
    def interrupted(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "registration_closed":
            raise ValueError("close interrupted after row")
        return result
    monkeypatch.setattr(g, "_append", interrupted)
    assert g.decide_replacement(candidate, g.Library(), frozen.registration_sha256).verdict == "block"
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)
    failed = g._journal().entries()[-1]
    closed = [row for row in g._journal().entries() if row["kind"] == "registration_closed"][-1]
    clock[0] = 7.
    append(g._journal(), "registration_closed", closed["body"] | {"recovery_of": failed["id"] if bound else "f"*64,
        "validation_s": 7., "monotonic_end": 7., "activation_receipts": []})
    if bound:
        assert g._validation_total(frozen.registration_sha256, None) == 7.
    else:
        with pytest.raises(ValueError, match="cost recovery"):
            g._validation_total(frozen.registration_sha256, None)


_REAL_WRITER = g._writer


def test_real_lease_unlink_delay_leaves_no_usable_replacement(sample, monkeypatch, tmp_path):
    from pathlib import Path
    from . import test_acceptance_repair2 as repair2
    candidate, frozen, clock = repair2.persisted_rig(sample, monkeypatch, tmp_path)
    monkeypatch.setattr(g, "_writer", _REAL_WRITER)
    unlink = Path.unlink
    def delayed(path, *args, **kwargs):
        if path.name == "acceptance_gate.lock":
            clock[0] += 20000.
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", delayed)
    answer = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert answer.verdict in {"block", "refuse"}
    assert not g._active(g.Library().list())
    assert g._validation_total(frozen.registration_sha256, None) >= 20000.
    retry = g.decide_replacement(candidate, g.Library(), frozen.registration_sha256)
    assert retry.verdict in {"block", "refuse"}
    assert "cost" in " ".join(retry.reasons)
