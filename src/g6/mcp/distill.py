# SPDX-License-Identifier: Apache-2.0
"""Distillation routes; consent declarations are issued separately in H1."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, Literal

from g6.distill import surface

DECIDE_FORM = {"type": "object", "properties": {
    "confirm": {"type": "string", "enum": ["change", "keep"]},
    "name": {"type": "string", "description": "Your name (recorded as supplied, not authenticated)"}},
    "required": ["confirm", "name"]}


_INPUT_ERRORS = (ValueError, KeyError, OSError, RecursionError)


def _refusal(exc):
    from g6.core.door import blocked_reply
    from g6.distill.ledger import PartialAuthorityWrite
    if isinstance(exc, PartialAuthorityWrite):
        earlier = exc.earlier_publication_committed and not exc.committed_steps
        steps = '; '.join(f'{kind} {ident}' for kind, ident in exc.committed_steps)
        state = ('This request recorded ' + steps + ('; intent cleanup pending' if exc.committed
                                                    else '; undo recovery incomplete') if steps else
                 'Earlier publication committed; its cleanup is incomplete; this request made no change' if earlier
                 else 'Publication committed; intent cleanup pending' if exc.publication_committed
                 else 'Recovery incomplete')
        return {**blocked_reply(f'[{exc.code}] {state}; durable state: ' + '; '.join(exc.durable_state)),
                'durable_state': exc.durable_state, 'publication_committed': exc.publication_committed,
                **({'earlier_publication_committed': True} if earlier else {}),
                **({'committed_steps': [{'kind': kind, 'id': ident} for kind, ident in exc.committed_steps]}
                   if exc.committed_steps else {}),
                'rollback_or_containment': (state + ('.' if exc.publication_committed else
                    '; no activation committed; the method is unchanged.' if exc.committed else
                    '; no activation committed; inspect the named durable state before retrying.')
                    if steps else state + '.' if earlier else
                    'The evaluation reservation committed; no admission decision was made; the method is unchanged.'
                    if exc.reservation else
                    'The publication committed; its intent still needs cleanup.' if exc.publication_committed else
                    'Undo is incomplete; inspect the named durable state before retrying.')}
    code = getattr(exc, 'code', '')
    if isinstance(code, str) and code.startswith('G6_E_'):
        return blocked_reply(f'[{code}] Backend refused the operation; read the current durable state.')
    return blocked_reply("[G6_E_DISTILL_INPUT] Invalid input, missing entry or unreadable distillation record. "
                         "The operation could not complete; read the current durable state.")


def _call(fn, *args, **kwargs) -> dict:
    from g6.core.ledger import LedgerError
    try:
        return fn(*args, **kwargs)
    except (*_INPUT_ERRORS, LedgerError) as exc:
        # RecursionError: a decoder's depth limit on deeply nested input is an input refusal like any other.
        return _refusal(exc)


def _owner_decision(fn, *args, short_transaction=True, **kwargs):
    """Capture the result before the short owner transaction's cleanup can fail."""
    from g6.distill.ledger import PartialAuthorityWrite, failure_writer

    class Refused(Exception):
        pass

    result, cleanup = None, {}
    try:
        # Restore evaluates outside the store lock and publishes in its own
        # short transactions. Its activation captures a decision before cleanup.
        with failure_writer() if short_transaction else nullcontext():
            answer = fn(*args, **kwargs)
            result = answer if isinstance(answer, dict) else asdict(answer)
            if result['verdict'] in {'refuse', 'block'}:
                # The gate catches validation errors outside its own nested
                # context. Carry that refusal through this outer rollback too.
                raise Refused
    except Refused:
        pass
    except PartialAuthorityWrite as error:
        if result is None:
            result = error.decision
        if not error.publication_committed or result is None:
            raise
        cleanup = {'publication_committed': True, 'cleanup_pending': True,
                   'cleanup_note': 'Decision committed; authority_intent.json cleanup pending.',
                   'durable_state': error.durable_state}
    return result, cleanup


def _envelope(fn):
    """Cover the complete handler, including preflight and post-dialog stores."""
    from g6.core.ledger import LedgerError
    if iscoroutinefunction(fn):
        @wraps(fn)
        async def asynchronous(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except (*_INPUT_ERRORS, LedgerError) as exc:
                return _refusal(exc)
        return asynchronous

    @wraps(fn)
    def synchronous(*args, **kwargs):
        return _call(fn, *args, **kwargs)
    return synchronous


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
        """Ask the person to lower, restore or release a held method through their client dialog.
        Withdrawn/demoted methods need reacceptance. Declining, cancelling or a stale entry changes nothing."""
        from g6.core.door import blocked_reply
        from g6.distill import acceptance_gate as gate
        from g6.distill.library import Library
        from g6.distill.library import public_decision
        from g6.mcp.consent import elicit

        if action not in {"demote", "withdraw", "restore"} or not reason.strip():
            return blocked_reply("[G6_E_DISTILL_INPUT] Choose demote, withdraw or restore and supply a reason.")
        if action == 'restore' and hasattr(backend, 'validate_registration'):
            invalid = _call(backend.validate_registration)
            if invalid is not None:
                return invalid
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
        cleanup = {}
        if action == 'restore' and entry['status'] == 'held':
            result, cleanup = _owner_decision(gate.lift_hold, entry_id, Library(), reason=reason,
                                              person=content["name"].strip())
        elif action == 'restore':
            result, cleanup = _owner_decision(backend.restore, entry_id, short_transaction=False)
        else:
            result, cleanup = _owner_decision(getattr(gate, action), entry_id, Library(), reason=reason,
                                              person=content["name"].strip())
        # Includes both restore paths and the nested refusal envelope. Raw owner
        # text remains in the local gate decision, never in the MCP reply.
        if 'verdict' in result:
            result = public_decision(result)
        if result.get('verdict') in {'refuse', 'block'}:
            return {**blocked_reply("[G6_E_DISTILL_DECISION] The acceptance gate refused the decision."),
                    'decision': result}
        return result | cleanup

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
        state, content, _ = await elicit(ctx, f'Start one bounded research tick for runner key {folder!r}? '
            'Host authority and all gates still apply.', DECIDE_FORM)
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
        server.tool()(_envelope(fn))
