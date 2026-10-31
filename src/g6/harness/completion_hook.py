# SPDX-License-Identifier: Apache-2.0
"""The completion loop's hooks for Claude Code (``plugins/g6``): ``python -P -m g6.harness.completion_hook prompt`` runs on
``UserPromptSubmit`` and ``... stop`` on ``Stop``, each reading the hook's JSON on its standard input. -P keeps the
project's folder off the import path, so nothing in it runs in G6's process.

``prompt``: what the person asks before the loop starts is the task, and HEAD at their first prompt the base commit, so a
commit the agent makes later cannot move it. It prints nothing, as what a prompt hook prints is added to the agent's
context. Nothing heavy is imported until a hook has work to do.

``stop``: ``completion_loop.advance``, for a session with a task. ``{"decision": "block", "reason": ...}`` hands the repair
text back to the agent; when the stop rule fires the turn ends and ``systemMessage`` shows the report.

The hooks are a convenience, not a security boundary. A failure never holds the person's turn: it ends with one line on
standard error, and any exit but 2 lets the turn end. A session's folder is named by a digest, so nothing in a hook's
input can name a path. Inside a scout's session (``CHILD``) they do nothing, whatever settings that session reads.
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


def on_prompt(event: dict) -> dict:
    session = folder(event["session_id"])
    task, known = str(event.get("prompt") or "").strip(), session / "task.json"
    if not task or (session / "state.json").exists():
        return {}
    if known.exists():
        asked = json.loads(known.read_text(encoding="utf-8"))
        known.write_text(json.dumps({**asked, "task": f"{asked['task']}\n\n{task}"}), encoding="utf-8")
        return {}
    from g6.workforce import repo_tests

    try:
        top = repo_tests._git(Path(event["cwd"]), "rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
        repo = repo_tests.checked_repo(top)
        base = repo_tests._git(repo, "rev-parse", "--verify", "-q", "HEAD").decode().strip()
    except repo_tests.RepoRefused:     # no repository, or no commit yet: nothing to test the work against
        return {}
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    known.write_text(json.dumps({"repo": str(repo), "task": task, "base": base}), encoding="utf-8")
    return {}


def on_stop(event: dict) -> dict:
    session = folder(event["session_id"])
    if not (session / "task.json").is_file():
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
