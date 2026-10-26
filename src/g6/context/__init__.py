# SPDX-License-Identifier: Apache-2.0
"""g6.context — assemble a small, budget-bounded context packet for one agent call, each item labelled with where it came from.

g6.context — assemble a small, budget-bounded, provenance-labelled context packet for one agent call from a task,
keyword matches in directories you choose and workspace notes (audit 032 §11).

Capability label: ``implemented``.

    from g6.context import Retriever, Workspace, build_packet, load_handle
    retriever = Retriever(["src", "tests"])
    packet = build_packet("fix the parser timeout", retriever, 4000, workspace=Workspace("/tmp/ctx"))
    packet.completion_state          # "qualified-draft" at best; "blocked-escalated" with no task or no room for it
    loaded = load_handle(retriever, packet.repository_handles[0], "fix the parser timeout")
    loaded.trust                     # "untrusted": repository text is data, not instructions

``Retriever`` matches query words in file paths and lines (a thin layer over a substring search). ``build_packet``
adds a governance line, the task, caller-supplied component metadata, workspace notes, a verification policy line
and the handles that fit the budget; ``select_within_budget`` / ``prune_packet`` hold the token budget and never drop
the task. ``score_packet`` recomputes the envelope from the packet's content, never from its own state fields; an
assembled packet is never ``verified``, and an empty retrieval, a stale note, a conflicting value or a pruned
packet each add evidence. ``scrub`` masks recognised secrets when an item is created, in ``load_handle``, in every
``Workspace`` writer and before a re-ranker sees anything. ``Workspace(root)`` holds all state under one directory
and writes only inside it. A re-ranker is an optional callable, OFF by default; whether one picks better context than
keyword order has not been measured.

``ConversationWindow`` (merged from the archive's ``ctx_claude_context``) keeps a conversation within a model's
window: the budget counts the system prompt, every message and an output reserve, each count with its tokenizer's
margin (characters / 4 with 30% by default); ``trim`` drops the oldest turns, never the newest, a retention planner
only through the same off-by-default reranker seam; ``compress`` folds them into an untrusted ``memory_note``, quoted
in a user turn, never a system message, and lossy.

``mask_emails``, ``mask_paths``, ``mask_hosts`` and ``sanitize_stack_trace`` (merged from the archive's
``diagnostic_collector``) mask e-mail addresses, home and temporary roots, URL user names, host names and IP addresses
(``KEPT_TEXT_MASKS`` lists each shape) and read a traceback's frames without its source lines; ``g6 doctor
--report`` builds one local error report from them, caller text stripped by default. Its sink, queue, rate limiter,
index, ``input_model`` import and hosted branches did not come across.

Not ported from the archive's ``context_engine`` (audit 032), and why:

- the situation classifier (``context_engine_block.py``): it answered ``novel`` on every first update and became
  ``fatigued`` from an update counter (CTX-002, CTX-013);
- ``authorization_pullback.py``: it authorized empty plans and any approver string, and had no caller (CTX-008);
- ``scope_filter.py``, ``evidence_cosheaf.py``, ``context_sheaf.py``: research code with no caller; the ACL failed
  open on an unknown audience (CTX-009);
- ``code_graph.py``: flag-gated and wrote an index into the user's checkout;
- the MCP server, its 36 ops and SQLite store: declared retention it did not implement, unvalidated imports and a
  global database under the home directory (CTX-014, CTX-007);
- ``agentic_runtime.py`` / ``agentic_evidence.py``: the LLM re-ranker is deferred; ``build_packet(reranker=...)`` is
  the seam (CTX-005, CTX-015);
- ``patterns.py``, ``skills.py``, ``skill/``: documentation catalogues;
- the file read cache: the archive shipped it off after measuring no benefit;
- the component registry lookup: the caller passes component metadata instead.
"""
from g6.context.assemble import (AUTHORITY_ORDER, VERIFICATION_POLICY, Reranker, assemble_packet, build_packet,
                                 subagent_packet, synthesize_subagent_results)
from g6.context.budget import PROTECTED_SECTIONS, PRUNE_ORDER, REPOSITORY_SHARE, prune_packet, select_within_budget
from g6.context.envelope import CHECKS_RUN, NOT_CHECKED, rescore_packet, score_packet
from g6.context.packet import (PACKET_SECTIONS, AuthorityLevel, ContextItem, ContextPacket, ContextScorecard,
                               RepositoryHandle, SubagentResult, estimate_tokens, find_conflicts, merge_context_items,
                               validate_name)
from g6.context.retrieve import DEFAULT_IGNORE, DEFAULT_SUFFIXES, LoadedText, Retrieval, Retriever, load_handle
from g6.context.redact import (EMAIL_MASK, KEPT_TEXT_MASKS, MASK, mask_emails, mask_hosts, mask_paths,
                               sanitize_stack_trace, scrub)
from g6.context.rerank import RerankDecision, validate_rerank_decision
from g6.context.fuse import fuse
from g6.context.compress import compress
from g6.context.window import ConversationWindow, WindowMessage, WindowReport
from g6.context.workspace import Workspace

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("long_context_degradation", "prompt_injection", "context_budget_overflow",
                                    "leaky_error_report")
__g6_introduces__: tuple[str, ...] = ("compaction_lost_decision",)

__all__ = ["compress", "fuse", "RerankDecision", "validate_rerank_decision", "AUTHORITY_ORDER", "CHECKS_RUN", "DEFAULT_IGNORE", "DEFAULT_SUFFIXES", "EMAIL_MASK", "MASK", "NOT_CHECKED",
           "KEPT_TEXT_MASKS", "mask_emails", "mask_hosts", "mask_paths", "sanitize_stack_trace",
           "PACKET_SECTIONS", "PROTECTED_SECTIONS", "PRUNE_ORDER", "REPOSITORY_SHARE", "VERIFICATION_POLICY",
           "AuthorityLevel", "ContextItem", "ContextPacket", "ContextScorecard", "ConversationWindow", "LoadedText",
           "Reranker", "RepositoryHandle", "Retrieval", "Retriever", "SubagentResult", "WindowMessage", "WindowReport",
           "Workspace", "assemble_packet",
           "build_packet", "estimate_tokens", "find_conflicts", "load_handle", "merge_context_items", "prune_packet",
           "rescore_packet", "score_packet", "scrub", "select_within_budget", "subagent_packet",
           "synthesize_subagent_results", "validate_name"]
