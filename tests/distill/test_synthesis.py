# SPDX-License-Identifier: Apache-2.0
"""S emits proposals using training evidence; it cannot confer library authority."""
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from g6.core.ledger import digest
from g6.core.llm import EmptyRetry, FakeLLM, LLMError
from g6.distill import records as r
from tests.distill.test_records import bodies


def api():
    # Lazy import makes the missing implementation an explicit RED assertion.
    import importlib.util
    assert importlib.util.find_spec("g6.distill.synthesis") is not None, "synthesis S is not implemented"
    from g6.distill.synthesis import SynthesisCase, Synthesizer
    return SynthesisCase, Synthesizer


def fixture(after="def test_a():\n    assert True\n", *, label="a"):
    Case, _ = api()
    data = dict(bodies()[0][1], case_ref=digest(label))
    failure = r.FailureRecord(**data)
    concept = r.Concept(**dict(bodies()[1][1], from_failures=[failure.record_id]))
    case = Case(failure, {"tests/test_a.py": "def test_a():\n    assert calc() == 4\n"},
                {"tests/test_a.py": after})
    return concept, case


class MemoryLibrary:
    """Read-only snapshot with genuine records; no durable gate decisions are written."""
    def __init__(self, templates):
        self.values = []
        self.methods = {}
        for name in templates:
            contract = r.MethodContract(**bodies()[2][1])
            candidate = r.VerifierCandidate(**dict(bodies()[3][1], form="template_instance",
                body=r.TemplateInstance(template=name, bindings={})))
            entry = r.LibraryEntry(**dict(bodies()[4][1], candidate_id=candidate.candidate_id,
                                         contract_sha256=contract.content_id))
            self.values.append(entry)
            self.methods[entry.entry_id] = candidate

    def list(self):
        return list(self.values)

    def candidate(self, entry):
        return self.methods[entry.entry_id]


def assert_proposed(candidate, concept, *, calls):
    assert candidate.authority == "proposed"
    assert candidate.model_calls == calls
    assert candidate.concept_id == concept.concept_id
    encoded = candidate.model_dump(mode="json")
    assert encoded["authority"] == "proposed"
    assert encoded["model_calls"] == calls
    assert r.VerifierCandidate.model_validate(encoded) == candidate


def test_reuse_precedes_model_and_preserves_contract_and_lineage():
    _, Synthesizer = api()
    concept, case = fixture()
    library = MemoryLibrary(["assert_strength.v1"])
    caller = FakeLLM(default="must not be needed")
    result = Synthesizer(caller=caller).synthesise(concept, [case], library=library)
    candidate, = result.candidates
    entry, = library.values
    assert candidate.form == "reuse"
    assert candidate.body == entry.entry_id
    assert candidate.contract == library.candidate(entry).contract
    assert candidate.lineage == [entry.entry_id]
    assert candidate.training_case_ids == [case.failure.case_ref]
    assert result.model_calls == 1 and result.synthesis_calls == 0
    assert caller.calls == []
    assert_proposed(candidate, concept, calls=1)


def test_composition_precedes_new_templates_and_models():
    _, Synthesizer = api()
    concept, removed = fixture()
    _, skipped = fixture("@pytest.mark.skip\ndef test_a():\n    assert calc() == 4\n", label="b")
    concept = r.Concept(**dict(concept.model_dump(exclude={"concept_id"}),
                              from_failures=[removed.failure.record_id, skipped.failure.record_id]))
    library = MemoryLibrary(["assert_strength.v1", "skip_gate.v1"])
    result = Synthesizer().synthesise(concept, [removed, skipped], library=library)
    candidate, = result.candidates
    assert candidate.form == "composition"
    assert candidate.body.op == "any"
    assert set(candidate.body.entries) == {e.entry_id for e in library.values}
    assert candidate.lineage == candidate.body.entries
    assert_proposed(candidate, concept, calls=1)


def test_seed_is_data_only_and_deterministic_without_model():
    _, Synthesizer = api()
    concept, case = fixture()
    first = Synthesizer().synthesise(concept, [case], library=MemoryLibrary([]))
    second = Synthesizer().synthesise(concept, [case], library=MemoryLibrary([]))
    assert first == second
    candidate, = first.candidates
    assert candidate.form == "template_instance"
    assert candidate.body.bindings == {}
    assert candidate.body.template in {"assert_strength.v1", "weakening.v1"}
    assert_proposed(candidate, concept, calls=1)


def quiet_case():
    # Existing templates do not flag this new observational concept.
    return fixture("def test_a():\n    assert calc() == 4\n")


def test_fifth_call_is_refused_recorded_and_budget_survives_retry():
    _, Synthesizer = api()
    concept, case = quiet_case()
    fake = FakeLLM(default="not JSON")
    synth = Synthesizer(caller=fake)
    result = synth.synthesise(concept, [case], library=MemoryLibrary([]))
    assert result.model_calls == 4 and result.synthesis_calls == 3
    assert len(fake.calls) == 3
    assert any(x.code == "G6_E_SYNTH_CALL_LIMIT" for x in result.refusals)
    again = synth.synthesise(concept, [case], library=MemoryLibrary([]))
    assert again.model_calls == 4 and len(fake.calls) == 3
    assert again.refusals[-1].code == "G6_E_SYNTH_CALL_LIMIT"


def test_retry_wrapper_cannot_hide_extra_sends():
    _, Synthesizer = api()
    concept, case = quiet_case()
    fake = FakeLLM(default="")
    result = Synthesizer(caller=EmptyRetry(fake)).synthesise(concept, [case], library=MemoryLibrary([]))
    assert result.model_calls == 4
    assert len(fake.calls) == 3
    assert any(x.code == "G6_E_SYNTH_CALL_LIMIT" for x in result.refusals)


def test_failed_model_attempts_count():
    _, Synthesizer = api()
    concept, case = quiet_case()
    def fail(system, user):
        raise LLMError("synthetic failure")
    fake = FakeLLM(fail)
    result = Synthesizer(caller=fake).synthesise(concept, [case], library=MemoryLibrary([]))
    assert result.model_calls == 4 and len(fake.calls) == 3
    assert not result.candidates


def test_template_binding_injection_is_refused():
    _, Synthesizer = api()
    concept, case = quiet_case()
    fake = FakeLLM(default=json.dumps({"template": "assert_strength.v1", "bindings": {
        "name": "calc(); __import__('os').system('bad')"}}))
    result = Synthesizer(caller=fake).synthesise(concept, [case], library=MemoryLibrary([]))
    assert not result.candidates
    assert any(x.code == "G6_E_SYNTH_BINDINGS" for x in result.refusals)


@pytest.mark.parametrize("split", ["held_out", "dev", "monitor", "field"])
def test_nontraining_case_is_refused_before_source_access(split):
    _, Synthesizer = api()
    concept, case = fixture()
    class Unreadable(dict):
        def items(self):
            raise AssertionError("nontraining content read")
        def __iter__(self):
            raise AssertionError("nontraining content read")
    failure = case.failure.model_copy(update={"split": split})
    case = replace(case, failure=failure, before=Unreadable(), after=Unreadable())
    result = Synthesizer().synthesise(concept, [case], library=MemoryLibrary([]))
    assert not result.candidates
    assert result.refusals[0].code == "G6_E_SYNTH_TRAINING_ONLY"


def test_missing_failure_and_empty_cases_refused():
    _, Synthesizer = api()
    concept, _ = fixture()
    result = Synthesizer().synthesise(concept, [], library=MemoryLibrary([]))
    assert not result.candidates
    assert result.refusals[0].code == "G6_E_SYNTH_TRAINING_ONLY"


PREDICATE = "def solve(facts):\n    return {'verdict': 'concern', 'reason': 'observed feature'}\n"


def predicate_caller():
    return FakeLLM(lambda system, user: json.dumps({"template": "kill_set.v1", "bindings": {}})
                   if "template bindings" in system else json.dumps({"source": PREDICATE}))


def test_predicate_is_refused_without_strict_isolation(monkeypatch):
    _, Synthesizer = api()
    concept, case = quiet_case()
    monkeypatch.delenv("G6_REQUIRE_ISOLATION", raising=False)
    result = Synthesizer(caller=predicate_caller()).synthesise(concept, [case], library=MemoryLibrary([]))
    assert not result.candidates
    assert any(x.code == "G6_E_ISOLATION_UNAVAILABLE" for x in result.refusals)


def test_predicate_only_runs_through_existing_confined_batch(monkeypatch):
    _, Synthesizer = api()
    from g6.distillation import sandbox
    concept, case = quiet_case()
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    seen = []
    def confined(source, values, *, timeout_s, memory_mb):
        seen.append((source, values, timeout_s, memory_mb))
        return [sandbox.Run("ok", {"verdict": "concern", "reason": "observed feature"}) for _ in values]
    monkeypatch.setattr(sandbox, "run_batch", confined)
    first = Synthesizer(caller=predicate_caller()).synthesise(concept, [case], library=MemoryLibrary([]))
    second = Synthesizer(caller=predicate_caller()).synthesise(concept, [case], library=MemoryLibrary([]))
    assert first == second
    candidate, = first.candidates
    assert candidate.form == "predicate" and candidate.body == PREDICATE
    assert candidate.synthesised_by.exchange_sha256
    assert_proposed(candidate, concept, calls=3)
    assert len(seen) == 2 and seen[0][0] == PREDICATE
    assert "files" in seen[0][1][0] and "split" not in seen[0][1][0]
    assert seen[0][2] <= candidate.contract.cost_bound.wall_s


def test_unique_training_literals_are_refused_before_execution(monkeypatch):
    _, Synthesizer = api()
    from g6.distillation import sandbox
    concept, case = quiet_case()
    source = "def solve(facts):\n return {'verdict':'concern','reason':'tests/test_a.py'}"
    fake = FakeLLM(lambda system, user: json.dumps({"template": "kill_set.v1", "bindings": {}})
                   if "template bindings" in system else json.dumps({"source": source}))
    def must_not_run(*args, **kwargs):
        raise AssertionError("leaking source executed")
    monkeypatch.setattr(sandbox, "run_batch", must_not_run)
    result = Synthesizer(caller=fake).synthesise(concept, [case], library=MemoryLibrary([]))
    assert not result.candidates
    assert any(x.code == "G6_E_SYNTH_CASE_LITERAL" for x in result.refusals)


def test_candidate_cannot_claim_earned_authority():
    api()
    with pytest.raises(ValidationError):
        r.VerifierCandidate(**dict(bodies()[3][1], authority="admitted"))


def test_legacy_candidate_keeps_its_content_identity():
    api()
    candidate = r.VerifierCandidate(**bodies()[3][1])
    assert candidate.authority == "proposed" and candidate.model_calls == 0
    assert candidate.candidate_id == digest(bodies()[3][1])
    encoded = candidate.model_dump(mode="json")
    assert "authority" not in encoded and "model_calls" not in encoded
    assert r.VerifierCandidate.model_validate(encoded).candidate_id == candidate.candidate_id


def test_held_out_in_later_case_prevents_reading_earlier_training_source():
    _, Synthesizer = api()
    concept, case = fixture()
    class Unreadable(dict):
        def items(self):
            raise AssertionError("content read before all headers checked")
        def __iter__(self):
            raise AssertionError("content read before all headers checked")
    bad = replace(case, failure=case.failure.model_copy(update={"split": "held_out"}))
    safe = replace(case, before=Unreadable(), after=Unreadable())
    result = Synthesizer().synthesise(concept, [safe, bad], library=MemoryLibrary([]))
    assert result.refusals[0].code == "G6_E_SYNTH_TRAINING_ONLY"


def test_prompt_scrubs_paths_names_and_long_case_strings():
    _, Synthesizer = api()
    concept, case = fixture('def test_a():\n    assert calc() == "case-unique-long-secret"\n')
    # Identical source is not flagged by templates.
    case = replace(case, before=case.after)
    fake = FakeLLM(default="{}")
    Synthesizer(caller=fake).synthesise(concept, [case], library=MemoryLibrary([]))
    assert fake.calls
    assert all("tests/test_a.py" not in user and "test_a" not in user
               and "case-unique-long-secret" not in user for _, user in fake.calls)


def test_withdrawn_entry_is_not_reused():
    _, Synthesizer = api()
    concept, case = fixture()
    library = MemoryLibrary(["assert_strength.v1"])
    body = library.values[0].model_dump(exclude={"entry_id"})
    library.values = [r.LibraryEntry(**dict(body, status="withdrawn", authority="A0"))]
    result = Synthesizer().synthesise(concept, [case], library=library)
    assert result.candidates[0].form == "template_instance"
    assert result.candidates[0].lineage == []


def test_reuse_cannot_widen_a_parent_contract():
    _, Synthesizer = api()
    concept, case = fixture()
    library = MemoryLibrary(["assert_strength.v1"])
    entry = library.values[0]
    method = library.candidate(entry)
    contract = r.MethodContract(**dict(method.contract.model_dump(), languages=["javascript"]))
    method = r.VerifierCandidate(**dict(method.model_dump(exclude={"candidate_id"}), contract=contract))
    entry = r.LibraryEntry(**dict(entry.model_dump(exclude={"entry_id"}),
                                candidate_id=method.candidate_id, contract_sha256=contract.content_id))
    library.values = [entry]
    library.methods = {entry.entry_id: method}
    result = Synthesizer().synthesise(concept, [case], library=library)
    assert result.candidates[0].form == "template_instance"


def test_nonfake_caller_requires_weekly_reservation_before_send():
    _, Synthesizer = api()
    concept, case = quiet_case()
    class Unauthorised:
        def complete(self, *args, **kwargs):
            raise AssertionError("model sent without pacing admission")
    result = Synthesizer(caller=Unauthorised()).synthesise(concept, [case], library=MemoryLibrary([]))
    assert result.model_calls == 1 and not result.candidates
    assert result.refusals[0].code == "G6_E_SYNTH_PACING"


def test_pacing_denial_is_returned_once_without_retrying():
    _, Synthesizer = api()
    from g6.self_maintenance.pacing import Admission
    concept, case = quiet_case()
    class Unauthorised:
        def complete(self, *args, **kwargs):
            raise AssertionError("model sent despite denial")
    class Pacing:
        def __init__(self):
            self.reservations = 0
        def reserve(self, provider, now, worst_case):
            self.reservations += 1
            return Admission(provider, 1, 0, 1, 1, False, reason="stale account reading")
    pacing = Pacing()
    result = Synthesizer(caller=Unauthorised(), pacing=pacing, provider="codex", worst_case=1).synthesise(
        concept, [case], library=MemoryLibrary([]))
    assert result.model_calls == 1 and not result.candidates
    assert result.refusals == [__import__("g6.distill.synthesis", fromlist=["Refusal"]).Refusal(
        code="G6_E_SYNTH_PACING", reason="stale account reading")]
    assert pacing.reservations == 1


def test_predicate_through_real_batch_preserves_isolation_refusal(monkeypatch):
    _, Synthesizer = api()
    from g6.core import sandbox
    concept, case = quiet_case()
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    def refused(*args, **kwargs):
        return sandbox.ScriptRun("refused", None, "", "G6_E_ISOLATION_UNAVAILABLE: no backend")
    monkeypatch.setattr(sandbox, "run_script", refused)
    result = Synthesizer(caller=predicate_caller()).synthesise(concept, [case], library=MemoryLibrary([]))
    assert not result.candidates
    assert any(x.code == "G6_E_ISOLATION_UNAVAILABLE" for x in result.refusals)


def test_predicate_batch_rejects_malformed_verdict(monkeypatch):
    _, Synthesizer = api()
    from g6.distillation import sandbox
    concept, case = quiet_case()
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    monkeypatch.setattr(sandbox, "run_batch", lambda *args, **kwargs: [sandbox.Run("ok", {"verdict": "verified"})])
    result = Synthesizer(caller=predicate_caller()).synthesise(concept, [case], library=MemoryLibrary([]))
    assert not result.candidates
    assert any(x.code == "G6_E_SYNTH_EXECUTION" for x in result.refusals)


def test_synthesis_never_writes_admission_state(monkeypatch):
    _, Synthesizer = api()
    from g6.distill.ledger import RecordStore
    def no_write(*args, **kwargs):
        raise AssertionError("S wrote a store")
    monkeypatch.setattr(RecordStore, "put", no_write)
    concept, case = fixture()
    result = Synthesizer().synthesise(concept, [case])
    assert result.candidates[0].authority == "proposed"


def test_expired_weekly_reservation_never_reaches_model():
    _, Synthesizer = api()
    from datetime import datetime, timedelta, timezone
    from g6.self_maintenance.pacing import Admission
    concept, case = quiet_case()
    start = datetime(2026, 11, 18, tzinfo=timezone.utc)
    clock = iter([start, start + timedelta(days=8)])
    fake = FakeLLM(default="{}")
    class OnlineCaller:
        def complete(self, *args, **kwargs):
            return fake.complete(*args, **kwargs)
    class Pacing:
        def reserve(self, provider, now, worst_case):
            return Admission(provider, 1, 0, 10, 10, False, admitted=True, amount=worst_case,
                             ends=start + timedelta(days=7))
        def settle(self, admission, amount):
            assert amount == admission.amount
    result = Synthesizer(caller=OnlineCaller(), pacing=Pacing(), provider="codex", worst_case=1,
                         now=lambda: next(clock, start + timedelta(days=8))).synthesise(
                             concept, [case], library=MemoryLibrary([]))
    assert fake.calls == []
    assert result.refusals[-1].code == "G6_E_SYNTH_PACING"


def test_synthesis_refusals_have_local_component_declarations():
    from g6.core import refusals
    from g6.core.registry import get_registry
    assert "distill" in get_registry().refs
    codes = refusals.component_declarations()["distill"]
    assert codes["G6_E_SYNTH_CALL_LIMIT"].fix_by == "none"
    assert codes["G6_E_ISOLATION_UNAVAILABLE"].fix_by == "person"


def test_predicate_repair_receives_observed_training_failure(monkeypatch):
    _, Synthesizer = api()
    from g6.distillation import sandbox
    concept, case = quiet_case()
    weak = "def solve(facts):\n return {'verdict': 'no_concern', 'reason': 'too weak'}"
    def responder(system, user):
        if "template bindings" in system:
            return json.dumps({"template": "kill_set.v1", "bindings": {}})
        if "predicate repair" in system:
            feedback = json.loads(user)["repair"]
            assert feedback["source"] == weak
            assert feedback["observed"] == ["no_concern"]
            assert feedback["expected"] == ["concern"]
            return json.dumps({"source": PREDICATE})
        return json.dumps({"source": weak})
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    def confined(source, values, **kwargs):
        verdict = "no_concern" if source == weak else "concern"
        return [sandbox.Run("ok", {"verdict": verdict, "reason": "fixture"})]
    monkeypatch.setattr(sandbox, "run_batch", confined)
    result = Synthesizer(caller=FakeLLM(responder)).synthesise(concept, [case], library=MemoryLibrary([]))
    assert result.model_calls == 4
    assert result.candidates[0].body == PREDICATE


def test_sequence_does_not_run_unused_predicate_parent(monkeypatch):
    api()
    from g6.distill.execution import Observation, run_candidate
    from g6.distill.facts import extract_facts
    concept, case = fixture()
    library = MemoryLibrary(["assert_strength.v1"])
    first_entry = library.values[0]
    fallback = r.VerifierCandidate(**dict(bodies()[3][1], body=PREDICATE))
    fallback_id = digest("unused predicate parent")
    methods = {first_entry.entry_id: library.candidate(first_entry), fallback_id: fallback}
    method = r.VerifierCandidate(**dict(bodies()[3][1], form="composition",
        body=r.Composition(op="sequence", entries=[first_entry.entry_id, fallback_id])))
    observation = Observation(case.before, case.after, extract_facts(case.before, case.after, split="training"))
    monkeypatch.delenv("G6_REQUIRE_ISOLATION", raising=False)
    assert run_candidate(method, [observation], methods.__getitem__) == ["concern"]
