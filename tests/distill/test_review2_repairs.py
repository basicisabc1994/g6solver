"""Review 2 of the distill integration: each finding repaired at its class."""
import hashlib
import json
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.distill import records as r


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_every_path_any_judging_or_oracle_digest_pins_is_protected():
    from g6.distill.judging import DIGESTED_PATHS, JUDGING_PATHS, ORACLE_PINS
    from g6.distill.merge_evidence_gate import protected_set
    from g6.self_training.merge_gate import FileChange
    assert {*JUDGING_PATHS, *ORACLE_PINS.values()} <= set(DIGESTED_PATHS)
    protected = protected_set()
    for path in DIGESTED_PATHS:
        assert protected.hits((FileChange(path=path, before="a = 1\n", after="a = 2\n"),)) == (path,), path


def test_the_oracle_digest_reads_the_shared_pins_and_keeps_its_value():
    from g6.core import confine, ledger
    from g6.distill.held_out import case_gate, oracle, registration_gate
    from g6.workforce import mutation_check, repo_tests
    # The value registrations pinned before the list moved: module names and the installed files' bytes.
    files = {m.__name__: Path(m.__file__) for m in (case_gate, oracle, mutation_check, repo_tests, confine, ledger)}
    held_out = Path(registration_gate.__file__)
    files.update({"sealed_gate": held_out.with_name("sealed_gate.py"), "registration_gate": held_out,
                  "operators": held_out.parents[1] / "operators.py"})
    before = digest({name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()})
    assert registration_gate.oracle_digest() == before


CASE = {
    "unit_id": "client-unit-1",
    "provenance": {"source": "maintainer", "cohort": "development", "source_ref": "client/repo@abc",
                   "source_sha256": "1" * 64},
    "files": {"pkg/calc.py": "def answer(x):\n    return x + 1\n"},
    "target": "pkg/calc.py",
    "changed_lines": [2],
    "before_tests": "def test_answer():\n    assert answer(2) == 3\n",
    "after_tests": "def test_answer():\n    assert answer(2) is not None\n",
    "known_wrong": ["def answer(x):\n    return x\n"],
}


class QueuedJobs:
    def __init__(self):
        self.operations = []

    def start(self, operation):
        self.operations.append(operation)
        return {"job_id": str(len(self.operations))}


def _positive(monkeypatch, seen=None):
    from g6.distill.held_out import oracle

    def label(case, **kwargs):
        if seen is not None:
            seen.append(case)
        return oracle.OracleResult(case_id=case.case_id, label="positive", reason="known_wrong_witness",
                                   policy_sha256="0" * 64)
    monkeypatch.setattr(oracle, "label", label)


def _unseen_cohorts():
    from typing import get_args
    from g6.distill.held_out.case_gate import Provenance, unit_split
    cohorts = get_args(Provenance.model_fields["cohort"].annotation)
    return [c for c in cohorts if unit_split("unit", c, "seed") == "unseen"]


@pytest.mark.parametrize("cohort", _unseen_cohorts())
def test_an_unseen_cohort_never_becomes_training_data(home, monkeypatch, cohort):
    from g6.distill import surface
    from g6.distill.ledger import Stores
    from g6.mcp.distill_backend import Service
    seen = []
    _positive(monkeypatch, seen)
    case = json.loads(json.dumps(CASE))
    case["provenance"]["cohort"] = cohort
    surface.failure_record("missed_defect", "test_weakening", json.dumps(case), "unseen")
    jobs = QueuedJobs()
    Service(jobs=jobs).distill_start("test_weakening")
    [operation] = jobs.operations
    result = operation()
    # Labelled out of training without running the oracle: the case is reported, never learnt from.
    assert result["warning_card"]["code"] == "G6_E_SYNTH_TRAINING_ONLY"
    assert [row["label"] for row in result["labels"]] == ["unseen"]
    assert not seen
    assert {f.observed_by for f in Stores().for_model(r.FailureRecord).list()} == {"self"}


def test_a_labelled_record_keeps_its_cohort_and_a_deterministic_identity(home):
    from g6.distill import surface
    from g6.distill.held_out import oracle
    from g6.distill.ledger import Stores
    from g6.mcp.distill_backend import _labelled, _oracle_case
    surface.failure_record("missed_defect", "test_weakening", json.dumps(CASE), "weak")
    [source] = Stores().for_model(r.FailureRecord).list()
    case = _oracle_case(source)
    result = oracle.OracleResult(case_id=case.case_id, label="positive", reason="known_wrong_witness",
                                 policy_sha256="0" * 64)
    first, second = _labelled(source, case, result), _labelled(source, case, result)
    assert first.record_id == second.record_id
    assert first.observed_at == source.observed_at
    assert first.provenance["cohort"] == "development"


def test_overlapping_starts_write_one_independent_record(home, monkeypatch):
    from g6.distill import surface
    from g6.distill.ledger import Stores
    from g6.mcp.distill_backend import Service
    _positive(monkeypatch)
    report = surface.failure_record("missed_defect", "test_weakening", json.dumps(CASE), "weak")
    jobs = QueuedJobs()
    service = Service(jobs=jobs)
    # Both starts finish intake before either queued job runs.
    service.distill_start("test_weakening", report["record_id"])
    service.distill_start("test_weakening", report["record_id"])
    first, second = (operation() for operation in jobs.operations)
    independent = [f for f in Stores().for_model(r.FailureRecord).list() if f.observed_by == "independent"]
    assert len(independent) == 1
    assert first["labels"][0]["training_record_id"] == second["labels"][0]["training_record_id"]


def test_the_training_view_drops_and_reports_a_duplicate_case(home):
    from g6.distill.synthesis import SynthesisCase, _training_view
    from tests.distill.test_records import bodies
    before = {"tests/test_a.py": "def test_a():\n    assert f() == 1\n"}
    after = {"tests/test_a.py": "def test_a():\n    assert f()\n"}
    one = r.FailureRecord(**bodies()[0][1])
    twin = r.FailureRecord(**(bodies()[0][1] | {"observed_at": "2026-11-19"}))
    assert one.case_ref == twin.case_ref and one.record_id != twin.record_id
    concept = r.Concept(**(bodies()[1][1] | {"from_failures": [one.record_id]}))
    view = _training_view(concept, [SynthesisCase(one, before, after), SynthesisCase(twin, before, after)])
    assert len(view.observations) == 1 and view.duplicates == [twin.record_id]
