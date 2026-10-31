# SPDX-License-Identifier: Apache-2.0
"""Adoption with no person, in the loop's own workspace only.

A ``ShadowHarness`` is a ``MetaHarness`` whose head lives in a separate ledger, ``adoptions.jsonl``. A candidate the
gate recommends is recorded there, and every later step runs on it, so the loop compounds. What a person did is never
touched or imitated: the profile store keeps the one version they confirmed, so anything opening this workspace as an
ordinary meta-harness sees that version and no other; ``activate`` and ``rollback`` still need a person and are
refused here; a live ``run`` is refused too, since nothing outside the loop may run on a head no person accepted. Each
row is signed with the loop's ``auto:`` name, and the ledger refuses any other. Nothing here reaches outward. Whether a
passed gate may ever stand in for a person outside this workspace is the owner's ruling, and ``g6.metaharness.loop``
keeps it behind one named setting.

It stays one workspace on purpose. The run ledger counts each use of the held-out split, and starting a fresh workspace
per adoption would reset that count and let the loop spend a split for ever.

The monitor reads a split the gate never saw. Each audit runs the adopted version and the one before it on a case and
takes the judge's verdict on both, and ``monitor.assess`` decides over the pairs that disagree: under no change a
disagreement goes either way, so ties add nothing and leaving them out keeps its bound. It may only move back, one
version, as the meta-harness's monitor does.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import ChainLedger
from g6.metaharness import monitor as drift
from g6.metaharness import recovery
from g6.metaharness.api import MetaHarness, _blocked, _envelope, _now, _serialized
from g6.metaharness.executor import ExecutorFactory
from g6.metaharness.gate import GateDecision
from g6.metaharness.propose import DevBudget, build_view, dev_run
from g6.metaharness.schema import STANDARD_CHANGED, Profile, SuiteCase, Workflow

#: The name on every row. An ``auto:`` name is one the store's rules and ``activate`` refuse, so it can never pass for
#: a person's.
ADOPTER = "auto:loop"
SCOPE = "shadow"
SHADOW_ONLY = "G6_E_LOOP_SHADOW_ONLY"
NOT_ADOPTABLE = "G6_E_LOOP_NOT_ADOPTABLE"
ADOPTED = "G6_E_LOOP_ADOPTED"
ROLLED_BACK = "G6_E_LOOP_ROLLED_BACK"
AUDITED = "G6_E_LOOP_AUDITED"
MEASURED = "G6_E_LOOP_MEASURED"


def _adopted(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The adoption the head stands for: the last row, or what a rollback went back to; ``None`` at the first version."""
    row = rows[-1] if rows else None
    while row is not None and row["kind"] == "rollback":
        row = rows[row["target_version"] - 1] if row["target_version"] else None
    return row


def _rules(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
    if kind not in ("adopt", "rollback"):
        return [f"unknown kind {kind!r}"]
    problems = []
    if not is_automated(str(body.get("by") or "")):
        problems.append("a shadow row is the loop's own: it never names a person")
    if body.get("scope") != SCOPE:
        problems.append(f"a shadow row holds only in scope {SCOPE!r}")
    rows = list(seen.values())
    if body.get("version") != len(rows) + 1 or ident != str(len(rows) + 1):
        problems.append(f"expected version {len(rows) + 1}")
    if kind == "adopt" and not (body.get("decision_digest") and body.get("profile")):
        problems.append("an adoption needs the gate's decision and the profile")
    if kind == "rollback":
        current = _adopted(rows)
        if current is None or body.get("target_version") != current["base_version"]:
            problems.append("a rollback goes only to the version before the adopted one")
    return problems


class ShadowHarness(MetaHarness):
    def __init__(self, workflow: Workflow, factory: ExecutorFactory | None, workspace: Path, *,
                 clock: Callable[[], float], problems: list[tuple[str, str]]) -> None:
        super().__init__(workflow, factory, workspace, theories=None, clock=clock, problems=problems)
        self.adoptions = ChainLedger(workspace / "adoptions.jsonl", validate=_rules) if not problems else None

    def adoption_rows(self) -> list[dict]:
        return [e["body"] for e in self.adoptions.entries()]

    def _head(self) -> tuple[int, Profile]:
        rows = self.adoption_rows()
        if not rows:
            return super()._head()
        return rows[-1]["version"], Profile.model_validate(rows[-1]["profile"])

    def _profile_at(self, version: int) -> Profile:
        if not version:
            return super()._head()[1]
        return Profile.model_validate(self.adoption_rows()[version - 1]["profile"])

    def held_out_left(self) -> int:
        """How many evaluations this held-out split still allows."""
        used = self._reuse_index(self.workflow.suite.digest("held_out")) - 1
        return self.workflow.policy.max_evaluations - used

    @_serialized
    def adopt(self, decision: GateDecision) -> dict[str, Any]:
        """Make ``decision``'s candidate the loop's head. Only a recommendation this harness issued, on this head."""
        refused = self._refusal()
        if refused:
            return refused
        if self._decisions.get(decision.digest()) is not decision:
            return _blocked(NOT_ADOPTABLE, "this decision was not issued by this harness")
        if decision.verdict != "recommend":
            return _blocked(NOT_ADOPTABLE, "only a 'recommend' decision can be adopted")
        version, base = self._head()
        if base.digest() != decision.base_digest or self.workflow.digest() != decision.workflow_digest:
            return _blocked(STANDARD_CHANGED, "the head or the workflow changed since the decision")
        proposal, why = self._recorded("proposal", decision.proposal_digest)
        if proposal is None:
            return _blocked(NOT_ADOPTABLE, why)
        number = len(self.adoption_rows()) + 1
        self.adoptions.append("adopt", str(number), {
            "kind": "adopt", "version": number, "base_version": version, "by": ADOPTER, "scope": SCOPE, "at": _now(),
            "decision_digest": decision.digest(), "report_digest": decision.report_digest,
            "profile_digest": proposal.candidate_digest, "profile": proposal.candidate.model_dump(mode="json"),
            "proposer": decision.proposer, "diff": dict(proposal.diff)})
        return _envelope("qualified-draft", ADOPTED, f"version {number} is the loop's head, in its own workspace only",
                         next_action="Later ticks run on it; the monitor compares it with the version before.",
                         head_version=number, head_profile=proposal.candidate_digest)

    @_serialized
    def measure(self, *, budget: int) -> dict[str, Any]:
        """The head on the tuning cases, its failures and their causes: what a proposer sees, and nothing held out."""
        refused = self._refusal()
        if refused:
            return refused
        version, base = self._head()
        view = build_view(self.workflow, self.factory, base, theories=self.theories, clock=self._clock,
                          budget=DevBudget(budget), artifacts=self.artifacts)
        plan = recovery.diagnose(view, self.workflow, self.ledger.rows("decision"))
        return _envelope("qualified-draft", MEASURED, plan.summary, next_action=plan.next_action, view=view,
                         recovery=plan.to_dict(), head_version=version)

    @_serialized
    def audit(self, cases: Sequence[SuiteCase], *, count: int) -> dict[str, Any]:
        """Audit up to ``count`` cases not yet used for the adopted version; roll back if the monitor says it got worse.
        Each case is used once per adoption: a repeated case would count one piece of evidence twice."""
        quiet = {"audited": 0, "pairs": 0, "mean_difference": 0.0, "threshold": None, "rolled_back": False}
        refused = self._refusal()
        if refused:
            return {**refused, **quiet}
        adopted = _adopted(self.adoption_rows())
        if adopted is None:
            return _envelope("qualified-draft", AUDITED, "nothing is adopted, so there is nothing to audit", **quiet)
        version = adopted["version"]
        head, previous = Profile.model_validate(adopted["profile"]), self._profile_at(adopted["base_version"])
        rows = [r for r in self.ledger.rows("run") if r.get("purpose") == "audit" and r.get("head_version") == version]
        done = {r["case_id"] for r in rows}
        fresh = [c for c in cases if c.case_id not in done][:count]
        differences = [r["difference"] for r in rows]
        for case in fresh:
            head_ok, result, _ = self._on_case(head, case)
            previous_ok, _, _ = self._on_case(previous, case)
            differences.append(float(previous_ok) - float(head_ok))
            self.ledger.append("run", f"audit-{version}-{case.case_id}", {
                "workflow_digest": self.workflow.digest(), "profile_digest": adopted["profile_digest"],
                "purpose": "audit", "head_version": version, "previous_version": adopted["base_version"],
                "case_id": case.case_id, "difference": differences[-1],
                "completion_state": result.get("completion_state", "blocked-escalated")})
        verdict = drift.assess([d for d in differences if d], alpha=self.workflow.policy.demote_alpha)
        said = {"audited": len(fresh), "pairs": verdict.pairs, "mean_difference": verdict.mean_difference,
                "threshold": verdict.threshold if verdict.pairs else None, "rolled_back": verdict.demote}
        if not verdict.demote:
            return _envelope("qualified-draft", AUDITED, f"{len(fresh)} audited, no sign it got worse", **said)
        number = len(self.adoption_rows()) + 1
        self.adoptions.append("rollback", str(number), {
            "kind": "rollback", "version": number, "target_version": adopted["base_version"], "by": ADOPTER,
            "scope": SCOPE, "at": _now(), "undoes": version, "audit": said,
            "profile": previous.model_dump(mode="json"), "profile_digest": previous.digest()})
        self.ledger.append("ticket", f"ticket-{number}", {"kind": "re-evaluate", "demoted_version": version,
                                                          "now_version": number, "audit": said})
        return _blocked(ROLLED_BACK, f"version {version} fell behind the version before it on {verdict.pairs} audited "
                        f"disagreements; the head is version {number}", **said, undoes=version, head_version=number)

    def _on_case(self, profile: Profile, case: SuiteCase) -> tuple[bool, dict[str, Any], int]:
        return dev_run(self.workflow, self.factory, profile, case, theories=self.theories, clock=self._clock,
                       artifacts=self.artifacts)

    def _refuse_outside_shadow(self, what: str) -> dict[str, Any]:
        return _blocked(SHADOW_ONLY, f"a shadow harness does not {what}: it holds the loop's own head, which no person "
                        "accepted", "Open the workspace with adapt() for what a person may do.")

    def activate(self, decision: GateDecision | str, accepted_by: str, *, candidate_digest: str = "") -> dict[str, Any]:
        return self._refuse_outside_shadow("activate")

    def rollback(self, version: int, accepted_by: str) -> dict[str, Any]:
        return self._refuse_outside_shadow("roll back a person's versions")

    def run(self, task_input: str, *, shadow: Any = None) -> dict[str, Any]:
        return self._refuse_outside_shadow("run live tasks")


def adapt_shadow(workflow: Workflow, factory: ExecutorFactory | None = None, *, workspace: str | Path,
                 clock: Callable[[], float] = time.monotonic) -> ShadowHarness:
    return ShadowHarness(workflow, factory, Path(workspace).resolve(), clock=clock, problems=workflow.problems())


__all__ = ["ADOPTED", "ADOPTER", "NOT_ADOPTABLE", "ROLLED_BACK", "SCOPE", "SHADOW_ONLY", "ShadowHarness", "adapt_shadow"]
