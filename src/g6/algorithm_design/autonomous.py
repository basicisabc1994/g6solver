# SPDX-License-Identifier: Apache-2.0
"""Bounded autonomous algorithm-design + SWE repair controller for a *host-approved* repository.

The proposing agent controls only a candidate Python source file. G6 controls:
frozen intent, static analysis, resource policy, test list, execution adapter,
evidence digests, the existing coding gate, and the stop policy.

A candidate is never installed into the source repository. Tests run against a
copy using G6's existing repo_tests mutant mechanism, *only if the host has an
actual OS confinement backend*. An absence of a backend is a refusal.

The interface is intentionally provider-independent: an LLM, coding agent or a
hard-coded producer can implement ProposalAgent. No provider calls occur here.
"""
from __future__ import annotations

import ast
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping, Protocol

from g6.algorithm_design.fusion import fuse
from g6.algorithm_design.ir import DesignProblem
from g6.algorithm_design.resources import DeviceBudget, ResourceEstimate, rank_options
from g6.algorithm_design.search import search_algorithms
from g6.coding_agent_gate.evidence import Evidence, VerifierResult
from g6.coding_agent_gate.gate import verify_answer
from g6.complexity.atlas import build_atlas
from g6.complexity.impact import impact_cone
from g6.complexity.ir import FrozenIntent, Hypothesis
from g6.core.ledger import ChainLedger, digest
from g6.harness.verification_controller import (
    Experiment, Receipt, VerificationState, next_experiment, rebase, reduce_observation,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative(path: str) -> bool:
    p = PurePosixPath(path)
    return (bool(path) and not p.is_absolute() and "\\" not in path and
            all(part not in ("", ".", "..") and not part.startswith(".") for part in p.parts)
            and str(p) == path)


@dataclass(frozen=True)
class DesignMission:
    """Caller/host-owned intent and resource limits, never constructed from agent output."""
    repo: str
    files: Mapping[str, str]  # read-only snapshot of package, rooted at source_root
    package: str
    source_root: str
    target_path: str  # relative to repository (one Python file; no test changes)
    tests: tuple[str, ...]
    intent: FrozenIntent
    problem: DesignProblem
    environment_digest: str
    device: DeviceBudget
    dimensions: Mapping[str, int]
    mode: Literal["algorithm", "swe", "combined"] = "combined"
    changed_symbols: tuple[str, ...] = ()
    max_attempts: int = 3
    timeout_per_run_s: int = 60
    execution_approved: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("algorithm", "swe", "combined") or not 1 <= self.max_attempts <= 5:
            raise ValueError("invalid mission mode or attempt limit")
        if not 1 <= self.timeout_per_run_s <= 300:
            raise ValueError("timeout must be between 1 and 300 seconds")
        if not _relative(self.target_path) or not self.target_path.endswith(".py"):
            raise ValueError("target_path must be a normal repository-relative Python file")
        if self.target_path.startswith(("tests/", "test_")) or "/tests/" in self.target_path:
            raise ValueError("autonomous agent cannot edit test files")
        if self.source_root and not _relative(self.source_root):
            raise ValueError("invalid source root")
        if not self.tests or any(not _relative(t.split("::", 1)[0]) for t in self.tests):
            raise ValueError("tests must be frozen repository-relative identities")
        if not self.environment_digest:
            raise ValueError("pin the intended execution environment")


@dataclass(frozen=True)
class PatchProposal:
    path: str
    source: str
    algorithm_id: str
    explanation: str = ""
    parent_digest: str = ""

    @property
    def fingerprint(self) -> str:
        return digest({"path": self.path, "source_sha256": _sha(self.source)})


@dataclass(frozen=True)
class AgentBrief:
    goal: str
    approved_algorithms: tuple[str, ...]
    complexity_findings: tuple[str, ...]
    impact: tuple[str, ...]
    preservation_checks: tuple[str, ...]
    proof_gaps: tuple[str, ...]
    resource_plans: tuple[ResourceEstimate, ...]
    attempted_digests: tuple[str, ...]
    last_failed_tests: tuple[str, ...]
    last_failure_excerpt: str
    remaining_attempts: int


class ProposalAgent(Protocol):
    """Agent adapter may synthesize, repair and explain; never access the locked evaluator."""
    def propose(self, brief: AgentBrief) -> PatchProposal | None: ...


@dataclass(frozen=True)
class TestObservation:
    verdict: str
    completed: bool
    isolated: bool
    backend: str
    environment_id: str
    checker_digest: str
    candidate_digest: str
    failed_tests: tuple[str, ...] = ()
    log_excerpt: str = ""
    host_observed: bool = False


class CandidateRunner(Protocol):
    """Custodian-supplied independent executable; the proposer never receives it."""
    def run(self, source: str, *, expected_path: str, tests: tuple[str, ...],
            timeout_s: int) -> TestObservation: ...


class RepoTestsRunner:
    """Production adapter to G6's existing isolated copied-repository test path.

    Only the Windows AppContainer backend currently satisfies G6's OS isolation
    requirement. On Linux/macOS, do NOT run untrusted code via this adapter:
    the preflight backend probe fails closed when no backend is available.
    """

    def __init__(self, mission: DesignMission) -> None:
        from g6.workforce import repo_tests
        self.repo = repo_tests.checked_repo(mission.repo)
        self.mission = mission
        self.target = mission.target_path
        self._source_key = self.target.removeprefix(mission.source_root + "/") if mission.source_root else self.target
        if self._source_key not in mission.files:
            raise ValueError("target source is missing from the pinned file map")
        self._base_sha = _sha(mission.files[self._source_key])
        self._checker = self._checker_digest()
        self._sources = self._source_digests()

    def _source_digests(self) -> dict[str, str]:
        root = (self.repo / self.mission.source_root).resolve() if self.mission.source_root else self.repo
        checked = {}
        for key, value in self.mission.files.items():
            if not _relative(key) or not key.endswith(".py"):
                raise ValueError("source map paths must be relative Python files")
            path = root / key
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError("source map path missing or symlinked")
            if path.read_text(encoding="utf-8") != value:
                raise ValueError("working tree differs from the pinned source map")
            checked[key] = _sha(value)
        return checked

    def _checker_digest(self) -> str:
        result = {}
        for test in self.mission.tests:
            p = (self.repo / test.split("::", 1)[0])
            if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(self.repo):
                raise ValueError("test path must be a normal checked-in file")
            result[str(p.relative_to(self.repo))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return digest({"tests": self.mission.tests, "files": result})

    def run(self, source: str, *, expected_path: str, tests: tuple[str, ...],
            timeout_s: int) -> TestObservation:
        if not self.mission.execution_approved:
            raise PermissionError("the host has not approved confined testing")
        if expected_path != self.target or tests != self.mission.tests:
            raise PermissionError("candidate attempted to change test or patch scope")
        if self._checker_digest() != self._checker or self._source_digests() != self._sources:
            raise ValueError("tests or snapshot changed after mission freeze")
        # Backend planning happens BEFORE any untrusted tests are executed.
        # repo_tests itself might run without isolation if left to its defaults.
        from g6.core import confine
        # Never change process-wide execution policy in a concurrent agent host.
        # The host MUST enable G6_REQUIRE_ISOLATION=1 before starting the process.
        if not confine.required():
            raise PermissionError("G6_REQUIRE_ISOLATION=1 required for autonomous testing")
        from g6.workforce import repo_tests
        from g6.core import confine as confinement
        raw = repo_tests.run(self.repo, list(tests), timeout_s=timeout_s,
                             network=False, mutant=(expected_path, source))
        if self._checker_digest() != self._checker or self._source_digests() != self._sources:
            raise ValueError("protected repository content drifted during execution")
        isolation = raw.get("isolation") or {}
        backend = isolation.get("backend") or ""
        isolated = backend == confinement.APPCONTAINER and not isolation.get("network")
        environment_id = digest({"python": raw.get("python"), "backend": backend,
                                 "interpreter_source": raw.get("interpreter_source")})
        return TestObservation(
            verdict=str(raw.get("verdict", "unknown")), completed=raw.get("completed") is True,
            isolated=isolated, backend=backend, environment_id=environment_id,
            checker_digest=self._checker, candidate_digest=_sha(source),
            failed_tests=tuple(str(x) for x in raw.get("failed_ids", ())[:30]),
            log_excerpt=str(raw.get("output", ""))[-1600:],
            host_observed=isolated,
        )


@dataclass(frozen=True)
class AgentReport:
    completion_state: str
    reason: str
    chosen_algorithm: str
    patch: PatchProposal | None
    resource_plans: tuple[ResourceEstimate, ...]
    impact: tuple[str, ...]
    preservation_obligations: tuple[str, ...]
    unknowns: tuple[str, ...]
    attempts: int
    observations: tuple[TestObservation, ...]
    audit_head: str
    gate_verdict: str = "not_run"


def run_agent(mission: DesignMission, producer: ProposalAgent,
              runner: CandidateRunner) -> AgentReport:
    """Autonomous bounded CEGIS: inspect -> model -> cost -> propose -> check -> revise.

    In production the host supplies RepoTestsRunner. Tests/proofs are frozen by the
    host, never synthesized by the model as authority. Nothing writes a patch,
    merges, deploys or creates a new standard.
    """
    cir = build_atlas(mission.files, package=mission.package, intent=mission.intent,
                      environment_digest=mission.environment_digest)
    change = mission.changed_symbols or (mission.package,)
    impact = impact_cone(cir, change)
    normative = tuple(sorted(o.id for o in mission.intent.obligations if o.normative))
    search = search_algorithms(mission.problem, max_candidates=16)
    viable = []
    plans = []
    gaps = set()
    for candidate in search.candidates:
        aligned = fuse(cir, candidate, required_target=mission.problem.target, impact=impact)
        costs = tuple(rank_options(candidate.steps, mission.dimensions, mission.device))
        plans.extend(costs)
        gaps.update(aligned.check_plan)
        if (aligned.matched_target and len(costs) == len(candidate.steps)
                and all(row.eligible for row in costs)
                and sum(row.operations or 0 for row in costs) <= mission.device.max_operations
                and max((row.ram_bytes or 0 for row in costs), default=0) <= mission.device.ram_bytes):
            viable.append((sum(row.operations or 0 for row in costs), candidate.steps))
    viable.sort(key=lambda r: (r[0], r[1]))
    choices = tuple(" + ".join(steps) for _, steps in viable)
    ledger = ChainLedger()
    ledger.append("mission", "initial", {"tree": cir.snapshot.tree_digest, "intent": cir.intent.fingerprint,
                                         "mode": mission.mode, "resources": mission.device.fingerprint,
                                         "tests_sha256": digest(mission.tests)})
    def result(state: str, reason: str, patch: PatchProposal | None = None,
               obs: tuple[TestObservation, ...] = (), gate: str = "not_run") -> AgentReport:
        return AgentReport(state, reason, patch.algorithm_id if patch else "", patch, tuple(plans),
                           impact.impacted, normative, tuple(sorted(set(cir.unknowns) | set(impact.uncertainty))),
                           len(obs), obs, ledger.head(), gate)
    if not normative:
        return result("blocked-escalated", "no_authoritative_obligations")
    if mission.mode != "swe" and not choices:
        return result("blocked-escalated", "no_cost_feasible_algorithm_design")
    if not mission.execution_approved:
        return result("blocked-escalated", "execution_not_approved")
    key = mission.target_path.removeprefix(mission.source_root + "/") if mission.source_root else mission.target_path
    if key not in mission.files:
        return result("blocked-escalated", "target_not_in_snapshot")
    accepted = (set(choices) if mission.mode != "swe" else {"swe_refactor"})
    # A baseline that fails cannot be treated as evidence of a regression-free repair.
    try:
        baseline = runner.run(mission.files[key], expected_path=mission.target_path,
                              tests=mission.tests, timeout_s=mission.timeout_per_run_s)
    except (Exception) as exc:  # environment and containment failures are blockers, not evidence
        ledger.append("refusal", "baseline", {"error_type": type(exc).__name__})
        return result("blocked-escalated", "baseline_could_not_run")
    ledger.append("baseline", "baseline", {"verdict": baseline.verdict, "isolated": baseline.isolated,
                                          "source": baseline.candidate_digest})
    if not (baseline.isolated and baseline.completed and baseline.verdict in ("passed", "failed")
            and baseline.candidate_digest == _sha(mission.files[key])):
        return result("blocked-escalated", "baseline_not_independently_established", obs=(baseline,))
    checker = baseline.checker_digest
    environment = baseline.environment_id
    if not checker or not environment:
        return result("blocked-escalated", "unbound_baseline", obs=(baseline,))
    hypotheses = (Hypothesis("regression", "A regression in changed behavior", "high"),
                  Hypothesis("hidden_dependency", "An unmodelled shared effect", "medium"),
                  Hypothesis("wrong_environment", "Wrong runtime loaded", "medium"))
    state = VerificationState(cir.snapshot.tree_digest, environment, checker,
                              hypotheses, mission.max_attempts)
    observed = [baseline]
    seen = set()
    last_failed: tuple[str, ...] = baseline.failed_tests if baseline.verdict == "failed" else ()
    failure_excerpt = baseline.log_excerpt if baseline.verdict == "failed" else ""
    for i in range(mission.max_attempts):
        brief = AgentBrief(mission.problem.target, choices if mission.mode != "swe" else ("swe_refactor",),
                           tuple(f.root_cause for f in cir.findings), impact.impacted, normative,
                           tuple(sorted(gaps)), tuple(plans), tuple(sorted(seen)),
                           last_failed, failure_excerpt, mission.max_attempts - i)
        try:
            patch = producer.propose(brief)
        except Exception as exc:
            ledger.append("refusal", f"agent-{i}", {"error_type": type(exc).__name__})
            return result("blocked-escalated", "proposer_error", obs=tuple(observed))
        if patch is None:
            return result("blocked-escalated", "agent_exhausted_options", obs=tuple(observed))
        reason = _patch_problem(patch, mission.target_path, accepted, seen)
        if reason:
            ledger.append("refusal", f"candidate-{i}", {"reason": reason})
            return result("blocked-escalated", reason, obs=tuple(observed))
        seen.add(patch.fingerprint)
        state = rebase(state, tree_digest=patch.fingerprint, environment_digest=environment,
                       checker_digest=checker)
        experiment = Experiment(f"candidate-{i}", "frozen_regression", ("regression", "hidden_dependency"),
                                1, allowed=True)
        if next_experiment(state, (experiment,)) is None:
            break
        try:
            observation = runner.run(patch.source, expected_path=patch.path,
                                     tests=mission.tests, timeout_s=mission.timeout_per_run_s)
        except Exception as exc:
            ledger.append("refusal", f"run-{i}", {"error_type": type(exc).__name__})
            return result("blocked-escalated", "candidate_runner_refused", obs=tuple(observed))
        observed.append(observation)
        valid = (observation.isolated and observation.completed and
                 observation.checker_digest == checker and observation.environment_id == environment
                 and observation.candidate_digest == _sha(patch.source))
        receipt = Receipt(experiment.id, patch.fingerprint, environment, checker,
                          "host" if observation.host_observed else "declared",
                          "pass" if observation.verdict == "passed" else
                          "fail" if observation.verdict == "failed" else "error",
                          observation.isolated and valid)
        state = reduce_observation(state, experiment, receipt)
        ledger.append("observation", f"run-{i}", {
            "patch_digest": patch.fingerprint, "status": observation.verdict,
            "bound": valid, "checker": observation.checker_digest,
            "environment": observation.environment_id, "failed_tests": list(observation.failed_tests)})
        if not valid:
            return result("blocked-escalated", "stale_or_unconfined_observation", obs=tuple(observed))
        if observation.verdict == "passed":
            # Reuse G6's existing non-promoting completion gate. A local test pass
            # is never a proof of total correctness or authorisation to deploy.
            evidence = Evidence("test", "frozen-regression:" + patch.fingerprint[:12],
                                exit_code=0, observed_by="g6" if
                                isinstance(runner, RepoTestsRunner) and observation.host_observed else "caller")
            decision = verify_answer(
                mission.problem.target, "Patch candidate produced for human review.",
                evidence=(evidence,), verifier=VerifierResult("pass", 0, "frozen_regression",
                         "g6" if evidence.observed_by == "g6" else "caller"),
                task_contract="; ".join(mission.intent.requirements[j].text
                                       for j in range(len(mission.intent.requirements))),
                risk_tier="medium")
            if decision.verdict != "pass":
                return result("blocked-escalated", "coding_gate_refused", obs=tuple(observed), gate=decision.verdict)
            ledger.append("candidate", f"success-{i}", {"patch_digest": patch.fingerprint,
                                                        "gate": decision.verdict})
            return result("qualified-draft", "frozen_tests_passed_pending_independent_review",
                          patch, tuple(observed), decision.verdict)
        if observation.verdict != "failed":
            return result("blocked-escalated", "test_result_inconclusive", obs=tuple(observed))
        last_failed = observation.failed_tests
        failure_excerpt = observation.log_excerpt[-1200:]
    return result("blocked-escalated", "repair_attempt_limit", obs=tuple(observed))


def _patch_problem(patch: PatchProposal, target: str, choices: set[str], seen: set[str]) -> str:
    if patch.path != target:
        return "patch_outside_frozen_scope"
    if patch.algorithm_id not in choices:
        return "unapproved_algorithm_choice"
    if not isinstance(patch.source, str) or len(patch.source.encode("utf-8")) > 512_000:
        return "candidate_size_limit"
    if patch.fingerprint in seen:
        return "identical_candidate_repeated"
    try:
        ast.parse(patch.source, filename=target)
    except (SyntaxError, ValueError, RecursionError):
        return "candidate_not_parseable"
    return ""
