---
name: g6-agent-nanoclaw
description: Use when several agents must pass messages to one another durably (groups of named members, messages to one member or to all, optionally typed), with every message reaching its reader at least once, in order, a page at a time, coming back until it is acknowledged, a retried send stored once, and none reaching the wrong member.
---

# G6 mailbox

A durable mailbox for agents, in one SQLite file under G6's state folder. A group has named members and, if it is
typed, a message schema checked on send. A member receives the messages for it after its cursor, oldest first, a page
at a time, and acknowledges up to the last one it handled; until then the messages come back on its next receive,
each saying how many times it was delivered. It carries messages; it is not an agent.

## When to use it

- Agents (or runs of one agent) that hand work to one another and must not lose a message when one of them crashes.
- A message that must reach one member only, or every member of a group.
- Messages with a fixed shape, refused at send when they do not fit.

## When not to use it

- For a single agent's own notes: there is nothing to deliver.
- When the sender's identity must be proved: the sender is the name the caller gives, checked against the group's
  members, not authenticated.
- For a durable workflow with steps and resumes: that is `agent_langgraph`.
- For decisions a person must approve: those go through `action_gating`, not a message.

## Ops

- `create_group`: a new group of named members (`group`, `members`, `schema`), typed when a schema of field names and types is given.
- `send`: store a message for one member or the whole group (`group`, `sender`, `body`, `to`, `dedupe_key`); a retried send with the same `dedupe_key` is stored once.
- `receive`: the member's messages after its cursor, oldest first, a page at a time (`group`, `member`, `since_cursor`, `limit`), each with how many times it was delivered.
- `ack`: acknowledge every message up to an id the member received (`group`, `member`, `upto`).
- `history`: every message of the group (`group`, `limit`), with whom it was delivered to and who acknowledged it.
- `delete_group`: delete the group and its messages (`group`), saying how many were not yet acknowledged; sensitive: from Python, or over MCP when the server's `G6_MCP_SENSITIVE` lists it.

## Reading the result

Every op answers what it did as a fact of G6's own record: `qualified-draft` with a `G6_E_MAILBOX_*` code
(`GROUP_CREATED`, `STORED` with the message's `id`, `ALREADY_STORED`, `DELIVERED` with `messages`, `next_cursor` and
`more`, `ACKED` with `acked_upto`, `HISTORY`, `GROUP_DELETED`). Each received message carries its `id`, `sender`
(with `sender_is`: declared, not authenticated), `to`, `body`, `sent` and `deliveries`. `blocked-escalated` codes:
`G6_E_MAILBOX_NO_GROUP`, `G6_E_MAILBOX_GROUP_EXISTS`, `G6_E_MAILBOX_NOT_A_MEMBER`, `G6_E_MAILBOX_BAD_NAME`,
`G6_E_MAILBOX_BAD_MESSAGE` (it does not fit the group's schema), `G6_E_MAILBOX_TOO_LARGE`, `G6_E_MAILBOX_ACK_UNSEEN`
(an acknowledgement past what the member received, or over a message never delivered to it),
`G6_E_MAILBOX_GROUP_FULL`, `G6_E_MAILBOX_STORE_FULL` and `G6_E_MAILBOX_REQUIRED`. A group holds at most 10,000
messages, the store at most 256 MiB of message bodies, and a body at most 64 KiB.

## Alone and composed

Alone, over MCP: `g6_invoke("agent_nanoclaw", "send", params_json)` and the rest; from Python, `Mailbox()` with the
same six methods. A reader's loop: `receive`, handle each message (skipping an id it has handled), `ack` up to the last
id, and `receive` again while `more` is true.

Composed:

- `agent_runtime` and its adapters (`agent_codex`, `agent_claude`, `agent_autogen`, `agent_langgraph`,
  `agent_smolagents`): agents that hand work to one another through a group.
- `g6.harness`: judges what an agent did with a message, never the mailbox.

## Failure modes it catches

- `lost_agent_message` (messages between agents lost or mixed up): a reader pages through every message for it with
  a cursor, however many there are; a message it received and did not acknowledge comes back after a crash, in this
  process or another, saying how many times it was delivered; an acknowledgement past what it received, or over a
  message never delivered to it, is refused, so nothing is skipped unseen; a retried send with the same `dedupe_key`
  is stored once; a message to one member
  reaches only that member, and a sender is not sent its own; a typed group refuses a message that does not fit.

## Blind spots

- The sender is the name the caller declares; any caller that can reach the mailbox can send as any member.
- Delivery is at least once: a reader that crashes after handling a message and before acknowledging it gets it
  again, and must drop it by its id.
- A message body is text another agent reads, and it can carry instructions that agent's model follows
  (`prompt_injection`).
- Anyone who can write G6's state folder can change the messages; the mailbox keeps no signature.
- `history` shows every message of a group, whoever it was for.
- An agent run by the family's runtime may call `history` in a dry run, the one op that changes nothing; any other
  op is a dry run there, and `delete_group` is refused.

## What it needs

Nothing outside G6: the standard library's SQLite, through `g6.core.storage`. No pip extra, no outside program.

## Examples

```g6-example op=create_group
{"group": "example-build", "members": ["planner", "coder"], "schema": {"task": "text"}}
```

A typed group of two; a second call answers `G6_E_MAILBOX_GROUP_EXISTS`.

```g6-example op=send
{"group": "example-build", "sender": "planner", "to": "coder", "body": {"task": "write the tests"}, "dedupe_key": "task-1"}
```

Stored, with its id; sent again with the same `dedupe_key` it is `G6_E_MAILBOX_ALREADY_STORED`.

```g6-example op=receive
{"group": "example-build", "member": "coder"}
```

The coder's messages after its cursor, oldest first, with `next_cursor` and `more`.

```g6-example op=ack
{"group": "example-build", "member": "coder", "upto": 1}
```

Acknowledges up to message 1, if the coder received it; otherwise `G6_E_MAILBOX_ACK_UNSEEN`.

```g6-example op=history
{"group": "example-build"}
```

Every message of the group, with whom it was delivered to and who acknowledged it.

```g6-example op=delete_group
{"group": "example-build"}
```

Deletes the group, saying how many messages went and how many were not yet acknowledged. It is sensitive, so over MCP
it runs only where the server's owner allows it.

```python
from g6.agents.agent_nanoclaw import Mailbox

box = Mailbox()
box.create_group("example-review", ["author", "reviewer"])
first = box.send("example-review", "author", "please review the change", to="reviewer")
page = box.receive("example-review", "reviewer")
assert [m["id"] for m in page["messages"]] == [first["id"]] and page["more"] is False
again = box.receive("example-review", "reviewer")          # not acknowledged yet, so it comes back
assert again["messages"][0]["deliveries"] == 2
box.ack("example-review", "reviewer", upto=first["id"])
assert box.receive("example-review", "reviewer")["messages"] == []
box.delete_group("example-review")
```

## The four open problems

- **Learning signals**: each message records how many times it was delivered to each member and who acknowledged it,
  so redelivery counts and the time to acknowledge can be read from `history`. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: cursors, acknowledgements, addressing and the schema are fixed code; what
  a message says never changes how it is delivered or labelled.
- **Transfer by failure mode**: `lost_agent_message` is handled by the store for any agents that use it, of any
  framework.
- **Enough held-out cases**: Not yet: its tests send, crash and re-read a few hundred messages; many writers at once
  are not measured.