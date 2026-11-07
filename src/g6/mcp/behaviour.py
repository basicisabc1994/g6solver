# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""``g6_confirm_behaviour``: one behaviour question about a task, shown to the person in the client's own dialog, and
the answer kept and enforced (G6 0.3 design, sections 3.2 and 7.3; slice 3).

``confirm`` takes the task and the sources of candidates (the calling agent's ``candidates``, and a finished
``g6_review_patch`` job whose differential.v1 instances G6 saw the base commit and the patch disagree on, with those
``template_instances``), chooses one candidate by the fixed priority (``g6.harness.behaviour_question``), records it
as asked in the task's store under ``G6_HOME`` within the task's budget (``g6.harness.question_store``), and shows the
behaviour form through ``g6.mcp.consent.elicit``, the transport every question to the person goes through. A question
already asked and not answered is shown again first, at no further cost. The answer comes from the dialog's result
only: no argument of the tool carries one, no agent gives one, and there is no terminal fallback. ``answered_by`` is
``person:`` and the name the form returned, recorded as given and not authenticated. A decline, a cancel, a skip, a
client without a dialog or a form without a name leaves the question pending: the reply reports it with the candidate,
so the person can answer when the tool is called again. With no candidate the question is held until one exists.

``enforced`` is what ``g6_review_patch`` reads: the standard of the expectations the person confirmed for the task, or
None when G6 asked nothing about it; a store it cannot read refuses the review rather than enforcing nothing.
``reviewed`` runs a queued review with the standard read when the review executes, never when it was queued, so an
answer given while it waited is the one enforced; and it binds the reply G6 saves to the task (``task_identity``). A
saved review gives candidates only when it is bound to this task and repository and its artefact digest is the digest
of the tree the question is asked about.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from g6.core.door import blocked_reply, card, door_envelope
from g6.core.ledger import digest
from g6.core.storage import state_path
from g6.harness import behaviour_question as bq
from g6.harness import question_store as qs
from g6.workforce import repo_tests

WHO = "g6_confirm_behaviour"
#: The reply's statuses: the person confirmed an outcome; answered with one that confirms nothing; the question is
#: pending (to be answered later); held until a candidate exists; or not asked, the task's budget being spent.
CONFIRMED, NOT_CONFIRMED, PENDING, HELD, SPENT = "confirmed", "answered_unconfirmed", "pending", "held", "budget_spent"
CODES = {CONFIRMED: "G6_E_QUESTION_CONFIRMED", NOT_CONFIRMED: "G6_E_QUESTION_UNCONFIRMED",
         PENDING: "G6_E_QUESTION_UNCONFIRMED", HELD: "G6_E_QUESTION_HELD", SPENT: "G6_E_QUESTION_BUDGET_SPENT"}
IDENTITY = "recorded as given, not authenticated"
NOT_FOR_YOU = ("Never answer it for the person, and offer no other way to answer: only the person answers, in this "
               "dialog.")
NEXT = {
    CONFIRMED: "The person's answer is enforced on each later g6_review_patch of this task, as its one example.v1 check.",
    NOT_CONFIRMED: "The person's answer confirms no outcome, so nothing is enforced; tell the person what you will do.",
    PENDING: ("The question stays unconfirmed. Show the person the question in this reply; when they want to answer, "
              "call g6_confirm_behaviour again with the same repo and task and the dialog shows it again. "
              + NOT_FOR_YOU),
    HELD: ("No candidate exists yet, so the question is held: call again with candidates, or with a finished review "
           "whose differential.v1 instances G6 saw the base commit and the patch disagree on."),
    SPENT: ("The task's question budget is spent, so nothing was asked; the candidate is shown for the person. Only the "
            "person allows more: in a form G6 shows them, or with G6_QUESTIONS_PER_TASK where the server is "
            "registered."),
}
_JOB = re.compile(r"[0-9a-f]{32}\Z")


#: The refusal of a saved review that is not about the task, repository and tree the question is asked about.
NOT_THIS_REVIEW = "G6_E_QUESTION_REVIEW_NOT_BOUND"


def bound(reply: Any, identity: str | None) -> Any:
    """``reply``, a finished review's, bound to the task it reviewed (``task_identity``, ``question_store``'s): the
    whole reply G6 saves carries it, and nothing it decides changes. A refusal, or a review of no task G6 could name,
    is returned as it is."""
    if identity is None or not isinstance(reply, dict) or not isinstance(reply.get("assessment"), dict):
        return reply
    return {**reply, "task_identity": identity}


def identity_of(repo: str, task: str) -> str | None:
    """The task's identity (``question_store.task_identity``), or None for a task or repository the review refuses."""
    if not isinstance(task, str) or not task.strip():
        return None
    try:
        return qs.task_identity(repo_tests.checked_repo(repo), task)
    except (repo_tests.RepoRefused, OSError, ValueError):
        return None


def _review_candidates(task: str, review_job: str, template_instances: Any, *, identity: str,
                       artifact: str) -> list[bq.Candidate]:
    """The candidates a finished review's differences give: G6 reads its own saved reply (``reviews/<job>.json``) for
    what it observed, only when the reply is bound to this task's ``identity`` (its repository and text) and its
    artefact digest is ``artifact``, the tree the question is asked about; and takes the instances' bindings from
    ``template_instances``, each held to the review's record of it by its digest."""
    if not review_job:
        return []
    if not isinstance(review_job, str) or not _JOB.match(review_job):
        raise bq.CandidateRefused("review_job is the job id g6_review_patch returned")
    from g6.harness import verification_templates

    try:
        saved = json.loads(state_path("reviews", f"{review_job}.json").read_text(encoding="utf-8"))
        completion = saved["assessment"]["completion"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise bq.CandidateRefused(f"G6 holds no finished review with this job id it can read ({type(exc).__name__})"
                                  ) from None
    if saved.get("task_identity") != identity:
        raise bq.CandidateRefused("that review is not bound to this task and repository: G6 takes a review's "
                                  "observations only for the task and repository it reviewed", NOT_THIS_REVIEW)
    if saved.get("artifact_digest") != artifact:
        raise bq.CandidateRefused("that review observed another tree: the working tree changed after it, so its "
                                  "observations are not about the tree the question is asked about; review again",
                                  NOT_THIS_REVIEW)
    items = template_instances or []
    named = {item.get("requirement_id") for item in items if isinstance(item, dict)}
    try:
        instances = verification_templates.template_instances(items, named)
    except ValueError as exc:
        raise bq.CandidateRefused(f"template_instances: {exc}") from None
    ranked = [q.get("check") for q in completion.get("open_questions") or [] if isinstance(q, dict)]
    found = bq.differential_candidates(task, instances, completion.get("activities") or [], ranked)
    return [bq.Candidate.make(**{**one.to_dict(), "evidence": [*one.to_dict()["evidence"],
                                                               f"the review's job id: {review_job}"]})
            for one in found]


def _budget(state: qs.State) -> dict:
    return {"allowance": state.allowance, "asked": len(state.asked), "left": state.left, "default": state.default,
            **({"note": state.budget_note} if state.budget_note else {})}


def _standard(state: qs.State) -> dict:
    standard = state.standard()
    return {"sha256": standard.sha256, "revision": standard.revision, "supersedes": standard.supersedes,
            "predicates": list(standard.predicates), "superseded": list(state.superseded)}


def _reply(status: str, why: str, *, state: qs.State, asked: qs.Asked | None = None, candidate: bq.Candidate | None
           = None, plan: dict | None = None, reading: bq.Reading | None = None, seconds: float | None = None) -> dict:
    found: dict[str, Any] = {"ok": True, "status": status, "why": why, "coverage": bq.UNKNOWN,
                             "task_determined": bq.UNKNOWN, "budget": _budget(state), "standard": _standard(state),
                             "next_action": NEXT[status]}
    if asked is not None:
        found["question"] = asked.question.to_dict()
        found["question_sha256"] = asked.question.sha256
        if asked.rule is not None:
            found["rule_question"] = asked.rule.to_dict()
        candidate = asked.candidate
    if candidate is not None:
        found["candidate"] = candidate.to_dict()
    if plan is not None:
        found["plan"] = plan
    if reading is not None and reading.answer is not None:
        answer = reading.answer
        found["answer"] = {"option": answer.option, "text": answer.text, "answered_by": answer.answered_by,
                           "answered_at": answer.answered_at, "identity": IDENTITY,
                           "answers": answer.question_sha256}
        if seconds is not None:
            found["person_seconds"] = round(seconds, 3)
    if status == CONFIRMED:
        held = state.standard()
        mine = next((one for one in held.expectations if one.answer_sha256 == reading.answer.sha256), None)
        if mine is not None:
            found["expectation"] = {"expectation_id": mine.expectation_id, "template": "example.v1",
                                    "provenance": mine.provenance, "check_sha256": mine.check_sha256,
                                    "enforced_on": "this one case, on each later g6_review_patch of this task"}
    shown = card(None, CODES[status], f"{WHO}: {status}: {why}"[:300])
    return {**found, **door_envelope("qualified-draft", who=WHO, warning_card=shown)}


async def confirm(ctx: Any, repo: str, task: str, candidates: Any = None, review_job: str = "",
                  template_instances: Any = None) -> dict:
    """The tool (the module's docstring)."""
    from g6.mcp.consent import elicit

    if not isinstance(task, str) or not task.strip():
        return blocked_reply("[G6_E_INPUT_VALIDATION] task must say what the change is for")
    try:
        root = repo_tests.checked_repo(repo)
    except repo_tests.RepoRefused as exc:
        return blocked_reply(f"[{exc.code}] {exc}")
    identity = qs.task_identity(root, task)
    store = qs.TaskQuestions(identity)
    try:
        state = store.state()
        asked, plan = state.pending, None
        if asked is None:
            # the tree the question is asked about, which a saved review must have observed
            artifact = digest(repo_tests.snapshot(root)) if review_job else ""
            try:
                made = [*bq.caller_candidates(task, candidates), *_review_candidates(
                    task, review_job, template_instances, identity=identity, artifact=artifact)]
            except bq.CandidateRefused as exc:
                return blocked_reply(f"[{exc.code}] {exc}")
            plan = bq.plan(made)
            chosen = bq.choose(made)
            if chosen is None:
                return _reply(HELD, "no candidate yet", state=state, plan=plan)
            if state.left < 1:
                return _reply(SPENT, state.budget_note or "the task's budget is spent", state=state,
                              candidate=chosen, plan=plan)
            case, rule = chosen.questions(artifact or digest(repo_tests.snapshot(root)))
            try:
                asked = store.ask(case, rule, chosen)
            except qs.BudgetSpent as exc:
                return _reply(SPENT, str(exc), state=store.state(), candidate=chosen, plan=plan)
        message, schema = bq.form(asked.question, asked.candidate, asked.rule)
        shown_at, started = qs.now(), time.monotonic()
        action, content, _error = await elicit(ctx, message, schema)
        back_at, seconds = qs.now(), time.monotonic() - started
        reading = bq.read(action, content, asked.question, asked.rule, answered_at=back_at)
        if reading.answer is not None:   # the answer first: a stop after it keeps what the person chose
            try:
                store.answer(reading.answer)
            except qs.AnswerRefused as exc:
                return blocked_reply(f"[{qs.REFUSED}] {exc}")
        store.show(asked, bq.ANSWERED if reading.answer is not None else reading.why, shown_at=shown_at,
                   back_at=back_at, seconds=seconds)
        if reading.further:
            store.allow_more(asked, by=reading.by)
        state = store.state()
    except qs.StoreUnreadable as exc:
        return blocked_reply(str(exc))
    if reading.answer is None:
        return _reply(PENDING, reading.why, state=state, asked=asked, plan=plan, reading=reading)
    status = CONFIRMED if reading.confirms else NOT_CONFIRMED
    return _reply(status, reading.why, state=state, asked=asked, plan=plan, reading=reading, seconds=seconds)


def enforced(repo: str, task: str) -> Any:
    """The standard ``g6_review_patch`` enforces for this task: None when G6 asked nothing about it (or the
    repository or task is one the review itself refuses); ``StoreUnreadable`` when its store cannot be read."""
    if not isinstance(task, str) or not task.strip():
        return None
    try:
        root = repo_tests.checked_repo(repo)
    except (repo_tests.RepoRefused, OSError, ValueError):
        return None
    store = qs.TaskQuestions(qs.task_identity(root, task))
    if not store.exists():
        return None
    standard = store.state().standard()
    return standard if standard.expectations else None


def reviewed(review: Any, repo: str, task: str, **kwargs: Any) -> Any:
    """A queued ``g6_review_patch``, as its job runs it: the standard the person confirmed for the task read now, when
    the review executes (``enforced``), so an answer given while it waited is the one enforced; a store G6 cannot read
    refuses the review. The reply is bound to the task (``bound``)."""
    from g6.harness import standard_publication

    try:
        confirmed = enforced(repo, task)
        if confirmed is None:
            return bound(review(repo, task=task, **kwargs), identity_of(repo, task))
        identity = identity_of(repo, task)
        store = qs.TaskQuestions(identity)
        with standard_publication.observing(store.counting):
            reply = review(repo, task=task, confirmed=confirmed, **kwargs)
        # Also refuse a standard changed after issuance but before the review returns its evidence.
        with store.counting(confirmed):
            return bound(reply, identity)
    except qs.StoreUnreadable as exc:
        return blocked_reply(str(exc))
    except standard_publication.StandardSuperseded as exc:
        return blocked_reply(str(exc))


__all__ = ["CODES", "CONFIRMED", "HELD", "IDENTITY", "NOT_CONFIRMED", "NOT_THIS_REVIEW", "PENDING", "SPENT", "WHO",
           "bound", "confirm", "enforced", "identity_of", "reviewed"]
