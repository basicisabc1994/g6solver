# SPDX-License-Identifier: Apache-2.0
"""The library facade for a local coding mission: bind, create, confirm, submit a patch, read status and certificate.

Everything runs on this machine under ``$G6_HOME/workforce`` (or ``$G6_WORKFORCE_DB_DIR``, or ``bind(db_dir=)``): no
network, no hosted service, no billing. A mission's repository is a git repository named under ``repository_dir(wf)``.

The flow::

    wf = bind()                                         # the local stores, one venture per owner
    allow_execution(wf, "sample", approver)             # a person allows G6 to run the repository's code
    found = create_coding_mission(wf, objective, repo="sample", module="policy.py", function="label")
    confirm(wf, found["mission_id"], approver)          # a person confirms the frozen standard
    submit_patch(wf, found["mission_id"], {"policy.py": "...full new text..."})
    status(wf, found["mission_id"])                     # verified / qualified-draft / blocked-escalated
    certificate(wf, found["mission_id"])                # what the evidence supports, predicate by predicate

``approver`` is any callable that is shown what it decides (a list of plain lines) and returns who decides (a
non-empty string), or ``None`` to refuse. G6 records the answer in the person's place under that identity, with its
principal (``decisions.principal``: channel ``approver``, not authenticated, in the caller's own process): it cannot
prove a person gave it (WF-013), so an approver that answers automatically is the caller's own claim, recorded as one.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from g6.workforce import acceptance, coding, objective, service
from g6.workforce.bench import ENV_DB_DIR, REPOS, workspace_root
from g6.workforce.decisions import principal
from g6.workforce.store import VentureStore

#: What confirms a standard: shown its lines, returns the confirmer's identity, or None to refuse.
Approver = Callable[[Sequence[str]], str | None]
DEFAULT_VENTURE = "local"
RISKS = ("R0", "R1", "R2", "R3")


class NotConfirmed(ValueError):
    """The approver refused (or gave no identity): nothing was recorded."""


def bind(*, venture: str = DEFAULT_VENTURE, owner: str = service.LOCAL_OWNER, db_dir: str | os.PathLike | None = None,
         invoke: Any = None) -> service.Workforce:
    """Open (creating when absent) the local stores and the owner's venture named ``venture``.

    ``db_dir`` is this workforce's root when given (else ``$G6_WORKFORCE_DB_DIR`` or ``$G6_HOME/workforce``): its
    stores live there and the bench resolves its missions' repositories under it. The process environment is not
    changed, so another binding keeps its own root. ``invoke`` is for tests (a scripted world)."""
    root = Path(db_dir).expanduser() if db_dir is not None else workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    store = VentureStore(root / "venture.db")
    scope = store.for_owner(owner)
    found = next((v for v in scope.list_ventures() if v["name"] == venture), None)
    if found is None:
        found = scope.create_venture(venture, budget_aud_month=0, model_cap_usd_month=0, task_cap_usd=0, max_turns=0)
    env = {**os.environ, ENV_DB_DIR: str(root), service.ENV_VENTURE: found["venture_id"], service.ENV_OWNER: owner}
    return service.bind(env, invoke=invoke)


def repository_dir(wf: service.Workforce) -> Path:
    """Where this venture's repositories live: a mission's ``repo`` is the name of a git repository here."""
    return Path(wf.db_dir) / REPOS / wf.venture_id


def allow_execution(wf: service.Workforce, repo: str, approver: Approver, *, reason: str = "allowed") -> dict:
    """Show ``approver`` what running repository ``repo``'s code means, at its present revision and content, and record
    its permission (``NotConfirmed`` when it refuses). ``create_coding_mission`` runs nothing of a repository until a
    permission for exactly the revision and content it would run is recorded (audit 010 WF-007); a new commit needs a
    new one. Returns the repository, revision and content digest allowed."""
    terms = coding.execution_terms(wf, repo)
    started = datetime.now(UTC)
    who = approver(list(terms["lines"]))
    ended = datetime.now(UTC)
    if not isinstance(who, str) or not who.strip():
        raise NotConfirmed("the approver did not allow G6 to run this repository's code; nothing was recorded")
    identity = f"approver:{who.strip()}"
    coding.allow_execution(wf, terms, reason=reason, decided_by=identity,
                           principal=principal(identity, channel="approver"), timing=(started, ended))
    return {key: terms[key] for key in ("repository", "revision", "digest")}


def create_coding_mission(wf: service.Workforce, objective_text: str, repo: str, *, module: str | None = None,
                          function: str | None = None, helpers: Sequence[str] | None = None, risk: str = "R2",
                          budget_cents: int = 0) -> dict:
    """Freeze the coding standard for ``objective_text`` on repository ``repo`` (a clean git repository under
    ``repository_dir``): the baseline snapshot, the required tests (the repository's own, plus any replay check the
    premortem constructs), the permitted file, the risk tier (at least R2), the budget and the attempt limit. The
    repository's test suite runs here, in a scratch copy, before the standard can be confirmed, so it runs only after a
    person allowed it for this revision (``allow_execution``; WF-007: not an OS sandbox); refused otherwise, with
    nothing run. ``module``/``function`` name the scope; without them G6 derives it from the failing tests."""
    if risk not in RISKS:
        raise ValueError(f"risk is one of {list(RISKS)}")
    repository: dict[str, Any] = {"repo": repo}
    if module:
        repository["module"] = module
    if function:
        repository["function"] = function
    if helpers:
        repository["helpers"] = list(helpers)
    return service.trim(objective.compile(wf, objective=objective_text, repository=repository, declared_risk=risk,
                                          budget_cents=budget_cents))


def answer_clarification(wf: service.Workforce, mission_id: str, replay_semantics: str) -> dict:
    """Answer the one clarification a replay objective asks before its standard is frozen."""
    return service.trim(service.accept(wf, mission_id, {"replay_semantics": replay_semantics}))


def confirm(wf: service.Workforce, mission_id: str, approver: Approver, *, reason: str = "confirmed") -> dict:
    """Show the frozen standard to ``approver``; record its confirmation and start the checks (``NotConfirmed`` when
    it refuses). The answer is recorded in a person's place, under the identity the approver returns and with its
    principal: the caller's own, unauthenticated claim (see module doc)."""
    lines = acceptance.describe(wf.missions.acceptance(mission_id))
    started = datetime.now(UTC)
    who = approver(list(lines))
    ended = datetime.now(UTC)
    if not isinstance(who, str) or not who.strip():
        raise NotConfirmed("the approver did not confirm what counts as done; nothing was recorded")
    identity = f"approver:{who.strip()}"
    wf.runtime.confirm_acceptance(mission_id, reason=reason, actor="human", decided_by=identity,
                                  timing=(started, ended), principal=principal(identity, channel="approver"))
    if coding.frozen(wf, mission_id):
        coding.advance(wf, mission_id)
    return service.summary(wf, mission_id)


def submit_patch(wf: service.Workforce, mission_id: str, files: dict[str, str], *, review: dict | None = None) -> dict:
    """Submit the full new text of the permitted source files; G6 checks it under the frozen standard at once.
    ``review``: what a patch review found on this same text (``coding.record_review``). One it holds back uses an
    attempt, and the mission's checks never judge it."""
    artifact = {"files": dict(files)}
    if review is not None:
        coding.record_review(wf.missions, mission_id, artifact, review)
    return service.trim(service.submit(wf, mission_id, "patch", artifact))


def decide(wf: service.Workforce, mission_id: str, unit: str, decision: str, approver: Approver, *,
           reason: str = "") -> dict:
    """A person's answer to a gate (an R3 mission's ``approve-patch``) or a disagreement between checks:
    ``approve``, ``reject``, ``uphold`` or ``set_aside``, under the identity ``approver`` returns and with its
    principal (the caller's own, unauthenticated claim)."""
    asked = next((q for q in wf.runtime.decisions.pending(mission_id) if q["unit"] == unit), None)
    who = approver([str((asked or {}).get("question") or unit)])
    if not isinstance(who, str) or not who.strip():
        raise NotConfirmed("the approver gave no decision; nothing was recorded")
    identity = f"approver:{who.strip()}"
    wf.runtime.decide(mission_id, unit, decision, reason=reason or decision, actor="human", decided_by=identity,
                      principal=principal(identity, channel="approver"))
    if coding.frozen(wf, mission_id):
        coding.advance(wf, mission_id)
    return service.summary(wf, mission_id)


def status(wf: service.Workforce, mission_id: str, detail: str = "summary") -> dict:
    """The mission: ``completion_state`` (verified / qualified-draft / blocked-escalated, once closed), the result,
    what it waits for; ``detail="full"`` adds every step, check, receipt and the harness pack."""
    if detail not in ("summary", "full"):
        raise ValueError('detail is "summary" or "full"')
    found = service.status(wf, mission_id, stages=detail == "full")
    return found if detail == "full" else service.trim(found)


def certificate(wf: service.Workforce, mission_id: str) -> dict:
    """The certificate the mission's close kept; before the close, one issued now on the latest patch
    (``CertificateRefused`` when there is no frozen standard or no patch)."""
    from g6.workforce import coding_certificate

    kept = next((e["detail"]["certificate"] for e in wf.missions.events(mission_id, kind="mission.closed")
                 if "certificate" in e["detail"]), None)
    if kept is not None:
        return kept
    return coding_certificate.certify(wf.missions, wf.ledger, mission_id).as_dict()


__all__ = ["DEFAULT_VENTURE", "Approver", "NotConfirmed", "allow_execution", "answer_clarification", "bind",
           "certificate", "confirm", "create_coding_mission", "decide", "repository_dir", "status", "submit_patch"]
