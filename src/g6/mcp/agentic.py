# SPDX-License-Identifier: Apache-2.0
"""g6_agentic_run: original first, confined substitution as a job, with the acting authority recorded.

No per-override dialog is requested. The check records remote admission as provider consent, local admission as
local model, and refusal as refused by the consent check; process callers are allowed in code. Sensitive allowances
still apply. Poll metaharness_job; show the model and both answers.
"""
from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

#: The longest params_json the tool decodes.
PARAMS_CHARS = 1_000_000


def register(server: Any, context_type: Any, jobs: Any) -> None:
    """Register ``g6_agentic_run`` on ``server``, its jobs kept in ``jobs`` (the meta-harness's, so
    ``metaharness_job`` polls them)."""
    from g6.core.consent_declarations import TOOLS

    async def g6_agentic_run(component: str, op: str, ctx: Any, params_json: str = "{}", part: str = "",
                             mode: str = "prompt", reason: str = "") -> dict:
        """Run the original G6 operation first, then a confined override for a declared pure-computation part
        when the component refuses with a declared limit, fails in its own code, or the caller names a part after
        a successful answer. Trust-door refusals never trigger an override. Prompt mode only; protected parts and
        code mode are refused. params_json holds the operation's JSON parameters; reason is recorded as quoted data.
        Remote admission records provider consent, local admission records local model, refusal records refused by
        the consent check, and process callers record allowed in code, with each send's authority in the reply.
        There is no per-override question or typed name. Requires host permission agentic.run and the operation's own
        sensitive allowance. Returns a job when
        an override runs: poll metaharness_job. Each value passes its type and the declared validator; the final
        result passes the declared checker with nothing substituted. At most three model calls by default, within
        a character and wall-clock budget. Only owned host sign-in, local models and loopback IP-literal endpoints
        qualify. The result is qualified-draft at best with G6_E_AGENTIC_SUBSTITUTE, the model used, original answer
        beside it, and trigger, digests, checks, cost and ledger entry in agentic. Failure leaves the original
        standing. Show both answers and the model name. A substitute never passes a requirement or becomes evidence.
        """
        from g6._agentic import decode, kernel
        from g6.core.door import SENSITIVE_ENV, allowed_sensitive, is_allowed
        from g6.core.external_data import plain_text
        from g6.core.guidance_builders import blocked_reply
        from g6.mcp.consent_controls import public_instructions

        allow = allowed_sensitive()
        if not is_allowed(*kernel.ALLOWANCE, allow):
            return public_instructions(blocked_reply(
                "[G6_E_SENSITIVE_COMPONENT_DENIED] agentic.run is not allowed: an override runs G6's code again with a "
                "model standing in for part of it. Only the person who runs this server can allow it, by adding "
                f"agentic.run to {SENSITIVE_ENV} where the server is registered. Host/endpoint calls still require "
                "the existing consent check (remote consent or local admission); process callers use the code's allowance."))
        try:   # the client's text, bounded in size and depth before it is decoded
            params = decode.loads(params_json or "{}", max_chars=PARAMS_CHARS)
        except decode.Undecodable as exc:
            return blocked_reply(f"[G6_E_INPUT_VALIDATION] params_json is not decoded: {exc}")
        if not isinstance(params, dict):
            return blocked_reply("[G6_E_INPUT_VALIDATION] params_json must be a JSON object")
        # The kernel takes exact str: the client's text crossed G6's MCP ingress with G6's own reply receipt.
        component, op, part, mode, reason = (plain_text(one) for one in (component, op, part, mode, reason))
        prepared = await asyncio.to_thread(partial(kernel.prepare, component, op, params, part=part or None,
                                                   mode=mode, reason=reason, allow=allow))
        if not isinstance(prepared, kernel.Pending):
            card = prepared.get("warning_card") if isinstance(prepared.get("warning_card"), dict) else {}
            if card.get("code") == "G6_E_SENSITIVE_COMPONENT_DENIED" and "original" not in prepared:
                return public_instructions(prepared)
            return prepared
        return jobs.start(lambda: kernel.override(prepared))

    g6_agentic_run.__annotations__["ctx"] = context_type
    g6_agentic_run._g6_declaration = TOOLS["g6_agentic_run"]
    server.tool()(g6_agentic_run)


__all__ = ["register"]
