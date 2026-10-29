# SPDX-License-Identifier: Apache-2.0
"""Bounded autonomous algorithm-design + SWE repair controller for a *host-approved* repository.

The proposing agent controls only a candidate Python source file. G6 controls:
frozen intent, static analysis, resource policy, test list, execution adapter,
evidence digests, the existing coding gate, and the stop policy.

A candidate is never installed into the source repository. Tests run against a
copy using G6's existing repo_tests mutant mechanism, and every run asks for an
operating-system boundary itself (``require_isolation``): a run no backend would
confine is refused before it starts. Whether a run counted as isolated is read
from confine's own record of it: any backend it names (bubblewrap on Linux,
AppContainer on Windows) with the network off. The baseline runs in place and is
receipted in G6's receipt store; a candidate runs as a mutant and never is.

Nothing runs without a person's approval: a ``run_approval`` item in the
operator's queue, decided by someone other than the requester, bound to the
mission's fingerprint (``DesignMission.fingerprint``); the report names who
approved. Each mission writes its decisions to a durable hash-chained ledger
under the G6 home, every entry prefixed with the run's id.

The interface is intentionally provider-independent: an LLM, coding agent or a
hard-coded producer can implement ProposalAgent. No provider calls occur here.
In P1 the only engineer is ``NoEngineer``, which refuses before any test runs;
the Claude Code and Codex engineers come with the mission over MCP (P3).
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping, Protocol

from g6.algorithm_design.fusion import fuse
from g6.algorithm_design.ir import DesignProblem
from g6.algorithm_design.knowledge_bridge import catalog_plan, CatalogInsight
from g6.algorithm_design.architecture import review_codebase
from g6.algorithm_design.invocation import list_kernels
from g6.algorithm_design.resources import DeviceBudget, ResourceEstimate, rank_options
from g6.algorithm_design.search import search_algorithms
from g6.coding_agent_gate.evidence import Evidence, VerifierResult
from g6.coding_agent_gate.gate import verify_answer
from g6.complexity.atlas import build_atlas
from g6.complexity.impact import impact_cone
from g6.complexity.ir import FrozenIntent, Hypothesis
from g6.core.ledger import ChainLedger, LedgerError, digest
from g6.core.storage import state_path
from g6.harness.verification_controller import (
    Experiment, ExperimentState, RunObservation, next_experiment, rebase, receipt_matches, reduce_observation,
)
from g6.operator.queue import APPROVAL_MISMATCH, ActionQueue, QueueError

#: The code a report carries for each blocked reason: "G6_E_ALGO_" and the reason in capitals.
CODE_PREFIX = "G6_E_ALGO_"
NO_ENGINEER = CODE_PREFIX + "NO_ENGINEER"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative(path: str) -> bool:
    p = PurePosixPath(path)
    return (bool(path) and not p.is_absolute() and "\\" not in path and
            all(part not in ("", ".", "..") and not part.startswith(".") for part in p.parts)
            and str(p) == path)


@dataclass(frozen=True)
class ApprovalRef:
    """Where a person's approval of a mission's runs is: a ``run_approval`` item in the operator's queue."""
    venture_id: str
    action_id: str


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
    approval: ApprovalRef | None = None
    catalog_problem_id: str | None = None

    @property
    def fingerprint(self) -> str:
        """The digest a person's approval is bound to: the file map's digest, the package, its root, the target, the
        test identifiers, the intent's fingerprint, the problem, the environment, the mode and the run limits. It never
        includes the repository's path, so an approval names the work, not a folder on one machine.

        It does not bind the test files' bytes, ``conftest.py`` or the rest of the repository: ``RepoTestsRunner``
        freezes the test files and the source map when it is built (after the approval is checked) and refuses a run
        once either changes, but an edit made between the approval and that moment is not caught. An approval is not
        used up by a run: it stands for every run of this fingerprint until the person rejects it. Binding the
        repository's tree into the approval waits for the mission container."""
        return digest({"files": digest(dict(self.files)), "package": self.package, "source_root": self.source_root,
                       "target_path": self.target_path, "tests": list(self.tests), "intent": self.intent.fingerprint,
                       "problem": dataclasses.asdict(self.problem), "environment_digest": self.environment_digest,
                       "mode": self.mode, "max_attempts": self.max_attempts,
                       "timeout_per_run_s": self.timeout_per_run_s})

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
    catalog_insights: tuple[CatalogInsight, ...] = ()
    available_agent_operations: tuple[str, ...] = ()
    architecture_obligations: tuple[str, ...] = ()
    callable_kernel_ids: tuple[str, ...] = ()


class ProposalAgent(Protocol):
    """Agent adapter may synthesize, repair and explain; never access the locked evaluator. An adapter with no
    engineer behind it says so with ``unavailable()`` (a ``G6_E_*`` code, "" when available)."""
    def propose(self, brief: AgentBrief) -> PatchProposal | None: ...


class NoEngineer:
    """The proposal agent P1 ships: no model or coding agent is behind it yet (the Claude Code and Codex engineers
    come with the mission over MCP, in P3), so it says it is unavailable and ``run_agent`` stops before any test runs."""

    def unavailable(self) -> str:
        return NO_ENGINEER

    def propose(self, brief: AgentBrief) -> PatchProposal | None:
        return None


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
    receipt: str = ""                      # the run record's receipt id; "" for a mutant, which is never receipted
    artifact_digest: str = ""              # the run record's source_before: the snapshot the run began on
    network: bool = False                  # the run record's isolation.network


class CandidateRunner(Protocol):
    """Custodian-supplied independent executable; the proposer never receives it."""
    def baseline(self, *, tests: tuple[str, ...], timeout_s: int) -> TestObservation: ...

    def run(self, source: str, *, expected_path: str, tests: tuple[str, ...],
            timeout_s: int) -> TestObservation: ...


class RepoTestsRunner:
    """Production adapter to G6's existing isolated test path (``g6.workforce.repo_tests``).

    Every run asks for an operating-system boundary (``require_isolation=True``), so one no backend would confine is
    refused before it starts, whatever the person allowed; a run counts as isolated when confine's record of it names
    a backend (bubblewrap or AppContainer) and the network was off. The baseline runs in place and is receipted; each
    candidate runs as a mutant on a copy. A person's approval, bound to the mission's fingerprint, is checked when the
    runner is built and before every run (``PermissionError`` otherwise).
    """

    def __init__(self, mission: DesignMission, *, queue: ActionQueue) -> None:
        from g6.workforce import repo_tests
        self.mission = mission
        self.queue = queue
        self.approved_by = self._approved()
        self.repo = repo_tests.checked_repo(mission.repo)
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

    def _approved(self) -> str:
        """The person who approved exactly this mission, or ``PermissionError`` saying why nothing may run."""
        approval = self.mission.approval
        if approval is None:
            raise PermissionError("[G6_E_OPERATOR_APPROVAL_REQUIRED] no person has approved this mission's runs")
        try:
            return self.queue.run_approver(approval.venture_id, approval.action_id, binding=self.mission.fingerprint)
        except (QueueError, LookupError) as exc:
            raise PermissionError(str(exc)) from None

    def _frozen(self, tests: tuple[str, ...]) -> None:
        self._approved()
        if tests != self.mission.tests:
            raise PermissionError("candidate attempted to change test or patch scope")
        if self._checker_digest() != self._checker or self._source_digests() != self._sources:
            raise ValueError("tests or snapshot changed after mission freeze")

    def baseline(self, *, tests: tuple[str, ...], timeout_s: int) -> TestObservation:
        """The frozen tests on the repository as it is, in place, confined and receipted."""
        self._frozen(tests)
        from g6.workforce import repo_tests
        raw = repo_tests.run(self.repo, list(tests), timeout_s=timeout_s, network=False, receipt=True,
                             require_isolation=True)
        return self._observed(raw, self.mission.files[self._source_key])

    def run(self, source: str, *, expected_path: str, tests: tuple[str, ...],
            timeout_s: int) -> TestObservation:
        """The frozen tests with ``source`` as the target file, as a mutant on a copy: confined, never receipted."""
        if expected_path != self.target:
            raise PermissionError("candidate attempted to change test or patch scope")
        self._frozen(tests)
        from g6.workforce import repo_tests
        raw = repo_tests.run(self.repo, list(tests), timeout_s=timeout_s, network=False,
                             mutant=(expected_path, source), require_isolation=True)
        return self._observed(raw, source)

    def _observed(self, raw: Mapping[str, Any], source: str) -> TestObservation:
        if self._checker_digest() != self._checker or self._source_digests() != self._sources:
            raise ValueError("protected repository content drifted during execution")
        isolation = raw.get("isolation") if isinstance(raw.get("isolation"), Mapping) else {}
        backend = str(isolation.get("backend") or "")
        isolated = bool(backend) and not isolation.get("network")   # any backend confine names, network off
        environment_id = digest({"python": raw.get("python"), "backend": backend,
                                 "interpreter_source": raw.get("interpreter_source")})
        return TestObservation(
            # completed as the run record says it: a verdict that ran, every expected test finished (not degraded)
            verdict=str(raw.get("verdict", "unknown")),
            completed=raw.get("verdict") in ("passed", "failed") and raw.get("degraded") is False,
            isolated=isolated, backend=backend, environment_id=environment_id,
            checker_digest=self._checker, candidate_digest=_sha(source),
            failed_tests=tuple(str(x) for x in raw.get("failed_ids", ())[:30]),
            log_excerpt=str(raw.get("output", ""))[-1600:],
            host_observed=isolated, receipt=str(raw.get("receipt") or ""),
            artifact_digest=str(raw.get("source_before") or ""), network=bool(isolation.get("network")),
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
    catalog_insights: tuple[CatalogInsight, ...] = ()
    architecture_obligations: tuple[str, ...] = ()
    code: str = ""                         # every blocked-escalated report: "G6_E_ALGO_" + its reason in capitals
    run_id: str = ""
    approved_by: str = ""                  # the person whose queue decision approved this mission's runs
    receipts: tuple[str, ...] = ()         # the baseline's test receipt, when it was given one


class _LedgerConflict(Exception):
    """The mission ledger refused an entry: another writer appended to the file, or it does not check out."""


def _ledger(given: ChainLedger | None) -> ChainLedger:
    if given is not None:
        return given
    path = state_path("algorithm_design", "missions.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    return ChainLedger(path)


def run_agent(mission: DesignMission, producer: ProposalAgent, runner: CandidateRunner, *,
              queue: ActionQueue | None = None, ledger: ChainLedger | None = None) -> AgentReport:
    """Autonomous bounded CEGIS: inspect -> model -> cost -> propose -> check -> revise.

    In production the host supplies RepoTestsRunner. Tests/proofs are frozen by the
    host, never synthesized by the model as authority. Nothing writes a patch,
    merges, deploys or creates a new standard.

    Before any test runs: the intent must hold a normative obligation, an algorithm choice must fit the device, the
    engineer must be available (``unavailable()``), and ``queue`` must hold a person's approval of exactly this
    mission (``mission.approval``, bound to ``mission.fingerprint``). Every decision is appended to ``ledger`` (by
    default ``missions.jsonl`` under the G6 home), each entry's id prefixed with the run's id; a ledger that refuses
    an entry ends the run as ``ledger_conflict``.
    """
    run_id = uuid.uuid4().hex[:16]
    try:
        book = _ledger(ledger)
    except LedgerError:
        return AgentReport("blocked-escalated", "ledger_conflict", "", None, (), (), (), (), 0, (), "",
                           code=CODE_PREFIX + "LEDGER_CONFLICT", run_id=run_id)
    said = {"approved_by": "", "receipts": ()}

    def log(kind: str, ident: str, body: dict) -> None:
        try:
            book.append(kind, f"{run_id}:{ident}", body)
        except LedgerError as exc:
            raise _LedgerConflict(str(exc)) from None

    try:
        return _run(mission, producer, runner, queue, book, run_id, log, said)
    except _LedgerConflict:
        return AgentReport("blocked-escalated", "ledger_conflict", "", None, (), (), (), (), 0, (), book.head(),
                           code=CODE_PREFIX + "LEDGER_CONFLICT", run_id=run_id, approved_by=said["approved_by"],
                           receipts=said["receipts"])


def _run(mission: DesignMission, producer: ProposalAgent, runner: CandidateRunner, queue: ActionQueue | None,
         ledger: ChainLedger, run_id: str, log: Any, said: dict) -> AgentReport:
    cir = build_atlas(mission.files, package=mission.package, intent=mission.intent,
                      environment_digest=mission.environment_digest)
    change = mission.changed_symbols or (mission.package,)
    impact = impact_cone(cir, change)
    normative = tuple(sorted(o.id for o in mission.intent.obligations if o.normative))
    architecture = review_codebase(cir, impact=impact.impacted)
    knowledge = catalog_plan(mission.problem, problem_id=mission.catalog_problem_id)
    callable_ids = tuple(x.algorithm_id for x in list_kernels())
    from g6.algorithm_design.catalog import primitives
    catalog = (*primitives(), *knowledge.typed_primitives)
    search = search_algorithms(mission.problem, catalog=catalog, max_candidates=16)
    viable = []
    plans = []
    gaps = set(architecture.obligations)
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
    log("mission", "initial", {"tree": cir.snapshot.tree_digest, "intent": cir.intent.fingerprint,
                               "mode": mission.mode, "resources": mission.device.fingerprint,
                               "tests_sha256": digest(mission.tests), "mission": mission.fingerprint})

    def result(state: str, reason: str, patch: PatchProposal | None = None,
               obs: tuple[TestObservation, ...] = (), gate: str = "not_run") -> AgentReport:
        return AgentReport(state, reason, patch.algorithm_id if patch else "", patch, tuple(plans),
                           impact.impacted, normative, tuple(sorted(set(cir.unknowns) | set(impact.uncertainty))),
                           len(obs), obs, ledger.head(), gate, knowledge.insights, architecture.obligations,
                           CODE_PREFIX + reason.upper() if state == "blocked-escalated" else "", run_id,
                           said["approved_by"], said["receipts"])
    if not normative:
        return result("blocked-escalated", "no_authoritative_obligations")
    if mission.mode != "swe" and not choices:
        return result("blocked-escalated", "no_cost_feasible_algorithm_design")
    unavailable = getattr(producer, "unavailable", None)
    if callable(unavailable) and unavailable():
        log("refusal", "engineer", {"reason": "no_engineer", "code": str(unavailable())[:100]})
        return result("blocked-escalated", "no_engineer")
    if mission.approval is None or queue is None:
        return result("blocked-escalated", "execution_not_approved")
    try:
        approver = queue.run_approver(mission.approval.venture_id, mission.approval.action_id,
                                      binding=mission.fingerprint)
    except (QueueError, LookupError) as exc:
        mismatch = str(exc).startswith(f"[{APPROVAL_MISMATCH}]")
        log("refusal", "approval", {"action_id": mission.approval.action_id, "reason": str(exc)[:300]})
        return result("blocked-escalated", "approval_mismatch" if mismatch else "execution_not_approved")
    said["approved_by"] = approver
    log("approval", "approval", {"action_id": mission.approval.action_id, "reviewer": approver,
                                 "binding": mission.fingerprint})
    key = mission.target_path.removeprefix(mission.source_root + "/") if mission.source_root else mission.target_path
    if key not in mission.files:
        return result("blocked-escalated", "target_not_in_snapshot")
    accepted = (set(choices) if mission.mode != "swe" else {"swe_refactor"})
    # A baseline that fails cannot be treated as evidence of a regression-free repair.
    try:
        baseline = runner.baseline(tests=mission.tests, timeout_s=mission.timeout_per_run_s)
    except (Exception) as exc:  # environment and containment failures are blockers, not evidence
        log("refusal", "baseline", {"error_type": type(exc).__name__})
        return result("blocked-escalated", "baseline_could_not_run")
    log("baseline", "baseline", {"verdict": baseline.verdict, "isolated": baseline.isolated,
                                 "source": baseline.candidate_digest, "receipt": baseline.receipt})
    if not (baseline.isolated and baseline.completed and baseline.verdict in ("passed", "failed")
            and baseline.candidate_digest == _sha(mission.files[key])):
        return result("blocked-escalated", "baseline_not_independently_established", obs=(baseline,))
    if baseline.receipt and not receipt_matches(baseline.receipt, artifact_digest=baseline.artifact_digest):
        return result("blocked-escalated", "baseline_receipt_not_matched", obs=(baseline,))
    said["receipts"] = (baseline.receipt,) if baseline.receipt else ()
    checker = baseline.checker_digest
    environment = baseline.environment_id
    if not checker or not environment:
        return result("blocked-escalated", "unbound_baseline", obs=(baseline,))
    hypotheses = (Hypothesis("regression", "A regression in changed behavior", "high"),
                  Hypothesis("hidden_dependency", "An unmodelled shared effect", "medium"),
                  Hypothesis("wrong_environment", "Wrong runtime loaded", "medium"))
    state = ExperimentState(cir.snapshot.tree_digest, environment, checker,
                              hypotheses, mission.max_attempts)
    observed = [baseline]
    seen = set()
    last_failed: tuple[str, ...] = baseline.failed_tests if baseline.verdict == "failed" else ()
    failure_excerpt = baseline.log_excerpt if baseline.verdict == "failed" else ""
    for i in range(mission.max_attempts):
        brief = AgentBrief(mission.problem.target, choices if mission.mode != "swe" else ("swe_refactor",),
                           tuple(f.root_cause for f in cir.findings), impact.impacted, normative,
                           tuple(sorted(gaps)), tuple(plans), tuple(sorted(seen)),
                           last_failed, failure_excerpt, mission.max_attempts - i,
                           knowledge.insights, knowledge.agent_operations, architecture.obligations,
                           callable_ids)
        try:
            patch = producer.propose(brief)
        except Exception as exc:
            log("refusal", f"agent-{i}", {"error_type": type(exc).__name__})
            return result("blocked-escalated", "proposer_error", obs=tuple(observed))
        if patch is None:
            return result("blocked-escalated", "agent_exhausted_options", obs=tuple(observed))
        reason = _patch_problem(patch, mission.target_path, accepted, seen)
        if reason:
            log("refusal", f"candidate-{i}", {"reason": reason})
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
            log("refusal", f"run-{i}", {"error_type": type(exc).__name__})
            return result("blocked-escalated", "candidate_runner_refused", obs=tuple(observed))
        observed.append(observation)
        valid = (observation.isolated and observation.completed and
                 observation.checker_digest == checker and observation.environment_id == environment
                 and observation.candidate_digest == _sha(patch.source))
        outcome = ("pass" if observation.verdict == "passed" else
                   "fail" if observation.verdict == "failed" else "error")
        state = reduce_observation(state, experiment, RunObservation(
            experiment.id, patch.fingerprint, checker, environment, outcome,
            observation.backend if observation.isolated else None, observation.network, observation.receipt))
        log("observation", f"run-{i}", {
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
            log("candidate", f"success-{i}", {"patch_digest": patch.fingerprint, "gate": decision.verdict})
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
