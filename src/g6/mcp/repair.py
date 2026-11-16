# SPDX-License-Identifier: Apache-2.0
"""The repair block: every refused or blocked G6 reply over MCP says who repairs it and how (plan revision 8, the
repair loop).

Each refused reply gains one additive field, ``repair``, computed from the refusal declarations
(``g6.core.refusals``, read through ``g6.core.selector.remedy_for``, so only the reply's own envelope is read)::

    {"code": "G6_E_...", "fix_by": "input" | "person" | "retry" | "none" | "tool:<name>",
     "by": "agent" | "person", "step": "<G6's words>", "call": {"tool": ..., "arguments": {...}} | None,
     "fill": [<fields to fill>], "validated": bool, "retries_left": int}

``by`` is ``person`` for every ``person`` and ``none`` class, and once ``retries_left`` is 0; ``call`` is a template
G6 checked against the tool's schema (``validated``) for ``input``, ``tool`` and ``retry``, and ``None`` for the
person, except for the consent code, whose dialog (``g6_cloud_grant``) the agent opens and the person answers there.
Nothing here grants anything or answers for the person.

Bounded retries: a refusal is never retried unchanged. ``retries_left`` counts down per (tool, code) in each MCP
session from ``MAX_RETRIES``; a call sent again with the same arguments after a refusal (a ``retry`` class aside)
leaves none. The digest of a refused call's arguments is kept in memory only, for that comparison.

The repair log (``g6.core.repair_log``, ``repairs/repairs.jsonl`` under G6's state folder) is append-only and holds
codes and ids only, never an argument or a word of text: one ``refused`` row per repair block, and one ``next`` row
saying whether the next call of the same tool in that session was refused again (``not``) or not (``resolved``), or,
when the block sent the agent to the person, ``person``. ``repair_stats`` reads it (resolution rate by code).

The block is added in the one place every MCP tool reply passes through, on the reply's frozen snapshot
(``g6.mcp.cloud_consent``), so a repaired reply carries exactly the sources its snapshot carries. A job's reply (it
holds ``job_id``) is bound to the digest of the result the job recorded, so it gets no block; its refusal is
repaired through ``g6_select``. A reply withheld for consent gets the person's block where it is made
(``withheld_reply``).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
import weakref
from typing import Any

from g6.core.repair_log import append as _log
from g6.core.repair_log import repair_stats

MAX_RETRIES = 2
_LOCK = threading.Lock()
_SESSIONS: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()
_LOCAL: dict[str, Any] = {}          # the store of a call that comes with no session (in-process)
_FIELD = re.compile(r"[a-z][a-z0-9_]*")


def _state(session: Any) -> dict[str, Any]:
    if session is None:
        return _LOCAL
    try:
        return _SESSIONS.setdefault(session, {})
    except TypeError:                   # an object that cannot be weakly referenced: kept apart by nothing else
        return _LOCAL


def _digest(arguments: Any) -> str:
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode("utf-8", "surrogatepass")
                          ).hexdigest()


def _inner(reply: dict[str, Any]) -> dict[str, Any]:
    """The reply a finished job wraps, else the reply."""
    return reply["result"] if "job_id" in reply and isinstance(reply.get("result"), dict) else reply


def refused(reply: Any) -> bool:
    """Whether a reply is a refusal: ``blocked-escalated``, or ``ok`` false, itself or in the job it wraps."""
    if not isinstance(reply, dict):
        return False
    inner = _inner(reply)
    return any(isinstance(r, dict) and (r.get("completion_state") == "blocked-escalated" or r.get("ok") is False)
               for r in (reply, inner))


def _envelope(reply: dict[str, Any]) -> str:
    """Only the envelope fields the selector reads (code, state, outcome words, the op), as JSON: nothing an op
    worked on is passed along."""
    inner = _inner(reply)
    small: dict[str, Any] = {}
    for key in ("code", "cloud_consent_code", "completion_state", "ok", "outcome", "ending", "action"):
        if isinstance(inner.get(key), (str, bool)):
            small[key] = inner[key]
    card = inner.get("warning_card")
    if isinstance(card, dict) and isinstance(card.get("code"), str):
        small["warning_card"] = {"code": card["code"]}
    error = inner.get("error")
    if isinstance(error, str):
        small["error"] = error.split("\n", 1)[0][:512]
    elif isinstance(error, dict) and isinstance(error.get("code"), str):
        small["error"] = {"code": error["code"]}
    assessment = inner.get("assessment")
    if isinstance(assessment, dict) and isinstance(assessment.get("outcome"), str):
        small["assessment"] = {"outcome": assessment["outcome"]}
    result = inner.get("result")
    if isinstance(result, dict) and isinstance(result.get("component"), str) and isinstance(result.get("op"), str):
        small["result"] = {"component": result["component"], "op": result["op"]}
    if reply.get("completion_state") == "blocked-escalated" and "completion_state" not in small:
        small["completion_state"] = "blocked-escalated"
    return json.dumps(small)


def _entry(data: dict[str, Any], tool: str) -> dict[str, Any] | None:
    return next((e for e in data["entries"] if e["kind"] == "tool" and e["call"]["tool"] == tool), None)


def _named(reply: dict[str, Any], schema: list[str]) -> list[str]:
    """The fields of the tool's schema that the refusal's own words name (only schema names can come out)."""
    inner = _inner(reply)
    card = inner.get("warning_card") if isinstance(inner.get("warning_card"), dict) else {}
    said = " ".join(str(x) for x in (inner.get("error", ""), card.get("message", ""), card.get("why", ""))
                    if isinstance(x, str))
    words = set(_FIELD.findall(said.lower()))
    return [field for field in schema if field in words]


def block(tool: str, reply: dict[str, Any], *, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The repair block for ``tool``'s refused ``reply``, before any retry is counted (``retries_left`` is
    ``MAX_RETRIES``, or 0 where only the person can act); ``None`` for a reply that is not a refusal."""
    from g6.core import selector
    from g6.skills import corpus

    if not refused(reply):
        return None
    if tool == "g6_cloud_grant" and selector._read(_envelope(reply))["code"] == "G6_E_CLOUD_CONSENT_REQUIRED":
        return {"code": "G6_E_CLOUD_CONSENT_REQUIRED", "fix_by": "person", "by": "person",
                "step": "The person did not share this source, or the client cannot ask in a dialog. "
                        "Stop and tell the person; do not reopen the dialog.",
                "call": None, "fill": [], "validated": False, "retries_left": 0}
    data = data or corpus.load()
    remedy = selector.remedy_for(_envelope(reply), data) or selector.remedy_for(
        json.dumps({"completion_state": "blocked-escalated"}), data)
    fix_by = remedy["fix_by"]
    person = fix_by in ("person", "none") or remedy["tell_person"]
    step = remedy["action"] + (f" The step: {remedy['setup']}." if remedy["setup"] else "")
    call, fill = None, []
    if remedy["call"] and remedy["available"]:
        call = remedy["call"]
        fill = sorted(k for k, v in call.get("arguments", {}).items() if isinstance(v, str) and v.startswith("<"))
    elif fix_by in ("input", "retry") and _entry(data, tool):
        entry = _entry(data, tool)
        call = {"tool": tool, "arguments": dict(entry["call"]["arguments"])}
        named = _named(reply, entry["schema"]["properties"]) if fix_by == "input" else []
        call["arguments"].update({f: f"<{f}>" for f in named if f not in call["arguments"]})
        fill = sorted(set(named) | set(call["arguments"]))
        step += (f" Fix {', '.join(named or fill)} in your call to {tool}, then make it again, changed."
                 if fix_by == "input" else f" Make the same call to {tool} once more.")
    validated = False
    if call is not None:
        if call.get("tool") == "g6_invoke" or "params_json" in call.get("arguments", {}):
            validated = True           # an op template from the corpus, checked by the door when it was built
        else:
            entry = _entry(data, call["tool"])
            validated = entry is not None and not selector._check_tool(entry, call.get("arguments", {}))
    return {"code": remedy["code"], "fix_by": fix_by, "by": "person" if person else "agent", "step": step,
            "call": call, "fill": fill, "validated": validated, "retries_left": 0 if person else MAX_RETRIES}


def observe(tool: str, arguments: Any, reply: Any, session: Any = None) -> Any:
    """``reply`` as the agent gets it: with its repair block when it is a refusal, the session's retry count applied;
    the repair log told how the last repair of this tool in the session ended. Never fails the call."""
    state = _state(session)
    with _LOCK:
        token = state.setdefault("_session", uuid.uuid4().hex[:12])
        pending = state.pop(("pending", tool), None)
    is_refused = refused(reply)
    if pending is not None:
        _log({"event": "next", "id": pending["id"], "session": token, "tool": tool, "code": pending["code"],
              "outcome": "not" if is_refused else "resolved"})
    if not is_refused or "job_id" in reply:
        # a job's reply is bound to the digest of the result the job recorded (g6.metaharness.jobs), so no word is
        # added to it here; its refusal is repaired through g6_select, which reads the job's reply
        return reply
    found = block(tool, reply)
    if found is None:
        return reply
    digest = _digest(arguments)
    with _LOCK:
        row = state.get((tool, found["code"]))
        if found["by"] == "person":
            left = 0
        elif row is None:
            left = MAX_RETRIES
        elif row["digest"] == digest and found["fix_by"] != "retry":
            left = 0                                    # the same call again, unchanged
        else:
            left = max(0, row["left"] - 1)
        state[(tool, found["code"])] = {"left": left, "digest": digest}
    if left == 0 and found["by"] == "agent":
        found = {**found, "by": "person", "step": found["step"] + (
            " This refusal came back again with no repair left (or the same call was sent unchanged): tell the "
            "person what it says, and stop.")}
    found["retries_left"] = left
    repair_id = uuid.uuid4().hex[:12]
    _log({"event": "refused", "id": repair_id, "session": token, "tool": tool, "code": found["code"],
          "fix_by": found["fix_by"], "by": found["by"]})
    if found["by"] == "person":
        _log({"event": "next", "id": repair_id, "session": token, "tool": tool, "code": found["code"],
              "outcome": "person"})
    else:
        with _LOCK:
            state[("pending", tool)] = {"id": repair_id, "code": found["code"]}
    return {**reply, "repair": found}


def consent_block() -> dict[str, Any]:
    """The repair block of a reply withheld for consent: the person decides, in the dialog ``g6_cloud_grant`` opens."""
    from g6.core import refusals

    words = refusals.layer("G6_E_CLOUD_CONSENT_REQUIRED").words
    return {"code": "G6_E_CLOUD_CONSENT_REQUIRED", "fix_by": "person", "by": "person", "step": words,
            "call": {"tool": "g6_cloud_grant", "arguments": {"source": "<source>", "provider": "<provider>"}},
            "fill": ["provider", "source"], "validated": True, "retries_left": 0}


__all__ = ["MAX_RETRIES", "block", "consent_block", "observe", "refused", "repair_stats"]
