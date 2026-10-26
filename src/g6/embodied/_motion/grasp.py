# SPDX-License-Identifier: Apache-2.0
"""The grasp check: each contact's friction margin, and the normal forces against the object's crush limit and the
gripper's own limit.

At a contact, the force along the surface it can hold without slipping is at most the coefficient of static friction
times its normal force (rule ``friction``). The margin is that maximum less the force along the surface the contact
must hold, and a margin of zero or below is a slip; a contact that must hold nothing along its surface has nothing to
slip. A normal force above the object's crush limit (rule ``crush_limit``) is a violation, and so is one above the
gripper's own limit (rule ``gripper_force``): each contact's, for a gripper rated per contact, or the sum of the
grasp's normal forces, for one rated on its total. Every force, the coefficient and both limits are the caller's (rule
``grasp_forces``): a gripper not given, or one whose rating is not given, cannot be assessed, and neither can a term
that is not a finite number. A coefficient above 1.0, where the source says one usually lies, is named in the result,
never accepted silently. Nothing gives a confidence: each violation names the rule that fired and its numbers.

:func:`grasp_findings` checks one grasp. ``check_plan`` runs it on each waypoint that carries a grasp (the check
``grasp_margin``, registered here with :func:`~g6.embodied._motion.checks.register_check`), and :func:`grasp_margin`
answers for one grasp on its own.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from g6.embodied._motion import geometry as geo
from g6.embodied._motion.checks import MAX_LISTED, Case, Outcome, Violation, register_check
from g6.embodied._motion.envelope import (
    CANNOT_ASSESS,
    GRASP_CRUSH,
    GRASP_SLIP,
    GRIPPER_FORCE,
    NO_VIOLATION_FOUND,
    MotionResult,
    answer,
    refuse,
)
from g6.embodied._motion.model import Grasp, Gripper
from g6.embodied._motion.sources import cited, stale_sources

#: N. A friction margin within this of zero, or a normal force within this of a limit, counts as reaching it (rule
#: ``force_tolerance``); it only makes the check stricter.
FORCE_TOLERANCE = 1e-9
#: The source: a coefficient of friction usually lies between 0 and 1.0. Above it the result names the value (rule
#: ``friction``); it changes no answer.
USUAL_COEFFICIENT = 1.0
#: The rules the grasp check applies; ``gripper_force`` only when the gripper's limit and its rating are given.
RULES_APPLIED = ("friction", "grasp_forces", "crush_limit", "gripper_force", "force_tolerance")
ASSUMPTIONS = (
    "The forces at each contact, the coefficient of static friction and the crush limit are the caller's, from its "
    "own measurement and the object's specification; G6 measures none of them.",
    "Each contact's force along its surface is taken to include its share of the object's weight and inertial load; "
    "how the load is split between the contacts is not checked.",
    "Friction is the source's approximate, empirical model: a contact holds along its surface at most the coefficient "
    "of static friction times its normal force. The source says it is not accurate for lubricated surfaces or for "
    "surfaces sliding across each other at high speeds.",
    "Each contact is checked on its own, with the forces given for it: twisting about a contact, a load that changes "
    "between waypoints (an arm's acceleration included), slip already under way and the sensor's error are not "
    "checked.",
)
_NO_GRIPPER = ("robot.gripper is not given, so no normal force can be assessed against the gripper's own limit; give "
               "the gripper's max_force and rated_as from its specification")
_NO_RATING = ("robot.gripper.rated_as is not given, so its max_force cannot be read as a limit on each contact or on "
              "all of a grasp's contacts together; give rated_as from the gripper's specification")


def _gripper_gap(gripper: Gripper | None) -> str:
    """Why the gripper's limit cannot be assessed, or ``""`` when it can."""
    if gripper is None:
        return _NO_GRIPPER
    return _NO_RATING if gripper.rated_as is None else ""


def _listed(waypoints: list[int]) -> str:
    more = f" and {len(waypoints) - 10} more" if len(waypoints) > 10 else ""
    return f"{waypoints[:10]}{more}"


def contact_margins(grasp: Grasp) -> list[dict[str, Any]]:
    """Each contact's forces, the most its friction holds along the surface, and the margin left. A holding force that
    is not a finite number raises ``ArithmeticError``, which answers "cannot assess", never "clear"."""
    out = []
    for index, contact in enumerate(grasp.contacts):
        name = contact.name or index
        hold = geo.finite(grasp.friction_coefficient * contact.normal_force, f"the friction contact {name} can hold")
        out.append({"contact": name, "normal_force": contact.normal_force,
                    "tangential_force": contact.tangential_force, "holds_up_to": hold,
                    "margin": hold - contact.tangential_force})
    return out


def coefficient_findings(grasp: Grasp, waypoint: int | None = None) -> list[dict[str, Any]]:
    """A finding when the grasp's coefficient of friction is above :data:`USUAL_COEFFICIENT`, or ``[]``."""
    mu = grasp.friction_coefficient
    if mu <= USUAL_COEFFICIENT:
        return []
    where = "" if waypoint is None else f" at waypoint {waypoint}"
    return [{"finding": "friction_coefficient_above_usual", "friction_coefficient": mu, "waypoint": waypoint,
             "message": f"The coefficient of static friction given{where}, {mu:.6g}, is above 1.0, and the source "
                        "says a coefficient of friction is usually between 0 and 1.0: every margin it clears rests on "
                        "that value being measured for this pair."}]


def grasp_findings(grasp: Grasp, gripper: Gripper | None, *, at: float = 0.0, waypoint: int | None = None,
                   time: float | None = None,
                   room: int = MAX_LISTED) -> tuple[list[Violation], list[dict[str, Any]], int]:
    """The violations one grasp has (at most ``room`` built, the rest only counted), each contact's margins, and how
    many violations were counted but not built. Without the gripper's limit or its rating the gripper rule is not
    applied; the caller says so as "cannot assess"."""
    margins = contact_margins(grasp)
    where = "" if waypoint is None else f" at waypoint {waypoint}"
    found: list[Violation] = []
    unlisted = 0

    def add(code: str, subject: str, contact: Any, rule: str, message: str, **detail: Any) -> None:
        nonlocal unlisted
        if len(found) >= room:
            unlisted += 1
            return
        found.append(Violation(check="grasp_margin", code=code, at=at, time=time, waypoint=waypoint,
                               message=f"{subject}{where} {message}",
                               detail={"contact": contact, "rule": rule, **detail}))

    rating = gripper.rated_as if gripper is not None else None  # None: the gripper rule is not applied
    limit = gripper.max_force if gripper is not None else float("inf")
    for row in margins:
        name, normal, load = row["contact"], row["normal_force"], row["tangential_force"]
        if load > 0 and row["margin"] <= FORCE_TOLERANCE:
            add(GRASP_SLIP, f"contact {name}", name, "friction",
                f"must hold {load:.6g} N along the surface and its friction holds at most "
                f"{row['holds_up_to']:.6g} N: it slips", normal_force=normal, tangential_force=load,
                friction_coefficient=grasp.friction_coefficient, holds_up_to=row["holds_up_to"],
                margin=row["margin"])
        if normal >= grasp.crush_limit - FORCE_TOLERANCE:
            add(GRASP_CRUSH, f"contact {name}", name, "crush_limit",
                f"presses with {normal:.6g} N, at or above the object's crush limit of {grasp.crush_limit:.6g} N",
                normal_force=normal, limit=grasp.crush_limit, over_by=normal - grasp.crush_limit)
        if rating == "per_contact" and normal >= limit - FORCE_TOLERANCE:
            add(GRIPPER_FORCE, f"contact {name}", name, "gripper_force",
                f"presses with {normal:.6g} N, at or above the gripper's limit per contact of {limit:.6g} N",
                rated_as="per_contact", normal_force=normal, limit=limit, over_by=normal - limit)
    if rating == "total":
        total = geo.finite(sum(c.normal_force for c in grasp.contacts), "the sum of the grasp's normal forces")
        if total >= limit - FORCE_TOLERANCE:
            add(GRIPPER_FORCE, "the grasp's contacts", "all contacts", "gripper_force",
                f"press with {total:.6g} N in all, at or above the gripper's total limit of {limit:.6g} N",
                rated_as="total", normal_force_total=total, limit=limit, over_by=total - limit)
    return found, margins, unlisted


def _applied(gripper: Gripper | None) -> tuple[str, ...]:
    return tuple(r for r in RULES_APPLIED if not _gripper_gap(gripper) or r != "gripper_force")


def _grasp_check(case: Case) -> Outcome:
    held = [i for i, w in enumerate(case.plan.waypoints) if w.grasp is not None]
    if not held:
        return Outcome(not_applicable="no grasp was given; nothing held was checked")
    gripper = case.body.gripper
    out = Outcome(rules=_applied(gripper), row={"grasp_waypoints": held})
    unusual: list[dict[str, Any]] = []
    for i in held:
        w = case.plan.waypoints[i]
        assert w.grasp is not None
        kept, _, more = grasp_findings(w.grasp, gripper, at=float(i), waypoint=i, time=w.t,
                                       room=max(0, MAX_LISTED - len(out.violations)))
        out.violations += kept
        out.unlisted += more
        unusual += coefficient_findings(w.grasp, waypoint=i)
    said = [*ASSUMPTIONS, f"Grasps were checked at waypoints {_listed(held)} only; an object held at any other "
                          "waypoint, carried between them included, was not checked."]
    if unusual:
        out.row["findings"] = unusual[:MAX_LISTED]
        said.append(f"The coefficient of static friction given at waypoints {_listed([f['waypoint'] for f in unusual])}"
                    " is above 1.0, and the source says a coefficient of friction is usually between 0 and 1.0: every "
                    "margin it clears there rests on that value being measured for the pair.")
    out.assumptions = tuple(said)
    out.cannot_assess = _gripper_gap(gripper)
    return out


register_check("grasp_margin", _grasp_check, rules=RULES_APPLIED, codes=(GRASP_SLIP, GRASP_CRUSH, GRIPPER_FORCE))


def grasp_margin(body: Any, grasp: Grasp, *, request_id: str = "", run_id: str = "",
                 today: _dt.date | None = None) -> MotionResult:
    """One grasp checked on its own against the body's gripper. A violation gives ``blocked-escalated`` with the code
    of the first one; a gripper or rating not given, or a term that is not a finite number, gives
    ``blocked-escalated`` with ``G6_E_MOTION_CANNOT_ASSESS``; otherwise ``qualified-draft``, which is not a safety
    clearance."""
    op, ids = "grasp_margin", {"request_id": request_id, "run_id": run_id}
    gripper = body.gripper
    try:
        violations, margins, _ = grasp_findings(grasp, gripper)
    except ArithmeticError as exc:
        return refuse(op, CANNOT_ASSESS, f"{exc}; the grasp is not cleared"[:300], verdict="cannot_assess", **ids)
    gap = _gripper_gap(gripper)
    unusual = coefficient_findings(grasp)
    assumptions = [*ASSUMPTIONS, *(f["message"] for f in unusual)]
    rules = (*_applied(gripper), "bounds")
    stale = stale_sources(today, rules)
    first = violations[0].model_dump(mode="json") if violations else None
    unassessed = [{"check": "grasp_margin", "reason": gap}] if gap else []
    evidence = [{"kind": "grasp_check", "contacts": margins, "first_violation": first, "cannot_assess": unassessed,
                 "assumptions": assumptions, "rules": cited(rules)}, *unusual, *stale]
    fields = {"verdict": "violation" if violations else "cannot_assess" if unassessed else "no_violation_found",
              "first_violation": first, "violations": [v.model_dump(mode="json") for v in violations],
              "violations_found": len(violations), "contacts": margins, "cannot_assess": unassessed,
              "findings": unusual, "assumptions": assumptions, "source_findings": stale}
    if violations:
        more = f" ({len(violations)} violations found in all)" if len(violations) > 1 else ""
        return refuse(op, violations[0].code, violations[0].message + more, evidence=evidence, **fields, **ids)
    if unassessed:
        return refuse(op, CANNOT_ASSESS, f"the grasp is not cleared: {gap}", evidence=evidence, **fields, **ids)
    note = "".join(f" {f['message']}" for f in unusual)
    return answer(op, NO_VIOLATION_FOUND,
                  "No violation found against the forces and limits given. This is not a safety clearance: it holds "
                  "only as far as the forces, the coefficient of friction and the limits given match the real ones."
                  + note,
                  "Before anything is gripped, have a person check the forces and limits against the real gripper "
                  "and object, and send any command through g6.action_gating.",
                  evidence=evidence, **fields, **ids)


__all__ = ["ASSUMPTIONS", "FORCE_TOLERANCE", "RULES_APPLIED", "USUAL_COEFFICIENT", "coefficient_findings",
           "contact_margins", "grasp_findings", "grasp_margin"]
