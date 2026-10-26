# SPDX-License-Identifier: Apache-2.0
"""Trace proposers use the existing candidate/evaluation/gate loop."""
from types import SimpleNamespace

import pytest

from g6.core.llm import FakeLLM
from g6.harness import Check
from g6.metaharness import (
    CostStrategy, Model, Profile, QualityStrategy, SpeedStrategy, Suite, SuiteCase, Workflow, adapt,
    MeasuredStep, MeasuredTrace,
)
from g6.metaharness.propose import ProposerView

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("metaharness_merge_rig", Path(__file__).with_name("_merge_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "harness"):
    _spec.loader.exec_module(rig)
harness = rig.harness


def view(steps):
    return ProposerView("task", "Do {input}", (), tuple(s.name for s in steps), ("dear", "cheap"),
                        ("review",), {"routing": {s.name: "dear" for s in steps}}, (), (), 0,
                        traces=(MeasuredTrace(case_id="d", steps=tuple(steps)),))


def step(name="answer", **kw):
    return MeasuredStep(name=name, model="dear", latency_s=kw.pop("latency_s", 2),
                        input_tokens=100, output_tokens=50, **kw)


def router(monkeypatch):
    from g6.llm_router import advice
    monkeypatch.setattr(advice, "route", lambda *a, **k: SimpleNamespace(chain=["cheap", "dear"]))
    monkeypatch.setattr(advice, "estimate_cost", lambda model, *a, **k: SimpleNamespace(
        estimated_usd={"dear": 3.0, "cheap": 0.7}.get(model), prices_as_of="2026-10-01", stale_price=False))


def test_opt_cost_d1_ranks_measured_spend_without_a_savings_ratio(monkeypatch):
    router(monkeypatch)
    v = view([step("a", cost_usd=1), step("b", cost_usd=7)])
    strategy = CostStrategy(task="code_generation", providers=("ollama",))
    candidates = strategy.propose(v, lambda *a: [])
    assert candidates[0]["routing"] == {"a": "dear", "b": "cheap"}
    assert "saving" not in str(candidates).lower() and strategy.evidence[0]["cost_usd"] == 7
    # RED: removing `ranked.sort(key=lambda item: (-item[0], item[1].name))` changes the first proposal.


def test_opt_cost_02_unknown_price_is_not_zero(monkeypatch):
    router(monkeypatch)
    v = view([step(cost_usd=9)])
    from g6.llm_router import advice
    monkeypatch.setattr(advice, "estimate_cost", lambda *a, **k: SimpleNamespace(estimated_usd=None))
    assert CostStrategy(task="code_generation").propose(v, lambda *a: []) == []
    # RED: removing `if cost is None: continue` allows an unpriced route to look cheaper.


def test_opt_cost_04_proposing_never_fetches_catalogues(monkeypatch):
    router(monkeypatch)
    from g6.llm_router import providers
    monkeypatch.setattr(providers, "fetch_openrouter_catalogue", lambda *a, **k: pytest.fail("network fetch"))
    assert CostStrategy(task="code_generation").propose(view([step(cost_usd=9)]), lambda *a: [])
    # RED: removing `route = advice.route(self.task, providers=self.providers, ...)` loses the candidate; a fetch also fails.


def test_opt_speed_d1_follows_longest_dependency_path():
    trace = MeasuredTrace(case_id="d", steps=(step("join", depends_on=("left", "right"), latency_s=1),
                         step("right", latency_s=8), step("left", depends_on=("start",), latency_s=6),
                         step("start", latency_s=5)))
    assert trace.critical_path() == ("start", "left", "join")
    strategy = SpeedStrategy(faster_models={"left": "cheap", "right": "cheap"})
    candidates = strategy.propose(view(trace.steps), lambda *a: [])
    assert len(candidates) == 1 and candidates[0]["routing"]["left"] == "cheap"
    assert candidates[0]["routing"]["right"] == "dear"
    # RED: removing `if step.name not in critical: continue` proposes the off-path change.


def test_opt_speed_02_no_fixed_latency_gain():
    strategy = SpeedStrategy(faster_models={"answer": "cheap"})
    candidates = strategy.propose(view([step(latency_s=0.0003)]), lambda *a: [])
    assert candidates and set(candidates[0]) == {"routing"}
    assert strategy.evidence[0]["latency_s"] == 0.0003
    # RED: removing `self.evidence.append({"step": step.name, "latency_s": step.latency_s, ...})` loses measured duration.


@pytest.mark.parametrize("passed,checked,expected", [(None, (), True), (False, ("custom",), True),
                                                     (True, ("custom",), False)])
def test_opt_quality_01_d1_reads_outcomes_not_component_names(passed, checked, expected):
    got = QualityStrategy(checks={"answer": "review"}).propose(
        view([step(passed=passed, checked_by=checked)]), lambda *a: [])
    assert bool(got) == expected
    if got:
        assert got[0]["extra_checks"] == ["review"]
    # RED: removing `if step.passed is True and step.checked_by: continue` changes the checked survival case.


def test_opt_quality_02_never_invents_a_quality_delta():
    got = QualityStrategy(checks={"answer": "review"}).propose(view([step()]), lambda *a: [])
    assert got and set(got[0]) == {"routing", "extra_checks"}
    # RED: removing `out.append(candidate)` in the optional-check branch loses the added check.


@pytest.mark.parametrize("strategy", [CostStrategy(task="code_generation"),
    SpeedStrategy(faster_models={"answer": "cheap"}), QualityStrategy(checks={"answer": "review"})])
def test_opt_cost_01_opt_speed_01_candidates_use_the_metaharness_gate(tmp_path, monkeypatch, strategy):
    router(monkeypatch)
    def cases(prefix):
        return tuple(SuiteCase(f"{prefix}{i}", f"{prefix} question {i}", "right") for i in range(30))
    wf = Workflow("trace", "Answer {input}", (Check("nonempty", bool, check_id="text"),),
                  lambda out, expected: out == expected, Suite(cases("d"), cases("h")),
                  {"dear": Model(FakeLLM(default="wrong"), usage=lambda: {"cost_usd": 3.0,
                    "input_tokens": 100, "output_tokens": 50}), "cheap": Model(FakeLLM(default="wrong"))},
                  optional_checks={"review": Check("reviewed", lambda out: out == "right", check_id="review")})
    mh = adapt(wf, workspace=tmp_path)
    mh.confirm("Ada", initial=Profile(routing={"answer": "dear"}))
    result = mh.propose(strategy)
    assert result["completion_state"] == "qualified-draft"
    proposal = result["proposals"][0]
    report = mh.evaluate(proposal)["report"]
    assert report.n == 30 and report.split == "held_out"
    assert mh.decide(report, proposal)["decision"].verdict != "recommend"
    assert len(mh.store.versions()) == 1
    # RED: removing `traces=tuple(traces)` in build_view yields no measured-trace candidate.


def test_observed_serial_trace_preserves_actual_step_order(tmp_path):
    from dataclasses import replace
    from g6.metaharness.propose import DevBudget, build_view
    old, _ = harness(tmp_path / "old", missing=True)
    wf = replace(old.workflow, steps=("second", "first"))
    profile = Profile(routing={"first": "local", "second": "local"})
    def factory(ctx):
        def execute(contract):
            ctx.caller("first").complete("", contract.task)
            return ctx.caller("second").complete("", contract.task)
        return execute
    v = build_view(wf, factory, profile, theories=None, clock=lambda: 0.0, budget=DevBudget(100))
    trace = v.traces[0]
    assert tuple(s.name for s in trace.ordered()) == ("first", "second")
    assert trace.steps[1].depends_on == ("first",)
    assert all(s.passed is None and not s.checked_by for s in trace.steps)
    # RED: removing `depends_on=(steps[-1].name,) if steps else (),` loses the observed serial predecessor.


def test_existing_mcp_surface_uses_declared_trace_strategy(tmp_path):
    from g6.metaharness.surface import Surface
    mh, _ = harness(tmp_path, missing=True)
    # Reuse the configured handle; no environment file, subprocess, socket or G6 MCP tool is invoked.
    surface = Surface({"measures": {"workflow": mh.workflow,
                       "strategy": SpeedStrategy(faster_models={"answer": "local"})}})
    surface._harnesses["measures"] = mh
    reply = surface.propose("measures", session="proposer")
    assert reply["warning_card"]["code"] == "G6_E_META_NO_NEW_CANDIDATE"
    # RED: removing `strategy = entry.get("strategy")` from Surface.propose dispatch loses the declared strategy.

def test_largest_trace_chain_has_one_reused_path_and_bounded_speed_proposals(monkeypatch):
    from dataclasses import replace
    maximum = next(m.max_length for m in MeasuredTrace.model_fields["steps"].metadata if hasattr(m, "max_length"))
    names = tuple(f"s{i:05d}" for i in range(maximum))
    v = view([step(name, latency_s=1, depends_on=(names[i - 1],) if i else ())
              for i, name in enumerate(names)])
    trace, = v.traces
    assert trace.critical_path() == names
    computed, lookups = [], []
    real = MeasuredTrace.critical_path
    def critical_path(self):
        computed.append(self.case_id)
        return real(self)
    class Alternatives(dict):
        def get(self, key, default=None):
            lookups.append(key)
            return super().get(key, default)
    monkeypatch.setattr(MeasuredTrace, "critical_path", critical_path)
    strategy = SpeedStrategy(faster_models=Alternatives({n: "cheap" for n in names}), max_candidates=2)
    candidates = strategy.propose(replace(v, traces=(trace, trace)), lambda *a: [])
    assert len(candidates) == len(strategy.evidence) == 2
    assert [c["routing"][n] for c, n in zip(candidates, names)] == ["cheap", "cheap"]
    assert computed == ["d"] and lookups == list(names[:2])
    assert strategy.evidence[0]["critical_path"] == list(names)
    assert strategy.evidence[0]["critical_path"] is strategy.evidence[1]["critical_path"]
    # RED: removing `if len(out) >= self.max_candidates: return out` constructs proposals past the limit.


def test_zero_speed_candidate_limit_does_not_compute_a_path(monkeypatch):
    def unexpected(self):
        pytest.fail("no proposal budget remains")
    monkeypatch.setattr(MeasuredTrace, "critical_path", unexpected)
    assert SpeedStrategy({"answer": "cheap"}, max_candidates=0).propose(view([step()]), lambda *a: []) == []
    # RED: removing `if self.max_candidates <= 0: return out` computes a path with no budget.
