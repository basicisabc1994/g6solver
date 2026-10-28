# SPDX-License-Identifier: Apache-2.0
"""The meta-harness behind the MCP surface (META_HARNESS_PLAN.md Phase 6; owner rulings Q10, Q16).

MCP calls cannot carry Python (checks, oracles, executors), so workflows are declared once in a Python file the user
owns, named by ``G6_METAHARNESS``: it defines ``WORKFLOWS = {name: {"workflow": Workflow, "factory": ..., "theories":
..., "space": {...}}}``. No tool input ever names a file to run. Every reply is the same envelope the Python API returns
(same fields, same ``G6_E_*`` codes), made JSON: objects appear as their digests and their recorded fields.

Approval is client-mediated consent, including in the proposing session. The name comes from MCP elicitation,
never from the tool's arguments. G6 records it as given, not as authenticated human identity. The question names the
decision and candidate digests and the measured consequences; activation revalidates the recorded decision.
This assumes a trusted client and host. Python callers and a same-user shell are outside that boundary.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
from functools import wraps
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from g6.metaharness.api import MetaHarness, _blocked, adapt
from g6.metaharness.gate import GateDecision
from g6.metaharness.propose import SearchStrategy
from g6.metaharness.schema import APPROVAL_REQUIRED, PROTECTED, SCHEMA

ENV = "G6_METAHARNESS"
NOT_CONFIGURED = "G6_E_META_NOT_CONFIGURED"
#: The run-ledger rows that name the MCP session that proposed a change.
PROPOSED_BY = "proposed_by"


def _lookup_serialized(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        if not self._surface_lock.acquire(blocking=False):
            return _blocked("G6_E_META_BUSY", "another workflow operation is running; poll its job and retry")
        try:
            return method(self, *args, **kwargs)
        finally:
            self._surface_lock.release()
    return invoke


def _surface_operation(method):
    @wraps(method)
    def invoke(self, workflow, *args, **kwargs):
        from g6.workspace._envelope import WorkspaceRefused

        def refused():
            result = _blocked("G6_E_META_BUSY", "another workflow operation is running; poll its job and retry")
            if method.__name__ == "approval_check":
                return result, ""
            if method.__name__ == "rollback_check":
                return result, "", {}
            return result

        if not self._surface_lock.acquire(blocking=False):
            return refused()
        try:
            mh = self.harness(workflow)
            if isinstance(mh, dict):
                # The method preserves its own return shape for missing or invalid workflows.
                return method(self, workflow, *args, **kwargs)
            with mh._operation():
                return method(self, workflow, *args, **kwargs)
        except WorkspaceRefused:
            return refused()
        finally:
            self._surface_lock.release()
    return invoke


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
    """Terminal fallback for a trusted host; names are recorded, never authenticated."""
    return (f'From the same project directory: g6 workflow approve {workflow} {decision_digest} '
            '--candidate <candidate_digest-on-card> --as "Your name". Or use metaharness_approve with '
            'a client that presents the consent prompt, including the proposing session.')


class Surface:
    """One process's meta-harnesses, by workflow name. Who proposed what (by MCP session) is in each run ledger."""

    def __init__(self, workflows: dict[str, dict[str, Any]] | None = None, *, workflow_root: Path | None = None) -> None:
        self._config = workflows
        self._surface_lock = threading.RLock()
        self._harnesses: dict[str, MetaHarness] = {}
        self.workflow_root = Path(workflow_root) if workflow_root is not None else Path.cwd() / ".g6" / "workflows"

    def _load(self) -> dict[str, dict[str, Any]] | None:
        if self._config is None:
            path = os.environ.get(ENV, "")
            if path and Path(path).is_file():
                spec = importlib.util.spec_from_file_location("g6_metaharness_workflows", path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                self._config = dict(getattr(module, "WORKFLOWS", {}))
        from g6.metaharness.declarative import discover
        from g6.core.storage import g6_home
        return {**discover(g6_home() / "workflows"), **discover(self.workflow_root), **(self._config or {})} or None

    @_lookup_serialized
    def harness(self, name: str) -> MetaHarness | dict[str, Any]:
        try:
            config = self._load()
        except (ValueError, OSError) as exc:
            return _blocked(SCHEMA, f"workflow data could not be loaded: {exc}")
        if config is None:
            return _blocked(NOT_CONFIGURED, "no workflows are declared",
                            f"Use metaharness_start, or set {ENV} to a Python file defining WORKFLOWS.")
        if name not in config:
            return _blocked(NOT_CONFIGURED, f"no workflow {name!r}; declared: {sorted(config)}")
        held = self._harnesses.get(name)
        if held is None or held._moved() or held.workflow.digest() != config[name]["workflow"].digest():
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
        # Check before replay (including cached decisions) and before eliciting consent.
        from g6.metaharness.api import judging_sha256

        confirmed_judging = mh.store.confirmed().get("judging_sha256")
        current_judging = judging_sha256()
        if confirmed_judging != current_judging:
            return None, None, _blocked(
                PROTECTED, "the judging code changed since workflow confirmation; approval is blocked",
                "Review the installed G6 changes before choosing whether to reconfirm the workflow. "
                "Reconfirmation does not approve this candidate; obtain a current decision before requesting approval.",
                confirmed_judging_sha256=confirmed_judging, current_judging_sha256=current_judging)
        decision, why = mh._recorded("decision", decision_digest)
        if decision is None:
            return None, None, _blocked(SCHEMA, why, "Pass the decision_digest that metaharness_decide returned.")
        if decision.verdict != "recommend":
            return None, None, _blocked(APPROVAL_REQUIRED, "only a 'recommend' decision can be approved",
                                        "Nothing changes; see the decision's recovery.")
        return mh, decision, None

    # --- the tools ---------------------------------------------------------------------------------------------
    def start(self, specification: dict) -> dict[str, Any]:
        from g6.metaharness.declarative import create
        from g6.metaharness.api import _envelope
        try:
            entry = create(specification, self.workflow_root)
            wf = entry["workflow"]
            return _envelope("qualified-draft", "G6_E_META_CREATED", "Workflow created; client consent is required",
                             workflow=wf.name, workflow_digest=wf.digest(), held_out_cases=len(wf.suite.held_out),
                             mode="explore" if len(wf.suite.held_out) < 30 else "evaluation",
                             next_action="metaharness_confirm with this workflow and digest")
        except (ValueError, OSError) as exc:
            return _blocked(SCHEMA, str(exc))

    @_surface_operation
    def confirm(self, workflow: str, workflow_digest: str, *, answer: str | None) -> dict[str, Any]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        if mh.workflow.digest() != workflow_digest:
            return _blocked(APPROVAL_REQUIRED, "workflow digest does not match; inspect the current workflow",
                            "No consent was applied. Inspect the current digest before confirming.",
                            consent_status="workflow_changed")
        if not answer or not answer.strip():
            return _blocked(APPROVAL_REQUIRED, "client consent was not supplied",
                            f'From the project directory: g6 workflow confirm {workflow} {mh.workflow.digest()} '
                            '--as "Your name" after inspecting that workflow.')
        return as_json(mh.confirm(answer.strip()))

    @_surface_operation
    def propose_code(self, workflow: str, files: dict[str, str], *, hypothesis: str, session: str) -> dict[str, Any]:
        from g6.metaharness.declarative import valid_files
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        if not hypothesis.strip() or not valid_files(files):
            return _blocked(SCHEMA, "supply a hypothesis and flat application Python files")
        got = mh.code_candidate(files, proposer=f"mcp:{session}", claim={"hypothesis": hypothesis})
        if "proposal" in got:
            proposal = got["proposal"]
            key = f"{proposal.digest()}:{session}"
            if not mh.ledger.has(PROPOSED_BY, key):
                mh.ledger.append(PROPOSED_BY, key, {"proposal_digest": proposal.digest(), "session": session})
        return as_json(got)

    @_surface_operation
    def run(self, workflow: str, task_input: str) -> dict[str, Any]:
        mh = self.harness(workflow)
        return mh if isinstance(mh, dict) else as_json(mh.run(task_input))

    @_surface_operation
    def inspect(self, workflow: str) -> dict[str, Any]:
        mh = self.harness(workflow)
        return mh if isinstance(mh, dict) else as_json(mh.inspect())

    @_surface_operation
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

    @_surface_operation
    def evaluate(self, workflow: str, proposal_digest: str) -> dict[str, Any]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        proposal, why = mh._recorded("proposal", proposal_digest)
        if proposal is None:
            return _blocked(SCHEMA, why)
        return as_json(mh.evaluate(proposal))

    @_surface_operation
    def decide(self, workflow: str, report_digest: str, proposal_digest: str) -> dict[str, Any]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        (report, why), (proposal, why_not) = mh._recorded("report", report_digest), mh._recorded("proposal",
                                                                                                 proposal_digest)
        if report is None or proposal is None:
            return _blocked(SCHEMA, why or why_not)
        return as_json(mh.decide(report, proposal))

    @_surface_operation
    def diagnose(self, workflow: str) -> dict[str, Any]:
        """Recovery and root cause: why the active version fails on dev, and what can be done next."""
        mh = self.harness(workflow)
        return mh if isinstance(mh, dict) else as_json(mh.diagnose())

    @_surface_operation
    def approval_check(self, workflow: str, decision_digest: str, *, session: str) -> tuple[dict[str, Any] | None, str]:
        """Before anyone is asked: the refusal (no such decision or not ``recommend``), or
        None and the question to put to the person, which shows the change and its held-out result (review B2-I1)."""
        mh, decision, refused = self._approvable(workflow, decision_digest, session)
        if refused is not None:
            return refused, ""
        report, _ = mh._recorded("report", decision.report_digest)
        proposal, _ = mh._recorded("proposal", decision.proposal_digest)
        card = {**mh._card(decision, report), "candidate_digest": decision.candidate_digest,
                "diff": dict(proposal.diff)}
        return None, f"Decision {decision_digest}. " + approval_question(decision.to_dict(), card)

    @_surface_operation
    def approve(self, workflow: str, decision_digest: str, *, session: str, answer: str | None) -> dict[str, Any]:
        """``answer``: the name the person typed when the client asked them (None: they declined, or the client cannot
        ask). Identity is recorded as given; a trusted client must mediate this consent."""
        mh, decision, refused = self._approvable(workflow, decision_digest, session)
        if refused is not None:
            return refused
        if not answer or not answer.strip():
            return _blocked(APPROVAL_REQUIRED, "no person approved: the client did not ask you, or you declined",
                            how_to_approve(workflow, mh, decision_digest))
        consent = {"method": "mcp-elicitation", "identity_authenticated": False,
                       "decision_digest": decision_digest, "candidate_digest": decision.candidate_digest,
                       "proposal_digest": decision.proposal_digest, "session": session,
                       "accepted_by": answer.strip()}
        # Receipt of consent is recorded before activation: it is not itself an activation record.
        from g6.core.ledger import digest
        consent_id = digest(consent)
        if not mh.ledger.has("client_consent", consent_id):
            mh.ledger.append("client_consent", consent_id, consent)
        result = as_json(mh.activate(decision_digest, accepted_by=answer.strip(),
                                     candidate_digest=decision.candidate_digest))
        if result.get("warning_card", {}).get("code") == "G6_E_META_ACTIVATED":
            result["consent"] = consent
        return result

    @_surface_operation
    def rollback_check(self, workflow: str, version: int) -> tuple[dict | None, str, dict]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh, "", {}
        refused = mh._refusal(allow_stopped=True)
        if refused:
            return refused, "", {}
        versions = mh.store.versions()
        if not 0 <= version < len(versions) or versions[version]["kind"] not in ("initial", "activate"):
            return _blocked(APPROVAL_REQUIRED, "target is not an approved version"), "", {}
        binding = {"workflow_digest": mh.workflow.digest(), "store_head": mh.store.head_hash(),
                   "profile_digest": versions[version]["profile_digest"], "version": version}
        question = (f"Roll back workflow {workflow} ({binding['workflow_digest']}) to approved version {version}, "
                    f"profile {binding['profile_digest']}? Future runs use that profile; prior external actions are "
                    "not undone. Type your name; identity is recorded as given.")
        return None, question, binding

    @_surface_operation
    def rollback(self, workflow: str, version: int, *, answer: str | None, binding: dict | None = None) -> dict[str, Any]:
        mh = self.harness(workflow)
        if isinstance(mh, dict):
            return mh
        if not answer or not answer.strip():
            return _blocked(APPROVAL_REQUIRED, "no person asked for the rollback (the client did not ask you)")
        if binding is not None:
            refused, _, current = self.rollback_check(workflow, version)
            if refused is not None or current != binding:
                return refused or _blocked(APPROVAL_REQUIRED, "workflow changed after the rollback prompt")
        return as_json(mh.rollback(version, accepted_by=answer.strip()))


def approval_question(decision: dict[str, Any], card: dict[str, Any] | None = None) -> str:
    shown = ""
    if card:   # which change, and what it did on the held-out split, so the person knows what they approve
        shown = (f" The change (candidate {card.get('candidate_digest')}): {card.get('diff')}. On "
                 f"{card.get('held_out_cases')} held-out cases: {card.get('success')}.")
    return ("G6 asks a person to approve a change to how it runs. Recorded evidence: "
            f"{decision.get('reasons')}.{shown} Type your name to approve it, or decline.")
