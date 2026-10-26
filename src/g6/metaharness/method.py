# SPDX-License-Identifier: Apache-2.0
"""The meta-harness's own method, and the loop that applies it (frontier design §4).

A ``Method`` is data: how many cycles, how much each step may spend, how racing is run, and which proposers to use.
It is versioned by digest and can be changed by level-2 self-evolution (``method_rca``). It has no field for what
judges a change: α, the confirmation's looks, the oracle, the controls and person approval live in the workflow a
person confirmed, and in code.

``tune`` runs one tuning run: for each cycle, every proposer offers candidates (dev only), and the candidates race on
the selection split against the active profile, with the champion so far racing again. After the last cycle the
champion is evaluated once on the held-out split (a sequential confirmation when the workflow's policy has looks),
the gate decides, and ``approve`` is asked for a person's name; only a name activates it. The whole run is written to
the ledger as one ``tuning_run`` row.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ledger import digest
from g6.metaharness.pool import EditSpace, Pool
from g6.metaharness.propose import RecoveryStrategy, Strategy
from g6.metaharness.reflect import EditStrategy, Pooled, ReflectiveStrategy
from g6.metaharness.schema import NO_SELECTION_SPLIT


class Method(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cycles: int = Field(3, ge=1, le=10)
    propose_budget: int = Field(200, ge=1)                # dev model calls per proposer per cycle
    candidates_per_cycle: int = Field(4, ge=1, le=16)     # per proposer
    race_block: int = Field(10, ge=1)
    race_keep: float = Field(0.5, gt=0, le=1)
    select_budget: int = Field(2000, ge=1)                # selection model calls per cycle
    use_recovery: bool = True
    use_reflection: bool = True
    use_edits: bool = False
    reflect_calls: int = Field(3, ge=1, le=12)
    merge_every: int = Field(3, ge=0, le=12)

    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))


@dataclass
class TuningRun:
    method_digest: str
    workflow_digest: str
    base_digest: str
    cycles: list[dict[str, Any]] = field(default_factory=list)
    champion: str | None = None
    confirmation: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    activated: int | None = None
    # activated | recommended_not_approved | approval_refused | refused | no_winner | blocked
    outcome: str = "no_winner"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strategies_for(method: Method, workflow: Any, *, reflector: Any = None, space: EditSpace | None = None,
                   pool: Pool | None = None) -> tuple[list[Strategy], list[str]]:
    """The proposers ``method`` asks for, sharing one pool; and a note for each it cannot build."""
    pool = pool if pool is not None else Pool()
    out: list[Strategy] = []
    notes: list[str] = []
    if method.use_recovery:   # its remedies join the pool, so reflections and merges can combine them (§4.1)
        out.append(Pooled(RecoveryStrategy(workflow, max_candidates=method.candidates_per_cycle), pool))
    if method.use_reflection:
        if reflector is None or space is None:
            notes.append("reflect: skipped, it needs a reflector model and an edit space")
        else:
            out.append(ReflectiveStrategy(reflector, space, pool, calls=method.reflect_calls,
                                          merge_every=method.merge_every))
    if method.use_edits:
        if space is None:
            notes.append("edit: skipped, it needs an edit space")
        else:
            out.append(EditStrategy(space, pool, calls=method.reflect_calls))
    return out, notes


def tune(mh: Any, method: Method, *, approve: Callable[[dict[str, Any]], str | None], reflector: Any = None,
         space: EditSpace | None = None, extra: Sequence[Strategy] = ()) -> TuningRun:
    """One tuning run of ``mh`` under ``method``; see the module docstring. ``approve`` gets the gate's recommendation
    (its card and decision) and returns the name of the person who accepts it, or ``None``."""
    refused = mh._refusal()
    if refused is None and not mh.workflow.suite.selection:
        refused = {"warning_card": {"code": NO_SELECTION_SPLIT},
                   "message": "the workflow has no selection split to race candidates on"}
    if refused is not None:   # before any budget is spent: say why, never raise
        run = TuningRun(method.digest(), mh.workflow.digest(), "",
                        notes=[f"{refused['warning_card']['code']}: {refused['message']}"])
        return _finish(mh, run, "blocked")
    _, base = mh._head()
    strategies, notes = strategies_for(method, mh.workflow, reflector=reflector, space=space)
    strategies += list(extra)
    run = TuningRun(method.digest(), mh.workflow.digest(), base.digest(), notes=notes)
    champion = None
    for cycle in range(1, method.cycles + 1):
        proposals, stats = [], {}
        for strategy in strategies:
            reply = mh.propose(strategy, budget=method.propose_budget, max_candidates=method.candidates_per_cycle)
            got = list(reply.get("proposals") or [])
            proposals += got
            stats[strategy.name] = {"proposed": len(got), "code": reply["warning_card"]["code"],
                                    "attempts": getattr(strategy, "made", None),
                                    "duplicates": getattr(strategy, "duplicates", None)}
        racers = proposals + ([champion] if champion is not None else [])
        record: dict[str, Any] = {"cycle": cycle, "strategies": stats, "raced": [], "winner": None,
                                  "new_champion": False, "selection_calls": 0, "stopped": "", "select_code": ""}
        if racers:
            selected = mh.select(racers, block=method.race_block, keep=method.race_keep, budget=method.select_budget)
            record["select_code"] = selected["warning_card"]["code"]
            fields = {p.candidate_digest: len(p.diff) for p in racers}
            record["raced"] = [{**row, "fields": fields.get(row["candidate_digest"], 0)}
                               for row in selected.get("ranking") or []]
            record["selection_calls"] = selected.get("calls_used", 0)
            record["stopped"] = selected.get("stopped", "")
            winner = selected.get("winner")
            if winner is not None:
                record["winner"] = winner.candidate_digest
                record["new_champion"] = champion is None or winner.digest() != champion.digest()
                champion = winner
        run.cycles.append(record)
    if champion is None:
        return _finish(mh, run, "no_winner")
    run.champion = champion.candidate_digest
    evaluated = mh.evaluate(champion)
    if evaluated["completion_state"] == "blocked-escalated" and "report" not in evaluated:
        run.notes.append(f"confirmation blocked: {evaluated['warning_card']['code']}: {evaluated['message']}")
        return _finish(mh, run, "blocked")
    report = evaluated["report"]
    run.confirmation = {"report_digest": report.digest(), "n": report.n, "success": report.success,
                        "partial": report.partial, "false_accepts": report.false_accepts,
                        "level": report.success.get("boundary", mh.workflow.policy.threshold),
                        "model_calls_used": report.model_calls_used}
    decided = mh.decide(report, champion)
    decision = decided.get("decision")
    if decision is None:
        run.notes.append(f"decision blocked: {decided['warning_card']['code']}: {decided['message']}")
        return _finish(mh, run, "blocked")
    run.decision = {"verdict": decision.verdict, "code": decision.code, "reasons": list(decision.reasons)}
    if decision.verdict != "recommend":
        run.recovery = decided.get("recovery")
        return _finish(mh, run, "refused")
    name = approve(decided)
    if not name:
        return _finish(mh, run, "recommended_not_approved")
    activated = mh.activate(decision, name)
    if activated["completion_state"] == "blocked-escalated":
        run.notes.append(f"activation refused: {activated['warning_card']['code']}: {activated['message']}")
        return _finish(mh, run, "approval_refused")
    run.activated = activated["head_version"]
    return _finish(mh, run, "activated")


def _finish(mh: Any, run: TuningRun, outcome: str) -> TuningRun:
    run.outcome = outcome
    body = run.to_dict()
    if mh.ledger is not None:   # an invalid workflow has no ledger: the run is returned, not recorded
        mh.ledger.append("tuning_run", f"{len(mh.ledger.rows('tuning_run')) + 1}:{digest(body)}", body)
    return run
