# SPDX-License-Identifier: Apache-2.0
"""g6.csf's model, its one validator and the union bound (audit 012: CSF-002, CSF-006, CSF-007, CSF-008, CSF-012,
CSF-013, CSF-015), with survival controls for every gate.

The archive cannot run here; each regression test says in its docstring what the archive did on the same input. Each
``test_null_*`` puts that archive behaviour back in memory (monkeypatching only) and shows the regression test fails.
"""
from __future__ import annotations

import copy
import dataclasses
import inspect
import itertools
import pickle
import random
import textwrap

import pytest

import g6.csf
from g6.csf import schema, verifier
from g6.csf.schema import EPSILON_INVALID, MODEL_INVALID, CSFInputError, ResourceTriple, TransitionOutcome
from g6.invariants import check_envelope


def _rows(*triples: tuple[str, str, float]) -> list[dict[str, object]]:
    return [{"from": a, "to": b, "probability": p} for a, b, p in triples]


def _agent(states, triples, initial, unsafe, n):
    return schema.BoundedAgent(states=states, transitions=_rows(*triples), initial_state=initial,
                               unsafe_states=unsafe, resources=ResourceTriple(N=n))


def _fails(test, *args) -> None:
    """The regression test ``test`` fails (an assertion, or a ``pytest.raises`` that saw nothing raised)."""
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        test(*args)


def _refused(call, fragment: str, code: str = MODEL_INVALID) -> None:
    with pytest.raises(CSFInputError) as caught:
        call()
    assert caught.value.code == code and fragment in caught.value.reason, caught.value.reason


# --- archive behaviours, for the null-instrument checks ---------------------------------------------------------------

def _archive_bridge_kernel(states, transitions):
    """The archive's formal-bridge kernel (bridge_handlers.py:47-65): rows that are not dicts skipped, an unreadable
    probability read as 1.0, unknown states kept, no sum check, a state with no row a self-loop, no transitions at all
    a synthetic cycle."""
    states = list(states)
    rows = list(transitions)
    if not rows:
        return {s: (TransitionOutcome(states[(i + 1) % len(states)], 1.0),) for i, s in enumerate(states)}
    by_source: dict[object, list[TransitionOutcome]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            p = float(row.get("probability", row.get("prob", 1.0)))
        except (TypeError, ValueError):
            p = 1.0
        by_source.setdefault(row.get("from"), []).append(TransitionOutcome(row.get("to"), p))
    return {s: tuple(by_source.get(s) or (TransitionOutcome(s, 1.0),)) for s in states}


_MASSES = verifier._unsafe_mass_by_step


def _archive_union_bound_masses(agent):
    """The archive's ``_union_bound`` (verifier.py:73-122): only successor states are scored, never step 0."""
    return _MASSES(agent)[1:]


def _archive_worst_case_masses(agent):
    """The archive's ``worst_case`` (verifier.py:174-177): |unsafe| / |states| * N, whatever the transitions say."""
    return [len(agent.unsafe_states) / len(agent.states) * agent.resources.N]


# --- survival controls: a gate that refused everything, or a bound that was always 1, would fail these ----------------

def test_a_correct_bound_on_a_hand_checked_chain_still_comes_out() -> None:
    """s0 -> s1 (0.6) | bad (0.4), both absorbing, N=2: P(unsafe at t) = 0, 0.4, 0.4, so vub = 0.8 (the true reach
    probability is 0.4; the union bound counts the absorbing unsafe state at both steps)."""
    agent = _agent(["s0", "s1", "bad"], [("s0", "s1", 0.6), ("s0", "bad", 0.4), ("s1", "s1", 1.0),
                                          ("bad", "bad", 1.0)], "s0", ["bad"], 2)
    report = verifier.reachability_bound(agent, 0.8)
    assert [s.unsafe_mass for s in report.steps] == pytest.approx([0.0, 0.4, 0.4])
    assert report.vub == pytest.approx(0.8) and report.meets_bound is True
    assert report.completion_state == "qualified-draft"
    assert verifier.reachability_bound(agent, 0.79).meets_bound is False


def test_a_model_that_never_reaches_an_unsafe_state_has_bound_zero() -> None:
    agent = _agent(["a", "b", "bad"], [("a", "b", 1.0), ("b", "a", 1.0), ("bad", "bad", 1.0)], "a", ["bad"], 50)
    report = verifier.reachability_bound(agent, 0.0)
    assert report.vub == 0.0 and report.meets_bound is True and report.completion_state == "qualified-draft"


def test_zero_probability_rows_are_accepted_and_dropped() -> None:
    agent = _agent(["a", "bad"], [("a", "a", 1.0), ("a", "bad", 0.0), ("bad", "bad", 1.0)], "a", ["bad"], 3)
    assert agent.kernel["a"] == (TransitionOutcome("a", 1.0),)
    assert verifier.reachability_bound(agent, 0.0).vub == 0.0


def test_an_agent_is_plain_data_that_rebuilds_from_its_description() -> None:
    agent = _agent(["a", "bad"], [("a", "a", 0.75), ("a", "bad", 0.25), ("bad", "bad", 1.0)], "a", ["bad"], 4)
    assert pickle.loads(pickle.dumps(agent)) == agent and hash(agent) == hash(copy.deepcopy(agent))
    d = agent.describe()
    rebuilt = schema.BoundedAgent(d["states"], d["transitions"], d["initial_state"], d["unsafe_states"],
                                  ResourceTriple(N=d["horizon_N"], T=d["declared_T"], M=d["declared_M"]))
    assert rebuilt == agent


def _random_chain(rng: random.Random):
    n = rng.randint(1, 5)
    states = [f"s{i}" for i in range(n)]
    triples = []
    for s in states:
        succ = rng.sample(states, rng.randint(1, n))
        weights = [rng.randint(1, 4) for _ in succ]
        triples += [(s, d, w / sum(weights)) for d, w in zip(succ, weights, strict=True)]
    unsafe = [s for s in states if rng.random() < 0.3]
    return _agent(states, triples, rng.choice(states), unsafe, rng.randint(1, 4))


def _paths(agent):
    """Every path of N steps from the initial state, with its probability (an independent oracle)."""
    frontier = [((agent.initial_state,), 1.0)]
    for _ in range(agent.resources.N):
        frontier = [(path + (o.next_state,), p * o.probability) for path, p in frontier for o in agent.kernel[path[-1]]]
    return frontier


def test_the_bound_matches_path_enumeration_on_random_chains() -> None:
    """Survival control and soundness: on 200 random chains vub equals sum_t P(X_t unsafe) from enumerating every
    path, and is at least the exact probability of reaching an unsafe state within N steps."""
    rng = random.Random(12)
    for _ in range(200):
        agent = _random_chain(rng)
        if not agent.unsafe_states:  # no unsafe state named: refused, never bounded at 0
            _refused(lambda agent=agent: verifier.reachability_bound(agent, 0.5), "no unsafe state")
            continue
        paths = _paths(agent)
        expected = sum(p * sum(s in agent.unsafe_states for s in path) for path, p in paths)
        reach = sum(p for path, p in paths if any(s in agent.unsafe_states for s in path))
        report = verifier.reachability_bound(agent, 0.5)
        assert report.vub == pytest.approx(min(1.0, expected), abs=1e-12)
        assert report.vub >= reach - 1e-12


# --- CSF-002: one validator; malformed models are refused, never read as safe ----------------------------------------

def test_csf002_transition_to_an_undeclared_state_is_refused() -> None:
    """Archive: ``bridge_check_z3`` with a -> "B" (a typo for b) kept the row; the mass sent to "B" vanished after step
    0, Z3 said unsat and the result was ``verified``."""
    _refused(lambda: _agent(["a", "b"], [("a", "B", 1.0), ("b", "b", 1.0)], "a", ["b"], 3), "undeclared state 'B'")


def test_csf002_unreadable_or_out_of_range_probabilities_are_refused() -> None:
    """Archive: an unreadable probability became 1.0 (bridge_handlers.py:55-58); a negative one was kept."""
    for bad in ("high", True, 1.5, -0.1, float("nan"), None):
        _refused(lambda bad=bad: _agent(["a", "b"], [("a", "b", bad), ("b", "b", 1.0)], "a", ["b"], 2),
                 "must be a real number in [0, 1]")


def test_csf002_probabilities_must_sum_to_one_and_every_state_needs_a_row() -> None:
    """Archive: no sum check on the bridge (the missing mass was lost), and a state with no row became a self-loop."""
    _refused(lambda: _agent(["a", "bad"], [("a", "a", 0.5), ("bad", "bad", 1.0)], "a", ["bad"], 2), "sum to 0.5")
    _refused(lambda: _agent(["a", "b", "bad"], [("a", "b", 1.0), ("bad", "bad", 1.0)], "a", ["bad"], 2),
             "state 'b' has no outgoing transition")


def test_csf002_rows_are_exactly_from_to_probability_and_unsafe_states_are_declared() -> None:
    """Archive: rows that were not dicts were skipped, a ``prob`` alias was read, and the unsafe set was never checked
    against the states (a typo there made every bound 0)."""
    states = ["a", "bad"]
    rows = [{"from": "a", "to": "a", "prob": 1.0}, {"from": "bad", "to": "bad", "probability": 1.0}]
    _refused(lambda: schema.BoundedAgent(states, rows, "a", ["bad"], ResourceTriple(N=1)), "exactly from, to")
    good = _rows(("a", "a", 1.0), ("bad", "bad", 1.0))
    _refused(lambda: schema.BoundedAgent(states, [*good, "a->bad"], "a", ["bad"], ResourceTriple(N=1)), "not a")
    _refused(lambda: schema.BoundedAgent(states, good, "a", ["Bad"], ResourceTriple(N=1)), "'Bad'")
    _refused(lambda: schema.BoundedAgent(states, good, "c", ["bad"], ResourceTriple(N=1)), "initial state 'c'")
    _refused(lambda: schema.BoundedAgent(states, [*good, good[0]], "a", ["bad"], ResourceTriple(N=1)), "twice")


def test_null_csf002_regressions_fail_on_the_archive_kernel(monkeypatch) -> None:
    monkeypatch.setattr(schema, "validate_transitions", _archive_bridge_kernel)
    _fails(test_csf002_transition_to_an_undeclared_state_is_refused)
    _fails(test_csf002_unreadable_or_out_of_range_probabilities_are_refused)
    _fails(test_csf002_probabilities_must_sum_to_one_and_every_state_needs_a_row)


# --- CSF-012: every model is explicit --------------------------------------------------------------------------------

def test_csf012_there_is_no_synthetic_model() -> None:
    """Archive: with no transitions every verify and bridge call ran on a synthetic cyclic kernel (and every MCP call
    had no way to pass transitions)."""
    _refused(lambda: _agent(["a", "bad"], [], "a", ["bad"], 2), "explicit transitions")


def test_null_csf012(monkeypatch) -> None:
    monkeypatch.setattr(schema, "validate_transitions", _archive_bridge_kernel)
    _fails(test_csf012_there_is_no_synthetic_model)


# --- CSF-007: step 0 counts; N=0 is refused --------------------------------------------------------------------------

def _starts_unsafe():
    return _agent(["bad", "ok"], [("bad", "ok", 1.0), ("ok", "ok", 1.0)], "bad", ["bad"], 1)


def test_csf007_an_unsafe_initial_state_counts() -> None:
    """Archive: ``_union_bound`` scored only successor states, so this agent, unsafe at step 0, got vub=0.0 and
    meets_bound=True, while its own Z3 encoding said p_unsafe=1.0."""
    report = verifier.reachability_bound(_starts_unsafe(), 0.2)
    assert report.steps[0].unsafe_mass == 1.0 and report.vub == 1.0
    assert report.meets_bound is False and report.completion_state == "blocked-escalated"


def test_csf007_a_zero_horizon_is_refused() -> None:
    """Archive: ResourceTriple accepted N=0, and a zero-step union bound was vacuously 0."""
    _refused(lambda: ResourceTriple(N=0), "between 1 and")


def _archive_resource_post_init(self) -> None:
    for name in ("T", "M", "N"):
        if getattr(self, name) < 0:
            raise ValueError(name)


def test_null_csf007(monkeypatch) -> None:
    monkeypatch.setattr(verifier, "_unsafe_mass_by_step", _archive_union_bound_masses)
    _fails(test_csf007_an_unsafe_initial_state_counts)
    monkeypatch.setattr(ResourceTriple, "__post_init__", _archive_resource_post_init)
    _fails(test_csf007_a_zero_horizon_is_refused)


# --- CSF-006: no worst_case, no method switch ------------------------------------------------------------------------

def test_csf006_a_reachable_unsafe_state_is_caught_and_worst_case_is_gone() -> None:
    """Archive: 11 states with s0 -> bad (p=1), N=1: ``worst_case`` gave vub = 1/11 <= 0.2, meets_bound=True, and a
    strategy spec could switch the gate to it. The true probability is 1."""
    states = [f"s{i}" for i in range(10)] + ["bad"]
    triples = [("s0", "bad", 1.0)] + [(s, s, 1.0) for s in states[1:]]
    report = verifier.reachability_bound(_agent(states, triples, "s0", ["bad"], 1), 0.2)
    assert report.vub == 1.0 and report.meets_bound is False and report.method == "union_bound"
    assert set(inspect.signature(verifier.reachability_bound).parameters) == {"agent", "epsilon", "request_id",
                                                                             "run_id"}
    assert not any(hasattr(mod, "worst_case") for mod in (verifier, g6.csf))


def test_null_csf006(monkeypatch) -> None:
    monkeypatch.setattr(verifier, "_unsafe_mass_by_step", _archive_worst_case_masses)
    _fails(test_csf006_a_reachable_unsafe_state_is_caught_and_worst_case_is_gone)


# --- CSF-008: p_unsafe is vub ----------------------------------------------------------------------------------------

def test_csf008_p_unsafe_is_vub_on_a_breaching_model() -> None:
    """Archive: SafetyDecisionReport.p_unsafe was never set, so csf_gate reported "P(unsafe)=0.0000 exceeds epsilon"
    and fed error_risk=0.0 to the trust envelope."""
    agent = _agent(["s0", "s1", "bad"], [("s0", "s1", 0.6), ("s0", "bad", 0.4), ("s1", "s1", 1.0),
                                          ("bad", "bad", 1.0)], "s0", ["bad"], 1)
    report = verifier.reachability_bound(agent, 0.2)
    assert report.p_unsafe == report.vub == pytest.approx(0.4) and not report.meets_bound
    envelope = report.to_envelope()
    assert envelope["p_unsafe"] == envelope["evidence"][0]["p_unsafe"] == report.vub


def test_null_csf008(monkeypatch) -> None:
    monkeypatch.setattr(verifier.SafetyReport, "p_unsafe", property(lambda self: 0.0))
    _fails(test_csf008_p_unsafe_is_vub_on_a_breaching_model)


# --- CSF-013: no interval or delta around an exact value -------------------------------------------------------------

def test_csf013_no_confidence_interval_or_pac_delta() -> None:
    """Archive: a Wilson interval with n = N "steps" around an exactly computed value, and a ``delta`` never used for
    sampling; ResourceTriple carried both epsilon and delta."""
    report = verifier.reachability_bound(_starts_unsafe(), 0.5)
    assert not any(hasattr(report, name) for name in ("confidence_interval", "delta"))
    assert {f.name for f in dataclasses.fields(ResourceTriple)} == {"N", "T", "M"}
    with pytest.raises(TypeError):
        ResourceTriple(N=1, delta=0.01)  # type: ignore[call-arg]


def test_null_csf013(monkeypatch) -> None:
    monkeypatch.setattr(verifier.SafetyReport, "confidence_interval", (0.0, 1.0), raising=False)
    _fails(test_csf013_no_confidence_interval_or_pac_delta)


# --- epsilon and envelope --------------------------------------------------------------------------------------------

def test_epsilon_outside_zero_one_is_refused() -> None:
    agent = _starts_unsafe()
    for bad in (-0.1, 1.5, float("nan"), True, "0.2", None):
        _refused(lambda bad=bad: verifier.reachability_bound(agent, bad), "epsilon must be", EPSILON_INVALID)
    _refused(lambda: verifier.reachability_bound("agent", 0.2), "BoundedAgent")  # type: ignore[arg-type]


def test_every_report_is_a_valid_envelope_never_verified_with_the_model_in_the_evidence() -> None:
    agent = _agent(["a", "bad"], [("a", "a", 0.9), ("a", "bad", 0.1), ("bad", "bad", 1.0)], "a", ["bad"], 2)
    for eps, state, code in ((0.5, "qualified-draft", "G6_E_CSF_MODEL_BOUND"),
                             (0.1, "blocked-escalated", "G6_E_CSF_BOUND_EXCEEDED")):
        envelope = verifier.reachability_bound(agent, eps, request_id="rq", run_id="rn").to_envelope()
        assert check_envelope(envelope) == [], envelope
        assert envelope["completion_state"] == state and envelope["warning_card"]["code"] == code
        assert envelope["request_id"] == "rq" and envelope["run_id"] == "rn" and envelope["degraded"] is True
        (evidence,) = envelope["evidence"]
        assert evidence["model"] == agent.describe() and evidence["measured"] is False
        assert evidence["assumptions"] == list(verifier.ASSUMPTIONS)
        assert evidence["unsafe_mass_by_step"] == pytest.approx([0.0, 0.1, 0.19])


# --- CSF-015: the package's documented example runs as written --------------------------------------------------------

def _doc_example() -> str:
    doc = g6.csf.__doc__ or ""
    block = doc.split("Capability label", 1)[1].split("What it is", 1)[0]
    return textwrap.dedent("\n".join(block.splitlines()[2:]))


def test_csf015_the_package_example_runs_and_its_numbers_are_right() -> None:
    """Archive: the documented example built ``BoundedAgent(name="robot_arm", hazard_probs={...})``, fields the class
    does not have, so it could not run."""
    namespace: dict[str, object] = {}
    exec(compile(_doc_example(), "g6.csf.__doc__", "exec"), namespace)  # noqa: S102 - our own documentation
    report = namespace["report"]
    assert (report.vub, report.meets_bound, report.completion_state) == (pytest.approx(0.1), True, "qualified-draft")


def test_null_csf015() -> None:
    archive = 'BoundedAgent(name="robot_arm", hazard_probs={"collision": 0.02, "drop": 0.01})'
    with pytest.raises(TypeError):
        exec(archive, {"BoundedAgent": schema.BoundedAgent})  # noqa: S102


def test_combinations_of_unsafe_labels_never_lower_the_bound() -> None:
    """Survival control for the labels: adding an unsafe state never lowers the bound (monotone in the labelling)."""
    states = ["a", "b", "c"]
    triples = [("a", "b", 0.5), ("a", "c", 0.5), ("b", "a", 1.0), ("c", "c", 1.0)]
    for small, large in itertools.combinations([("c",), ("b", "c"), ("a", "b", "c")], 2):
        low = verifier.reachability_bound(_agent(states, triples, "a", list(small), 3), 1.0).vub
        high = verifier.reachability_bound(_agent(states, triples, "a", list(large), 3), 1.0).vub
        assert high >= low
