# SPDX-License-Identifier: Apache-2.0
"""The evaluator: a paired, compute-matched comparison on one split, with a negative control (META_HARNESS_PLAN.md
4.4 "Evaluate", 5.3).

Every case is run through ``g6.harness.run`` under each arm, with its own in-memory harness memory so no arm's failures
can block another's. A run *succeeds* when the harness releases it (its label is not ``blocked-escalated``) and the
workflow's oracle accepts the output against the case's held-out answer; it is a *false accept* when the harness releases
it and the oracle rejects it. The oracle is the only code that reads an answer, and it runs here, never in an executor
or a proposer.

Arms: the base profile; the candidate; when the candidate used more calls per case than the base allows, a
compute-matched control (the base run as many times, majority-voted, as ``reasoning.self_consistency`` does for one
model call); and the negative control (an executor that answers nothing). The evaluation stops early, and says so, when
the stop is engaged or its model-call budget is spent: a partial evaluation is never used for a decision (MH-13).

With ``policy.looks > 1`` the split is a sequential confirmation (frontier design §4.2): after each look the cumulative
pairs are tested against that look's boundary (``sequential_boundaries``); a clear win stops early, and so does a
candidate not ahead after the first look (non-binding futility). Either is a finished evaluation, not a partial one.
"""
from __future__ import annotations

import functools
import hashlib
import math
import random
import statistics
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from statistics import NormalDist
from typing import Any

from g6.core import stop
from g6.core.ledger import digest
from g6.harness import Memory, Standard, run
from g6.learning import TheoryStore
from g6.metaharness.executor import ExecutorFactory, ProfileContext, context_for, null_factory
from g6.metaharness.schema import PatchEvidence, Profile, SuiteCase, Workflow
from g6.reasoning.compare import sign_test_p

Z_95 = 1.6449  # one-sided 95% normal quantile; the bounds below use the normal approximation, stated in the report


@dataclass(frozen=True)
class RunRecord:
    case_id: str
    arm: str
    released: bool
    oracle: str                      # pass | fail | unknown
    model_calls: int
    wall_clock_s: float
    completion_state: str
    output_sha256: str
    run_id: str
    error: str = ""
    cost_usd: float | None = None

    @property
    def success(self) -> bool:
        return self.released and self.oracle == "pass"

    @property
    def false_accept(self) -> bool:
        return self.released and self.oracle == "fail"

    def measure(self, name: str) -> float:
        return float(self.success) if name == "success" else float(getattr(self, name))


def standard_for(workflow: Workflow, profile: Profile, case_input: str) -> Standard:
    extra = tuple(workflow.optional_checks[k] for k in profile.extra_checks)
    return Standard(goal=workflow.task.replace("{input}", case_input), checks=(*workflow.checks, *extra),
                    family=f"metaharness/{workflow.name}")


def _oracle(workflow: Workflow, output: str, case: SuiteCase) -> str:
    try:
        verdict = workflow.oracle(output, case.expected)
    except Exception:  # noqa: BLE001 - an oracle that raises has not answered
        return "unknown"
    return "pass" if verdict is True else "fail" if verdict is False else "unknown"


def run_case(workflow: Workflow, executor: Callable[[Any], str], profile: Profile, case: SuiteCase, arm: str, *,
             calls: Callable[[], int], clock: Callable[[], float] = time.monotonic,
             cost: Callable[[], float | None] = lambda: None) -> RunRecord:
    """One case under one arm, adjudicated by the harness and, when it is released, by the oracle."""
    start = clock()
    result = run(standard_for(workflow, profile, case.input), executor, decisions=workflow.harness_decisions(),
                 memory=Memory(":memory:"), producer=f"metaharness:{arm}")
    elapsed = max(0.0, clock() - start)
    state = result.get("completion_state", "blocked-escalated")
    output = result.get("output")
    released = state != "blocked-escalated" and isinstance(output, str)
    verdict = _oracle(workflow, output, case) if released else "unknown"
    card = result.get("warning_card") or {}
    return RunRecord(case.case_id, arm, released, verdict, calls(), round(elapsed, 6), state,
                     hashlib.sha256((output or "").encode("utf-8")).hexdigest(), result.get("run_id", ""),
                     "" if released else str(card.get("code", "")), cost())


def _vote(outputs: list[str]) -> str:
    """The most common answer (after trimming); a tie goes to the earliest."""
    counts = Counter(o.strip() for o in outputs)
    best = max(counts.values())
    return next(o for o in outputs if counts[o.strip()] == best)


@dataclass
class _Meter:
    limit: int
    used: int = 0

    def charge(self, n: int) -> None:
        self.used += n

    @property
    def spent(self) -> bool:
        return self.used >= self.limit


def _arm(workflow: Workflow, factory: ExecutorFactory, profile: Profile, case: SuiteCase, arm: str, *,
         theories: TheoryStore | None, clock: Callable[[], float], repeat: int = 1, artifacts: Any = None) -> RunRecord:
    contexts: list[ProfileContext] = []

    def execute(contract: Any) -> str:
        outputs = []
        for _ in range(repeat):
            ctx = context_for(workflow, profile, theories=theories, clock=clock, artifacts=artifacts)
            contexts.append(ctx)
            outputs.append(factory(ctx)(contract))
        return outputs[0] if repeat == 1 else _vote(outputs)

    def measured_cost() -> float | None:
        observations = [o for ctx in contexts for caller in ctx.callers.values()
                        for o in getattr(caller, "observations", ())]
        if not observations or any(o.cost_usd is None for o in observations):
            return None
        return sum(o.cost_usd for o in observations)

    return run_case(workflow, execute, profile, case, arm, calls=lambda: sum(c.calls for c in contexts), clock=clock,
                    cost=measured_cost)


def _paired(base: list[float], cand: list[float]) -> dict[str, float]:
    diffs = [c - b for b, c in zip(base, cand)]
    n = len(diffs)
    mean_b, mean_c = statistics.fmean(base), statistics.fmean(cand)
    mean_d = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    return {"base_mean": round(mean_b, 6), "candidate_mean": round(mean_c, 6), "diff_mean": round(mean_d, 6),
            "diff_upper_95": round(mean_d + Z_95 * sd / math.sqrt(n), 6) if n else math.inf,
            "candidate_upper_95": round(mean_c + Z_95 * (statistics.stdev(cand) if n > 1 else 0.0) / math.sqrt(n), 6)
            if n else math.inf}


def _sign(base: list[bool], cand: list[bool]) -> dict[str, float]:
    wins = sum(c and not b for b, c in zip(base, cand))
    losses = sum(b and not c for b, c in zip(base, cand))
    return {"wins": wins, "losses": losses, "ties": len(base) - wins - losses, "discordant": wins + losses,
            "p_one_sided": round(sign_test_p(wins, losses), 6)}


@functools.lru_cache(maxsize=32)
def sequential_boundaries(alpha: float, looks: int) -> tuple[float, ...]:
    """Each look's nominal one-sided level for a confirmation with ``looks`` equally spaced looks that spends ``alpha``
    once in all (Lan-DeMets, O'Brien-Fleming-type spending α(t) = 2 − 2Φ(z₁₋α/₂ / √t)). Look 1 is exact; later looks
    are set on 100,000 simulated paths of the standardised statistic from a fixed seed, so the levels are the same in
    every run. Little is spent early and most is left for the last look."""
    if looks == 1:
        return (alpha,)
    normal = NormalDist()
    z = normal.inv_cdf(1 - alpha / 2)
    spend = [2 - 2 * normal.cdf(z / math.sqrt(k / looks)) for k in range(1, looks + 1)]
    rng, paths = random.Random(20261019), 100_000
    totals, alive, levels, spent = [0.0] * paths, [True] * paths, [], 0.0
    for k in range(1, looks + 1):
        scale = math.sqrt(k / looks)
        for i in range(paths):
            totals[i] += rng.gauss(0.0, math.sqrt(1 / looks))
        if k == 1:
            cut = normal.inv_cdf(1 - spend[0])
        else:
            scores = sorted((totals[i] / scale for i in range(paths) if alive[i]), reverse=True)
            cut = scores[min(len(scores) - 1, round((spend[k - 1] - spent) * paths))]
        for i in range(paths):
            if alive[i] and totals[i] / scale > cut:
                alive[i] = False
        spent = spend[k - 1]
        levels.append(round(1 - normal.cdf(cut), 6))
    return tuple(levels)


def power(n: int, discordant_rate: float, gain: float, threshold: float, min_discordant: int) -> float:
    """The chance that a true gain in success rate of ``gain`` is recommended on ``n`` cases at this discordance:
    discordant pairs ~ Binomial(n, d), wins among them ~ Binomial(D, 1/2 + gain / (2d)), recommend when wins > losses,
    D >= min_discordant and the one-sided sign test is at or below ``threshold``."""
    d = min(1.0, max(discordant_rate, abs(gain), 1e-9))
    q = min(1.0, 0.5 + gain / (2 * d))
    total = 0.0
    for big_d in range(min_discordant, n + 1):
        p_d = math.comb(n, big_d) * d ** big_d * (1 - d) ** (n - big_d)
        if p_d < 1e-12:
            continue
        for w in range(big_d // 2 + 1, big_d + 1):
            if sign_test_p(w, big_d - w) <= threshold:
                total += p_d * sum(math.comb(big_d, k) * q ** k * (1 - q) ** (big_d - k) for k in range(w, big_d + 1))
                break
    return round(min(1.0, total), 4)


@dataclass
class EvaluationReport:
    workflow_digest: str
    split: str
    split_digest: str
    base_digest: str
    candidate_digest: str
    n: int
    cases: list[dict[str, Any]]
    estimates: dict[str, dict[str, float]]
    success: dict[str, float]
    false_accepts: dict[str, int]
    negative_control: dict[str, Any]
    control: dict[str, Any] | None
    power: dict[str, float]
    partial: bool
    stopped: str
    model_calls_used: int
    budget: int
    reuse_index: int                 # this evaluation's index on the split (1 = first)
    provenance: str
    method: str = ("paired by case; success by one-sided exact sign test; cost bounds one-sided 95% by the normal "
                   "approximation")
    notes: list[str] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    patch_evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not self.patch_evidence:
            data.pop("patch_evidence")
        if not self.stages:
            data.pop("stages")
        return data

    def digest(self) -> str:
        return digest(self.to_dict())


def evaluate(workflow: Workflow, base: Profile, candidate: Profile, factory: ExecutorFactory, *, split: str = "held_out",
             theories: TheoryStore | None = None, clock: Callable[[], float] = time.monotonic, reuse_index: int = 1,
             budget: int | None = None, artifacts: Any = None) -> EvaluationReport:
    """Run base, candidate and the controls on every case of ``split``; see the module docstring."""
    cases = list(getattr(workflow.suite, split))
    limit = min(budget or workflow.policy.max_eval_model_calls, workflow.policy.max_eval_model_calls)
    meter = _Meter(limit)
    rows: list[tuple[RunRecord, RunRecord, RunRecord]] = []
    stopped, partial = "", False
    policy = workflow.policy
    # Sequential only for a confirmation: a change measured on the held-out split. A baseline (base against itself)
    # or a dev measurement runs every case.
    confirming = policy.looks > 1 and split == "held_out" and base.digest() != candidate.digest()
    bounds = sequential_boundaries(policy.threshold, policy.looks) if confirming else ()
    ends = [math.ceil(k * len(cases) / policy.looks) for k in range(1, policy.looks + 1)] if bounds else []
    stages = []
    staged = policy.staged_evaluation and split == "held_out" and base.digest() != candidate.digest()
    look = 0                         # looks completed
    for case in cases:
        engaged = stop.engaged()
        if engaged is not None:
            stopped, partial = f"stopped: {engaged}", True
            break
        if meter.spent:
            stopped, partial = "the evaluation's model-call budget is spent", True
            break
        b = _arm(workflow, factory, base, case, "base", theories=theories, clock=clock, artifacts=artifacts)
        c = _arm(workflow, factory, candidate, case, "candidate", theories=theories, clock=clock, artifacts=artifacts)
        z = _arm(workflow, null_factory, base, case, "negative_control", theories=None, clock=clock)
        meter.charge(b.model_calls + c.model_calls)
        rows.append((b, c, z))
        if staged and len(rows) in (5, 20):
            required = 0.8 if len(rows) == 5 else 0.6
            rate = sum(row[1].success for row in rows) / len(rows)
            stages.append({"tasks": len(rows), "pass_rate": rate, "required": required, "passed": rate >= required})
            if rate < required:
                stopped, partial = "G6_E_META_STAGE_FAILED: staged pass rate below threshold", True
                break
        if ends and len(rows) >= ends[look]:
            so_far = _sign([r[0].success for r in rows], [r[1].success for r in rows])
            look += 1
            enough = len(rows) >= policy.min_cases and so_far["discordant"] >= policy.min_discordant
            if so_far["wins"] > so_far["losses"] and so_far["p_one_sided"] <= bounds[look - 1] and enough:
                break                                    # a clear win: stop early
            if look >= 2 and so_far["wins"] <= so_far["losses"]:
                break                                    # not ahead after the first look: stop for futility
    if staged and not partial and len(rows) == len(cases):
        stages.append({"tasks": len(rows), "stage": "all", "passed": True})
    base_rows, cand_rows, null_rows = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
    n = len(rows)
    estimates = {o.name: _paired([r.measure(o.name) for r in base_rows], [r.measure(o.name) for r in cand_rows])
                 for o in workflow.objectives if o.name != "cost_usd"} if n else {}
    if n:
        for name in ("success", "wall_clock_s", "model_calls"):
            estimates.setdefault(name, _paired([r.measure(name) for r in base_rows],
                                               [r.measure(name) for r in cand_rows]))
        if all(r.cost_usd is not None for r in (*base_rows, *cand_rows)):
            estimates["cost_usd"] = _paired([r.cost_usd for r in base_rows], [r.cost_usd for r in cand_rows])
    if n:   # false accepts as a paired estimate, so the gate can test a rise against a margin (V6b-F5)
        estimates["false_accepts"] = _paired([float(r.false_accept) for r in base_rows],
                                             [float(r.false_accept) for r in cand_rows])
    success = _sign([r.success for r in base_rows], [r.success for r in cand_rows]) if n else {}
    if bounds and look and not partial:
        success |= {"look": look, "looks": policy.looks, "boundary": bounds[look - 1]}
    neg = _sign([r.success for r in null_rows], [r.success for r in base_rows]) if n else {}
    negative_control = {**neg, "base_success": sum(r.success for r in base_rows),
                        "null_success": sum(r.success for r in null_rows)}
    control = None
    calls_b = statistics.fmean(r.model_calls for r in base_rows) if n else 0.0
    calls_c = statistics.fmean(r.model_calls for r in cand_rows) if n else 0.0
    if n and calls_c > calls_b * workflow.policy.call_tolerance and not partial:
        repeat = max(2, math.ceil(calls_c / max(calls_b, 1e-9)))
        ctl_rows = []
        for case in cases[:n]:
            if stop.engaged() is not None or meter.spent:
                partial, stopped = True, stopped or "the control arm could not finish inside the budget or stop"
                break
            r = _arm(workflow, factory, base, case, "compute_matched_control", theories=theories, clock=clock,
                     artifacts=artifacts,
                     repeat=repeat)
            meter.charge(r.model_calls)
            ctl_rows.append(r)
        if not partial:
            control = {"repeat": repeat, **_sign([r.success for r in ctl_rows], [r.success for r in cand_rows]),
                       "control_success": sum(r.success for r in ctl_rows),
                       "control_calls_mean": round(statistics.fmean(r.model_calls for r in ctl_rows), 6)}
    d_rate = (success.get("discordant", 0) / n) if n else 0.0
    thr = workflow.policy.threshold
    power_est = {f"gain_{g:.2f}": power(max(n, 1), d_rate or 0.2, g, thr, workflow.policy.min_discordant)
                 for g in (0.05, 0.10, 0.20)}
    notes = []
    if d_rate == 0 and n:
        notes.append("no discordant cases: the power estimate assumes a discordance rate of 0.2")
    provenance = workflow.suite.provenance.get(split, "caller")
    if provenance != "independent":
        notes.append(f"the {split} split is {provenance}-authored, not independent")
    report = EvaluationReport(
        workflow_digest=workflow.digest(), split=split, split_digest=workflow.suite.digest(split),
        base_digest=base.digest(), candidate_digest=candidate.digest(), n=n,
        cases=[{"case_id": b.case_id, "base": asdict(b) | {"success": b.success},
                "candidate": asdict(c) | {"success": c.success}, "negative_control": {"success": z.success}}
               for b, c, z in rows],
        estimates=estimates, success=success,
        false_accepts={"base": sum(r.false_accept for r in base_rows),
                       "candidate": sum(r.false_accept for r in cand_rows)},
        negative_control=negative_control, control=control, power=power_est, partial=partial, stopped=stopped,
        model_calls_used=meter.used, budget=limit, reuse_index=reuse_index,
        provenance=workflow.suite.provenance.get(split, "caller"), notes=notes, stages=stages)
    if (base.code != candidate.code and split == "held_out" and not partial
            and workflow.patch_evaluator is not None):
        try:
            observed = workflow.patch_evaluator(base, candidate, artifacts)
            report.patch_evidence = PatchEvidence.model_validate({
                "base_digest": base.digest(), "candidate_digest": candidate.digest(), **observed,
            }).model_dump(mode="json")
        except Exception as exc:  # a failed or malformed independent check supplies no safety fact
            report.patch_evidence = {"error": f"patch_evaluator failed: {type(exc).__name__}: {exc}"}
    return report
