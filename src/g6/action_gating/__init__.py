# SPDX-License-Identifier: Apache-2.0
"""Action gating: a deterministic pre-flight decision for an action an agent wants to take.

Capability label: ``implemented``.

Use it before an agent acts, ahead of execution: before it sends an email or message, makes a payment, deletes or
overwrites files, runs a shell command, deploys, or changes permissions. It says whether to allow the action, ask a
person, or block it.

    from g6.action_gating import evaluate_action
    ev = evaluate_action({"action_type": "delete_file", "target_backend": "local",
                          "target_resource": "notes.txt"})
    ev.gate_decision.decision   # "allow" | "require_confirmation" | "require_human_review" | "block"

What it does: normalises the action into an ``ActionCapsule``, classifies reversibility and
impact, runs the declared-context checks from ``g6.action_logic`` (verification contract,
coordination, weak signals / threshold breach, hypotheses when enabled), and returns a
decision with reason codes, a warning card and evidence. An irreversible, external or
real-world action that omits a check's context is blocked (no verification contract) or sent
to review (no coordination context or world state). Audit records go to a durable,
hash-chained trail under ``$G6_HOME``. ``native_ops`` adds a durable, single-use gate-token
ledger for a simulated executor.

What it does not do (audit 002): it does not execute anything, authenticate approvals, or
observe the world — risk facts are declared by the caller (for a known action type they can
only raise its profile, and declared authority is never treated as observed). Do not place it
as the only security boundary in front of a real executor.

A review scope (``scope.ReviewScope``, merged from the archive's ``domain_certification``) is
declared context for ``evaluate_action(action, scope=...)``: an action outside it, above its
risk ceiling or after its expiry goes to a person, a forbidden one is blocked, and nothing in
it can lower a hold. ``scope.review_record`` reads what was observed for a scope through
``g6.evidence``, plus a person's decision recorded on the control plane; it grants nothing.
"""
from g6.action_gating.action_gating_block import ActionGatingBlock, ActionGatingInput
from g6.action_gating.decision_engine import decide_gate, evaluate_action
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.operation_budget import OperationBudget
from g6.action_gating.scope import ReviewScope, review_record

__g6_component__ = True
__g6_block__ = "ActionGatingBlock"
__g6_sensitive_ops__ = ("budget_check", "budget_record", "budget_policy")
__g6_requires__ = ("action_logic", "epistemic_integrity", "csf", "evidence")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unsafe_autonomous_action", "repeated_mistake", "fail_open_check",
                                    "overstated_verification", "tampered_audit_record", "unbounded_risky_operation",
                                    "beyond_review_scope")
__g6_introduces__: tuple[str, ...] = ()

__all__ = [
    "ActionGatingBlock",
    "ActionGatingInput",
    "OperationBudget",
    "ReviewScope",
    "decide_gate",
    "evaluate_action",
    "normalize_action_capsule",
    "review_record",
]
