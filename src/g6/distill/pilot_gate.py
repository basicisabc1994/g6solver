# SPDX-License-Identifier: Apache-2.0
"""Exploratory, study-only replacement gate; production authority is untouched.

Production references are acceptance_gate.py at the frozen P2/probe merge.
Every changed or replaced check is recorded here for owner review:

Check                       Production lines/value                Pilot value
Policy                      256-268 protected defaults             PilotPolicy digest, separate version
Admission repositories      2474-2500, 2683-2684: >=30              allocation count, 5
Monitor repositories        2474-2500, 2610-2617: >=30 per bound    allocation count, 3
Quality R2 FN/FP             2580-2605: upper-95 <=.02              no teacher-correct unit becomes wrong;
                                                                   added false acceptances =0
Quality R1 graph loss        2598-2604: upper bound <=.03            same unit rule, teacher-only baseline
Admission FPR               2608-2617, 2710: upper-95 delta <=.02    no negative unit adds a false alarm
Monitor FPR                 2731-2735: upper-95 delta <=.02         zero observed monitor false positives
Cost at H                   2530-2548, 2741-2745: upper-95 <=0      point estimate <=0 at H=1000
Prior validation            2728: cumulative validation charged   study candidate journal, all attempts
Coverage                    2703-2708: point >=.10                 raw unit accuracy; abstain is incorrect
Planted/trivial controls     2711-2721: planted + sign test          both classes required; no sign-test bar
Library/regression          2690-2700, 2722: current union/replay    leaf method, empty library baseline
Repeated looks/batch         2640-2641, 2153-2158: max 5 + BH       one immutable study admission per candidate;
                                                                   no batch promotion
Publication/authority        1662-1709, 2160-2173: production A1     study bundle only; no production authority
Probe execution             496-523, 675-681: evaluator runner     also execution.run_candidate in isolation;
                                                                   both paths must agree, both are charged

Kept checks (same production helper or same invariant):
settled labels only (2657-2658); training never evidence (2659-2660);
leakage guard (2661-2662); teacher trace per registered case (2651-2665);
one repository per unit (2666-2670); admission/monitor disjoint (2671-2674);
registration before any concept (306-324, via _load); independent oracle
headline/strata (2553-2568); registered replacement scope/bounds (2620-2632);
three-run stability and wall budget (734-753); cost comparison at H=1000
and finite, registered cost input bounds (2530-2537). Production quality and
cost bounds are reported, including Inconclusive messages; neither admits.

Scope predicates are already assessed by this production merge (672-674).
No scope relaxation is needed. This gate is an evaluator/study API, not an MCP
or serving endpoint. Callers do not supply observations, oracle labels or runners.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Literal
from uuid import uuid4

from g6.core.ledger import canonical, digest
from g6.core.repair_log import _process_lock
from g6.core.storage import contained, g6_home

from . import acceptance_gate as production
from . import execution
from .library import Library
from .probes import ProbeInput
from .records import (AcceptanceEvidence, Cost, DecidedBy, FieldEvidence, LibraryEntry,
                      RegressionCase, VerifierCandidate)

PILOT_GATE_VERSION = "distill-pilot-gate-v1"


@dataclass(frozen=True)
class PilotPolicy:
    """Owner-reviewable bounds; changing these requires a new gate version."""

    admission_repositories: int = 5
    monitor_repositories: int = 3
    minimum_units_per_label: int = 1
    minimum_accuracy_delta: int = 0
    maximum_added_false_acceptances: int = 0
    maximum_monitor_false_positives: int = 0
    horizon: int = 1000
    maximum_cost_point: float = 0.
    maximum_admissions_per_candidate: int = 1
    publication_allowance_s: float = 1.


@dataclass(frozen=True)
class PilotDecision:
    """Separate shape deliberately omits the production consumer's verdict field."""

    pilot_verdict: Literal["admit", "refuse"]
    code: str
    production_decision: production.AcceptanceDecision
    candidate_id: str
    policy_sha256: str
    allocation_sha256: str
    measurements: dict
    admission_path: str = ""
    exploratory: Literal[True] = True
    pilot_version: str = PILOT_GATE_VERSION


class PilotRefused(ValueError):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code


def _require(condition, code, reason):
    if not condition:
        raise PilotRefused(code, reason)


def _cost_append(path, row):
    try:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError) as error:
        raise PilotRefused("PILOT_COST_RECORD", "study validation cost append failed") from error


def _pilot_validation_total(path, candidate_id, active):
    """Study equivalent of production._reconcile_validation, without production stores.

    Sum every completed attempt, retaining checkpoints on interruption. An orphan
    start is charged through recovery only with continuous wall/monotonic clocks.
    The caller holds the candidate's OS lock throughout assessment and publication.
    """
    attempts, total = {}, 0.
    if not path.exists():
        return total
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                _require(row["candidate_id"] == candidate_id, "PILOT_COST_HISTORY", "cost candidate differs")
                ident, kind = row["attempt"], row["kind"]
                if kind == "start":
                    _require(ident not in attempts, "PILOT_COST_HISTORY", "duplicated validation attempt")
                    _require(all(type(row[k]) in (int, float) and math.isfinite(row[k])
                                 for k in ("monotonic_start", "wall_start")),
                             "PILOT_COST_HISTORY", "invalid validation clock")
                    attempts[ident] = {"opening": row, "floor": 0., "open": True}
                else:
                    _require(kind in {"checkpoint", "finish"} and ident in attempts and attempts[ident]["open"],
                             "PILOT_COST_HISTORY", "validation history order invalid")
                    attempt = attempts[ident]
                    seconds = row["validation_s"]
                    _require(type(seconds) in (int, float) and math.isfinite(seconds)
                             and seconds >= attempt["floor"]
                             and row["validation_total_s"] == total + seconds,
                             "PILOT_COST_HISTORY", "validation cost decreased or invalid")
                    attempt["floor"] = seconds
                    if kind == "finish":
                        attempt["open"] = False
                        total += seconds
        for ident, attempt in attempts.items():
            if not attempt["open"] or ident == active:
                continue
            opening = attempt["opening"]
            elapsed = time.monotonic() - opening["monotonic_start"]
            wall = time.time() - opening["wall_start"]
            _require(all(math.isfinite(v) and v >= 0 for v in (elapsed, wall))
                     and abs(wall - elapsed) <= max(2., .01 * max(wall, elapsed))
                     and (not sys.platform.startswith("linux") or bool(opening["boot_id"]))
                     and production._boot_identity() == opening["boot_id"],
                     "PILOT_COST_HISTORY", "interrupted cost needs clock recovery")
            seconds = max(attempt["floor"], elapsed)
            total += seconds
            _cost_append(path, {"kind": "finish", "attempt": ident, "candidate_id": candidate_id,
                               "validation_s": seconds, "validation_total_s": total})
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, PilotRefused):
            raise
        raise PilotRefused("PILOT_COST_HISTORY", "study validation cost history unreadable") from error
    _require(math.isfinite(total), "PILOT_COST_HISTORY", "finite cumulative validation cost required")
    return total


def _validation_charge(meter, allowance=0., *, finish=False):
    elapsed = time.monotonic() - meter["started"]
    _require(math.isfinite(elapsed) and elapsed >= 0, "PILOT_COST_HISTORY", "validation clock moved backwards")
    seconds = max(meter["floor"], elapsed + allowance)
    total = meter["prior"] + seconds
    _require(math.isfinite(total), "PILOT_COST_HISTORY", "finite cumulative validation cost required")
    _cost_append(meter["path"], {"kind": "finish" if finish else "checkpoint", "attempt": meter["attempt"],
        "candidate_id": meter["candidate_id"], "validation_s": seconds, "validation_total_s": total})
    meter.update(floor=seconds, closed=finish)
    return total


@contextmanager
def _validation_attempt(root, candidate_id, policy):
    path = contained(root, root / "pilot-validation" / f"{candidate_id}.jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _process_lock(contained(root, path.with_suffix(".lock"))):
            started, wall = time.monotonic(), time.time()
            ident = uuid4().hex
            _cost_append(path, {"kind": "start", "attempt": ident, "candidate_id": candidate_id,
                "monotonic_start": started, "wall_start": wall, "boot_id": production._boot_identity()})
            prior = _pilot_validation_total(path, candidate_id, ident)
            meter = {"path": path, "prior": prior, "started": started, "attempt": ident,
                     "candidate_id": candidate_id, "floor": 0., "closed": False}
            try:
                yield meter
            finally:
                if not meter["closed"]:
                    total = _validation_charge(meter, policy.publication_allowance_s, finish=True)
                    if meter.get("admitting"):
                        _require(total <= meter["assessed_total"], "PILOT_PUBLICATION_BUDGET",
                                 "validation and publication exceeded charged allowance")
    except OSError as error:
        raise PilotRefused("PILOT_COST_RECORD", "study validation cost storage unavailable") from error


def pilot_point_cost(fixed_difference, savings, *, value_bounds, policy):
    """A different point rule, not the production bound rule; H stays registered.

    Validate the production cost inputs before arithmetic. Production's bounded
    interval is called separately on these same inputs and is only reported.
    """
    _require(math.isfinite(fixed_difference), "PILOT_COST_FIXED", "finite fixed cost required")
    samples = [s if isinstance(s, production._ClusterValue) else production._ClusterValue(s, str(i))
               for i, s in enumerate(savings)]
    lo, hi = value_bounds
    _require(samples and all(math.isfinite(v) for v in (lo, hi)) and lo < hi
             and all(math.isfinite(s.value) and lo <= s.value <= hi
                     and math.isfinite(s.weight) and s.weight > 0 for s in samples),
             "PILOT_COST_BOUNDS", "finite savings within registered bounds required")
    saving = math.fsum(s.value * s.weight for s in samples) / math.fsum(s.weight for s in samples)
    point = fixed_difference - policy.horizon * saving
    kind = ("dominates" if fixed_difference <= 0 <= saving else
            "break_even" if fixed_difference > 0 and saving > 0 else
            "crossover" if fixed_difference < 0 and saving < 0 else "never")
    return {"horizon": policy.horizon, "fixed_difference": fixed_difference,
            "per_task_saving": saving, "point": point, "kind": kind,
            "crossing": fixed_difference / saving if kind in {"break_even", "crossover"} else None,
            "admissible": point <= policy.maximum_cost_point}


def _reported(call):
    try:
        return {"verdict": "assessed", "result": call()}
    except production.Inconclusive as error:
        return {"verdict": "inconclusive", "message": str(error)}
    except ValueError as error:
        return {"verdict": "refuse", "message": str(error)}


def _production_decision(candidate, frozen):
    try:
        return production._assess_replacement(candidate, [], frozen, 1)[0]
    except production.Inconclusive as error:
        return production.AcceptanceDecision("inconclusive", (str(error),), candidate.candidate_id)
    except ValueError as error:
        return production.AcceptanceDecision("refuse", (str(error),), candidate.candidate_id)


def _study_root(raw):
    path = Path(raw)
    _require(path.is_absolute(), "PILOT_STORAGE_ROOT", "absolute study root required")
    # Refuse aliases before creating a directory, and exclude the whole state root.
    _require(not any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)),
             "PILOT_STORAGE_ROOT", "linked study roots refused")
    root = path.resolve()
    homes = (g6_home().resolve(), Path.home() / ".g6")
    _require(not any(root.is_relative_to(home) or home.is_relative_to(root) for home in homes),
             "PILOT_STORAGE_ROOT", "study storage overlaps production state")
    return root


def _allocation_roles(allocation):
    _require(isinstance(allocation, dict) and allocation.get("cohort") == "development",
             "PILOT_ALLOCATION", "development allocation required")
    roles = allocation.get("roles", {})
    _require(isinstance(roles, dict) and {"admission", "monitor"} <= roles.keys(),
             "PILOT_ALLOCATION", "admission and monitor allocation required")
    seen_repos, seen_bugs = set(), set()
    for group in roles.values():
        _require(isinstance(group, dict), "PILOT_ALLOCATION", "allocation role required")
        repos, bugs = group.get("repositories"), group.get("bugs")
        _require(isinstance(repos, list) and isinstance(bugs, list)
                 and all(isinstance(v, str) and v for v in (*repos, *bugs))
                 and len(set(repos)) == len(repos) and len(set(bugs)) == len(bugs),
                 "PILOT_ALLOCATION", "distinct allocation names required")
        _require(not seen_repos.intersection(repos) and not seen_bugs.intersection(bugs),
                 "PILOT_ALLOCATION", "allocation roles overlap")
        seen_repos.update(repos)
        seen_bugs.update(bugs)
    return roles


def _inputs(candidate, frozen, allocation, policy):
    production._candidate_boundary(candidate)
    _require(candidate.form in {"template_instance", "predicate"} and not candidate.lineage,
             "PILOT_DEPENDENCY", "pilot v1 requires a leaf replacement")
    study = frozen.replacement
    _require(study is not None and study.candidate_id == candidate.candidate_id
             and study.registration_sha256 == frozen.registration_sha256,
             "PILOT_TEACHER_TRACE", "registered replacement traces required")
    fresh = (*frozen.acceptance, *frozen.monitor)
    all_cases = (*fresh, *frozen.tuning, *frozen.regression)
    _require(all(o.label in {"positive", "negative"} for o in all_cases),
             "PILOT_LABELS", "settled labels only")
    _require(not set(candidate.training_case_ids).intersection(o.case.case_id for o in fresh),
             "PILOT_TRAINING", "training cases never evidence")
    _require(not production._leaks(candidate, all_cases), "PILOT_LEAKAGE", "candidate leakage guard refused")
    rows = {r.case_id: r for r in study.rows}
    _require(len(rows) == len(study.rows)
             and {o.case.case_id for o in (*fresh, *frozen.tuning)} <= rows.keys(),
             "PILOT_TEACHER_TRACE", "teacher trace missing or duplicated")
    units = {}
    for o in fresh:
        repo = rows[o.case.case_id].repository
        _require(units.setdefault(o.case.unit_id, repo) == repo,
                 "PILOT_UNIT_REPOSITORY", "unit spans conflicting repositories")
    admission_repos = {rows[o.case.case_id].repository for o in frozen.acceptance}
    monitor_repos = {rows[o.case.case_id].repository for o in frozen.monitor}
    _require(not admission_repos.intersection(monitor_repos), "PILOT_REPOSITORY_OVERLAP",
             "admission and monitor repositories overlap")
    _require(len(admission_repos) >= policy.admission_repositories, "PILOT_REPOSITORIES",
             "allocation admission repository minimum unavailable")
    _require(len(monitor_repos) >= policy.monitor_repositories, "PILOT_MONITOR_REPOSITORIES",
             "allocation monitor repository minimum unavailable")
    roles = _allocation_roles(allocation)
    for name, group, repos, count in (("admission", frozen.acceptance, admission_repos, policy.admission_repositories),
                                     ("monitor", frozen.monitor, monitor_repos, policy.monitor_repositories)):
        _require(len(roles[name]["repositories"]) == count and set(roles[name]["repositories"]) == repos
                 and set(roles[name]["bugs"]) == {o.case.unit_id for o in group},
                 "PILOT_ALLOCATION", "evidence differs from allocation")
    scope = production._replacement_scope(candidate, study)
    admission, strata = production._headline(frozen.acceptance, frozen.oracle_policy)
    monitor, monitor_strata = production._headline(frozen.monitor, frozen.oracle_policy)
    _require(len({rows[o.case.case_id].repository for o in admission}) >= policy.admission_repositories
             and len({rows[o.case.case_id].repository for o in monitor}) >= policy.monitor_repositories,
             "PILOT_HEADLINE", "independent headline repositories unavailable")
    for group in (admission, monitor):
        _require(all(len({o.case.unit_id for o in group if o.label == label}) >= policy.minimum_units_per_label
                     for label in ("positive", "negative")), "PILOT_HEADLINE", "both oracle classes required")
    return study, rows, scope, admission, monitor, strata, monitor_strata


def _measure(candidate, group, scope):
    if candidate.contract.probes:
        _require(os.environ.get("G6_REQUIRE_ISOLATION", "").strip().lower() in {"1", "true", "yes", "on"},
                 "PILOT_ISOLATION", "probe execution requires strict isolation")
    own, costs = production._measure(candidate, group, [])
    if candidate.contract.probes:
        inputs = []
        for o in group:
            before, after = ({"tests/test_case.py": o.case.before_tests}, {"tests/test_case.py": o.case.after_tests})
            data = getattr(o.case, "probe_input", None) or ProbeInput.from_case(o.case)
            inputs.append(execution.Observation(before, after, production._extract_facts(before, after),
                                                probe_input=data))
        wired = execution.run_candidate(candidate, inputs, lambda _: None)
        _require(wired == own, "PILOT_PROBE_DISAGREEMENT", "probe execution paths disagree")
    return [v if production._case_in_scope(scope, o.case) else "abstain" for o, v in zip(group, own)], costs


def _assess(candidate, frozen, allocation, policy, meter, data):
    study, rows, scope, admission, monitor, strata, monitor_strata = _inputs(candidate, frozen, allocation, policy)
    own, costs = _measure(candidate, frozen.acceptance, scope)
    monitored, _ = _measure(candidate, monitor, scope)
    indices = [i for i, o in enumerate(frozen.acceptance) if o in admission]
    selected = [own[i] for i in indices]
    teacher = [rows[o.case.case_id].teacher_verdict for o in admission]
    before, after = production._unit_pairs(admission, teacher, selected)
    delta = sum(after) - sum(before)
    all_teacher = [rows[o.case.case_id].teacher_verdict for o in frozen.acceptance]
    all_before, all_after = production._unit_pairs(frozen.acceptance, all_teacher, own)
    regressed = sum(b and not a for b, a in zip(all_before, all_after))
    teacher_alarms = {o.case.unit_id for o, t in zip(frozen.acceptance, all_teacher)
                      if o.label == "negative" and t == "concern"}
    candidate_alarms = {o.case.unit_id for o, q in zip(frozen.acceptance, own)
                        if o.label == "negative" and q == "concern"}
    added_alarms = candidate_alarms - teacher_alarms
    added = {o.case.unit_id for o, t, q in zip(admission, teacher, selected)
             if o.label == "positive" and q == "no_concern" and t != "no_concern"}
    units = {o.case.unit_id: o for o in admission}
    differences = [production._ClusterValue(float(b) - float(a), rows[o.case.case_id].repository)
                   for o, b, a in zip(units.values(), before, after)]
    # The production quality path supplies the actual bounds or abstention message.
    combined = [t if q == "abstain" else q for t, q in zip(teacher, selected)]
    quality = _reported(lambda: production._replacement_quality(admission, rows, teacher, teacher,
        combined, selected, [production._case_in_scope(scope, o.case) for o in admission],
        production.EvidencePolicy().threshold))
    margin = _reported(lambda: production._margin_bound(differences, .03, production.EvidencePolicy().threshold))
    serving = [statistics.fmean(costs[3*i:3*i+3]) for i in range(len(frozen.acceptance))]
    savings = [(rows[o.case.case_id].teacher_s * study.teacher_weight * (1 - study.shadow_rate)
                if q != "abstain" else 0.) - c * study.cpu_weight - rows[o.case.case_id].maintenance_delta_s
               for o, q, c in zip(frozen.acceptance, own, serving)]
    # Reserve publication and a terminal cost append before testing the point cost.
    validation_s = _validation_charge(meter, policy.publication_allowance_s
        + max(policy.publication_allowance_s, study.activation_allowance_s or 0.))
    fixed = study.fixed_new_s - study.fixed_base_s + validation_s * study.cpu_weight
    samples = production._cluster_differences(frozen.acceptance, rows, savings)
    production_cost = _reported(lambda: production._replacement_cost(fixed, samples,
                                                                    value_bounds=study.task_saving_bounds))
    data.update({"validation_s": validation_s, "accuracy_delta": delta, "regressed_units": regressed,
            "added_false_acceptances": len(added), "added_admission_false_positives": len(added_alarms),
            "teacher_admission_false_positives": len(teacher_alarms),
            "admission_false_positives": len(candidate_alarms),
            "production_quality": quality, "production_margin": margin, "production_cost": production_cost,
            "strata": strata, "monitor_strata": monitor_strata})
    cost = pilot_point_cost(fixed, samples, value_bounds=study.task_saving_bounds, policy=policy)
    data["cost"] = cost
    meter["assessed_total"] = validation_s
    _require(len(added) <= policy.maximum_added_false_acceptances, "PILOT_FALSE_ACCEPTANCE",
             "replacement adds false acceptances")
    _require(not added_alarms, "PILOT_ADMISSION_FP", "negative admission unit adds a false alarm")
    _require(delta >= policy.minimum_accuracy_delta, "PILOT_QUALITY", "unit accuracy decreased")
    _require(not regressed, "PILOT_UNIT_REGRESSION", "teacher-correct admission unit became incorrect")
    fp = production._counts(monitor, monitored)["fp"]
    _require(fp <= policy.maximum_monitor_false_positives, "PILOT_MONITOR_FP", "monitor false positive")
    _require(cost["admissible"], "PILOT_COST", "point cost at H exceeds zero")
    return own, costs


def _bundle(candidate, frozen, answer, own, costs):
    counts = production._counts(frozen.acceptance, own)
    report_digest = digest(asdict(answer))
    evidence = AcceptanceEvidence(acceptance_report_sha256=report_digest, policy_sha256=answer.policy_sha256,
        split_sha256=frozen.split_sha256, reuse_index=1, **counts,
        precision=production._interval(counts["tp"], counts["tp"] + counts["fp"]),
        recall=production._interval(counts["tp"], counts["n_pos"]),
        false_rejection=production._interval(counts["fp"], counts["n_neg"]),
        cost=Cost(median_s=statistics.median(costs), p95_s=sorted(costs)[math.ceil(.95 * len(costs)) - 1]))
    authority = asdict(answer) | {"scope": "study_only", "report_sha256": report_digest}
    now = datetime.now(timezone.utc).isoformat()
    entry = LibraryEntry(candidate_id=candidate.candidate_id, contract_sha256=candidate.contract.content_id,
        concept_id=candidate.concept_id, family=candidate.contract.family, version=1, parents=[],
        status="probation", authority="A1", evidence=evidence,
        regression_suite=[RegressionCase(case_ref=o.case.case_id, verdict=v) for o, v in zip(frozen.acceptance, own)],
        field=FieldEvidence(fired=0, confirmed=0, refuted=0, unlabelled=0, precision=None),
        decided_by=DecidedBy(gate_version=PILOT_GATE_VERSION, person=None), certificate_sha256=digest(authority),
        created_at=now, changed_at=now, reason="exploratory study admission only", replacement="R",
        replacement_scope=frozen.replacement.scope, teacher=frozen.replacement.teacher,
        noninferiority=digest(answer.measurements))
    return {"entry": entry.model_dump(mode="json"), "authority": authority}


def decide_pilot(candidate: VerifierCandidate, *, study_root: Path, allocation: dict,
                 registration: str) -> PilotDecision:
    """Load registered evaluator custody, assess once, and publish only in the study.

    Production _load retains its owner anchors, training ancestry and chronology.
    No environment variable or production store is redirected for publication.
    """
    policy = PilotPolicy()
    allocation_sha256 = digest(allocation)
    policy_sha256 = digest(asdict(policy))
    ident = candidate.candidate_id if type(candidate) is VerifierCandidate else ""
    prod = production.AcceptanceDecision("block", ("pilot inputs not loaded",), ident)
    measurements = {}
    created = None
    admitted = False
    try:
        root = _study_root(study_root)
        _require(type(candidate) is VerifierCandidate, "PILOT_CANDIDATE", "typed candidate required")
        candidate = VerifierCandidate.model_validate(candidate.model_dump(mode="json"))
        path = contained(root, root / "pilot-gate" / f"{ident}.json")
        _require(int(path.exists()) < policy.maximum_admissions_per_candidate,
                 "PILOT_REUSED", "candidate already has a study admission")
        with _validation_attempt(root, ident, policy) as meter:
            _require(not path.exists(), "PILOT_REUSED", "candidate already has a study admission")
            try:
                frozen = production._load(registration, candidate, Library(), True)
            except (ValueError, KeyError, FileNotFoundError) as error:
                raise PilotRefused("PILOT_REGISTRATION", "production registration loader refused") from error
            prod = _production_decision(candidate, frozen)
            own, costs = _assess(candidate, frozen, allocation, policy, meter, measurements)
            answer = PilotDecision("admit", "PILOT_ADMIT", prod, ident, policy_sha256,
                                   allocation_sha256, measurements, str(path))
            publishing = time.monotonic()
            bundle = _bundle(candidate, frozen, answer, own, costs)
            encoded = canonical(bundle) + "\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            path = contained(root, path)
            _require(time.monotonic() - publishing <= max(policy.publication_allowance_s,
                     frozen.replacement.activation_allowance_s or 0.), "PILOT_PUBLICATION_BUDGET",
                     "study publication preparation exceeded its charged allowance")
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                created = path
                stream.write(encoded)
            meter["admitting"] = True
        admitted = True
        return answer
    except PilotRefused as error:
        measurements = measurements | {"refusal": str(error)}
        code = error.code
    except (ValueError, KeyError, OSError):
        code = "PILOT_INPUT"
    finally:
        if created is not None and not admitted:
            try:
                created.unlink()
            except OSError:
                code = "PILOT_PUBLICATION_CLEANUP"
    return PilotDecision("refuse", code, prod, ident, policy_sha256, allocation_sha256, measurements)
