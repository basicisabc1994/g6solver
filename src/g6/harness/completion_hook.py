# SPDX-License-Identifier: Apache-2.0
"""The completion loop's hooks for Claude Code (``plugins/g6``): ``python -P -m g6.harness.completion_hook prompt`` runs on
``UserPromptSubmit`` and ``... stop`` on ``Stop``, each reading the hook's JSON on its standard input. -P keeps the
project's folder off the import path, so nothing in it runs in G6's process.

``prompt``: what the person asks before the loop starts testing is the task, and HEAD at their first prompt the base commit,
so a commit the agent makes later cannot move it. With ``G6_LOOP_SCOUTS=before_fix`` in the person's key file, the first
prompt also runs the scouts (``completion_loop.prepare``) and gives the agent what they found as ``additionalContext``.
Nothing heavy is imported until a hook has work to do.

``stop``: ``completion_loop.advance``, for a session with a task; with none, because no prompt hook saw it, the task is read
from the transcript and HEAD is the base. ``{"decision": "block", "reason": ...}`` hands the repair text back to the agent;
when the stop rule fires the turn ends and ``systemMessage`` shows the report.

The hooks are a convenience, not a security boundary. A failure never holds the person's turn or the agent's: it ends with
one line on standard error, and only exit 2 would block, which these never give. A session's folder is named by a digest,
so nothing in a hook's input can name a path. Inside a scout's session (``CHILD``) they do nothing, whatever settings that
session reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from g6.core.storage import state_path

#: Set in a scout's session.
CHILD = "G6_COMPLETION_LOOP_CHILD"


def folder(session_id: str) -> Path:
    """A session's state folder."""
    return state_path("completion_loop", hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:32])


def _record(session: Path, cwd: str, task: str) -> dict | None:
    """The task, and HEAD now as its base commit, in the session's folder; None outside a repository with a commit, where
    there is nothing to test the work against."""
    from g6.workforce import repo_tests

    try:
        top = repo_tests._git(Path(cwd), "rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
        repo = repo_tests.checked_repo(top)
        base = repo_tests._git(repo, "rev-parse", "--verify", "-q", "HEAD").decode().strip()
    except repo_tests.RepoRefused:
        return None
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    asked = {"repo": str(repo), "task": task, "base": base}
    (session / "task.json").write_text(json.dumps(asked), encoding="utf-8")
    return asked


def _prompts(transcript: object) -> str:
    """What the person asked, from the session's transcript: for a task no prompt hook saw ("" when it cannot be read)."""
    try:
        lines = Path(str(transcript)).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    found: list[str] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict) or item.get("type") != "user" or item.get("isMeta"):
            continue
        said = (item.get("message") or {}).get("content")
        found += [said] if isinstance(said, str) else [part.get("text") or "" for part in said or []
                                                       if isinstance(part, dict) and part.get("type") == "text"]
    return "\n\n".join(text.strip() for text in found if text.strip())


def on_prompt(event: dict) -> dict:
    session = folder(event["session_id"])
    task, known = str(event.get("prompt") or "").strip(), session / "task.json"
    if not task:
        return {}
    if known.exists():
        state = session / "state.json"
        if not state.exists() or "started" not in json.loads(state.read_text(encoding="utf-8")):
            asked = json.loads(known.read_text(encoding="utf-8"))
            known.write_text(json.dumps({**asked, "task": f"{asked['task']}\n\n{task}"}), encoding="utf-8")
        return {}
    asked = _record(session, event["cwd"], task)
    if asked is None:
        return {}
    from g6.harness import completion_loop as loop

    context = loop.prepare(asked["repo"], task, asked["base"], session)
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}} if context else {}


def on_stop(event: dict) -> dict:
    session = folder(event["session_id"])
    if not (session / "task.json").is_file() and _record(session, event["cwd"], _prompts(event.get("transcript_path"))) is None:
        return {}
    from g6.harness import completion_loop as loop

    asked = json.loads((session / "task.json").read_text(encoding="utf-8"))
    step = loop.advance(asked["repo"], asked["task"], asked["base"], session)
    if step.action == loop.CONTINUE:
        return {"decision": "block", "reason": step.text}
    return {"systemMessage": step.text} if step.text else {}


HOOKS = {"prompt": on_prompt, "stop": on_stop}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if os.environ.get(CHILD) or not args or args[0] not in HOOKS:
        return 0
    try:
        said = HOOKS[args[0]](json.loads(sys.stdin.buffer.read().decode("utf-8")))
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the person's turn ends
        why = " ".join(f"{type(exc).__name__}: {exc}".split())
        print(f"G6's completion loop stopped, so this work is not tested: {why}"[:300], file=sys.stderr)
        return 1
    if said:
        print(json.dumps(said))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
