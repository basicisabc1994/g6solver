# SPDX-License-Identifier: Apache-2.0
"""``g6_agentic_run``: the agentic override kernel over MCP (``g6._agentic``; design calls 10.1 and 10.9).

Over MCP the caller is itself a model, so every override asks the person, whatever its trigger: G6 writes the
question, the agent's reason appears in it only quoted and cut short, and the answer applies only to that question's
digest. Only the client's own dialog answers; there is no terminal fallback and no argument that agrees. A standing
allowance (``agentic.run`` in ``G6_MCP_SENSITIVE``) only lets the tool be called. The override runs as a job, polled
through ``metaharness_job`` like patch review; no pending proposal is stored."""
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
        """When a G6 op refuses with a limit its component declares, or its own code fails, ask the person to let a
        model stand in for one named part of that component's code, for this one call. Prompt mode only (mode="code"
        is refused, G6_E_AGENTIC_MODE_UNAVAILABLE), and only for pure-computation ops a component opts in
        (G6_E_AGENTIC_UNSUPPORTED otherwise), never a protected part (G6_E_AGENTIC_PROTECTED). params_json is the
        op's parameters as for g6_invoke; part names a declared part (empty: the op's only one; naming it after an
        answer that was not refused asks for an override anyway); reason, one line, is shown to the person quoted, as
        your claim. G6 runs the original first: with no trigger you get its reply unchanged and nobody is asked. A
        trust door's refusal (a sensitive op not allowed, invalid input, no isolation, consent, budgets) is never a
        trigger. Otherwise the person is asked in the client's own dialog, bound to the question's digest; a decline,
        a cancel, a client with no dialog or an empty name leaves the original's answer standing (agentic.outcome
        not_run), and you never answer for them. Once they agree it returns a job: poll metaharness_job. The op runs
        again in an isolated child with no network where each value the part returns comes from the model configured
        for G6 (at most 3 calls), checked by type and by the component's own validator; the final answer is re-checked
        by the component's own checker. The result is qualified-draft at best, never verified, with warning card
        G6_E_AGENTIC_SUBSTITUTE, the original's answer in original, and in agentic the trigger, part, model, digests,
        the person's answer and name (not authenticated), the checks and the cost. Anything that fails leaves the
        original's answer, with degradation_reason. Show the person both answers. A substituted result never passes
        a requirement and is never evidence. Requires host permission agentic.run and the op's own in
        G6_MCP_SENSITIVE."""
        from g6._agentic import decode, kernel
        from g6.core.door import SENSITIVE_ENV, allowed_sensitive, blocked_reply, is_allowed
        from g6.mcp.consent import request_consent
        from g6.mcp.consent_controls import public_instructions

        allow = allowed_sensitive()
        if not is_allowed(*kernel.ALLOWANCE, allow):
            return public_instructions(blocked_reply(
                "[G6_E_SENSITIVE_COMPONENT_DENIED] agentic.run is not allowed: an override runs G6's code again with a "
                "model standing in for part of it. Only the person who runs this server can allow it, by adding "
                f"agentic.run to {SENSITIVE_ENV} where the server is registered; the person is then still asked "
                "before each override."))
        try:   # the client's text, bounded in size and depth before it is decoded
            params = decode.loads(params_json or "{}", max_chars=PARAMS_CHARS)
        except decode.Undecodable as exc:
            return blocked_reply(f"[G6_E_INPUT_VALIDATION] params_json is not decoded: {exc}")
        if not isinstance(params, dict):
            return blocked_reply("[G6_E_INPUT_VALIDATION] params_json must be a JSON object")
        prepared = await asyncio.to_thread(partial(kernel.prepare, component, op, params, part=part or None,
                                                   mode=mode, reason=reason, allow=allow))
        if not isinstance(prepared, kernel.Pending):
            card = prepared.get("warning_card") if isinstance(prepared.get("warning_card"), dict) else {}
            if card.get("code") == "G6_E_SENSITIVE_COMPONENT_DENIED" and "original" not in prepared:
                return public_instructions(prepared)
            return prepared
        consent = await request_consent(ctx, prepared.question)
        if consent.status != "accepted":
            return kernel.declined(prepared, consent.status)
        confirmation = kernel.Confirmation(prepared.digest, consent.answer or "")
        return jobs.start(lambda: kernel.override(prepared, confirmation=confirmation))

    g6_agentic_run.__annotations__["ctx"] = context_type
    g6_agentic_run._g6_declaration = TOOLS["g6_agentic_run"]
    server.tool()(g6_agentic_run)


__all__ = ["register"]
