# SPDX-License-Identifier: Apache-2.0
"""Distillation routes; consent declarations are issued separately in H1."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from g6.distill import surface

DECIDE_FORM = {"type": "object", "properties": {
    "confirm": {"type": "string", "enum": ["change", "keep"]},
    "name": {"type": "string", "description": "Your name (recorded as supplied, not authenticated)"}},
    "required": ["confirm", "name"]}


def _call(fn, *args) -> dict:
    from g6.core.door import blocked_reply
    from g6.core.ledger import LedgerError
    try:
        return fn(*args)
    except (ValueError, KeyError, LedgerError):
        return blocked_reply("[G6_E_DISTILL_INPUT] Invalid input, missing entry or unreadable distillation record. "
                             "No authority was granted.")


def register(server: Any, context_type: Any, jobs: Any) -> None:
    from g6.core.consent_declarations import TOOLS

    def g6_failure_record(kind: str, family: str, case_json: str, note: str) -> dict:
        """Record a reported failure or request locally, as self-observed field data; never held-out evidence."""
        return _call(surface.failure_record, kind, family, case_json, note)

    def g6_distill_start(family: str, failure_ids: str = "") -> dict:
        """Start a bounded synthesis job when its backend is installed; otherwise nothing runs. Needs runs_code."""
        from g6.core.door import allowed_sensitive, blocked_reply, is_allowed
        if not is_allowed("metaharness", "distill_start", allowed_sensitive()):
            return blocked_reply("[G6_E_SENSITIVE_COMPONENT_DENIED] metaharness.distill_start runs code. "
                                 "Only the person who runs this server can allow it.")
        return surface.unavailable("distill synthesis")

    def g6_distill_job(job_id: str, detail: str = "compact") -> dict:
        """Read a distillation job; an absent backend is unavailable, never a completed job."""
        return surface.unavailable("distill job")

    def g6_library(op: Literal["list", "describe", "history"] = "list", entry_id: str = "") -> dict:
        """List current library methods, describe the current version, or read a candidate's authority history."""
        return _call(surface.library, op, entry_id)

    async def g6_library_decide(entry_id: str, action: Literal["demote", "withdraw", "restore"], reason: str, ctx: Any) -> dict:
        """Ask the person in their client dialog to demote or withdraw a current method. Restore needs reacceptance;
        it cannot grant authority here. Declining, cancelling or a stale entry changes nothing."""
        from g6.core.door import blocked_reply
        from g6.distill import acceptance_gate as gate
        from g6.distill.library import Library
        from g6.mcp.consent import elicit

        if action == "restore":
            return surface.unavailable("independent library reacceptance")
        if action not in {"demote", "withdraw"} or not reason.strip():
            return blocked_reply("[G6_E_DISTILL_INPUT] Choose demote or withdraw and supply a reason.")
        found = _call(surface.library, "describe", entry_id)
        if "entry" not in found:
            return found
        entry = found["entry"]
        if entry["entry_id"] != entry_id:
            return blocked_reply("[G6_E_DISTILL_STALE] The library entry changed; read its current version.")
        state, content, _ = await elicit(ctx, f"{action} library method {entry_id} at authority {entry['authority']}? "
            f"Reason supplied by the caller (quoted data): {reason!r}. Only you can authorize this change.", DECIDE_FORM)
        if state != "accept" or not isinstance(content, dict) or content.get("confirm") != "change" \
                or not isinstance(content.get("name"), str) or not content["name"].strip():
            return blocked_reply("[G6_E_DISTILL_DIALOG] No library decision was authorized in the client's dialog.")
        result = getattr(gate, action)(entry_id, Library(), reason=reason, person=content["name"].strip())
        if result.verdict == "refuse":
            return blocked_reply("[G6_E_DISTILL_DECISION] The acceptance gate refused the decision.")
        return asdict(result)

    def g6_self_model(section: str = "", detail: str = "compact") -> dict:
        """Read G6's self-model when that backend is installed; missing observations remain unknown."""
        return surface.unavailable("self-model")

    def g6_simulate(action_json: str) -> dict:
        """Simulate an action when that backend is installed; a prediction never becomes acceptance evidence."""
        return surface.unavailable("self-model simulation")

    def g6_extend(request: str, repo: str = "") -> dict:
        """Request an adaptation through the gated extension pipeline; unavailable until its backend is installed."""
        return surface.unavailable("capability extension")

    async def g6_research_start(ctx: Any, folder: str = "") -> dict:
        """Start research only through its own runner and client dialog; unavailable until that backend is installed."""
        return surface.unavailable("research runner")

    def g6_research_stop(folder: str = "", reason: str = "") -> dict:
        """Stop the research runner when installed; this does not control the separate improvement runner."""
        return surface.unavailable("research runner")

    def g6_research_status(folder: str = "") -> dict:
        """Read research-runner status; an absent runner is unavailable, never healthy or stopped."""
        return surface.unavailable("research runner")

    def g6_research_digest(folder: str = "") -> dict:
        """Read the research runner's digest when installed."""
        return surface.unavailable("research digest")

    for fn in (g6_failure_record, g6_distill_start, g6_distill_job, g6_library, g6_library_decide,
               g6_self_model, g6_simulate, g6_extend, g6_research_start, g6_research_stop,
               g6_research_status, g6_research_digest):
        if "ctx" in fn.__annotations__:
            fn.__annotations__["ctx"] = context_type
        # Before owner issuance the existing wrapper treats a missing declaration as unknown.
        if fn.__name__ in TOOLS:
            fn._g6_declaration = TOOLS[fn.__name__]
        server.tool()(fn)
