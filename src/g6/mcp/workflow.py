# SPDX-License-Identifier: Apache-2.0
"""The workflow record over MCP (design section 17): observing G6's own calls, and the person's lifecycle tools.

``install`` adds a middleware that records each tool call naming a repository and a task. It sits inside the consent
egress (``g6.mcp.cloud_consent.install``, added first, so it is the last thing to touch a reply) and reads the call's
arguments and its time only, never its reply. ``finishing`` and ``stepped`` wrap the two review paths, so a finished
review job and a loop step write their event boundaries; neither can change what the review returns, and a record that
cannot be written adds one note and nothing else.

The three lifecycle tools do their work locally whatever the client may see: ``g6_workflow_export`` writes
``inspection.json`` for the person to open, ``g6_workflow_correct`` applies the ``corrections.json`` the person wrote,
``g6_workflow_delete`` removes a run, a task or the project's record once the person confirms in the client's own
dialog. Every reply is bound to the source ``workflow:<project-id>`` (the tools' declaration names it, and the reply
is bound again here), so without the person's grant for that source and the client's provider the reply is section
16's withholding notice plus one fixed sentence about the files (``FILES``), which names no path and no id. No reply
repeats what either file holds. No argument confirms a deletion or carries a correction.
"""
from __future__ import annotations

import json
import time
from typing import Any

from g6.core import cloud_consent as cloud
from g6.harness.workflow import adapters, lifecycle, policy
from g6.harness.workflow.record import ProjectRecord, RecordUnreadable, now, project_id, workflow_source

CODE = "G6_E_WORKFLOW_RECORD"
TOOLS = ("g6_workflow_export", "g6_workflow_correct", "g6_workflow_delete")
#: Said with a withheld reply. Fixed words: no path, no project id, nothing from the record.
FILES = ("The workflow record stays on this computer. The person can open inspection.json, and write corrections.json, "
         "in this project's folder under workflow in G6's state folder. To share the record with this provider, the "
         "person grants the source named workflow: followed by this repository's identifier (g6_cloud_grant).")
#: Added to what g6_help returns over MCP.
HELP = ("G6 can keep a local record of its own review work on a task, off until the person sets G6_WORKFLOW_RECORD=on "
        "where the server is registered. g6_workflow_export writes inspection.json for the person to open, "
        "g6_workflow_correct applies the corrections.json they wrote in the same folder, and g6_workflow_delete "
        "removes a run, a task or the project's record after they confirm in the client's own dialog. Never read "
        "those files back to the person and never answer the dialog.")
NOT_CHANGED = ("Nothing was deleted. Without a confirmation in the client's own dialog G6 deletes nothing; the person "
               'can instead add {"action": "delete", "scope": "run", "task" or "project", "id": <the run or task id '
               'from inspection.json>, "by": <their name>} to corrections.json and ask G6 to apply corrections.')
DELETE_FORM = {"type": "object", "properties": {
    "confirm": {"type": "string", "enum": ["delete", "keep"],
                "description": "Choose delete to remove it. This cannot be undone."},
    "name": {"type": "string", "description": "Your name (recorded as supplied, not authenticated)"}},
    "required": ["confirm", "name"]}
SCOPE_WORDS = {"run": "one run of a task", "task": "everything recorded for one task",
               "project": "everything recorded for this repository"}


# -- observing G6's own calls -----------------------------------------------------------------------------------
def _noted(reply: Any, note: str) -> Any:
    """``reply`` with one ``workflow`` note when the record could not be written; its sources kept as they were."""
    if not note or not isinstance(reply, dict):
        return reply
    refs, metadata = cloud.output_sources(reply), cloud.output_metadata(reply)
    noted = {**reply, "workflow": {"note": note}}
    return cloud.bind_output(noted, refs, metadata=metadata) if refs is not None else noted


def finishing(finish: Any, repo: str, task: str) -> Any:
    """The compact step of a review job (``review_reply.finish``), followed by the record of that finished review."""
    started_at, began = now(), time.monotonic()

    def done(reply: Any, job_id: str) -> Any:
        shown = finish(reply, job_id)
        if not policy.enabled()[0]:
            return shown
        where = (shown.get("full_assessment") or {}).get("file") if isinstance(shown, dict) else None
        note = adapters.observe_review(repo, task, job_id, reply, started_at=started_at, finished_at=now(),
                                       seconds=time.monotonic() - began, saved=where)
        return _noted(shown, note)
    return done


def stepped(step: Any, repo: str, task: str, session: str, base: str = "HEAD", reason: str = "") -> Any:
    """One step of the completion loop over MCP (``review_loop.step``), followed by the record of where it stands."""
    result = step(repo, task, session, base, reason)
    if not policy.enabled()[0] or not isinstance(result, dict) or "action" not in result:
        return result
    from g6.harness import completion_hook

    note = adapters.observe_step(repo, task, completion_hook.folder(f"mcp:{session}"), _Step(result))
    return _noted(result, note)


class _Step:
    """What a loop step's reply says of the step, for the adapter."""

    def __init__(self, result: dict) -> None:
        self.action = str(result.get("action") or "")
        self.ending = str(result.get("ending") or "")
        found = adapters._CODE.match(str(result.get("error") or ""))
        self.code = found[1] if found else ""


def install(server: Any) -> None:
    """Record each call of a tool whose arguments name a repository and a task. Arguments and time only."""
    import anyio
    from fastmcp.server.middleware import Middleware

    class WorkflowObserver(Middleware):
        async def on_call_tool(self, context: Any, call_next: Any) -> Any:
            started_at, began = now(), time.monotonic()
            try:
                return await call_next(context)
            finally:
                try:
                    args = context.message.arguments or {}
                    repo, task = args.get("repo"), args.get("task")
                    if policy.enabled()[0] and isinstance(repo, str) and isinstance(task, str) and task.strip() \
                            and context.message.name not in TOOLS:
                        # a request is one call of one session of this process: its id alone repeats across sessions
                        ctx = context.fastmcp_context
                        request = f"{adapters.PROCESS}:{id(getattr(ctx, 'session', None))}:{getattr(ctx, 'request_id', '')}"
                        digest = cloud.sha256(json.dumps(args, sort_keys=True, default=str))
                        await anyio.to_thread.run_sync(lambda: adapters.observe_call(
                            repo, task, context.message.name, request, digest, started_at=started_at,
                            seconds=time.monotonic() - began))
                except Exception:  # noqa: BLE001 - observing a call never changes the call
                    pass

    server.add_middleware(WorkflowObserver())


# -- the lifecycle tools ----------------------------------------------------------------------------------------
def _reply(project: str, item: str, result: dict, summary: str, next_step: str) -> dict:
    """The one way a workflow tool replies: G6's envelope, bound to the project's workflow source, with the fixed
    sentence a withheld reply carries in its place."""
    from g6.core.reliability_envelope import envelope_fields, warning_card

    reply = {"ok": True, "result": result, **envelope_fields(
        reliability_label="qualified-draft", warning_card=warning_card(CODE, summary, next_step), evidence=[])}
    source = cloud.Source.text(workflow_source(project), item, json.dumps(result, sort_keys=True, default=str))
    return cloud.bind_output(reply, (source,), metadata={"workflow_files": FILES})


def _project(repo: str) -> str | None:
    """The project for ``repo``; None when it is not a full path to a folder."""
    from pathlib import Path

    try:
        return project_id(repo) if isinstance(repo, str) and Path(repo).is_absolute() and Path(repo).is_dir() else None
    except (ValueError, OSError):
        return None


def _refused(why: str) -> dict:
    from g6.core.door import blocked_reply
    from g6.mcp.consent_controls import public_instructions

    return public_instructions(blocked_reply(f"[G6_E_INPUT_VALIDATION] {why}"))


def _unreadable(project: str, exc: RecordUnreadable) -> dict:
    return _reply(project, "unreadable", {"status": "unreadable", "code": exc.code, "why": exc.why},
                  "A part of the workflow record cannot be read; nothing was changed.",
                  "Delete that part of the record (g6_workflow_delete), or leave it: the review is not affected.")


def export(repo: str) -> dict:
    project = _project(repo)
    if project is None:
        return _refused("repo must be the full path of the repository's folder")
    try:
        lifecycle.expire(project)
        path = lifecycle.export(project)
        record = ProjectRecord(project)
        tasks = record.tasks()
        events = record.events()
    except RecordUnreadable as exc:
        return _unreadable(project, exc)
    adapters.observe_project(repo, "inspection", cloud.sha256(str(path) + now()), at=now())
    on, why = policy.enabled()
    return _reply(project, "export", {
        "status": "written", "file": str(path), "tasks": len(tasks), "runs": len({e.run for e in events}),
        "events": len(events), "recording": on, **({"recording_note": why} if why else {}),
        "policy_version": policy.POLICY_VERSION, "expires_after_days": policy.RETENTION_DAYS["exports"]},
        "inspection.json was written for the person to open. It holds codes, counts, times and digests about G6's "
        "own review work, and no task text or code.", "Tell the person where the file is. Do not read it back to them.")


def correct(repo: str) -> dict:
    project = _project(repo)
    if project is None:
        return _refused("repo must be the full path of the repository's folder")
    try:
        lifecycle.expire(project)
        result = lifecycle.apply_corrections(project)
    except RecordUnreadable as exc:
        return _unreadable(project, exc)
    if result.get("applied"):
        adapters.observe_project(repo, "correction_applied", cloud.sha256(json.dumps(result) + now()), at=now(),
                                 data={"applied": result["applied"]})
    return _reply(project, "correct", result,
                  "corrections.json was read from the project's folder; a correction is added beside an entry and "
                  "never changes it.", "Tell the person what was applied, or which line or entry G6 could not read.")


async def delete(repo: str, ctx: Any, scope: str = "task", task: str = "", run: str = "") -> dict:
    from g6.mcp.consent import elicit
    from g6.mcp.consent_transport import consent_form

    project = _project(repo)
    if project is None or scope not in lifecycle.SCOPES:
        return _refused("repo must be the full path of the repository's folder, and scope one of run, task, project")
    try:
        ident = {"project": lambda: project, "run": lambda: run,
                 "task": lambda: adapters.identities(repo, task)[1] if task.strip() else ""}[scope]()
    except Exception:  # noqa: BLE001 - a repository G6 cannot read names no task
        ident = ""
    if not ident:
        return _refused("name the task (its text, as given to the review) or the run (its id in inspection.json)")
    message = (f"Delete {SCOPE_WORDS[scope]} from G6's local workflow record? G6 removes those files and the "
               "inspection file made from them, and keeps one receipt that says a deletion happened. Saved reviews, "
               "questions and answers are not touched. Nothing already shared is recalled. This cannot be undone.")
    with consent_form():     # fixed words only: the dialog shows nothing from the record
        action, content, _error = await elicit(ctx, message, DELETE_FORM)
    name = content.get("name") if isinstance(content, dict) else None
    if action != "accept" or not isinstance(content, dict) or content.get("confirm") != "delete" \
            or not isinstance(name, str) or not name.strip():
        return _reply(project, "delete", {"status": "not_changed", "dialog": action, "removed": 0, "how": NOT_CHANGED},
                      "Nothing was deleted: the person did not confirm it in the client's dialog.",
                      "Do not ask again. The person can use corrections.json if their client has no dialog.")
    result = lifecycle.delete(project, scope=scope, ident=ident, by=name.strip())
    return _reply(project, "delete", result, "The person confirmed the deletion in the client's dialog."
                  if result["status"] == "deleted" else "Nothing was deleted.",
                  "Tell the person what was removed. Saved reviews and answers are kept by their own rules.")


def register(server: Any, context_type: Any) -> None:
    """Add the lifecycle tools and the call observer to ``server``."""
    from g6.core.consent_declarations import TOOLS as DECLARED

    def g6_workflow_export(repo: str) -> dict:
        """Write inspection.json, what G6 recorded about its own review work in this repository (codes, counts, times
        and digests; no task text or code), into the project's folder under G6's state folder, for the person to open.
        repo: the repository's folder, a full path. The reply says where the file is and how much it holds; it never
        repeats the file. Without the person's grant for the workflow source the file is still written and the reply
        is withheld. Runs no model and no code."""
        return export(repo)

    def g6_workflow_correct(repo: str) -> dict:
        """Apply the corrections.json the person wrote in the project's folder (beside inspection.json): each entry
        annotates or supersedes one recorded entry, or asks for a deletion. It takes no correction as an argument: you
        cannot correct the record for the person. A file G6 cannot read changes nothing, and the reply names the line
        or entry, never the content. repo: the repository's folder, a full path."""
        return correct(repo)

    async def g6_workflow_delete(repo: str, ctx: Any, scope: str = "task", task: str = "", run: str = "") -> dict:
        """Delete part of G6's local workflow record after the person confirms in the client's own dialog: one run
        (scope "run", run: its id from inspection.json), one task (scope "task", task: the task's text as given to
        g6_review_patch) or the whole project record (scope "project"). No argument confirms it and you never answer
        the dialog; a decline, a cancel or a client without a dialog deletes nothing. Saved reviews, questions and
        answers are kept. repo: the repository's folder, a full path."""
        return await delete(repo, ctx, scope=scope, task=task, run=run)

    g6_workflow_delete.__annotations__["ctx"] = context_type
    for tool in (g6_workflow_export, g6_workflow_correct, g6_workflow_delete):
        tool._g6_declaration = DECLARED[tool.__name__]
        server.tool()(tool)
    install(server)


__all__ = ["CODE", "FILES", "HELP", "TOOLS", "correct", "delete", "export", "finishing", "install", "register",
           "stepped"]
