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

    def g6_select(task: str, phase: str = "", last_outcome: str = "", considering: list | str = "", limit: int = 5,
                  rerank: str = "lexical") -> dict:
        """What to do next with G6. Give your task, the phase you are in (plan, implement, verify, diagnose or stuck),
        and last_outcome: a refusal's text or G6_E_* code, or an outcome such as defect_detected, from your last G6
        call. Optional considering: calls or hypotheses you are weighing, as tool or component.op names,
        {tool, arguments}, {component, op, args}, {hypothesis, id, severity, predicts, check} or a requirement
        {id, text, checks}; each is validated. Returns at most min(limit, 8) candidates, each with its exact call
        template, what it establishes and does not, its static needs (a sensitive allowance, consent classes, a model,
        a confined run), its cost, which rankers put it there and whether its template validated; next, the one call to
        make (or what to tell the person); and mechanisms, which stage ran and why. After a refusal it reroutes: a
        withheld reply to g6_cloud_grant (the person answers in their dialog; never grant for them), a sensitive
        refusal to the person with the g6 setup step, an input refusal to g6_describe. Whether an op is allowed here, a
        grant exists or a model is configured is unknown until a call observes it: pass that refusal back. Ranking is
        fixed rules over G6's own text, no model; rerank is lexical (a semantic rerank is not offered here, and
        "colbert" is answered lexically). Runs nothing; qualified-draft. You choose, and the call you make is checked then."""
        from g6.core.selector import select

        return select(task, phase, last_outcome, considering, limit, rerank, request_id=uuid.uuid4().hex[:12],
                      run_id="")

    g6_select._g6_declaration = TOOLS["g6_select"]
    server.tool()(g6_select)


__all__ = ["register"]
