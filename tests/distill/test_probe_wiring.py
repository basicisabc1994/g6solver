"""Probe proposals and execution use registered data and the shared runner."""
import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core import confine
from g6.core.ledger import digest
from g6.core.llm import FakeLLM
from g6.distill import execution, probes, synthesis
from g6.distill.cost_ledger import CostLedger
from g6.distill.detectors import SEED_TEMPLATES
from g6.distill.held_out.case_gate import CaseRecord
from g6.distill.records import VerifierCandidate
from tests.distill import test_probe_policy
from tests.distill.test_probes import BASE, BEFORE, BINDINGS, BUDGET, FIXED, WEAK
from tests.distill.test_records import bodies
from tests.distill.test_synthesis import MemoryLibrary, quiet_case

recorded = test_probe_policy.recorded


def probe_candidate(template="kill_set.v2", bindings=None, budget=None):
    budget = BUDGET if budget is None else budget
    return VerifierCandidate(**(bodies()[3][1] | {
        "form": "template_instance",
        "body": {"template": template, "bindings": BINDINGS if bindings is None else bindings},
        "contract": bodies()[2][1] | {"cost_bound": budget,
            "probes": [{"name": template, "runs": budget["probe_runs"], "wall_s": budget["wall_s"]}]},
    }))


def observation(**updates):
    return execution.Observation(BEFORE, WEAK, {}, **updates)


def inputs():
    return probes.ProbeInput(files={"subject.py": FIXED}, target="subject.py", changed_lines=(2,))


@pytest.fixture
def workspace(monkeypatch):
    calls = []

    def folder(**kwargs):
        calls.append(kwargs)
        return nullcontext(Path(".build"))

    monkeypatch.setattr(confine, "run_folder", folder)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    return calls


def test_default_probe_follows_static_misses_and_round_trips(monkeypatch):
    concept, case = quiet_case()
    seen = []

    def evaluate(method, observations, resolve):
        seen.append(method)
        return ["concern" if method.body.template == "kill_set.v2" else "no_concern"]

    monkeypatch.setattr(synthesis, "run_candidate", evaluate)
    caller = FakeLLM(default="must not be called")
    result = synthesis.Synthesizer(caller=caller).synthesise(concept, [case], library=MemoryLibrary([]))
    method, = result.candidates
    assert [m.body.template for m in seen[:-1]] == [
        name for name in synthesis.Synthesizer().policy.template_order if SEED_TEMPLATES[name].supported]
    assert method.body.template == "kill_set.v2"
    assert method.body.bindings == {"limit": 24, "lines": "changed_by_patch", "min_settled": 12, "reruns": 2}
    instance = probes.ProbeTemplateInstance.model_validate(method.body.model_dump(mode="json"))
    assert probes.ProbeTemplateInstance.model_validate_json(instance.model_dump_json()) == instance
    assert VerifierCandidate.model_validate_json(method.model_dump_json()) == method
    assert method.contract.probes[0].name == "kill_set.v2"
    assert method.contract.cost_bound.probe_runs == 98
    assert caller.calls == []


def test_model_probe_bindings_are_accepted_after_default_misses(monkeypatch):
    concept, case = quiet_case()
    monkeypatch.setattr(synthesis, "run_candidate", lambda method, *a: [
        "concern" if method.body.template == "kill_set.v2" and method.body.bindings.get("limit") == 2
        else "no_concern"])
    fake = FakeLLM(default=json.dumps({"template": "kill_set.v2", "bindings": BINDINGS}))
    result = synthesis.Synthesizer(caller=fake).synthesise(concept, [case], library=MemoryLibrary([]))
    method, = result.candidates
    assert method.body.bindings == BINDINGS
    assert method.contract.cost_bound.probe_runs == 10
    assert method.synthesised_by.kind == "model"
    assert len(fake.calls) == 1


@pytest.mark.parametrize("template,bindings", [
    ("kill_set.v2", {"limit": True}), ("kill_set.v2", {"min_settled": 25}),
    ("discriminates_at_base.v2", {"reruns": 4}), ("discriminates_at_base.v2", {"source": "bad"}),
    ("assert_strength.v1", {"reruns": 2}),
])
def test_bad_model_bindings_are_refused_with_binding_code(template, bindings):
    concept, case = quiet_case()
    from g6.distill.meta.records import SynthesisPolicy
    fake = FakeLLM(default=json.dumps({"template": template, "bindings": bindings}))
    result = synthesis.Synthesizer(caller=fake, policy=SynthesisPolicy(try_predicate=False)).synthesise(
        concept, [case], library=MemoryLibrary([]))
    assert not result.candidates
    assert any(row.code == "G6_E_SYNTH_BINDINGS" for row in result.refusals)


def test_execution_runs_probe_and_charges_actual_runs(workspace, recorded):
    ledger = CostLedger()
    reasons = []
    with ledger.measure("probe_candidate"):
        result = execution.run_candidate(probe_candidate(), [observation(probe_input=inputs())],
                                         lambda _: None, reasons=reasons)
    assert result == ["concern"]
    assert reasons == ["lost_kill"]
    assert workspace
    assert recorded[0] and all(row[2]["workspace"] == Path(".build") for row in recorded[0])
    assert ledger.total("acquisition").probe_runs == len(recorded[0]) == 10
    assert ledger.total("acquisition").sandbox_runs == 0


def test_execution_missing_input_abstains_with_reason(monkeypatch):
    def no_folder(**kwargs):
        raise AssertionError("missing input must not create a workspace")

    monkeypatch.setattr(confine, "run_folder", no_folder)
    monkeypatch.delenv("G6_REQUIRE_ISOLATION", raising=False)
    reasons = []
    ledger = CostLedger()
    with ledger.measure("missing_probe_input"):
        assert execution.run_candidate(probe_candidate(), [observation()], lambda _: None,
                                       reasons=reasons) == ["abstain"]
    assert reasons and "required" in reasons[0].lower()
    assert ledger.total("acquisition").probe_runs == 0


def test_execution_requires_isolation_before_runner(monkeypatch, workspace, recorded):
    monkeypatch.delenv("G6_REQUIRE_ISOLATION", raising=False)
    with pytest.raises(execution.ExecutionRefused) as caught:
        execution.run_candidate(probe_candidate(), [observation(probe_input=inputs())], lambda _: None)
    assert caught.value.code == "G6_E_ISOLATION_UNAVAILABLE"
    assert not recorded[0] and not workspace


def test_execution_passes_budget_and_bindings_before_any_run(workspace, recorded):
    with pytest.raises(execution.ExecutionRefused) as caught:
        execution.run_candidate(probe_candidate(budget={**BUDGET, "probe_runs": 8}),
                                [observation(probe_input=inputs())], lambda _: None)
    assert caught.value.code == "G6_E_PROBE_BUDGET"
    assert not recorded[0]


def test_execution_base_probe_receives_full_input(workspace, recorded):
    data = inputs().model_copy(update={"base_implementation": BASE, "new_tests": WEAK})
    assert execution.run_candidate(probe_candidate("discriminates_at_base.v2", {"reruns": 2}),
                                   [observation(probe_input=data)], lambda _: None) == ["concern"]
    assert len(recorded[0]) == 5


def test_case_record_builds_input_and_training_preserves_it():
    case = CaseRecord(unit_id="sample", provenance=dict(source="operator", cohort="development",
        source_ref="fixture", source_sha256=digest("fixture")), files={"subject.py": FIXED},
        target="subject.py", changed_lines=(2,), before_tests=BEFORE["tests/test_answer.py"],
        after_tests=WEAK["tests/test_answer.py"], known_wrong=(BASE,))
    data = probes.ProbeInput.from_case(case)
    assert data == inputs()
    assert data.base_implementation is None and data.new_tests is None
    concept, training = quiet_case()
    _, observations, _ = synthesis._training_view(concept, [replace(training, probe_input=data)])
    assert observations[0].probe_input == data


def test_operator_preserves_probe_input_through_binding_and_replay(workspace, recorded):
    from g6.distill import distill_operator as operator
    from tests.distill.test_distill_operator import D, case

    original, store = case()
    original = replace(original, before=BEFORE, after=WEAK, probe_input=inputs())
    bound, = operator.bind_oracle_labels([original], oracle_store=store, registered_policy_sha256=D)
    assert operator._training_cases([bound])[0].probe_input == inputs()
    assert operator._replay(probe_candidate(), [bound]) == ["concern"]


def test_execution_still_refuses_seed_bindings():
    raw = probe_candidate().model_dump(exclude={"candidate_id"})
    raw["body"] = {"template": "assert_strength.v1", "bindings": {"reruns": 2}}
    with pytest.raises(execution.ExecutionRefused) as caught:
        execution.run_candidate(VerifierCandidate(**raw), [observation()], lambda _: None)
    assert caught.value.code == "G6_E_SYNTH_BINDINGS"
