# SPDX-License-Identifier: Apache-2.0
"""The meta-harness behind the MCP surface (META_HARNESS_PLAN.md Phase 6; owner rulings Q10, Q16).

MCP calls cannot carry Python (checks, oracles, executors), so workflows are declared once in a Python file the user
owns, named by ``G6_METAHARNESS``: it defines ``WORKFLOWS = {name: {"workflow": Workflow, "factory": ..., "theories":
..., "space": {...}}}``. No tool input ever names a file to run. Every reply is the same envelope the Python API returns
(same fields, same ``G6_E_*`` codes), made JSON: objects appear as their digests and their recorded fields.

Approval is the stronger channel of plan 5.4: ``approve`` refuses a session that proposed the change, and the person's
name comes from the client asking them directly (MCP elicitation), never from the agent's arguments. Both are checked
before anyone is asked (``approval_check``). Proposals, reports, decisions and the proposing session are recorded in the
workspace's run ledger, so a decision outlives this process: the stdio server's one session is the proposer's, and a
person approves from Python in another process (``MetaHarness.activate`` by the decision's digest) or from a new MCP
session whose client can ask them. A refusal says exactly how.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from g6.metaharness.api import MetaHarness, _blocked, adapt
from g6.metaharness.gate import GateDecision
from g6.metaharness.propose import SearchStrategy
from g6.metaharness.schema import APPROVAL_REQUIRED, APPROVER_IS_PROPOSER, SCHEMA

ENV = "G6_METAHARNESS"
NOT_CONFIGURED = "G6_E_META_NOT_CONFIGURED"
#: The run-ledger rows that name the MCP session that proposed a change.
PROPOSED_BY = "proposed_by"


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _json(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value if isinstance(value, (str, int, float, bool)) or value is None else repr(value)


def as_json(envelope: dict[str, Any]) -> dict[str, Any]:
    """The envelope with every object replaced by its JSON form (proposals, reports and decisions by digest too)."""
    out = {}
    for key, value in envelope.items():
        if key in ("proposal", "report", "decision") and hasattr(value, "digest"):
            out[f"{key}_digest"] = value.digest()
        if key == "proposals":
            out["proposal_digests"] = [p.digest() for p in value]
        out[key] = _json(value)
    return out


def how_to_approve(workflow: str, mh: MetaHarness, decision_digest: str) -> str:
    """The exact ways a person approves a decision, never from the session that proposed it."""
    return (f"A person approves it from Python, in a process of their own: adapt(WORKFLOWS[{workflow!r}]['workflow'], "
            f"workspace=r'{mh.workspace}').activate({decision_digest!r}, accepted_by='<their name>'), with WORKFLOWS "
            f"from the file {ENV} names, which shows them the decision's card; then the same call with "
            "candidate_digest='<the candidate_digest on the card>'. Or with metaharness_approve from an MCP session "
            "other than the proposer's, whose client asks them.")


class Surface:
    """One process's meta-harnesses, by workflow name. Who proposed what (by MCP session) is in each run ledger."""

    def __init__(self, workflows: dict[str, dict[str, Any]] | None = None) -> None:
        self._config = workflows
        self._harnesses: dict[str, MetaHarness] = {}

    def _load(self) -> dict[str, dict[str, Any]] | None:
        if self._config is None:
            path = os.environ.get(ENV, "")
            if not path or not Path(path).is_file():
                return None
            spec = importlib.util.spec_from_file_location("g6_metaharness_workflows", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            self._config = dict(getattr(module, "WORKFLOWS", {}))
        return self._config

    def harness(self, name: str) -> MetaHarness | dict[str, Any]:
        config = self._load()
        if config is None:
            return _blocked(NOT_CONFIGURED, f"no workflows are declared: set {ENV} to a Python file defining WORKFLOWS",
                            "Declare your workflows in a file you own, then restart the MCP server.")
        if name not in config:
            return _blocked(NOT_CONFIGURED, f"no workflow {name!r}; declared: {sorted(config)}")
        held = self._harnesses.get(name)
        if held is None or held._moved():   # another process (a person approving from Python) wrote the workspace
            entry = config[name]
            extra = {"clock": entry["clock"]} if "clock" in entry else {}
            self._harnesses[name] = adapt(entry["workflow"], entry.get("factory"), workspace=entry.get("workspace"),
                                          theories=entry.get("theories"), **extra)
        return self._harnesses[name]

    @staticmethod
    def _proposers(mh: MetaHarness, proposal_digest: str) -> set[str]:
        return {r["session"] for r in mh.ledger.rows(PROPOSED_BY) if r.get("proposal_digest") == proposal_digest}

    def _approvable(self, workflow: str, decision_digest: str,
                    session: str) -> tuple[MetaHarness | None, GateDecision | None, dict[str, Any] | None]:
        """The harness and the decision this session may ask a person to approve, or the refusal."""
        mh = self.harness(workflow)
        if isinstance(mh, dict) or mh._problems:
            return None, None, mh if isinstance(mh, dict) else mh.envelope
        refused = mh._refusal()   # stopped, unconfirmed or changed: say so before deciding anything again
        if refused:
            return None, None, refused
        decision, why = mh._recorded("decision", decision_digest)
        if decision is None:
            return None, None, _blocked(SCHEMA, why, "Pass the decision_digest that metaharness_decide returned.")
        if decision.verdict != "recommend":
            return None, None, _blocked(APPROVAL_REQUIRED, "only a 'recommend' decision can be approved",
                                        "Nothing changes; see the decision's recovery.")
        if session in self._proposers(mh, decision.proposal_digest):
            return None, None, _blocked(APPROVER_IS_PROPOSER, "the session that proposed this change cannot approve it",
                                        how_to_approve(workflow, mh, decision_digest))
        return mh, decision, None

    # --- the tools ---------------------------------------------------------------------------------------------
    def inspect(self, workflow: str) -> dict[str, Any]:
        mh = self.harness(workflow)
        return mh if isinstance(mh, dict) else as_json(mh.inspect())

    def propose(self, workflow: str, *, session: str, profile: dict[str, Any] | None = None,
                proposer: str = "") -> dict[str, Any]:
        """A profile the agent wrote, or (with no profile) the declared search space's proposals."""
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        if profile is not None:
            got = mh.candidate(profile, proposer=proposer or f"mcp:{session}", strategy="mcp")
            made = [got["proposal"]] if "proposal" in got else []
        else:
            entry = (self._load() or {}).get(workflow, {})
            space, strategy = entry.get("space"), entry.get("strategy")
            if strategy is None and not space:
                return _blocked(SCHEMA, "no search space or strategy is declared for this workflow: pass a profile")
            got = mh.propose(strategy if strategy is not None else SearchStrategy(space))
            made = list(got.get("proposals", []))
        for proposal in made:
            key = f"{proposal.digest()}:{session}"
            if not mh.ledger.has(PROPOSED_BY, key):
                mh.ledger.append(PROPOSED_BY, key, {"proposal_digest": proposal.digest(), "session": session})
        return as_json(got)

    def evaluate(self, workflow: str, proposal_digest: str) -> dict[str, Any]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        proposal, why = mh._recorded("proposal", proposal_digest)
        if proposal is None:
            return _blocked(SCHEMA, why)
        return as_json(mh.evaluate(proposal))

    def decide(self, workflow: str, report_digest: str, proposal_digest: str) -> dict[str, Any]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        (report, why), (proposal, why_not) = mh._recorded("report", report_digest), mh._recorded("proposal",
                                                                                                 proposal_digest)
        if report is None or proposal is None:
            return _blocked(SCHEMA, why or why_not)
        return as_json(mh.decide(report, proposal))

    def diagnose(self, workflow: str) -> dict[str, Any]:
        """Recovery and root cause: why the active version fails on dev, and what can be done next."""
        mh = self.harness(workflow)
        return mh if isinstance(mh, dict) else as_json(mh.diagnose())

    def approval_check(self, workflow: str, decision_digest: str, *, session: str) -> tuple[dict[str, Any] | None, str]:
        """Before anyone is asked: the refusal (no such decision, not ``recommend``, or this session proposed it), or
        None and the question to put to the person, which shows the change and its held-out result (review B2-I1)."""
        mh, decision, refused = self._approvable(workflow, decision_digest, session)
        if refused is not None:
            return refused, ""
        report, _ = mh._recorded("report", decision.report_digest)
        proposal, _ = mh._recorded("proposal", decision.proposal_digest)
        card = {**mh._card(decision, report), "candidate_digest": decision.candidate_digest,
                "diff": dict(proposal.diff)}
        return None, approval_question(decision.to_dict(), card)

    def approve(self, workflow: str, decision_digest: str, *, session: str, answer: str | None) -> dict[str, Any]:
        """``answer``: the name the person typed when the client asked them (None: they declined, or the client cannot
        ask). The session that proposed the change cannot approve it."""
        mh, decision, refused = self._approvable(workflow, decision_digest, session)
        if refused is not None:
            return refused
        if not answer or not answer.strip():
            return _blocked(APPROVAL_REQUIRED, "no person approved: the client did not ask you, or you declined",
                            how_to_approve(workflow, mh, decision_digest))
        return as_json(mh.activate(decision, accepted_by=answer.strip()))

    def rollback(self, workflow: str, version: int, *, answer: str | None) -> dict[str, Any]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        if not answer or not answer.strip():
            return _blocked(APPROVAL_REQUIRED, "no person asked for the rollback (the client did not ask you)")
        return as_json(mh.rollback(version, accepted_by=answer.strip()))


def approval_question(decision: dict[str, Any], card: dict[str, Any] | None = None) -> str:
    shown = ""
    if card:   # which change, and what it did on the held-out split, so the person knows what they approve
        shown = (f" The change (candidate {card.get('candidate_digest')}): {card.get('diff')}. On "
                 f"{card.get('held_out_cases')} held-out cases: {card.get('success')}.")
    return ("G6 asks a person to approve a change to how it runs. Recorded evidence: "
            f"{decision.get('reasons')}.{shown} Type your name to approve it, or decline.")
