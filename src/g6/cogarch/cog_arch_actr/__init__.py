# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.cog_arch_actr — rank an agent's memories by recency, frequency and context with ACT-R's equations, refusing a recall below the retrieval threshold.

An extension in the cogarch family (standard library only): ACT-R declarative memory for agents, with a small
procedural module, not a validated model of any person's memory. ``retrieve`` returns the matching chunk with the
highest activation, with its breakdown (base level from every presentation, spreading from a context, seeded logistic
noise) and its latency, or a ``blocked-escalated`` refusal when the best match is below the threshold. ``run`` fires
productions with variables and retrieval requests until the caller's goal test passes against the goal buffer, and
``reward`` teaches their utilities. Every presentation and utility is written to the memory's file under G6's state
folder, so learning accumulates across calls.

    from g6.cogarch.cog_arch_actr import CogArchActrBlock, CogArchActrInput
    block = CogArchActrBlock()
    block.infer(CogArchActrInput(op="add_chunk", name="refund-days", isa="policy", slots={"topic": "refund",
                                                                                         "days": 14}))
    out = block.infer(CogArchActrInput(op="retrieve", request={"isa": "policy", "topic": "refund"},
                                       threshold=-5)).unwrap()
    out["chunk"]["days"], out["breakdown"]["base_level"], out["latency_s"]

The equations and their defaults are the ACT-R 7.30+ Reference Manual's; ``source_register.json`` holds each clause
with its page and SHA-256. No op is sensitive: none reaches the network or acts outside G6, and a memory is written
only under G6's own state folder.

Not ported from the archive's cog_arch_actr, and why: the realtime bridge, an HTTP, websocket and SQLite reader on an
allowlist the caller supplied (ACTR-01); the engine reloaded from the store after each change, which threw away every
retrieval's access time and every utility update (ACTR-02); the equality-only matcher whose productions never asked
memory for anything (ACTR-03); the noise drawn twice from the module's unseeded generator (ACTR-04); the HAT gate
that approved on the caller's word (ACTR-05); the claude CLI model door and the two model ops (ACTR-06); the second
block, the home-folder database and the scaffold (ACTR-07); the six discovery ops; and find_analogy, a slot-overlap
count (ACTR-09).
"""
from g6.cogarch.cog_arch_actr import equations, memory
from g6.cogarch.cog_arch_actr.block import SENSITIVE_OPS, CogArchActrBlock, CogArchActrInput

__g6_component__ = True
__g6_block__ = "CogArchActrBlock"
__g6_family__ = "cogarch"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # nothing reaches the network or acts outside G6
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a threshold
# that refuses a recall can hide a memory that was relevant).
__g6_mitigates__: tuple[str, ...] = ("unmodelled_recall",)
__g6_introduces__: tuple[str, ...] = ("under_retrieval",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "CogArchActrBlock", "CogArchActrInput", "equations", "memory"]
