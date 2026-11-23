# SPDX-License-Identifier: Apache-2.0
"""``g6_select``, the staged selector over MCP (``g6.core.selector``; plan revision 8, phase B).

It takes no repository and reads none: its reply is built from the client's own arguments and G6's built-in text
(the corpus shipped with G6), so its declaration is ``client_request`` and ``g6_operational`` and a client that has
granted nothing gets it. Nothing runs.
"""
from __future__ import annotations

import uuid
from typing import Any


def register(server: Any) -> None:
    from g6.core.consent_declarations import TOOLS

    def g6_select(task: str, phase: str = "", last_outcome: str = "", considering: list | dict | str = "",
                  limit: int = 5, rerank: str = "lexical") -> dict:
        """What to do next with G6. Give your task, the phase you are in (plan, implement, verify, diagnose or stuck),
        and last_outcome: a refusal's text or G6_E_* code, or an outcome such as defect_detected, from your last G6
        call. Optional considering, a list or one entry: calls or hypotheses you are weighing, as tool or component.op
        names, {tool, arguments}, {component, op, args}, {hypothesis, id, severity, predicts, check} or a requirement
        {id, text, checks}; each is validated. Returns at most min(limit, 8) candidates, each with its exact call
        template, what it establishes and does not, its static needs (a sensitive allowance, consent classes, a model,
        a confined run), its cost, which rankers put it there, whether its template ran as given when G6 was built
        (validated) and, if not, the fields to fill; next, the one call to make with those fields and what to put in
        them (or what to tell the person); and mechanisms, which stage ran and why. After a refusal it reroutes: a
        withheld reply to g6_cloud_grant (the person answers in their dialog; never grant for them), a sensitive
        refusal to the person to decide themselves, an input refusal to g6_describe. Whether an op is allowed here, a
        grant exists or a model is configured is unknown until a call observes it: pass that refusal back. Ranking is
        fixed rules over G6's own text. The shared reader may add safety suggestions or raise reported risk through
        the configured model and consent checks; every reading and fallback is in mechanisms. Rerank is lexical
        ("colbert" is answered lexically). No proposed call runs; qualified-draft. You choose the checked call."""
        from g6.core.selector import select
        from g6.mcp.families import compact_selection

        reply = select(task, phase, last_outcome, considering, limit, rerank,
                       request_id=uuid.uuid4().hex[:12], run_id="")
        return compact_selection(reply, considering=bool(considering))

    g6_select._g6_declaration = TOOLS["g6_select"]
    server.tool()(g6_select)


__all__ = ["register"]
