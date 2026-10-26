# SPDX-License-Identifier: Apache-2.0
"""Where every rule a motion check applies comes from: the committed source register, or the caller's own data.

``data/source_register.json`` holds one entry per primary source: issuing body, title, edition, publication date,
jurisdiction, URL, licence and redistribution terms, the date it was retrieved, the SHA-256 of the retrieved text and
the exact clause used. :data:`RULES` names each rule the checks apply and where it comes from: a register entry, the
caller's fields (every limit is the caller's, from its robot's specification), or G6's own numerical bound, which can
only make a check stricter or make it answer "cannot assess", never let a plan through.

:func:`stale_sources` raises a finding for an entry older than its review window. ``tools/check_sources.py`` re-fetches
each entry on a schedule and compares the hash; the default test suite stays offline.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

#: The fields every register entry fills in.
FIELDS = ("id", "issuing_body", "title", "edition", "published", "jurisdiction", "url", "licence", "retrieved",
          "sha256", "clause", "review_months")


@dataclass(frozen=True)
class Rule:
    """One rule a check applies. ``source`` is a register entry's id, the caller's fields, or ``"g6"`` for a guard on a
    numerical bound of G6's own (``effect`` says what it can do to an answer: refuse, escalate, lower_label or note).
    ``tier`` is what the rule's certificate checks and ``implements`` the function that applies it."""

    rule: str
    kind: Literal["register", "caller", "own"]
    source: str | tuple[str, ...]
    effect: str = ""
    tier: str = ""
    implements: str = ""


RULES: dict[str, Rule] = {
    "distance": Rule("the gap between the robot and an obstacle is the distance between their closest points, less "
                     "both radii", "register", "lavalle-2006-5.3.1", tier="property",
                     implements="g6/embodied/_motion/geometry.py:closest_on_segment"),
    "clearance": Rule("a gap smaller than the body's clearance is a collision", "caller",
                      ("robot.clearance", "robot.radius", "robot.link_radius", "world.obstacles")),
    "swept_point": Rule("a point body's segment is checked whole, as the source requires, not point by point: the first "
                        "point where it comes within reach of an obstacle is found in closed form (the closed form is "
                        "G6's own algebra: the root of a quadratic in the fraction along the segment)", "register",
                        "lavalle-2006-5.3.4", tier="property",
                        implements="g6/embodied/_motion/geometry.py:segment_entry"),
    "swept_arm": Rule("an arm's segment is checked by steps no point of the arm can cross, as in the source's "
                      "per-joint bound: the arm moves at most the sum over joints of the change in angle times a "
                      "constant c_i. Taking c_i as the length of the chain outward of joint i is G6's derivation for a "
                      "planar chain (a chord is no longer than its arc); it holds everywhere and is looser than a "
                      "local bound", "register", "lavalle-2006-5.3.4", tier="property",
                      implements="g6/embodied/_motion/checks.py:_arm_segment"),
    "chain": Rule("each joint's place follows from the base, the link lengths and the joint angles", "register",
                  "lavalle-2006-5.3.4-chain", tier="computes",
                  implements="g6/embodied/_motion/geometry.py:chain_points"),
    "workspace": Rule("every part of the robot, its radius included, stays inside the workspace box", "caller",
                      ("world.lower", "world.upper", "robot.radius", "robot.link_radius")),
    "joint_limits": Rule("each joint angle lies within the joint's own limits", "caller",
                         ("robot.joints[].lower", "robot.joints[].upper")),
    "average_speed": Rule("the average speed over a segment is its change divided by its time; the source gives that "
                          "ratio only as an approximation of the rate, so the speed inside a segment can be higher "
                          "and is not checked", "register",
                          "lavalle-2006-14.3.2-euler", tier="property",
                          implements="g6/embodied/_motion/checks.py:_speed_limits"),
    "speed_limits": Rule("no segment's speed exceeds the body's limit", "caller",
                         ("robot.max_speed", "robot.joints[].max_velocity", "plan.waypoints[].t")),
    "goal": Rule("the last waypoint lies within the goal's tolerance", "caller", ("goal.q", "goal.tip",
                                                                               "goal.tolerance")),
    "a_star": Rule("A* on a grid of cells, each cell blocked when any part of it is within reach of an obstacle or "
                   "outside the workspace less the body's radius", "register", "lavalle-2006-2.2.2",
                   tier="property", implements="g6/embodied/adapt_physical_ai/planners.py:plan_astar"),
    "rrt": Rule("a random tree grown from its nearest vertex towards each sample by at most one step, each new edge "
                "checked whole before it is kept. Two simplifications of the source: the nearest vertex is used, not "
                "the nearest point of an edge, and an edge that would reach an obstacle is dropped instead of stopped "
                "at its boundary; two shortcuts, each edge checked whole like any other: a first straight attempt "
                "from the start to the goal, and a final edge to the goal from each new vertex within one step of it; "
                "the step length and the goal bias are the caller's tuning", "register",
                "lavalle-2006-5.5.1", tier="property",
                implements="g6/embodied/adapt_physical_ai/planners.py:plan_rrt"),
    "euler": Rule("Euler's step: position and velocity each advanced by the step's length times their rate at the "
                  "start", "register", "lavalle-2006-14.3.2-euler",
                  tier="property", implements="g6/embodied/_motion/geometry.py:euler_step"),
    "closed_form": Rule("under constant acceleration the motion has a closed form, p + v t + a t^2 / 2", "register",
                        "lavalle-2006-14.3.2-closed-form", tier="property",
                        implements="g6/embodied/_motion/geometry.py:constant_acceleration"),
    "friction": Rule("a contact holds along its surface at most the coefficient of static friction times its normal "
                     "force (the source's maximum static friction: its equation is not decoded in the held copy, so "
                     "it is read from the prose, and the worked example's numbers agree with the product); the margin "
                     "is that maximum less the force along the surface the contact must hold, and a margin of zero or "
                     "below is a slip, stricter than the source, where the object moves once the force exceeds the "
                     "maximum; a contact that must hold nothing along its surface has nothing to slip; a coefficient "
                     "above 1.0, where the source says one usually lies between 0 and 1.0, is named in the result",
                     "register", "openstax-up1-2016-6.2", tier="computes",
                     implements="g6/embodied/_motion/grasp.py:contact_margins"),
    "grasp_forces": Rule("the forces at each contact and the coefficient of static friction are the caller's, from its "
                         "own measurement", "caller", ("grasp.friction_coefficient", "grasp.contacts[].normal_force",
                                                       "grasp.contacts[].tangential_force")),
    "crush_limit": Rule("a contact pressing on the object harder than the object's crush limit is a violation",
                        "caller", ("grasp.crush_limit", "grasp.contacts[].normal_force")),
    "gripper_force": Rule("a normal force above the gripper's own limit is a violation: each contact's, for a gripper "
                          "rated per contact, or the sum of the grasp's, for one rated on its total; a gripper whose "
                          "rating is not given cannot be assessed", "caller",
                          ("robot.gripper.max_force", "robot.gripper.rated_as", "grasp.contacts[].normal_force")),
    "tolerance": Rule("a gap within 1e-9 m of the limit counts as reaching it: stricter only, it can turn a pass "
                      "into a violation, never a violation into a pass", "own", "g6", "refuse", tier="guard",
                      implements="g6/embodied/_motion/geometry.py:segment_entry"),
    "work_bound": Rule("a plan whose arm checks need more than two million distance evaluations between them answers "
                       "cannot assess", "own", "g6", "escalate",
                       tier="guard", implements="g6/embodied/_motion/checks.py:_arm_segment"),
    "force_tolerance": Rule("a friction margin within 1e-9 N of zero, or a normal force within 1e-9 N of a limit, "
                            "counts as reaching it, and reaching it is a violation: stricter only, it can turn a "
                            "pass into a violation, never a violation into a pass", "own", "g6",
                            "refuse", tier="guard", implements="g6/embodied/_motion/grasp.py:grasp_findings"),
    "bounds": Rule("a length or coordinate beyond 1,000,000 m of zero (a joint angle beyond 1,000,000 rad) answers "
                   "cannot assess, so no square or product the checks take can overflow; it never lets a plan "
                   "through", "own", "g6", "escalate", tier="guard",
                   implements="g6/embodied/_motion/model.py:range_problems"),
    "sizes": Rule("the sizes one request may carry are the caller's within the typed input's limits (12 joints, "
                  "1,000 obstacles, 5,000 waypoints, 20 contacts per grasp; for the planners 250,000 grid cells and "
                  "5,000 iterations, with 1,000 iterations and a goal bias of 0.1 when the caller gives none), refused "
                  "beyond them; a "
                  "result lists its first 200 violations in full and violations_found counts every one", "caller",
                  ("robot.joints", "world.obstacles", "plan.waypoints")),
}


def register() -> dict[str, Any]:
    """The committed register, read as package data."""
    text = resources.files("g6.embodied._motion").joinpath("data/source_register.json").read_text(encoding="utf-8")
    return json.loads(text)


def review() -> dict[str, Any]:
    """The record of the independent pass over each rule against its source (``data/rule_review.json``). It is
    evidence of kind ``review`` and raises no label."""
    text = resources.files("g6.embodied._motion").joinpath("data/rule_review.json").read_text(encoding="utf-8")
    return json.loads(text)


def entries() -> dict[str, dict[str, Any]]:
    """Each register entry by its id."""
    return {e["id"]: e for e in register()["entries"]}


def register_problems() -> list[str]:
    """What is wrong with the register or the rule table, or ``[]``: an entry with a field missing or empty, a hash
    that is not SHA-256, a date that is not one, or a rule naming an entry the register does not have."""
    out: list[str] = []
    found = entries()
    for entry_id, entry in found.items():
        missing = [f for f in FIELDS if entry.get(f) in (None, "")]
        if missing:
            out.append(f"{entry_id}: {', '.join(missing)} not filled in")
        clause_hash = hashlib.sha256(str(entry.get("clause", "")).encode("utf-8")).hexdigest()
        field = "sha256" if entry.get("hash_scope") == "clause_utf8" else "clause_sha256"
        if clause_hash != entry.get(field):
            out.append(f"{entry_id}: recorded clause hash differs")
        sha = str(entry.get("sha256", ""))
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            out.append(f"{entry_id}: sha256 is not a SHA-256 hex digest")
        try:
            _dt.date.fromisoformat(str(entry.get("retrieved", "")))
        except ValueError:
            out.append(f"{entry_id}: retrieved is not a date")
    for name, rule in RULES.items():
        if rule.kind == "register" and rule.source not in found:
            out.append(f"rule {name}: {rule.source} is not in the register")
        if rule.kind == "own" and not rule.effect:
            out.append(f"rule {name}: a bound of G6's own must say what it can do to an answer")
    return out


def _add_months(day: _dt.date, months: int) -> _dt.date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return day.replace(year=year, month=month, day=min(day.day, last))
        except ValueError:
            continue
    raise ValueError(f"no date {months} months after {day}")  # pragma: no cover - every month has a 28th


def stale_sources(today: _dt.date | None = None, rules: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """A finding for each register entry (of ``rules``, or all) retrieved longer ago than its review window."""
    today = today or _dt.date.today()
    wanted = {RULES[r].source for r in rules if RULES[r].kind == "register"} if rules else None
    out = []
    for entry_id, entry in sorted(entries().items()):
        if wanted is not None and entry_id not in wanted:
            continue
        due = _add_months(_dt.date.fromisoformat(entry["retrieved"]), int(entry["review_months"]))
        if today > due:
            out.append({"finding": "stale_source", "source": entry_id, "retrieved": entry["retrieved"],
                        "review_due": due.isoformat(),
                        "message": f"{entry_id} was retrieved on {entry['retrieved']} and was due for review on "
                                   f"{due.isoformat()}: re-read it against its URL before relying on this check"})
    return out


def cited(rules: tuple[str, ...]) -> list[dict[str, Any]]:
    """Each rule with where it comes from, as evidence carries it."""
    found = entries()
    out = []
    for name in rules:
        rule = RULES[name]
        item: dict[str, Any] = {"rule": name, "says": rule.rule, "kind": rule.kind}
        if rule.kind == "register":
            entry = found[str(rule.source)]
            item.update(source=entry["id"], title=entry["title"], url=entry["url"], clause=entry["clause"])
        elif rule.kind == "caller":
            item["caller_fields"] = list(rule.source)
        else:
            item.update(source=rule.source, effect=rule.effect)
        out.append(item)
    return out


__all__ = ["FIELDS", "RULES", "Rule", "cited", "entries", "register", "register_problems", "review", "stale_sources"]
