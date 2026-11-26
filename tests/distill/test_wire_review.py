"""Authority is applied after execution, never inherited from caller input."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("authority,status,verdict,expected", [
    ("A0", "withdrawn", "concern", "insufficient_evidence"),
    ("A1", "probation", "concern", "insufficient_evidence"),
    ("A1", "demoted", "concern", "insufficient_evidence"),
    ("A2", "admitted", "concern", "defect_detected"),
    ("A2", "admitted", "no_concern", "not_a_pass"),
    ("A1", "probation", "no_concern", "not_a_pass"),
    ("A0", "withdrawn", "no_concern", "not_a_pass"),
    ("A2", "admitted", "abstain", "not_a_pass"),
])
def test_authority_caps(authority, status, verdict, expected):
    from g6.harness.library_review_gate import observation
    entry = SimpleNamespace(entry_id="a" * 64, candidate_id="b" * 64, authority=authority, status=status,
        evidence=SimpleNamespace(precision=SimpleNamespace(lo95=.94), n_pos=100, n_neg=100, n_units=200))
    found = observation(entry, verdict)
    assert found["outcome"] == expected
    assert found["provenance"] == "library"
    assert found["authority"] == authority
    assert found["precision"]["lo95"] == .94
    assert found["held_out_n"] == 200
    assert not found["passes_requirement"]


def test_learned_scope_uses_library_never_passes(monkeypatch):
    from g6.harness import library_review_gate as review
    from g6.workforce.patch_checks import run
    monkeypatch.setattr(review, "check_learned", lambda params, **kwargs: {
        "status": "not_run", "reason": "not a pass", "library": [], "counterexamples": [], "functions": []})
    result = run({"regression": {"learned": {"entry_id": "a" * 64}}}, venture=None)
    assert result["result"]["probe"]["reason"] == "not a pass"


@pytest.mark.parametrize("authority,verdict,outcome,label", [
    ("A1", "concern", "insufficient_evidence", "qualified-draft"),
    ("A2", "concern", "defect_detected", "blocked-escalated"),
    ("A2", "no_concern", "supported_within_scope", "qualified-draft"),
])
def test_review_reply_preserves_envelope_and_authority(monkeypatch, authority, verdict, outcome, label):
    import time
    from g6.harness import library_review_gate as review
    from g6.harness import patch_review
    entry = SimpleNamespace(entry_id="a" * 64, candidate_id="b" * 64, authority=authority,
        status="admitted" if authority == "A2" else "probation",
        evidence=SimpleNamespace(precision=SimpleNamespace(lo95=.94), n_pos=100, n_neg=100, n_units=200))
    monkeypatch.setattr(review, "check", lambda *a, **kw: [review.observation(entry, verdict)])
    reasons, runs = [], []
    review.apply_review({}, {}, reasons, runs)
    reply = patch_review._reply(SimpleNamespace(base_commit="c" * 40, files=["app.py"]), [], reasons,
        [], runs, [], [], {}, {}, [], "d" * 64, "", None, time.monotonic())
    assert reply["assessment"]["outcome"] == outcome
    assert reply["completion_state"] == label
    assert reply["assessment"]["requirements"] == []
    assert reply["assessment"]["scope"]["checks_run"][0]["provenance"] == "library"
    if verdict == "concern":
        assert reply["assessment"]["reasons"][0]["provenance"] == "library"


def test_learned_scope_resolves_real_bench_inputs(monkeypatch):
    from g6.harness import library_review_gate as review
    from g6.workforce import bench, patch_checks

    class Source:
        def __truediv__(self, path):
            return self

        def read_text(self, **kwargs):
            return "def f():\n    return 1\n"

    monkeypatch.setattr(bench, "_repo", lambda name, venture: Source())
    monkeypatch.setattr(bench, "_linked", lambda path: False)
    observed = []
    monkeypatch.setattr(review, "check", lambda before, after, **kw: observed.append((before, after)) or [])
    result = patch_checks.run({"repo": "example", "files": {"app.py": "def f():\n    return 2\n"},
                              "regression": {"learned": {"entry_id": "a" * 64}}}, venture="v")
    assert observed == [({"app.py": "def f():\n    return 1\n"}, {"app.py": "def f():\n    return 2\n"})]
    assert result["result"]["probe"]["status"] == "not_run"


@pytest.mark.parametrize("authority,status,expected", [
    ("A1", "probation", "insufficient_evidence"), ("A2", "admitted", "defect_detected"),
    ("A0", "withdrawn", None), ("A0", "superseded", None),
])
def test_actual_seed_executor_current_library_scope(monkeypatch, authority, status, expected):
    from g6.distill import records as r
    from g6.harness import library_review_gate as review
    from .test_records import bodies
    fires = []
    monkeypatch.setattr(review, "record_fire", lambda *args: fires.append(args))
    candidate = r.VerifierCandidate(**(bodies()[3][1] | {
        "form": "template_instance", "body": {"template": "assert_strength.v1", "bindings": {}}}))
    entry = r.LibraryEntry(**(bodies()[4][1] | {"candidate_id": candidate.candidate_id,
                                              "authority": authority, "status": status}))
    monkeypatch.setattr(review.Library, "list", lambda self: [entry])
    class Store:
        def __init__(self, cls):
            self.cls = cls

        def get(self, ident):
            return candidate if self.cls is r.VerifierCandidate else entry
    monkeypatch.setattr(review.Stores, "for_model", lambda self, cls: Store(cls))
    before = {"tests/test_app.py": "def test_app():\n    assert answer(1) == 2\n"}
    after = {"tests/test_app.py": "def test_app():\n    assert answer(1)\n"}
    found = review.check(before, after)
    assert ([f["outcome"] for f in found] == [expected]) if expected else found == []
    if found:
        assert found[0]["seconds"] >= 0
    assert len(fires) == (1 if expected else 0)
    assert review.check({"app.py": before["tests/test_app.py"]}, {"app.py": after["tests/test_app.py"]}) == []
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {
        "contract": candidate.contract.model_dump() | {"languages": ["javascript"]}}))
    assert review.check(before, after) == []


def test_library_no_concern_cannot_satisfy_unestablished_requirement(monkeypatch):
    import time
    from g6.harness import library_review_gate as review
    from g6.harness import patch_review
    entry = SimpleNamespace(entry_id="a" * 64, candidate_id="b" * 64, authority="A2", status="admitted",
        evidence=SimpleNamespace(precision=SimpleNamespace(lo95=.94), n_pos=100, n_neg=100, n_units=200))
    monkeypatch.setattr(review, "check", lambda *a, **kw: [review.observation(entry, "no_concern")])
    reasons = [{"stage": "evidence", "code": "requirement_has_no_check", "detail": "R1 has no stated check"}]
    runs = []
    review.apply_review({}, {}, reasons, runs)
    requirement = {"id": "R1", "text": "requested behaviour", "provenance": "stated", "checks": [], "kind": "behaviour",
                   "status": patch_review.NOT_ESTABLISHED, "reason": "no stated check"}
    reply = patch_review._reply(SimpleNamespace(base_commit="c" * 40, files=["app.py"]), [requirement], reasons,
        [], runs, [], [], {}, {}, [], "d" * 64, "", None, time.monotonic())
    assert reply["assessment"]["outcome"] == "insufficient_evidence"
    assert reply["assessment"]["requirements_without_adequate_check"] == ["R1"]


def test_optional_field_record_failure_preserves_a2_concern(monkeypatch):
    from g6.distill import records as r
    from g6.harness import library_review_gate as review
    from g6.core.ledger import LedgerError
    from .test_records import bodies
    candidate = r.VerifierCandidate(**(bodies()[3][1] | {
        "form": "template_instance", "body": {"template": "assert_strength.v1", "bindings": {}}}))
    entry = r.LibraryEntry(**(bodies()[4][1] | {"candidate_id": candidate.candidate_id}))
    monkeypatch.setattr(review.Library, "list", lambda self: [entry])
    class Store:
        def get(self, ident):
            return candidate
    monkeypatch.setattr(review.Stores, "for_model", lambda *a: Store())
    def unavailable(*a):
        raise LedgerError("synthetic busy writer")
    monkeypatch.setattr(review, "record_fire", unavailable)
    [found] = review.check({"tests/test_a.py": "def test_a():\n    assert f() == 1\n"},
                          {"tests/test_a.py": "def test_a():\n    assert f()\n"})
    assert found["outcome"] == "defect_detected"
    assert found["field_recorded"] is False
