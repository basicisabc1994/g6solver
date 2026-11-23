# SPDX-License-Identifier: Apache-2.0
"""Independent, owner-protected acceptance and authority decisions (design section 6).

Only this module consumes registered evaluator custody. Proposals contain methods,
never reports, labels, policies or predictions. All statistics used for admission
are adapted to the existing metaharness gate; no executor/model is called there.
Host custody and mission mounts must enforce the OS boundary: Python module
identity and hash chains do not protect against arbitrary same-user shell access.
"""
from __future__ import annotations

import ast
import hashlib
import math
import os
import re
import statistics
import time
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.binomial import upper_bound
from g6.core.ledger import ChainLedger, canonical, digest
from g6.core.storage import state_path
from g6.distillation.sandbox import run_batch
from g6.metaharness import gate as meta_gate
from g6.metaharness.evaluate import EvaluationReport, _paired, _sign
from g6.metaharness.schema import EvidencePolicy, Profile, Proposal, Suite, SuiteCase, Workflow
from g6.safety_engine import weakening

from .detectors import SEED_TEMPLATES
from .facts import _extract_facts
from .judging import JUDGING_PATHS
from .ledger import Stores
from .library import Library
from .records import (AcceptanceEvidence, Cost, DecidedBy, FieldEvidence,
                      Interval, LibraryEntry, MethodContract, Model, RegressionCase, Sha256,
                      TemplateInstance, VerifierCandidate)

ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = ROOT / "validation/distill/held_out/policy.json"
VERSION = "distill-acceptance-v1"
_MUTEX = threading.RLock()


@contextmanager
def _writer():
    """Single evaluator writer across threads/processes; stale locks block."""
    with _MUTEX:
        path = state_path("distill", "acceptance_gate.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream = path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            raise ValueError("acceptance evaluator writer busy or recovery required") from None
        try:
            with stream:
                stream.write(str(os.getpid()) + "\n")
            yield
        finally:
            path.unlink()


@dataclass(frozen=True)
class Observation:
    case: object
    label: str
    split: str


@dataclass(frozen=True)
class EvaluationSet:
    registration_sha256: str
    split_sha256: str
    acceptance: tuple[Observation, ...]
    monitor: tuple[Observation, ...]
    regression: tuple[Observation, ...]
    controls: tuple[str, str]


@dataclass(frozen=True)
class AcceptanceDecision:
    verdict: str
    reasons: tuple[str, ...]
    candidate_id: str = ""
    report_sha256: str = ""
    entry_id: str = ""
    p_value: float = 1.0
    minimum_detectable_gain: float = 1.0
    p_value_upper_bound: float | None = None
    minimum_detectable_precision_difference: float | None = None


class FieldOutcome(Model):
    candidate_id: Sha256
    fire_id: Annotated[str, Field(min_length=1, max_length=256)]
    source: Literal["person", "maintainer", "oracle"]
    label: Literal["confirmed", "refuted", "unlabelled"]
    receipt_sha256: Sha256
    unit_id: Annotated[str, Field(min_length=1, pattern=r'\S')] | None = None


class HealthObservation(Model):
    candidate_id: Sha256
    observation_id: Annotated[str, Field(min_length=1, max_length=256)]
    source: Literal["person", "maintainer", "oracle"]
    observed_at: datetime
    wall_s: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    withdrawal_condition: str | None = None
    receipt_sha256: Sha256


def _bh(pairs, batch_size):
    ordered = sorted(pairs)
    cutoff = max((i for i, (p, _) in enumerate(ordered, 1) if p <= .05 * i / batch_size), default=0)
    return {ident for _, ident in ordered[:cutoff]}


def _policy():
    from g6.distill.held_out.registration_gate import strict_json

    data = strict_json(_plain(POLICY_PATH, ROOT / "validation/distill/held_out").read_bytes())
    # A caller cannot relax EvidencePolicy, request sequential looks, or invent
    # new threshold fields. Owner changes require a versioned code review.
    defaults = asdict(EvidencePolicy())
    if (set(data) != {"version", "evidence", "utility_fp_penalty", "fpr_margin", "batch_q", "registrations"}
            or data["version"] != 1 or data["evidence"] != defaults
            or data["utility_fp_penalty"] != 3 or data["fpr_margin"] != .02 or data["batch_q"] != .05
            or not isinstance(data["registrations"], dict)):
        raise ValueError("protected acceptance policy changed")
    return EvidencePolicy(**data["evidence"]), data


def _policy_digest():
    _, data = _policy()
    return digest({k: v for k, v in data.items() if k != "registrations"})


def _judging_digest():
    """Pin reused detector, execution and statistics code as well as this gate (the merge gate protects each)."""
    return digest({p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in JUDGING_PATHS})


def _plain(path: Path, root: Path) -> Path:
    path, root = path.absolute(), root.absolute()
    if not path.is_relative_to(root):
        raise ValueError("input outside evaluator custody")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("linked evaluator input")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("oversized evaluator input")
    return path


def _git(*args):
    # Same fixed environment, disabled programs/hooks and process window as review.
    # RepoRefused is a ValueError with a stable code; do not replace it here.
    from g6.workforce.repo_tests import _git as hardened_git
    return hardened_git(ROOT, *args, timeout_s=20, isolated_config=True)


def _chronology(anchor, registration, candidate):
    commit = anchor["commit"]
    concept = anchor["concepts"][candidate.concept_id]
    later = concept["commit"]
    if not all(isinstance(c, str) and re.fullmatch(r"[0-9a-f]{40}", c) for c in (commit, later)) or commit == later:
        raise ValueError("registration must predate concept")
    for rel in (anchor["path"], concept["path"]):
        if not isinstance(rel, str) or ".." in Path(rel).parts or Path(rel).is_absolute() or ":" in rel:
            raise ValueError("invalid chronology path")
    _git("merge-base", "--is-ancestor", commit, later)
    from g6.distill.held_out.registration_gate import strict_json
    if digest(strict_json(_git("show", f"{commit}:{anchor['path']}"))) != registration.registration_id:
        raise ValueError("registration commit does not bind this registration")
    recorded = strict_json(_git("show", f"{later}:{concept['path']}"))
    # The independently anchored first concept snapshot must be the exact record.
    from .records import Concept
    actual = Stores().for_model(Concept).get(candidate.concept_id)
    if recorded != actual.model_dump(mode="json"):
        raise ValueError("concept commit does not bind this concept")


def _load(registration_id, candidate, library) -> EvaluationSet:
    from g6.distill.held_out import oracle, sealed_gate
    from g6.distill.held_out.case_gate import CaseRecord
    from g6.distill.held_out.registration_gate import read_registration, strict_json

    _, settings = _policy()
    anchor = settings["registrations"].get(registration_id)
    if not anchor:
        raise ValueError("missing owner registration anchor")
    path = _plain(ROOT / anchor["path"], ROOT / "validation/distill")
    frozen = read_registration(path, expected_digest=registration_id)
    _chronology(anchor, frozen, candidate)
    from .records import Concept, FailureRecord
    concept = Stores().for_model(Concept).get(candidate.concept_id)
    if frozen.study.get("family") != candidate.contract.family or concept.family != candidate.contract.family:
        raise ValueError("registered family differs from method/concept scope")
    training = set(candidate.training_case_ids)
    training.update(Stores().for_model(FailureRecord).get(ident).case_ref for ident in concept.from_failures)
    tuning = {ref.case_id for ref in frozen.cases if ref.split == "tuning"}
    if training - tuning:
        raise ValueError("all concept and candidate training cases must be registered tuning cases")
    if frozen.study.get("acceptance_policy_sha256") != _policy_digest():
        raise ValueError("registration acceptance policy digest changed")
    if frozen.study.get("acceptance_code_sha256") != _judging_digest():
        raise ValueError("registration acceptance judging code digest changed")
    custody = state_path("distill", "held_out")
    workspace = state_path("distill", "evaluation-work")
    workspace.mkdir(parents=True, exist_ok=True)
    controls = tuple(frozen.study["acceptance_controls"])
    if len(controls) != 2 or controls[0] == controls[1]:
        raise ValueError("registered planted controls required")
    wanted = {c.case_ref for e in library.list() if e.authority in {"A1", "A2"} for c in e.regression_suite}
    groups = {"acceptance": [], "monitor": [], "regression": []}
    for ref in frozen.cases:
        if ref.split not in {"acceptance", "tuning", "monitor"} and ref.case_id not in wanted:
            continue
        raw = strict_json(_plain(custody / "cases" / f"{ref.case_id}.json", custody).read_bytes())
        case = CaseRecord.model_validate(raw)
        if case.case_id != ref.case_id or case.unit_id != ref.unit_id:
            raise ValueError("registered case digest changed")
        result = oracle.label(case, policy=frozen.policy, workspace=workspace)
        target = "acceptance" if ref.split == "acceptance" else "monitor"
        if result.label != "excluded":
            groups[target].append(Observation(case, result.label, ref.split))
        if ref.case_id in wanted:
            groups["regression"].append(Observation(case, result.label, "regression"))
        if ref.split in {"acceptance", "monitor"}:
            reference = sealed_gate.SealedRef(sha256=frozen.operator_digests["held_out"])
            sealed = custody / "objects" / f"{reference.sha256}.json"
            for generated, labelled in sealed_gate.acceptance_observations(
                    sealed, reference, case, policy=frozen.policy, workspace=workspace,
                    registration=(path, registration_id)):
                if labelled.label != "excluded":
                    groups[target].append(Observation(generated, labelled.label, ref.split))
                if generated.case_id in wanted:
                    groups["regression"].append(Observation(generated, labelled.label, "regression"))
    observations = tuple(groups["acceptance"])
    found = {o.case.case_id for group in groups.values() for o in group}
    for ident in sorted(wanted - found):
        # Earlier admissions may use a different registered cut. Their frozen
        # bodies are kept by this evaluator, never exposed to the runner.
        archived = custody / "regression_cases" / f"{ident}.json"
        case = CaseRecord.model_validate(strict_json(_plain(archived, custody).read_bytes()))
        if case.case_id != ident:
            raise ValueError("archived regression digest changed")
        result = oracle.label(case, policy=frozen.policy, workspace=workspace)
        groups["regression"].append(Observation(case, result.label, "regression"))
    return EvaluationSet(registration_id, digest([o.case.case_id for o in observations]), observations,
                         tuple(groups["monitor"]), tuple(groups["regression"]), controls)


def _interval(k, n):
    """Two-sided Wilson 95% descriptive interval (not a second admission test).

    n=0 means unknown [0,1]; its point is a storage placeholder, never evidence.
    """
    if not n:
        return Interval(point=0, lo95=0, hi95=1, method="wilson")
    z = statistics.NormalDist().inv_cdf(.975)
    p, z2 = k / n, z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    radius = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return Interval(point=p, lo95=min(p, max(0, centre - radius)),
                    hi95=max(p, min(1, centre + radius)), method="wilson")


def _active(entries):
    return [e for e in entries if e.authority in {"A1", "A2"} and e.status in {"probation", "admitted", "demoted"}]


def _candidate(entry):
    return Stores().for_model(VerifierCandidate).get(entry.candidate_id)


def _run(candidate, case, entries, stack=frozenset()):
    if any(entry.candidate_id == candidate.candidate_id and entry.status == "held" for entry in entries):
        raise ValueError("held method cannot be applied")
    if candidate.candidate_id in stack:
        raise ValueError("cyclic method dependency")
    stack = stack | {candidate.candidate_id}
    contract = candidate.contract
    if contract.applies_when or contract.probes:
        # Feature predicate aggregation/probe receipts have no registered semantics
        # in wave 1. Do not guess or treat a missing probe as a clean verdict.
        return "abstain"
    if candidate.form == "predicate":
        # Admission cannot inherit an owner's permission for unconfined exploratory
        # scripts. This setting only tightens execution and is restored afterwards.
        if os.environ.get("G6_REQUIRE_ISOLATION") != "1":
            raise ValueError("acceptance predicates require G6_REQUIRE_ISOLATION=1")
        facts = _extract_facts({"tests/test_case.py": case.before_tests}, {"tests/test_case.py": case.after_tests})
        outcomes = run_batch(candidate.body, [facts], timeout_s=contract.cost_bound.wall_s)
        if len(outcomes) != 1 or not outcomes[0].ok:
            raise ValueError("confined method run unavailable")
        value = outcomes[0].value
        if not isinstance(value, dict) or set(value) - {"verdict", "reason"}:
            raise ValueError("invalid method verdict")
        verdict = value.get("verdict")
    elif candidate.form == "template_instance":
        instance = candidate.body
        if not isinstance(instance, TemplateInstance) or instance.bindings:
            raise ValueError("unregistered template bindings")
        seed = SEED_TEMPLATES[instance.template]
        if not seed.supported:
            return "abstain"
        report = weakening.detect_test_weakening({"tests/test_case.py": case.before_tests},
                                                {"tests/test_case.py": case.after_tests})
        reasons = [reason for reason in report.reasons if not seed.reason_fragments
                   or any(fragment in reason for fragment in seed.reason_fragments)]
        verdict = "concern" if reasons else "no_concern" if report.checked else "abstain"
    else:
        body = candidate.body
        refs = [body] if candidate.form == "reuse" else body.entries
        children = []
        for ref in refs:
            historical = Stores().for_model(LibraryEntry).get(ref)
            current = next((e for e in _active(entries) if e.candidate_id == historical.candidate_id), None)
            if current is None:
                raise ValueError("method dependency has lost authority")
            children.append(_run(_candidate(current), case, entries, stack))
        if candidate.form == "reuse":
            verdict = children[0]
        elif body.op == "sequence":
            verdict = next((v for v in children if v != "abstain"), "abstain")
        elif body.op in {"any", "k_of_n"}:
            k = 1 if body.op == "any" else body.k
            verdict = "concern" if children.count("concern") >= k else (
                "no_concern" if children.count("concern") + children.count("abstain") < k else "abstain")
        else:
            verdict = "no_concern" if "no_concern" in children else "concern" if all(
                v == "concern" for v in children) else "abstain"
    if verdict not in contract.verdict_kinds:
        raise ValueError("method verdict outside contract")
    return verdict


def _measure(candidate, observations, entries):
    verdicts, costs, over = [], [], 0
    for observation in observations:
        repeats = []
        for _ in range(3):
            if stop.engaged() is not None:
                raise ValueError("stop engaged during evaluation")
            start = time.monotonic()
            repeats.append(_run(candidate, observation.case, entries))
            cost = max(0, time.monotonic() - start)
            costs.append(cost)
            over += cost > candidate.contract.cost_bound.wall_s
        if len(set(repeats)) != 1:
            raise ValueError("flaky verdict across three runs")
        verdicts.append(repeats[0])
    if costs and over / len(costs) > .1:
        raise ValueError("cost bound exceeded on more than 10% of runs")
    return verdicts, costs


def _union(verdicts):
    return "concern" if "concern" in verdicts else "abstain" if "abstain" in verdicts else "no_concern"


def _library_verdicts(entries, observations):
    methods = [_measure(_candidate(e), observations, entries)[0] for e in _active(entries)]
    return [_union([v[i] for v in methods]) for i in range(len(observations))]


def _leaks(candidate, observations):
    body = canonical(candidate.model_dump(mode="json"))
    for observation in observations:
        case = observation.case
        names = {case.target, *case.files.keys()}
        for text in (case.before_tests, case.after_tests):
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 8:
                    names.add(node.value)
        if any(len(name) >= 3 and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', body)
               for name in names):
            return True
    return False


def _regress(entries, observations, added=None, *, targets=None):
    # Replay every frozen case, including negative and abstain verdicts. Unknown
    # or missing bodies refuse; a new method cannot silently replace a verdict.
    cases = {o.case.case_id: o for o in observations}
    for entry in _active(entries if targets is None else targets):
        for expected in entry.regression_suite:
            observation = cases.get(expected.case_ref)
            if observation is None or observation.label == "excluded":
                raise ValueError("regression case unavailable")
            own = _measure(_candidate(entry), (observation,), entries)[0][0]
            if own != expected.verdict:
                raise ValueError("entry regression no longer reproduces")
            before = _library_verdicts(entries, (observation,))[0]
            after = _union([before, _measure(added, (observation,), entries)[0][0]]) if added else before
            if after != before:
                raise ValueError("whole-library regression verdict changed")


def _counts(observations, verdicts):
    groups = {}
    for o, verdict in zip(observations, verdicts):
        groups.setdefault(o.case.unit_id, []).append((o.label, verdict))
    n_pos = n_neg = tp = fp = tn = abstain = 0
    for rows in groups.values():
        positive = [v for label, v in rows if label == "positive"]
        negative = [v for label, v in rows if label == "negative"]
        n_pos += bool(positive)
        n_neg += bool(negative)
        false_alarm = "concern" in negative
        fp += false_alarm
        # A unit with any false alarm is one refuted fire, even if it also
        # detected a positive. Never let many mutations inflate its precision.
        tp += bool(positive) and all(v == "concern" for v in positive) and not false_alarm
        tn += bool(negative) and all(v == "no_concern" for v in negative)
        abstain += any(v == "abstain" for _, v in rows)
    return dict(n_units=len(groups), n_pos=n_pos, n_neg=n_neg, tp=tp, fp=fp, tn=tn, fn=n_pos - tp, abstain=abstain)


def _negative_rates(observations, verdicts):
    groups = {}
    for o, v in zip(observations, verdicts):
        if o.label == "negative":
            groups.setdefault(o.case.unit_id, []).append(v == "concern")
    return [float(any(rows)) for rows in groups.values()]


def _unit_pairs(observations, base, candidate):
    grouped = {}
    for o, b, c in zip(observations, base, candidate):
        expected = "concern" if o.label == "positive" else "no_concern"
        grouped.setdefault(o.case.unit_id, []).append((b == expected, c == expected))
    # Strict success for the entire unit prevents pseudo-replication by operator.
    return [all(b for b, _ in rows) for rows in grouped.values()], [
        all(c for _, c in rows) for rows in grouped.values()]


def _meta(observations, base, combined, reuse_index, split_digest, candidate_id):
    policy, _ = _policy()
    b, c = _unit_pairs(observations, base, combined)
    suite = Suite((), tuple(SuiteCase(str(i), str(i), True) for i in range(len(b))),
                  {"dev": "independent", "held_out": "independent"})
    workflow = Workflow("distill-acceptance", "measure registered methods", (), _equal, suite, {},
                        objectives=(), policy=policy, steps=())
    baseline = Profile()
    proposed = Profile(prompt=candidate_id)
    # The two trivial controls must lose to the measured combined detector.
    zeros = ["no_concern"] * len(observations)
    ones = ["concern"] * len(observations)
    z, _ = _unit_pairs(observations, zeros, combined)
    a, _ = _unit_pairs(observations, ones, combined)
    controls = [_sign(z, c), _sign(a, c)]
    if any(x["wins"] <= x["losses"] or x["p_one_sided"] > policy.alpha for x in controls):
        raise ValueError("trivial controls do not fail the acceptance bar")
    report = EvaluationReport(workflow.digest(), "held_out", suite.digest("held_out"), baseline.digest(),
        proposed.digest(), len(b), [], {"model_calls": _paired([0.] * len(b), [0.] * len(b))},
        _sign(b, c), {"base": 0, "candidate": 0}, controls[0], None,
        {"units": len(b), "split_sha256": split_digest}, False, "", 0, policy.max_eval_model_calls,
        reuse_index, "independent")
    confirmation = {"workflow_digest": workflow.digest(), "judging_sha256": _policy_digest(),
                    "held_out_digest": suite.digest("held_out")}
    decision = meta_gate.decide(report, Proposal(baseline.digest(), proposed, "distill-independent-evaluator"),
                                workflow, confirmed=confirmation, judging_sha256=_policy_digest(), base=baseline)
    return decision, report


def _equal(output, expected):
    return output == expected


def _assess(candidate, entries, frozen, reuse_index):
    observations = frozen.acceptance
    if not observations or any(o.label not in {"positive", "negative"} for o in observations):
        raise ValueError("settled acceptance cases required")
    if set(candidate.training_case_ids) & {o.case.case_id for o in observations}:
        raise ValueError("training cases cannot be held-out acceptance evidence")
    acceptance_units = {o.case.unit_id for o in observations}
    monitor_units = {o.case.unit_id for o in frozen.monitor}
    if any(not isinstance(u, str) or not u.strip() for u in acceptance_units | monitor_units):
        raise ValueError('acceptance evidence requires a bug unit identity')
    if acceptance_units & monitor_units:
        raise ValueError('bug unit overlap between acceptance and dev/monitor evidence')
    if _leaks(candidate, (*observations, *frozen.monitor, *frozen.regression)):
        raise ValueError("candidate leakage guard refused")
    own, costs = _measure(candidate, observations, entries)
    base = _library_verdicts(entries, observations)
    combined = [_union([b, c]) for b, c in zip(base, own)]
    counts, previous = _counts(observations, combined), _counts(observations, base)
    neg = [i for i, o in enumerate(observations) if o.label == "negative"]
    if not neg:
        raise ValueError("legitimate negative cases required")
    # Reuse the meta-harness paired bound, with the design's stricter +.02 bar.
    fpr = _paired(_negative_rates(observations, base), _negative_rates(observations, combined))
    if fpr["diff_upper_95"] > .02:
        raise ValueError("held-out library FPR margin exceeded")
    if counts["tp"] - 3 * counts["fp"] <= previous["tp"] - 3 * previous["fp"]:
        raise ValueError("no positive utility gain")
    planted = {o.case.case_id: (o.label, own[i]) for i, o in enumerate(observations)}
    if (planted.get(frozen.controls[0]) != ("positive", "concern")
            or planted.get(frozen.controls[1]) != ("negative", "no_concern")):
        raise ValueError("registered planted controls failed")
    decision, report = _meta(observations, base, combined, reuse_index, frozen.split_sha256, candidate.candidate_id)
    if decision.verdict != "recommend":
        raise ValueError("metaharness refused: " + decision.code)
    _regress(entries, (*observations, *frozen.monitor, *frozen.regression), candidate)
    if not frozen.monitor:
        raise ValueError("dev plus monitor regression evidence unavailable")
    monitor_base = _library_verdicts(entries, frozen.monitor)
    monitor_added, _ = _measure(candidate, frozen.monitor, entries)
    neg = [i for i, o in enumerate(frozen.monitor) if o.label == "negative"]
    if not neg:
        raise ValueError("dev plus monitor negatives unavailable")
    monitor_combined = [_union([b, c]) for b, c in zip(monitor_base, monitor_added)]
    margin = _paired(_negative_rates(frozen.monitor, monitor_base), _negative_rates(frozen.monitor, monitor_combined))
    if margin["diff_upper_95"] > .02:
        raise ValueError("dev plus monitor library FPR margin exceeded")
    # Entry precision describes the method itself, not the union's credited hits.
    measured = _counts(observations, own)
    cost = Cost(median_s=statistics.median(costs), p95_s=sorted(costs)[math.ceil(.95 * len(costs)) - 1])
    n = report.n
    smallest = next((w for w in range(1, n + 1) if _sign([False] * w, [True] * w)["p_one_sided"]
                     <= EvidencePolicy().threshold and w >= EvidencePolicy().min_discordant), n)
    fires = measured["tp"] + measured["fp"]
    precision_resolution = smallest / fires if fires >= smallest else None
    data = {"candidate_id": candidate.candidate_id, "registration": frozen.registration_sha256,
        "split": frozen.split_sha256, "policy": _policy_digest(), "reuse_index": reuse_index,
        "meta": report.to_dict(), "counts": measured, "utility_counts": counts, "fpr": fpr,
        "judging_sha256": _judging_digest(),
        "cost": cost.model_dump(mode="json"),
        "resolution": {"unit_count": n, "labelled_firing_units": fires,
            "minimum_detectable_gain": smallest / n,
            "minimum_detectable_precision_difference": precision_resolution,
            "scope": "optimistic paired all-wins/no-losses resolution at alpha/K; not 80% power; "
                     "precision comparison additionally requires common labelled fires"},
        "regression_fpr": margin, "verdicts": [digest([o.case.case_id, v]) for o, v in zip(observations, own)]}
    evidence = AcceptanceEvidence(acceptance_report_sha256=digest(data), policy_sha256=_policy_digest(),
        split_sha256=frozen.split_sha256, reuse_index=reuse_index, **measured,
        precision=_interval(measured["tp"], measured["tp"] + measured["fp"]),
        recall=_interval(measured["tp"], measured["n_pos"]),
        false_rejection=_interval(measured["fp"], measured["n_neg"]),
        cost=cost)
    answer = AcceptanceDecision("admit_probation", ("independent held-out gain; whole-library regression passed",),
        candidate.candidate_id, digest(data), p_value=report.success["p_one_sided"], minimum_detectable_gain=smallest / n,
        minimum_detectable_precision_difference=precision_resolution)
    return answer, evidence, own, data


def _journal():
    return ChainLedger(state_path("distill", "acceptance_gate.jsonl"))


def _append(journal, kind, body):
    ident = digest([kind, len(journal.entries()), body])
    journal.append(kind, ident, body)
    return ident


def _look_available(registration):
    journal = _journal()
    rows = journal.entries()
    pending = {r["id"] for r in rows if r["kind"] == "reserve"}
    pending -= {r["body"]["reservation"] for r in rows if r["kind"] == "decision" and r["body"].get("reservation")}
    if pending:
        raise ValueError("unfinished evaluation requires owner recovery")
    index = 1 + sum(r["kind"] == "reserve" and r["body"].get("registration") == registration for r in rows)
    if index > EvidencePolicy().max_evaluations:
        raise ValueError("held-out split spent; owner must freeze a fresh split")
    return journal, index


def _spend(registration, candidate):
    journal, index = _look_available(registration)
    reservation = _append(journal, "reserve", {"registration": registration, "candidate_id": candidate.candidate_id,
                                              "reuse_index": index})
    return index, reservation


def _record(answer, reservation=""):
    _append(_journal(), "decision", asdict(answer) | {"reservation": reservation, "gate_version": VERSION})
    return answer


def _save_report(data):
    ident = digest(data)
    path = state_path("distill", "acceptance_gate_reports", f"{ident}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical(data) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ValueError("acceptance report changed")
    else:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    return ident


def _snapshot(library, entry_id):
    if type(library) is not Library:
        raise ValueError("gate requires the persisted library view")
    entry = library.get(entry_id)
    if entry is None:
        raise ValueError("stale or missing library snapshot")
    return entry


def _persist(candidate, answer, evidence, own, frozen, data, library):
    if any(e.candidate_id == candidate.candidate_id and e.status in {"probation", "admitted", "held"}
           for e in library.list()):
        raise ValueError("candidate already active")
    contracts = Stores().for_model(MethodContract)
    try:
        existing = contracts.get(candidate.contract.content_id)
    except KeyError:
        contracts.put(candidate.contract)
    else:
        if existing != candidate.contract:
            raise ValueError("stored method contract differs")
    previous = [e for e in Stores().for_model(LibraryEntry).list() if e.candidate_id == candidate.candidate_id]
    for observation in frozen.acceptance:
        case = observation.case
        path = state_path("distill", "held_out", "regression_cases", f"{case.case_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = canonical(case.model_dump(mode="json")) + "\n"
        if path.exists():
            if _plain(path, state_path("distill", "held_out")).read_text(encoding="utf-8") != text:
                raise ValueError("archived regression object changed")
        else:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
    now = datetime.now(timezone.utc).isoformat()
    certificate = {"gate": VERSION, "candidate": candidate.candidate_id, "report": _save_report(data),
                   "authority": "A1", "scope": "checked on the registered held-out cases; not proof"}
    entry = LibraryEntry(candidate_id=candidate.candidate_id, contract_sha256=candidate.contract.content_id,
        concept_id=candidate.concept_id, family=candidate.contract.family,
        version=max((e.version for e in previous), default=0) + 1, parents=candidate.lineage,
        status="probation", authority="A1", evidence=evidence,
        regression_suite=[RegressionCase(case_ref=o.case.case_id, verdict=v) for o, v in zip(frozen.acceptance, own)],
        field=FieldEvidence(fired=0, confirmed=0, refuted=0, unlabelled=0, precision=None),
        decided_by=DecidedBy(gate_version=VERSION, person=None), certificate_sha256=_save_report(certificate),
        created_at=now, changed_at=now, reason="; ".join(answer.reasons))
    Stores().for_model(LibraryEntry).put(entry)
    return replace(answer, entry_id=entry.entry_id)


def decide(candidate: VerifierCandidate, library: Library, registration: str) -> AcceptanceDecision:
    """A singleton is a predeclared batch of one; no caller-supplied evidence accepted."""
    return decide_batch((candidate,), library, registration)[0]


def decide_batch(candidates, library: Library, registration: str) -> tuple[AcceptanceDecision, ...]:
    """Seal the complete tick batch before measuring; BH q=.05 in addition to alpha/K.

    A runner must call this once per tick, rather than adaptively submitting a
    series of singleton batches. Every attempt consumes one registered look.
    """
    return _decide_batch(candidates, library, registration)


def restore(entry_id: str, library: Library, registration: str) -> AcceptanceDecision:
    """Person-authorized restore path; still requires fresh independent admission."""
    entry = _snapshot(library, entry_id)
    return _decide_batch((_candidate(entry),), library, registration, restoring=entry_id)[0]


def lift_hold(entry_id: str, library: Library, *, reason: str, person: str) -> AcceptanceDecision:
    """Release a suspension only after the existing wire dialog accepts it."""
    from .rectify_gate import Rectifier
    try:
        with _writer():
            if stop.engaged() is not None:
                raise ValueError("stop engaged")
            if not person.strip() or is_automated(person) or not reason.strip():
                raise ValueError("named person and reason required; wire consent dialog authenticates the request")
            Rectifier().reconcile()
            entry = _snapshot(library, entry_id)
            if entry.status != "held":
                raise ValueError("hold release requires a current held method")
            history = Stores().for_model(LibraryEntry).list()
            if not any(row.candidate_id == entry.candidate_id and row.status == "admitted"
                       and row.certificate_sha256 == entry.certificate_sha256 for row in history):
                raise ValueError("prior admission unavailable")
            changed = _change(entry, status="admitted", authority="A2", reason=reason, person=person)
            return _record(AcceptanceDecision("lift_hold", (reason,), entry.candidate_id, entry_id=changed.entry_id))
    except (ValueError, KeyError) as error:
        return AcceptanceDecision("refuse", (str(error) if isinstance(error, ValueError) else "hold evidence unavailable",))


def _execution_contract(candidate):
    contract = candidate.contract.model_dump(mode='json', exclude={
        'assumptions', 'escape_family', 'withdrawal_conditions'})
    for name in ('languages', 'frameworks', 'inputs', 'verdict_kinds'):
        contract[name] = sorted(set(contract[name]))
    return contract


class _IdentityAST(ast.NodeTransformer):
    """Bounded static rewrites for the JSON predicate calling convention.

    This does not execute predicates or claim general Python equivalence. Keep
    duplicate keys, effectful values and non-identity decorators in the identity.
    """

    @staticmethod
    def _pure(node):
        return isinstance(node, (ast.Constant, ast.Name)) or (
            isinstance(node, ast.IfExp) and all(_IdentityAST._pure(part)
                for part in (node.test, node.body, node.orelse)))

    def visit_BinOp(self, node):
        node = self.generic_visit(node)
        if not isinstance(node.left, ast.Constant) or not isinstance(node.right, ast.Constant):
            return node
        left, right = node.left.value, node.right.value
        # Do not evaluate arbitrary AST or allocate from unbounded repetition.
        numeric = type(left) in (int, float, bool) and type(right) in (int, float, bool)
        try:
            if isinstance(node.op, ast.Add) and (numeric or type(left) is type(right) is str):
                value = left + right
            elif isinstance(node.op, ast.Sub) and numeric:
                value = left - right
            elif isinstance(node.op, ast.Mult) and numeric:
                value = left * right
            else:
                return node
            if ((isinstance(value, str) and len(value) > 8192)
                    or (type(value) is int and value.bit_length() > 4096)
                    or (type(value) is float and not math.isfinite(value))):
                return node
            return ast.copy_location(ast.Constant(value=value), node)
        except (ArithmeticError, ValueError):
            return node

    def visit_UnaryOp(self, node):
        node = self.generic_visit(node)
        if not isinstance(node.operand, ast.Constant):
            return node
        value = node.operand.value
        if isinstance(node.op, ast.Not):
            return ast.copy_location(ast.Constant(value=not value), node)
        if type(value) in (int, float, bool):
            if isinstance(node.op, ast.USub):
                return ast.copy_location(ast.Constant(value=-value), node)
            if isinstance(node.op, ast.UAdd):
                return ast.copy_location(ast.Constant(value=+value), node)
        return node

    def visit_Dict(self, node):
        node = self.generic_visit(node)
        keys = [key.value for key in node.keys if isinstance(key, ast.Constant) and type(key.value) is str]
        if (len(keys) == len(node.keys) and len(set(keys)) == len(keys)
                and all(self._pure(value) for value in node.values)):
            pairs = sorted(zip(node.keys, node.values), key=lambda pair: pair[0].value)
            node.keys = [key for key, _ in pairs]
            node.values = [value for _, value in pairs]
        return node

    def visit_FunctionDef(self, node):
        node = self.generic_visit(node)
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body.pop(0)
        if node.name == 'solve':
            # The predicate runner always supplies its one positional argument.
            if all(isinstance(value, ast.Constant) for value in node.args.defaults):
                node.args.defaults = []
            used = {part.id for statement in node.body for part in ast.walk(statement)
                    if isinstance(part, ast.Name)}
            kept = [(arg, value) for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
                    if arg.arg in used or not isinstance(value, ast.Constant)]
            node.args.kwonlyargs = [arg for arg, _ in kept]
            node.args.kw_defaults = [value for _, value in kept]
        return node


def _predicate_identity(source):
    tree = _IdentityAST().visit(ast.parse(source))
    # Erase only a top-level, undecorated, single-return identity helper whose
    # name has no uses other than the decorators removed here.
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    helpers = {}
    for node in definitions:
        args = node.args
        if (node.name != 'solve' and len(args.args) == 1 and not args.posonlyargs
                and not args.defaults and not args.kwonlyargs and not args.vararg and not args.kwarg
                and not node.decorator_list and not node.returns and not args.args[0].annotation
                and len(node.body) == 1 and isinstance(node.body[0], ast.Return)
                and isinstance(node.body[0].value, ast.Name)
                and node.body[0].value.id == args.args[0].arg
                and sum(other.name == node.name for other in definitions) == 1):
            uses = [part for part in ast.walk(tree) if isinstance(part, ast.Name) and part.id == node.name]
            decorators = [part for fn in definitions for part in fn.decorator_list
                          if isinstance(part, ast.Name) and part.id == node.name]
            if uses and len(uses) == len(decorators):
                helpers[node.name] = node
    for node in definitions:
        node.decorator_list = [part for part in node.decorator_list
                               if not (isinstance(part, ast.Name) and part.id in helpers)]
    tree.body = [node for node in tree.body if node not in helpers.values()]
    return ast.dump(tree, include_attributes=False)


def _method_identity(candidate, stack=()):
    """Typed executable identity, independent of proposal/observation metadata.

    AST locations and comments do not run. Template names resolve to the fixed
    detector's bindings; aliases of the same oracle have the same identity.
    Parent entry names likewise resolve to their executable content.
    """
    if candidate.candidate_id in stack or len(stack) >= 16:
        raise ValueError('inactive method identity has a cyclic or over-deep dependency')
    stack = (*stack, candidate.candidate_id)
    contract = _execution_contract(candidate)
    if candidate.form == 'predicate':
        body = {'ast': _predicate_identity(candidate.body)}
    elif candidate.form == 'template_instance':
        seed = SEED_TEMPLATES[candidate.body.template]
        body = {'oracle': 'weakening.detect_test_weakening', 'supported': seed.supported,
                'reason_fragments': seed.reason_fragments, 'bindings': candidate.body.bindings}
    else:
        refs = [candidate.body] if candidate.form == 'reuse' else candidate.body.entries
        children = []
        for ref in refs:
            entry = Stores().for_model(LibraryEntry).get(ref)
            child = _candidate(entry)
            child_identity = _method_identity(child, stack)
            if len(refs) == 1 and contract == _execution_contract(child):
                return child_identity
            children.append(child_identity)
        body = {'children': children}
        if candidate.form == 'composition':
            body.update(op=candidate.body.op, k=candidate.body.k)
    return digest({'form': candidate.form, 'body': body, 'execution_contract': contract})


def _inactive_match(candidate, entries, status):
    identity = None
    for entry in entries:
        if entry.status != status:
            continue
        if entry.candidate_id == candidate.candidate_id:
            return True
        try:
            if identity is None:
                identity = _method_identity(candidate)
            if identity == _method_identity(_candidate(entry)):
                return True
        except (AttributeError, KeyError, StopIteration):
            # Missing historical code never permits an inactive method to return.
            raise ValueError(f'{status} executable identity unavailable') from None
    return False


def _withdrawn_match(candidate):
    return _inactive_match(candidate, Stores().for_model(LibraryEntry).list(), 'withdrawn')


def _decide_batch(candidates, library, registration, *, restoring=''):
    originals = tuple(candidates)
    try:
        if any(type(c) is not VerifierCandidate for c in originals):
            raise ValueError("typed candidates required")
        candidates = tuple(VerifierCandidate.model_validate(c.model_dump(mode="json")) for c in originals)
    except (ValueError, TypeError):
        return tuple(AcceptanceDecision("block", ("invalid candidate identity or contract",)) for _ in originals)
    if not candidates or len({c.candidate_id for c in candidates}) != len(candidates):
        raise ValueError("nonempty batch of distinct candidates required")
    try:
        with _writer():
            if stop.engaged() is not None:
                return tuple(_record(AcceptanceDecision("block", ("stop engaged",), c.candidate_id)) for c in candidates)
            _policy()
            if type(library) is not Library:
                raise ValueError("gate requires persisted library")
            if restoring:
                entry = _snapshot(library, restoring)
                if (len(candidates) != 1 or entry.candidate_id != candidates[0].candidate_id
                        or entry.status not in {'withdrawn', 'demoted'}):
                    raise ValueError('restore requires a current withdrawn or demoted entry')
            results, evaluated = {}, {}
            # No evidence-bearing callback, configurable runner or external report.
            for candidate in candidates:
                reservation = ""
                try:
                    if candidate.form == 'reuse':
                        raise ValueError('reuse is a reuse row, never an admission candidate')
                    if _inactive_match(candidate, library.list(), 'held'):
                        raise ValueError('held executable method requires a hold release')
                    if not restoring and _withdrawn_match(candidate):
                        raise ValueError('withdrawn executable method requires a person-authorized restore')
                    _look_available(registration)  # A spent split never reaches the oracle again.
                    frozen = _load(registration, candidate, library)
                    if Stores().for_model(VerifierCandidate).get(candidate.candidate_id) != candidate:
                        raise ValueError("candidate differs from persisted proposal")
                    index, reservation = _spend(registration, candidate)
                    answer, evidence, own, data = _assess(candidate, library.list(), frozen, index)
                    evaluated[candidate.candidate_id] = (answer, evidence, own, frozen, data)
                except (ValueError, KeyError, FileNotFoundError) as error:
                    # Do not echo corpus bodies, subprocess stderr or model output.
                    reason = str(error) if isinstance(error, ValueError) else "registered input missing"
                    verdict = "refuse" if reservation or "split spent" in reason else "block"
                    answer = AcceptanceDecision(verdict, (reason,), candidate.candidate_id)
                results[candidate.candidate_id] = (answer, reservation)
                # Close the reservation before spending the next look. A later
                # activation is a separate append-only decision row.
                _record(replace(answer, verdict="evaluated") if answer.verdict == "admit_probation" else answer, reservation)
            accepted = _bh(((v[0].p_value, ident) for ident, v in evaluated.items()), len(candidates))
            for candidate in sorted(candidates, key=lambda c: c.candidate_id):
                ident = candidate.candidate_id
                if ident not in evaluated:
                    continue
                answer, evidence, own, frozen, data = evaluated[ident]
                try:
                    if ident not in accepted:
                        raise ValueError("batch Benjamini-Hochberg bar not met")
                    # Earlier members of this batch may already have changed L_t.
                    answer, evidence, own, data = _assess(candidate, library.list(), frozen, evidence.reuse_index)
                    if answer.p_value > .05 * len(accepted) / len(candidates):
                        raise ValueError("batch bar not met after library changed")
                    answer = _persist(candidate, answer, evidence, own, frozen, data, library)
                except ValueError as error:
                    answer = replace(answer, verdict="refuse", reasons=(str(error),))
                results[ident] = (answer, "")
                _record(answer)
            return tuple(results[c.candidate_id][0] for c in candidates)
    except (ValueError, FileNotFoundError) as error:
        reason = str(error) if isinstance(error, ValueError) else "evaluator input unavailable"
        return tuple(AcceptanceDecision("block", (reason,), c.candidate_id) for c in candidates)


def _field(candidate_id):
    """Read independent labelled fires from unmounted evaluator custody only.

    The wire/dialog slice owns writing these observations. Each fire is unique,
    labels require a receipt; unknown sources and prediction fields are refused.
    No caller can pass counters or a purported precision interval to promotion.
    """
    path = state_path("distill", "held_out", "field", f"{candidate_id}.jsonl")
    if not path.exists():
        return 0, 0, 0
    root = state_path("distill", "held_out")
    _plain(path, root)
    rows = ChainLedger(path).entries()
    return _field_counts(rows, candidate_id)


def _field_counts(rows, candidate_id):
    counts = {"confirmed": 0, "refuted": 0, "unlabelled": 0}
    labels, units = {}, {}
    for row in rows:
        body = FieldOutcome.model_validate(row["body"])
        if row["kind"] != "field" or body.candidate_id != candidate_id:
            raise ValueError("independent field receipt required; prediction evidence refused")
        if body.unit_id is None:
            raise ValueError('field evidence requires a bug unit identity')
        if units.setdefault(body.fire_id, body.unit_id) != body.unit_id:
            raise ValueError('field fire cannot change bug unit identity')
        old = labels.get(body.fire_id)
        if old is not None and (old != "unlabelled" or body.label == "unlabelled"):
            raise ValueError("field fire already labelled; duplicate or relabel refused")
        labels[body.fire_id] = body.label
    grouped = {}
    for fire, label in labels.items():
        grouped.setdefault(units[fire], []).append(label)
    for values in grouped.values():
        # A refutation dominates. Incomplete labels cannot inflate precision.
        label = 'refuted' if 'refuted' in values else 'unlabelled' if 'unlabelled' in values else 'confirmed'
        counts[label] += 1
    return counts["confirmed"], counts["refuted"], counts["unlabelled"]


def _field_evidence(candidate_id):
    confirmed, refuted, unlabelled = _field(candidate_id)
    return FieldEvidence(fired=confirmed + refuted + unlabelled, confirmed=confirmed, refuted=refuted,
        unlabelled=unlabelled, precision=_interval(confirmed, confirmed + refuted) if confirmed + refuted else None)


def _a2_bar(evidence, field):
    if ((evidence.n_units or 0) < 30 or evidence.n_neg < 10
            or _interval(evidence.tp, evidence.tp + evidence.fp).lo95 < .90
            or _interval(evidence.fp, evidence.n_neg).hi95 > .05):
        raise ValueError("A2 held-out precision or false-rejection bar not met")
    if (field.confirmed + field.refuted < 20
            or _interval(field.confirmed, field.confirmed + field.refuted).lo95 < .85):
        raise ValueError("A2 needs at least 20 independently labelled field fires with lower precision >= .85")


def _demotion(confirmed, refuted, policy):
    n = confirmed + refuted
    return n > 0 and upper_bound(confirmed, n, policy.demote_alpha) <= .85


def _health_bar(candidate, costs, conditions):
    if any(c not in candidate.contract.withdrawal_conditions for c in conditions):
        raise ValueError("withdrawal condition was not pre-stated")
    if conditions:
        return "pre-stated withdrawal condition observed"
    if costs and sum(c > candidate.contract.cost_bound.wall_s for c in costs) / len(costs) > .1:
        return "weekly cost bound exceeded"
    return None


def _health(candidate):
    path = state_path("distill", "held_out", "health", f"{candidate.candidate_id}.jsonl")
    if not path.exists():
        return None
    _plain(path, state_path("distill", "held_out"))
    now, costs, conditions, seen = datetime.now(timezone.utc), [], [], set()
    for row in ChainLedger(path).entries():
        value = HealthObservation.model_validate(row["body"])
        if (row["kind"] != "health" or value.candidate_id != candidate.candidate_id
                or value.observation_id in seen or value.observed_at.tzinfo is None
                or value.observed_at > now or (value.wall_s is None and value.withdrawal_condition is None)):
            raise ValueError("invalid independent health observation")
        seen.add(value.observation_id)
        if value.withdrawal_condition is not None:
            conditions.append(value.withdrawal_condition)
        if value.wall_s is not None and value.observed_at >= now - timedelta(days=7):
            costs.append(value.wall_s)
    return _health_bar(candidate, costs, conditions)


def recheck(entry_id: str, library: Library, registration: str) -> AcceptanceDecision:
    """Withdraw on reproduced regression drift, weekly overruns or observed defeaters."""
    try:
        with _writer():
            _policy()
            entry = _snapshot(library, entry_id)
            if entry not in _active(library.list()):
                raise ValueError("recheck requires an active entry")
            candidate = _candidate(entry)
            reason = _health(candidate)
            if reason is None:
                if stop.engaged() is not None:
                    return _record(AcceptanceDecision("block", ("stop engaged",), entry.candidate_id))
                frozen = _load(registration, candidate, library)
                try:
                    _regress(library.list(), (*frozen.acceptance, *frozen.monitor, *frozen.regression), targets=[entry])
                except ValueError as error:
                    if str(error) != "entry regression no longer reproduces":
                        raise
                    reason = "own regression suite no longer reproduces"
            if reason is None:
                return _record(AcceptanceDecision("retain", ("regression and health checks held",), entry.candidate_id))
            changed = _change(entry, status="withdrawn", authority="A0", reason=reason)
            _cascade(changed, library)
            return _record(AcceptanceDecision("withdraw", (reason,), entry.candidate_id, entry_id=changed.entry_id))
    except (ValueError, KeyError, FileNotFoundError) as error:
        return AcceptanceDecision("block", (str(error) if isinstance(error, ValueError) else "recheck evidence unavailable",))


def ablate(entry_id: str, library: Library, registration: str) -> AcceptanceDecision:
    """Archive an entry only if its removal costs nothing on the family's frozen suites."""
    try:
        with _writer():
            if stop.engaged() is not None:
                return _record(AcceptanceDecision("block", ("stop engaged",)))
            entry = _snapshot(library, entry_id)
            entries = _active(library.list())
            if entry not in entries:
                raise ValueError("ablation requires an active entry")
            frozen = _load(registration, _candidate(entry), library)
            observations = {o.case.case_id: o for o in (*frozen.acceptance, *frozen.monitor, *frozen.regression)}
            family = [e for e in entries if e.family == entry.family]
            ids = {c.case_ref for e in family for c in e.regression_suite}
            if not ids or ids - observations.keys():
                raise ValueError("family ablation suite unavailable")
            suite = tuple(observations[i] for i in sorted(ids))
            rest = [e for e in entries if e.candidate_id != entry.candidate_id]
            # A composition that depends on the target is not an independent
            # replacement. _run refuses that dependency when target is removed.
            if _library_verdicts(entries, suite) != _library_verdicts(rest, suite):
                raise ValueError("removal loses a frozen family verdict")
            changed = _change(entry, status="superseded", authority="A0", reason="removal costs no frozen family verdict")
            _cascade(changed, library)
            return _record(AcceptanceDecision("supersede", (changed.reason,), entry.candidate_id, entry_id=changed.entry_id))
    except (ValueError, KeyError) as error:
        return AcceptanceDecision("refuse", (str(error) if isinstance(error, ValueError) else "ablation evidence unavailable",))


def _change(entry, *, status, authority, reason, person=None, field=None):
    data = entry.model_dump(mode="json", exclude={"entry_id"}) | {
        "version": entry.version + 1, "status": status, "authority": authority,
        "changed_at": datetime.now(timezone.utc).isoformat(), "reason": reason,
        "decided_by": {"gate_version": VERSION, "person": person}}
    if field is not None:
        data["field"] = field.model_dump(mode="json")
    result = LibraryEntry(**data)
    Stores().for_model(LibraryEntry).put(result)
    return result


def _cascade(changed, library):
    affected = {changed.candidate_id}
    # Parents can name historical snapshots: resolve them to candidate identity.
    while True:
        dependent = []
        for entry in _active(library.list()):
            if entry.candidate_id in affected:
                continue
            candidate = _candidate(entry)
            refs = set(entry.parents) | set(candidate.lineage)
            if candidate.form == "composition":
                refs.update(candidate.body.entries)
            elif candidate.form == "reuse":
                refs.add(candidate.body)
            if any(Stores().for_model(LibraryEntry).get(ref).candidate_id in affected for ref in refs):
                dependent.append(entry)
        if not dependent:
            break
        for entry in dependent:
            _change(entry, status="demoted", authority="A0", reason="dependency lost authority; fresh acceptance required")
            affected.add(entry.candidate_id)


def promote(entry_id: str, library: Library, registration: str) -> AcceptanceDecision:
    """A1 -> A2 only after held-out precision, independent field labels and regression."""
    try:
        with _writer():
            if stop.engaged() is not None:
                return _record(AcceptanceDecision("block", ("stop engaged",)))
            entry = _snapshot(library, entry_id)
            if entry.status != "probation" or entry.authority != "A1":
                raise ValueError("A2 requires current A1 probation")
            evidence = entry.evidence
            # Recompute rather than trust serialized intervals/caller constructions.
            field = _field_evidence(entry.candidate_id)
            _a2_bar(evidence, field)
            candidate = _candidate(entry)
            frozen = _load(registration, candidate, library)
            if frozen.split_sha256 != evidence.split_sha256 or evidence.policy_sha256 != _policy_digest():
                raise ValueError("A2 acceptance evidence changed")
            report_path = state_path("distill", "acceptance_gate_reports", f"{evidence.acceptance_report_sha256}.json")
            from g6.distill.held_out.registration_gate import strict_json
            report = strict_json(report_path.read_bytes())
            if (digest(report) != evidence.acceptance_report_sha256 or report["candidate_id"] != entry.candidate_id
                    or report["counts"] != {k: getattr(evidence, k) for k in report["counts"]}
                    or report["meta"]["n"] < EvidencePolicy().min_cases
                    or report["cost"] != evidence.cost.model_dump(mode="json")
                    or report["judging_sha256"] != _judging_digest()):
                raise ValueError("A2 independent acceptance report mismatch")
            _regress(library.list(), (*frozen.acceptance, *frozen.monitor, *frozen.regression))
            if not frozen.monitor:
                raise ValueError("A2 dev plus monitor regression unavailable")
            changed = _change(entry, status="admitted", authority="A2", field=field,
                              reason="held-out and labelled field precision bars met; regression passed again")
            return _record(AcceptanceDecision("admit", (changed.reason,), entry.candidate_id,
                                             evidence.acceptance_report_sha256, changed.entry_id))
    except (ValueError, KeyError, FileNotFoundError) as error:
        return AcceptanceDecision("refuse", (str(error) if isinstance(error, ValueError) else "A2 evidence unavailable",))


def monitor(entry_id: str, library: Library) -> AcceptanceDecision:
    """Demote at the frozen policy's exact one-sided binomial threshold."""
    try:
        with _writer():
            policy, _ = _policy()
            entry = _snapshot(library, entry_id)
            if entry.authority != "A2":
                raise ValueError("field demotion applies to A2")
            field = _field_evidence(entry.candidate_id)
            # Invert the existing exact binomial bound instead of implementing
            # another binomial CDF. p<=alpha iff upper(k,n,alpha)<=.85.
            adverse = _demotion(field.confirmed, field.refuted, policy)
            if not adverse:
                return _record(AcceptanceDecision("retain", ("field evidence does not meet demotion bar",), entry.candidate_id))
            changed = _change(entry, status="demoted", authority="A1", field=field,
                              reason="field precision below .85 at demote_alpha")
            _cascade(changed, library)
            return _record(AcceptanceDecision("demote", (changed.reason,), entry.candidate_id,
                entry_id=changed.entry_id, p_value_upper_bound=policy.demote_alpha))
    except (ValueError, KeyError) as error:
        return AcceptanceDecision("refuse", (str(error) if isinstance(error, ValueError) else "entry unavailable",))


def withdraw(entry_id: str, library: Library, *, reason: str, person: str) -> AcceptanceDecision:
    """Owner withdrawal; wire must obtain the person through its consent dialog."""
    return _lower(entry_id, library, action="withdraw", reason=reason, person=person)


def demote(entry_id: str, library: Library, *, reason: str, person: str) -> AcceptanceDecision:
    """An upheld dispute or owner request can lower authority immediately."""
    return _lower(entry_id, library, action="demote", reason=reason, person=person)


def _lower(entry_id, library, *, action, reason, person):
    try:
        with _writer():
            if not person.strip() or is_automated(person) or not reason.strip():
                raise ValueError("named person and reason required; wire consent dialog authenticates the request")
            entry = _snapshot(library, entry_id)
            if action == "demote" and entry.authority != "A2":
                raise ValueError("manual demotion requires A2")
            changed = _change(entry, status="withdrawn" if action == "withdraw" else "demoted",
                authority="A0" if action == "withdraw" else "A1", reason=reason, person=person)
            _cascade(changed, library)
            return _record(AcceptanceDecision(action, (reason,), entry.candidate_id, entry_id=changed.entry_id))
    except (ValueError, KeyError) as error:
        return AcceptanceDecision("refuse", (str(error) if isinstance(error, ValueError) else "entry unavailable",))
