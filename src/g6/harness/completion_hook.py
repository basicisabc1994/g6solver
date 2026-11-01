# SPDX-License-Identifier: Apache-2.0
"""The completion loop's hooks for Claude Code (``plugins/g6``): ``python -P -m g6.harness.completion_hook prompt`` runs on
``UserPromptSubmit`` and ``... stop`` on ``Stop``, each reading the hook's JSON on its standard input. -P keeps the
project's folder off the import path, so nothing in it runs in G6's process.

``prompt``: what the person asks before the loop starts testing is the task, and HEAD at their first prompt the base commit,
so a commit the agent makes later cannot move it. With ``G6_LOOP_SCOUTS=before_fix`` in the person's key file, the first
prompt also runs the scouts (``completion_loop.prepare``) and gives the agent what they found as ``additionalContext``.
Nothing heavy is imported until a hook has work to do. A prompt after a loop has ended arms a new one (``_again``), so one
ended loop does not leave the rest of a session unreviewed. Its base is HEAD at that prompt, and it has a folder of its own
(``current``).

``stop``: ``completion_loop.advance``, for a session with a task; with none, because no prompt hook saw it, the task is read
from the transcript and HEAD is the base. ``{"decision": "block", "reason": ...}`` hands the repair text back to the agent;
when the stop rule fires the turn ends and ``systemMessage`` shows the report. What the agent said as it stopped goes with
each call, from the hook's ``last_assistant_message`` where Claude Code gives one and else from the transcript, as it is the
agent's reason if it left the code as it was. Where a loop did not run, ``systemMessage`` says what did not run and why, in
one line: a turn never ends with the work silently untested.

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
#: What a stop says with no repository to test in.
NO_REPOSITORY = ("G6's completion loop did not run: this folder is not a git repository with a commit, so there is no base to "
                 "test the work against. Nothing was tested.")


def folder(session_id: str) -> Path:
    """A session's state folder."""
    return state_path("completion_loop", hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:32])


def current(session_id: str, asked: dict) -> Path:
    """The folder of the session's current loop. Its first keeps the session's own, which also holds ``task.json``. One a later
    prompt armed has its own, named by the session, the base at that prompt and its place in the session: two loops on one
    base, with nothing committed between them, are two runs, and each keeps its rounds and its row."""
    run = asked.get("run", 0)
    return folder(f"{session_id}:{asked['base']}:{run}") if run else folder(session_id)


def _here(cwd: str) -> tuple[Path, str] | None:
    """The repository at ``cwd`` and its HEAD; None outside a repository with a commit, where there is nothing to test the
    work against."""
    from g6.workforce import repo_tests

    try:
        top = repo_tests._git(Path(cwd), "rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
        repo = repo_tests.checked_repo(top)
        return repo, repo_tests._git(repo, "rev-parse", "--verify", "-q", "HEAD").decode().strip()
    except repo_tests.RepoRefused:
        return None


def _record(session: Path, cwd: str, task: str) -> dict | None:
    """The task, and HEAD now as its base commit, in the session's folder; None outside a repository with a commit."""
    here = _here(cwd)
    if here is None:
        return None
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    asked = {"repo": str(here[0]), "task": task, "base": here[1]}
    (session / "task.json").write_text(json.dumps(asked), encoding="utf-8")
    return asked


def _again(event: dict, asked: dict, ended: dict, task: str) -> None:
    """A new loop after one that ended. Its base is HEAD now, and its task this prompt, with the earlier task if that base is
    the same, as the patch still holds that work. On that same base its state names the patch the last review read, so a
    "thanks" spends no review on it. On another base it names nothing: the same diff on a parent no review saw is another
    patch. Nor does it after a loop that ended with a stable code: that came to no result, so the next stop may try again. The
    scouts do not run here, as they do at a session's first prompt: this may be any prompt at all. A row the ended loop still
    owes is tried first, and if the log keeps it out it stays owed: the new loop's calls reach it by its mark, as they no
    longer reach that loop's folder. Outside a repository nothing moves, and the stop says so."""
    session_id, session = event["session_id"], folder(event["session_id"])
    if "row_due" in ended:
        from g6.harness import completion_loop as loop

        loop.settle()
    here = _here(event["cwd"])
    if here is None:
        return
    repo, base, reviews = str(here[0]), here[1], ended.get("reviews") or []
    same = (repo, base) == (asked["repo"], asked["base"])
    fresh = {"repo": repo, "task": f"{asked['task']}\n\n{task}" if same else task, "base": base,
             "run": asked.get("run", 0) + 1}
    run = current(session_id, fresh)
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    read = {"reviewed": reviews[-1]["patch"]} if same and reviews and not ended.get("code") else {}
    (run / "state.json").write_text(json.dumps(read), encoding="utf-8")
    (session / "task.json.new").write_text(json.dumps(fresh), encoding="utf-8")
    os.replace(session / "task.json.new", session / "task.json")


def _lines(transcript: object) -> list[dict]:
    """Each item in the session's transcript, skipping any line it cannot read ([] when the file cannot be read)."""
    try:
        lines = Path(str(transcript)).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[dict] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


def _items(transcript: object, kind: str) -> list[list[str]]:
    """The text of each item of ``kind`` in the session's transcript, skipping meta items."""
    found: list[list[str]] = []
    for item in _lines(transcript):
        if item.get("type") != kind or item.get("isMeta"):
            continue
        said = (item.get("message") or {}).get("content")
        found.append([said] if isinstance(said, str) else [part.get("text") or "" for part in said or []
                                                           if isinstance(part, dict) and part.get("type") == "text"])
    return found


def _prompts(transcript: object) -> str:
    """What the person asked, from the session's transcript: for a task no prompt hook saw ("" when it cannot be read)."""
    return "\n\n".join(text.strip() for item in _items(transcript, "user") for text in item if text.strip())


def _said(event: dict) -> str:
    """What the agent said as it stopped: the hook's own field, else the transcript's last text ("" with neither)."""
    said = event.get("last_assistant_message")
    if isinstance(said, str) and said.strip():
        return said
    spoken = ["\n".join(text for text in item if text.strip()) for item in _items(event.get("transcript_path"), "assistant")]
    return next((text for text in reversed(spoken) if text), "")


def _model(event: dict) -> str:
    """The agent's model, for the run's row: the latest the transcript names, as a session may change it ("" with none). A name
    in angle brackets is Claude Code's own, for a message no model wrote."""
    named = ((item.get("message") or {}).get("model") for item in reversed(_lines(event.get("transcript_path")))
             if item.get("type") == "assistant")
    return next((one for one in named if isinstance(one, str) and one and not one.startswith("<")), "")


def on_prompt(event: dict) -> dict:
    session = folder(event["session_id"])
    task, known = str(event.get("prompt") or "").strip(), session / "task.json"
    if not task:
        return {}
    if known.exists():
        asked = json.loads(known.read_text(encoding="utf-8"))
        path = current(event["session_id"], asked) / "state.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if state.get("done"):
            _again(event, asked, state, task)
        elif "started" not in state:
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
        return {"systemMessage": NO_REPOSITORY}
    from g6.harness import completion_loop as loop

    asked = json.loads((session / "task.json").read_text(encoding="utf-8"))
    step = loop.advance(asked["repo"], asked["task"], asked["base"], current(event["session_id"], asked), said=_said(event),
                        agent=_model(event))
    if step.action == loop.CONTINUE:
        return {"decision": "block", "reason": step.text}
    said = step.text or step.note
    return {"systemMessage": said} if said else {}


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
