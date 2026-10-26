# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_nanoclaw — a durable mailbox between agents: each reader pages through every message for it, and one it did not acknowledge comes back after a crash.

An extension in the agents family: the mailbox, the owner's 2026-10-20 merge target for ``agent_nanoclaw`` and
``agent_openclaw`` (a channel became a group; "typed" became a group's message schema, checked on send). The archive's
two near-duplicate stores under the home folder returned only the newest 50 messages, or every message on a channel to
every reader, never marked one delivered, and called each op ``verified``.

Capability label: ``implemented``.

    from g6.agents.agent_nanoclaw import Mailbox
    box = Mailbox()                                          # $G6_HOME/agents/mailbox.db
    box.create_group("build", ["planner", "coder"], schema={"task": "text"})
    box.send("build", "planner", {"task": "write the tests"}, to="coder", dedupe_key="t-1")
    page = box.receive("build", "coder")                     # oldest first; next_cursor and more page on
    box.ack("build", "coder", upto=page["messages"][-1]["id"])   # until then it comes back on the next receive

The mailbox (:mod:`~g6.agents.agent_nanoclaw.mailbox`) is one SQLite file under G6's state folder, through
``g6.core.storage.open_db``. Delivery is at least once: a member's messages come back until it acknowledges them, each
saying how many times it was delivered, so a reader drops a repeat by its id; a retried send with the same
``dedupe_key`` is stored once; a message addressed to one member reaches only that member. The sender is the name the
caller declares, checked against the group's members, not authenticated. It carries messages; it is not an agent.

Ops (:mod:`~g6.agents.agent_nanoclaw.agent_nanoclaw_block`): ``create_group``, ``send``, ``receive``, ``ack``,
``history`` and ``delete_group``; ``delete_group`` destroys a group and its messages, so it is sensitive.

Not ported from the archive: tasks, "sandboxes" (a status row that isolated nothing), memories, skills (whose invoke ran
nothing), TF-IDF search, the claw runtime adapter that could run a configured program, the backend planner, the provider
"TOS" check, and from ``agent_openclaw`` its webhooks (stored, never sent), contacts, scheduling and claw adapter.
"""
from g6.agents.agent_nanoclaw.agent_nanoclaw_block import (
    READ_ONLY_OPS,
    SENSITIVE_OPS,
    AgentNanoclawBlock,
    AgentNanoclawInput,
)
from g6.agents.agent_nanoclaw.mailbox import Mailbox

__g6_component__ = True
__g6_block__ = "AgentNanoclawBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # delete_group destroys a group and its messages
__g6_read_only_ops__: tuple[str, ...] = READ_ONLY_OPS  # the ops an agent's dry run may call (g6.agents._runtime)
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a message body is
# text another agent reads, and it can carry instructions that agent's model follows).
__g6_mitigates__: tuple[str, ...] = ("lost_agent_message",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()

__all__ = ["AgentNanoclawBlock", "AgentNanoclawInput", "Mailbox"]