# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A stored plan as Design §8's timeline, for the person's approval (E8e; plan ruling R14).

What the person sees before a mission runs:
- the steps in plain words, each with how it will be done and how it will be checked;
- what will wait for them.

No chain of thought and no internal names (Design §8, §19). Only the Design §8 stages a plan has steps for are shown.
Pure: no I/O, no model.
"""
from __future__ import annotations

from g6.workforce.plans import Plan
from g6.workforce.scheduler import SchedulerDecision

STAGES = ("Understanding the task", "Breaking it into key questions", "Testing possible answers",
          "Checking evidence", "Independent verification")
#: Each reason code as a person reads it (Design §4.1).
SAID = {
    "DETERMINISTIC_METHOD_AVAILABLE": "worked out by code, with no AI model",
    "OBJECTIVE_VERIFIER_AVAILABLE": "checked separately by code, with no AI model",
    "HUMAN_APPROVAL_REQUIRED": "waits for your approval",
    "HIGHER_RELIABILITY_REQUIRED": "consequential, so it is checked more strictly",
}
STRATEGY_SAID = {"worker": "written by your AI assistant", "capability": "done by a G6 tool",
                 "deterministic": "worked out by code", "human": "your decision"}
NEXT = "Show these steps to the person you work for; mission_run starts them once they agree."


def plan_timeline(plan: Plan, decisions: list[SchedulerDecision]) -> dict:
    said = {decision.work_unit_id: decision for decision in decisions}

    def how(key: str) -> str:
        decision = said[key]
        words = [SAID[code] for code in decision.reason_codes if code in SAID]
        return "; ".join(words) or STRATEGY_SAID.get(decision.selected_option.get("strategy_type", ""), "")

    doing = [{"step": unit.goal, "how": how(unit.key)} for unit in plan.units if unit.kind == "do"]
    checking = [{"step": unit.goal, "how": how(unit.key)} for unit in plan.units if unit.kind == "verify"]
    approvals = [unit.goal for unit in plan.units if unit.kind == "gate"]
    stages = [{"stage": STAGES[0], "state": "Complete"},
              {"stage": STAGES[1], "state": "Complete",
               "detail": f"{len(doing)} part{'' if len(doing) == 1 else 's'}, each with its own check"}]
    if doing:
        stages.append({"stage": STAGES[2], "state": "Waiting", "steps": doing})
    if checking:
        stages.append({"stage": STAGES[4], "state": "Waiting", "steps": checking})
    out: dict = {"title": "Working on your problem", "stages": stages}
    if approvals:
        out["needs_your_approval"] = approvals
    out["next"] = NEXT
    return out


__all__ = ["NEXT", "SAID", "STAGES", "STRATEGY_SAID", "plan_timeline"]
