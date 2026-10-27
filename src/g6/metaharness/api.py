# SPDX-License-Identifier: Apache-2.0
"""``adapt`` and the ``MetaHarness`` handle: confirm, baseline, evaluate, decide, activate, run, roll back, report
(META_HARNESS_PLAN.md 4.2, 4.4).

Every call returns the library envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``,
``run_id``) with its own fields; an expected failure is returned, never raised. The stop is checked first, except by
``rollback`` (a person may move back to an approved version while G6 is stopped, owner ruling Q15) and the read-only
``inspect`` and ``report``. Nothing here returns ``verified``.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
import threading
from contextlib import contextmanager
from functools import wraps
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import g6
from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.ledger import LedgerError, digest
from g6.core.llm import LLMError
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.storage import g6_home, state_path
from g6.harness import Memory, run
from g6.integrity import build_manifest, manifest_sha256
from g6.learning import TheoryStore
from g6.metaharness.evaluate import EvaluationReport, evaluate, standard_for
from g6.metaharness.artifacts import ArtifactError, ArtifactStore
from g6.metaharness.executor import ExecutorFactory, code_factory, context_for, model_call_factory
from g6.metaharness import monitor as drift
from g6.metaharness import recovery
from g6.metaharness.gate import GateDecision, decide
from g6.metaharness.propose import DevBudget, DevFailure, ProposerView, Strategy, build_view, dev_runner
from g6.metaharness.select import race
from g6.metaharness.schema import (
    APPROVAL_REQUIRED,
    APPROVER_IS_PROPOSER,
    AUDIT_FLOOR,
    BUDGET_EXCEEDED,
    DRIFT_DEMOTED,
    HOLDOUT_CHANGED,
    HOLDOUT_REUSED,
    NO_NEW_CANDIDATE,
    NO_SELECTION_SPLIT,
    NOT_CONFIRMED,
    NOT_HELD_OUT,
    ONLINE_OFF,
    SCHEMA,
    SELECTED,
    STANDARD_CHANGED,
    Profile,
    Proposal,
    Workflow,
    parse_profile,
    profile_diff,
)
from g6.metaharness.store import ProfileStore, RunLedger

#: The code that judges a change: pinned at confirmation, compared before every decision (MH-4).
JUDGING_PATTERNS = ("metaharness/evaluate.py", "metaharness/gate.py", "metaharness/store.py", "metaharness/schema.py",
                    "metaharness/declarative.py", "metaharness/api.py", "workspace/_coordination.py",
                    "workspace/_envelope.py", "core/storage.py",
                    "metaharness/executor.py", "metaharness/measurement.py", "metaharness/promotion.py",
                    "metaharness/monitor.py", "metaharness/_code_safety.py", "metaharness/trace.py", "harness/*.py", "evidence/*.py", "reasoning/compare.py",
                    "reasoning/budget.py", "core/model_calls.py", "core/ledger.py", "core/stop.py", "invariants/*.py")


def judging_sha256() -> str:
    return manifest_sha256(build_manifest(Path(g6.__file__).resolve().parent, patterns=JUDGING_PATTERNS))


def _envelope(state: str, code: str, message: str, *, next_action: str = "", evidence: list | None = None,
              **extra: Any) -> dict[str, Any]:
    # Qualified replies retain the invariant-required warning card, but explain
    # the assurance limit rather than implying ordinary bookkeeping failed.
    ordinary = {"G6_E_META_CREATED", "G6_E_META_CONFIRMED", "G6_E_META_INSPECT", "G6_E_META_PROPOSED"}
    if state == "qualified-draft" and code in ordinary:
        next_action = next_action or "Review the recorded consent, versions, evaluations and decisions; inspection does not execute the application."
        card = warning_card(code, message + ". This records workflow state, not independently verified application behavior.", next_action)
    else:
        card = warning_card(code, message, next_action or None)
    env = envelope_fields(reliability_label=state, warning_card=card,
                          request_id=uuid.uuid4().hex[:12], run_id=uuid.uuid4().hex, evidence=evidence or [])
    if next_action:
        env["recommended_next_steps"] = [next_action]
    return {**env, "code": code, "message": message, "next_action": next_action, **extra}


def _blocked(code: str, message: str, next_action: str = "", **extra: Any) -> dict[str, Any]:
    return _envelope("blocked-escalated", code, message, next_action=next_action, **extra)


def _serialized(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        from g6.workspace._envelope import WorkspaceRefused
        try:
            with self._operation():
                return method(self, *args, **kwargs)
        except WorkspaceRefused:
            return _blocked("G6_E_META_BUSY", "another operation owns this workflow; retry after it finishes")
    return invoke


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _proposal_body(proposal: Proposal) -> dict[str, Any]:
    return {"base_digest": proposal.base_digest, "candidate": proposal.candidate.model_dump(mode="json"),
            "proposer": proposal.proposer, "strategy": proposal.strategy, "claim": dict(proposal.claim),
            "source_run_ids": list(proposal.source_run_ids), "diff": dict(proposal.diff),
            **({"parent_digests": list(proposal.parent_digests)} if proposal.parent_digests else {})}


def _rebuilt(kind: str, body: dict[str, Any]) -> Proposal | EvaluationReport | GateDecision:
    """A proposal, report or decision from its run-ledger row (``MetaHarness._recorded`` checks its digest)."""
    if kind == "proposal":
        return Proposal(body["base_digest"], Profile.model_validate(body["candidate"]), body["proposer"],
                        body["strategy"], dict(body["claim"]), tuple(body["source_run_ids"]), dict(body["diff"]),
                        tuple(body.get("parent_digests", ())))
    if kind == "report":
        return EvaluationReport(**body)
    fields = {k: v for k, v in body.items() if k != "completion_state"}
    return GateDecision(**{**fields, "reasons": tuple(fields["reasons"]), "checks": tuple(fields.get("checks", ()))})


class MetaHarness:
    def __init__(self, workflow: Workflow, factory: ExecutorFactory | None, workspace: Path, *,
                 theories: TheoryStore | None, clock: Callable[[], float],
                 problems: list[tuple[str, str]]) -> None:
        self.workflow, self.workspace = workflow, workspace
        self._operation_lock = threading.RLock()
        self._operation_depth = 0
        self.theories, self._clock = theories, clock
        self.artifacts = ArtifactStore(workspace / "artifacts")
        self.factory = factory or (code_factory(workflow, self.artifacts) if workflow.code_root is not None
                                   else model_call_factory())
        self._problems = problems
        self._proposals: dict[str, Proposal] = {}
        self._reports: dict[str, EvaluationReport] = {}
        self._decisions: dict[str, GateDecision] = {}
        self.store: ProfileStore | None = None
        self.ledger: RunLedger | None = None
        self._online: Strategy | None = None
        if not problems:
            workspace.mkdir(parents=True, exist_ok=True)
            self.store = ProfileStore(workspace / "store.jsonl")
            self.ledger = RunLedger(workspace / "ledger.jsonl")
            problems += self._live_leaks()
            self._problems = problems
        code, why = problems[0] if problems else ("G6_E_META_READY", "")
        self.envelope = (_blocked(code, "; ".join(p for _, p in problems), "Fix the workflow; then adapt again.",
                                  problems=[{"code": c, "problem": p} for c, p in problems])
                         if problems else
                         _envelope("qualified-draft", "G6_E_META_READY", "the workflow is valid; a person confirms it next",
                                   next_action="mh.confirm(accepted_by='<your name>')",
                                   workflow_digest=workflow.digest(), workspace=str(workspace)))

    @contextmanager
    def _operation(self):
        """One complete operation per workspace across synchronous tools, jobs and processes.

        Nested calls share the lock. Reload after acquiring it so waiting handles cannot
        spend a hold-out use or activate from the ledger snapshot seen before waiting.
        """
        from g6.workspace._coordination import Coordination
        with self._operation_lock:
            if self._operation_depth or self._problems:
                yield
                return
            with Coordination(self.workspace / ".operation").mutex():
                if self._moved():
                    self.store._chain.reload()
                    self.ledger._chain.reload()
                self._operation_depth += 1
                try:
                    yield
                finally:
                    self._operation_depth -= 1

    def _live_leaks(self) -> list[tuple[str, str]]:
        """A live episode can never be held-out evidence (owner ruling Q3): a held-out case whose id is a live run's, or
        whose input a live run had, is refused."""
        live = [r for r in self.ledger.rows("run") if r.get("purpose") == "live"]
        ids = {r["_id"] for r in live}
        inputs = {r.get("input_sha256") for r in live}
        hits = sorted(c.case_id for c in self.workflow.suite.held_out
                      if c.case_id in ids or c.ref()["input_sha256"] in inputs)
        return [(NOT_HELD_OUT, f"held-out cases that are live episodes: {hits[:5]}")] if hits else []

    def _setting(self, key: str, default: Any) -> Any:
        rows = [r for r in self.ledger.rows("setting") if key in r]
        return rows[-1][key] if rows else default

    # --- guards -------------------------------------------------------------------------------------------------
    def _refusal(self, *, allow_stopped: bool = False, need_confirmed: bool = True) -> dict[str, Any] | None:
        if self._problems:
            return self.envelope
        if not allow_stopped:
            engaged = stop.engaged()
            if engaged is not None:
                return _blocked(stop.STOPPED, f"G6 is stopped: {engaged}", "Whoever engaged the stop releases it.")
        if need_confirmed:
            confirmed = self.store.confirmed()
            if confirmed is None:
                return _blocked(NOT_CONFIRMED, "a person has not confirmed the workflow",
                                "mh.confirm(accepted_by='<your name>')")
            if confirmed["workflow_digest"] != self.workflow.digest():
                return _blocked(STANDARD_CHANGED, "the workflow changed since it was confirmed; every earlier "
                                "comparison is void", "Confirm the new workflow (a person), then baseline again.")
        return None

    def _head(self) -> tuple[int, Profile]:
        head = self.store.head()
        assert head is not None  # confirm writes version 0
        return head

    def _vault_changed(self) -> dict[str, Any] | None:
        """The sealed held-out file, when there is one, must be exactly what was sealed (ECHA-10; audit 037)."""
        vault = self.workflow.held_out_vault
        if vault is None:
            return None
        changed = vault.verify_unchanged(ledger=self.ledger)
        if not changed:
            return None
        return _blocked(HOLDOUT_CHANGED, f"the sealed held-out file changed since it was sealed: {list(changed)[:3]}",
                        "Every evaluation against it is void. A person freezes a fresh split and confirms it.")

    def _reuse_index(self, split_digest: str) -> int:
        return 1 + sum(1 for r in self.ledger.rows("evaluation") if r.get("split_digest") == split_digest)

    # --- what another process reads: proposals, reports and decisions, by digest ---------------------------------
    def _keep(self, kind: str, ident: str, body: dict[str, Any]) -> None:
        if not self.ledger.has(kind, ident):
            self.ledger.append(kind, ident, body)

    def _recorded(self, kind: str, ident: str) -> tuple[Any, str]:
        """The proposal, report or decision with digest ``ident``: one this handle made, or one any process recorded
        in this workspace, rebuilt and checked against its digest. A rebuilt proposal is validated as a new one is; a
        rebuilt decision must match the report and proposal it names and be what the gate decides on them now (review
        B2-I1). ``(None, why)`` when there is none, or it does not check out."""
        held = {"proposal": self._proposals, "report": self._reports, "decision": self._decisions}[kind]
        if ident in held:
            return held[ident], ""
        row = {"proposal": "candidate", "report": "report", "decision": "decision"}[kind]
        if self.ledger is None or not self.ledger.has(row, ident):
            return None, f"no {kind} with the digest {ident!r} is recorded in this workspace"
        try:
            found = _rebuilt(kind, self.ledger.get(row, ident))
        except (KeyError, TypeError, ValueError) as exc:
            return None, f"the recorded {kind} {ident!r} cannot be read ({type(exc).__name__})"
        if found.digest() != ident:
            return None, f"the recorded {kind} does not match its digest {ident!r}"
        if kind == "proposal":  # a profile that never passed validation is never evaluated
            problems = found.candidate.problems_for(self.workflow) + self._artifact_problems(found.candidate)
            if problems:
                return None, f"the recorded proposal {ident!r} is not a valid profile here: " + "; ".join(
                    p for _, p in problems)
        if kind == "decision":
            report, why = self._recorded("report", found.report_digest)
            proposal, why_not = self._recorded("proposal", found.proposal_digest)
            if report is None or proposal is None:
                return None, f"the recorded decision names what this workspace does not hold: {why or why_not}"
            if not (found.candidate_digest == report.candidate_digest == proposal.candidate_digest
                    and found.base_digest == report.base_digest == proposal.base_digest
                    and found.workflow_digest == report.workflow_digest and found.proposer == proposal.proposer):
                return None, "the recorded decision does not match the report and proposal it names"
            # The verdict is decided again on what it names, as g6.learning's activate runs promote again: a row
            # that says "recommend" over a report that shows a loss is refused.
            try:
                base = self.store.profile(report.base_digest)
            except KeyError:
                return None, "the recorded decision's base profile is not in this workspace's store"
            again = decide(report, proposal, self.workflow, confirmed=self.store.confirmed(),
                           judging_sha256=judging_sha256(), base=base)
            if again.digest() != ident:
                return None, (f"the recorded decision does not follow from the report and proposal it names: the gate "
                              f"decides {again.verdict} ({again.code}) on them now")
        held[ident] = found
        return found, ""

    def _moved(self) -> bool:
        """Another process wrote this workspace's store or ledger since this handle read them: open it again."""
        if self.store is None or self.ledger is None:
            return False
        from g6.core.ledger import _file_head

        return any((_file_head(self.workspace / name) or "") != head for name, head in (
            ("store.jsonl", self.store.head_hash()), ("ledger.jsonl", self.ledger.head())))

    # --- the loop -----------------------------------------------------------------------------------------------
    @_serialized
    def confirm(self, accepted_by: str, *, initial: Profile | Mapping[str, Any] | None = None) -> dict[str, Any]:
        """A person confirms the standard, the capabilities and the held-out provenance; the workflow is frozen."""
        refused = self._refusal(need_confirmed=False)
        if refused:
            return refused
        if not accepted_by.strip() or is_automated(accepted_by):
            return _blocked(APPROVAL_REQUIRED, "a person must confirm the workflow (no empty or auto: name)")
        digest_now, judging = self.workflow.digest(), judging_sha256()
        vault = self.workflow.held_out_vault
        if vault is not None:
            changed = self._vault_changed()
            if changed:
                return changed
            if not self.ledger.has("vault", vault.vault_id):
                self.ledger.add_vault(vault.vault_id, vault.digest, files=len(vault.manifest), roots=len(vault.roots))
        existing = self.store.confirmed()
        # a new workflow, or G6's judging code changed since (an upgrade): the person's confirmation pins it again
        if existing is None or existing["workflow_digest"] != digest_now or existing.get("judging_sha256") != judging:
            self.store.confirm(workflow_digest=digest_now, judging_sha256=judging,
                               held_out_digest=self.workflow.suite.digest("held_out"), accepted_by=accepted_by,
                               at=_now())
        if self.store.head() is None:
            first = initial if initial is not None else Profile(
                routing={s: next(iter(self.workflow.models)) for s in self.workflow.steps},
                pipeline=tuple(n for n, s in self.workflow.pipeline_steps.items() if s.mandatory))
            profile, why = parse_profile(first)
            problems = profile.problems_for(self.workflow) if profile else [(SCHEMA, why)]
            if problems:
                return _blocked(SCHEMA, "; ".join(p for _, p in problems), "Fix the initial profile.")
            self.store.append_version("initial", profile, at=_now(), accepted_by=accepted_by,
                                      ledger_head=self.ledger.head())
        version, profile = self._head()
        return _envelope("qualified-draft", "G6_E_META_CONFIRMED", "workflow confirmed and frozen",
                         next_action="Inspect the confirmed workflow, then propose a candidate and request a bounded evaluation.", workflow_digest=digest_now, judging_sha256=judging,
                         held_out_digest=self.workflow.suite.digest("held_out"), head_version=version,
                         head_profile=profile.digest(), accept_risky=self.workflow.accept_risky,
                         identity="accepted_by is recorded as given")

    @_serialized
    def candidate(self, profile: Profile | Mapping[str, Any], *, proposer: str, strategy: str = "hand-written",
                  claim: Mapping[str, str] | None = None, source_run_ids: tuple[str, ...] = (),
                  parent_digests: tuple[str, ...] = ()) -> dict[str, Any]:
        """A proposal from a hand-written (or agent-written) profile; Phase 2 adds proposer strategies."""
        refused = self._refusal()
        if refused:
            return refused
        parsed, why = parse_profile(profile)
        if parsed is None:
            return _blocked(SCHEMA, f"not a profile: {why}", "Use only the profile's acting fields.")
        problems = parsed.problems_for(self.workflow) + self._artifact_problems(parsed)
        if problems:
            return _blocked(SCHEMA, "; ".join(p for _, p in problems), "Fix the profile.")
        if not proposer.strip():
            return _blocked(SCHEMA, "a proposal needs a proposer identity")
        _, base = self._head()
        proposal = Proposal(base.digest(), parsed, proposer, strategy, dict(claim or {}), tuple(source_run_ids),
                            profile_diff(base, parsed), parent_digests or (base.digest(),))
        self._proposals[proposal.digest()] = proposal
        self._keep("candidate", proposal.digest(), _proposal_body(proposal))
        return _envelope("qualified-draft", "G6_E_META_PROPOSED", "a candidate only: evaluate it next",
                         next_action="mh.evaluate(proposal)", proposal=proposal, diff=proposal.diff,
                         candidate_digest=proposal.candidate_digest)

    @_serialized
    def propose(self, strategy: Strategy, *, budget: int = 200, max_candidates: int = 3) -> dict[str, Any]:
        """Candidates from ``strategy``, which sees the dev split and the active version's dev failures only. Each is
        validated like a hand-written one; one already proposed is not proposed again."""
        refused = self._refusal()
        if refused:
            return refused
        _, base = self._head()
        meter = DevBudget(budget)
        view = build_view(self.workflow, self.factory, base, theories=self.theories, clock=self._clock, budget=meter,
                          artifacts=self.artifacts)
        return self._offer(view, strategy, f"auto:{strategy.name}", meter, max_candidates)

    @_serialized
    def diagnose(self, *, budget: int = 200) -> dict[str, Any]:
        """Recovery and root cause (DECISIONS #27): why the active version fails on the dev split, and what can be done
        next: G6 components the meta-harness can try as candidates, and what only a person can unblock. It reads dev
        cases and the decisions so far, never a held-out case, and changes nothing."""
        refused = self._refusal()
        if refused:
            return refused
        version, base = self._head()
        meter = DevBudget(budget)
        view = build_view(self.workflow, self.factory, base, theories=self.theories, clock=self._clock, budget=meter,
                          artifacts=self.artifacts)
        plan = recovery.diagnose(view, self.workflow, self.ledger.rows("decision"))
        return _envelope("qualified-draft", "G6_E_META_DIAGNOSED", plan.summary, next_action=plan.next_action,
                         recovery=plan.to_dict(), head_version=version, dev_success=view.dev_success,
                         dev_cases=len(view.dev), dev_calls_used=meter.used, notes=list(view.notes))

    def _offer(self, view: ProposerView, strategy: Strategy, proposer: str, meter: DevBudget,
               max_candidates: int) -> dict[str, Any]:
        _, base = self._head()
        runner = dev_runner(self.workflow, self.factory, theories=self.theories, clock=self._clock, budget=meter,
                            artifacts=self.artifacts)
        try:
            raw = list(strategy.propose(view, runner))
        except LLMError as exc:
            why = str(exc)
            code = re.search(r"\bG6_E_[A-Z0-9_]+\b", why)
            return _blocked(code.group() if code else "G6_E_META_MODEL_FAILED", why, why)
        except Exception as exc:  # noqa: BLE001 - a failing strategy proposes nothing; it never raises through
            why = f"{type(exc).__name__}: {exc}"
            plan = recovery.for_proposal(why, ())
            return _blocked(NO_NEW_CANDIDATE, f"the strategy failed: {why}", plan.next_action,
                            recovery=plan.to_dict())
        seen = {r.get("candidate_digest") for r in self.ledger.rows("proposal")} | {base.digest()}
        proposals, refused_rows = [], []
        for item in raw:
            parsed, why = parse_profile(item)
            problems = parsed.problems_for(self.workflow) if parsed else [(SCHEMA, why)]
            if problems:
                refused_rows.append("; ".join(p for _, p in problems))
                continue
            if parsed.digest() in seen:
                continue
            seen.add(parsed.digest())
            made = self.candidate(parsed, proposer=proposer, strategy=strategy.name,
                                  source_run_ids=tuple(f.case_id for f in view.failures),
                                  parent_digests=getattr(strategy, "lineage", {}).get(parsed.digest(), ()))
            proposal = made["proposal"]
            self.ledger.append("proposal", proposal.digest(), {
                "strategy": strategy.name, "proposer": proposer, "candidate_digest": proposal.candidate_digest,
                "diff": dict(proposal.diff), "failures_seen": len(view.failures), "dev_calls_used": meter.used})
            proposals.append(proposal)
            if len(proposals) >= max_candidates:
                break
        flagged = sorted({p for f in view.failures for p in f.flags})
        if not proposals:
            plan = recovery.for_proposal(None, refused_rows)
            return _blocked(NO_NEW_CANDIDATE, "no new candidate: the strategy proposed nothing that is valid and new",
                            plan.next_action, refused=refused_rows, guard_flags=flagged, dev_calls_used=meter.used,
                            recovery=plan.to_dict())
        return _envelope("qualified-draft", "G6_E_META_PROPOSED", f"{len(proposals)} candidate(s); evaluate each",
                         next_action="mh.evaluate(proposal)", proposals=proposals, refused=refused_rows,
                         guard_flags=flagged, dev_success=view.dev_success, dev_calls_used=meter.used,
                         notes=list(view.notes))

    @_serialized
    def select(self, proposals: Sequence[Proposal], *, block: int = 10, keep: float = 0.5,
               budget: int | None = None) -> dict[str, Any]:
        """Rank ``proposals`` on the selection split by racing (frontier design §4.2). It decides nothing: the winner
        still goes to ``evaluate`` and ``decide`` on the held-out split, once."""
        refused = self._refusal()
        if refused:
            return refused
        if not self.workflow.suite.selection:
            return _blocked(NO_SELECTION_SPLIT, "the workflow has no selection split to rank candidates on",
                            "Give the suite a selection split (cases outside dev and held-out), or evaluate one "
                            "candidate directly: mh.evaluate(proposal).")
        _, base = self._head()
        for proposal in proposals:
            if self._proposals.get(proposal.digest()) is not proposal:
                return _blocked(SCHEMA, "a proposal was not made by this meta-harness", "mh.candidate(...)")
            if proposal.base_digest != base.digest():
                return _blocked(STANDARD_CHANGED, "the active profile changed since a proposal was made",
                                "Make the candidates again against the current version.")
        result = race(self.workflow, self.factory, base, [p.candidate for p in proposals], block=block, keep=keep,
                      budget=budget or self.workflow.policy.max_eval_model_calls, theories=self.theories,
                      clock=self._clock, artifacts=self.artifacts)
        winner = next((p for p in proposals if result.winner is not None and p.candidate is result.winner), None)
        row = {"split_digest": self.workflow.suite.digest("selection"), "base_digest": base.digest(),
               "ranking": result.ranking, "winner": winner.candidate_digest if winner else None,
               "cases_used": result.cases_used, "calls_used": result.calls_used, "stopped": result.stopped}
        self.ledger.append("selection", f"{len(self.ledger.rows('selection')) + 1}:{digest(row)}", row)
        if winner is None and result.stopped:
            return _blocked(BUDGET_EXCEEDED, result.stopped, "Raise the selection budget, or race fewer candidates.",
                            ranking=result.ranking, winner=None, cases_used=result.cases_used,
                            calls_used=result.calls_used, stopped=result.stopped)
        message = ("the leader beat the active profile on the selection split; evaluate it on the held-out split"
                   if winner else "no candidate beat the active profile on the selection split")
        return _envelope("qualified-draft", SELECTED, message,
                         next_action="mh.evaluate(winner)" if winner else "Propose again (mh.propose).",
                         ranking=result.ranking, winner=winner, cases_used=result.cases_used,
                         calls_used=result.calls_used, stopped=result.stopped)

    @_serialized
    def baseline(self, *, budget: int | None = None) -> dict[str, Any]:
        """The active profile measured on dev and held-out against the negative control, with the power estimate."""
        refused = self._refusal() or self._vault_changed()
        if refused:
            return refused
        version, profile = self._head()
        out: dict[str, Any] = {}
        for split in ("dev", "held_out"):
            digest_ = self.workflow.suite.digest(split)
            index = self._reuse_index(digest_) if split == "held_out" else 1
            if split == "held_out" and index > self.workflow.policy.max_evaluations:
                return _blocked(HOLDOUT_REUSED, "the held-out split has reached its evaluation limit",
                                "Freeze a fresh held-out split (a person), then confirm the new workflow.")
            report = evaluate(self.workflow, profile, profile, self.factory, split=split, theories=self.theories,
                              artifacts=self.artifacts,
                              clock=self._clock, reuse_index=index, budget=budget)
            self._record_evaluation(report, purpose="baseline")
            if split == "held_out":
                self._reports[report.digest()] = report
                self._keep("report", report.digest(), report.to_dict())
            held = Counter(c["base"]["error"] or "no code" for c in report.cases if not c["base"]["released"])
            out[split] = {"n": report.n, "estimates": {k: {"mean": v["base_mean"]} for k, v in report.estimates.items()},
                          "negative_control": report.negative_control, "power": report.power,
                          "partial": report.partial, "notes": report.notes, "held": dict(held)}
        held_codes = Counter(out["dev"]["held"]) + Counter(out["held_out"]["held"])
        extra: dict[str, Any] = {}
        next_action = "Write a candidate profile: mh.candidate(...), or mh.propose(strategy)."
        if held_codes:   # counts only: which held-out case was held is never shown
            extra["recovery"] = {"summary": f"the harness held runs of the active version: {dict(held_codes)}",
                                 "next_action": "Run mh.diagnose(): no profile gets past a hold, and it says who can."}
            next_action = extra["recovery"]["next_action"]
        return _envelope("qualified-draft", "G6_E_META_BASELINE", "the active profile measured", head_version=version,
                         baseline=out, next_action=next_action, **extra)

    def _record_evaluation(self, report: EvaluationReport, *, purpose: str) -> None:
        self._keep("evaluation", report.digest(), {
            "purpose": purpose, "split": report.split, "split_digest": report.split_digest,
            "report_digest": report.digest(),
            "workflow_digest": report.workflow_digest, "base_digest": report.base_digest,
            "candidate_digest": report.candidate_digest, "n": report.n, "success": report.success,
            "partial": report.partial, "model_calls_used": report.model_calls_used,
            "head_version": self._head()[0], "estimates": report.estimates,
            "judge_counts": [sum(c["base"]["released"] == (c["base"]["oracle"] == "pass")
                                  for c in report.cases if c["base"]["oracle"] != "unknown"),
                             sum(c["base"]["released"] != (c["base"]["oracle"] == "pass")
                                  for c in report.cases if c["base"]["oracle"] != "unknown")],
            "run_ids": [c["candidate"]["run_id"] for c in report.cases]})

    @_serialized
    def evaluate(self, proposal: Proposal, *, budget: int | None = None) -> dict[str, Any]:
        refused = self._refusal() or self._vault_changed()
        if refused:
            return refused
        if self._proposals.get(proposal.digest()) is not proposal:
            return _blocked(SCHEMA, "this proposal was not made by this meta-harness", "mh.candidate(...)")
        _, base = self._head()
        if proposal.base_digest != base.digest():
            return _blocked(STANDARD_CHANGED, "the active profile changed since this proposal was made",
                            "Make the candidate again against the current version.")
        digest_ = self.workflow.suite.digest("held_out")
        index = self._reuse_index(digest_)
        if index > self.workflow.policy.max_evaluations:
            return _blocked(HOLDOUT_REUSED, f"the held-out split has been evaluated {index - 1} times; the limit is "
                            f"{self.workflow.policy.max_evaluations}", "Freeze a fresh held-out split (a person).")
        report = evaluate(self.workflow, base, proposal.candidate, self.factory, theories=self.theories,
                          artifacts=self.artifacts,
                          clock=self._clock, reuse_index=index, budget=budget)
        self._record_evaluation(report, purpose="evaluation")
        self._reports[report.digest()] = report
        self._keep("report", report.digest(), report.to_dict())
        state ="blocked-escalated" if report.partial else "qualified-draft"
        return _envelope(state, "G6_E_META_EVALUATED" if not report.partial else (
                         "G6_E_META_STAGE_FAILED" if report.stages and not report.stages[-1]["passed"]
                         else "G6_E_META_BUDGET_EXCEEDED"),
                         "measured on the held-out split" if not report.partial else report.stopped,
                         next_action="mh.decide(report)", report=report, success=report.success,
                         estimates=report.estimates, power=report.power)

    @_serialized
    def decide(self, report: EvaluationReport, proposal: Proposal) -> dict[str, Any]:
        refused = self._refusal()
        if refused:
            return refused
        if self._reports.get(report.digest()) is not report or self._proposals.get(proposal.digest()) is not proposal:
            return _blocked(SCHEMA, "the report or proposal was not made by this meta-harness")
        _, base = self._head()
        decision = decide(report, proposal, self.workflow, confirmed=self.store.confirmed(),
                          judging_sha256=judging_sha256(), base=base)
        self._decisions[decision.digest()] = decision
        if not self.ledger.has("decision", decision.digest()):  # the same report decided again is the same decision
            self.ledger.append("decision", decision.digest(), decision.to_dict())
        if decision.verdict != "recommend":
            plan = recovery.for_decision(decision, report, self.workflow)
            return _blocked(decision.code, "; ".join(decision.reasons), f"Nothing changes. {plan.next_action}",
                            decision=decision, card=self._card(decision, report), recovery=plan.to_dict())
        return _envelope("qualified-draft", decision.code, decision.reasons[0],
                         next_action="A person reviews the evidence, then mh.activate(decision, accepted_by=...).",
                         decision=decision, card=self._card(decision, report))

    @staticmethod
    def _card(decision: GateDecision, report: EvaluationReport) -> dict[str, Any]:
        """A decision card printed only from recorded values (the result_card rule)."""
        return {"held_out_cases": report.n, "success": report.success, "false_accepts": report.false_accepts,
                "estimates": report.estimates, "negative_control": report.negative_control, "control": report.control,
                "provenance": report.provenance, "proposer": decision.proposer, "gate_checks": list(decision.checks)}

    @_serialized
    def activate(self, decision: GateDecision | str, accepted_by: str, *, candidate_digest: str = "") -> dict[str, Any]:
        """``decision``, or its digest: a decision recorded in this workspace by any process (one made over MCP among
        them) is rebuilt from the run ledger, checked against its digest and the report and proposal it names, and
        decided again. By digest, the person first sees the decision's card: the call is refused with it until
        ``candidate_digest`` is the candidate digest the card shows (review B2-I1), so with several recommendations the
        one activated is the one the person read."""
        refused = self._refusal()
        if refused:
            return refused
        by_digest = isinstance(decision, str)
        if by_digest:
            found, why = self._recorded("decision", decision)
            if found is None:
                return _blocked(APPROVAL_REQUIRED, why, "Pass the digest of a decision recorded in this workspace.")
            decision = found
        if self._decisions.get(decision.digest()) is not decision:
            return _blocked(APPROVAL_REQUIRED, "this decision was not issued by this meta-harness",
                            "Pass its digest instead: mh.activate('<decision digest>', accepted_by='<your name>'); "
                            "it shows the decision's card and asks for the candidate digest on it.")
        if decision.verdict != "recommend":
            return _blocked(APPROVAL_REQUIRED, "only a 'recommend' decision can be activated")
        who = accepted_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person must accept the change (no empty or auto: name)")
        if who.lower() == decision.proposer.strip().lower():
            return _blocked(APPROVER_IS_PROPOSER, "the person accepting a change cannot be its proposer")
        version, base = self._head()
        if base.digest() != decision.base_digest or self.workflow.digest() != decision.workflow_digest:
            return _blocked(STANDARD_CHANGED, "the active version or the workflow changed since the decision",
                            "Evaluate again against the current version.")
        proposal, _ = self._recorded("proposal", decision.proposal_digest)
        if proposal is None or proposal.candidate_digest != decision.candidate_digest:
            return _blocked(APPROVAL_REQUIRED, "the decision does not match a candidate this meta-harness holds")
        if by_digest and candidate_digest != decision.candidate_digest:
            report, _ = self._recorded("report", decision.report_digest)
            card = {**(self._card(decision, report) if report is not None else {}),
                    "candidate_digest": decision.candidate_digest, "diff": dict(proposal.diff)}
            why = ("read this decision's card first: approving by digest needs the candidate digest it shows"
                   if not candidate_digest else
                   "the candidate digest given is not the one this decision recommends; read its card")
            return _blocked(APPROVAL_REQUIRED, why,
                            f"Read the card, then mh.activate({decision.digest()!r}, accepted_by='<your name>', "
                            "candidate_digest='<the candidate_digest on the card>').", card=card, decision=decision)
        snap = self._snapshot(version + 1)
        try:
            number = self.store.append_version("activate", proposal.candidate, at=_now(), accepted_by=who,
                                               decision_digest=decision.digest(), proposer=decision.proposer,
                                               ledger_head=self.ledger.head(), snapshot=snap)
        except LedgerError as exc:
            return _blocked(APPROVAL_REQUIRED, f"the store refused the activation: {exc}")
        return _envelope("qualified-draft", "G6_E_META_ACTIVATED", f"version {number} is active",
                         next_action="Live runs now use it; the monitor compares it with the previous version.",
                         head_version=number, head_profile=proposal.candidate_digest, snapshot=snap,
                         identity="accepted_by is recorded as given")

    def _snapshot(self, number: int) -> str:
        home = g6_home()
        try:
            inside = self.workspace.resolve().is_relative_to(home.resolve())
        except OSError:
            inside = False
        if not (inside and home.is_dir()):
            return ""
        from g6.core.snapshots import snapshot

        return snapshot(f"metaharness-{self.workflow.name}-v{number}").name

    def _live(self, profile: Profile, task_input: str, producer: str, memory: Memory) -> tuple[dict, int, float]:
        ctx = context_for(self.workflow, profile, theories=self.theories, clock=self._clock, artifacts=self.artifacts)
        start = self._clock()
        result = run(standard_for(self.workflow, profile, task_input), self.factory(ctx),
                     decisions=self.workflow.harness_decisions(), memory=memory, producer=producer)
        return result, ctx.calls, round(max(0.0, self._clock() - start), 6)

    @_serialized
    def run(self, task_input: str, *, shadow: Proposal | None = None) -> dict[str, Any]:
        """One live task under the active profile: the ``harness.run`` result, never relabelled, plus a ledger row.

        ``shadow`` (opt-in, owner ruling Q4): also run that candidate on the same task. Its output is recorded by digest
        and never returned or used; its cost is reported."""
        refused = self._refusal()
        if refused:
            return refused
        if shadow is not None and self._proposals.get(shadow.digest()) is not shadow:
            return _blocked(SCHEMA, "the shadow candidate was not made by this meta-harness")
        version, profile = self._head()
        memory = Memory(self.workspace / "harness.db")
        try:
            result, calls, elapsed = self._live(profile, task_input, f"metaharness:v{version}", memory)
        finally:
            memory.close()
        run_id = result.get("run_id") or uuid.uuid4().hex
        state = result.get("completion_state", "blocked-escalated")
        released = state != "blocked-escalated"
        row = {"workflow_digest": self.workflow.digest(), "profile_digest": profile.digest(), "profile_version": version,
               "purpose": "live", "completion_state": state, "released": released,
               "input_sha256": hashlib.sha256(task_input.encode("utf-8")).hexdigest(),
               "check_results": result.get("results", {}), "model_calls": calls, "wall_clock_s": elapsed}
        online = self._setting("online_tuning", False)
        if online and not released:
            from g6.context import scrub

            row["failure"] = {"input": scrub(task_input)[:600], "output": scrub(result.get("output") or "")[:600],
                              "failed_checks": [k for k, v in (result.get("results") or {}).items() if v != "pass"]}
        self.ledger.append("run", run_id, row)
        out = {**result, "profile_version": version, "profile_digest": profile.digest()}
        audit = self._audit(task_input, run_id, version, released)
        if audit:
            out["audit"] = audit
        if online and self._online is not None:
            every = int(self._setting("online_every", 32))
            if self._live_since_online() % every == 0:
                tuned = self.tune_online(self._online)
                out["online_tuning"] = {"code": tuned["warning_card"]["code"],
                                        "candidates": [p.candidate_digest for p in tuned.get("proposals", [])],
                                        "note": "candidates only: each needs the held-out gate and a person"}
        if shadow is not None:
            shadow_result, s_calls, s_elapsed = self._live(shadow.candidate, task_input, "metaharness:shadow",
                                                           Memory(":memory:"))
            s_output = shadow_result.get("output") or ""
            row = {"workflow_digest": self.workflow.digest(), "profile_digest": shadow.candidate_digest,
                   "purpose": "shadow", "shadow_of": run_id,
                   "completion_state": shadow_result.get("completion_state", "blocked-escalated"),
                   "model_calls": s_calls, "wall_clock_s": s_elapsed,
                   "output_sha256": hashlib.sha256(s_output.encode("utf-8")).hexdigest()}
            self.ledger.append("run", f"shadow-{run_id}", row)
            out["shadow"] = {k: row[k] for k in ("profile_digest", "completion_state", "model_calls", "wall_clock_s",
                                                  "output_sha256")} | {
                "note": "a shadow run's output is recorded by digest only and never returned or used"}
        return out

    # --- Phase 5: structural changes ------------------------------------------------------------------------------
    def _artifact_problems(self, profile: Profile) -> list[tuple[str, str]]:
        """Every artifact a profile names must exist here, with the bytes its digest names."""
        found = []
        for kind, items in (("distillate", profile.distillates.values()), ("code blob", profile.code.values()),
                            ("adapter", [profile.adapter] if profile.adapter else [])):
            for sha in items:
                try:
                    {"distillate": self.artifacts.distillate, "code blob": self.artifacts.blob,
                     "adapter": self.artifacts.adapter_dir}[kind](sha)
                except ArtifactError as exc:
                    found.append((SCHEMA, f"{kind}: {exc}"))
        return found

    @_serialized
    def admit_distillate(self, source: str, *, step: str = "answer",
                         training_case_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        """R2: compiled code that would replace a model call for ``step``, admitted only by
        ``g6.distillation.evaluate`` on the held-out split with a failing negative control. Code the AST pre-filter
        refuses is never run. An admission reads the held-out split, so it counts toward its evaluation limit."""
        refused = self._refusal() or self._vault_changed()
        if refused:
            return refused
        from g6.distillation import Candidate, HoldoutCase, check_source
        from g6.distillation import evaluate as admit

        if step not in self.workflow.steps:
            return _blocked(SCHEMA, f"no step {step!r} in this workflow")
        problems = check_source(source)
        if problems:
            return _blocked(SCHEMA, f"the distillate is refused before it runs: {problems}")
        digest_ = self.workflow.suite.digest("held_out")
        index = self._reuse_index(digest_)
        if index > self.workflow.policy.max_evaluations:
            return _blocked(HOLDOUT_REUSED, "the held-out split has reached its evaluation limit")
        cases = [HoldoutCase(c.case_id, self.workflow.task.replace("{input}", c.input), c.expected)
                 for c in self.workflow.suite.held_out]
        report = admit(Candidate(source, training_case_ids=tuple(training_case_ids)), cases,
                       negative_control=Candidate("def solve(x):\n    return None\n"))
        self.ledger.append("evaluation", f"distillate-{hashlib.sha256(source.encode()).hexdigest()}-{index}", {
            "purpose": "distillate admission", "split": "held_out", "split_digest": digest_, "verdict": report.verdict,
            "reason": report.reason, "passed": len(report.passed), "failed": len(report.failed)})
        if report.verdict != "passed":
            return _blocked(NO_NEW_CANDIDATE, f"the distillate was not admitted: {report.verdict}: {report.reason}",
                            report={"verdict": report.verdict, "failed": list(report.failed)[:10]})
        sha = self.artifacts.put_distillate(source, {"verdict": report.verdict, "passed": list(report.passed),
                                                     "negative_control_failed": list(report.negative_control_failed)})
        return _envelope("qualified-draft", "G6_E_META_DISTILLATE_ADMITTED", "admitted; a profile may now use it",
                         next_action=f"mh.candidate({{..., 'distillates': {{'{step}': '{sha}'}}}}, proposer=...)",
                         distillate=sha)

    @_serialized
    def code_candidate(self, files: Mapping[str, str], *, proposer: str, strategy: str = "hand-written",
                       claim: Mapping[str, str] | None = None) -> dict[str, Any]:
        """A code-level change (owner ruling Q7): new contents for files in the workflow's ``code_root``, stored by
        digest. It runs only in a fresh copy, out of process, and never edits the user's folder or G6's own source."""
        refused = self._refusal()
        if refused:
            return refused
        if self.workflow.code_root is None:
            return _blocked(SCHEMA, "this workflow has no code_root")
        _, base = self._head()
        code = dict(base.code) | {name: self.artifacts.put_blob(text) for name, text in files.items()}
        return self.candidate(base.model_dump(mode="json") | {"code": code}, proposer=proposer, strategy=strategy,
                              claim=claim)

    @_serialized
    def train_adapter(self, spec: Any, *, model: str, examples: list[tuple[str, str]] | None = None,
                      trainer: Callable[..., Any] | None = None) -> dict[str, Any]:
        """LoRA (owner ruling Q8): train an adapter for a declared local model on the dev split (or ``examples``, which
        may contain no held-out case). The base model's files must be unchanged afterwards. The adapter is stored by
        digest; a profile that names it is a candidate like any other."""
        refused = self._refusal()
        if refused:
            return refused
        from g6.metaharness import lora

        if model not in self.workflow.models or self.workflow.models[model].adapter_loader is None:
            return _blocked(SCHEMA, f"model {model!r} is not a declared local model that can load an adapter")
        rows = examples if examples is not None else [
            (self.workflow.task.replace("{input}", c.input), str(c.expected)) for c in self.workflow.suite.dev]
        held = {c.ref()["input_sha256"] for c in self.workflow.suite.held_out}
        held_tasks = {hashlib.sha256(self.workflow.task.replace("{input}", c.input).encode()).hexdigest()
                      for c in self.workflow.suite.held_out}
        leaked = [p for p, _ in rows if hashlib.sha256(p.encode()).hexdigest() in held | held_tasks]
        if leaked:
            return _blocked(NOT_HELD_OUT, f"{len(leaked)} training example(s) are held-out cases: refused")
        result = lora.train(spec, rows, trainer=trainer)
        if not result.ok:
            return _blocked(result.code, result.message, result.next_action)
        sha = self.artifacts.put_adapter(result.folder)
        self.ledger.append("evaluation", f"adapter-{sha}", {"purpose": "adapter training", "examples": len(rows),
                                                            "base_unchanged": True, "report": result.report})
        return _envelope("qualified-draft", "G6_E_META_ADAPTER_TRAINED", "an adapter, a candidate only",
                         next_action=f"mh.candidate({{..., 'adapter': '{sha}'}}, proposer=...)", adapter=sha,
                         report=result.report)

    # --- Phase 3: the monitor and opt-in online tuning --------------------------------------------------------------
    def _previous_approved(self) -> int | None:
        current = self.store.current_approved()
        earlier = [n for n in self.store.approved() if current is not None and n < current]
        return earlier[-1] if earlier else None

    def _audit(self, task_input: str, run_id: str, version: int, released: bool) -> dict[str, Any] | None:
        previous = self._previous_approved()
        if previous is None:
            return None
        n_live = sum(1 for r in self.ledger.rows("run") if r.get("purpose") == "live"
                     and r.get("profile_version") == version)
        rate = max(self.workflow.audit_floor, float(self._setting("audit_rate", self.workflow.audit_floor)))
        if not drift.due(n_live, rate):
            return None
        prev = self.store.profile(self.store.versions()[previous]["profile_digest"])
        result, calls, elapsed = self._live(prev, task_input, "metaharness:audit", Memory(":memory:"))
        prev_released = result.get("completion_state", "blocked-escalated") != "blocked-escalated"
        self.ledger.append("run", f"audit-{run_id}", {
            "workflow_digest": self.workflow.digest(), "profile_digest": prev.digest(), "purpose": "audit",
            "audit_of": run_id, "head_version": version, "previous_version": previous,
            "completion_state": result.get("completion_state", "blocked-escalated"), "released": prev_released,
            "current_released": released, "difference": float(prev_released) - float(released),
            "model_calls": calls, "wall_clock_s": elapsed})
        return {"previous_version": previous, "model_calls": calls, "wall_clock_s": elapsed,
                "note": "the previous approved version also ran on this task; its output is not returned"}

    @_serialized
    def promotion(self, request=None) -> dict[str, Any]:
        """Read/tighten the optional readiness cap; an upward step needs its own measured report and person."""
        from g6.metaharness.promotion import PromotionRequest, assess

        if request is not None and not isinstance(request, PromotionRequest):
            try:
                request = PromotionRequest.model_validate(request)
            except (TypeError, ValueError):
                return _blocked(SCHEMA, "invalid promotion request")
        return assess(self, request)

    @_serialized
    def monitor(self) -> dict[str, Any]:
        """The drift test over the audited pairs since the head moved; it may demote to the previous approved version.
        It does not run while G6 is stopped."""
        refused = self._refusal()
        if refused:
            return refused
        version, _ = self._head()
        pairs = [r["difference"] for r in self.ledger.rows("run")
                 if r.get("purpose") == "audit" and r.get("head_version") == version]
        promotion = self.promotion() if self.workflow.promotion_policy else None
        observations = drift.version_observations(self.ledger.rows("evaluation"), self.workflow.digest(),
                                                   alpha=self.workflow.policy.demote_alpha)
        for observation in observations:
            self._keep("ticket", digest(observation), {**observation, "kind": "self_observation",
                                                      "detector": observation["kind"]})
        verdict = drift.assess(pairs, alpha=self.workflow.policy.demote_alpha)
        summary = {"pairs": verdict.pairs, "mean_difference": verdict.mean_difference, "threshold": verdict.threshold,
                   "head_version": version}
        previous = self._previous_approved()
        if not verdict.demote or previous is None:
            return _envelope("qualified-draft", "G6_E_META_MONITOR", "no live drift detected", audit=summary, observations=observations, promotion=promotion,
                             next_action="Keep running; the monitor compares a share of runs with the previous version.")
        target = self.store.versions()[previous]
        number = self.store.append_version("demote", self.store.profile(target["profile_digest"]), at=_now(),
                                           accepted_by="monitor", target_version=previous,
                                           ledger_head=self.ledger.head(),
                                           reason=f"drift: mean difference {verdict.mean_difference} over "
                                                  f"{verdict.pairs} audited runs > {verdict.threshold}")
        self.ledger.append("ticket", f"ticket-{number}", {"kind": "re-evaluate", "demoted_version": version,
                                                         "now_version": number, "audit": summary})
        return _blocked(DRIFT_DEMOTED, f"version {version} fell behind the previous approved version on its audited "
                        f"runs; the head is now version {number} (= version {previous})",
                        "Re-evaluate the demoted candidate on a fresh held-out split before trying it again.",
                        audit=summary, head_version=number, observations=observations, promotion=promotion)

    @_serialized
    def set_audit_rate(self, rate: float, accepted_by: str) -> dict[str, Any]:
        """A person may raise the share of audited runs; never below the workflow's floor."""
        refused = self._refusal()
        if refused:
            return refused
        who = accepted_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person sets the audit rate")
        if not self.workflow.audit_floor <= rate <= 1:
            return _blocked(AUDIT_FLOOR, f"the audit rate must be between the workflow's floor "
                            f"{self.workflow.audit_floor} and 1")
        self.ledger.append("setting", f"audit-rate-{len(self.ledger.rows('setting'))}",
                           {"audit_rate": rate, "accepted_by": who, "at": _now()})
        return _envelope("qualified-draft", "G6_E_META_SETTING", f"audit rate {rate}", audit_rate=rate)

    def _live_since_online(self) -> int:
        rows = self.ledger.rows()
        start = max((i for i, r in enumerate(rows) if r["_kind"] == "setting" and r.get("online_tuning")), default=-1)
        return sum(1 for r in rows[start + 1:] if r["_kind"] == "run" and r.get("purpose") == "live")

    @_serialized
    def enable_online_tuning(self, strategy: Strategy, accepted_by: str, *, every: int = 32) -> dict[str, Any]:
        """Opt in (owner ruling Q3): every ``every`` live runs, ``strategy`` proposes from the live failures. What it
        proposes is a candidate only; a live run is never held-out evidence."""
        refused = self._refusal()
        if refused:
            return refused
        who = accepted_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person switches online tuning on")
        if every < 1:
            return _blocked(SCHEMA, "every must be >= 1")
        self.ledger.append("setting", f"online-{len(self.ledger.rows('setting'))}", {
            "online_tuning": True, "online_every": every, "online_strategy": strategy.name, "accepted_by": who,
            "at": _now()})
        self._online = strategy
        return _envelope("qualified-draft", "G6_E_META_SETTING", f"online tuning on, every {every} live runs",
                         next_action="Proposals it makes are candidates: evaluate, decide and approve each one.")

    @_serialized
    def disable_online_tuning(self, accepted_by: str) -> dict[str, Any]:
        who = accepted_by.strip()
        if self._problems:
            return self.envelope
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person switches online tuning off")
        self.ledger.append("setting", f"online-{len(self.ledger.rows('setting'))}",
                           {"online_tuning": False, "accepted_by": who, "at": _now()})
        self._online = None
        return _envelope("qualified-draft", "G6_E_META_SETTING", "online tuning off")

    @_serialized
    def tune_online(self, strategy: Strategy, *, max_candidates: int = 2, budget: int = 100) -> dict[str, Any]:
        """Candidates from the failures of live runs under the active version (online tuning must be on)."""
        refused = self._refusal()
        if refused:
            return refused
        if not self._setting("online_tuning", False):
            return _blocked(ONLINE_OFF, "online tuning is off (it is opt-in): a person switches it on first",
                            "mh.enable_online_tuning(strategy, accepted_by='<your name>')")
        version, base = self._head()
        from g6.guards import PatternGuard
        from g6.metaharness.propose import _clean

        guard, failures = PatternGuard(), []
        for r in self.ledger.rows("run"):
            if r.get("purpose") == "live" and r.get("profile_version") == version and r.get("failure"):
                f = r["failure"]
                task_input, in_flags = _clean(f["input"], guard)
                output, out_flags = _clean(f["output"], guard)
                failures.append(DevFailure(r["_id"], task_input, output, "", tuple(f["failed_checks"]), "unknown",
                                           (), in_flags + out_flags))
        view = ProposerView(workflow=self.workflow.name, task=self.workflow.task,
                            checks=tuple(c.predicate for c in self.workflow.checks), steps=self.workflow.steps,
                            models=tuple(self.workflow.models), optional_checks=tuple(self.workflow.optional_checks),
                            base=base.model_dump(mode="json"), dev=(), failures=tuple(failures), dev_success=0,
                            notes=("failures from live runs under the active version; no answers are known for them",))
        return self._offer(view, strategy, f"auto:online:{strategy.name}", DevBudget(budget), max_candidates)

    @_serialized
    def rollback(self, version: int, accepted_by: str) -> dict[str, Any]:
        """A person moves the head back to an approved version; allowed while G6 is stopped (it starts no work)."""
        refused = self._refusal(allow_stopped=True)
        if refused:
            return refused
        who = accepted_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person must ask for a rollback (no empty or auto: name)")
        versions = self.store.versions()
        if not 0 <= version < len(versions) or versions[version]["kind"] not in ("initial", "activate"):
            return _blocked(APPROVAL_REQUIRED, f"version {version} is not a version a person approved")
        profile = self.store.profile(versions[version]["profile_digest"])
        number = self.store.append_version("rollback", profile, at=_now(), accepted_by=who, target_version=version,
                                           ledger_head=self.ledger.head(), snapshot=self._snapshot(len(versions)))
        return _envelope("qualified-draft", "G6_E_META_ROLLED_BACK", f"version {number} points at version {version}",
                         head_version=number, head_profile=profile.digest())

    # --- Phase 4: profile packs ----------------------------------------------------------------------------------------
    @_serialized
    def export(self, path: str | Path, *, version: int | None = None) -> dict[str, Any]:
        """A profile pack of the active (or a given approved) version, with the evidence recorded for it, stated."""
        if self._problems:
            return self.envelope
        from g6.metaharness.packs import build_pack, write_pack

        versions = self.store.versions()
        number = version if version is not None else versions[-1]["version"]
        if not 0 <= number < len(versions):
            return _blocked(SCHEMA, f"no version {number}")
        profile = self.store.profile(versions[number]["profile_digest"])
        theories = [t for t in (self.theories.get(h) for h in profile.hints) if t] if self.theories else []
        evaluations = [r for r in self.ledger.rows("evaluation")
                       if r.get("candidate_digest") == profile.digest() and r.get("split") == "held_out"]
        stated = ({k: evaluations[-1][k] for k in ("n", "success", "split_digest")} if evaluations else {})
        pack = build_pack(workflow=self.workflow.name, workflow_digest=self.workflow.digest(),
                          profile=profile.model_dump(mode="json"), theories=theories, stated_evidence=stated)
        risks = write_pack(pack, path)
        if risks:
            return _blocked(SCHEMA, f"not exported: the pack trips the secret scan ({risks})")
        return _envelope("qualified-draft", "G6_E_META_EXPORTED", f"version {number} exported", path=str(path),
                         content_digest=pack["content_digest"], hints=len(theories))

    @_serialized
    def import_profile(self, path: str | Path, *, proposer: str,
                       routing: Mapping[str, str] | None = None) -> dict[str, Any]:
        """A pack as a candidate only: its stated evidence is ignored, its hints become candidate theories, and it must
        win on this workflow's held-out split. ``routing`` maps the pack's steps onto this workflow's declared models."""
        refused = self._refusal()
        if refused:
            return refused
        from g6.metaharness.packs import read_pack, theories_of

        pack, problems = read_pack(path)
        if pack is None or problems:
            return _blocked(SCHEMA, "; ".join(problems) or "not a pack", "Ask the exporter for a clean pack.")
        data = dict(pack["profile"])
        if routing is not None:
            data["routing"] = dict(routing)
        notes = []
        theories = theories_of(pack)
        if theories and self.theories is not None:
            self.theories.add_candidates(theories)
        elif theories:
            data["hints"] = []
            notes.append("the pack's hints were dropped: this meta-harness has no theory store")
        made = self.candidate(data, proposer=f"import:{proposer}", strategy="import")
        if made["completion_state"] == "blocked-escalated":
            return made
        return {**made, "stated_evidence_ignored": pack.get("stated_evidence") or {}, "notes": notes,
                "message": "imported as a candidate: it must win on this workflow's held-out split"}

    @_serialized
    def inspect(self) -> dict[str, Any]:
        if self._problems:
            return self.envelope
        return _envelope("qualified-draft", "G6_E_META_INSPECT", "Workflow history; this inspection executed no application code", versions=self.store.versions(),
                         confirmed=self.store.confirmed(), store_head=self.store.head_hash(),
                         ledger_head=self.ledger.head(), evaluations=self.ledger.rows("evaluation"),
                         decisions=[{k: r[k] for k in ("verdict", "code", "candidate_digest", "proposer")}
                                    for r in self.ledger.rows("decision")])

    @_serialized
    def report(self) -> dict[str, Any]:
        """Claims tagged checked / measured / assumed (META_HARNESS_PLAN.md 5.6)."""
        if self._problems:
            return self.envelope
        versions = self.store.versions()
        runs = self.ledger.rows("run")
        heads = {v["version"]: v["profile_digest"] for v in versions}
        checked = [
            {"claim": "the store's hash chain verifies", "holds": self.store.verify() == ()},
            {"claim": "the ledger's hash chain verifies", "holds": self.ledger.verify() == ()},
            {"claim": "every activation names a person and a decision",
             "holds": all(v["accepted_by"] and v["decision_digest"] for v in versions if v["kind"] == "activate")},
            {"claim": "every live run used the profile of the version it names",
             "holds": all(heads.get(r.get("profile_version")) == r["profile_digest"] for r in runs
                          if r["purpose"] == "live")},
        ]
        last = ([r for r in self.ledger.rows("evaluation") if r.get("split") == "held_out"] or [None])[-1]
        measured = [{"claim": "the last held-out evaluation", "value": last}] if last else []
        online = [r for r in self.ledger.rows("setting") if "online_tuning" in r]
        if online and online[-1]["online_tuning"]:
            checked.append({"claim": f"online tuning is ON (opt-in by {online[-1]['accepted_by']} at "
                                     f"{online[-1]['at']}): live failures shape candidates; none goes live without the "
                                     "held-out gate and a person", "holds": True})
        else:
            checked.append({"claim": "online tuning is off", "holds": True})
        checked.append({"claim": f"audited share of live runs: at least "
                                 f"{max(self.workflow.audit_floor, float(self._setting('audit_rate', 0)))}",
                        "holds": True})
        assumed = [
            "live tasks resemble the suite (stationarity, KS A1)",
            "the held-out answers are right, and the oracle reads them correctly",
            "accepted_by names the person who accepted (recorded as given)",
            f"the held-out split is {self.workflow.suite.provenance.get('held_out', 'caller')}-authored",
        ]
        return _envelope("qualified-draft", "G6_E_META_REPORT", "what this meta-harness has shown",
                         checked=checked, measured=measured, assumed=assumed)


def adapt(workflow: Workflow, executor_factory: ExecutorFactory | None = None, *,
          workspace: str | Path | None = None, theories: TheoryStore | None = None,
          clock: Callable[[], float] = time.monotonic) -> MetaHarness:
    """A ``MetaHarness`` for ``workflow``. Its ``envelope`` is ``blocked-escalated`` when the workflow is invalid, and
    then every call returns that refusal. ``workspace`` is resolved once, here: the store, the run ledger, the
    artifacts and the live runs' harness memory all live in that one folder."""
    problems = workflow.problems()
    folder = (Path(workspace) if workspace is not None else state_path("metaharness", workflow.name)).resolve()
    return MetaHarness(workflow, executor_factory, folder, theories=theories, clock=clock, problems=problems)
