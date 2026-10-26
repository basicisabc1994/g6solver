# SPDX-License-Identifier: Apache-2.0
"""AgentNanoclawBlock: the registry and MCP door (``g6_invoke``) to the mailbox.

Six ops, each reading or writing only the mailbox in G6's own state folder; none starts a program, reaches the network
or calls a model. ``create_group``, ``send``, ``receive``, ``ack``, ``history`` and ``delete_group``
(:mod:`~g6.agents.agent_nanoclaw.mailbox`). ``delete_group`` destroys a group and every message in it, so it is
sensitive: callable from Python, and over MCP (or by an agent through the family's gated calls) only when the person
who runs the server lists it in ``G6_MCP_SENSITIVE``. ``history`` is the one op that changes nothing
(:data:`READ_ONLY_OPS`), so it is the one an agent's dry run may call. Each answers what it did, with ids, as a fact of
G6's own record: ``qualified-draft``; a refusal is ``blocked-escalated`` with its own code.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.agents.agent_nanoclaw.mailbox import CODES, FieldType, Mailbox, reply
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result

#: delete_group destroys a group and its messages; the other ops keep to adding, delivering and acknowledging.
SENSITIVE_OPS: tuple[str, ...] = ("delete_group",)
#: The ops that change nothing (receive records a delivery, ack moves a cursor).
READ_ONLY_OPS: tuple[str, ...] = ("history", "ops", "help")
REQUIRED = "G6_E_MAILBOX_REQUIRED"
_OPS: dict[str, str] = {
    "create_group": "create_group(group, members, schema): a new group of named members, typed when a schema of field "
                    "names and types is given",
    "send": "send(group, sender, body, to, dedupe_key): store a message for one member or the whole group; a retried "
            "send with the same dedupe_key is stored once",
    "receive": "receive(group, member, since_cursor, limit=50): the member's messages after its cursor, oldest first, "
               "a page at a time, each with how many times it was delivered",
    "ack": "ack(group, member, upto): acknowledge every message up to an id the member received",
    "history": "history(group, limit=100): every message of the group, with whom it was delivered to and who "
               "acknowledged it",
    "delete_group": "delete_group(group): delete the group and its messages, saying how many were not yet "
                    "acknowledged (sensitive: from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)",
}
_NEEDS = {"create_group": ("group", "members"), "send": ("group", "sender", "body"), "receive": ("group", "member"),
          "ack": ("group", "member", "upto"), "history": ("group",), "delete_group": ("group",)}


class AgentNanoclawInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["create_group", "send", "receive", "ack", "history", "delete_group", "ops", "help"] = "help"
    group: str = Field("", max_length=64)
    members: list[str] = Field(default_factory=list, max_length=50)
    schema_: dict[str, FieldType] | None = Field(None, alias="schema")
    sender: str = Field("", max_length=64)
    to: str | None = Field(None, max_length=64)
    body: Any = None
    dedupe_key: str | None = Field(None, max_length=64)
    member: str = Field("", max_length=64)
    since_cursor: int | None = Field(None, ge=0)
    upto: int | None = Field(None, ge=0)
    limit: int = Field(50, ge=1, le=1000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _run(data: AgentNanoclawInput) -> dict[str, Any]:
    op = data.op
    missing = [f for f in _NEEDS[op] if getattr(data, f) in ("", None, [])]
    if missing:
        out = reply(op, "blocked-escalated", REQUIRED, f"{op} needs {', '.join(missing)}")
    else:
        box = Mailbox()
        out = {"create_group": lambda: box.create_group(data.group, data.members, schema=data.schema_),
               "send": lambda: box.send(data.group, data.sender, data.body, to=data.to, dedupe_key=data.dedupe_key),
               "receive": lambda: box.receive(data.group, data.member, since_cursor=data.since_cursor,
                                              limit=min(data.limit, 200)),
               "ack": lambda: box.ack(data.group, data.member, upto=int(data.upto or 0)),
               "history": lambda: box.history(data.group, limit=data.limit),
               "delete_group": lambda: box.delete_group(data.group)}[op]()
    return {**out, "request_id": data.request_id, "run_id": data.run_id}


@component_maturity(
    "beta",
    reason="a durable SQLite mailbox under G6's state folder: cursors that page through every message, at-least-once "
           "delivery with acknowledgements, stored-once retries, addressed and typed messages; answers are facts of "
           "its own record",
)
class AgentNanoclawBlock(AIBlock[AgentNanoclawInput, dict, dict]):
    """Carry messages between agents. Never ``verified``."""

    def __init__(self, name: str = "agent_nanoclaw", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AgentNanoclawInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_run(data))


__all__ = ["CODES", "READ_ONLY_OPS", "REQUIRED", "SENSITIVE_OPS", "AgentNanoclawBlock", "AgentNanoclawInput"]