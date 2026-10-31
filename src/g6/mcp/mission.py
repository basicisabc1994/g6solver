# SPDX-License-Identifier: Apache-2.0
"""The coding mission over MCP: what the ``g6_mission_*`` tools and ``g6_premortem`` do, over ``g6.workforce.api``.

A mission's repository is a name under ``api.repository_dir``; a person's is a folder anywhere, so ``register`` takes
its commit there. What the person allows and confirms goes through the client's own dialog (``g6.mcp.consent``), and
nowhere else: no tool takes an answer, a reply never offers the terminal command for it, and a decline, a cancel or a
client without a dialog leaves things as they were. ``g6.workforce.api`` calls its approver in the middle of its work
and a dialog cannot wait there, so the dialog comes first and ``_bound`` answers only for the lines the person read.

The check step (``submit``) is ``review_patch`` on the agent's working tree, with the frozen tests as its stated checks:
it sees the whole change, and a ``files`` map would not. The text then goes to ``api.submit_patch`` with what the review
found. A reproduced defect or a sign the tests cannot be believed holds it back: it uses an attempt on the mission's own
repair ladder, the mission's checks never judge it, and so the mission cannot close on it. Any other outcome goes on to
the mission's bench, whose verdicts the ladder, the receipts and the certificate read, and whose frozen copy is the one
thing the agent's tree cannot reach. So nothing here is a second bench or a second ladder, and a patch that gets that
far runs the frozen tests twice. ``insufficient_evidence`` blocks nothing, and the mission says it plainly.

Every reply carries the library envelope. A mission's own ``verified`` is held at this door as at any other.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from g6.core import git_env
from g6.core.door import (
    SENSITIVE_ENV,
    allowed_sensitive,
    blocked_reply,
    card,
    corrected_result,
    door_envelope,
    is_allowed,
    said_state,
)
from g6.core.ledger import digest
from g6.harness import verification_search
from g6.harness.patch_review import DEFECT, INTEGRITY
from g6.intent_engine import IntentElicitor
from g6.mcp import review_reply
from g6.reliability_pipeline import premortem as checklist
from g6.workforce import acceptance, api, bench, coding, repo_tests

#: What ``G6_MCP_SENSITIVE`` lists for the tools that run a repository's code: ``workforce.mission``, or ``workforce``.
SENSITIVE = ("workforce", "mission")
WHO = "workforce.mission"
REFUSED = "G6_E_MISSION_REFUSED"
#: The review's outcomes that hold a patch back: the mission must not close on either.
HELD = (DEFECT, INTEGRITY)
#: Said in every dialog: the name is a claim, and what declining does.
NAME = ("Type your name to agree; it is recorded as you give it and is not authenticated. To refuse, decline: nothing "
        "changes.")
# COPY: What a person allows besides the library's terms: the check step runs in their own folder, not in a copy.
IN_PLACE = ("G6 also reviews each patch in that folder itself: those runs are confined by the operating system, read "
            "the folder and cannot write to it, and are refused where no boundary can confine them.")
# COPY: Refused before anything is asked: a standard is frozen from a commit, before any change exists.
DIRTY = ("G6 froze nothing: the repository has changes that are not committed ({files}). A standard is frozen from a "
         "commit, before any change: commit or set them aside, then create the mission again.")
NOT_ASKED = ("Nothing was recorded. The person decided in the client's dialog, or no dialog could be shown: ask the "
             "person whether they want this before calling the tool again, and offer no other way to agree.")
UNCONFIRMED = ("[G6_E_HUMAN_APPROVAL_REQUIRED] No person has confirmed what counts as done for this mission, so it takes "
               "no patch. Nothing ran and nothing was recorded. Call g6_mission_confirm: the person answers in the "
               "client's dialog, and there is no other way.")
NOT_WAITING = "This mission is not waiting for a patch: read g6_mission_status."
NOTHING_ASKED = ("This mission has nothing to confirm: it is confirmed, closed, or still has a question to answer. "
                 "Read g6_mission_status.")
STANDARD_CHANGED = "What counts as done changed after the person read it, so it was not confirmed. Ask them again."
LINES_CHANGED = "What the person read is not what G6 would record, so nothing was recorded. Ask them again."
OUT_OF_SCOPE = ("[G6_E_MISSION_OUT_OF_SCOPE] The working tree changes files the frozen standard does not permit: {files}. "
                "Only {allowed} may change. A frozen test stays as it is: nothing ran and no attempt was used.")
NO_CHANGE = ("[G6_E_MISSION_NO_CHANGE] The working tree changes nothing against the frozen commit, so nothing ran and "
             "no attempt was used.")
DRAFT =f"{WHO}: usable with caution; g6_mission_certificate says what the evidence supports"
# COPY: A brief's instructions on this surface, in place of ``coding.OBJECTIVE_INSTRUCTIONS``: the patch is the tree.
SUBMIT_HERE = ("Change only the source files in scope in the working tree, do not change the tests, and call "
               "g6_mission_submit with the repository's folder. G6 runs the frozen tests after each submission and tells "
               "you what failed.")
#: The library names a mission's steps as an earlier server did, and offers its terminal for a person's: as this surface
#: offers each of them.
_TOOLS = {"create": "g6_mission_create", "status": "g6_mission_status", "submit": "g6_mission_submit",
          "accept": "g6_mission_clarify"}
_NAMED = re.compile(r"\bmission_(create|status|submit|accept)\b")
_DIALOG = {"confirm": "call g6_mission_confirm, which asks the person in the client's dialog",
           "allow": "call g6_mission_create, which asks the person in the client's dialog"}
_TERMINAL = re.compile(r'(?:confirm it at your terminal: )?`?python -P -m g6\.workforce\.decide (confirm|allow) \S+ '
                       r'--reason "\.\.\."`?')
_CODED = re.compile(r"\b(G6_E_[A-Z0-9_]+)\b")
MAX_CHECKS_STATED = 200
MAX_TEST_FILES = 200


def refused() -> dict | None:
    """The refusal of a tool that runs the repository's code, unless the host allows it."""
    if is_allowed(*SENSITIVE, allowed_sensitive()):
        return None
    return blocked_reply(f"[G6_E_SENSITIVE_COMPONENT_DENIED] {WHO} runs the repository's code; the host must allow it, "
                         f"by adding {WHO} (or workforce) to {SENSITIVE_ENV} where the server is registered")


def _here(value: Any) -> Any:
    """``value`` with each step named as this surface offers it (``_TOOLS``, ``_DIALOG``, ``SUBMIT_HERE``)."""
    if isinstance(value, str):
        named = _NAMED.sub(lambda found: _TOOLS[found[1]], value.replace(coding.OBJECTIVE_INSTRUCTIONS, SUBMIT_HERE))
        return _TERMINAL.sub(lambda found: _DIALOG[found[1]], named)
    if isinstance(value, dict):
        return {key: _here(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_here(item) for item in value]
    return value


def reply(found: dict) -> dict:
    """``found`` in the library envelope: the state a closed mission reached, a ``qualified-draft`` while it is open."""
    said = said_state(found) if found.get("completion_state") else "qualified-draft"
    if said == "blocked-escalated":
        shown = card(None, "G6_E_BLOCKED", f"{WHO} reported blocked-escalated: {found.get('result')}"[:500])
    else:
        shown = None if said == "verified" else card(None, "G6_E_QUALIFIED_DRAFT",
                                                     (found.get("review") or {}).get("said") or DRAFT)
    envelope = door_envelope(said, who=WHO, value=found, warning_card=shown, run_id=str(found.get("mission_id") or ""))
    state = envelope["completion_state"]
    return {"ok": state != "blocked-escalated", **corrected_result(_here(found), state), **envelope}


def _refusal(said: str, code: str = REFUSED) -> dict:
    return _here(blocked_reply(f"[{code}] {said}"))


def _guarded(work: Callable[[], dict]) -> dict:
    """``work()``, or what refused it as a blocked reply with its code: the exception's, the one its words name, or
    ``REFUSED``."""
    try:
        return work()
    except (ValueError, LookupError, OSError, subprocess.SubprocessError) as exc:
        named = _CODED.search(str(exc))
        return _refusal(str(exc), getattr(exc, "code", "") or (named[1] if named else REFUSED))


def _bound(shown: list[str], who: str) -> api.Approver:
    """The approver ``g6.workforce.api`` calls: the name the person gave, for exactly the lines they read."""
    return lambda lines: who if list(lines) == shown else None


def _changed(found: repo_tests.Change) -> list[str]:
    """The files ``found`` changes, with what a test run leaves behind left out."""
    return [rel for rel in found.files
            if not bench._IGNORED_PART.intersection(PurePosixPath(rel).parts) and not rel.endswith(".pyc")]


def _name(root: Path) -> str:
    """The repository's name under ``api.repository_dir``: its folder's, and its path's digest, so that two folders of
    one name never share a copy."""
    plain = re.sub(r"[^A-Za-z0-9_.-]", "-", root.name).strip("-_.")[:40] or "repository"
    return f"{plain}-{hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:8]}"


def register(wf: Any, repo: str) -> str:
    """The commit a clean repository stands at, in a git repository of G6's own under ``api.repository_dir``: its name,
    which is what a mission takes. The agent's tree stays the person's folder. Git carries the commit and nothing else:
    no ignored file, no configuration, no hook; and this one command may use the one transport that stays on this
    computer."""
    root = repo_tests.checked_repo(repo)
    changed = _changed(repo_tests.change(root, "HEAD"))
    if changed:
        raise repo_tests.RepoRefused("G6_E_GIT_REPO_DIRTY", DIRTY.format(files=", ".join(changed[:10])))
    target = api.repository_dir(wf) / _name(root)
    target.mkdir(parents=True, exist_ok=True)
    with git_env.safe_git(root) as env:
        local = {**env, "GIT_ALLOW_PROTOCOL": "file"}
        for args in (("init", "-q"), ("fetch", "-q", "--no-tags", "--depth=1", str(root), "HEAD"),
                     ("reset", "-q", "--hard", "FETCH_HEAD"), ("clean", "-qfdx")):
            subprocess.run([git_env.git_program(), "-C", str(target), *args], env=local, check=True,
                           capture_output=True, timeout=git_env.GIT_TIMEOUT_S)
    return target.name


def execution(repo: str) -> dict:
    """What ``g6_mission_create`` needs before its dialog: the ``name`` under ``api.repository_dir``, and ``question``
    and ``lines`` when no person has allowed this revision's code yet. A blocked reply when it cannot begin."""
    def work() -> dict:
        wf = api.bind()
        name = register(wf, repo)
        terms = coding.execution_terms(wf, name)
        if coding.execution_allowed(wf, name, terms["revision"], terms["digest"]):
            return {"name": name}
        question = [f"Folder: {repo}", *terms["lines"], IN_PLACE, NAME]
        return {"name": name, "lines": terms["lines"], "question": "\n".join(question)}

    return _guarded(work)


def create(name: str, objective: str, *, lines: list[str] | None = None, who: str = "", module: str = "",
           function: str = "", helpers: list[str] | None = None, risk: str = "R2", budget_cents: int = 0) -> dict:
    """Record the person's permission (``lines``, ``who``), when one was asked, and freeze the standard."""
    def work() -> dict:
        wf = api.bind()
        if lines is not None:
            api.allow_execution(wf, name, _bound(lines, who), reason="allowed in the client's dialog")
        return reply(api.create_coding_mission(wf, objective, name, module=module or None, function=function or None,
                                               helpers=helpers, risk=risk, budget_cents=budget_cents))

    return _guarded(work)


def clarify(mission_id: str, replay_semantics: str) -> dict:
    """Answer the replay question a mission asks before its standard is frozen. This is no confirmation."""
    return _guarded(lambda: reply(api.answer_clarification(api.bind(), mission_id, replay_semantics)))


def asked(mission_id: str) -> tuple[list[str], str, str] | dict:
    """What the person reads before confirming: the lines ``api.confirm`` shows, the standard's digest, and the dialog's
    whole text. A blocked reply when nothing waits for a confirmation, so no dialog is shown for nothing."""
    def work() -> tuple[list[str], str, str]:
        wf = api.bind()
        if not wf.runtime.confirmation_pending(mission_id):
            raise ValueError(NOTHING_ASKED)
        contract = wf.missions.acceptance(mission_id)
        standard, lines = coding.frozen(wf, mission_id), acceptance.describe(contract)
        scope = standard["scope"]["selected"]
        question = [f"Confirm what counts as done for: {wf.missions.get(mission_id)['objective']}",
                    f"Scope: {scope['module']} / {scope['function']}. {coding.access_reason(standard)}".rstrip(),
                    *lines, f"Standard digest: {contract.sha256}.",
                    "G6 then runs the repository's tests, and later checks each patch against this. Merging stays your "
                    "own separate decision.", NAME]
        return lines, contract.sha256, "\n".join(question)

    return _guarded(work)


def confirm(mission_id: str, lines: list[str], sha: str, who: str) -> dict:
    """Record the person's confirmation of the standard they read, and start its checks."""
    def work() -> dict:
        wf = api.bind()
        if wf.missions.acceptance(mission_id).sha256 != sha:
            raise ValueError(STANDARD_CHANGED)
        try:
            return reply(api.confirm(wf, mission_id, _bound(lines, who), reason="confirmed in the client's dialog"))
        except api.NotConfirmed as exc:
            raise ValueError(LINES_CHANGED) from exc

    return _guarded(work)


def _stated(wf: Any, mission_id: str, standard: dict) -> tuple[list[dict], list[str]]:
    """The frozen tests as ``review_patch`` takes them: the requirement's checks are those the baseline reproduced
    failing (an old test that passes at the base commit says nothing of the change, so a requirement of those alone
    could never be supported), up to ``Requirement``'s limit, and the rest must keep passing as extra tests. With none
    reproduced, every test is stated. A check the premortem constructed exists only in the frozen copy: the bench runs
    it."""
    frozen = list(standard.get("original_required_tests", standard["required_tests"]))
    ran = coding._reproduction(wf, mission_id).get("tests") or {}
    failing = [node for node in frozen if ran.get(node) is False] or frozen
    checks = failing[:MAX_CHECKS_STATED]
    objective = wf.missions.get(mission_id)["objective"]
    return [{"id": "standard", "text": objective, "checks": checks}], [node for node in frozen if node not in checks]


def _reviewed(wf: Any, mission_id: str, found: dict) -> dict:
    """``found`` with the patch review of the mission's latest proposal, when it had one."""
    review = coding.last_review(wf.missions, wf.ledger, mission_id)
    return {**found, "review": review} if review else found


def submit(mission_id: str, repo: str, *, claim: str = "", timeout_s: int = 300) -> dict:
    """The check step: ``review_patch`` on the agent's working tree, then the same text to the mission with what the
    review found (``HELD`` holds it back, as the module says). An unconfirmed mission, a change outside the standard's
    permission and a review that refused leave the mission as it was, and use no attempt."""
    def work() -> dict:
        wf = api.bind()
        if wf.runtime.confirmation_pending(mission_id):
            return blocked_reply(UNCONFIRMED)
        if not any(spec.key == "patch" for spec in wf.runtime.awaiting(mission_id)):
            raise ValueError(NOT_WAITING)
        standard = coding.frozen(wf, mission_id)
        root, base = repo_tests.checked_repo(repo), standard["baseline_revision"]
        found, allowed = repo_tests.change(root, base), standard["permissions"]["files"]
        changed = _changed(found)
        # a link that leaves the repository is never read in G6's own process (``repo_tests.links_outside``)
        outside = [rel for rel in changed if rel not in allowed or rel in found.outside_links]
        if outside:
            return blocked_reply(OUT_OF_SCOPE.format(files=", ".join(outside[:10]), allowed=", ".join(allowed)))
        if not changed:
            return blocked_reply(NO_CHANGE)
        files = {rel: (root / rel).read_bytes().decode("utf-8") for rel in changed}
        requirements, tests = _stated(wf, mission_id, standard)
        reviewed = review_reply.review(root, task=wf.missions.get(mission_id)["objective"], requirements=requirements,
                                       tests=tests, claim=claim, base=base, timeout_s=timeout_s,
                                       expected_artifact_digest=digest(repo_tests.snapshot(root)))
        assessment = reviewed.get("assessment")
        if not isinstance(assessment, dict):
            return reviewed
        outcome = assessment.get("outcome")
        kept = {"outcome": outcome, "artifact_digest": reviewed.get("artifact_digest"), "held": outcome in HELD,
                "reasons": [r.get("code") for r in assessment.get("reasons") or [] if isinstance(r, dict)]}
        mission = _guarded(lambda: reply(_reviewed(wf, mission_id, api.submit_patch(wf, mission_id, files, review=kept))))
        if mission["completion_state"] == "blocked-escalated":
            reviewed = {**reviewed, "ok": False, "completion_state": "blocked-escalated",
                        "reliability_label": "blocked-escalated"}
        return {**reviewed, "mission": mission}

    return _guarded(work)


def finish(found: Any, job_id: str) -> Any:
    """What ``metaharness_job`` returns for a finished submission: the review's compact reply, and the mission's."""
    shown = review_reply.finish(found, job_id)
    return {**shown, "mission": found["mission"]} if isinstance(found, dict) and "mission" in found else shown


def status(mission_id: str, detail: str = "summary") -> dict:
    def work() -> dict:
        wf = api.bind()
        return reply(_reviewed(wf, mission_id, api.status(wf, mission_id, detail)))

    return _guarded(work)


def certificate(mission_id: str) -> dict:
    """The certificate, with the patch review beside it: its digest binds its fields, as ``certificate_gaps`` says."""
    def work() -> dict:
        wf = api.bind()
        return reply(_reviewed(wf, mission_id, {"mission_id": mission_id,
                                                "certificate": api.certificate(wf, mission_id)}))

    return _guarded(work)


def premortem(repo: str, task: str, intent: dict | None = None) -> dict:
    """Before the work, from what the caller gave and the repository's file names: no model, and nothing runs. The
    intent contract, a proposed standard of done (a requirement that names one of the repository's test files has it
    as its stated check; any other is not established), the competing ways the task can fail and at most three
    questions. A proposal: nothing here is frozen, and the person confirms a standard only through a mission."""
    if not task.strip():
        return blocked_reply("[G6_E_INPUT_VALIDATION] task must say what the change is for")
    try:
        root = repo_tests.checked_repo(repo)
        given = verification_search.intent_contract(intent) if intent is not None else None
    except repo_tests.RepoRefused as exc:
        return blocked_reply(f"[{exc.code}] {exc}")
    except ValueError as exc:
        return blocked_reply(f"[G6_E_INPUT_VALIDATION] intent: {exc}")

    def work() -> dict:
        session = IntentElicitor().elicit(task, prior=given)
        listed = repo_tests._git(root, "ls-files", "-z").decode("utf-8", "replace").split("\0")
        tests = [rel for rel in listed if rel and repo_tests.is_test_related(rel)]
        mapped = verification_search.requirements_from_intent(session.contract, [])
        standard = [{"id": found.id, "text": found.text, "kind": found.kind,
                     "checks": (named := [rel for rel in tests if rel in found.text]),
                     "status": "stated_checks" if named else "not_established"} for found in mapped.requirements]
        questions = [q.question for q in session.questions[:verification_search.MAX_QUESTIONS]]
        shown = card(None, "G6_E_QUALIFIED_DRAFT", "g6_premortem: a proposal; nothing in it has been checked")
        return {"ok": True, "intent": session.contract.model_dump(mode="json"), "standard": standard,
                "hypotheses": checklist.hypotheses(), "questions": questions, "test_files": tests[:MAX_TEST_FILES],
                "unknowns": mapped.unknowns, "model_calls": 0,
                **door_envelope("qualified-draft", who="g6_premortem", warning_card=shown)}

    return _guarded(work)


__all__ = ["asked", "certificate", "clarify", "confirm", "create", "execution", "finish", "premortem", "refused",
           "register", "reply", "status", "submit"]
