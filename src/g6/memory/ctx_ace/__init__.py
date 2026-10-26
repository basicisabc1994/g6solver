# SPDX-License-Identifier: Apache-2.0
"""g6.memory.ctx_ace — keep an agent's notes and find them again by relevance, each read back as data with its source, writer and age.

An extension in the memory family, and the family's core store (standard library only). ``put`` keeps a note, or a
lesson for the playbook, as a :class:`~g6.memory._types.MemoryRecord` in a SQLite file under G6's state folder
(``$G6_HOME/memory/ctx_ace.db``). ``search`` ranks every record by BM25 relevance to the query alone
(``g6.grounding.TextRetriever``), so a relevant old note is not buried under new ones and no writer's weight lifts a
note to the top; ``recall`` lists records by id, tag or kind. A record past its ``stale_after`` is returned apart, as
stale, never as current. Who wrote a record (``written_by``) is the writer's claim and is kept as one.

    from g6.memory.ctx_ace import Store
    store = Store()                                  # or Store(path) for another file
    store.put(text="Refunds take 14 days.", written_by="agent:run-7", source="https://example.org/policy")
    found = store.search("how long do refunds take")
    record, score = found.current[0]
    record.to_context_item().may_instruct            # False: a kept note is data, never an instruction

A lesson is a proposal until an approval is recorded (``approve``: the reviewer's name is kept as the approving
caller's claim, which G6 does not check), and even then it is served only as an untrusted note marked
``approval_is: "claim"``: the store applies nothing. ``forget`` removes what a filter names, for a reason, and checks
each record is gone. Both are sensitive, so over MCP they run only when the person who runs the server allows them;
allow ``ctx_ace.forget`` alone, since a model allowed ``approve`` can approve its own lesson under any name.

Not ported from the archive's ctx_ace, and why: the 26-op playbook server and its train step, which applied
model-written lessons to a playbook later prompts were told to obey (CTX_ACE-01); the ranking that multiplied
relevance by a weight the writer chose (CTX_ACE-02); the substring test that graded answers (CTX_ACE-03); a forget
that emptied everything when no filter was given (CTX_ACE-04); its prompt texts, whose origin and licence are not
established, and the model calls behind them (CTX_ACE-05); and the scaffold (CTX_ACE-06).
"""
from g6.memory._types import Approval, MemoryRecord, is_stale
from g6.memory.ctx_ace.block import SENSITIVE_OPS, CtxAceBlock, CtxAceInput
from g6.memory.ctx_ace.store import Found, Receipt, Store, StoreError

__g6_component__ = True
__g6_block__ = "CtxAceBlock"
__g6_family__ = "memory"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # forget deletes kept records; approve changes what is served
__g6_requires__: tuple[str, ...] = ("context", "grounding", "knowledge")  # g6.context's memory_note, BM25, Passage
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("buried_observation", "stale_context_used", "memory_poisoning")
__g6_introduces__: tuple[str, ...] = ("under_retrieval",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "Approval", "CtxAceBlock", "CtxAceInput", "Found", "MemoryRecord", "Receipt", "Store",
           "StoreError", "is_stale"]
