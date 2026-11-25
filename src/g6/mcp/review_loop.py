# SPDX-License-Identifier: Apache-2.0
"""The completion loop over MCP (``g6_review_until_clean``): one step of ``completion_loop.advance``, for a client the plugin's
Stop hook cannot reach, in G6's envelope.

Each call is one step of one session, as each Stop hook is. The session's first call fixes its repository, task and base
commit, under the session's lock, so a commit the agent makes later cannot move the base and two first calls cannot both
bind it. The name a client gives is only ever digested into a folder, so it can name no path. ``continue`` carries the text for the agent and ``stop`` the report, to show as it is, and how the run ended. A stop
that tested nothing or left no clean review is ``blocked-escalated`` with its own code; any other is ``qualified-draft`` at
best, as a review that reproduces nothing is not evidence the work is right. Nothing here decides anything.

A client may repeat a call whose reply it lost, and a server's jobs run one at a time, so that repeat runs just after the call
it repeats, with no overlap for a lock to see. So only a call with a ``reason`` rejects a finding: with the code unchanged and
no reason, the same finding goes back, and nothing is spent. A reason that asks anything rejects nothing either: the step says
wait, for the person's answer.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from g6.core.refusals import code_of
from g6.harness import _completion_storage as store
from g6.harness.completion_loop import CHANGED as CHANGED
from g6.harness.completion_loop import RESERVATION_UNSAVED
from g6.invariants import ERROR_CODE_PATTERN


#: The card a step carries: the stable code of a result that is a draft, whatever the loop found.
CODE = "G6_E_REVIEW_UNTIL_CLEAN"
SENDS_BACK = ("A reviewer's test fails on the patch. It is a finding you may reject: check it against the task.",
              "Repair the cause and call again, or leave the code as it was and call again with your reason in one line.")
ENDED = ("The loop has ended. A review that reproduces nothing is not evidence the work is right, only that this reviewer "
         "found no failure it could show.", "Show the report as it is. Merging is the person's own decision.")
NOTHING = ("No Python changed against the base commit, so nothing was tested and nothing was spent. Call again once the "
           "session has changed Python.")


def _asked(repo: str, task: str, base: str, session: Path) -> dict:
    """What the session's first call gave, which later calls cannot move: ``repo_tests.change`` reads the base as a commit.
    Called with the session held, so one first call binds it. A call for another repository or task is refused, as its
    reply would be about what the first asked. A later base is not compared: it may be HEAD, which the agent's commits move."""
    from g6.workforce import repo_tests

    root, known = repo_tests.checked_repo(repo), store.task_path(session / 'task.json')
    if store.task_present(session / 'task.json'):
        try:
            asked = store.read_task(session / 'task.json')
            if not isinstance(asked, dict) or set(asked) != {'repo', 'task', 'base'}:
                raise ValueError('malformed retained task')
        except (OSError, TypeError, ValueError, RecursionError, MemoryError) as exc:
            if isinstance(exc, PermissionError) and code_of(exc) == RESERVATION_UNSAVED:
                raise
            raise repo_tests.RepoRefused(store.TASK_UNREADABLE, store.UNREADABLE_TASK) from None
        if asked["repo"] != str(root):
            raise repo_tests.RepoRefused("G6_E_INPUT_VALIDATION", "this session belongs to another repository")
        if asked["task"] != task:
            raise repo_tests.RepoRefused("G6_E_INPUT_VALIDATION", "this session belongs to another task: repeat its first "
                                                                  "call's task as it was, or name a new session")
        return asked
    asked = {"repo": str(root), "task": task, "base": repo_tests.change(root, base).base_commit}
    with store.task_publication(known):
        store.write_retained(known, json.dumps(asked).encode('utf-8'))
    return asked


def _evidence(state: dict, base: str) -> list[dict[str, Any]]:
    """Each review that ran: what it said, and what G6's own runs of the reviewers' tests did."""
    return [{"source": "g6.harness.completion_loop", "summary": f"review {n}: {one['verdict']}; tests: {one['results']}"[:200],
             "authority": "host-observed", "artifact_digest": one["patch"], "base_commit": base,
             "scope": "these reviewers' tests on this patch, and nothing else"}
            for n, one in enumerate(state.get("reviews") or [], 1)]


def _step(repo: str, task: str, session: str, base: str = "HEAD", reason: str = "") -> dict:
    """One step of the loop, for a job over MCP: see the module's docstring."""
    from g6.core.door import blocked_reply
    from g6.core.reliability_envelope import envelope_fields, warning_card
    from g6.harness import completion_hook, completion_loop as loop
    from g6.workforce import repo_tests
    from g6.workspace._envelope import BUSY as WORKSPACE_BUSY, WorkspaceRefused

    if not task.strip() or not session.strip():
        return {**blocked_reply("[G6_E_INPUT_VALIDATION] a task and a session are needed: name one session for one task"),
                "code": "G6_E_INPUT_VALIDATION", "missing": [key for key, value in (("task", task), ("session", session)) if not value.strip()]}
    request_id = uuid.uuid4().hex[:12]
    try:
        folder = completion_hook.folder(f"mcp:{session}")
        store.mkdir(folder, parents=True, exist_ok=True, mode=0o700)
        with loop._held(folder):
            asked = _asked(repo, task, base, folder)
        done = loop.advance(asked["repo"], asked["task"], asked["base"], folder, said=reason, answered=bool(reason.strip()))
    except repo_tests.RepoRefused as exc:
        return {**blocked_reply(f"[{exc.code}] {exc}", request_id=request_id), "code": exc.code,
                **({'recovery': loop.NEW_SESSION} if exc.code == store.TASK_UNREADABLE else {})}
    except PermissionError as exc:
        if code_of(exc) != RESERVATION_UNSAVED:
            raise
        return {**blocked_reply(str(exc), request_id=request_id), 'code': RESERVATION_UNSAVED,
                'action': loop.STOP}
    except WorkspaceRefused as exc:
        if exc.code not in (WORKSPACE_BUSY, loop.BUSY):
            return {**blocked_reply(str(exc), request_id=request_id), "action": loop.STOP, "code": exc.code}
        done = loop.Step(loop.WAIT, loop.ALREADY_RUNNING, loop.BUSY, call_ran=False)
    if done.action == loop.WAIT:      # the step that is running owns the state, so nothing of it is read here
        return {**blocked_reply(f"[{done.code}] {done.text}", request_id=request_id), "run_id": folder.name,
                "action": done.action, "code": done.code}
    total = None
    try:
        # A late worker can reserve after advance releases its lock. Read the
        # report and accounting together under that same writer serialization.
        # Keep this surface's report reader here; the common builder decides
        # whether this turn has any retained report to read.
        done, state, why = loop.reply_for(folder, done, asked['repo'], asked['base'],
            report_reader=lambda run, state, *, code: loop.read_report(run, state, code=code))
        total = loop.attempted(state)
        text = done.text
    except PermissionError as exc:
        if code_of(exc) != RESERVATION_UNSAVED:
            raise
        return {**blocked_reply(str(exc), request_id=request_id), 'code': RESERVATION_UNSAVED,
                'run_id': folder.name, 'action': loop.STOP}
    except WorkspaceRefused as exc:
        if exc.code in (WORKSPACE_BUSY, loop.BUSY):
            return {**blocked_reply(f'[{loop.BUSY}] The retained reply is waiting for its writer. Retry this call; '
                                   'the send count is unavailable while publication is in progress.', request_id=request_id),
                    'run_id': folder.name, 'action': loop.WAIT, 'code': loop.BUSY}
        return {**blocked_reply(str(exc), request_id=request_id), "run_id": folder.name,
                "action": loop.STOP, "code": exc.code,
                **({"requests_sent": total} if total is not None else {})}
    evidence = _evidence(state, asked["base"])
    code, ending, note = done.code, done.ending, done.note
    said = {"action": done.action, "text": text} if done.action == loop.CONTINUE else {
        "action": done.action, "report": text, "ending": ending, **({"note": note} if note else {})}
    if note == loop.ALREADY_ENDED:
        said['recovery'] = 'Call g6_review_until_clean with a new session to review further changes.'
    elif isinstance(state, loop._RetainedState) and not state.readable:
        said['recovery'] = loop.NEW_SESSION
    if code:
        refused = blocked_reply(f"[{code}] {why}"[:600], request_id=request_id)
        result = {**refused, "run_id": folder.name, "evidence": evidence, **said, "code": code}
    else:
        card = SENDS_BACK if done.action == loop.CONTINUE else ENDED
        result = {"ok": True, **said, **envelope_fields(
            reliability_label="qualified-draft", warning_card=warning_card(CODE, *card), evidence=evidence, request_id=request_id,
            run_id=folder.name, degradation_reason=card[0][:300])}
    if code and code != loop.NO_PYTHON and (total is not None or state.get("done")):
        # Completed legacy runs keep their unknown-count field and metadata.
        # NO_PYTHON omits the field; other stateless refusals have no count.
        result["requests_sent"] = total
    from g6.core.cloud_consent import bind_output
    return bind_output(result, loop.source_refs(state))


def _unsent(result: dict) -> bool:
    """Whether a step's reply is a review that was not sent: untested for want of a grant, with a request count present
    and zero (a missing count is unknown, never zero)."""
    from g6.core.cloud_consent import REQUIRED
    from g6.harness.workflow.obstacle import none_sent

    return result.get('ending') == 'untested' and code_of(result) == REQUIRED and none_sent(result.get('requests_sent'))


def step(repo: str, task: str, session: str, base: str = "HEAD", reason: str = "") -> dict:
    """Retain the run's saved origins; never rebind old report content to the current request."""
    from g6.core.cloud_consent import Source, bind_output, repository_source, freeze_output
    from g6.harness import completion_loop
    result = _step(repo, task, session, base, reason)
    if result.get('action') == completion_loop.WAIT:
        # The running step owns the state. This reply contains only fresh host
        # envelope fields and the fixed busy message; do not load its report.
        from g6.mcp.consent_controls import public_instructions
        return public_instructions(result)
    from g6.core.cloud_consent import current_capture, input_source
    capture = current_capture()
    if capture is not None:
        capture.review_unsent = _unsent(result)
    body, refs, _ = freeze_output(result)
    try:
        result = json.loads(body)
    except (RecursionError, MemoryError):
        raise ValueError("a JSON record exceeds decoder limits") from None
    # requests_sent is kept with the rest: a withheld reply rebuilt from this metadata must not read as unsent
    metadata = {key: result[key] for key in ('action', 'ending', 'request_id', 'run_id', 'requests_sent')
                if key in result}
    metadata.update(has_report='report' in result, has_text='text' in result)
    if capture is not None and capture.review_unsent and capture.refused_provider:
        metadata['review_provider'] = capture.refused_provider
    code = code_of(result)
    if ERROR_CODE_PATTERN.match(code):
        metadata['error_code'] = code
    return bind_output(result, (Source.text(repository_source(repo), "completion-report", body),
                                Source.text(input_source(task), "task", task), *(refs or ()),
                                *(capture.sources if capture is not None else ())),
                       metadata=metadata)


__all__ = ["CODE", "step"]
