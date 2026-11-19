"""Exploratory pilot decisions stay separate from production authority."""
from dataclasses import asdict, replace
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess

import pytest

from g6.core.ledger import digest
from g6.distill import acceptance_gate as production
from g6.distill import records
from g6.distill.library import Library
from tests.distill import test_acceptance_measurements as fixtures


@pytest.fixture
def pilot():
    return pytest.importorskip("g6.distill.pilot_gate")


def test_pilot_module_exists():
    assert find_spec("g6.distill.pilot_gate") is not None, "separate pilot gate is missing"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    _, candidate, source, make = fixtures.sample.__wrapped__()

    def labelled(i, positive, split):
        o = make(i, positive, split=split)
        return replace(o, oracle_result={"case_id": o.case.case_id, "label": o.label,
            "wrong_witnesses": ["d" * 64] if positive else []})

    admission = tuple(labelled(i, i < 5, "acceptance") for i in range(10))
    monitor = tuple(labelled(i + 20, i < 2, "monitor") for i in range(4))
    rows = [production.TeacherObservation(case_id=o.case.case_id,
        repository=f"admission-{i % 5}" if o.split == "acceptance" else f"monitor-{i % 3}",
        teacher_verdict="concern" if o.label == "positive" else "no_concern",
        teacher_s=10, maintenance_delta_s=.01) for i, o in enumerate((*admission, *monitor))]
    study = production.ReplacementStudy(teacher="c" * 64, candidate_id=candidate.candidate_id,
        registration_sha256=source.registration_sha256, shadow_seed="e" * 64, fixed_new_s=10,
        fixed_base_s=0, rows=rows, scope=[], task_saving_bounds=(-2., 12.))
    frozen = replace(source, acceptance=admission, monitor=monitor, replacement=study,
                     controls=(admission[0].case.case_id, admission[-1].case.case_id))
    allocation = {"cohort": "development", "label": "synthetic-pilot-v1", "roles": {
        "admission": {"repositories": [f"admission-{i}" for i in range(5)],
                      "bugs": [o.case.unit_id for o in admission]},
        "monitor": {"repositories": [f"monitor-{i}" for i in range(3)],
                    "bugs": [o.case.unit_id for o in monitor]}}}
    loaded = []

    def load(*args):
        loaded.append(args)
        return box["frozen"]

    box = {"frozen": frozen, "candidate": candidate, "allocation": allocation,
           "root": tmp_path / "study", "loaded": loaded}
    monkeypatch.setattr(production, "_load", load)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "production"))
    return box


def decide(pilot, rig):
    return pilot.decide_pilot(rig["candidate"], study_root=rig["root"], allocation=rig["allocation"],
                              registration=rig["frozen"].registration_sha256)


def test_frozen_production_files_have_no_diff():
    root = Path(__file__).resolve().parents[2]
    base = subprocess.check_output(["git", "merge-base", "HEAD", "claude/distill-gate-v3"],
                                   cwd=root, text=True).strip()
    changed = subprocess.check_output(["git", "diff", base, "--",
        "src/g6/distill/acceptance_gate.py", "src/g6/distill/rectify_gate.py",
        "src/g6/harness/library_review_gate.py"], cwd=root, text=True)
    assert changed == ""


def test_sparse_pilot_admits_with_unchanged_production_outcome(pilot, rig):
    result = decide(pilot, rig)
    assert result.pilot_verdict == "admit"
    assert result.production_decision.verdict == "inconclusive"
    assert "minimum independent repositories" in result.production_decision.reasons[0]
    assert result.exploratory is True
    assert result.pilot_version == pilot.PILOT_GATE_VERSION
    assert result.policy_sha256 == digest(asdict(pilot.PilotPolicy()))
    assert result.allocation_sha256 == digest(rig["allocation"])
    assert result.measurements["production_quality"]["verdict"] == "inconclusive"
    assert result.measurements["production_cost"]["verdict"] == "inconclusive"
    assert result.measurements["cost"]["point"] < 0
    assert rig["loaded"][0][3] is True


@pytest.mark.parametrize("condition,code", [
    ("minimum", "PILOT_REPOSITORIES"), ("quality", "PILOT_QUALITY"),
    ("false_acceptance", "PILOT_FALSE_ACCEPTANCE"), ("monitor", "PILOT_MONITOR_FP"),
    ("cost", "PILOT_COST"), ("fixed", "PILOT_COST_FIXED"),
    ("saving", "PILOT_COST_BOUNDS"), ("overlap", "PILOT_REPOSITORY_OVERLAP"),
    ("training", "PILOT_TRAINING"), ("unsettled", "PILOT_LABELS"),
    ("trace", "PILOT_TEACHER_TRACE"), ("unit", "PILOT_UNIT_REPOSITORY"),
    ("allocation", "PILOT_ALLOCATION"), ("leak", "PILOT_LEAKAGE"),
])
def test_conditions_refuse_with_stable_codes(pilot, rig, monkeypatch, condition, code):
    frozen = rig["frozen"]
    rows = list(frozen.replacement.rows)
    original = production._measure

    def measure(candidate, observations, entries):
        values, costs = original(candidate, observations, entries)
        for i, o in enumerate(observations):
            if condition == "quality" and o == frozen.acceptance[0]:
                values[i] = "abstain"
            if condition == "false_acceptance" and o == frozen.acceptance[0]:
                values[i] = "no_concern"
            if condition == "monitor" and o == frozen.monitor[-1]:
                values[i] = "concern"
        return values, costs

    monkeypatch.setattr(production, "_measure", measure)
    if condition == "minimum":
        rows = [r.model_copy(update={"repository": "admission-0"})
                if i < 10 else r for i, r in enumerate(rows)]
    if condition == "overlap":
        rows[-1] = rows[-1].model_copy(update={"repository": "admission-0"})
    if condition == "trace":
        rows.pop()
    if condition == "unit":
        changed = replace(frozen.acceptance[1], case=frozen.acceptance[1].case.model_copy(
            update={"unit_id": frozen.acceptance[0].case.unit_id}))
        changed = replace(changed, oracle_result=changed.oracle_result | {"case_id": changed.case.case_id})
        rows[1] = rows[1].model_copy(update={"case_id": changed.case.case_id})
        frozen = replace(frozen, acceptance=(frozen.acceptance[0], changed, *frozen.acceptance[2:]))
    if condition == "training":
        rig["candidate"] = records.VerifierCandidate(**(rig["candidate"].model_dump(
            exclude={"candidate_id"}) | {"training_case_ids": [frozen.acceptance[0].case.case_id]}))
    if condition == "unsettled":
        frozen = replace(frozen, monitor=(replace(frozen.monitor[0], label="excluded"), *frozen.monitor[1:]))
    if condition == "allocation":
        rig["allocation"]["roles"]["admission"]["repositories"][0] = "unknown"
    if condition == "leak":
        monkeypatch.setattr(production, "_leaks", lambda *a: True)
    updates = {"rows": rows, "candidate_id": rig["candidate"].candidate_id}
    if condition == "cost":
        updates["fixed_new_s"] = 1e9
    if condition == "fixed":
        updates["fixed_new_s"] = float("inf")
    if condition == "saving":
        updates["task_saving_bounds"] = (-2., 1.)
    rig["frozen"] = replace(frozen, replacement=frozen.replacement.model_copy(update=updates))
    result = decide(pilot, rig)
    assert result.pilot_verdict == "refuse"
    assert result.code == code
    # A refusal admits nothing: no admission bundle. Since the step 4 repair, the candidate's cumulative
    # validation cost history is the only thing a refusal may leave under the study root.
    written = sorted(p.relative_to(rig["root"]).as_posix() for p in rig["root"].rglob("*") if p.is_file()) \
        if rig["root"].exists() else []
    assert not (rig["root"] / "pilot-gate").exists()
    assert all(p.startswith("pilot-validation/") and p.endswith((".jsonl", ".lock")) for p in written)


def test_study_only_storage_and_production_consumers(pilot, rig):
    from g6.distill.ledger import Stores
    from g6.harness import library_review_gate
    result = decide(pilot, rig)
    payload = json.loads(Path(result.admission_path).read_text(encoding="utf-8"))
    entry = records.LibraryEntry.model_validate(payload["entry"])
    assert Path(result.admission_path).is_relative_to(rig["root"])
    assert payload["authority"]["exploratory"] is True
    assert payload["authority"]["policy_sha256"] == result.policy_sha256
    assert entry.decided_by.gate_version == pilot.PILOT_GATE_VERSION
    assert Library().list() == []
    assert Stores().for_model(records.LibraryEntry).list() == []
    assert library_review_gate.check({"tests/test_x.py": "assert True"},
                                    {"tests/test_x.py": "pass"}) == []
    assert not isinstance(result, production.AcceptanceDecision)
    assert not hasattr(result, "verdict")
    answers = production._decide_batch_inner([result], Library(), "a" * 64, replacement=True,
        attempt=(0, ""), pending={}, published={})
    assert answers[0].verdict == "block"


def test_production_root_is_refused_before_loading(pilot, rig):
    from g6.core.storage import state_path
    rig["root"] = state_path("distill", "pilot-study")
    result = decide(pilot, rig)
    assert result.code == "PILOT_STORAGE_ROOT"
    assert not rig["loaded"]


def test_registration_loader_failure_is_not_bypassed(pilot, rig, monkeypatch):
    def unregistered(*args):
        raise ValueError("registration must predate concept")
    monkeypatch.setattr(production, "_load", unregistered)
    assert decide(pilot, rig).code == "PILOT_REGISTRATION"


def test_scoped_contract_is_assessed(pilot, rig):
    raw = rig["candidate"].model_dump(exclude={"candidate_id"})
    scope = [{"feature": "diff_hunks", "op": "ne", "value": {}}]
    raw["contract"] = raw["contract"] | {"applies_when": scope}
    raw["contract"].pop("content_id", None)
    rig["candidate"] = records.VerifierCandidate(**raw)
    rig["frozen"] = replace(rig["frozen"], replacement=rig["frozen"].replacement.model_copy(
        update={"candidate_id": rig["candidate"].candidate_id,
                "scope": [records.FeaturePredicate(**s) for s in scope]}))
    assert decide(pilot, rig).pilot_verdict == "admit"


def test_study_admission_cannot_be_replaced(pilot, rig):
    first = decide(pilot, rig)
    raw = Path(first.admission_path).read_bytes()
    second = decide(pilot, rig)
    assert second.code == "PILOT_REUSED"
    assert Path(first.admission_path).read_bytes() == raw


def test_probe_isolation_is_checked_before_measurement(pilot, monkeypatch):
    from tests.distill.test_probe_wiring import probe_candidate
    monkeypatch.delenv("G6_REQUIRE_ISOLATION", raising=False)
    monkeypatch.setattr(production, "_measure", lambda *a: pytest.fail("unisolated probe measured"))
    with pytest.raises(pilot.PilotRefused) as caught:
        pilot._measure(probe_candidate(), (), [])
    assert caught.value.code == "PILOT_ISOLATION"


def test_probe_contract_uses_wired_execution_under_isolation(pilot, rig, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from g6.core import confine
    from g6.distill import execution, probes
    from tests.distill.test_probe_wiring import probe_candidate
    candidate = probe_candidate()
    rig["candidate"] = candidate
    rig["frozen"] = replace(rig["frozen"], replacement=rig["frozen"].replacement.model_copy(
        update={"candidate_id": candidate.candidate_id}))
    calls, folders = [], []
    actual = execution.run_candidate

    def execute(*args, **kwargs):
        calls.append(args)
        return actual(*args, **kwargs)

    def folder(**kwargs):
        folders.append(kwargs)
        return nullcontext(tmp_path)

    def run(instance, before, after, **kwargs):
        return SimpleNamespace(verdict="concern" if "pass" in str(after) else "no_concern", reason="fake")

    monkeypatch.setattr(execution, "run_candidate", execute)
    monkeypatch.setattr(confine, "run_folder", folder)
    monkeypatch.setattr(probes, "_run_probe", run)
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    result = decide(pilot, rig)
    assert result.pilot_verdict == "admit", result
    assert calls and folders


@pytest.mark.parametrize("fixed,saving,kind,admit", [
    (-1, 0, "dominates", True), (10, 1, "break_even", True),
    (-1000, -1, "crossover", True), (10, 0, "never", False), (0, 0, "dominates", True),
])
def test_point_cost_rule_at_h(pilot, fixed, saving, kind, admit):
    result = pilot.pilot_point_cost(fixed, [saving] * 5, value_bounds=(-2., 12.), policy=pilot.PilotPolicy())
    assert result["point"] == fixed - 1000 * saving
    assert result["kind"] == kind
    assert result["admissible"] is admit


@pytest.mark.parametrize("fixed,savings,bounds,code", [
    (float("inf"), [1.], (-2., 12.), "PILOT_COST_FIXED"),
    (float("nan"), [1.], (-2., 12.), "PILOT_COST_FIXED"),
    (0., [13.], (-2., 12.), "PILOT_COST_BOUNDS"),
    (0., [-3.], (-2., 12.), "PILOT_COST_BOUNDS"),
    (0., [float("nan")], (-2., 12.), "PILOT_COST_BOUNDS"),
    (0., [float("inf")], (-2., 12.), "PILOT_COST_BOUNDS"),
    (0., [], (-2., 12.), "PILOT_COST_BOUNDS"),
    (0., [1.], (1., 1.), "PILOT_COST_BOUNDS"),
    (0., [1.], (-2., float("inf")), "PILOT_COST_BOUNDS"),
])
def test_cost_input_validation(pilot, fixed, savings, bounds, code):
    with pytest.raises(pilot.PilotRefused) as caught:
        pilot.pilot_point_cost(fixed, savings, value_bounds=bounds, policy=pilot.PilotPolicy())
    assert caught.value.code == code


def test_policy_is_frozen_and_bound_to_digest(pilot):
    from dataclasses import FrozenInstanceError
    policy = pilot.PilotPolicy()
    with pytest.raises(FrozenInstanceError):
        policy.admission_repositories = 1
    changed = replace(policy, admission_repositories=6)
    assert digest(asdict(policy)) != digest(asdict(changed))


def test_mcp_gate_result_consumer_rejects_pilot_shape(pilot, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from g6.mcp import consent, distill
    routes = {}

    class Server:
        def tool(self):
            def register(fn):
                routes[fn.__name__] = fn
                return fn
            return register

    async def accept(*args, **kwargs):
        return "accept", {"confirm": "change", "name": "Fixture person"}, None

    result = pilot.PilotDecision("refuse", "PILOT_INPUT",
        production.AcceptanceDecision("inconclusive", ("synthetic",)), "a" * 64,
        "b" * 64, "c" * 64, {})
    monkeypatch.setattr(distill.surface, "library", lambda *a: {
        "entry": {"entry_id": "a" * 64, "authority": "A1"}})
    monkeypatch.setattr(consent, "elicit", accept)
    monkeypatch.setattr(production, "demote", lambda *a, **kw: result)
    distill.register(Server(), object, None, backend=SimpleNamespace())
    with pytest.raises(AttributeError, match="verdict"):
        asyncio.run(routes["g6_library_decide"]("a" * 64, "demote", "synthetic", None))
