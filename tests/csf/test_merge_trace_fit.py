# SPDX-License-Identifier: Apache-2.0
import pytest

from g6.csf import BoundedAgent, CSFInputError, ResourceTriple, fit_from_trace, reachability_bound
from g6.csf import schema

TRACE = [("start", "done")] * 6 + [("start", "injury")] * 2 + [("done", "done")] * 2 + [("injury", "injury")] * 2


N1 = ResourceTriple(N=1)


def test_trace_fits_existing_model_with_counts_and_realised_hazard():
    # Removal: trace.py `unsafe_states=unsafe` loses external hazard labels (CSF_AUTO_MODEL-01/D1).
    agent = fit_from_trace(TRACE, unsafe={"injury"}, min_support=2, resources=N1)
    assert isinstance(agent, BoundedAgent)
    assert agent.transition_counts[("start", "injury")] == 2
    report = reachability_bound(agent, epsilon=0.2)
    # done has two outgoing observations: its hazard is bounded only by 1 - 0.025 ** (1/2) (95%, two safe states).
    assert abs(report.vub - (1 - 0.025 ** 0.5)) < 1e-9 and report.completion_state == "blocked-escalated"
    assert report.model["transition_counts"][1]["count"] == 2


def test_unseen_hazard_on_thin_data_earns_no_bound():
    # Removal: trace.py `per_state = {s: upper_bound(...) ...}` (back to observed frequencies) bounds this at 0 (D1).
    trace = [("start", "done")] * 2 + [("done", "done")] * 2 + [("injury", "injury")] * 2
    report = reachability_bound(fit_from_trace(trace, unsafe={"injury"}, min_support=2, resources=N1), epsilon=0.5)
    assert report.vub > 0.5 and not report.meets_bound and report.completion_state != "qualified-draft"


def test_a_well_supported_trace_gives_a_conservative_usable_bound():
    # Removal: trace.py `transitions.append({... "to": UNOBSERVED ...})` drops the unseen hazard the bound must carry.
    trace = ([("start", "done")] * 2999 + [("start", "injury")] + [("done", "done")] * 3000
             + [("injury", "injury")] * 3)
    agent = fit_from_trace(trace, unsafe={"injury"}, min_support=1, resources=N1)
    report = reachability_bound(agent, epsilon=0.01)
    assert 1 / 3000 < report.vub < 0.01 and report.completion_state == "qualified-draft"
    fit = report.to_envelope()["evidence"][0]["model"]["fit"]
    assert "Clopper-Pearson" in fit["method"] and fit["confidence"] == 0.95
    assert abs(fit["hazard_upper_bound"] - report.vub) < 1e-12


def test_resources_must_be_given():
    # Removal: trace.py `resources: ResourceTriple` with no default (the old default was a one-step horizon).
    with pytest.raises(TypeError):
        fit_from_trace(TRACE, unsafe={"injury"}, min_support=2)


def test_the_reserved_hazard_state_name_is_refused():
    # Removal: trace.py `if UNOBSERVED in states:` lets a trace state merge with the unseen-hazard state.
    with pytest.raises(CSFInputError, match="G6_E_CSF_TRACE_INPUT"):
        fit_from_trace([("a", "<unobserved>"), ("<unobserved>", "<unobserved>")], unsafe={"a"}, min_support=1,
                       resources=N1)


@pytest.mark.parametrize("trace", [[], [("a", "b")], [("a", "a")] * 30 + [("a", "b"), ("b", "b")]])
def test_cold_start_and_thin_transitions_refuse_without_fallback(trace):
    # Removal: trace.py `if not counts or any(n < min_support ...)` admits cold/thin models (CSF_AUTO_MODEL-02).
    with pytest.raises(CSFInputError, match="G6_E_CSF_TRACE_SUPPORT"):
        fit_from_trace(trace, unsafe={"b"}, min_support=2, resources=N1)


def test_terminal_states_are_not_given_invented_self_loops():
    # Removal: trace.py `return FittedAgent(...)` bypasses validate_transitions' completeness check.
    with pytest.raises(CSFInputError, match="no outgoing"):
        fit_from_trace([("a", "b")] * 3, unsafe={"b"}, min_support=2, resources=N1)


def test_validator_failure_refuses_model(monkeypatch):
    # Removal: trace.py `except Exception as exc:` loses the typed fail-closed dependency refusal.
    def broken(*args):
        raise RuntimeError("validator unavailable")
    monkeypatch.setattr(schema, "validate_transitions", broken)
    with pytest.raises(CSFInputError, match="G6_E_CSF_TRACE_FAILED"):
        fit_from_trace(TRACE, unsafe={"injury"}, min_support=2, resources=N1)


@pytest.mark.parametrize("unsafe", [set(), {"typo"}])
def test_missing_or_unknown_hazard_labels_refuse(unsafe):
    # Removal: trace.py `if not unsafe:` or BoundedAgent validation permits an unnamed hazard.
    with pytest.raises(CSFInputError):
        fit_from_trace(TRACE, unsafe=unsafe, min_support=2, resources=N1)


def test_trace_reader_failure_refuses_partial_model():
    # Removal: trace.py `except Exception as exc:` would lose the typed refusal for failed trace input.
    def broken_trace():
        yield ("a", "a")
        raise OSError("trace unavailable")
    with pytest.raises(CSFInputError, match="G6_E_CSF_TRACE_FAILED"):
        fit_from_trace(broken_trace(), unsafe={"a"}, min_support=1, resources=N1)
