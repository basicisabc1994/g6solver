# SPDX-License-Identifier: Apache-2.0
"""The builder flow for a new use case: AD P0-P4 as a guided sequence (META_HARNESS_PLAN.md Phase 4).

* P0 objectives: the six questions of AD A2.3, in their order, beside ``g6.intent_engine``'s own questions about the
  goal. Every objective the builder infers is ``provenance="inferred"``, ``confirmed=False`` until a person confirms it,
  and a weight can be put only on a confirmed objective.
* P1 capabilities: each action the executor can take is classified by ``g6.action_gating`` from its name; ``unknown``
  is treated as irreversible, and every effect other than idempotent waits for a person to confirm it with a loss scale.
  The builder never answers the irreversibility question itself (AD A2.3, Proposition A.5).
* P2-P3 suite: a model may draft dev cases only. Held-out cases come from a source other than the builder: a person
  other than the builder, a third-party dataset or historical outcomes. Anything else is labelled as what it is
  (``caller`` or ``model-drafted``), and every report and recommendation says so. A held-out split read from a file is
  copied into the workspace and sealed in a ``g6.core.vault.Vault``: a change after sealing voids every evaluation.
* P4 baseline: ``adapt`` + ``confirm`` + ``baseline`` on the workflow the builder returns.
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from g6.action_gating.classifiers import classify_reversibility
from g6.action_gating.normalizer import normalize_action_capsule
from g6.core.adaptation_gate import is_automated
from g6.core.vault import Vault, guarded_read
from g6.harness import Check
from g6.intent_engine import IntentElicitor
from g6.metaharness.schema import (
    Capability,
    EvidencePolicy,
    Model,
    Objective,
    Suite,
    SuiteCase,
    Workflow,
    default_objectives,
)

#: AD A2.3, in its order: what each answer fills.
QUESTIONS: tuple[tuple[str, str], ...] = (
    ("outcome", "What does a good outcome of one run look like, in terms you already measure?"),
    ("stop", "What is the one number that, if it went wrong, you would stop the system?"),
    ("irreversible", "Which actions, once taken, cannot be undone? What does one such mistake cost?"),
    ("good_enough", "What level of each measure would you call good enough?"),
    ("spend", "How much are you willing to spend per run, in money and in time?"),
    ("preference", "If two runs differ only in A and B, which would you prefer? (asked at most three times, on "
                   "observed pairs)"),
)
INDEPENDENT_SOURCES = ("person", "third-party", "historical")


@dataclass
class Builder:
    name: str
    task: str
    builder: str                                # who is building the workflow (a person or an agent)
    answers: dict[str, Any] = field(default_factory=dict)
    objectives: dict[str, Objective] = field(default_factory=lambda: {o.name: o for o in default_objectives()})
    capabilities: dict[str, Capability] = field(default_factory=dict)
    dev: tuple[SuiteCase, ...] = ()
    held_out: tuple[SuiteCase, ...] = ()
    provenance: dict[str, str] = field(default_factory=lambda: {"dev": "caller", "held_out": "caller"})
    held_out_vault: Vault | None = None
    preferences_asked: int = 0
    log: list[str] = field(default_factory=list)

    # --- P0 ---------------------------------------------------------------------------------------------------------
    def next_question(self) -> tuple[str, str] | None:
        """The first unanswered question, in AD's order. The preference question waits for observed pairs."""
        for key, text in QUESTIONS:
            if key == "preference":
                continue
            if key not in self.answers:
                return key, text
        return None

    def intent_questions(self) -> list[tuple[str, str]]:
        """What ``g6.intent_engine`` still asks about the goal itself (a completeness checklist)."""
        session = IntentElicitor().elicit(self.task, answers={k: v for k, v in self.answers.items()
                                                             if k in ("why", "success_criteria", "failure_criteria",
                                                                      "non_goals", "risk_tolerance")})
        return [(q.field, q.question) for q in session.questions]

    def answer(self, key: str, value: Any) -> None:
        self.answers[key] = value
        self.log.append(f"answered {key}")

    def infer(self, name: str, direction: str, *, bound: float | None = None, tolerance: float = 0.05,
              reference: float | None = None) -> Objective:
        """An objective the builder inferred from an answer: unconfirmed until a person confirms it."""
        objective = Objective(name, direction, tolerance=tolerance, bound=bound, reference=reference,
                              provenance="inferred", confirmed=False)
        self.objectives[name] = objective
        return objective

    def confirm_objective(self, name: str, accepted_by: str) -> Objective:
        _person(accepted_by)
        self.objectives[name] = replace(self.objectives[name], confirmed=True,
                                        provenance=self.objectives[name].provenance)
        self.log.append(f"objective {name} confirmed by {accepted_by}")
        return self.objectives[name]

    def weight(self, name: str, value: float) -> Objective:
        """A weight ranks proposals for a person; never on an objective nobody confirmed (AD gate P0)."""
        objective = self.objectives[name]
        if not objective.confirmed:
            raise ValueError(f"G6_E_META_WORKFLOW_INVALID: {name!r} is not confirmed; no weight on it")
        self.objectives[name] = replace(objective, weight=float(value))
        return self.objectives[name]

    def ask_preference(self) -> str | None:
        if self.preferences_asked >= 3:
            return None
        self.preferences_asked += 1
        return QUESTIONS[-1][1]

    # --- P1 ---------------------------------------------------------------------------------------------------------
    def capability(self, name: str, *, description: str = "") -> Capability:
        """Classified by ``g6.action_gating`` from its name; ``unknown`` is treated as irreversible."""
        found = str(classify_reversibility(normalize_action_capsule({"action_type": name})))
        # the classifier never says idempotent: only a person can (confirm_capability), so every action waits for one
        effect = {"irreversible": "irreversible", "unknown": "unknown"}.get(found, "reversible")
        cap = Capability(name, effect, description=description)
        self.capabilities[name] = cap
        return cap

    def confirm_capability(self, name: str, accepted_by: str, *, effect: str | None = None,
                           loss_scale: float | None = None) -> Capability:
        """A person states the effect (it may only be made stricter than the classifier's, or kept) and its loss."""
        _person(accepted_by)
        cap = self.capabilities[name]
        order = ("idempotent", "reversible", "irreversible", "unknown")
        chosen = effect or cap.effect
        if order.index(chosen) < order.index(cap.effect) and cap.effect != "unknown":
            raise ValueError(f"capability {name!r}: a person may not declare it safer than its classification")
        self.capabilities[name] = replace(cap, effect=chosen, loss_scale=loss_scale, confirmed_by=accepted_by)
        return self.capabilities[name]

    # --- P2-P3 ------------------------------------------------------------------------------------------------------
    def set_dev(self, cases: Sequence[SuiteCase], *, source: str = "caller") -> None:
        self.dev = tuple(cases)
        self.provenance["dev"] = "model-drafted" if source == "model" else "caller"

    def draft_dev(self, llm: Any, n: int, *, example: str = "") -> tuple[SuiteCase, ...]:
        """A model drafts dev cases only (input and expected answer as JSON lines); held-out cases it may not draft."""
        prompt = (f"Write {n} example tasks for: {self.task}\nOne JSON object per line with keys input and expected."
                  + (f"\nExample: {example}" if example else ""))
        cases = []
        for line in (llm.complete("Draft development examples. JSON lines only.", prompt) or "").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("input"), str) and "expected" in row:
                cases.append(SuiteCase(f"draft{len(cases)}", row["input"], row["expected"]))
        self.set_dev(cases[:n], source="model")
        return self.dev

    def set_held_out(self, cases: Sequence[SuiteCase], *, source: str, author: str) -> str:
        """``source``: ``person`` (someone other than the builder), ``third-party`` (a public dataset),
        ``historical`` (recorded outcomes), ``caller`` or ``model``. Returns the provenance recorded."""
        self.held_out = tuple(cases)
        independent = source in INDEPENDENT_SOURCES and author.strip().lower() != self.builder.strip().lower()
        self.provenance["held_out"] = ("independent" if independent else "model-drafted" if source == "model"
                                       else "caller")
        self.log.append(f"held-out from {source} by {author}: {self.provenance['held_out']}")
        return self.provenance["held_out"]

    def held_out_file(self, path: str | Path, *, source: str, author: str, workspace: str | Path) -> str:
        """Read a held-out split from a JSON-lines file (``case_id``, ``input``, ``expected``), copied into the
        workspace and sealed; it is read back only through the vault, as the evaluator."""
        sealed_dir = Path(workspace) / "held_out"
        sealed_dir.mkdir(parents=True, exist_ok=True)
        target = sealed_dir / Path(path).name
        shutil.copyfile(path, target)
        self.held_out_vault = Vault.seal([sealed_dir], ledger=None, vault_id=f"held-out-{self.name}")
        rows = guarded_read(self.held_out_vault, "evaluator", target).decode("utf-8").splitlines()
        cases = [SuiteCase(str(r["case_id"]), str(r["input"]), r["expected"]) for r in map(json.loads, rows) if r]
        return self.set_held_out(cases, source=source, author=author)

    # --- the workflow -------------------------------------------------------------------------------------------------
    def build(self, *, checks: Sequence[Check], oracle: Callable[[str, Any], bool], models: Mapping[str, Model],
              policy: EvidencePolicy | None = None, **kw: Any) -> Workflow:
        """The workflow: still to be confirmed by a person through ``MetaHarness.confirm``. Its ``problems()`` name
        every unconfirmed objective and capability, which keep it from being confirmed."""
        return Workflow(name=self.name, task=self.task, checks=tuple(checks), oracle=oracle,
                        suite=Suite(self.dev, self.held_out, dict(self.provenance)), models=dict(models),
                        objectives=tuple(self.objectives.values()), policy=policy or EvidencePolicy(),
                        capabilities=tuple(self.capabilities.values()), held_out_vault=self.held_out_vault, **kw)


def _person(name: str) -> None:
    if not name.strip() or is_automated(name):
        raise ValueError("G6_E_META_APPROVAL_REQUIRED: a person confirms this (no empty or auto: name)")
