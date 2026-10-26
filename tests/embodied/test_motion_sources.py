# SPDX-License-Identifier: Apache-2.0
"""The vetting record of the motion checks (handoff section 4): every rule they apply traces to a committed source
register entry with all its fields filled, or to fields the caller supplies from its own robot's specification; an
entry past its review window raises a finding; no standard's text is reproduced; and the shared code passes the
conformance kit's static checks, which do not scan ``_``-prefixed family code by themselves."""
from __future__ import annotations

import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from g6.embodied import _motion
from g6.embodied._motion import Goal, Obstacle, Plan, PointBody, Waypoint, World, check_plan, registered_checks
from g6.embodied._motion.model import Contact, Grasp, Gripper, Joint, PlanarArm
from g6.embodied._motion.sources import FIELDS, RULES, entries, register_problems, review, stale_sources

OSS = Path(__file__).resolve().parents[2]
RETRIEVED = dt.date(2026, 10, 21)
#: Robot-safety standards the archive named without a safety case; nothing here may claim them.
STANDARD = re.compile(r"ISO[ /]?(TS )?(10218|15066|13482)|IEC 61508|\bSIL[ _-]?\d")
#: The parts of a clause a pass that matched formulas "by number and context, not read" did not read as text.
FORMULA = re.compile(r"[Ee]quations? \(|Figure|Example|worked case")


def test_every_register_entry_has_every_field_and_every_rule_resolves() -> None:
    assert register_problems() == []
    for entry in entries().values():
        assert all(str(entry[f]).strip() for f in FIELDS), entry["id"]
        assert entry["url"].startswith("http") and entry["review_months"] > 0


def test_every_rule_a_registered_check_applies_comes_from_the_register_or_the_caller() -> None:
    for check in registered_checks():
        for name in check.rules:
            rule = RULES[name]
            assert rule.kind in ("register", "caller", "own"), (check.name, name)
            if rule.kind == "register":
                assert rule.source in entries(), (check.name, name)


_MODELS = {"robot": (PointBody, PlanarArm), "world": (World,), "goal": (Goal,), "plan": (Plan,),
           "robot.joints[]": (Joint,), "plan.waypoints[]": (Waypoint,), "world.obstacles[]": (Obstacle,),
           "robot.gripper": (Gripper,), "grasp": (Grasp,), "grasp.contacts[]": (Contact,)}
#: Tiered rules added after the independent pass (data/rule_review.json), each still owed its row there.
AWAITING_INDEPENDENT_PASS: set[str] = set()
#: Rules whose source changed after the pass, each owed a re-read: their rows stay, but their source hash is the old
#: source's until the reader re-reads them.
AWAITING_RE_READ: set[str] = set()


def test_each_caller_rule_names_fields_the_caller_actually_supplies() -> None:
    for name, rule in RULES.items():
        if rule.kind != "caller":
            continue
        for path in rule.source:
            owner, _, leaf = path.rpartition(".")
            assert any(leaf in m.model_fields for m in _MODELS[owner]), (name, path)


def test_g6s_own_bounds_can_only_tighten_an_answer() -> None:
    # Certificates v2a: a guard of G6's own can only refuse or escalate here (never let a plan through).
    own = {n: r for n, r in RULES.items() if r.kind == "own"}
    # removing sources.py's "force_tolerance" rule (the grasp check's 1e-9 N) fails this
    assert set(own) == {"tolerance", "work_bound", "bounds", "force_tolerance"}
    assert all(r.effect in ("refuse", "escalate") and r.tier == "guard" for r in own.values())


def test_an_independent_pass_checked_every_register_rule_against_its_source() -> None:
    # The version 2a record (the format the certificate issuer enforces): one finding per tiered rule.
    record = review()
    assert record["kind"] == "review" and "not the code's author" in record["reviewer"]
    assert record["label_effect"].startswith("none")
    rows = {r["rule"]: r for r in record["rules"]}
    # a tiered rule is in the record or named as still owed its pass, never silently left out of both; adding a
    # tiered rule to sources.RULES without either fails this
    assert set(rows) | AWAITING_INDEPENDENT_PASS == {n for n, r in RULES.items() if r.tier}
    assert not set(rows) & AWAITING_INDEPENDENT_PASS
    unread = "not read" in record["method"]
    for name, row in rows.items():
        assert row["code_matches"] is True, name
        if RULES[name].source == "g6":  # a guard on G6's own threshold has no clause to hash or judge
            assert row["source_sha256"] is None and row["clause_fair"] is None, name
            continue
        # the reader's own hash of the bytes it read is the register's
        assert name in AWAITING_RE_READ or row["source_sha256"] == entries()[RULES[name].source]["sha256"], name
        # a clause is fair (true) only where its text was read; one resting on a formula the pass did not read is
        # fair by context only, and says why
        assert row["clause_fair"] in (True, "by context only"), name
        if row["clause_fair"] == "by context only":
            assert row["context_attested"] is True and row["reason"].strip(), name
        if unread and FORMULA.search(entries()[RULES[name].source]["clause"]):
            assert row["clause_fair"] == "by context only", name


def test_chain_is_computed_on_5_3_4s_printed_chain_and_rrt_names_its_shortcuts() -> None:
    # Fix pass 2, PA2-PA4: chain back on 3.3.1 (no printed value), an rrt text silent on plan_rrt's two shortcuts, or
    # a friction licence that is not the held copy's own statement, fails this.
    from g6.embodied._motion.geometry import chain_points

    chain = RULES["chain"]
    assert (chain.tier, chain.source) == ("computes", "lavalle-2006-5.3.4-chain")
    assert "(500,0)" in entries()[chain.source]["clause"]
    assert chain_points([0, 0], [10] * 50, [0] * 50)[-1] == [500.0, 0.0]
    assert "first straight attempt" in RULES["rrt"].rule and "within one step" in RULES["rrt"].rule
    assert "©2016 Rice University" in entries()["openstax-up1-2016-6.2"]["licence"]


def test_the_evidence_cites_only_the_rules_applied_to_this_body() -> None:
    world = World(lower=[-2, -2], upper=[2, 2], obstacles=[])
    point = check_plan(Plan(waypoints=[Waypoint(q=[0.0, 0.0], t=0.0), Waypoint(q=[1.0, 0.0], t=2.0)]), world,
                       PointBody(kind="point", radius=0.1, max_speed=1.0, clearance=0.0),
                       Goal(q=[1.0, 0.0], tolerance=0.0), today=RETRIEVED)
    arm = check_plan(Plan(waypoints=[Waypoint(q=[0.0], t=0.0), Waypoint(q=[0.5], t=2.0)]), world,
                     PlanarArm(kind="planar_arm", base=[0, 0], link_lengths=[1.0], link_radius=0.0, clearance=0.0,
                               joints=[Joint(name="j", lower=-1, upper=1, max_velocity=1)]),
                     Goal(q=[0.5], tolerance=0.0), today=RETRIEVED)
    point_rules = {r["rule"] for r in point.evidence[0]["rules"]}
    arm_rules = {r["rule"] for r in arm.evidence[0]["rules"]}
    assert {"swept_point", "distance", "bounds"} <= point_rules and not point_rules & {"swept_arm", "chain", "work_bound"}
    assert {"swept_arm", "chain", "joint_limits"} <= arm_rules and "swept_point" not in arm_rules


def test_an_entry_past_its_review_window_raises_a_stale_source_finding() -> None:
    # entries retrieved on different days fall due on different days: each is due on the day itself, stale the next;
    # removing stale_sources' `if today > due` finding fails this
    assert stale_sources(RETRIEVED) == []
    for entry_id, entry in entries().items():
        retrieved = dt.date.fromisoformat(entry["retrieved"])
        assert entry["review_months"] == 12, entry_id
        due = retrieved.replace(year=retrieved.year + 1)
        assert entry_id not in {f["source"] for f in stale_sources(due)}, entry_id
        assert entry_id in {f["source"] for f in stale_sources(due + dt.timedelta(days=1))}, entry_id
    late = stale_sources(dt.date(2027, 10, 23))
    assert {f["source"] for f in late} == set(entries()) and all(f["finding"] == "stale_source" for f in late)


def test_a_check_plan_answer_carries_its_rules_and_any_stale_finding() -> None:
    args = (Plan(waypoints=[Waypoint(q=[1.0, 1.0], t=0.0), Waypoint(q=[2.0, 1.0], t=2.0)]),
            World(lower=[0, 0], upper=[10, 10], obstacles=[]),
            PointBody(kind="point", radius=0.1, max_speed=1.0, clearance=0.0), Goal(q=[2.0, 1.0], tolerance=0.0))
    fresh = check_plan(*args, today=RETRIEVED)
    assert fresh.completion_state == "qualified-draft" and fresh.source_findings == []
    cited = {r["rule"]: r for r in fresh.evidence[0]["rules"]}
    assert cited["swept_point"]["source"] == "lavalle-2006-5.3.4" and cited["swept_point"]["url"]
    assert cited["speed_limits"]["caller_fields"]
    stale = check_plan(*args, today=dt.date(2030, 1, 1))
    assert stale.source_findings and any(e.get("finding") == "stale_source" for e in stale.evidence)


def test_no_standards_text_is_reproduced_and_no_standard_is_claimed() -> None:
    for entry in entries().values():
        assert len(entry["clause"]) <= 500, entry["id"]  # a reference to the clause, never the text
        assert not re.search(r"\b(ISO|IEC|ANSI|NFPA|IEEE)\b", entry["issuing_body"]), entry["id"]
    for path in Path(_motion.__file__).parent.rglob("*.py"):
        assert not STANDARD.search(path.read_text(encoding="utf-8")), path.name


def _kit():
    if "conformance_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location("conformance_kit", OSS / "tests" / "conformance" / "kit.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["conformance_kit"] = module
        spec.loader.exec_module(module)
    return sys.modules["conformance_kit"]


@pytest.mark.parametrize("check", ["g1_labels", "g2_g9_processes_and_network", "g6_models_and_keys", "g7_doubles",
                                   "g8_headers", "d4_isolation", "d4_home"])
def test_the_shared_motion_code_passes_the_kits_static_checks(check: str) -> None:
    kit = _kit()
    root = Path(_motion.__file__).parent
    files = tuple(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts))
    target = kit.Target("adapt_physical_ai", "g6.embodied._motion", root, files, "embodied", "builtin")
    assert getattr(kit, check)(target) == []
