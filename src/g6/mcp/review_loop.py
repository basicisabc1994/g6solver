# SPDX-License-Identifier: Apache-2.0
"""The completion loop over MCP (``g6_review_until_clean``): one step of ``completion_loop.advance``, for a client the plugin's
Stop hook cannot reach, in G6's envelope.

Each call is one step of one session, as each Stop hook is. The session's first call fixes its repository, task and base
commit, so a commit the agent makes later cannot move the base, and the name a client gives is only ever digested into a
folder, so it can name no path. ``continue`` carries the text for the agent and ``stop`` the report, to show as it is. A stop
that tested nothing or left no clean review is ``blocked-escalated`` with its own code; any other is ``qualified-draft`` at
best, as a review that reproduces nothing is not evidence the work is right. Nothing here decides anything.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

#: The card a step carries: the stable code of a result that is a draft, whatever the loop found.
CODE = "G6_E_REVIEW_UNTIL_CLEAN"
SENDS_BACK = ("A reviewer's test fails on the patch. It is a finding you may reject: check it against the task.",
              "Repair the cause and call again, or leave the code as it was and call again with your reason in one line.")
ENDED = ("The loop has ended. A review that reproduces nothing is not evidence the work is right, only that this reviewer "
         "found no failure it could show.", "Show the report as it is. Merging is the person's own decision.")
NOTHING = ("No Python changed against the base commit, so nothing was tested and nothing was spent.",
           "Call again once the session has changed Python.")


def _asked(repo: str, task: str, base: str, session: Path) -> dict:
    """What the session's first call gave, which later calls cannot move: ``repo_tests.change`` reads the base as a commit."""
    from g6.workforce import repo_tests

    root, known = repo_tests.checked_repo(repo), session / "task.json"
    if known.is_file():
        asked = json.loads(known.read_text(encoding="utf-8"))
        if asked["repo"] != str(root):
            raise repo_tests.RepoRefused("G6_E_INPUT_VALIDATION", "this session belongs to another repository")
        return asked
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    asked = {"repo": str(root), "task": task, "base": repo_tests.change(root, base).base_commit}
    known.write_text(json.dumps(asked), encoding="utf-8")
    return asked


def _evidence(state: dict, base: str) -> list[dict[str, Any]]:
    """Each review that ran: what it said, and what G6's own runs of the reviewers' tests did."""
    return [{"source": "g6.harness.completion_loop", "summary": f"review {n}: {one['verdict']}; tests: {one['results']}"[:200],
             "authority": "host-observed", "artifact_digest": one["patch"], "base_commit": base,
             "scope": "these reviewers' tests on this patch, and nothing else"}
            for n, one in enumerate(state.get("reviews") or [], 1)]


def step(repo: str, task: str, session: str, base: str = "HEAD", reason: str = "") -> dict:
    """One step of the loop, for a job over MCP: see the module's docstring."""
    from g6.core.door import blocked_reply
    from g6.core.reliability_envelope import envelope_fields, warning_card
    from g6.harness import completion_hook, completion_loop as loop
    from g6.workforce import repo_tests

    if not task.strip() or not session.strip():
        return blocked_reply("[G6_E_INPUT_VALIDATION] a task and a session are needed: name one session for one task")
    folder = completion_hook.folder(f"mcp:{session}")
    request_id = uuid.uuid4().hex[:12]
    try:
        asked = _asked(repo, task, base, folder)
        done = loop.advance(asked["repo"], asked["task"], asked["base"], folder, said=reason)
    except repo_tests.RepoRefused as exc:
        return blocked_reply(f"[{exc.code}] {exc}", request_id=request_id)
    state = loop._load(folder)
    evidence, saved = _evidence(state, asked["base"]), folder / "report.md"
    text = done.text or (saved.read_text(encoding="utf-8") if saved.is_file() else "")
    said = {"action": done.action, "text" if done.action == loop.CONTINUE else "report": text}
    if done.code:
        refused = blocked_reply(f"[{done.code}] {state.get('stop') or text}"[:600], request_id=request_id)
        return {**refused, "run_id": folder.name, "evidence": evidence, **said}
    card = SENDS_BACK if done.action == loop.CONTINUE else ENDED if text else NOTHING
    return {"ok": True, **said, **envelope_fields(
        reliability_label="qualified-draft", warning_card=warning_card(CODE, *card), evidence=evidence, request_id=request_id,
        run_id=folder.name, degradation_reason=card[0][:300])}


__all__ = ["CODE", "step"]
