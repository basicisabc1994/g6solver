"""Each registered probe keeps its declaration and resource bounds."""
import pytest

from g6.distill import execution, probes
from g6.distill.cost_ledger import CostLedger
from g6.distill.records import VerifierCandidate
from tests.distill import test_probe_wiring as wiring
from tests.distill.test_probe_wiring import inputs, observation, probe_candidate

recorded = wiring.recorded
workspace = wiring.workspace

def declared_probe(**updates):
    raw = probe_candidate().model_dump(exclude={"candidate_id"})
    raw["contract"].pop("content_id", None)
    raw["contract"]["probes"][0].update(updates)
    return VerifierCandidate(**raw)


def test_individual_run_limit_refuses_without_uncharged_work(workspace, recorded):
    ledger = CostLedger()
    with ledger.measure("limited_probe"):
        with pytest.raises(execution.ExecutionRefused) as caught:
            execution.run_candidate(declared_probe(runs=1), [observation(probe_input=inputs())], lambda _: None)
    assert caught.value.code == "G6_E_PROBE_BUDGET"
    assert ledger.total("acquisition").probe_runs == len(recorded[0]) == 0


def test_individual_wall_limit_refuses_without_runs(workspace, recorded):
    ledger = CostLedger()
    with ledger.measure("short_probe"):
        with pytest.raises(execution.ExecutionRefused) as caught:
            execution.run_candidate(declared_probe(wall_s=.001), [observation(probe_input=inputs())], lambda _: None)
    assert caught.value.code == "G6_E_PROBE_BUDGET"
    assert ledger.total("acquisition").probe_runs == len(recorded[0]) == 0


def test_individual_timeout_charges_completed_run(workspace, recorded, monkeypatch):
    now = [probes.time.monotonic()]
    original = probes._stage_run

    def staged(*args, **kwargs):
        now[0] += 3
        return original(*args, **kwargs)

    monkeypatch.setattr(probes.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(probes, "_stage_run", staged)
    ledger = CostLedger()
    with ledger.measure("late_probe"):
        with pytest.raises(execution.ExecutionRefused) as caught:
            execution.run_candidate(declared_probe(wall_s=2), [observation(probe_input=inputs())], lambda _: None)
    assert caught.value.code == "G6_E_PROBE_BUDGET"
    assert ledger.total("acquisition").probe_runs == len(recorded[0]) == 1


@pytest.mark.parametrize("declaration", ["missing", "mismatch", "duplicate"])
def test_probe_declaration_must_match_template(workspace, recorded, declaration):
    raw = probe_candidate().model_dump(exclude={"candidate_id"})
    contract = raw["contract"]
    contract.pop("content_id", None)
    if declaration == "missing":
        contract["probes"] = []
    elif declaration == "mismatch":
        contract["probes"][0]["name"] = "discriminates_at_base.v2"
    else:
        contract["probes"][0]["runs"] = 10
        contract["probes"] *= 2
    candidate = VerifierCandidate(**raw)
    with pytest.raises(execution.ExecutionRefused) as caught:
        execution.run_candidate(candidate, [observation(probe_input=inputs())], lambda _: None)
    assert caught.value.code == "G6_E_SYNTH_PROBES"
    assert not recorded[0]


def test_static_template_cannot_declare_a_probe(workspace, recorded):
    raw = probe_candidate().model_dump(exclude={"candidate_id"})
    raw["body"] = {"template": "assert_strength.v1", "bindings": {}}
    with pytest.raises(execution.ExecutionRefused) as caught:
        execution.run_candidate(VerifierCandidate(**raw), [observation(probe_input=inputs())], lambda _: None)
    assert caught.value.code == "G6_E_SYNTH_PROBES"
    assert not recorded[0]
