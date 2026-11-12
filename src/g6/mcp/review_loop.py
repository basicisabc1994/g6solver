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
import re
import uuid
from pathlib import Path
from typing import Any

#: The card a step carries: the stable code of a result that is a draft, whatever the loop found.
CODE = "G6_E_REVIEW_UNTIL_CLEAN"
SENDS_BACK = ("A reviewer's test fails on the patch. It is a finding you may reject: check it against the task.",
              "Repair the cause and call again, or leave the code as it was and call again with your reason in one line.")
ENDED = ("The loop has ended. A review that reproduces nothing is not evidence the work is right, only that this reviewer "
         "found no failure it could show.", "Show the report as it is. Merging is the person's own decision.")
NOTHING = ("No Python changed against the base commit, so nothing was tested and nothing was spent. Call again once the "
           "session has changed Python.")
CHANGED = ("The code has changed since this session's loop ended, so the report below is of an earlier patch and nothing in "
           "this call tested the code as it stands. Name a new session to review it.")


def _asked(repo: str, task: str, base: str, session: Path) -> dict:
    """What the session's first call gave, which later calls cannot move: ``repo_tests.change`` reads the base as a commit.
    Called with the session held, so one first call binds it. A call for another repository or task is refused, as its
    reply would be about what the first asked. A later base is not compared: it may be HEAD, which the agent's commits move."""
    from g6.workforce import repo_tests
    from g6.core import cloud_consent as cloud

    root, known = repo_tests.checked_repo(repo), session / "task.json"
    if known.is_file():
        try:
            expected = (session / 'task.sha256').read_text(encoding='ascii')
            asked = json.loads(cloud.read_retained(known, expected))
            if not isinstance(asked, dict) or set(asked) != {'repo', 'task', 'base'}:
                raise ValueError('malformed retained task')
        except (OSError, TypeError, ValueError):
            cloud.record_sources((cloud.UNKNOWN,))
            raise repo_tests.RepoRefused(cloud.REQUIRED, cloud.WITHHELD) from None
        if asked["repo"] != str(root):
            raise repo_tests.RepoRefused("G6_E_INPUT_VALIDATION", "this session belongs to another repository")
        if asked["task"] != task:
            raise repo_tests.RepoRefused("G6_E_INPUT_VALIDATION", "this session belongs to another task: repeat its first "
                                                                  "call's task as it was, or name a new session")
        return asked
    asked = {"repo": str(root), "task": task, "base": repo_tests.change(root, base).base_commit}
    cloud.write_retained(known, json.dumps(asked).encode('utf-8'))
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
    from g6.workspace._envelope import WorkspaceRefused

    if not task.strip() or not session.strip():
        return blocked_reply("[G6_E_INPUT_VALIDATION] a task and a session are needed: name one session for one task")
    folder = completion_hook.folder(f"mcp:{session}")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    request_id = uuid.uuid4().hex[:12]
    try:
        with loop._held(folder):
            asked = _asked(repo, task, base, folder)
        done = loop.advance(asked["repo"], asked["task"], asked["base"], folder, said=reason, answered=bool(reason.strip()))
    except repo_tests.RepoRefused as exc:
        return blocked_reply(f"[{exc.code}] {exc}", request_id=request_id)
    except WorkspaceRefused:
        done = loop.Step(loop.WAIT, loop.ALREADY_RUNNING, loop.BUSY)
    if done.action == loop.WAIT:      # the step that is running owns the state, so nothing of it is read here
        return {**blocked_reply(f"[{done.code}] {done.text}", request_id=request_id), "run_id": folder.name,
                "action": done.action}
    state = loop._load(folder)
    evidence = _evidence(state, asked["base"])
    text = done.text or loop.read_report(folder, state)
    code, ending, why, note = done.code, done.ending, state.get("stop") or text or NOTHING, done.note
    if note == loop.ALREADY_ENDED:     # a repeat ran nothing: its report is of the patch as the session's loop ended
        reviews = state.get("reviews") or []    # a stop with a code keeps it, as it says why the session's loop ended
        ended = state.get("ended_patch") or (reviews[-1]["patch"] if reviews else "")    # a session ended before it was kept
        if ended and loop._patch_now(Path(asked["repo"]), asked["base"]) != ended:
            text, code, ending, why, note = f"{CHANGED}\n\n{text}", code or loop.NO_CLEAN_REVIEW, "untested", CHANGED, CHANGED
    said = {"action": done.action, "text": text} if done.action == loop.CONTINUE else {
        "action": done.action, "report": text, "ending": ending, **({"note": note} if note else {})}
    if code:
        refused = blocked_reply(f"[{code}] {why}"[:600], request_id=request_id)
        result = {**refused, "run_id": folder.name, "evidence": evidence, **said}
    else:
        card = SENDS_BACK if done.action == loop.CONTINUE else ENDED
        result = {"ok": True, **said, **envelope_fields(
            reliability_label="qualified-draft", warning_card=warning_card(CODE, *card), evidence=evidence, request_id=request_id,
            run_id=folder.name, degradation_reason=card[0][:300])}
    from g6.core.cloud_consent import bind_output
    return bind_output(result, loop.source_refs(state))


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
    from g6.core.cloud_consent import REQUIRED, current_capture, input_source
    capture = current_capture()
    if capture is not None:
        capture.review_unsent = (result.get('ending') == 'untested' and
            str(result.get('error', '')).startswith(f'[{REQUIRED}]'))
    body, refs, _ = freeze_output(result)
    result = json.loads(body)
    metadata = {key: result[key] for key in ('action', 'ending', 'request_id', 'run_id') if key in result}
    metadata.update(has_report='report' in result, has_text='text' in result)
    if capture is not None and capture.review_unsent and capture.refused_provider:
        metadata['review_provider'] = capture.refused_provider
    code = re.match(r'\[(G6_E_[A-Z0-9_]+)\]', result.get('error', ''))
    if code:
        metadata['error_code'] = code[1]
    return bind_output(result, (Source.text(repository_source(repo), "completion-report", body),
                                Source.text(input_source(task), "task", task), *(refs or ()),
                                *(capture.sources if capture is not None else ())),
                       metadata=metadata)


__all__ = ["CODE", "step"]
