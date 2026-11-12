# SPDX-License-Identifier: Apache-2.0
"""Adoption with no person, in the loop's own workspace only.

A ``ShadowHarness`` is a ``MetaHarness`` whose head lives in a separate ledger, ``adoptions.jsonl``. A candidate the
gate recommends is recorded there, and every later step runs on it, so the loop compounds. What a person did is never
touched or imitated: the profile store keeps the one version they confirmed, so anything opening this workspace as an
ordinary meta-harness sees that version and no other; ``activate`` and ``rollback`` still need a person and are
refused here; a live ``run`` is refused too, since nothing outside the loop may run on a head no person accepted. Each
adoption is signed with the loop's ``auto:`` name, and the ledger refuses any other. The one row a person's name can
stand on is a rollback, from ``demote``: taking the head back moves nothing outside this chain, and a daily reviewer needs
a lever short of the stop. It asks for no measurement, so it claims none. Nothing here reaches outward. Whether a
passed gate may ever stand in for a person outside this workspace is the owner's ruling, and ``g6.metaharness.loop``
keeps it behind one named setting.

It stays one workspace on purpose. The run ledger counts each use of the held-out split, and starting a fresh workspace
per adoption would reset that count and let the loop spend a split for ever.

The monitor reads a split the gate never saw. Each audit runs the adopted version and the one before it on a case and
takes the judge's verdict on both, and ``monitor.assess`` decides over the pairs that disagree: under no change a
disagreement goes either way, so ties add nothing and leaving them out keeps its bound. It may only move back, one
version, as the meta-harness's monitor does.

**The generations.** Each row of ``adoptions.jsonl`` is a numbered generation, in this one chain: an adoption, or a
rollback, so the lineage shows losses as well as gains. A row's ``version`` is its number and ``base_version`` its parent.
It carries the profile's digest, the change in plain words, what the proposer found, and what was measured: the held-out
split's figures before and after, as the gate read them, and for a rollback the same figures the other way with what the
monitor saw. They are measurements on those splits, nothing stronger. An adoption also carries ``frozen``: the digests
of what judges, read before the cycle developed anything, which the harness's own last check found still on disk as it
was written; an adoption with no such reading, or by a harness built with no such check, is never written. These are the loop's own numbers, which have nothing to do with G6's version, its tags or its releases: those
stay the owner's.
"""
from __future__ import annotations

import copy
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import ChainLedger
from g6.metaharness import monitor as drift
from g6.metaharness import recovery
from g6.metaharness.api import MetaHarness, _blocked, _envelope, _now, _serialized
from g6.metaharness.evaluate import EvaluationReport
from g6.metaharness.executor import ExecutorFactory
from g6.metaharness.gate import GateDecision
from g6.metaharness.propose import DevBudget, ProposerView, Strategy, build_view, dev_run
from g6.metaharness.schema import STANDARD_CHANGED, Profile, Proposal, SuiteCase, Workflow
from g6.self_maintenance.envelope import FINDINGS

#: The name on every row. An ``auto:`` name is one the store's rules and ``activate`` refuse, so it can never pass for
#: a person's.
ADOPTER = "auto:loop"
ADOPTIONS = "adoptions.jsonl"
SCOPE = "shadow"
SHADOW_ONLY = "G6_E_LOOP_SHADOW_ONLY"
NOT_ADOPTABLE = "G6_E_LOOP_NOT_ADOPTABLE"
ADOPTED = "G6_E_LOOP_ADOPTED"
ROLLED_BACK = "G6_E_LOOP_ROLLED_BACK"
DEMOTED = "G6_E_LOOP_DEMOTED"
NOT_DEMOTABLE = "G6_E_LOOP_NOT_DEMOTABLE"
AUDITED = "G6_E_LOOP_AUDITED"
MEASURED = "G6_E_LOOP_MEASURED"
#: What judges changed, or was never read, so no adoption may stand on what was measured (``g6.metaharness.loop``).
FROZEN_CHANGED = "G6_E_LOOP_FROZEN_CHANGED"


@dataclass(frozen=True)
class Held:
    """The one success value of a harness's last check: what judges, as read before the cycle developed anything, is what
    is on disk now. ``frozen`` is that reading, which an adoption's row carries. Anything else a check returns is a
    refusal."""

    frozen: Mapping[str, str]
#: What the harness holds a run for when its executor raised, which a failed model call does.
RUN_FAILED = "G6_E_HARNESS_EXECUTOR_FAILED"


def _adopted(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The adoption the head stands for: the last row, or what a rollback went back to; ``None`` at the first version."""
    row = rows[-1] if rows else None
    while row is not None and row["kind"] == "rollback":
        row = rows[row["target_version"] - 1] if row["target_version"] else None
    return row


def _change(proposal: Proposal) -> str:
    """The change in plain words: who proposed it and what it moved. The row's ``diff`` holds the values."""
    return f"{proposal.proposer} changed {', '.join(name.replace('_', ' ') for name in sorted(proposal.diff)) or 'nothing'}"


def _measured(report: EvaluationReport) -> dict[str, Any]:
    """The held-out figures an adoption stands on, as the gate read them: before is the head, after the candidate. What the
    gain cost is beside them and under ``not_gated``, since no adoption here stands on it."""
    success = report.estimates["success"]

    def dollars(side: str) -> float | None:
        known = [row[side].get("cost_usd") for row in report.cases]
        return sum(known) / len(known) if known and None not in known else None     # a missing receipt reads as unknown, never as zero

    return {"split": report.split, "cases": report.n, "before": success["base_mean"], "after": success["candidate_mean"],
            "wins": report.success["wins"], "losses": report.success["losses"],
            "not_gated": {**{name: {"before": report.estimates[name]["base_mean"], "after": report.estimates[name]["candidate_mean"]}
                             for name in ("wall_clock_s", "model_calls")},
                          "cost_usd": {"before": dollars("base"), "after": dollars("candidate")}}}


def _refusal_in(said: Any, who: str) -> dict[str, Any]:
    """What a last check returned, as the refusal it is: a blocked reply as it stands, and anything else, empty or not, as
    ``FROZEN_CHANGED``. Nothing here is ever read as success."""
    card = said.get("warning_card") if isinstance(said, Mapping) else None
    if (isinstance(said, Mapping) and said.get("completion_state") == "blocked-escalated" and isinstance(card, Mapping)
            and isinstance(card.get("code"), str) and isinstance(said.get("message"), str)):
        return dict(said)
    return _blocked(FROZEN_CHANGED, f"{who} gave neither a refusal nor this harness's own success value, so nothing is "
                    "adopted")


def _generation(row: Mapping[str, Any]) -> dict[str, Any]:
    """A row of the chain as the lineage reads it."""
    undoing = ({"undoes": row["undoes"], "restores": row["target_version"],
                "asked_by": "" if is_automated(row["by"]) else row["by"]} if row["kind"] == "rollback" else {})
    return {"generation": row["version"], "parent": row.get("base_version"), "kind": row["kind"], "at": row["at"],
            "profile_digest": row["profile_digest"], "change": row.get("change", ""), "measured": row.get("measured", {}),
            **undoing, **{name: row.get(name, [] if name == "composed_from" else "") for name in FINDINGS}}


def lineage(workspace: str | Path, *, last: int = 8) -> dict[str, Any]:
    """The current generation and the ``last`` before it, read from the chain and written nowhere. Generation 0 is what a
    person confirmed. A chain that does not check out raises, as it does for the loop."""
    rows = [entry["body"] for entry in ChainLedger(Path(workspace) / ADOPTIONS, validate=_rules).entries()]
    return {"current": rows[-1]["version"] if rows else 0, "recent": [_generation(row) for row in rows[-last:]]}


def _rules(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
    if kind not in ("adopt", "rollback"):
        return [f"unknown kind {kind!r}"]
    problems = []
    who = str(body.get("by") or "")
    if kind == "adopt" and not is_automated(who):
        problems.append("an adoption is the loop's own: it never names a person")
    if kind == "rollback" and not who.strip():
        problems.append("a rollback names who asked for it")
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
                 clock: Callable[[], float], problems: list[tuple[str, str]],
                 before_evaluation: Callable[[], None] = lambda: None,
                 hold: Callable[[], Any] | None = None) -> None:
        super().__init__(workflow, factory, workspace, theories=None, clock=clock, problems=problems)
        self.adoptions = ChainLedger(workspace / ADOPTIONS, validate=_rules) if not problems else None
        self._measured: tuple[tuple[str, str, int], ProposerView] | None = None
        self._before_evaluation = before_evaluation
        self._hold = hold                     # this harness's own last check: what judges, read and compared

    def refresh(self) -> None:
        """Read every ledger again: another handle may have written them. What it wrote decides the head."""
        if self._moved():
            self.store._chain.reload()
            self.ledger._chain.reload()
        self.adoptions.reload()

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
    def adopt(self, decision: GateDecision, *, frozen: Mapping[str, str] | None = None,
              last_check: Callable[[], dict[str, Any] | None] | None = None,
              findings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Make ``decision``'s candidate the loop's head and its next generation, with what its proposer ``findings``.
        Only a recommendation this harness issued, on this head, and only once this harness's own last check, the one it
        was built with (``hold``), says that what judges, as the cycle read it before it developed anything, is what is on
        disk now. That check runs last: the row is made first, so nothing but its write comes after the check, under the
        lock this harness holds for every write. Only its own success value, a ``Held``, lets the write through, and the row
        carries the reading it holds. Anything else it returns stands in place of the adoption, or is refused as
        ``FROZEN_CHANGED``. A caller's ``frozen`` must be that same reading, and a caller's ``last_check`` can only add a
        refusal: anything but ``None`` from it refuses. Neither stands in for the harness's own. A harness built with no
        check adopts nothing: this is the one function that writes an adoption."""
        refused = self._refusal()
        if refused:
            return refused
        if self._hold is None:
            return _blocked(FROZEN_CHANGED, "this harness was built with no check of what judges, so nothing is adopted",
                            "Adopt through the loop's tick, which reads what judges before it develops anything.")
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
        report, why = self._recorded("report", decision.report_digest)
        if report is None:
            return _blocked(NOT_ADOPTABLE, why)
        number = len(self.adoption_rows()) + 1
        row = {"kind": "adopt", "version": number, "base_version": version, "by": ADOPTER, "scope": SCOPE, "at": _now(),
               "decision_digest": decision.digest(), "report_digest": decision.report_digest,
               "profile_digest": proposal.candidate_digest, "profile": proposal.candidate.model_dump(mode="json"),
               "proposer": decision.proposer, "diff": dict(proposal.diff), "change": _change(proposal),
               "measured": _measured(report), **{name: value for name, value in (findings or {}).items() if name in FINDINGS}}
        said = last_check() if last_check is not None else None
        if said is not None:
            return _refusal_in(said, "the caller's own last check")
        held = self._hold()                     # last: the row is made, and only its write follows
        if type(held) is not Held:
            return _refusal_in(held, "this harness's last check")
        if frozen is not None and dict(frozen) != dict(held.frozen):
            return _blocked(FROZEN_CHANGED, "the reading of what judges this adoption was offered is not the one this "
                            "cycle read before it developed anything, so nothing is adopted")
        row["frozen"] = dict(held.frozen)
        self.adoptions.append("adopt", str(number), row)
        return _envelope("qualified-draft", ADOPTED, f"version {number} is the loop's head, in its own workspace only",
                         next_action="Later ticks run on it; the monitor compares it with the version before.",
                         head_version=number, head_profile=proposal.candidate_digest)

    def _view(self, budget: DevBudget, *, measuring: bool = False) -> ProposerView:
        """The head on the tuning cases, run once for each head, case set and budget, so an unchanged head costs nothing
        however many ticks and proposers read it. A run the model failed measured the provider and not the head, so the
        next measurement runs such a view again, and until then the proposers read it. It is held in memory, so a restart
        measures once more. Each caller reads a copy of its own, so what one proposer, or a domain shown the view, does to
        it, no later reader sees."""
        _, base = self._head()
        key = (base.digest(), self.workflow.digest(), budget.limit)
        kept = self._measured
        if kept is not None and kept[0] == key and not (measuring and any(f.code == RUN_FAILED for f in kept[1].failures)):
            return copy.deepcopy(kept[1])
        view = build_view(self.workflow, self.factory, base, theories=self.theories, clock=self._clock, budget=budget,
                          artifacts=self.artifacts)
        self._measured = key, view
        return copy.deepcopy(view)

    def evaluate(self, proposal: Proposal, *, budget: int | None = None) -> dict[str, Any]:
        """As ``MetaHarness.evaluate``, once ``before_evaluation`` has said it may run. What that raises ends the tick with
        no report, so the split is not spent."""
        self._before_evaluation()
        return super().evaluate(proposal, budget=budget)

    def unfinished(self) -> list[Proposal]:
        """The candidates on this head that a run left with no result: proposed since the last tuning run was written, and
        not beaten in a race. Only a tick cut off before its end leaves any, since ``proposal`` is written as it is made
        and a proposer may not offer one twice. It is read from the ledger, so it holds across a restart."""
        rows = self.ledger.rows()
        since = rows[1 + max((i for i, row in enumerate(rows) if row["_kind"] == "tuning_run"), default=-1):]
        beaten = {entry["candidate_digest"] for row in since if row["_kind"] == "selection" for entry in row["ranking"]
                  if entry["candidate_digest"] != row["winner"]}
        found = [self._recorded("proposal", row["_id"])[0] for row in since
                 if row["_kind"] == "proposal" and row["candidate_digest"] not in beaten]
        return [proposal for proposal in found if proposal is not None and proposal.base_digest == self._head()[1].digest()]

    @_serialized
    def propose(self, strategy: Strategy, *, budget: int = 200, max_candidates: int = 3) -> dict[str, Any]:
        """As ``MetaHarness.propose``, on the view the head already has."""
        refused = self._refusal()
        if refused:
            return refused
        meter = DevBudget(budget)
        return self._offer(self._view(meter), strategy, f"auto:{strategy.name}", meter, max_candidates)

    @_serialized
    def measure(self, *, budget: int) -> dict[str, Any]:
        """The head on the tuning cases, its failures and their causes: what a proposer sees, and nothing held out."""
        refused = self._refusal()
        if refused:
            return refused
        version, _ = self._head()
        view = self._view(DevBudget(budget), measuring=True)
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
        gate = adopted.get("measured", {})
        self.adoptions.append("rollback", str(number), {
            "kind": "rollback", "version": number, "base_version": number - 1, "target_version": adopted["base_version"],
            "by": ADOPTER, "scope": SCOPE, "at": _now(), "undoes": version, "audit": said,
            "profile": previous.model_dump(mode="json"), "profile_digest": previous.digest(),
            "change": f"went back to generation {adopted['base_version']} after generation {version} fell behind it",
            "measured": {"split": gate.get("split"), "cases": gate.get("cases"), "before": gate.get("after"),
                         "after": gate.get("before"),
                         "monitor": {key: said[key] for key in ("audited", "pairs", "mean_difference")}}})
        self.ledger.append("ticket", f"ticket-{number}", {"kind": "re-evaluate", "demoted_version": version,
                                                          "now_version": number, "audit": said})
        return _blocked(ROLLED_BACK, f"version {version} fell behind the version before it on {verdict.pairs} audited "
                        f"disagreements; the head is version {number}", **said, undoes=version, head_version=number)

    @_serialized
    def demote(self, generation: int, by: str) -> dict[str, Any]:
        """A person takes the head back one version, as the monitor may: the row is theirs, and it measures nothing.
        ``generation`` is the head they read, so a head that moved on since is theirs to read again."""
        refused = self._refusal()
        if refused:
            return refused
        rows = self.adoption_rows()
        if generation != len(rows):
            return _blocked(NOT_DEMOTABLE, f"the head is generation {len(rows)}, not {generation}, and nothing was changed",
                            "Read the lineage again and name the generation that stands.")
        adopted = _adopted(rows)
        if adopted is None:
            return _blocked(NOT_DEMOTABLE, f"generation {generation} is what a person confirmed: nothing adopted stands to "
                            "take back, and nothing was changed")
        number, previous = generation + 1, self._profile_at(adopted["base_version"])
        self.adoptions.append("rollback", str(number), {
            "kind": "rollback", "version": number, "base_version": generation, "target_version": adopted["base_version"],
            "by": by, "scope": SCOPE, "at": _now(), "undoes": adopted["version"], "measured": {},
            "profile": previous.model_dump(mode="json"), "profile_digest": previous.digest(),
            "change": f"a person went back to generation {adopted['base_version']} from generation {adopted['version']}"})
        return _envelope("qualified-draft", DEMOTED, f"generation {adopted['version']} is taken back: the head is generation "
                         f"{number}, the profile of generation {adopted['base_version']}", head_version=number,
                         undoes=adopted["version"], next_action="Later ticks run on it. The monitor compares it with the "
                         "version before it, as with any head.")

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
                 clock: Callable[[], float] = time.monotonic,
                 before_evaluation: Callable[[], None] = lambda: None,
                 hold: Callable[[], Any] | None = None) -> ShadowHarness:
    """A shadow harness on ``workflow``. ``hold`` is its own last check before an adoption is written: it returns a ``Held``
    when what judges is unchanged, and a refusal otherwise. With none, nothing is ever adopted."""
    return ShadowHarness(workflow, factory, Path(workspace).resolve(), clock=clock, problems=workflow.problems(),
                         before_evaluation=before_evaluation, hold=hold)


__all__ = ["ADOPTED", "ADOPTER", "DEMOTED", "Held", "NOT_ADOPTABLE", "NOT_DEMOTABLE", "ROLLED_BACK", "SCOPE", "SHADOW_ONLY",
           "ShadowHarness", "adapt_shadow", "lineage"]
