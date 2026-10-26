# SPDX-License-Identifier: Apache-2.0
"""The grasp check merged from tactile_fusion into the embodied family's motion package: each contact's friction margin
(the coefficient of static friction times its normal force, less the force along the surface it must hold), and its
normal force against the object's crush limit and the gripper's own limit. Every number is the caller's; a limit
that is not given cannot be assessed; nothing is a safety clearance."""
from __future__ import annotations

import datetime as dt
import json
import random
from fractions import Fraction

import pytest
from pydantic import ValidationError

from g6.embodied._motion import (
    Contact,
    Goal,
    Grasp,
    Gripper,
    Plan,
    PointBody,
    Waypoint,
    World,
    check_plan,
    grasp_margin,
)
from g6.embodied._motion import grasp as grasp_module
from g6.embodied._motion.sources import RULES, entries
from g6.mcp import invoke_reply

SLIP, CRUSH, GRIPPER = "G6_E_MOTION_GRASP_SLIP", "G6_E_MOTION_GRASP_CRUSH", "G6_E_MOTION_GRIPPER_FORCE_LIMIT"
RETRIEVED = dt.date(2026, 10, 22)


def body(max_force: float | None = 100.0, rated_as: str | None = "per_contact") -> PointBody:
    return PointBody(kind="point", radius=0.05, max_speed=1.0, clearance=0.0,
                     gripper=None if max_force is None else Gripper(max_force=max_force, rated_as=rated_as))


def grasp(*forces: tuple[float, float], mu: float = 0.5, crush: float = 80.0) -> Grasp:
    return Grasp(contacts=[Contact(name=f"finger{i}", normal_force=n, tangential_force=t)
                           for i, (n, t) in enumerate(forces)], friction_coefficient=mu, crush_limit=crush)


def carried(g: Grasp | None, at: int = 1) -> Plan:
    qs = ([1.0, 1.0], [2.0, 1.0], [3.0, 1.0])
    return Plan(waypoints=[Waypoint(q=q, t=2.0 * i, grasp=g if i == at else None) for i, q in enumerate(qs)])


WORLD = World(lower=[0.0, 0.0], upper=[10.0, 10.0], obstacles=[])
GOAL = Goal(q=[3.0, 1.0], tolerance=1e-9)


# --- D1: a grasp too light for its load is caught ---------------------------------------------------------------------

def test_d1_an_even_grip_too_light_for_its_load_slips() -> None:
    # The archive scored a perfectly even grip 1.0 whatever its load (TACTILE_FUSION-01): four equal contacts, each
    # pressing 5 N with a coefficient of 0.5, hold 2.5 N along the surface, and each must hold 3 N.
    # Removing grasp.py's `if row["margin"] <= FORCE_TOLERANCE:` slip test fails this.
    r = grasp_margin(body(), grasp((5.0, 3.0), (5.0, 3.0), (5.0, 3.0), (5.0, 3.0)))
    assert (r.completion_state, r.code, r.verdict) == ("blocked-escalated", SLIP, "violation")
    assert r.violations_found == 4 and r.first_violation["detail"]["margin"] == pytest.approx(-0.5)
    assert [c["holds_up_to"] for c in r.contacts] == [pytest.approx(2.5)] * 4


def test_d1_random_grasps_never_pass_a_slip_unseen() -> None:
    """Cases the check was not written around: random coefficients and forces, with exact rational arithmetic as the
    independent oracle for which contacts slip."""
    # Removing grasp.py's `if row["margin"] <= FORCE_TOLERANCE:` slip test fails this.
    rng = random.Random(20261022)
    slipping = holding = 0
    for _ in range(500):
        mu = rng.uniform(0.0, 1.5)
        forces = [(rng.uniform(0.0, 50.0), rng.uniform(0.0, 40.0)) for _ in range(rng.randint(1, 5))]
        r = grasp_margin(body(max_force=1e6), grasp(*forces, mu=mu, crush=1e6))
        oracle = [t > 0 and Fraction(mu) * Fraction(n) - Fraction(t) <= Fraction(grasp_module.FORCE_TOLERANCE)
                  for n, t in forces]
        found = {v["detail"]["contact"] for v in r.violations if v["code"] == SLIP}
        assert found == {f"finger{i}" for i, slips in enumerate(oracle) if slips}, (mu, forces)
        slipping += any(oracle)
        holding += not any(oracle)
    assert slipping >= 50 and holding >= 50  # the cases did reach both answers


# --- the source's own worked example (OpenStax University Physics Volume 1, section 6.2, the worked example titled
# --- Static and Kinetic Friction, as the register records it; its number is not in the held copy) ------------------

def test_the_friction_rule_reproduces_the_sources_worked_example() -> None:
    # A 20.0-kg crate with a coefficient of static friction of 0.700: on a floor its normal force is its weight,
    # 20.0 x 9.80 N (the book's weight of a 1.00-kg object); the book prints the most static friction holds as 137 N,
    # and says the crate stays at rest while the push is less than 137 N and moves once it is greater.
    # Dropping the coefficient from grasp.py's `hold = ... friction_coefficient * contact.normal_force` fails this.
    example = entries()["openstax-up1-2016-6.2"]["worked_example"]
    normal = example["mass_kg"] * example["weight_of_1_kg_N"]
    mu = example["coefficient_of_static_friction"]
    printed = example["maximum_static_friction_N"]
    held = grasp_margin(body(max_force=1e3), grasp((normal, printed - 1), mu=mu, crush=1e3))
    assert held.contacts[0]["holds_up_to"] == pytest.approx(printed, abs=0.5)  # 137.2 N, printed to three figures
    assert held.completion_state == "qualified-draft" and held.contacts[0]["margin"] > 0
    pushed = grasp_margin(body(max_force=1e3), grasp((normal, printed + 1), mu=mu, crush=1e3))
    assert pushed.code == SLIP and pushed.first_violation["detail"]["rule"] == "friction"


def test_the_friction_rule_cites_its_register_entry_with_its_corpus_hash_and_clause() -> None:
    # Removing the openstax-up1-2016-6.2 entry from data/source_register.json fails this.
    rule = RULES["friction"]
    entry = entries()[rule.source]
    assert rule.kind == "register" and entry["hash_scope"] == "corpus_file"
    assert entry["file_name"] == "University_Physics_Volume_1_-_WEB.md" and len(entry["sha256"]) == 64
    assert "CC BY 4.0" in entry["licence"] and "not decoded" in entry["clause"]
    cited = {c["rule"]: c for c in grasp_margin(body(), grasp((10.0, 1.0)), today=RETRIEVED).evidence[0]["rules"]}
    assert cited["friction"]["source"] == "openstax-up1-2016-6.2" and cited["friction"]["url"].startswith("https://")
    assert cited["crush_limit"]["caller_fields"] and cited["gripper_force"]["caller_fields"]


# --- the crush limit and the gripper's own limit ---------------------------------------------------------------------

def test_a_contact_pressing_harder_than_the_objects_crush_limit_is_caught() -> None:
    # Removing grasp.py's crush-limit comparison fails this: the friction margin alone is ample.
    r = grasp_margin(body(), grasp((50.0, 1.0), (20.0, 1.0), crush=40.0))
    assert r.code == CRUSH and r.first_violation["detail"] == {
        "contact": "finger0", "rule": "crush_limit", "normal_force": 50.0, "limit": 40.0, "over_by": pytest.approx(10.0)}


def test_a_contact_pressing_harder_than_the_grippers_own_limit_is_caught() -> None:
    # The archive's validate_gripper_force, now in the grasp check; removing grasp.py's gripper comparison fails this.
    r = grasp_margin(body(max_force=30.0), grasp((35.0, 1.0), crush=80.0))
    assert r.code == GRIPPER and r.first_violation["detail"]["rule"] == "gripper_force"
    assert r.first_violation["detail"]["limit"] == 30.0


# --- G4: a missing limit, or arithmetic that overflows, is never a pass ----------------------------------------------

def test_g4_a_grasp_without_the_grippers_limit_cannot_be_assessed() -> None:
    # Removing grasp.py's `if gripper is None` cannot-assess reason lets an unchecked normal force through.
    r = grasp_margin(body(max_force=None), grasp((10.0, 1.0)))
    assert (r.completion_state, r.code, r.verdict) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS",
                                                       "cannot_assess")
    assert "robot.gripper" in r.reason
    plan = check_plan(carried(grasp((10.0, 1.0))), WORLD, body(max_force=None), GOAL)
    assert plan.code == "G6_E_MOTION_CANNOT_ASSESS" and plan.cannot_assess[0]["check"] == "grasp_margin"


def test_g4_forces_whose_product_overflows_cannot_assess_never_clear() -> None:
    # Removing grasp.py's geo.finite() on the holding force lets an infinite hold read as an ample margin.
    r = grasp_margin(body(max_force=1e308), grasp((1e200, 1e300), mu=1e200, crush=1e308))
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")
    assert "finite" in r.reason


@pytest.mark.parametrize("bad", [
    {"contacts": [{"normal_force": 5.0, "tangential_force": 1.0}], "crush_limit": 10.0},       # no coefficient
    {"contacts": [{"normal_force": 5.0, "tangential_force": 1.0}], "friction_coefficient": 0.5},  # no crush limit
    {"contacts": [{"normal_force": 5.0}], "friction_coefficient": 0.5, "crush_limit": 10.0},   # no load given
    {"contacts": [], "friction_coefficient": 0.5, "crush_limit": 10.0},
    {"contacts": [{"normal_force": -5.0, "tangential_force": 1.0}], "friction_coefficient": 0.5, "crush_limit": 10.0},
    {"contacts": [{"normal_force": 5.0, "tangential_force": 1.0}], "friction_coefficient": 0.5, "crush_limit": 10.0,
     "grip_verified": True},                                                                   # a caller's word
])
def test_g3_g4_a_grasp_without_its_numbers_or_with_a_claim_is_refused(bad) -> None:
    # Removing model.py's `model_config = _STRICT` from Grasp (or giving a field a default) fails this.
    with pytest.raises(ValidationError):
        Grasp.model_validate(bad)


# --- TACTILE_FUSION-02: no confidence, the rule that fired named ------------------------------------------------------

def test_p1_02_no_confidence_is_given_and_each_violation_names_the_rule_that_fired() -> None:
    # Removing the `"rule": ...` entry from grasp.py's violation details fails this.
    r = grasp_margin(body(max_force=30.0), grasp((35.0, 20.0), (5.0, 1.0), mu=0.5, crush=30.0))
    assert "confidence" not in json.dumps(r.model_dump(mode="json"))
    assert {(v["detail"]["contact"], v["detail"]["rule"]) for v in r.violations} == {
        ("finger0", "friction"), ("finger0", "crush_limit"), ("finger0", "gripper_force")}


# --- inside check_plan, and over MCP ---------------------------------------------------------------------------------

def test_check_plan_checks_each_waypoints_grasp_and_reports_the_first_slip() -> None:
    # Removing grasp.py's register_check("grasp_margin", ...) lets a plan carrying a slipping grasp through.
    r = check_plan(carried(grasp((4.0, 3.0), (4.0, 1.0))), WORLD, body(), GOAL)
    first = r.first_violation
    assert (r.completion_state, first["check"], first["code"], first["waypoint"]) == (
        "blocked-escalated", "grasp_margin", SLIP, 1)
    assert first["time"] == 2.0 and first["detail"]["contact"] == "finger0"
    clear = check_plan(carried(grasp((10.0, 1.0))), WORLD, body(), GOAL, today=RETRIEVED)
    assert clear.completion_state == "qualified-draft" and clear.checks[-1] == {
        "check": "grasp_margin", "result": "no violation found", "violations": 0, "grasp_waypoints": [1]}
    assert "coefficient of static friction" in " ".join(clear.assumptions)
    empty = check_plan(carried(None), WORLD, body(), GOAL)
    assert empty.checks[-1]["result"] == "not applicable" and empty.completion_state == "qualified-draft"


def test_a_clear_grasp_is_a_qualified_draft_that_says_it_is_not_a_clearance() -> None:
    # Removing grasp.py's ASSUMPTIONS from grasp_margin's fields fails this.
    r = grasp_margin(body(), grasp((10.0, 1.0), (10.0, 1.0)), today=RETRIEVED)
    assert (r.completion_state, r.verdict, r.first_violation) == ("qualified-draft", "no_violation_found", None)
    assert r.warning_card["code"] == "G6_E_MOTION_NO_VIOLATION_FOUND"
    assert "not a safety clearance" in r.warning_card["message"]
    said = " ".join(r.assumptions)
    for unchecked in ("lubricated", "twisting", "between waypoints"):
        assert unchecked in said, unchecked


def test_grasp_margin_round_trips_over_mcp_and_refuses_a_claim() -> None:
    # Removing block.py's "grasp_margin" entry from _RUN fails this.
    robot = body().model_dump(mode="json")
    ok = invoke_reply("adapt_physical_ai", "grasp_margin", json.dumps(
        {"robot": robot, "grasp": grasp((10.0, 1.0)).model_dump(mode="json")}))
    assert ok["ok"] and ok["result"]["completion_state"] == "qualified-draft"
    slip = invoke_reply("adapt_physical_ai", "grasp_margin", json.dumps(
        {"robot": robot, "grasp": grasp((2.0, 3.0)).model_dump(mode="json")}))
    assert slip["result"]["code"] == SLIP
    claim = invoke_reply("adapt_physical_ai", "grasp_margin", json.dumps(
        {"robot": robot, "grasp": grasp((2.0, 3.0)).model_dump(mode="json"), "approved": True}))
    assert not claim["ok"] and claim["error"].startswith("[G6_E_INPUT_VALIDATION]")
    missing = invoke_reply("adapt_physical_ai", "grasp_margin", json.dumps({"robot": robot}))
    assert missing["result"]["code"] == "G6_E_MOTION_CANNOT_ASSESS" and missing["result"]["missing"] == ["grasp"]


# --- review fix pass: the gripper's rating, an unloaded contact, what was not seen, the cap, an unusual coefficient --

def test_a_gripper_rated_on_its_total_is_checked_against_the_sum_of_the_normal_forces() -> None:
    # Two contacts at 30 N each pass a 40 N limit one by one, but press 60 N together, as the archive's whole-gripper
    # validate_gripper_force compared. Removing grasp.py's `rated_as == "total"` sum test fails this.
    r = grasp_margin(body(max_force=40.0, rated_as="total"), grasp((30.0, 1.0), (30.0, 1.0)))
    assert (r.code, r.violations_found) == (GRIPPER, 1)
    assert r.first_violation["detail"] == {"contact": "all contacts", "rule": "gripper_force", "rated_as": "total",
                                           "normal_force_total": 60.0, "limit": 40.0, "over_by": pytest.approx(20.0)}
    # the reviewer's case: three contacts at 35 N against a 40 N gripper
    three = grasp((35.0, 1.0), (35.0, 1.0), (35.0, 1.0))
    assert grasp_margin(body(max_force=40.0, rated_as="total"), three).code == GRIPPER
    assert grasp_margin(body(max_force=40.0, rated_as="per_contact"), three).completion_state == "qualified-draft"


def test_a_gripper_whose_rating_is_not_given_cannot_be_assessed() -> None:
    # Removing grasp.py's `gripper.rated_as is None` cannot-assess reason reads an unstated rating as one of the two.
    r = grasp_margin(body(max_force=40.0, rated_as=None), grasp((30.0, 1.0), (30.0, 1.0)))
    assert (r.completion_state, r.code, r.verdict) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS",
                                                       "cannot_assess")
    assert "robot.gripper.rated_as" in r.reason
    plan = check_plan(carried(grasp((30.0, 1.0), (30.0, 1.0))), WORLD, body(max_force=40.0, rated_as=None), GOAL)
    assert plan.code == "G6_E_MOTION_CANNOT_ASSESS" and "rated_as" in plan.cannot_assess[0]["reason"]
    with pytest.raises(ValidationError):
        Gripper.model_validate({"max_force": 40.0, "rated_as": "per finger"})


def test_a_contact_with_no_load_along_its_surface_does_not_slip() -> None:
    # Removing grasp.py's `row["tangential_force"] > 0` condition reports an unloaded contact as slipping.
    r = grasp_margin(body(), grasp((10.0, 0.0), mu=0.0))
    assert (r.completion_state, r.violations_found) == ("qualified-draft", 0)
    assert grasp_margin(body(), grasp((10.0, 1e-6), mu=0.0)).code == SLIP  # any load on a frictionless contact slips


def test_the_grasp_row_names_the_waypoints_whose_grasp_was_checked_and_no_more() -> None:
    # A grasp given at waypoint 0 only, the object carried on through 1 and 2: the result must not read as covering
    # them. Removing grasp.py's `row={"grasp_waypoints": ...}` (and its assumption) fails this.
    plan = Plan(waypoints=[Waypoint(q=[1.0 + i, 1.0], t=2.0 * i, grasp=grasp((10.0, 1.0)) if i == 0 else None)
                           for i in range(3)])
    r = check_plan(plan, WORLD, body(), GOAL, today=RETRIEVED)
    assert r.completion_state == "qualified-draft" and r.checks[-1]["grasp_waypoints"] == [0]
    said = " ".join(r.assumptions)
    assert "waypoints [0]" in said and "not checked" in said
    empty = check_plan(carried(None), WORLD, body(), GOAL)
    assert empty.checks[-1] == {"check": "grasp_margin", "result": "not applicable",
                                "reason": "no grasp was given; nothing held was checked"}


def test_violations_past_the_listing_cap_are_counted_but_never_built() -> None:
    # Twenty waypoints, each with twenty slipping contacts: 400 slips. Removing grasp.py's `if len(found) >= room:`
    # cap builds all 400 Violation objects before the result lists 200.
    from g6.embodied._motion import checks as engine

    slipping = grasp(*[(1.0, 5.0)] * 20, crush=1e3)
    plan = Plan(waypoints=[Waypoint(q=[1.0, 1.0], t=float(i), grasp=slipping) for i in range(20)])
    goal = Goal(q=[1.0, 1.0], tolerance=1e-9)
    case = engine.Case(plan, WORLD, body(max_force=1e3), goal)
    outcome = next(c for c in engine.registered_checks() if c.name == "grasp_margin").run(case)
    assert (len(outcome.violations), outcome.unlisted) == (engine.MAX_LISTED, 400 - engine.MAX_LISTED)
    r = check_plan(plan, WORLD, body(max_force=1e3), goal)
    assert (len(r.violations), r.violations_found) == (engine.MAX_LISTED, 400)
    assert r.checks[-1]["violations"] == 400 and "400 violations found in all" in r.reason


def test_a_friction_coefficient_above_the_usual_range_is_named_never_silently_accepted() -> None:
    # The source: a coefficient of friction is "usually between 0 and 1.0". A coefficient of 3 that clears a 20 N load
    # on a 10 N press still passes, but says so. Removing grasp.py's coefficient_findings fails this.
    r = grasp_margin(body(), grasp((10.0, 20.0), mu=3.0))
    assert r.completion_state == "qualified-draft"
    assert [f["finding"] for f in r.findings] == ["friction_coefficient_above_usual"]
    assert r.findings[0]["friction_coefficient"] == 3.0 and "usually between 0 and 1.0" in r.findings[0]["message"]
    assert "usually between 0 and 1.0" in r.warning_card["message"] and r.findings[0] in r.evidence
    plan = check_plan(carried(grasp((10.0, 20.0), mu=3.0)), WORLD, body(), GOAL)
    assert plan.checks[-1]["findings"][0]["finding"] == "friction_coefficient_above_usual"
    assert "usually between 0 and 1.0" in " ".join(plan.assumptions)
    assert grasp_margin(body(), grasp((10.0, 1.0), mu=1.0)).findings == []
    assert "usual_range" in entries()["openstax-up1-2016-6.2"]


def test_the_register_marks_an_omitted_sentence_apart_from_an_undecoded_symbol() -> None:
    # Removing "[sentence omitted]" from the clause hides that a sentence of the source was left out.
    from g6.core.component_certificate import sha

    entry = entries()["openstax-up1-2016-6.2"]
    assert "maximum value of [...] [sentence omitted] Once" in entry["clause"]
    assert entry["clause_sha256"] == sha(entry["clause"].encode("utf-8"))
    assert "(137 N), so the crate can no longer remain at rest" in entry["worked_example"]["clause"]


def test_the_skill_says_the_tangential_force_carries_weight_and_inertial_load() -> None:
    # Removing SKILL.md's sentence on the load split fails this.
    from g6.mcp import describe_reply

    skill = " ".join(describe_reply("adapt_physical_ai")["skill"].split())
    assert "weight and inertial load" in skill and "G6 does not check how the load is split" in skill
