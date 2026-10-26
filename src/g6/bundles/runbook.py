# SPDX-License-Identifier: Apache-2.0
"""Bundle: Runbook safety check — find the risky steps in an operations runbook before anyone runs it.

Needs a model, used only to read the runbook into structured steps (action, target, reversible?, reach,
is it a check/backup?). The decisions are G6's: every step goes through ``g6.action_gating`` with the
facts the model read, whatever kind the model gives it, and fixed rules check the structure (a rollback
section, a final verification). A kind is the model's reading and clears nothing (RUNBOOK-01): an
irreversible step is cleared only by a backup you name for its target in the backups field, which G6
shows but does not open. Facts read by a model can be wrong, so each row shows them and the best label is
``qualified-draft``.
"""
from __future__ import annotations

import re
from typing import Any

from g6.bundles import Bundle, BundleResult, Field, weakest

SYSTEM = (
    "Read the runbook and list its steps in order. Reply with one JSON object: "
    '{"steps": [{"n": 1, "text": "<step as written>", "action_type": "<verb_noun, e.g. restart_service>", '
    '"target": "<what it acts on>", "reversible": "yes|partly|no|unknown", '
    '"reach": "none|local|account|organization|external_party|public|unknown", '
    '"kind": "change|check|backup|rollback|communication"}], "has_rollback_section": true|false}. '
    "Use unknown when the runbook does not say."
)
_REV = {"yes": "reversible", "partly": "partially_reversible", "no": "irreversible"}
_REACH = ("none", "local", "account", "organization", "external_party", "public")
_BACKUP = re.compile(r"^\s*(?P<target>\S.*?):\s+(?P<where>\S.*?)\s*$")
_BACKUP_HELP = "One line per backup: what it covers, a colon, then where it is, for example 'users_old: s3://ops/users_old.sql'."


def _backups(text: str) -> tuple[dict[str, str], list[str]]:
    """The backups the request names, by target, and the lines that could not be read (never guessed)."""
    named: dict[str, str] = {}
    unread: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _BACKUP.match(line)
        if m:
            named[m["target"].strip().lower()] = m["where"]
        else:
            unread.append(line.strip())
    return named, unread


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    from g6.action_gating import evaluate_action
    from g6.core.llm import LLMError
    from g6.reasoning import first_json_object

    text = inputs.get("runbook", "").strip()
    if len(text.split()) < 10:
        return BundleResult("blocked-escalated", "Paste the runbook (at least a few steps).")
    if llm is None:
        return BundleResult("blocked-escalated", "Choose an AI model first (AI model panel above).")
    try:
        parsed = first_json_object(llm.complete(SYSTEM, text, max_tokens=3000))
    except LLMError as exc:
        return BundleResult("blocked-escalated", f"The AI model could not read the runbook: {exc}")
    except ValueError:
        return BundleResult("blocked-escalated", "The AI model's reply was not in the expected format; try again.")
    steps = [s for s in parsed.get("steps") or [] if isinstance(s, dict) and str(s.get("text", "")).strip()]
    if not steps:
        return BundleResult("blocked-escalated", "No steps could be read from the runbook.")
    backups, unread = _backups(inputs.get("backups", ""))
    rows, states = [], []
    for i, s in enumerate(steps, 1):
        kind = str(s.get("kind", "change")).lower()
        action: dict[str, Any] = {"action_type": str(s.get("action_type") or "unknown_action")[:80],
                                  "target_resource": str(s.get("target") or "unknown")[:200]}
        if str(s.get("reversible", "")).lower() in _REV:
            action["reversibility"] = _REV[str(s["reversible"]).lower()]
        if str(s.get("reach", "")).lower() in _REACH:
            action["blast_radius"] = str(s["reach"]).lower()
        problems = []
        ev = evaluate_action(action)   # every step, whatever kind the model gives it (RUNBOOK-01)
        decision, state = ev.gate_decision.decision.replace("_", " "), ev.completion_state
        backup = backups.get(action["target_resource"].strip().lower())
        if action.get("reversibility") == "irreversible" and backup is None:
            problems.append("irreversible, and no backup named for it")
            state = "blocked-escalated"
        if decision in ("block", "require human review", "require confirmation"):
            problems.append("needs a named person to approve at this step")
        states.append(state)
        rows.append({
            "step": i, "as written": str(s["text"]).strip()[:300], "G6 decision": decision,
            "facts read by the model": f"{action['action_type']} on {action['target_resource']}; reversible: "
                                       f"{s.get('reversible', 'unknown')}; reach: {s.get('reach', 'unknown')}; "
                                       f"kind (the model's reading): {kind}",
            "backup": f"{backup} (yours, not opened by G6)" if backup else "—",
            "problems": "; ".join(problems) or "—", "label": state,
        })
    notes = ["Facts were read from your text by an AI model; check the 'facts' column — a wrong fact changes the decision.",
             "A step the model calls a check or a backup is gated like any other and clears nothing; only a backup you "
             "name in the backups field clears an irreversible step on its target. G6 does not open or check it."]
    if unread:
        states.append("blocked-escalated")
        notes.insert(0, "G6 could not read these backup lines, so they clear nothing: "
                        + "; ".join(repr(u) for u in unread) + ". " + _BACKUP_HELP)
    if not parsed.get("has_rollback_section"):
        states.append("blocked-escalated")
        notes.insert(0, "No rollback section: add how to undo the change if it goes wrong.")
    if not any(str(s.get("kind", "")).lower() == "check" for s in steps[-2:]):
        states.append("blocked-escalated")
        notes.insert(0, "No final check: add a step that confirms the system works afterwards.")
    flagged = sum(r["problems"] != "—" for r in rows)
    return BundleResult(weakest(states), f"{len(rows)} steps read; {flagged} have problems.", rows, notes)


BUNDLE = Bundle(
    id="runbook",
    title="Runbook safety check",
    summary="Find the risky steps in an operations runbook before anyone runs it.",
    fields=(Field("runbook", "Your runbook",
                  placeholder="1. Announce maintenance in #ops\n2. Stop the web service\n3. Drop the old_sessions "
                              "table\n4. Deploy v2.3\n5. Start the web service",
                  help="Paste the steps as you would hand them to the person on call."),
            Field("backups", "Backups already taken (optional)",
                  placeholder="users_old: s3://ops-backups/users_old-2026-10-23.sql",
                  help=_BACKUP_HELP + " Only a backup named here clears an irreversible step on that target; G6 "
                       "shows it but does not open or check it.")),
    run=run,
    needs_model=True,
)
