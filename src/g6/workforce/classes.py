# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Failure classes (Phase E Part 1, E2a; Ledger §26, §15; Design §23).

Every failure code an attempt can record (``task_runs.failure_code``: the rungs', the door's refusals, the
ledger's and the runtime's) and every code of the venture operator's taxonomy (``venture/failures.CODES``) is one
of the Ledger's 14 classes. ``tests/mvp/venture/test_workforce_classes.py`` scans the package and fails when a code
is written that is not mapped here; a code nobody mapped reads ``UNKNOWN``, never "no failure".

The class decides the recovery (Ledger §15's hierarchy). The runtime by itself only tries the same call again
after a failure that may not recur, or sends a failed output back to the owner's chat with the check's reasons
(V4b). Anything else needs the plan changed or a person, so the step stops at once and says so (``explain``) in the
four parts Design §23 asks for, ending with a stable code that already exists in the shared catalog (plan ruling
R11: the catalog is outside this package, so no new code is introduced here).

Phase E Part 2 (E8a; plan ruling P2.16): a code's advice is one a person can act on when they read it (a running
mission's plan and budget cannot change), and ``problem`` gives the message in the result contract's structured form.
"""
from __future__ import annotations

from g6.workforce.result_view import Problem

CLASSES = ("TRANSIENT_TOOL_FAILURE", "INVALID_PARAMETERS", "CAPABILITY_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE",
           "INSUFFICIENT_EVIDENCE", "VERIFICATION_FAILURE", "MODEL_REASONING_FAILURE", "PLAN_FAILURE",
           "PERMISSION_DENIED", "BUDGET_EXHAUSTED", "TIMEOUT", "EXTERNAL_STATE_AMBIGUOUS", "HUMAN_INPUT_REQUIRED",
           "UNKNOWN")

#: Every code a workforce attempt records, and its class.
WORKFORCE_CODES: dict[str, str] = {
    # rungs.py: a unit no rung can run, a missing input, and how a call or a check did not answer
    "no_rung": "PLAN_FAILURE", "no_oracle": "PLAN_FAILURE", "oracle_unbound": "PLAN_FAILURE",
    "unbound_input": "DEPENDENCY_UNAVAILABLE", "nothing_to_verify": "DEPENDENCY_UNAVAILABLE",
    "call_unreachable": "TRANSIENT_TOOL_FAILURE", "call_timeout": "TIMEOUT", "call_raised": "UNKNOWN",
    # AT16: a write that timed out may have happened, so the same call is never sent again; a person checks first
    "call_timeout_unconfirmed": "EXTERNAL_STATE_AMBIGUOUS",
    "call_blocked": "CAPABILITY_UNAVAILABLE",  # P2.16(d): the same call answers the same way, never retried
    "verify_failed": "VERIFICATION_FAILURE", "verify_unmeasured": "INSUFFICIENT_EVIDENCE",
    # runs.py: why an attempt was abandoned, a verdict bound to nothing, and a blocked close given no code
    "abandoned": "UNKNOWN", "abandoned:claim": "DEPENDENCY_UNAVAILABLE",
    "abandoned:lease": "TRANSIENT_TOOL_FAILURE", "abandoned:clock": "TIMEOUT",
    "verdict_unbound": "INSUFFICIENT_EVIDENCE",
    "verdict_stale": "DEPENDENCY_UNAVAILABLE",  # P2.16(f): the output changed while it was judged; judged again
    "unclassified": "UNKNOWN",
    # runtime.py: a gate a person has not decided or rejected; a saved result that may no longer be reused (E2b);
    # an output too large to keep, and a result the ledger refused to record: the same call would end the same way,
    # so neither is tried again (fix I1 of the Part 1 review)
    "undecided": "HUMAN_INPUT_REQUIRED", "rejected": "PERMISSION_DENIED", "stale_reuse": "DEPENDENCY_UNAVAILABLE",
    "output_too_large": "PLAN_FAILURE", "unrecorded": "PLAN_FAILURE",
    "coding_scope": "PERMISSION_DENIED",  # returned patch violates the frozen file permissions/shape
    # calls.py: the door's refusals (calls.REFUSALS)
    "refused:allowlist": "PERMISSION_DENIED", "refused:undeclared": "CAPABILITY_UNAVAILABLE",
    "refused:params": "INVALID_PARAMETERS", "refused:sensitive": "PERMISSION_DENIED",
    "refused:read_only": "PERMISSION_DENIED", "refused:killed": "PERMISSION_DENIED",
    "refused:ceiling": "PLAN_FAILURE",  # P2.16(b): a step needing more calls than one worker may make
    "refused:clock": "TIMEOUT", "refused:policy": "PERMISSION_DENIED",
    "refused:permit": "PERMISSION_DENIED", "refused:budget": "BUDGET_EXHAUSTED",
    "refused:unpriced": "PERMISSION_DENIED",  # P2.16(a): a policy refusal on a charged run, not a budget run out
    # written before E2a split the door's refusals: still on old rows, never written now (plan ruling R14)
    "refused": "UNKNOWN",
}
#: Codes old rows carry that nothing writes any more.
LEGACY_CODES = frozenset({"refused"})
#: The venture operator's taxonomy (``venture/failures.py``), plan ruling R17.
VENTURE_CODES: dict[str, str] = {
    "VERIFY_TESTS": "VERIFICATION_FAILURE", "VERIFY_SCHEMA": "VERIFICATION_FAILURE",
    "VERIFY_QUEUE": "VERIFICATION_FAILURE", "VERIFY_STANDARD": "VERIFICATION_FAILURE",
    "VERIFY_TEST_INTEGRITY": "VERIFICATION_FAILURE", "VERIFY_SCOPE": "VERIFICATION_FAILURE",
    "VERIFY_CITES": "INSUFFICIENT_EVIDENCE", "VERIFY_UNCHECKED": "VERIFICATION_FAILURE",
    "VERIFY_REGRESSION": "VERIFICATION_FAILURE",
    "NO_PROGRESS_EMPTY": "MODEL_REASONING_FAILURE", "NO_PROGRESS_TURNS": "MODEL_REASONING_FAILURE",
    "NO_PROGRESS_STREAK": "MODEL_REASONING_FAILURE", "BUDGET_TASK": "BUDGET_EXHAUSTED",
    "BUDGET_VENTURE": "BUDGET_EXHAUSTED", "TOOLING": "CAPABILITY_UNAVAILABLE", "UNKNOWN": "UNKNOWN",
}
#: A founder's coded decision on a venture draft (``failures.code_from_decision``).
DECISION_PREFIXES = {"REJECT_": "VERIFICATION_FAILURE", "INAPPROPRIATE_": "PERMISSION_DENIED"}
NOT_FAILURES = frozenset({"", "PASSED"})

#: Ledger §15's recovery hierarchy, in its order.
STEPS = ("transient_retry", "parameter_correction", "alternate_capability", "alternate_strategy",
         "stronger_resource", "decomposition", "replan", "human_escalation", "blocked")
#: The step each class calls for first.
RECOVERY = {
    "TRANSIENT_TOOL_FAILURE": "transient_retry", "TIMEOUT": "transient_retry", "UNKNOWN": "transient_retry",
    "DEPENDENCY_UNAVAILABLE": "transient_retry", "INSUFFICIENT_EVIDENCE": "transient_retry",
    "VERIFICATION_FAILURE": "alternate_strategy", "INVALID_PARAMETERS": "parameter_correction",
    "CAPABILITY_UNAVAILABLE": "alternate_capability", "MODEL_REASONING_FAILURE": "stronger_resource",
    "PLAN_FAILURE": "replan", "PERMISSION_DENIED": "human_escalation", "BUDGET_EXHAUSTED": "human_escalation",
    "EXTERNAL_STATE_AMBIGUOUS": "human_escalation", "HUMAN_INPUT_REQUIRED": "human_escalation",
}
#: Codes whose recovery is not their class's: a stale saved result is planned again, never waited for.
RECOVERY_BY_CODE = {"stale_reuse": "replan", "verify_unmeasured": "replan", "verdict_unbound": "human_escalation",
                    "refused:ceiling": "decomposition"}
#: What the runtime does by itself (plan ruling R10): the same call again, or V4b's send-back to the owner's chat.
RUNTIME_STEPS = frozenset({"transient_retry", "alternate_strategy"})

#: Each class's stable code, from the shared catalog (``core/error_codes.yaml``; plan ruling R11).
SURFACE = {
    "TRANSIENT_TOOL_FAILURE": "G6_E_RETRY", "INVALID_PARAMETERS": "G6_E_MISSING_PARAM",
    "CAPABILITY_UNAVAILABLE": "G6_E_CAPABILITY_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE": "G6_E_DEPENDENCY_UNAVAILABLE",
    "INSUFFICIENT_EVIDENCE": "G6_E_TASK_CONTRACT_UNVERIFIABLE", "VERIFICATION_FAILURE": "G6_E_RESULT_NOT_VERIFIED",
    "MODEL_REASONING_FAILURE": "G6_E_WORK_LOOP_NO_PROGRESS", "PLAN_FAILURE": "G6_E_CONFIG_INVALID",
    "PERMISSION_DENIED": "G6_E_POLICY_BLOCKED", "BUDGET_EXHAUSTED": "G6_E_COST_GATE_OVER_BUDGET",
    "TIMEOUT": "G6_E_TIMEOUT", "EXTERNAL_STATE_AMBIGUOUS": "G6_E_HUMAN_APPROVAL_REQUIRED",
    "HUMAN_INPUT_REQUIRED": "G6_E_PENDING_APPROVAL", "UNKNOWN": "G6_E_INTERNAL",
}
SURFACE_BY_CODE = {"refused:sensitive": "G6_E_SENSITIVE_COMPONENT_DENIED", "refused:unpriced": "G6_E_COST_GATE_GATED",
                   "refused:ceiling": "G6_E_RESOURCE_LIMIT", "unrecorded": "G6_E_INTERNAL"}

#: How material a stopped step is (Design §23 question 3), in plain words (§19), with no percentage (§11).
_KEPT = "The work done before it is kept, but this step's result is missing, so the answer cannot be verified."
MATERIAL = {cls: _KEPT for cls in CLASSES}
MATERIAL["BUDGET_EXHAUSTED"] = f"Nothing more was spent. {_KEPT}"
#: What would resolve it (Design §23 question 4), by class; a code may say more precisely.
RESOLVE = {
    "TRANSIENT_TOOL_FAILURE": "run the mission again later; the tool may answer then.",
    "INVALID_PARAMETERS": "correct this step's inputs in the plan, then run the mission again.",
    "CAPABILITY_UNAVAILABLE": "plan this step with a tool that is available here.",
    "DEPENDENCY_UNAVAILABLE": "make sure the step it depends on produced its result, then run the mission again.",
    "INSUFFICIENT_EVIDENCE": "give the check what it needs to decide, then run the mission again.",
    "VERIFICATION_FAILURE": "correct the result so that it passes the independent check.",
    "MODEL_REASONING_FAILURE": "split the task into smaller steps, or use a stronger approach.",
    "PLAN_FAILURE": "plan this step again so that it can be done and checked.",
    "PERMISSION_DENIED": "plan this step with a tool it is allowed to use, or ask a person to approve it.",
    "BUDGET_EXHAUSTED": "start the mission again with a larger budget, or plan this step with a cheaper tool (a "
                        "running mission's budget cannot be raised).",
    "TIMEOUT": "run the mission again, or give this step less to do at once.",
    "EXTERNAL_STATE_AMBIGUOUS": "check by hand whether the action happened before anything is tried again.",
    "HUMAN_INPUT_REQUIRED": "a person decides this step at the local command line.",
    "UNKNOWN": "run the mission again; if it stops here again, report it with this message.",
}
RESOLVE_BY_CODE = {
    "coding_scope": "repair the producing tool so its patch contains only the agreed source files; "
                    "start a new mission with the original objective and scope. The rejected result is kept.",
    "refused:unpriced": "run this mission on your own machine, or ask the operator to declare this tool's cost.",
    "stale_reuse": "plan this step again so G6 redoes the work instead of reusing that result.",
    "output_too_large": "plan this step so that its tool returns less, for example a narrower request or a summary, "
                        "or split the step into smaller ones.",
    "unrecorded": "report it with this message; the same attempt would be refused the same way, so G6 does not "
                  "try it again.",
    "refused:ceiling": "split this step into smaller steps: one step may make only so many calls.",
    "call_blocked": "give this step different inputs, or plan it with another tool; the same call would answer the "
                    "same way.",
    "verify_unmeasured": "plan the check so that it reads a field its tool returns; the same check would decide the "
                         "same way.",
    "verdict_unbound": "report it with this message; G6 does not count a check whose record does not match what it "
                       "judged, and does not try it again blind.",
    "verdict_stale": "run the mission again; the result changed while it was being checked.",
    "rejected": "address the person's reason, then start the mission again.",
}
#: Advice that differs once a mission runs: a running mission's plan cannot be replaced (plan ruling P2.16).
RESOLVE_AT_RUN = {"stale_reuse": "start a new mission without that saved result, so G6 redoes the work (a running "
                                 "mission's plan cannot be replaced)."}
#: When a message is read: while the plan is judged, or while the mission runs.
WHEN = ("run", "plan")
#: The door's own prefix on a refusal reason (an internal term a person need not read; E8a rewrites the rest).
_DOOR_PREFIX = "refused at the door: "
_WHY_CHARS = 400


def classify(code: str) -> str:
    """The code's Ledger §26 class; "" when it is no failure; ``UNKNOWN`` when nobody mapped it."""
    code = str(code or "")
    if code in NOT_FAILURES:
        return ""
    found = WORKFORCE_CODES.get(code) or VENTURE_CODES.get(code)
    if found:
        return found
    for prefix, cls in DECISION_PREFIXES.items():
        if code.startswith(prefix):
            return cls
    return "UNKNOWN"


def recovery(code: str) -> str:
    """The Ledger §15 step a failure with this code calls for first ("" when it is no failure)."""
    cls = classify(code)
    return RECOVERY_BY_CODE.get(code, RECOVERY[cls]) if cls else ""


def retried_by_runtime(code: str) -> bool:
    """Whether the runtime may try the unit again by itself after this code (no failure: it may)."""
    return not code or recovery(code) in RUNTIME_STEPS


def surface_code(code: str) -> str:
    """The stable ``G6_E_*`` code a person sees for this failure."""
    return SURFACE_BY_CODE.get(code) or SURFACE[classify(code) or "UNKNOWN"]


def resolve(code: str, when: str = "run") -> str:
    """What would resolve a failure with this code, as a person can act on it ``when`` they read it."""
    if when not in WHEN:
        raise ValueError(f"when is one of {WHEN}, got {when!r}")
    if when == "run" and code in RESOLVE_AT_RUN:
        return RESOLVE_AT_RUN[code]
    return RESOLVE_BY_CODE.get(code, RESOLVE[classify(code) or "UNKNOWN"])


def problem(code: str, why: str, *, what: str = "This step stopped", material: str | None = None,
            when: str = "run") -> Problem:
    """Design §23's four parts, structured (the result contract renders them, E8a): what failed and why, how material
    it is, what would resolve it, and the stable code."""
    cls = classify(code) or "UNKNOWN"
    why = str(why).removeprefix(_DOOR_PREFIX).strip().rstrip(".")[:_WHY_CHARS] or "no reason was recorded"
    return Problem(what=what, why=why, material=material or MATERIAL[cls], resolve=resolve(code, when),
                   code=surface_code(code))


def explain(code: str, why: str, *, what: str = "This step stopped", material: str | None = None,
            when: str = "run") -> str:
    """Design §23's four parts in one message, the stable code last (Design §5: technical detail last)."""
    return problem(code, why, what=what, material=material, when=when).text()


def why_retry(code: str) -> str:
    """Why another attempt is worth its cost (Ledger §15: the evidence a retry could succeed)."""
    if not code:
        return "its earlier output was replaced, so the work is done again on the new one"
    step = recovery(code)
    if step == "alternate_strategy":
        return "an independent check failed the last output; a new output is written with the check's reasons"
    if step == "transient_retry":
        return f"the last attempt failed in a way that may not recur ({classify(code)}), so the same call is tried again"
    return f"another attempt after {classify(code)} needs the plan changed or a person"


__all__ = ["CLASSES", "DECISION_PREFIXES", "LEGACY_CODES", "MATERIAL", "NOT_FAILURES", "RECOVERY",
           "RECOVERY_BY_CODE", "RESOLVE", "RESOLVE_AT_RUN", "RESOLVE_BY_CODE", "RUNTIME_STEPS", "STEPS", "SURFACE",
           "SURFACE_BY_CODE", "VENTURE_CODES", "WHEN", "WORKFORCE_CODES", "classify", "explain", "problem",
           "recovery", "resolve", "retried_by_runtime", "surface_code", "why_retry"]
