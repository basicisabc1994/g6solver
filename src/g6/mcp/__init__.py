# SPDX-License-Identifier: Apache-2.0
"""g6-mcp — a stdio MCP server exposing G6's progressive discovery surface.

Five discovery tools: ``g6_help``, ``g6_find``, ``g6_describe``, ``g6_invoke`` and ``g6_components``; and seven
meta-harness tools (``metaharness_inspect``, ``_propose``, ``_evaluate``, ``_decide``, ``_diagnose``, ``_approve``,
``_rollback``) for the workflows declared in the file ``G6_METAHARNESS`` names, where approval and rollback ask the
person through the client and never come from the session that proposed, and a failed attempt carries its recovery
(``g6.metaharness.surface``). Install
with ``pip install "g6[mcp] @ git+https://github.com/basicisabc1994/g6solver"`` and register with ``claude mcp add g6 -- g6-mcp`` (or, for Codex,
``codex mcp add g6 -- g6-mcp``). There is no account, key or network service.

An op that needs a model uses the one the person set in their key file (``G6_LLM_PROVIDER`` in ``~/.g6/.env``, for
example ``claude_code``, their own Claude Code sign-in): ``g6_invoke`` builds it for each call and gives it only to a
component that declares ``__g6_model__``. With none set, such an op says how to set one, and the agent can do that
step itself and call the op that checks it.

Ops a component marks sensitive (they run a program on your machine, reach the network or change it) are refused on
this surface unless the person who runs the server lists them in ``G6_MCP_SENSITIVE``, a comma-separated list of
component names (``lean``) or single ops (``lean.check_proof``), set where the server is registered, for example
``claude mcp add g6 -e G6_MCP_SENSITIVE=lean -- g6-mcp``. There is no wildcard, and the agent cannot set it: the
server reads it from its own environment. ``g6_describe`` says which sensitive ops this server allows. Before you list
one, know this (the refusal says it too): Where no operating-system boundary can confine it (macOS, Linux without
bubblewrap, and Windows before the one-time access grant), G6 refuses to run untrusted code for you (a patch, a
generated script, a repository's tests) unless you allow it once with g6 setup. Code you allow then has your own
rights: it can read your key file ~/.g6/.env and your other files, and its network guard works inside Python only. You
are responsible for what you run.

Every ``g6_invoke`` reply carries the library envelope (``completion_state``, ``warning_card``,
``evidence``, ``request_id``, ``run_id``) beside ``ok`` and ``result`` / ``error`` (``g6.core.door``). The door
never raises a state: a failure is ``blocked-escalated`` with its ``G6_E_*`` code; a result is the state its
component reported, ``qualified-draft`` when it reported none, and ``verified`` only when G6's own code re-runs a
check at the door and reproduces it. Any other ``verified`` is held at ``qualified-draft`` with a
``G6_E_PROMOTION_LAW`` card and ``claimed_completion_state``, and the ``result`` copy says the same. G6 re-checks
artifacts with its own code; it does not defend against malicious code in its own process.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

#: The server's own setting: the sensitive components or ops (``component.op``) the person allows over MCP.
SENSITIVE_ENV = "G6_MCP_SENSITIVE"
_DENIED = "[G6_E_SENSITIVE_COMPONENT_DENIED]"


def allowed_sensitive(setting: str | None = None) -> frozenset[str]:
    """The names listed in ``G6_MCP_SENSITIVE`` (or ``setting``): components or ``component.op``, no wildcard."""
    raw = os.environ.get(SENSITIVE_ENV, "") if setting is None else setting
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def _allowed(component: str, op: str, allow: frozenset[str]) -> bool:
    return component in allow or f"{component}.{op}" in allow


def describe_reply(component: str, *, allow_sensitive: frozenset[str] | None = None) -> dict[str, Any]:
    """The ``g6_describe`` reply: the component's description and, where it has sensitive ops, which of them this
    server allows."""
    from g6.core import discovery

    info = discovery.describe(component)
    sensitive = info.get("sensitive_ops") or []
    if sensitive:
        allow = allowed_sensitive() if allow_sensitive is None else allow_sensitive
        info["sensitive_ops_allowed_here"] = [op for op in sensitive if _allowed(component, op, allow)]
    return info


def _person_model() -> Any:
    """The model the person configured for G6 (``g6.core.llm.provider_from_env``: ``G6_LLM_PROVIDER`` in their key
    file), or ``None`` when they configured none. A setting G6 cannot use gives a caller that says why when it is
    asked, so only an op that needs a model hears of it."""
    from g6.core import llm

    try:
        caller = llm.provider_from_env()
    except llm.LLMError as exc:
        return llm.UnusableModel(str(exc))
    return None if isinstance(caller, llm.NoModel) else caller


def invoke_reply(component: str, op: str, params_json: str = "{}", *, registry: Any = None,
                 allow_sensitive: frozenset[str] | None = None) -> dict[str, Any]:
    """The ``g6_invoke`` reply: the component's answer (or why there is none) inside the library envelope. A
    sensitive op runs only when ``allow_sensitive`` (by default ``G6_MCP_SENSITIVE``) lists it or its component. A
    component whose block takes a model (``__g6_model__``) is given the one the person configured, built once for
    the call; with none configured, nothing is passed."""
    from g6.core.door import blocked_reply, door_reply
    from g6.core.registry import get_registry

    request_id = uuid.uuid4().hex[:12]

    def failed(error: str) -> dict[str, Any]:
        return blocked_reply(error, request_id=request_id)

    try:
        params = json.loads(params_json or "{}")
    except json.JSONDecodeError as exc:
        return failed(f"[G6_E_INPUT_VALIDATION] params_json is not JSON: {exc}")
    if not isinstance(params, dict):
        return failed("[G6_E_INPUT_VALIDATION] params_json must be a JSON object")
    allow = allowed_sensitive() if allow_sensitive is None else allow_sensitive
    model = _person_model()
    registry = registry or get_registry()
    result = registry.invoke(component, op, params,
                                                 block_sensitive=not _allowed(component, op, allow),
                                                 **({"llm": model} if model is not None else {}))
    if result.__class__.__name__ != "Success":
        error = str(result.failure())
        if error.startswith(_DENIED):
            from g6.core.confine import UNCONFINED_WARNING

            error += (f": it runs a program on your machine, reaches the network or changes it. Only the person who "
                      f"runs this server can allow it, by adding {component}.{op} (or {component}) to {SENSITIVE_ENV} "
                      f"where the server is registered. {UNCONFINED_WARNING}")
        return failed(error)
    return door_reply(component, op, result, caller=params, registry=registry, request_id=request_id)


def build_server(surface: Any = None) -> Any:
    try:
        from fastmcp import Context, FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SystemExit("g6-mcp needs the MCP extra: pip install 'g6[mcp] @ git+https://github.com/basicisabc1994/g6solver'") from exc

    from g6.core import discovery
    from g6.core.registry import get_registry

    server = FastMCP("g6")

    @server.tool()
    def g6_help() -> str:
        """How to use G6 from an agent. Call this first."""
        return discovery.HELP

    @server.tool()
    def g6_components() -> list[str]:
        """Every installed G6 component."""
        return get_registry().names()

    @server.tool()
    def g6_find(query: str, limit: int = 5) -> list[dict]:
        """Rank components for a task description; empty list means nothing matched."""
        return discovery.find(query, limit)

    @server.tool()
    def g6_describe(component: str) -> dict:
        """Maturity, ops and declared dependencies of one component, and which of its sensitive ops this server
        allows."""
        return describe_reply(component)

    @server.tool()
    def g6_invoke(component: str, op: str, params_json: str = "{}") -> dict:
        """Run one op of a component. ``params_json`` is a JSON object of the op's parameters. The reply carries
        ``completion_state`` (verified / qualified-draft / blocked-escalated) and a ``warning_card``: read them."""
        return invoke_reply(component, op, params_json)

    _metaharness_tools(server, surface, Context)
    return server


def _metaharness_tools(server: Any, surface: Any, context_type: Any) -> None:
    """The meta-harness tools (META_HARNESS_PLAN.md Phase 6): additive, the Python API's envelopes as JSON. Approval
    and rollback ask the person through the client (MCP elicitation); the agent never supplies the approver."""
    from g6.metaharness.surface import Surface

    mh = surface or Surface()

    def session(ctx: Any) -> str:
        return str(getattr(ctx, "session_id", None) or id(getattr(ctx, "session", ctx)))

    async def ask(ctx: Any, question: str) -> str | None:
        try:
            answer = await ctx.elicit(question, response_type=str)
        except Exception:  # noqa: BLE001 - a client that cannot ask means no person approved
            return None
        return answer.data if getattr(answer, "action", "") == "accept" else None

    def metaharness_inspect(workflow: str) -> dict:
        """A declared workflow's versions, confirmations, evaluations and decisions."""
        return mh.inspect(workflow)

    def metaharness_propose(workflow: str, ctx: Any, profile_json: str = "", proposer: str = "") -> dict:
        """A candidate profile (``profile_json``: acting fields only), or with none, the declared search space's
        candidates. A candidate changes nothing until it is evaluated, decided and approved by a person."""
        try:
            profile = json.loads(profile_json) if profile_json else None
        except json.JSONDecodeError as exc:
            from g6.metaharness.api import _blocked

            return _blocked("G6_E_META_SCHEMA", f"profile_json is not JSON: {exc}")
        return mh.propose(workflow, session=session(ctx), profile=profile, proposer=proposer)

    def metaharness_evaluate(workflow: str, proposal_digest: str) -> dict:
        """Measure a candidate against the active version on the held-out split (paired, compute-matched)."""
        return mh.evaluate(workflow, proposal_digest)

    def metaharness_decide(workflow: str, report_digest: str, proposal_digest: str) -> dict:
        """The meta-gate's decision: recommend (qualified-draft at best), refuse or block, with a G6_E_META_* code.
        A decision that is not recommend carries ``recovery``: why, and what can be done next. Tell your user."""
        return mh.decide(workflow, report_digest, proposal_digest)

    def metaharness_diagnose(workflow: str) -> dict:
        """Recovery and root cause: why the active version fails on the dev split, and what can be done next (G6
        components to try as candidates, and what only your user can unblock). Changes nothing."""
        return mh.diagnose(workflow)

    async def metaharness_approve(workflow: str, decision_digest: str, ctx: Any) -> dict:
        """Ask the person, through the client, to approve a recommended change. The session that proposed it cannot:
        that is checked before anyone is asked, and the refusal says how a person approves it instead."""
        who = session(ctx)
        refused, question = mh.approval_check(workflow, decision_digest, session=who)
        if refused is not None:
            return refused
        answer = await ask(ctx, question)
        return mh.approve(workflow, decision_digest, session=who, answer=answer)

    async def metaharness_rollback(workflow: str, version: int, ctx: Any) -> dict:
        """Ask the person, through the client, to move back to a version they approved."""
        answer = await ask(ctx, f"G6 asks a person to roll back to approved version {version}. Type your name.")
        return mh.rollback(workflow, version, answer=answer)

    for tool in (metaharness_inspect, metaharness_propose, metaharness_evaluate, metaharness_decide,
                 metaharness_diagnose, metaharness_approve, metaharness_rollback):
        if "ctx" in tool.__annotations__:  # the real Context type, so the server injects it (annotations are strings)
            tool.__annotations__["ctx"] = context_type
        server.tool()(tool)


def main() -> None:
    build_server().run()
