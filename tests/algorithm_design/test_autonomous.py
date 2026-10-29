# SPDX-License-Identifier: Apache-2.0
"""Hard-coded resource policy and autonomous CEGIS controls with a fake host runner.

Fake-runner tests do not establish platform isolation or end-to-end production execution.
"""
from dataclasses import replace

from g6.algorithm_design.autonomous import (
    DesignMission, PatchProposal, TestObservation, run_agent,
)
from g6.algorithm_design.ir import DesignProblem
from g6.algorithm_design.resources import DeviceBudget, estimate
from g6.complexity.ir import FrozenIntent, Obligation, Requirement


def _mission(*, mode="swe", attempts=3, approved=True):
    intent = FrozenIntent(
        (Requirement("r1", "Preserve public function output", "stated", "person"),),
        (Obligation("o1", "r1", "assert original behavior", "person", True),))
    return DesignMission(
        repo="/unused", files={"app/__init__.py": "", "app/logic.py": "def f(x):\n    return x + 1\n"},
        package="app", source_root="", target_path="app/logic.py", tests=("tests/test_logic.py",),
        intent=intent, problem=DesignProblem(("sequence",), "membership"), environment_digest="env1",
        device=DeviceBudget(1_000_000, 100_000, 1_000_000),
        dimensions={"n": 50}, mode=mode, max_attempts=attempts,
        execution_approved=approved)


class Producer:
    def __init__(self, sources):
        self.sources = iter(sources)
        self.briefs = []

    def propose(self, brief):
        self.briefs.append(brief)
        source = next(self.sources, None)
        return PatchProposal("app/logic.py", source, "swe_refactor") if source is not None else None


class Runner:
    def __init__(self, verdicts):
        self.verdicts = iter(verdicts)
        self.sources = []

    def run(self, source, *, expected_path, tests, timeout_s):
        import hashlib
        self.sources.append(source)
        verdict = next(self.verdicts)
        return TestObservation(verdict, True, True, "fake-host", "env1", "checks",
                               hashlib.sha256(source.encode()).hexdigest(),
                               ("tests/test_logic.py",) if verdict == "failed" else (),
                               "assertion failure" if verdict == "failed" else "", True)


def test_resource_planner_flags_binary_bit_complexity():
    d = DeviceBudget(8_000_000, 0, 1_000_000, allow_pseudopolynomial=False)
    r = estimate("knapsack_dp", {"n": 100, "W": 1000}, d)
    assert not r.eligible and r.complexity_kind == "pseudo_polynomial_in_numeric_W"
    assert "pseudo_polynomial_disallowed" in r.reasons


def test_resource_planner_rejects_oversized_and_unmodeled():
    d = DeviceBudget(1000, 0, 100)
    assert not estimate("sort_comparison", {"n": 10_000}, d).eligible
    assert not estimate("unknown_primitive", {"n": 20}, d).eligible
    assert not estimate("linear_scan", {"n": -1}, d).eligible


def test_no_gpu_unless_explicitly_checked_by_host():
    d = DeviceBudget(1_000_000, 1_000_000, 1_000_000, accelerator="cuda")
    assert estimate("linear_scan", {"n": 30}, d).device == "cpu"
    verified_kernel = replace(d, checked_accelerator_kernels=("linear_scan",))
    assert estimate("linear_scan", {"n": 30}, verified_kernel).device == "cuda"


def test_autonomous_repair_from_failing_baseline():
    mission = _mission()
    producer = Producer(["def f(x):\n    return x - 1\n", "def f(x):\n    return x + 2\n"])
    runner = Runner(["failed", "failed", "passed"])
    report = run_agent(mission, producer, runner)
    assert report.completion_state == "qualified-draft"
    assert report.reason == "frozen_tests_passed_pending_independent_review"
    assert report.attempts == 3  # baseline plus two tested proposals
    assert len(producer.briefs) == 2
    assert "tests/test_logic.py" in producer.briefs[1].last_failed_tests
    assert report.patch.source.endswith("return x + 2\n")
    assert report.gate_verdict == "pass"


def test_agent_cannot_change_tests_or_scope():
    mission = _mission()
    class Bad:
        def propose(self, brief):
            return PatchProposal("tests/test_logic.py", "assert True", "swe_refactor")
    report = run_agent(mission, Bad(), Runner(["passed"]))
    assert report.completion_state == "blocked-escalated"
    assert report.reason == "patch_outside_frozen_scope"


def test_budget_and_duplicate_stop():
    mission = _mission(attempts=3)
    source = "def f(x):\n    return x - 1\n"
    report = run_agent(mission, Producer([source, source]), Runner(["passed", "failed"]))
    assert report.reason == "identical_candidate_repeated"


def test_unconfined_baseline_refused():
    class Unsafe(Runner):
        def run(self, source, **kwargs):
            return replace(super().run(source, **kwargs), isolated=False)
    report = run_agent(_mission(), Producer([]), Unsafe(["passed"]))
    assert report.reason == "baseline_not_independently_established"


def test_no_permission_means_no_proposer_and_no_runner():
    producer = Producer(["def f(x): return 1"])
    runner = Runner(["passed"])
    report = run_agent(_mission(approved=False), producer, runner)
    assert report.reason == "execution_not_approved"
    assert not producer.briefs and not runner.sources


def test_algorithm_mode_requires_cost_feasible_choice():
    mission = replace(_mission(mode="algorithm"), device=DeviceBudget(1024, 0, 1))
    report = run_agent(mission, Producer([]), Runner([]))
    assert report.reason == "no_cost_feasible_algorithm_design"
