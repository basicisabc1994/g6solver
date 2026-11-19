# SPDX-License-Identifier: Apache-2.0
"""Independent, owner-protected acceptance and authority decisions (design section 6).

Only this module consumes registered evaluator custody. Proposals contain methods,
never reports, labels, policies or predictions. All statistics used for admission
are adapted to the existing metaharness gate; no executor/model is called there.
Host custody and mission mounts must enforce the OS boundary: Python module
identity and hash chains do not protect against arbitrary same-user shell access.

R4 charges continuous monotonic time from opening to the final sample, plus
twice the registered d_append_s deadline (default five seconds). Timed journal
operations remain subject to that deadline and carry latency diagnostics.
Complete intervals retain their recorded total. Incomplete intervals are voided,
charging elapsed time through recovery's final sample plus the fixed allowance.
Clock discontinuity requires a client-dialog-approved floor covering wall time.

Authority requires a returned close, an authority confirmation and a durable
confirmation_observed marker binding the journal, chain, opening, close and
confirmation digest. Completed bindings survive restart without owner approval.
An incomplete interval never grants authority; reassessment uses a new interval
without another held-out look. Owner approval is issued only by the MCP client
elicitation adapter and consumed immediately for the exact recovery action. Clock
continuity compares wall and monotonic elapsed readings within max(2 s, 1%),
and Linux boot_id where available; disagreement requires owner approval too.

Marker preparation is deadline checked before publication. The marker write's
own latency is unobserved; no assessment work runs in it. Authority reads over
the deadline serve the teacher path without changing durable state. If setup
leaves no opening row, a process obligation withholds authority until recovery.
"""
from __future__ import annotations

import ast
import hashlib
import math
import os
import re
import statistics
import sys
import time
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from g6.core import stop
from g6.core.approval_authority import get_approval_authority
from g6.core.adaptation_gate import is_automated
from g6.core.binomial import upper_bound
from g6.core.ledger import ChainLedger, LedgerError, canonical, digest
from g6.core.storage import state_path
from g6.distillation.sandbox import run_batch
from g6.metaharness import gate as meta_gate
from g6.metaharness.evaluate import EvaluationReport, _paired, _sign
from g6.metaharness.schema import EvidencePolicy, Profile, Proposal, Suite, SuiteCase, Workflow
from g6.safety_engine import weakening

from .detectors import SEED_TEMPLATES
from .facts import _extract_facts
from .ledger import Stores
from .library import Library
from .records import (AcceptanceEvidence, Cost, DecidedBy, FieldEvidence,
                      FeaturePredicate, Interval, LibraryEntry, MethodContract, Model, RegressionCase, Sha256,
                      TemplateInstance, VerifierCandidate)

ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = ROOT / "validation/distill/held_out/policy.json"
VERSION = "distill-acceptance-v1"
_MUTEX = threading.RLock()
_BOOKKEEPING = threading.local()
_RETURNED_CONFIRMATIONS = {}
_BOOKKEEPING_DIAGNOSTICS = {}
_UNRECORDED_BOOKKEEPING = {}
_MEMORY_JOURNALS = {}


def _boot_identity():
    """Use Linux's boot identity as an additional clock continuity check."""
    try:
        if sys.platform.startswith("linux"):
            return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip() or None
    except (OSError, AttributeError):
        return None


def _journal_identity(journal, opening):
    path = getattr(journal, "path", None)
    if path is None:
        token = _MEMORY_JOURNALS.setdefault(journal, uuid4().hex)
        return ("memory", token, opening)
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    return ((str(path), stat.st_dev, stat.st_ino) if stat.st_ino
            else (str(path), _MEMORY_JOURNALS.setdefault(journal, uuid4().hex)))


def _confirmation_key(row):
    body = row["body"]
    return (tuple(row["journal_identity"]), row["interval_chain"], body.get("interval_id", body["attempt"]),
            body["attempt"], body["close"], digest(row))


def _returned_confirmation(row, rows=None):
    body = row["body"]
    rows = _interval_rows() if rows is None else rows
    raw = {key: value for key, value in row.items() if key not in {"journal_identity", "interval_chain"}}
    identity = list(row["journal_identity"])
    if body.get("journal_identity") != identity or body.get("interval_chain") != row["interval_chain"]:
        return False
    return any(marker["kind"] == "confirmation_observed" and marker["seq"] == row["seq"] + 1
        and marker["prev"] == row["entry_sha256"]
        and marker["interval_chain"] == row["interval_chain"]
        and list(marker["journal_identity"]) == identity
        and all(marker["body"].get(key) == value for key, value in {
            "journal_identity": identity, "interval_chain": row["interval_chain"],
            "attempt": body["attempt"], "close": body["close"], "confirmation": row["id"],
            "confirmation_digest": digest(raw), "registration": body["registration"]}.items())
        for marker in rows)


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
        lease = {"id": uuid4().hex, "retain": False}
        try:
            with stream:
                stream.write(str(os.getpid()) + "\n" + lease["id"] + "\n")
            yield lease
        finally:
            if not lease["retain"]:
                path.unlink()


@dataclass(frozen=True)
class Observation:
    case: object
    label: str
    split: str
    oracle_result: dict | None = None


@dataclass(frozen=True)
class ProbeCase:
    """Source case with evaluator-owned, registration-bound probe inputs."""
    source_case: object
    probe_input: object

    def __getattr__(self, name):
        return getattr(self.source_case, name)


@dataclass(frozen=True)
class EvaluationSet:
    registration_sha256: str
    split_sha256: str
    acceptance: tuple[Observation, ...]
    monitor: tuple[Observation, ...]
    regression: tuple[Observation, ...]
    controls: tuple[str, str]
    replacement: ReplacementStudy | None = None
    oracle_policy: object | None = None
    tuning: tuple[Observation, ...] = ()


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


class Inconclusive(ValueError):
    """Registered evidence cannot resolve the admission margin."""


Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class TeacherObservation(Model):
    case_id: Sha256
    repository: Annotated[str, Field(min_length=1, max_length=256)]
    teacher_verdict: Literal["concern", "no_concern", "abstain"]
    teacher_s: Nonnegative
    maintenance_delta_s: float


class ReplacementStudy(Model):
    """Owner-registered, evaluator-custodied traces; never a proposal argument.

    Fixed costs and maintenance are booked in registered weighted seconds.
    CPU and teacher durations use the registered resource prices. Fixed costs
    include acquisition and prior validation, including discarded attempts.
    The supported graph is the current library union with one teacher judge.
    """
    teacher: Sha256
    teacher_model: Literal["gpt-oss-20b"] = "gpt-oss-20b"
    candidate_id: Sha256
    registration_sha256: Sha256
    topology: Literal["library_any_teacher.v1"] = "library_any_teacher.v1"
    normalization: Literal["alpha_functions_inline_constants.v1"] = "alpha_functions_inline_constants.v1"
    horizon: Literal[1000] = 1000
    shadow_rate: Literal[0.05] = .05
    shadow_seed: Sha256
    cpu_weight: Annotated[float, Field(gt=0)] = 1.
    teacher_weight: Annotated[float, Field(gt=0)] = 1.
    fixed_new_s: Nonnegative
    fixed_base_s: Nonnegative
    rows: list[TeacherObservation]
    scope: list[FeaturePredicate] | None = None
    task_saving_bounds: tuple[float, float] | None = None
    activation_allowance_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None


class FieldOutcome(Model):
    candidate_id: Sha256
    fire_id: Annotated[str, Field(min_length=1, max_length=256)]
    source: Literal["person", "maintainer", "oracle"]
    label: Literal["confirmed", "refuted", "unlabelled"]
    receipt_sha256: Sha256


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
    """Pin reused detector, execution and statistics code as well as this gate."""
    paths = ("src/g6/distill/acceptance_gate.py", "src/g6/distill/records.py", "src/g6/distill/ledger.py",
             "src/g6/distill/library.py", "src/g6/distill/facts.py", "src/g6/distill/detectors.py",
             "src/g6/safety_engine/weakening.py", "src/g6/distillation/sandbox.py", "src/g6/core/source_check.py",
             "src/g6/core/sandbox.py", "src/g6/core/component_process.py", "src/g6/core/binomial.py",
             "src/g6/reasoning/compare.py", "src/g6/metaharness/gate.py", "src/g6/metaharness/schema.py",
             "src/g6/metaharness/evaluate.py", "src/g6/distill/probes.py")
    return digest({p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})


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


def _registered_replacement(frozen, registration_id, candidate, custody, *, required):
    from g6.distill.held_out.registration_gate import strict_json

    replacement = None
    if "replacement_protocol_sha256" in frozen.study:
        ident = frozen.study["replacement_protocol_sha256"]
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{64}", ident):
            raise ValueError("invalid registered replacement digest")
        data = strict_json(_plain(custody / "replacement" / f"{candidate.candidate_id}.json", custody).read_bytes())
        replacement = ReplacementStudy.model_validate(data)
        protocol = replacement.model_dump(mode="json", include={"teacher", "teacher_model", "topology",
            "normalization", "horizon", "shadow_rate", "shadow_seed", "scope", "cpu_weight", "teacher_weight",
            *({"task_saving_bounds"} if required or replacement.task_saving_bounds is not None else set()),
            *({"activation_allowance_s"} if replacement.activation_allowance_s is not None else set())})
        if digest(protocol) != ident or replacement.registration_sha256 != registration_id:
            raise ValueError("registered replacement protocol changed")
    if required:
        _replacement_scope(candidate, replacement)
    return replacement


def _load(registration_id, candidate, library, replacement_mode=False) -> EvaluationSet:
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
    replacement = _registered_replacement(frozen, registration_id, candidate, custody, required=True) if replacement_mode else None
    workspace = state_path("distill", "evaluation-work")
    workspace.mkdir(parents=True, exist_ok=True)
    controls = tuple(frozen.study["acceptance_controls"])
    if len(controls) != 2 or controls[0] == controls[1]:
        raise ValueError("registered planted controls required")
    wanted = {c.case_ref for e in library.list() if e.authority in {"A1", "A2"} for c in e.regression_suite}
    groups = {"acceptance": [], "monitor": [], "regression": [], "tuning": []}
    for ref in frozen.cases:
        if ref.split not in {"acceptance", "tuning", "monitor"} and ref.case_id not in wanted:
            continue
        raw = strict_json(_plain(custody / "cases" / f"{ref.case_id}.json", custody).read_bytes())
        case = CaseRecord.model_validate(raw)
        if case.case_id != ref.case_id:
            raise ValueError("registered case digest changed")
        result = oracle.label(case, policy=frozen.policy, workspace=workspace)
        target = "acceptance" if ref.split == "acceptance" else "monitor"
        if replacement_mode and ref.split not in {"acceptance", "monitor"}:
            target = "tuning" if ref.split == "tuning" else "regression"
        if result.label != "excluded":
            groups[target].append(Observation(case, result.label, ref.split, result.model_dump(mode="json")))
        if ref.case_id in wanted and target != "regression":
            groups["regression"].append(Observation(case, result.label, "regression", result.model_dump(mode="json")))
        if ref.split in {"acceptance", "monitor"}:
            reference = sealed_gate.SealedRef(sha256=frozen.operator_digests["held_out"])
            sealed = custody / "objects" / f"{reference.sha256}.json"
            for generated, labelled in sealed_gate.acceptance_observations(
                    sealed, reference, case, policy=frozen.policy, workspace=workspace,
                    registration=(path, registration_id)):
                if labelled.label != "excluded":
                    groups[target].append(Observation(generated, labelled.label, ref.split, labelled.model_dump(mode="json")))
                if generated.case_id in wanted:
                    groups["regression"].append(Observation(generated, labelled.label, "regression", labelled.model_dump(mode="json")))
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
        groups["regression"].append(Observation(case, result.label, "regression", result.model_dump(mode="json")))
    if "probe_inputs_sha256" in frozen.study:
        ident = frozen.study["probe_inputs_sha256"]
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{64}", ident):
            raise ValueError("invalid registered probe input digest")
        raw = strict_json(_plain(custody / "probe_inputs" / f"{ident}.json", custody).read_bytes())
        if not isinstance(raw, dict) or digest(raw) != ident:
            raise ValueError("registered probe inputs changed")
        from .probes import ProbeInput
        inputs = {key: ProbeInput.model_validate(value) for key, value in raw.items()}
        for name, group in groups.items():
            groups[name] = [replace(o, case=ProbeCase(o.case, inputs[o.case.case_id]))
                            if o.case.case_id in inputs else o for o in group]
        observations = tuple(groups["acceptance"])
    return EvaluationSet(registration_id, digest([o.case.case_id for o in observations]), observations,
                         tuple(groups["monitor"]), tuple(groups["regression"]), controls, replacement, frozen.policy,
                         tuple(groups["tuning"]))


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
    return [e for e in entries if e.authority in {"A1", "A2"}
            and e.status in {"probation", "admitted", "demoted"}
            and (e.replacement != "R" or _replacement_authorized(e))]


def _candidate(entry):
    return Stores().for_model(VerifierCandidate).get(entry.candidate_id)


def _candidate_boundary(candidate):
    body = canonical(candidate.model_dump(mode="json"))
    if any(token in body for token in ("held_out", "evaluator_only", "_evaluator_mutants")):
        raise ValueError("candidate uses evaluator-only operators")


def _in_scope(predicates, facts):
    for predicate in predicates:
        value, other = facts.get(predicate.feature), predicate.value
        if value is None:
            return False
        try:
            if predicate.op == "eq":
                matches = value == other
            elif predicate.op == "ne":
                matches = value != other
            elif predicate.op == "contains":
                matches = other in value
            elif type(value) not in (int, float) or type(other) not in (int, float):
                return False
            elif predicate.op == "gt":
                matches = value > other
            elif predicate.op == "ge":
                matches = value >= other
            elif predicate.op == "lt":
                matches = value < other
            else:
                matches = value <= other
        except (TypeError, ValueError):
            return False
        if not matches:
            return False
    return True


def _probe_verdict(candidate, case):
    from . import probes

    body, contract = candidate.body, candidate.contract
    if candidate.form != "template_instance" or body.template not in probes.PROBE_NAMES_V2:
        raise ValueError("registered v2 probe template required")
    if len(contract.probes) != 1 or contract.probes[0].name != body.template:
        raise ValueError("probe template differs from registered probe")
    inputs = {"files": case.files, "target": case.target, "changed_lines": case.changed_lines}
    if body.template == "discriminates_at_base.v2":
        bound = getattr(case, "probe_input", None)
        if bound is None:
            return "abstain"
        bound = probes.ProbeInput.model_validate(bound.model_dump(mode="json"))
        if (bound.files != case.files or bound.target != case.target or bound.changed_lines != case.changed_lines
                or bound.base_implementation not in case.known_wrong
                or bound.new_tests != {"tests/test_case.py": case.after_tests}):
            raise ValueError("registered base probe inputs differ from source case")
        inputs = bound.model_dump(mode="json")
    probe = contract.probes[0]
    workspace = state_path("distill", "evaluation-work")
    workspace.mkdir(parents=True, exist_ok=True)
    outcome = probes._run_probe(probes.ProbeTemplateInstance.model_validate(body.model_dump(mode="json")),
        {"tests/test_case.py": case.before_tests}, {"tests/test_case.py": case.after_tests}, split="acceptance",
        probe_input=inputs,
        workspace=workspace, cost_bound={"wall_s": min(probe.wall_s, contract.cost_bound.wall_s),
            "probe_runs": min(probe.runs, contract.cost_bound.probe_runs), "model_calls": 0})
    return outcome.verdict


def _normal_form(source, reserved=()):
    """A deliberately small normalisation with direct function calls only."""
    reserved = set(reserved) | set(re.findall(r"\b\w+\b", source))
    tree = ast.parse(source)
    names, constants, kept = {}, {}, []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.decorator_list:
            if node.name.startswith(("pytest_", "setup_", "teardown_")):
                raise ValueError("normalisation excludes test hooks")
            if node.name in names or node.args.defaults or node.args.kw_defaults:
                raise ValueError("normalisation excludes decorated or defaulted functions")
            prefix = "test_" if node.name.startswith("test_") else "helper_"
            index = len(names)
            while prefix + str(index) in reserved or prefix + str(index) in names.values():
                index += 1
            names[node.name] = prefix + str(index)
            kept.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id.isupper() and isinstance(node.value, ast.Constant)):
            if node.targets[0].id in constants:
                raise ValueError("reassigned constant")
            constants[node.targets[0].id] = node.value
        else:
            raise ValueError("unsupported normalisation form")
    tree.body = kept
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    normalised = constants.keys() | names.keys()
    for node, binding in _name_bindings(tree):
        if binding == "*" or binding in constants or (binding in normalised and not
                (isinstance(node, ast.FunctionDef) and node in kept)):
            raise ValueError("normalised name shadowed")
    allowed_nodes = {ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
        ast.Assert, ast.If, ast.Pass, ast.Expr, ast.Call, ast.Name, ast.Constant,
        ast.Load, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.BinOp,
        ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Subscript, ast.Slice,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
        ast.In, ast.NotIn, ast.And, ast.Or, ast.Not, ast.UAdd, ast.USub,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow}
    allowed_names = set(names) | set(constants) | {
        "answer", "abs", "all", "any", "bool", "float", "int", "len", "max", "min",
        "range", "round", "str", "sum", "tuple"}
    for node in ast.walk(tree):
        if type(node) not in allowed_nodes:
            raise ValueError("unsupported normalisation node")
        if isinstance(node, ast.arg) and (node.arg not in allowed_names or node.annotation is not None):
            raise ValueError("unsupported normalisation argument")
        if isinstance(node, ast.FunctionDef) and node not in kept:
            raise ValueError("nested function")
        if isinstance(node, ast.Name):
            if node.id not in allowed_names:
                raise ValueError("unsupported normalisation name")
            if node.id in names and not (isinstance(parents.get(node), ast.Call) and parents[node].func is node):
                raise ValueError("function name escapes direct call")

    class Normalise(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            node.name = names[node.name]
            return self.generic_visit(node)

        def visit_Name(self, node):
            if node.id in constants:
                return constants[node.id]
            node.id = names.get(node.id, node.id)
            return node

    return ast.dump(Normalise().visit(tree), include_attributes=False)


def _name_bindings(tree):
    """Collect lexical bindings, including names stored as AST string fields."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            yield node, node.id
        elif isinstance(node, ast.arg):
            yield node, node.arg
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node, node.name
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            yield node, node.name
        elif isinstance(node, ast.MatchMapping) and node.rest:
            yield node, node.rest
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                yield node, name
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                yield node, alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
        elif isinstance(node, ast.TypeVar):
            yield node, node.name
        elif isinstance(node, (ast.ParamSpec, ast.TypeVarTuple)):
            yield node, node.name


def _normal_equivalent(before, after):
    try:
        reserved = set(re.findall(r"\b\w+\b", before + "\n" + after))
        return _normal_form(before, reserved) == _normal_form(after, reserved)
    except (SyntaxError, ValueError, RecursionError):
        return False


def _negative_justification(case, policy):
    if _normal_equivalent(case.before_tests, case.after_tests):
        return "n1"
    from .held_out import oracle

    policy = policy or oracle.OraclePolicy()
    workspace = state_path("distill", "evaluation-work")
    workspace.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + policy.wall_s
    if not case.known_wrong:
        return "shared_mechanism"
    for source in case.known_wrong:
        results = []
        for suite in (case.before_tests, case.after_tests):
            remaining = int(deadline - time.monotonic())
            if remaining < 1:
                return "shared_mechanism"
            results.append(oracle._run(case, source, suite, workspace=workspace,
                                       timeout_s=min(remaining, policy.per_run_s)))
        if results[0] not in {"passed", "failed"} or results[0] != results[1]:
            return "shared_mechanism"
    return "n2"


@dataclass(frozen=True)
class _GuardedCandidate:
    candidate: VerifierCandidate
    scope: list
    rows: dict

    @property
    def contract(self):
        return self.candidate.contract


def _run(candidate, case, entries, stack=frozenset()):
    if isinstance(candidate, _GuardedCandidate):
        own = (_run(candidate.candidate, case, entries, stack)
               if _case_in_scope(candidate.scope, case) else "abstain")
        served = candidate.rows[case.case_id].teacher_verdict if own == "abstain" else own
        return own, served
    _candidate_boundary(candidate)
    if candidate.candidate_id in stack:
        raise ValueError("cyclic method dependency")
    stack = stack | {candidate.candidate_id}
    contract = candidate.contract
    facts = _extract_facts({"tests/test_case.py": case.before_tests}, {"tests/test_case.py": case.after_tests})
    if not _in_scope(contract.applies_when, facts):
        return "abstain"
    if contract.probes or (isinstance(candidate.body, TemplateInstance) and candidate.body.template.endswith(".v2")):
        verdict = _probe_verdict(candidate, case)
        if verdict not in contract.verdict_kinds:
            raise ValueError("method verdict outside contract")
        return verdict
    if candidate.form == "predicate":
        # Admission cannot inherit an owner's permission for unconfined exploratory
        # scripts. This setting only tightens execution and is restored afterwards.
        if os.environ.get("G6_REQUIRE_ISOLATION") != "1":
            raise ValueError("acceptance predicates require G6_REQUIRE_ISOLATION=1")
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
            if current.replacement == "R" and not _case_in_scope(current.replacement_scope, case):
                children.append("abstain")
            else:
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


def _case_in_scope(scope, case):
    return _in_scope(scope, _extract_facts({"tests/test_case.py": case.before_tests},
                                          {"tests/test_case.py": case.after_tests}))


def _measure_entry(entry, observations, entries):
    if entry.replacement == "R" and not _replacement_authorized(entry):
        return ["abstain"] * len(observations), [0.] * (3 * len(observations))
    if entry.replacement != "R":
        return _measure(_candidate(entry), observations, entries)
    inside = [i for i, observation in enumerate(observations)
              if _case_in_scope(entry.replacement_scope, observation.case)]
    verdicts, costs = ["abstain"] * len(observations), [0.] * (3 * len(observations))
    if inside:
        values, measured = _measure(_candidate(entry), [observations[i] for i in inside], entries)
        for offset, index in enumerate(inside):
            verdicts[index] = values[offset]
            costs[3*index:3*index+3] = measured[3*offset:3*offset+3]
    return verdicts, costs


def _library_verdicts(entries, observations):
    methods = [_measure_entry(e, observations, entries)[0] for e in _active(entries)]
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
        if any(name and name in body for name in names):
            return True
    return False


def _regress(entries, observations, added=None, *, targets=None, scope=None):
    # Replay every frozen case, including negative and abstain verdicts. Unknown
    # or missing bodies refuse; a new method cannot silently replace a verdict.
    cases = {o.case.case_id: o for o in observations}
    for entry in _active(entries if targets is None else targets):
        for expected in entry.regression_suite:
            observation = cases.get(expected.case_ref)
            if observation is None or observation.label == "excluded":
                raise ValueError("regression case unavailable")
            own = _measure_entry(entry, (observation,), entries)[0][0]
            if own != expected.verdict:
                raise ValueError("entry regression no longer reproduces")
            before = _library_verdicts(entries, (observation,))[0]
            after = before
            if added and (scope is None or _case_in_scope(scope, observation.case)):
                after = _union([before, _measure(added, (observation,), entries)[0][0]])
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
    return dict(n_pos=n_pos, n_neg=n_neg, tp=tp, fp=fp, tn=tn, fn=n_pos - tp, abstain=abstain)


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
    _candidate_boundary(candidate)
    observations = frozen.acceptance
    if not observations or any(o.label not in {"positive", "negative"} for o in observations):
        raise ValueError("settled acceptance cases required")
    if set(candidate.training_case_ids) & {o.case.case_id for o in observations}:
        raise ValueError("training cases cannot be held-out acceptance evidence")
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


def _unrecorded_key(registration):
    return str(state_path("distill", "acceptance_gate.intervals")), _journal, registration


def _bookkeeping_call(operation, call, *, deadline=None, enforce=True):
    meter = getattr(_BOOKKEEPING, "meter", None)
    if meter is None and deadline is None:
        return call()
    began = time.monotonic()
    failed = True
    if meter is not None:
        meter["depth"] += 1
    overhead_before = meter["overhead_s"] if meter is not None else 0.
    try:
        result = call()
        failed = False
    finally:
        elapsed = time.monotonic() - began
        limit = deadline if deadline is not None else meter["deadline"]
        bad = failed or not math.isfinite(elapsed) or not 0 <= elapsed <= limit
        if meter is not None:
            meter["depth"] -= 1
            nested = meter["overhead_s"] - overhead_before
            meter["overhead_s"] += max(0., elapsed - nested)
            meter["diagnostics"].append({"operation": operation, "latency_s": elapsed,
                                         "failed": failed, "deadline_exceeded": bad})
            meter["bad"] |= bad
        else:
            _BOOKKEEPING_DIAGNOSTICS[uuid4().hex] = [{"operation": operation, "latency_s": elapsed,
                                                      "failed": failed, "deadline_exceeded": bad}]
    if bad and enforce:
        raise ValueError("registration bookkeeping deadline exceeded; owner cost recovery required")
    return result


def _gate_journal():
    return _bookkeeping_call("journal_acquisition", _journal)


@contextmanager
def _bookkeeping_writer():
    manager = _writer()
    entered = False
    def enter():
        nonlocal entered
        lease = manager.__enter__()
        entered = True
        return lease
    try:
        lease = _bookkeeping_call("writer_acquisition", enter)
    except BaseException:
        error = sys.exc_info()
        if entered:
            _bookkeeping_call("writer_release", lambda: manager.__exit__(*error))
        raise
    try:
        yield lease
    except BaseException:
        error = sys.exc_info()
        if not _bookkeeping_call("writer_release", lambda: manager.__exit__(*error)):
            raise
    else:
        _bookkeeping_call("writer_release", lambda: manager.__exit__(None, None, None))


def _reconcile_validation(registration, active):
    return _bookkeeping_call("journal_reconciliation", lambda: _validation_total(registration, active))


def _acquire_interval(ident=None, *, enforce=True):
    return _bookkeeping_call("interval_acquisition", lambda: _interval_journal(ident), enforce=enforce)


def _assessment_elapsed(started):
    return max(0., time.monotonic() - started)


def _diagnostic_body(body):
    meter = getattr(_BOOKKEEPING, "meter", None)
    if meter is None:
        return body
    return body | {"bookkeeping": list(meter["diagnostics"]), "bookkeeping_s": meter["overhead_s"]}


def _write(journal, kind, body):
    return _bookkeeping_call("write:" + kind, lambda: _append(journal, kind, _diagnostic_body(body)))


def _append(journal, kind, body):
    if body.get("interval_version") == 1 and kind in {"replacement_attempt_start", "registration_open_failed",
            "registration_closed", "registration_close_failed", "registration_meter_failure", "authority_confirmed",
            "authority_reconfirmed"}:
        return _endpoint_append(journal, kind, body, raw=True)
    return _append_row(journal, kind, body)


def _append_row(journal, kind, body):
    ident = digest([kind, len(journal.entries()), body])
    journal.append(kind, ident, body)
    return ident


def _look_available(registration):
    journal = _gate_journal()
    rows = journal.entries()
    pending = {r["id"] for r in rows if r["kind"] == "reserve"}
    pending -= {r["body"]["reservation"] for r in rows if r["kind"] == "decision" and r["body"].get("reservation")}
    if pending:
        raise ValueError("unfinished evaluation requires owner recovery")
    index = 1 + sum(r["kind"] == "reserve" and r["body"].get("registration") == registration for r in rows)
    if index > EvidencePolicy().max_evaluations:
        raise ValueError("held-out split spent; owner must freeze a fresh split")
    return journal, index


def _spend(registration, candidate, meter=None):
    reusable = (_reusable_look(registration, candidate.candidate_id)
                if meter and getattr(_BOOKKEEPING, "meter", None) is not None else None)
    if reusable is not None:
        original, previous = reusable
        _write(_gate_journal(), "look_reused", {"registration": registration,
            "candidate_id": candidate.candidate_id, "meter": meter, "previous_meter": previous,
            "reservation": original["id"], "reuse_index": original["body"]["reuse_index"]})
        return original["body"]["reuse_index"], original["id"]
    journal, index = _look_available(registration)
    reservation = _write(journal, "reserve", {"registration": registration, "candidate_id": candidate.candidate_id,
                                              "reuse_index": index, **({"meter": meter} if meter else {})})
    return index, reservation


def _reusable_look(registration, candidate_id):
    """A voided assessment transfers its original look once to the same candidate."""
    voided = {r["body"]["attempt"] for r in _interval_rows()
              if r["kind"] == "registration_voided" and r["body"].get("registration") == registration}
    rows = _gate_journal().entries()
    for original in reversed(rows):
        body = original["body"]
        if original["kind"] != "reserve" or body.get("registration") != registration \
                or body.get("candidate_id") != candidate_id:
            continue
        transfers = [r["body"] for r in rows if r["kind"] == "look_reused"
                     and r["body"].get("reservation") == original["id"]]
        previous = transfers[-1]["meter"] if transfers else body.get("meter")
        if previous in voided:
            return original, previous


def _interval_journal(ident=None):
    """Each interval owns its append-only chain, independent of the writer lease."""
    root = state_path("distill", "acceptance_gate.intervals")
    if ident is not None:
        root.mkdir(parents=True, exist_ok=True)
    return ChainLedger(root / (ident + ".jsonl")) if ident else root


def _interval_rows():
    journal = _acquire_interval()
    if isinstance(journal, Path):
        return _bookkeeping_call("interval_read", lambda: [row
            for path in sorted(journal.glob("*.jsonl")) for row in _owned_rows(ChainLedger(path))])
    return _bookkeeping_call("interval_read", lambda: _owned_rows(journal))


def _owned_rows(journal):
    rows = journal.entries()
    opening = next((r["id"] for r in rows if r["kind"] in {
        "replacement_attempt_start", "registration_open_failed"}), "")
    identity = _journal_identity(journal, opening)
    chain = str(Path(journal.path).resolve()) if getattr(journal, "path", None) else "memory"
    return [r | {"interval_chain": chain, "journal_identity": identity} for r in rows]


def _append_deadline(registration):
    _, policy = _policy()
    value = policy["registrations"].get(registration, {}).get("d_append_s", 5.)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("registered endpoint deadline required")
    return float(value)


def _endpoint_allowance(opening):
    value = opening.get("d_append_s", 5.)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("registration endpoint deadline invalid; owner cost recovery required")
    return 2 * value


def _endpoint_append(journal, kind, body, *, raw=False):
    if raw and kind == "registration_closed" and body.get("recovery_of") is not None:
        floor = max([body["validation_s"], *(r["body"].get("validation_s", 0.) for r in journal.entries()
            if r["id"] == body["attempt"] or r["body"].get("attempt") == body["attempt"])])
        body = body | {"recovery_elapsed_s": floor}
    try:
        return _bookkeeping_call("write:" + kind,
            lambda: _append_row(journal, kind, _diagnostic_body(body)) if raw else _append(journal, kind, body),
            deadline=_endpoint_allowance(body) / 2)
    except Exception:
        if raw:
            attempt = body.get("attempt")
            if attempt is None:
                starts = [r for r in journal.entries() if r["kind"] == kind and r["body"] == body]
                attempt = starts[-1]["id"] if starts else None
            if attempt is not None:
                _invalidate_endpoint(journal, body, attempt)
        raise


def _invalidate_endpoint(journal, opening, attempt_id):
    for _ in range(2):
        try:
            path = getattr(journal, "path", None)
            current = _bookkeeping_call("failure_acquisition", lambda: ChainLedger(path)) if path is not None else journal
            body = opening | {"state": "incomplete", "attempt": attempt_id,
                "validation_s": _interval_elapsed(opening), "monotonic_end": time.monotonic()}
            _bookkeeping_call("write:registration_close_failed",
                lambda: _append_row(current, "registration_close_failed", _diagnostic_body(body)),
                deadline=_endpoint_allowance(opening) / 2)
            return
        except Exception:
            continue


def _interval_failure(interval_id, opening, attempt_id):
    for _ in range(2):
        try:
            _endpoint_append(_acquire_interval(interval_id), "registration_close_failed", opening | {
                "attempt": attempt_id, "validation_s": _interval_elapsed(opening),
                "monotonic_end": time.monotonic()})
            return
        except Exception:
            continue


def _interval_elapsed(body, *, recovery=False):
    elapsed = time.monotonic() - body["monotonic_start"]
    wall = time.time() - body["wall_start"]
    if not math.isfinite(elapsed) or not math.isfinite(wall):
        raise ValueError("registration clock requires owner cost recovery")
    if recovery:
        boot = _boot_identity()
        if (elapsed < 0 or wall < 0 or abs(wall - elapsed) > max(2., .01 * max(wall, elapsed))
                or (sys.platform.startswith("linux") and (not boot or body.get("boot_id") != boot))
                or (boot is not None and body.get("boot_id") != boot)):
            raise ValueError("registration continuous clock unavailable; owner cost recovery required")
        return elapsed
    return max(elapsed, 0.)


def _legacy_validation_total(registration, active):
    intervals = {r["id"] for r in _interval_rows() if r["body"].get("interval_version") == 1
                 and r["kind"] == "replacement_attempt_start" and r["body"].get("registration") == registration}
    rows = [r for r in _gate_journal().entries() if r["body"].get("registration") == registration
            and not r["body"].get("interval_version") and r["body"].get("meter") not in intervals]
    starts = {r["id"]: r["body"] for r in rows if r["kind"] == "replacement_attempt_start"}
    finished = {}
    for row in rows:
        if row["kind"] != "replacement_attempt":
            continue
        body = row["body"]
        ident, elapsed = body["attempt"], body["validation_s"]
        if ident not in starts or ident in finished or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("replacement attempt cost history invalid")
        finished[ident] = elapsed
    sealed = set()
    for row in rows:
        if row["kind"] not in {"registration_meter_closed", "registration_meter_failure"}:
            continue
        body = row["body"]
        ident, elapsed = body["attempt"], body["validation_s"]
        if row["kind"] == "registration_meter_failure":
            if (ident not in starts or not math.isfinite(elapsed) or elapsed < 0
                    or body["candidate_id"] != starts[ident]["candidate_id"]):
                raise ValueError("registration meter failure binding invalid")
            finished[ident] = max(finished.get(ident, 0.), elapsed)
            sealed.add(ident)
            continue
        if (ident not in finished or ident in sealed or not math.isfinite(elapsed)
                or elapsed < finished[ident] or body["candidate_id"] != starts[ident]["candidate_id"]):
            raise ValueError("registration meter closure invalid")
        finished[ident] = elapsed
        sealed.add(ident)
    for ident, body in starts.items():
        if body.get("outer") and ident not in sealed:
            finished.pop(ident, None)
    if starts.keys() - finished.keys() - {active}:
        raise ValueError("unfinished replacement attempt requires owner cost recovery")
    recovered_meters = {r["body"].get("meter") for r in rows if r["kind"] == "reservation_meter_recovery"}
    if any(not starts[ident].get("outer") and ident not in recovered_meters for ident in finished):
        raise ValueError("unsealed earlier evaluation requires owner cost recovery")
    reservations = {r["id"]: r["body"] for r in rows if r["kind"] == "reserve"}
    recovered = {}
    for row in rows:
        if row["kind"] != "reservation_meter_recovery":
            continue
        body = row["body"]
        reservation, meter = body["reservation"], body["meter"]
        original = reservations.get(reservation)
        start = starts.get(meter)
        if (original is None or original.get("meter") or reservation in recovered or meter not in finished
                or start is None or start["candidate_id"] != original.get("candidate_id")):
            raise ValueError("reservation cost recovery binding invalid")
        recovered[reservation] = meter
    used = set()
    for row in rows:
        if row["kind"] != "reserve":
            continue
        meter = row["body"].get("meter") or recovered.get(row["id"])
        start = starts.get(meter)
        key = (meter, row["body"].get("candidate_id"))
        if (start is None or key in used or start["phase"] != "assessment"
                or row["body"].get("candidate_id") not in start.get("candidate_ids", [start["candidate_id"]])):
            raise ValueError("unmetered earlier evaluation requires owner cost recovery")
        used.add(key)
    return math.fsum(finished.values())


def _validation_total(registration, active):
    unrecorded = _UNRECORDED_BOOKKEEPING.get(_unrecorded_key(registration))
    if unrecorded is not None:
        openings = [r["body"] for r in _interval_rows() if active is not None and r["id"] == active
                    and r["kind"] == "registration_open_failed"] if active is not None else []
        if not openings or any(openings[0].get(key) != unrecorded.get(key)
                               for key in ("interval_id", "registration", "monotonic_start", "wall_start", "d_append_s")):
            raise ValueError("unrecorded registration opening requires owner cost recovery")
    total = _legacy_validation_total(registration, active)
    rows = [r for r in _interval_rows() if r["body"].get("registration") == registration
            and r["body"].get("interval_version") == 1]
    starts, owners, closed, failed, floors, ends, members, proposals = {}, {}, {}, {}, {}, {}, {}, {}
    voided, approvals = {}, {}
    for row in rows:
        body = row["body"]
        if row["kind"] in {"replacement_attempt_start", "registration_open_failed"}:
            ident = row["id"]
            if ident in starts:
                raise ValueError("registration opening duplicated; owner cost recovery required")
            starts[ident], owners[ident] = body, row["interval_chain"]
            floors[ident] = body.get("validation_s", 0.)
            ends[ident] = body.get("monotonic_end", body["monotonic_start"] + floors[ident])
            if (not math.isfinite(floors[ident]) or floors[ident] < 0 or not math.isfinite(ends[ident])
                    or ends[ident] < body["monotonic_start"]):
                raise ValueError("registration opening cost invalid; owner cost recovery required")
            if _endpoint_allowance(body) != 2 * _append_deadline(registration):
                raise ValueError("registration endpoint deadline changed; owner cost recovery required")
            if row["kind"] == "registration_open_failed":
                failed[ident] = ident
            continue
        if row["kind"] not in {"replacement_attempt", "registration_meter_closed", "authority_confirmed", "authority_reconfirmed",
                "registration_meter_failure", "registration_close_failed", "registration_closed", "registration_voided"}:
            continue
        ident = body["attempt"]
        if (ident not in starts or row["interval_chain"] != owners[ident]
                or body.get("d_append_s", 5.) != starts[ident].get("d_append_s", 5.)):
            raise ValueError("registration endpoint chain binding invalid; owner cost recovery required")
        elapsed = body.get("validation_s", floors[ident])
        end = body.get("monotonic_end", starts[ident]["monotonic_start"] + elapsed)
        if not math.isfinite(elapsed) or not math.isfinite(end) or elapsed < 0:
            raise ValueError("registration elapsed cost invalid; owner cost recovery required")
        if row["kind"] == "authority_reconfirmed" and body.get("action") == "void":
            approvals[row["id"]] = body
            continue
        if row["kind"] == "authority_reconfirmed":
            continue
        if row["kind"] == "registration_voided":
            binding = body.get("approval_binding")
            approval = approvals.get(body.get("approval_row"))
            if (ident in closed or ident in voided or body.get("action") != "void"
                    or body.get("close", "") != proposals.get(ident, ("",))[0]
                    or body.get("journal_identity") != list(row["journal_identity"])
                    or (binding is not None and (approval is None
                        or approval.get("approval_binding") != binding
                        or approval.get("approval_id") != body.get("approval_id")
                        or approval.get("approval_scope") != body.get("approval_scope")
                        or body.get("approval_scope") != "distill:reconfirm:" + digest(binding)
                        or binding.get("interval_id") != starts[ident].get("interval_id", ident)
                        or binding.get("close_id") != body.get("close", "")
                        or binding.get("action") != "void"
                        or binding.get("seconds_to_charge") != elapsed + _endpoint_allowance(starts[ident])))
                    or (not body.get("clock_continuous") and binding is None)):
                raise ValueError("registration void binding invalid; owner cost recovery required")
            voided[ident] = elapsed + _endpoint_allowance(starts[ident])
            members[ident] = body["candidate_ids"]
            failed.pop(ident, None)
            continue
        if ident in voided:
            if row["kind"] == "registration_close_failed":
                voided.pop(ident)
            else:
                raise ValueError("voided interval cannot grant authority; owner cost recovery required")
        if row["kind"] == "registration_closed":
            if (ident in closed or (ident in proposals and not body.get("recovery_of"))
                    or body.get("recovery_of") != failed.get(ident)
                    or elapsed < floors[ident] or end < ends[ident]
                    or elapsed < end - starts[ident]["monotonic_start"]):
                raise ValueError("registration interval closure invalid; owner cost recovery required")
            if body.get("recovery_of") is not None:
                recovery = body.get("recovery_elapsed_s")
                if (not isinstance(recovery, (int, float)) or not math.isfinite(recovery)
                        or recovery < max(elapsed, floors[ident])):
                    raise ValueError("registration recovery floor invalid; owner cost recovery required")
                elapsed = recovery
            proposals[ident] = (row["id"], elapsed, body)
            members[ident] = body["candidate_ids"]
        elif row["kind"] in {"authority_confirmed", "authority_reconfirmed"}:
            proposal = proposals.get(ident)
            latency, checked = body.get("close_latency_s"), body.get("validation_total_s")
            if (proposal is None or body.get("close") != proposal[0] or ident in closed
                    or body.get("candidate_ids") != proposal[2].get("candidate_ids")
                    or body.get("activation_receipts") != proposal[2].get("activation_receipts")
                    or not isinstance(latency, (int, float)) or not math.isfinite(latency)
                    or not 0 <= latency <= _endpoint_allowance(starts[ident]) / 2
                    or not isinstance(checked, (int, float)) or not math.isfinite(checked)
                    or elapsed < proposal[1] or checked < elapsed + _endpoint_allowance(starts[ident])):
                raise ValueError("registration confirmation invalid; owner cost recovery required")
            if _returned_confirmation(row, rows):
                closed[ident] = elapsed + _endpoint_allowance(starts[ident])
                failed.pop(ident, None)
        elif row["kind"] == "registration_close_failed":
            closed.pop(ident, None)
            failed[ident] = row["id"]
        floors[ident] = max(floors[ident], elapsed)
        ends[ident] = max(ends[ident], end)
    unfinished = starts.keys() - closed.keys() - voided.keys() - {active}
    total += math.fsum([*closed.values(), *voided.values()])
    used = set()
    for row in _gate_journal().entries():
        body = row["body"]
        meter = body.get("meter")
        if row["kind"] != "reserve" or body.get("registration") != registration or meter not in starts:
            continue
        key = (meter, body.get("candidate_id"))
        if (key in used or starts[meter]["phase"] != "assessment"
                or (meter != active and body.get("candidate_id") not in members.get(meter, []))):
            raise ValueError("registration interval reservation binding invalid; owner cost recovery required")
        used.add(key)
    if unfinished:
        total += math.fsum(floors[ident] + _endpoint_allowance(starts[ident]) for ident in unfinished)
        raise ValueError(f"unfinished registration interval requires owner cost recovery; elapsed total {total}")
    return total


def reconfirmation_scope(journal, close, *, owner_validation_s=None):
    """The six displayed recovery fields; this function mints nothing."""
    row = next(r for r in journal.entries() if r["id"] == close and r["kind"] == "registration_closed")
    seconds = None if owner_validation_s is None else owner_validation_s + _endpoint_allowance(row["body"])
    return "distill:reconfirm:" + digest(_recovery_binding(journal, close, seconds_to_charge=seconds))


def _recovery_binding(journal, close, *, seconds_to_charge=None):
    rows = journal.entries()
    row = next(r for r in rows if r["id"] == close and r["kind"] in {
        "registration_closed", "replacement_attempt_start", "registration_open_failed"})
    attempt = row["body"].get("attempt", row["id"])
    opening = next(r for r in rows if r["id"] == attempt)
    closes = [r for r in rows if r["kind"] == "registration_closed" and r["body"].get("attempt") == attempt]
    if closes and closes[-1]["id"] != close:
        raise ValueError("owner recovery requires the current close")
    receipts = [r for r in _gate_journal().entries() if r["kind"] == "replacement_state"
                and r["body"].get("attempt") == attempt]
    candidates = row["body"].get("candidate_ids", opening["body"].get("candidate_ids", []))
    if not candidates:
        candidates = list(dict.fromkeys(r["body"]["candidate_id"] for r in _gate_journal().entries()
            if r["kind"] == "reserve" and r["body"].get("meter") == attempt))
    if len(candidates) > 1:
        raise ValueError("owner recovery requires a singleton interval")
    return {"interval_id": opening["body"].get("interval_id", attempt),
            "close_id": close if row["kind"] == "registration_closed" else "",
            "report_sha256": digest(receipts[-1]["body"]["report"]) if receipts else "",
            "candidate": candidates[0] if candidates else "", "action": "void",
            "seconds_to_charge": seconds_to_charge}


def _recover_interval(journal, close, *, owner_grant=None, seconds_to_charge=None):
    """Void an incomplete interval, never restore its authority. Same-user shell access is outside this boundary."""
    with _bookkeeping_writer():
        binding = _recovery_binding(journal, close, seconds_to_charge=seconds_to_charge)
        rows = _owned_rows(journal)
        row = next(r for r in rows if r["id"] == close)
        attempt = row["body"].get("attempt", row["id"])
        opening = next(r["body"] for r in rows if r["id"] == attempt)
        terminal = [r for r in rows if r["body"].get("attempt") == attempt
                    and (r["kind"] in {"registration_voided", "registration_close_failed"}
                         or (r["kind"] == "authority_confirmed" and _returned_confirmation(r, rows)))]
        if terminal and terminal[-1]["kind"] != "registration_close_failed":
            raise ValueError("interval already complete or voided; owner recovery refused")
        _reconcile_validation(opening["registration"], attempt)
        try:
            _interval_elapsed(opening, recovery=True)
            continuous = True
        except ValueError:
            continuous = False
        approval_row = None
        if owner_grant is not None or not continuous:
            from .recovery_approvals import consume
            authority = get_approval_authority()
            if (isinstance(seconds_to_charge, bool) or not isinstance(seconds_to_charge, (int, float))
                    or not math.isfinite(seconds_to_charge)
                    or not consume(owner_grant, binding, authority=authority)):
                raise ValueError("client-dialog owner approval required for recovery")
            record = authority.resolve(owner_grant)
            approval = opening | {"attempt": attempt, "close": binding["close_id"], "action": "void",
                "approval_id": owner_grant, "approval_scope": "distill:reconfirm:" + digest(binding),
                "approval_binding": binding, "dialog_text": record.disclosure,
                "dialog_reply": record.acknowledgement}
            approval_row = _write(journal, "authority_reconfirmed", approval)
        elif seconds_to_charge is not None:
            raise ValueError("owner floor requires client-dialog approval")
        prepared = opening | {"attempt": attempt, "close": binding["close_id"], "action": "void",
            "state": "voided", "candidate_ids": [binding["candidate"]] if binding["candidate"] else [],
            "activation_receipts": [], "journal_identity": list(row["journal_identity"]),
            "clock_continuous": continuous, "approval_row": approval_row,
            "approval_id": owner_grant, "approval_scope": "distill:reconfirm:" + digest(binding) if approval_row else None,
            "approval_binding": binding if approval_row else None}
    key = _unrecorded_key(opening["registration"])
    unrecorded = _UNRECORDED_BOOKKEEPING.get(key)
    # Sample after reconciliation, approval consumption and durable approval recording.
    boot = _boot_identity() if not approval_row else None
    allowance = _endpoint_allowance(opening)
    wall_elapsed = time.time() - opening["wall_start"]
    sampled = time.monotonic()
    elapsed = sampled - opening["monotonic_start"]
    if approval_row:
        floor = seconds_to_charge - allowance
        if (not math.isfinite(wall_elapsed) or wall_elapsed < 0 or floor < wall_elapsed
                or (continuous and floor < elapsed)):
            raise ValueError("owner recovery floor does not cover elapsed time")
        elapsed = floor
    elif (not math.isfinite(elapsed) or not math.isfinite(wall_elapsed) or elapsed < 0 or wall_elapsed < 0
            or abs(wall_elapsed - elapsed) > max(2., .01 * max(wall_elapsed, elapsed))
            or (sys.platform.startswith("linux") and (not boot or opening.get("boot_id") != boot))
            or (boot is not None and opening.get("boot_id") != boot)):
        raise ValueError("continuous clock unavailable; owner recovery required")
    body = prepared | {"validation_s": elapsed, "monotonic_end": opening["monotonic_start"] + elapsed,
                       "recovery_sample": sampled, "wall_elapsed_s": wall_elapsed}
    try:
        ident = _endpoint_append(journal, "registration_voided", body)
    except Exception:
        _invalidate_endpoint(journal, opening, attempt)
        raise
    if unrecorded is not None and opening.get("interval_id") == unrecorded["interval_id"]:
        _UNRECORDED_BOOKKEEPING.pop(key)
    return {"state": "voided", "interval_id": binding["interval_id"], "row_id": ident,
            "seconds_to_charge": elapsed + allowance}


def _sample_confirmation(body, prior, allowance, limits, *, anchor, floor):
    """Final clock sample and scalar R4 checks; no evidence or journal reads."""
    sampled = time.monotonic()
    elapsed = max(floor, sampled - anchor) if anchor is not None else floor
    total = prior + elapsed + allowance
    if (not math.isfinite(total) or (anchor is not None and sampled < anchor)
            or any(not math.isfinite(limit) or total > limit for limit in limits)):
        raise ValueError("cumulative cost upper bound at H is not lower after activation")
    return body | {"confirmation_sample": sampled, "validation_s": elapsed,
                   "monotonic_end": body["monotonic_start"] + elapsed,
                   "validation_total_s": total}


def _observe_confirmation(journal, row):
    """Prepare a bounded marker, then append it with no further assessment work."""
    raw = {key: value for key, value in row.items() if key not in {"journal_identity", "interval_chain"}}
    body = {key: row["body"][key] for key in ("registration", "attempt", "close")}
    body.update(interval_version=1, confirmation=row["id"], confirmation_digest=digest(raw),
                journal_identity=list(row["journal_identity"]), interval_chain=row["interval_chain"])

    def prepare():
        staged = ChainLedger()
        for previous in journal.entries():
            staged.append(previous["kind"], previous["id"], previous["body"])
        if staged.head() != row["entry_sha256"]:
            raise ValueError("confirmation marker chain changed; owner cost recovery required")
        ident = _write(staged, "confirmation_observed", body)
        return next(item for item in staged.entries() if item["id"] == ident)

    marker = _bookkeeping_call("marker_preparation", prepare, deadline=_endpoint_allowance(row["body"]) / 2)
    # This final append's own latency is unobserved, with no assessment in it.
    journal.append(marker["kind"], marker["id"], marker["body"])


def _confirm_interval(journal, close, *, close_latency_s=0., pending=(), owner_grant=None,
                      owner_validation_s=None):
    """Confirm only the live returned close; incomplete intervals require void recovery."""
    if getattr(_BOOKKEEPING, "meter", None) is None:
        began = time.monotonic()
        row = next(r for r in journal.entries() if r["id"] == close and r["kind"] == "registration_closed")
        deadline = _append_deadline(row["body"]["registration"])
        elapsed = time.monotonic() - began
        bad = not math.isfinite(elapsed) or not 0 <= elapsed <= deadline
        meter = {"deadline": deadline, "depth": 0, "overhead_s": max(0., elapsed), "bad": bad,
                 "diagnostics": [{"operation": "confirmation_setup", "latency_s": elapsed,
                                  "failed": False, "deadline_exceeded": bad}]}
        _BOOKKEEPING.meter = meter
        try:
            return _confirm_interval(journal, close, close_latency_s=close_latency_s, pending=pending,
                owner_grant=owner_grant, owner_validation_s=owner_validation_s)
        finally:
            _BOOKKEEPING_DIAGNOSTICS[uuid4().hex] = list(meter["diagnostics"])
            _BOOKKEEPING.meter = None
    rows = _bookkeeping_call("confirmation_read", journal.entries)
    row = next(r for r in rows if r["id"] == close and r["kind"] == "registration_closed")
    body = row["body"]
    meter = getattr(_BOOKKEEPING, "meter", None)
    if meter is not None and meter["bad"]:
        raise ValueError("registration bookkeeping incomplete; owner cost recovery required")
    identity = _bookkeeping_call("confirmation_identity", lambda: _journal_identity(journal, body["attempt"]))
    owned = _bookkeeping_call("confirmation_history", lambda: _owned_rows(journal))
    if any(r["kind"] == "registration_voided" and r["body"].get("attempt") == body["attempt"] for r in owned):
        raise ValueError("voided interval cannot authorize; owner recovery requires reassessment")
    completed = [r for r in owned if r["kind"] == "authority_confirmed"
                 and r["body"].get("close") == close and _returned_confirmation(r, owned)]
    if completed:
        _reconcile_validation(body["registration"], None)
        return completed[-1]["id"]
    live = meter.pop("live_close", None) == (identity, close)
    if not live or body.get("recovery_of") is not None:
        raise ValueError("incomplete interval cannot authorize; owner cost recovery must void it")
    kind = "authority_confirmed"
    boot = _bookkeeping_call("confirmation_boot", _boot_identity)
    try:
        _interval_elapsed(body, recovery=True)
        continuous = True
    except ValueError:
        continuous = False
    anchor = body["monotonic_start"]
    floor = max(body["validation_s"], body.get("recovery_elapsed_s", 0.),
                *(r["body"].get("validation_s", 0.) for r in rows if r["body"].get("attempt") == body["attempt"]))
    if owner_validation_s is not None:
        if (isinstance(owner_validation_s, bool) or not isinstance(owner_validation_s, (int, float))
                or not math.isfinite(owner_validation_s) or owner_validation_s < floor):
            raise ValueError("owner cost recovery cannot reduce the recorded floor")
        floor = owner_validation_s
    prior = _reconcile_validation(body["registration"], body["attempt"])
    allowance = _endpoint_allowance(body)
    total = prior + floor + allowance
    limits = []
    for _, frozen, data in pending:
        checked = _final_replacement_cost(data, frozen.replacement, total)["replacement"]
        if frozen.replacement.cpu_weight > 0:
            limits.append(total - checked["cost"]["upper_95"] / frozen.replacement.cpu_weight)
    receipts = _bookkeeping_call("confirmation_receipts", lambda: _gate_journal().entries())
    for receipt in receipts:
        if receipt["id"] in body["activation_receipts"]:
            replacement = receipt["body"]["report"]["replacement"]
            weight = replacement["cpu_weight"]
            if weight > 0:
                limits.append(replacement["validation_s"] - replacement["cost"]["upper_95"] / weight)
            elif replacement["cost"]["upper_95"] > 0:
                raise ValueError("cumulative cost upper bound at H is not lower after activation")
    nonce = uuid4().hex
    prepared = body | {"close": close, "close_latency_s": close_latency_s, "confirmation_nonce": nonce,
                       "owner_grant": owner_grant if not live else None, "confirmation_boot_id": boot,
                       "clock_continuous": continuous, "journal_identity": list(owned[0]["journal_identity"]),
                       "interval_chain": owned[0]["interval_chain"]}
    confirmation = _sample_confirmation(prepared, prior, allowance, limits, anchor=anchor, floor=floor)
    ident = _write(journal, kind, confirmation)
    returned = _bookkeeping_call("confirmation_return_read", lambda: next(r for r in _owned_rows(journal) if r["id"] == ident))
    _observe_confirmation(journal, returned)
    _RETURNED_CONFIRMATIONS[_confirmation_key(returned)] = {
        "registration": body["registration"], "deadline": _endpoint_allowance(body) / 2,
        "candidate_ids": body["candidate_ids"], "pending_receipts": [r["body"].get("pending_receipt")
            for r in receipts if r["id"] in body["activation_receipts"]]}
    key = _unrecorded_key(body["registration"])
    unrecorded = _UNRECORDED_BOOKKEEPING.get(key)
    if unrecorded is not None and body.get("interval_id") == unrecorded["interval_id"]:
        _UNRECORDED_BOOKKEEPING.pop(key)
    return ident


def _record(answer, reservation=""):
    _write(_gate_journal(), "decision", asdict(answer) | {"reservation": reservation, "gate_version": VERSION})
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
    if entry is None or (entry.replacement == "R" and not _replacement_authorized(entry)):
        raise ValueError("stale, pending or missing library snapshot")
    return entry


def _persist(candidate, answer, evidence, own, frozen, data, library, *, prepare=False):
    if any(e.candidate_id == candidate.candidate_id and e.status in {"probation", "admitted"} for e in library.list()):
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
    replacement = data.get("replacement")
    pending = replacement is not None and replacement.get("state") == "pending" and prepare != "activation"
    if replacement is not None:
        certificate = certificate | {"authority": "A0" if pending else "A1", "state": "pending"}
    fields = {} if replacement is None else {
        "replacement": "none" if pending else "R", "replacement_scope": replacement["scope"], "teacher": replacement["teacher"],
        "noninferiority": _save_report(replacement)}
    entry = LibraryEntry(candidate_id=candidate.candidate_id, contract_sha256=candidate.contract.content_id,
        concept_id=candidate.concept_id, family=candidate.contract.family,
        version=max((e.version for e in previous), default=0) + 1, parents=candidate.lineage,
        status="demoted" if pending else "probation", authority="A0" if pending else "A1", evidence=evidence,
        regression_suite=[RegressionCase(case_ref=o.case.case_id, verdict=v) for o, v in zip(frozen.acceptance, own)],
        field=FieldEvidence(fired=0, confirmed=0, refuted=0, unlabelled=0, precision=None),
        decided_by=DecidedBy(gate_version=VERSION, person=None), certificate_sha256=_save_report(certificate),
        created_at=now, changed_at=now, reason="; ".join(answer.reasons), **fields)
    if prepare:
        return entry
    Stores().for_model(LibraryEntry).put(entry)
    return replace(answer, entry_id=entry.entry_id)


def _stage_replacement(entry):
    """Prepare a complete library chain without changing its published path."""
    store = Stores().for_model(LibraryEntry)
    target = store.path
    target.parent.mkdir(parents=True, exist_ok=True)
    head = store.chain.head()
    staged = target.with_name(target.name + ".pending-" + uuid4().hex)
    try:
        with staged.open("xb") as stream:
            stream.write(target.read_bytes() if target.exists() else b"")
        store.path = staged
        store.chain = ChainLedger(staged, validate=store._validate)
        store.put(entry)
        return staged, target, head
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _publish_replacement(staged, target, head, deadline):
    if ChainLedger(target).head() != head:
        raise ValueError("library changed during replacement preparation")
    if time.monotonic() > deadline:
        raise ValueError("activation allowance exceeded; owner cost recovery required")
    os.replace(staged, target)


def _final_replacement_cost(data, study, validation_s):
    """Shift the fixed cost while retaining the task-weighted sampling bounds."""
    replacement = data["replacement"]
    cost = dict(replacement["cost"])
    fixed = study.fixed_new_s - study.fixed_base_s + validation_s * study.cpu_weight
    shift = fixed - cost["fixed_difference"]
    cost.update(fixed_difference=fixed, point=cost["point"] + shift, upper_95=cost["upper_95"] + shift)
    saving = cost["per_task_saving"]
    kind = ("dominates" if fixed <= 0 <= saving else "break_even" if fixed > 0 and saving > 0
            else "crossover" if fixed < 0 and saving < 0 else "never")
    cost.update(kind=kind, crossing=fixed / saving if kind in {"break_even", "crossover"} else None,
                admissible=cost["upper_95"] <= 0)
    # The fixed-cost null changed; retain a conservative descriptive p value.
    cost["interval"] = cost["interval"] | {"margin": -fixed / 1000, "p_value": 1.}
    if not math.isfinite(fixed) or not cost["admissible"]:
        raise ValueError("cumulative cost upper bound at H is not lower after activation")
    return data | {"replacement": replacement | {"cost": cost, "validation_s": validation_s}}


def _replacement_authorized(entry):
    """An activation receipt needs a durable observed confirmation and reconciled R4."""
    meter = {"deadline": 5., "depth": 0, "overhead_s": 0.,
             "bad": False, "diagnostics": []}
    previous = getattr(_BOOKKEEPING, "meter", None)
    _BOOKKEEPING.meter = meter
    try:
        return _bookkeeping_call("authority_read", lambda: _replacement_authorized_inner(entry))
    except Exception:
        return False
    finally:
        _BOOKKEEPING_DIAGNOSTICS[uuid4().hex] = list(meter["diagnostics"])
        _BOOKKEEPING.meter = previous


def _replacement_authorized_inner(entry):
    try:
        journal = _gate_journal()
        rows = _bookkeeping_call("authority_journal_read", journal.entries)
        states = [r for r in rows if r["kind"] == "replacement_state"
                  and (r["body"].get("entry_id") == entry.entry_id
                       or (r["body"].get("pending_receipt") == entry.noninferiority
                           and r["body"].get("candidate_id") == entry.candidate_id))]
        if not states or states[-1]["body"].get("state") != "active":
            return False
        row = states[-1]
        receipt = row["body"]
        report = receipt["report"]
        replacement = report["replacement"]
        limit = _bookkeeping_call("authority_deadline", lambda: _append_deadline(report["registration"]))
        getattr(_BOOKKEEPING, "meter")["deadline"] = limit
        intervals = _bookkeeping_call("authority_intervals", _interval_rows)
        if any(r["kind"] == "registration_voided" and r["body"].get("attempt") == receipt.get("attempt")
               for r in intervals):
            return False
        closed = [r["body"] for r in intervals
                  if r["kind"] == "authority_confirmed"
                  and r["body"].get("interval_version") == 1
                  and _returned_confirmation(r, intervals)
                  and r["body"].get("attempt") == receipt.get("attempt")]
        if (not closed or row["id"] not in closed[-1]["activation_receipts"]
                or closed[-1]["registration"] != report["registration"]):
            return False
        if limit != closed[-1].get("d_append_s", 5.):
            return False
        total = _reconcile_validation(report["registration"], None)
        upper = replacement["cost"]["upper_95"] + (
            total - replacement["validation_s"]) * replacement["cpu_weight"]
        return (entry.candidate_id in closed[-1]["candidate_ids"]
                and entry.candidate_id == report["candidate_id"]
                and entry.noninferiority == receipt["pending_receipt"]
                and entry.teacher == replacement["teacher"]
                and [p.model_dump(mode="json") for p in entry.replacement_scope] == replacement["scope"]
                and replacement["state"] == "active" and math.isfinite(upper) and upper <= 0)
    except (ValueError, KeyError, OSError, TypeError):
        return False


def _activate_replacement(candidate, library, frozen, evidence, batch_bar, attempt):
    registration = frozen.registration_sha256
    prior = _reconcile_validation(registration, attempt[1])
    answer, evidence, own, data = _assess_replacement(candidate, library.list(), frozen,
        evidence.reuse_index, prior_validation_s=prior, validation_started=attempt[0])
    if answer.p_value > batch_bar:
        raise ValueError("batch bar not met after library changed")
    if frozen.replacement.activation_allowance_s is None:
        raise ValueError("registered activation allowance required")
    data["replacement"]["state"] = "pending"
    evidence = AcceptanceEvidence.model_validate(evidence.model_dump(mode="json") |
        {"acceptance_report_sha256": digest(data)})
    answer = replace(answer, report_sha256=digest(data))
    for prepare in (True, "activation"):
        if prepare == "activation":
            _final_replacement_cost(data, frozen.replacement,
                prior + _assessment_elapsed(attempt[0]) + 2 * _append_deadline(registration))
            if not getattr(Library, "replacement_state_checked", False):
                raise ValueError("pending replacement reader issuance required")
        entry = _persist(candidate, answer, evidence, own, frozen, data, library, prepare=prepare)
        staged, target, head = _stage_replacement(entry)
        try:
            _publish_replacement(staged, target, head, math.inf)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
    return replace(answer, entry_id=entry.entry_id), data


def decide(candidate: VerifierCandidate, library: Library, registration: str) -> AcceptanceDecision:
    """A singleton is a predeclared batch of one; no caller-supplied evidence accepted."""
    return decide_batch((candidate,), library, registration)[0]


def decide_replacement(candidate: VerifierCandidate, library: Library, registration: str) -> AcceptanceDecision:
    """Judge a singleton replacement using registered evaluator-custodied traces."""
    return decide_replacement_batch((candidate,), library, registration)[0]


def decide_replacement_batch(candidates, library: Library, registration: str) -> tuple[AcceptanceDecision, ...]:
    """Spend one look per proposal and apply BH to the complete replacement tick."""
    return _decide_batch(candidates, library, registration, replacement=True)


def decide_batch(candidates, library: Library, registration: str) -> tuple[AcceptanceDecision, ...]:
    """Seal the complete tick batch before measuring; BH q=.05 in addition to alpha/K.

    A runner must call this once per tick, rather than adaptively submitting a
    series of singleton batches. Every attempt consumes one registered look.
    """
    return _decide_batch(candidates, library, registration, replacement=False)


def _decide_batch(candidates, library, registration, *, replacement):
    if not replacement:
        return _decide_add(candidates, library, registration)
    if _unrecorded_key(registration) in _UNRECORDED_BOOKKEEPING:
        return tuple(AcceptanceDecision("block", ("unrecorded registration opening requires owner cost recovery",))
                     for _ in candidates)
    clocks = time.monotonic(), time.time()
    try:
        deadline = _append_deadline(registration)
    except (ValueError, OSError):
        return tuple(AcceptanceDecision("block", ("registered endpoint deadline unavailable",)) for _ in candidates)
    latency = time.monotonic() - clocks[0]
    bad = not math.isfinite(latency) or not 0 <= latency <= deadline
    meter = {"deadline": deadline, "depth": 0, "overhead_s": max(0., latency), "bad": bad,
             "diagnostics": [{"operation": "deadline_lookup", "latency_s": latency,
                              "failed": False, "deadline_exceeded": bad}]}
    previous = getattr(_BOOKKEEPING, "meter", None)
    _BOOKKEEPING.meter = meter
    try:
        return _decide_batch_metered(candidates, library, registration, replacement=True, opening_clocks=clocks)
    finally:
        _BOOKKEEPING_DIAGNOSTICS[meter.get("interval_id", uuid4().hex)] = list(meter["diagnostics"])
        _BOOKKEEPING.meter = previous


@contextmanager
def _add_attempt(registration, candidate_ids, started):
    """Original add accounting, independent of replacement endpoint settings."""
    body = {"registration": registration, "candidate_id": digest(candidate_ids),
            "phase": "assessment", "outer": True, "candidate_ids": candidate_ids}
    attempt = _append(_journal(), "replacement_attempt_start", body)
    try:
        yield started, attempt
    finally:
        try:
            elapsed = time.monotonic() - started
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("replacement attempt clock invalid")
            _append(_journal(), "replacement_attempt", body | {"attempt": attempt, "validation_s": elapsed})
            sealed = time.monotonic() - started + 1.
            _append(_journal(), "registration_meter_closed", body | {
                "attempt": attempt, "validation_s": sealed, "tail_allowance_s": 0.})
            if time.monotonic() - started > sealed:
                raise ValueError("meter seal exceeded; owner cost recovery required")
        except Exception:
            failure = body | {"attempt": attempt, "validation_s": max(0., time.monotonic() - started)}
            _append(_journal(), "registration_meter_failure", failure)
            raise
        # A legacy close is diagnostic only and carries no replacement protocol.
        try:
            _append(_journal(), "registration_closed", body | {"attempt": attempt, "validation_s": elapsed})
        except Exception:
            _append(_journal(), "registration_close_failed", body | {
                "attempt": attempt, "validation_s": max(0., time.monotonic() - started)})


def _decide_add(candidates, library, registration):
    started = time.monotonic()
    candidates = tuple(candidates)
    candidate_ids = tuple(c.candidate_id if type(c) is VerifierCandidate else "invalid" for c in candidates)
    published, results = {}, None
    try:
        with _writer():
            with _add_attempt(registration, candidate_ids, started) as attempt:
                results = _decide_batch_inner(candidates, library, registration, replacement=False,
                                             attempt=attempt, pending={}, published=published)
        return results
    except Exception as error:
        if results is not None:
            return tuple(published.get(ident, answer) for ident, answer in zip(candidate_ids, results))
        if not isinstance(error, (ValueError, FileNotFoundError)):
            raise
        reason = str(error) if isinstance(error, ValueError) else "evaluator input unavailable"
        if reason == "nonempty batch of distinct candidates required":
            raise
        return tuple(AcceptanceDecision("block", (reason,), getattr(c, "candidate_id", "")) for c in candidates)


def _decide_batch_metered(candidates, library, registration, *, replacement, opening_clocks=None):
    started, wall = opening_clocks if opening_clocks is not None else (time.monotonic(), time.time())
    interval_id = uuid4().hex
    opening = {"registration": registration, "monotonic_start": started, "wall_start": wall,
               "interval_version": 1, "state": "opened", "phase": "assessment",
               "authority_required": replacement, "interval_id": interval_id}
    meter = getattr(_BOOKKEEPING, "meter", None)
    if meter is not None:
        meter["interval_id"] = interval_id
    journal = None
    try:
        opening["d_append_s"] = _append_deadline(registration)
    except (ValueError, OSError):
        return tuple(AcceptanceDecision("block", ("registered endpoint deadline unavailable",)) for _ in candidates)
    try:
        if replacement:
            opening["boot_id"] = _bookkeeping_call("opening_boot", _boot_identity)
        journal = _acquire_interval(interval_id, enforce=False)
        attempt_id = _endpoint_append(journal, "replacement_attempt_start", opening)
        if meter is not None and meter["bad"]:
            raise ValueError("registration bookkeeping incomplete; owner cost recovery required")
    except Exception:
        recorded = False
        try:
            if journal is None:
                journal = _acquire_interval(interval_id, enforce=False)
            starts = [r for r in journal.entries() if r["kind"] == "replacement_attempt_start"
                      and r["body"].get("monotonic_start") == opening["monotonic_start"]]
            attempt_id = starts[-1]["id"] if starts else _endpoint_append(journal, "registration_open_failed",
                opening | {"validation_s": _interval_elapsed(opening), "monotonic_end": time.monotonic()})
            recorded = True
            _interval_failure(interval_id, opening, attempt_id)
        except Exception:
            pass
        if replacement and not recorded:
            _UNRECORDED_BOOKKEEPING[_unrecorded_key(registration)] = _diagnostic_body(opening)
        return tuple(AcceptanceDecision("block", ("interval opening requires owner cost recovery",),
                     c.candidate_id if type(c) is VerifierCandidate else "") for c in candidates)
    candidates = tuple(candidates)
    candidate_ids = [c.candidate_id if type(c) is VerifierCandidate else "invalid" for c in candidates]
    pending, published, receipts = {}, {}, []
    results, meter_error = None, None
    completion_attempted = False
    try:
        with _bookkeeping_writer():
            results = _decide_batch_inner(candidates, library, registration, replacement=replacement,
                attempt=(started, attempt_id), pending=pending, published=published)
            completion_attempted = True
            _write(_acquire_interval(interval_id), "replacement_attempt", opening | {
                "attempt": attempt_id, "validation_s": _interval_elapsed(opening)})
            _write(_acquire_interval(interval_id), "registration_meter_closed", opening | {
                "state": "assessed", "attempt": attempt_id, "validation_s": _interval_elapsed(opening)})
            if replacement and pending:
                _reconcile_validation(registration, attempt_id)
            for index, ident in enumerate(candidate_ids):
                if ident not in pending:
                    continue
                answer, frozen, data = pending[ident]
                try:
                    total = (_reconcile_validation(registration, attempt_id) + _interval_elapsed(opening)
                             + _endpoint_allowance(opening))
                    active = _final_replacement_cost(data, frozen.replacement, total)
                    active["replacement"]["state"] = "active"
                    receipt = _write(_gate_journal(), "replacement_state", {
                        "state": "active", "entry_id": answer.entry_id, "registration": registration,
                        "attempt": attempt_id, "candidate_id": ident,
                        "pending_receipt": digest(data["replacement"]), "report": active})
                    _reconcile_validation(registration, attempt_id)
                    receipts.append(receipt)
                except ValueError as error:
                    answer = replace(answer, verdict="refuse", reasons=(str(error),))
                results = (*results[:index], answer, *results[index + 1:])
    except Exception as error:
        if str(error) == "nonempty batch of distinct candidates required":
            raise
        if results is not None:
            meter_error = type(error).__name__
            receipts.clear()
        elif not isinstance(error, (ValueError, OSError)):
            raise
        if replacement or results is None:
            reason = str(error) if isinstance(error, ValueError) else "registration operation unavailable"
            results = tuple(AcceptanceDecision("block", (reason,), ident) for ident in candidate_ids)
    finally:
        can_close = True
        if not completion_attempted:
            try:
                _write(_acquire_interval(interval_id), "replacement_attempt", opening | {
                    "attempt": attempt_id, "validation_s": _interval_elapsed(opening)})
            except Exception as error:
                meter_error = type(error).__name__
        if meter_error is not None:
            try:
                _endpoint_append(_acquire_interval(interval_id), "registration_meter_failure", opening | {
                    "attempt": attempt_id, "error_type": meter_error})
            except Exception:
                can_close = False
        try:
            if replacement and pending and results is not None:
                total = (_reconcile_validation(registration, attempt_id) + _interval_elapsed(opening)
                         + _endpoint_allowance(opening))
                for index, ident in enumerate(candidate_ids):
                    if ident not in pending:
                        continue
                    answer, frozen, data = pending[ident]
                    try:
                        _final_replacement_cost(data, frozen.replacement, total)
                    except ValueError as error:
                        receipts.clear()
                        answer = replace(answer, verdict="refuse", reasons=(str(error),))
                        if results[index].verdict in {"admit_probation", "admit_scoped"}:
                            results = (*results[:index], answer, *results[index + 1:])
        except ValueError as error:
            receipts.clear()
            if replacement and results is not None:
                results = tuple(replace(answer, verdict="block", reasons=(str(error),)) for answer in results)
        if not replacement and results is not None:
            results = tuple(published.get(ident, answer) for ident, answer in zip(candidate_ids, results))
        try:
            if not can_close:
                raise ValueError("registration journal requires owner cost recovery")
            ended, wall_end = time.monotonic(), time.time()
            elapsed = _interval_elapsed(opening)
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("registration clock requires owner cost recovery")
            journal = _acquire_interval(interval_id)
            close_started = time.monotonic()
            close = _endpoint_append(journal, "registration_closed", opening | {
                "state": "closed", "attempt": attempt_id, "validation_s": elapsed,
                "monotonic_end": ended, "wall_end": wall_end,
                "candidate_ids": candidate_ids, "activation_receipts": receipts})
            if replacement:
                identity = _bookkeeping_call("live_close_identity", lambda: _journal_identity(journal, attempt_id))
                meter["live_close"] = (identity, close)
                _confirm_interval(journal, close, close_latency_s=time.monotonic() - close_started,
                                  pending=pending.values() if receipts else ())
        except Exception:
            _interval_failure(interval_id, opening, attempt_id)
            if replacement and results is not None:
                results = tuple(replace(answer, verdict="block", reasons=("interval close requires owner cost recovery",))
                                for answer in results)
    return results


def _decide_batch_inner(candidates, library, registration, *, replacement, attempt, pending, published):
    assess = _assess_replacement if replacement else _assess
    originals = tuple(candidates)
    try:
        if any(type(c) is not VerifierCandidate for c in originals):
            raise ValueError("typed candidates required")
        candidates = tuple(VerifierCandidate.model_validate(c.model_dump(mode="json")) for c in originals)
    except (ValueError, TypeError):
        return tuple(AcceptanceDecision("block", ("invalid candidate identity or contract",)) for _ in originals)
    if not candidates or len({c.candidate_id for c in candidates}) != len(candidates):
        raise ValueError("nonempty batch of distinct candidates required")
    if replacement and len(candidates) != 1:
        return tuple(AcceptanceDecision("block", ("joint activation requires a registered batch protocol",),
                                       c.candidate_id) for c in candidates)
    try:
        if stop.engaged() is not None:
            return tuple(_record(AcceptanceDecision("block", ("stop engaged",), c.candidate_id)) for c in candidates)
        _policy()
        if type(library) is not Library:
            raise ValueError("gate requires persisted library")
        results, evaluated = {}, {}
        # No evidence-bearing callback, configurable runner or external report.
        for candidate in candidates:
            reservation = ""
            try:
                if replacement:
                    _reconcile_validation(registration, attempt[1])
                if not replacement or _reusable_look(registration, candidate.candidate_id) is None:
                    _look_available(registration)  # A spent split never reaches the oracle again.
                frozen = _load(registration, candidate, library, True) if replacement else _load(registration, candidate, library)
                if Stores().for_model(VerifierCandidate).get(candidate.candidate_id) != candidate:
                    raise ValueError("candidate differs from persisted proposal")
                if replacement and frozen.replacement is None:
                    raise ValueError("registered replacement traces required")
                index, reservation = _spend(registration, candidate, attempt[1])
                if replacement:
                    answer, evidence, own, data = assess(candidate, library.list(), frozen, index,
                        prior_validation_s=_reconcile_validation(registration, attempt[1]), validation_started=attempt[0])
                else:
                    answer, evidence, own, data = assess(candidate, library.list(), frozen, index)
                evaluated[candidate.candidate_id] = (answer, evidence, own, frozen, data)
            except Inconclusive as error:
                answer = AcceptanceDecision("inconclusive", (str(error),), candidate.candidate_id)
            except (ValueError, KeyError, FileNotFoundError) as error:
                # Do not echo corpus bodies, subprocess stderr or model output.
                reason = str(error) if isinstance(error, ValueError) else "registered input missing"
                verdict = "refuse" if reservation or "split spent" in reason or "cost recovery" in reason else "block"
                answer = AcceptanceDecision(verdict, (reason,), candidate.candidate_id)
            results[candidate.candidate_id] = (answer, reservation)
            # Close the reservation before spending the next look. A later
            # activation is a separate append-only decision row.
            _record(replace(answer, verdict="evaluated") if answer.verdict in {"admit_probation", "admit_scoped"}
                    else answer, reservation)
        accepted = _bh(((v[0].p_value, ident) for ident, v in evaluated.items()), len(candidates))
        for candidate in sorted(candidates, key=lambda c: c.candidate_id):
            ident = candidate.candidate_id
            if ident not in evaluated:
                continue
            answer, evidence, own, frozen, data = evaluated[ident]
            if replacement:
                try:
                    if ident not in accepted:
                        raise ValueError("batch Benjamini-Hochberg bar not met")
                    answer, data = _activate_replacement(candidate, library, frozen, evidence,
                        .05 * len(accepted) / len(candidates), attempt)
                    pending[ident] = (answer, frozen, data)
                except Inconclusive as error:
                    answer = replace(answer, verdict="inconclusive", reasons=(str(error),))
                    _record(answer)
                except LedgerError:
                    raise
                except ValueError as error:
                    answer = replace(answer, verdict="refuse", reasons=(str(error),))
                    _record(answer)
                results[ident] = (answer, "")
                continue
            try:
                if ident not in accepted:
                    raise ValueError("batch Benjamini-Hochberg bar not met")
                # Earlier members of this batch may already have changed L_t.
                answer, evidence, own, data = assess(candidate, library.list(), frozen, evidence.reuse_index)
                if answer.p_value > .05 * len(accepted) / len(candidates):
                    raise ValueError("batch bar not met after library changed")
                answer = _persist(candidate, answer, evidence, own, frozen, data, library)
                published[ident] = answer
            except Inconclusive as error:
                answer = replace(answer, verdict="inconclusive", reasons=(str(error),))
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
    labels = {}
    for row in rows:
        body = FieldOutcome.model_validate(row["body"])
        if row["kind"] != "field" or body.candidate_id != candidate_id:
            raise ValueError("independent field receipt required; prediction evidence refused")
        old = labels.get(body.fire_id)
        if old is not None and (old != "unlabelled" or body.label == "unlabelled"):
            raise ValueError("field fire already labelled; duplicate or relabel refused")
        labels[body.fire_id] = body.label
    for label in labels.values():
        counts[label] += 1
    return counts["confirmed"], counts["refuted"], counts["unlabelled"]


def _field_evidence(candidate_id):
    confirmed, refuted, unlabelled = _field(candidate_id)
    return FieldEvidence(fired=confirmed + refuted + unlabelled, confirmed=confirmed, refuted=refuted,
        unlabelled=unlabelled, precision=_interval(confirmed, confirmed + refuted) if confirmed + refuted else None)


def _a2_bar(evidence, field):
    if (evidence.n_pos + evidence.n_neg < 30 or evidence.n_neg < 10
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
    if authority != "A2" and authority < entry.authority:
        data.update(replacement="none", replacement_scope=None, teacher=None, noninferiority=None)
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


@dataclass(frozen=True)
class _ClusterValue:
    value: float
    repository: str
    weight: float = 1.


def _kl_upper(point, budget):
    if point >= 1:
        return 1.
    if point <= 0:
        return -math.expm1(-budget)
    low, high = point, 1.
    for _ in range(60):
        middle = (low + high) / 2
        if middle == 1.:
            break
        divergence = point * math.log(point / middle) + (1 - point) * math.log((1 - point) / (1 - middle))
        if divergence <= budget:
            low = middle
        else:
            high = middle
    return high


def _margin_bound(values, margin, alpha, *, value_bounds=(-1., 1.)):
    """Unit-weighted mean with repository sandwich and bounded tail intervals.

    Repository sizes and value limits are fixed before observing outcomes.
    For independent clusters, bounded Chernoff tails remain valid with unequal
    weights: their KL budget is max(cluster weight) * log(1 / tail level).
    Split positive and negative parts share the tail level by a union bound.
    The sandwich interval can only widen the bounded interval.
    """
    samples = [v if isinstance(v, _ClusterValue) else _ClusterValue(v, str(i)) for i, v in enumerate(values)]
    lo, hi = value_bounds
    if (not samples or not all(math.isfinite(v) for v in (lo, hi, margin)) or not lo < hi
            or not 0 < alpha < .5 or any(not math.isfinite(s.value) or not lo <= s.value <= hi
            or not math.isfinite(s.weight) or s.weight <= 0 for s in samples)):
        raise ValueError("finite measurements within registered bounds required")
    total = math.fsum(s.weight for s in samples)
    groups = {}
    for sample in samples:
        groups.setdefault(sample.repository, []).append(sample)
    m = len(groups)
    if m < EvidencePolicy().min_cases:
        raise Inconclusive("registered minimum independent repositories unavailable")
    clusters = []
    for group in groups.values():
        mass = math.fsum(s.weight for s in group)
        clusters.append((mass / total, math.fsum(s.weight * s.value for s in group) / mass))
    point = math.fsum(w * v for w, v in clusters)
    se = math.sqrt(m / (m - 1) * math.fsum((w * (v - point)) ** 2 for w, v in clusters))
    max_weight = max(w for w, _ in clusters)
    positive_scale, negative_scale = max(hi, 0.), max(-lo, 0.)
    positive = math.fsum(w * max(v, 0.) for w, v in clusters) / positive_scale if positive_scale else 0.
    negative = math.fsum(w * max(-v, 0.) for w, v in clusters) / negative_scale if negative_scale else 0.

    def limits(level):
        budget = max_weight * math.log(2 / level)
        upper = positive_scale * _kl_upper(positive, budget) - negative_scale * (1 - _kl_upper(1 - negative, budget))
        lower = positive_scale * (1 - _kl_upper(1 - positive, budget)) - negative_scale * _kl_upper(negative, budget)
        z = statistics.NormalDist().inv_cdf(1 - level)
        return max(lo, min(lower, point - z * se)), min(hi, max(upper, point + z * se))

    lower, upper = limits(alpha)
    p = 1.
    if margin > point and limits(.499999)[1] <= margin:
        low, high = 1e-15, .499999
        for _ in range(48):
            middle = (low + high) / 2
            if limits(middle)[1] <= margin:
                high = middle
            else:
                low = middle
        p = high
    return {"point": point, "lower": lower, "upper": upper, "p_value": p,
            "repositories": m, "margin": margin, "max_repository_weight": max_weight,
            "cluster_se": se, "value_bounds": [lo, hi]}


def _replacement_cost(fixed_difference, savings, *, value_bounds):
    if not math.isfinite(fixed_difference):
        raise ValueError("finite cumulative cost measurements required")
    samples = [v if isinstance(v, _ClusterValue) else _ClusterValue(v, str(i)) for i, v in enumerate(savings)]
    if any(not value_bounds[0] <= s.value <= value_bounds[1] for s in samples):
        raise ValueError("cumulative cost measurements exceed registered task saving bounds")
    interval = _margin_bound([replace(s, value=-s.value) for s in samples], -fixed_difference / 1000, .05,
                             value_bounds=(-value_bounds[1], -value_bounds[0]))
    saving = -interval["point"]
    point = fixed_difference + 1000 * interval["point"]
    upper = fixed_difference + 1000 * interval["upper"]
    kind = ("dominates" if fixed_difference <= 0 <= saving else
            "break_even" if fixed_difference > 0 and saving > 0 else
            "crossover" if fixed_difference < 0 and saving < 0 else "never")
    return {"horizon": 1000, "fixed_difference": fixed_difference, "per_task_saving": saving,
            "point": point, "upper_95": upper, "admissible": upper <= 0, "interval": interval,
            "kind": kind, "crossing": fixed_difference / saving if kind in {"break_even", "crossover"} else None}


def _cluster_differences(observations, rows, values):
    return [_ClusterValue(value, rows[o.case.case_id].repository) for o, value in zip(observations, values)]


def _headline(observations, policy):
    headline, strata = [], {"witness_positive": 0, "n1": 0, "n2": 0, "shared_mechanism": 0}
    for o in observations:
        result = o.oracle_result
        if not result or result.get("case_id") != o.case.case_id or result.get("label") != o.label:
            raise ValueError("independent oracle receipt missing or mismatched")
        kind = ("witness_positive" if result.get("wrong_witnesses") else "shared_mechanism") if o.label == "positive" else (
            _negative_justification(o.case, policy))
        strata[kind] += 1
        if kind != "shared_mechanism":
            headline.append(o)
    return tuple(headline), strata


def _error_differences(observations, rows, indices, before, proposed, expected, *, fp=False):
    units = {}
    for i in indices:
        o = observations[i]
        units.setdefault((rows[o.case.case_id].repository, o.case.unit_id), []).append((before[i], proposed[i]))
    differences = []
    for (repository, _), pairs in units.items():
        old, new = zip(*pairs)
        old_error = any(v == "concern" if fp else v != expected for v in old)
        new_error = any(v == "concern" if fp else v != expected for v in new)
        differences.append(_ClusterValue(float(new_error) - float(old_error), repository))
    return differences


def _replacement_quality(observations, rows, teacher, base, after, own, scope, alpha):
    bounds, graph_values = {}, []
    for label, error in (("positive", "FN"), ("negative", "FP")):
        indices = [i for i, o in enumerate(observations) if o.label == label]
        if not indices:
            raise Inconclusive("registered minimum positive and negative units unavailable")
        expected = "concern" if label == "positive" else "no_concern"
        values = _error_differences(observations, rows, indices, base, after, expected)
        graph_values.extend(replace(v, weight=.5 / len(values)) for v in values)
        inside = [i for i in indices if scope[i]]
        values = _error_differences(observations, rows, inside, teacher, own, expected, fp=label == "negative")
        if not values:
            raise Inconclusive("registered minimum independent repositories unavailable")
        bounds[error] = _margin_bound(values, .02, .05)
        if bounds[error]["point"] > .02:
            raise ValueError(f"replacement {error} difference breach")
        if bounds[error]["upper"] > .02:
            raise Inconclusive(f"replacement {error} bound exceeds margin")
    bounds["graph_loss"] = _margin_bound(graph_values, .03, alpha)
    if bounds["graph_loss"]["point"] > .03:
        raise ValueError("whole-graph non-inferiority breach")
    if bounds["graph_loss"]["upper"] > .03:
        raise Inconclusive("whole-graph non-inferiority unresolved")
    return bounds, max(b["p_value"] for b in bounds.values())


def _replacement_fpr(observations, rows, base, after, name):
    indices = [i for i, o in enumerate(observations) if o.label == "negative"]
    values = _error_differences(observations, rows, indices, base, after, "no_concern", fp=True)
    if not values:
        raise Inconclusive("registered minimum monitor negatives unavailable")
    bound = _margin_bound(values, .02, .05)
    if bound["point"] > .02:
        raise ValueError(f"{name} library FPR margin exceeded")
    if bound["upper"] > .02:
        raise Inconclusive(f"{name} library FPR margin unresolved")
    return bound | {"diff_upper_95": bound["upper"]}


def _replacement_scope(candidate, study):
    if study is None:
        raise ValueError("registered replacement traces required")
    if study.candidate_id != candidate.candidate_id:
        raise ValueError("replacement traces bind a different candidate")
    if study.scope is None:
        raise ValueError("explicit registered effective scope required")
    if any(p not in study.scope for p in candidate.contract.applies_when):
        raise ValueError("registered replacement scope must narrow the proposed scope")
    limits = study.task_saving_bounds
    if limits is None or not all(math.isfinite(v) for v in limits) or not limits[0] < limits[1]:
        raise ValueError("registered finite task saving bounds required")
    return study.scope


def _assess_replacement(candidate, entries, frozen, reuse_index, *, prior_validation_s=0., validation_started=None):
    started = time.monotonic() if validation_started is None else validation_started
    _candidate_boundary(candidate)
    policy, _ = _policy()
    if not 1 <= reuse_index <= policy.max_evaluations:
        raise ValueError("held-out split spent")
    if frozen.replacement is None:
        raise ValueError("registered replacement traces required")
    study = ReplacementStudy.model_validate(frozen.replacement.model_dump(mode="json"))
    if study.candidate_id != candidate.candidate_id:
        raise ValueError("replacement traces bind a different candidate")
    if study.registration_sha256 != frozen.registration_sha256:
        raise ValueError("replacement traces bind a different registration")
    proposed = candidate.contract.applies_when
    scope = _replacement_scope(candidate, study)
    rows = {r.case_id: r for r in study.rows}
    if len(rows) != len(study.rows):
        raise ValueError("duplicate teacher case trace")
    fresh = (*frozen.acceptance, *frozen.monitor)
    all_observations = (*fresh, *frozen.regression, *frozen.tuning)
    if any(o.label not in {"positive", "negative"} for o in all_observations):
        raise ValueError("settled replacement cases required")
    if set(candidate.training_case_ids) & {o.case.case_id for o in fresh}:
        raise ValueError("training cases cannot be replacement evidence")
    if _leaks(candidate, fresh):
        raise ValueError("candidate leakage guard refused")
    required = {o.case.case_id for o in (*fresh, *frozen.tuning)}
    if required - rows.keys():
        raise ValueError("teacher trace missing for registered cases")
    unit_repositories = {}
    for o in (*frozen.acceptance, *frozen.monitor):
        repo = rows[o.case.case_id].repository
        if unit_repositories.setdefault(o.case.unit_id, repo) != repo:
            raise ValueError("unit spans conflicting repositories")
    acceptance_repos = {rows[o.case.case_id].repository for o in frozen.acceptance}
    monitor_repos = {rows[o.case.case_id].repository for o in frozen.monitor}
    if acceptance_repos & monitor_repos:
        raise ValueError("replacement acceptance and monitor repositories overlap")
    observations, strata = _headline(frozen.acceptance, frozen.oracle_policy)
    monitor, monitor_strata = _headline(frozen.monitor, frozen.oracle_policy)
    tuning, tuning_strata = _headline(frozen.tuning, frozen.oracle_policy)
    if len({rows[o.case.case_id].repository for o in observations}) < policy.min_cases:
        raise Inconclusive("registered minimum independent repositories unavailable")
    if not monitor:
        raise Inconclusive("registered minimum monitor evidence unavailable")

    def measure(group):
        own, costs = _measure(_GuardedCandidate(candidate, scope, rows), group, entries)
        own = [v[0] if isinstance(v, tuple) else v for v in own]
        inside = [_in_scope(scope, _extract_facts({"tests/test_case.py": o.case.before_tests},
                   {"tests/test_case.py": o.case.after_tests})) for o in group]
        own = [v if s else "abstain" for v, s in zip(own, inside)]
        teacher = [rows[o.case.case_id].teacher_verdict for o in group]
        library = _library_verdicts(entries, group)
        before = [_union([lib, t]) for lib, t in zip(library, teacher)]
        after = [_union([lib, t if q == "abstain" else q]) for lib, t, q in zip(library, teacher, own)]
        return own, costs, inside, teacher, before, after

    all_own, all_costs, all_inside, all_teacher, all_base, all_combined = measure(frozen.acceptance)
    indices = [i for i, o in enumerate(frozen.acceptance) if o in observations]
    own, inside, teacher, base, combined = ([values[i] for i in indices] for values in
                                           (all_own, all_inside, all_teacher, all_base, all_combined))
    costs = [v for i in indices for v in all_costs[3*i:3*i+3]]
    coverage_groups = {}
    for o, v in zip(frozen.acceptance, all_own):
        coverage_groups.setdefault(o.case.unit_id, []).append(v != "abstain")
    covered = sum(all(v) for v in coverage_groups.values())
    coverage = _interval(covered, len(coverage_groups))
    if coverage.point < .10:
        raise ValueError("replacement coverage below 0.10")
    bounds, p = _replacement_quality(observations, rows, teacher, base, combined, own, inside, policy.threshold)
    fpr = _replacement_fpr(observations, rows, base, combined, "held-out")
    planted = {o.case.case_id: (o.label, v) for o, v in zip(observations, own)}
    if (planted.get(frozen.controls[0]) != ("positive", "concern")
            or planted.get(frozen.controls[1]) != ("negative", "no_concern")):
        raise ValueError("registered planted controls failed")
    _, success = _unit_pairs(observations, base, combined)
    for trivial in ("concern", "no_concern"):
        control, _ = _unit_pairs(observations, [trivial] * len(observations), combined)
        test = _sign(control, success)
        if test["wins"] <= test["losses"] or test["p_one_sided"] > policy.alpha:
            raise ValueError("trivial controls do not fail the acceptance bar")
    _regress(entries, all_observations, candidate, scope=scope)
    dev_monitor = (*tuning, *monitor)
    _, _, _, _, monitor_base, monitor_after = measure(dev_monitor)
    if not any(o.label == "negative" for o in monitor):
        raise Inconclusive("registered minimum monitor negatives unavailable")
    margin = _replacement_fpr(dev_monitor, rows, monitor_base, monitor_after, "dev plus monitor")
    # Three runs per case are measured by the gate, including failed probe time.
    serving = [statistics.fmean(all_costs[3*i:3*i+3]) for i in range(len(frozen.acceptance))]
    savings = [(rows[o.case.case_id].teacher_s * study.teacher_weight * (1 - study.shadow_rate)
                if q != "abstain" else 0.) - c * study.cpu_weight - rows[o.case.case_id].maintenance_delta_s
               for o, q, c in zip(frozen.acceptance, all_own, serving)]
    validation_s = prior_validation_s + _assessment_elapsed(started) + 2 * _append_deadline(frozen.registration_sha256)
    cost_result = _replacement_cost(study.fixed_new_s - study.fixed_base_s + validation_s * study.cpu_weight,
                                    _cluster_differences(frozen.acceptance, rows, savings),
                                    value_bounds=study.task_saving_bounds)
    if not cost_result["admissible"]:
        raise ValueError("cumulative cost upper bound at H is not lower")
    measured = _counts(observations, own)
    cost = Cost(median_s=statistics.median(costs), p95_s=sorted(costs)[math.ceil(.95 * len(costs)) - 1])
    replacement = {"teacher": study.teacher, "scope": [s.model_dump(mode="json") for s in scope],
                   "noninferiority": bounds, "coverage": coverage.model_dump(mode="json"),
                   "cost": cost_result, "strata": strata, "monitor_strata": monitor_strata,
                   "tuning_strata": tuning_strata,
                   "shadow_rate": study.shadow_rate, "shadow_seed": study.shadow_seed,
                   "cpu_weight": study.cpu_weight, "teacher_weight": study.teacher_weight,
                   "trace_sha256": digest(study.model_dump(mode="json")),
                   "validation_s": validation_s}
    data = {"candidate_id": candidate.candidate_id, "registration": frozen.registration_sha256,
            "split": frozen.split_sha256, "policy": _policy_digest(), "reuse_index": reuse_index,
            "judging_sha256": _judging_digest(), "counts": measured, "cost": cost.model_dump(mode="json"),
            "replacement": replacement, "fpr": fpr, "regression_fpr": margin,
            "meta": {"n": len(acceptance_repos)}}
    evidence = AcceptanceEvidence(acceptance_report_sha256=digest(data), policy_sha256=_policy_digest(),
        split_sha256=frozen.split_sha256, reuse_index=reuse_index, **measured,
        precision=_interval(measured["tp"], measured["tp"] + measured["fp"]),
        recall=_interval(measured["tp"], measured["n_pos"]),
        false_rejection=_interval(measured["fp"], measured["n_neg"]), cost=cost)
    narrowed = scope != proposed
    answer = AcceptanceDecision("admit_scoped" if narrowed else "admit_probation",
        ("independent replacement bounds and cost at H met; scoped authority only",),
        candidate.candidate_id, digest(data), p_value=p)
    # Archive all cases, including shared-mechanism cases, with their actual verdict.
    return answer, evidence, all_own, data
