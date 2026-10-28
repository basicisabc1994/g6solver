# SPDX-License-Identifier: Apache-2.0
"""Coding-agent gate: deterministic checks a coding agent runs before it acts and before it reports work as done.

Capability label: ``implemented``.

    from g6.coding_agent_gate import Evidence, verify_answer
    d = verify_answer("Fix the off-by-one in paginate()", "Changed range(n) to range(n + 1) in paginate().",
                      task_contract="paginate(10, 3) returns 4 pages; existing tests stay green",
                      evidence=[Evidence("file", "src/page.py"),
                                Evidence("test", "pytest -q tests/test_page.py", exit_code=0)])
    d.verdict, d.completion_state      # ("pass", "qualified-draft")

``preflight`` checks a task and the agent's proposed action (``block`` / ``needs_review`` / ``needs_evidence`` /
``proceed_with_caution``); ``verify_answer`` checks a drafted answer (``block`` / ``needs_review`` / ``revise`` /
``pass``). Evidence counts only with the structure its kind needs: a file path, a test command with its exit code, a
non-empty log, a unified diff, or a review naming a reviewer other than the author. Placeholders such as ``none``,
``TODO``, ``not run`` or "tests pass" with no exit code never count, and a call with no counted evidence never
passes. A declared risk tier can raise the computed tier, never lower it; high-risk work without a named review is
``needs_review``, a blocking state, as are a shortcut and (by default) a missing task contract. ``review.tighten``
is an opt-in model reviewer that can only make a decision stricter; nothing else calls a model, reads the
environment or writes a file.

What it does not establish (audit 004): it reads what the caller sends. It does not run a test or check that a
reviewer exists, and the only file it opens is G6's own test-receipt store, read when an item cites a receipt and the
host built the block with ``receipt_artifact_digest``. Evidence is ``observed_by="caller"`` unless a G6 component
produced it, and everything through the registry or MCP door is caller-declared: the door's instance has no binding,
so a receipt there is ``not_honoured_at_door`` (review C1). A host that binds the block to the digest of the tree it
reviews (``g6.harness.patch_review``) gets a cited receipt the store holds for that digest counted as G6-observed
(``g6.workforce.receipt_store``: only G6's confined runs write receipts; the store is a file owned by the same user,
so it defends against what a caller sends, not against code running as that user).
The shortcut text detector is a tripwire fitted to known
phrasings (it caught none of the audit's fresh paraphrases); a supplied diff is scanned for its structure, but
semantic shortcuts (a snapshot pinned to wrong output, a test made to return True) are out of reach. So a pass is
``qualified-draft``, never ``verified``, and the gate is advisory unless its caller obeys it. Its effect on agent
behaviour has not been measured.
"""
from g6.coding_agent_gate.coding_agent_gate_block import CodingAgentGateBlock, CodingAgentGateInput
from g6.coding_agent_gate.detectors import scan_diff, shortcut_hits
from g6.coding_agent_gate.evidence import Evidence, VerifierResult
from g6.coding_agent_gate.protected_paths import PathExemption, ProtectedPathsPolicy
from g6.coding_agent_gate.gate import (
    SEVERITY,
    Finding,
    GateDecision,
    preflight,
    stricter_of,
    surface_state,
    verify_answer,
)

__g6_component__ = True
__g6_block__ = "CodingAgentGateBlock"
__g6_requires__: tuple[str, ...] = ("workforce",)   # the test-receipt store, read when an item cites a receipt
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("hallucinated_success", "unapproved_protected_change")
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["SEVERITY", "CodingAgentGateBlock", "CodingAgentGateInput", "Evidence", "Finding", "GateDecision",
           "VerifierResult", "preflight", "scan_diff", "shortcut_hits", "stricter_of", "surface_state",
           "verify_answer", "PathExemption", "ProtectedPathsPolicy"]
