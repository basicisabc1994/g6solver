"""Review 1 of the distill integration: each finding repaired at its class."""
import json

import pytest

from g6.core.ledger import digest
from g6.distill import records as r


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_every_file_the_acceptance_gate_judges_by_is_protected_from_autonomous_merge():
    from g6.distill import acceptance_gate, judging
    from g6.distill.merge_evidence_gate import protected_set
    from g6.self_training.merge_gate import FileChange
    # One source: the judging digest and the merge gate's protected set read the same tuple.
    assert acceptance_gate.JUDGING_PATHS is judging.JUDGING_PATHS
    protected = protected_set()
    for path in judging.JUDGING_PATHS:
        change = FileChange(path=path, before="a = 1\n", after="a = 2\n")
        assert protected.hits((change,)) == (path,), path


def _entry(**changes):
    from tests.distill.test_records import bodies
    return r.LibraryEntry(**(bodies()[4][1] | changes))


def test_a_withdrawal_during_a_review_takes_effect_before_authority_is_assigned(monkeypatch):
    from g6.distill import acceptance_gate
    from g6.distill.library import Library
    from g6.harness import library_review_gate as review
    from tests.distill.test_records import bodies
    candidate = r.VerifierCandidate(**bodies()[3][1])
    admitted = _entry(candidate_id=candidate.candidate_id)
    withdrawn = _entry(candidate_id=candidate.candidate_id, version=2, status="withdrawn", authority="A0")
    library = [admitted]
    monkeypatch.setattr(Library, "list", lambda self: list(library))

    class Store:
        def get(self, ident):
            return candidate
    monkeypatch.setattr(review.Stores, "for_model", lambda self, model: Store())
    monkeypatch.setattr(review, "record_fire", lambda *a: None)

    def run(*args):
        library[:] = [withdrawn]  # the person's withdrawal completes while the method runs
        return "concern"
    monkeypatch.setattr(acceptance_gate, "_run", run)
    [found] = review.check({"tests/test_a.py": "def test_a():\n    assert f() == 1\n"},
                           {"tests/test_a.py": "def test_a():\n    assert f()\n"})
    assert found["outcome"] != "defect_detected"
    assert (found["authority"], found["status"], found["entry_id"]) == ("A0", "withdrawn", withdrawn.entry_id)


def test_a_method_removed_during_a_review_reports_nothing(monkeypatch):
    from g6.distill import acceptance_gate
    from g6.distill.library import Library
    from g6.harness import library_review_gate as review
    from tests.distill.test_records import bodies
    candidate = r.VerifierCandidate(**bodies()[3][1])
    library = [_entry(candidate_id=candidate.candidate_id)]
    monkeypatch.setattr(Library, "list", lambda self: list(library))

    class Store:
        def get(self, ident):
            return candidate
    monkeypatch.setattr(review.Stores, "for_model", lambda self, model: Store())

    def run(*args):
        library.clear()
        return "concern"
    monkeypatch.setattr(acceptance_gate, "_run", run)
    assert review.check({"tests/test_a.py": "x = 1\n"}, {"tests/test_a.py": "x = 2\n"}) == []


def test_public_intake_names_its_other_class_and_rectification_reconciles(home):
    from g6.distill import surface
    from g6.distill.intake import class_key
    from g6.distill.ledger import Stores
    from g6.distill.rectify_gate import Rectifier
    surface.failure_record("missed_defect", "test_weakening", "{}", "A reported defect")
    surface.record_reproduction({"tool_response": {"assessment": {"outcome": "defect_detected", "reasons": [
        {"reproduction": "tests/test_app.py::test_app"}]}}})
    rows = Stores().for_model(r.FailureRecord).list()
    assert len(rows) == 2 and all(class_key(row).startswith("other: ") for row in rows)
    assert Rectifier().reconcile() == {"malformed": []}


def test_a_malformed_old_record_is_skipped_and_reported_not_fatal(home):
    from g6.distill.ledger import Stores
    from g6.distill.rectify_gate import Rectifier
    from tests.distill.test_records import bodies
    store = Stores().for_model(r.FailureRecord)
    good = r.FailureRecord(**bodies()[0][1])
    legacy = r.FailureRecord(**(bodies()[0][1] | {"finding_class": "other", "case_ref": digest("legacy")}))
    store.put(good)
    store.put(legacy)
    rectifier = Rectifier()
    assert rectifier.reconcile() == {"malformed": [legacy.record_id]}
    assert rectifier.mode("boundary_case").occurrences == 1


def test_deeply_nested_json_is_an_input_refusal_not_an_exception(home):
    from g6.mcp.distill import register
    tools = {}

    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)
    register(Server(), object, None)
    nested = '{"x":' + "[" * 4000 + "0" + "]" * 4000 + "}"
    for name, arguments in (("g6_failure_record", ("missed_defect", "test_weakening", nested, "n")),
                            ("g6_simulate", (nested,))):
        result = tools[name](*arguments)
        assert result["completion_state"] == "blocked-escalated", name
        assert result["warning_card"]["code"].startswith("G6_E_"), name
    assert json.loads(json.dumps(result))
