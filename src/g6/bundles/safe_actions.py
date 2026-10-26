# SPDX-License-Identifier: Apache-2.0
"""Bundle: Safe agent actions — gate the actions an AI agent proposes before it runs them.

Works offline (no model). Each line is one proposed action, ``<action type> <target> [fact=value ...]``,
for example ``delete_file reports/q3.xlsx reversible=no reach=local``. Facts the user leaves out stay
unknown, and unknown is never treated as low risk. Every action goes through
``g6.action_gating.evaluate_action``; nothing is executed.
"""
from __future__ import annotations

from typing import Any

from g6.bundles import Bundle, BundleResult, Field, weakest

_FACT_HELP = ("Facts: reversible=yes|partly|no, reach=none|local|account|organization|external_party|public, "
             "backend=<system>, external=yes|no.")

_ADVICE = {
    "allow": "Low impact — can proceed.",
    "allow_with_notice": "Can proceed; read the notice first.",
    "require_confirmation": "Ask a person to confirm before running it.",
    "require_mitigation": "Reduce the risk (backup, smaller scope) before running it.",
    "require_additional_information": "Not enough is known about this action yet — describe it more fully.",
    "require_human_review": "A person must review this before it runs.",
    "block": "Do not run this action.",
}


_REVERSIBLE = {"yes": "reversible", "partly": "partially_reversible", "no": "irreversible"}
_REACH = ("none", "local", "account", "organization", "external_party", "public")


def _parse(line: str) -> dict[str, Any] | None:
    """``<type> <target words> [reversible=yes|partly|no] [reach=...] [backend=...] [external=yes|no]``."""
    words = line.split()
    if not words or words[0].startswith("#"):
        return None
    action: dict[str, Any] = {"action_type": words[0]}
    target, problems = [], []
    for word in words[1:]:
        key, sep, value = word.partition("=")
        if not sep:
            target.append(word)
        elif key == "reversible" and value in _REVERSIBLE:
            action["reversibility"] = _REVERSIBLE[value]
        elif key == "reach" and value in _REACH:
            action["blast_radius"] = value
        elif key == "backend" and value:
            action["target_backend"] = value
        elif key == "external" and value in ("yes", "no"):
            action["external_side_effect"] = value == "yes"
        else:
            problems.append(word)
    if target:
        action["target_resource"] = " ".join(target)
    if problems:
        action["_problems"] = problems
    return action


# COPY: Why an irreversible or external action is blocked here, and what unblocks it (audit 003 AL-001).
_NO_PLAN = (" It is irreversible or reaches outside, and has no verification plan (the result that proves it worked, "
            "what is watched and for how long, the early and late signs, when to stop, how to roll it back). This page "
            "cannot take a plan yet: give one through the Python or MCP interface (verification_contract), or have a "
            "person decide.")


def _open_questions(ev: Any) -> str:
    matrix = ev.gate_decision.rumsfeld_matrix
    return "; ".join(matrix.known_unknowns) if matrix and matrix.known_unknowns else ""


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    from g6.action_gating import evaluate_action

    actions = [a for a in (_parse(x) for x in inputs.get("actions", "").splitlines()) if a]
    if not actions:
        return BundleResult("blocked-escalated", "Enter at least one action, one per line.")
    rows, states = [], []
    for action in actions:
        label = f"{action['action_type']} {action.get('target_resource', '')}".strip()
        problems = action.pop("_problems", None)
        if problems:
            states.append("blocked-escalated")
            rows.append({"action": label, "decision": "not checked",
                         "what to do": f"Fix these facts: {' '.join(problems)}. " + _FACT_HELP,
                         "label": "blocked-escalated", "reasons": "G6_E_INPUT_VALIDATION"})
            continue
        ev = evaluate_action(action)
        decision = ev.gate_decision.decision
        states.append(ev.completion_state)
        advice = _ADVICE.get(decision, decision)
        if "G6_E_BALLISTIC_ACTION_BLOCKED" in ev.gate_decision.reason_codes:
            advice += _NO_PLAN
        questions = _open_questions(ev)
        rows.append({
            "action": label,
            "decision": decision.replace("_", " "),
            "what to do": f"{advice} Open questions: {questions}" if questions else advice,
            "label": ev.completion_state,
            "reasons": ", ".join(c for c in ev.gate_decision.reason_codes if c != "G6_E_GATE_OK") or "—",
        })
    def count(*words: str) -> int:
        return sum(1 for r in rows if any(w in r["decision"] for w in words))

    blocked, person, facts = count("block", "not checked"), count("review", "confirmation"), count("information")
    return BundleResult(
        weakest(states),
        f"{len(rows)} action(s): {blocked} blocked, {person} need a person, {facts} need more facts, "
        f"{len(rows) - blocked - person - facts} can proceed (some with conditions).",
        rows,
        ["Risk facts come from the action's type and target only; G6 does not inspect your systems.",
         "A rule check is a policy decision, not proof the action is safe, so nothing here is labelled verified."],
    )


BUNDLE = Bundle(
    id="safe-actions",
    title="Safe agent actions",
    summary="Check the actions an AI agent wants to take, before it takes them.",
    fields=(Field("actions", "Proposed actions, one per line",
                  placeholder="read_file notes.txt reversible=yes reach=local\n"
                              "delete_file reports/q3.xlsx reversible=no reach=local\n"
                              "send_email all-customers reversible=no reach=public external=yes",
                  help="One per line: <action type> <target> then any facts you know. " + _FACT_HELP
                       + " Leave a fact out if you don't know it. Nothing is executed."),),
    run=run,
)
