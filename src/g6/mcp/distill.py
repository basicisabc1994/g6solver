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
    except (ValueError, KeyError, LedgerError, RecursionError) as exc:
        # RecursionError: a decoder's depth limit on deeply nested input is an input refusal like any other.
        code = getattr(exc, 'code', '')
        if isinstance(code, str) and code.startswith('G6_E_'):
            return blocked_reply(f'[{code}] Backend refused the operation; no authority was granted.')
        return blocked_reply("[G6_E_DISTILL_INPUT] Invalid input, missing entry or unreadable distillation record. "
                             "No authority was granted.")


def register(server: Any, context_type: Any, jobs: Any, *, backend=None) -> None:
    from g6.core.consent_declarations import TOOLS
    from g6.mcp.distill_backend import Service
    backend = Service(jobs=jobs) if backend is None else backend

    def g6_failure_record(kind: str, family: str, case_json: str, note: str) -> dict:
        """Record a reported failure or request locally, as self-observed field data; never held-out evidence.
        For g6_distill_start to learn from it, case_json holds unit_id, provenance, files, target, changed_lines,
        before_tests, after_tests and known_wrong: G6 then labels the case by its own confined oracle run."""
        return _call(surface.failure_record, kind, family, case_json, note)

    def g6_distill_start(family: str, failure_ids: str = "") -> dict:
        """Start bounded training-only concept synthesis and independent admission. Needs runs_code."""
        from g6.core.door import allowed_sensitive, blocked_reply, is_allowed
        if not is_allowed("metaharness", "distill_start", allowed_sensitive()):
            return blocked_reply("[G6_E_SENSITIVE_COMPONENT_DENIED] metaharness.distill_start runs code. "
                                 "Only the person who runs this server can allow it.")
        return _call(backend.distill_start, family, failure_ids)

    def g6_distill_job(job_id: str, detail: str = "compact") -> dict:
        """Read a retained distillation job; unknown IDs retain the job store's refusal."""
        return _call(backend.distill_job, job_id, detail)

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

        if action not in {"demote", "withdraw", "restore"} or not reason.strip():
            return blocked_reply("[G6_E_DISTILL_INPUT] Choose demote, withdraw or restore and supply a reason.")
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
        if action == 'restore':
            return _call(backend.restore, entry_id)
        result = getattr(gate, action)(entry_id, Library(), reason=reason, person=content["name"].strip())
        if result.verdict in {'refuse', 'block'}:
            return {**blocked_reply("[G6_E_DISTILL_DECISION] The acceptance gate refused the decision."),
                    'decision': asdict(result)}
        return asdict(result)

    def g6_self_model(section: str = "", detail: str = "compact") -> dict:
        """Read G6's self-model; missing or stale measured observations remain unknown."""
        return _call(backend.self_model, section, detail)

    def g6_simulate(action_json: str) -> dict:
        """Simulate with host-measured context; predictions never become acceptance evidence.
        Components are G6 package names such as g6.distill; G6 reads its own import graph and protected set."""
        import json
        from g6.core.door import blocked_reply
        try:
            action = json.loads(action_json)
        except (ValueError, RecursionError):
            action = None
        if isinstance(action, dict) and (action.get("gate") is not None or action.get("plan") is not None):
            return blocked_reply("[G6_E_DISTILL_INPUT] Gate and plan readouts are measured by G6, never supplied by "
                                 "the caller; omit gate and plan.")
        return _call(backend.simulate, action_json)

    def g6_extend(request: str, repo: str = "") -> dict:
        """Record a local capability request through rectification; a request grants no authority."""
        return _call(backend.extend, request, repo)

    async def g6_research_start(ctx: Any, folder: str = "") -> dict:
        """Start one bounded host research tick after authority checks and a client dialog."""
        from g6.core.door import blocked_reply
        from g6.mcp.consent import elicit
        denied = _call(backend.research_admission, folder)
        if denied:
            return denied
        state, content, _ = await elicit(ctx, 'Start one bounded research tick? Host authority and all gates still apply.', DECIDE_FORM)
        if state != 'accept' or not isinstance(content, dict) or content.get('confirm') != 'change' \
                or not isinstance(content.get('name'), str) or not content['name'].strip():
            return blocked_reply('[G6_E_DISTILL_DIALOG] No research start authorized in the client dialog.')
        return _call(backend.research_start, folder)

    def g6_research_stop(folder: str = "", reason: str = "") -> dict:
        """Persist a scoped research stop checked before and during each research tick."""
        return _call(backend.research_stop, folder, reason)

    def g6_research_status(folder: str = "") -> dict:
        """Read actual research authority, host configuration and stop state."""
        return _call(backend.research_status, folder)

    def g6_research_digest(folder: str = "") -> dict:
        """Read the research digest without publishing it or changing authority."""
        return _call(backend.research_digest, folder)

    for fn in (g6_failure_record, g6_distill_start, g6_distill_job, g6_library, g6_library_decide,
               g6_self_model, g6_simulate, g6_extend, g6_research_start, g6_research_stop,
               g6_research_status, g6_research_digest):
        if "ctx" in fn.__annotations__:
            fn.__annotations__["ctx"] = context_type
        # Before owner issuance the existing wrapper treats a missing declaration as unknown.
        if fn.__name__ in TOOLS:
            fn._g6_declaration = TOOLS[fn.__name__]
        server.tool()(fn)
