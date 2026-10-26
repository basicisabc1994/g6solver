# SPDX-License-Identifier: Apache-2.0
"""Coverage ledger R2 (founder brief §9): the operational contract fields, ratcheted and read by the scheduler.

R2-T1 is the ratchet: every route ``objective.STAGES`` names declares an ``operational`` block whose claims cite curated
known-answer evidence for that op, name only the failure surfaces of the classes that route it, use the closed state
vocabulary, and never touch the contract digest. Its survival control plants a contract that cites a test no oracle
lists, and the checker must raise. R2-T2 is ``scheduler.choose(requires=)`` alone: a route that does not mitigate the
failure mode its step requires is rejected with why, and an empty ``requires`` changes nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from g6.workforce import capabilities as wcap
from g6.workforce import objective, scheduler

ROOT = Path(__file__).resolve().parents[3]
CATALOGUE, CURATED_LIST = ROOT / "scripts" / "capability_map" / "oracles.json", ROOT / "ci" / "curated_tests.txt"
IN_SOURCE = CATALOGUE.is_file() and CURATED_LIST.is_file()
ORACLES = json.loads(CATALOGUE.read_text(encoding="utf-8")) if IN_SOURCE else {}
CURATED = ({line.strip() for line in CURATED_LIST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")} if IN_SOURCE else set())


def _cited(op: str) -> set[str]:
    """Every test the oracle catalogue lists for ``component.op``, of any kind."""
    return {node for nodes in (ORACLES.get(op) or {}).values() for node in nodes}


def _curated(node: str) -> bool:
    """The test file is curated (the gate runs it) and defines the named test."""
    path, _, name = node.partition("::")
    return path in CURATED and (ROOT / path).is_file() and f"def {name}(" in (ROOT / path).read_text(encoding="utf-8")


def check(contracts: dict, stages: dict | None = None, surfaces: dict | None = None) -> None:
    """The ratchet: raise ValueError naming every routed op whose operational block is missing or overclaims."""
    stages = objective.STAGES if stages is None else stages
    surfaces = objective.FAILURE_SURFACES if surfaces is None else surfaces
    routed: dict[tuple[str, str], set[str]] = {}
    for cls, by_stage in stages.items():
        for routes in by_stage.values():
            for route in routes:
                routed.setdefault((route["component"], route["op"]), set()).add(cls)
    problems = []
    for (component, op), classes in sorted(routed.items()):
        label = f"{component}.{op}"
        contract = contracts.get((component, op))
        block = getattr(contract, "operational", None)
        if not block:
            problems.append(f"{label}: no operational block")
            continue
        cited = _cited(label)
        for claim in block["mitigates_failure_modes"]:
            if claim["when"] == "" and not (claim["evidence"] in cited and _curated(claim["evidence"])):
                problems.append(f"{label}: {claim['failure_mode']} cites {claim['evidence']}, which is not a curated "
                                "oracle test of this op")
        named = {c["failure_mode"] for c in block["mitigates_failure_modes"]} | set(block["introduces_failure_modes"])
        for cls in sorted(classes):
            unknown = sorted(named - {s["id"] for s in surfaces.get(cls, [])})
            if unknown:
                problems.append(f"{label}: {unknown} are not failure surfaces of {cls}")
        states = set(block["state_reads"]) | set(block["state_writes"])
        if not states <= set(wcap.STATES):
            problems.append(f"{label}: states {sorted(states - set(wcap.STATES))} are not in the vocabulary")
        if contract.side_effect == "read" and not set(block["state_writes"]) <= {"scratch"}:
            problems.append(f"{label}: a read op writes {sorted(set(block['state_writes']) - {'scratch'})}")
        if not (block["state_evidence"] in cited and _curated(block["state_evidence"])):
            problems.append(f"{label}: its state evidence {block['state_evidence']} is not a curated oracle test")
    if problems:
        raise ValueError("; ".join(problems))


def _shipped(op: str) -> dict:
    return json.loads((wcap.PARAMS_DIR / f"workforce.{op}.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(not IN_SOURCE, reason="the oracle catalogue and the curated test list are in the source "
                                          "repository only")
def test_every_routed_op_declares_operational_fields_backed_by_curated_evidence():
    """R2-T1."""
    contracts = wcap.load_contracts()
    check(contracts)
    for op in ("run_tests", "run_tests_in_place"):
        raw = _shipped(op)
        contract = wcap.ParamContract.from_dict(raw)
        assert contract.operational == raw["operational"]
        assert len(contract.operational_sha256) == 64
        # L-1: the block sits outside the digest, so no probe row or census reading goes stale when a claim changes.
        bare = wcap.ParamContract.from_dict({k: v for k, v in raw.items() if k != "operational"})
        assert contract.sha256 == bare.sha256 and bare.operational is None and bare.operational_sha256 == ""
        edited = {**raw, "operational": {**raw["operational"], "introduces_failure_modes": []}}
        moved = wcap.ParamContract.from_dict(edited)
        assert moved.sha256 == contract.sha256 and moved.operational_sha256 != contract.operational_sha256
    # Survival control: a planted contract that cites a test no oracle lists for the op makes the checker raise.
    raw = _shipped("run_tests")
    planted = {**raw, "operational": {**raw["operational"], "mitigates_failure_modes": [
        {"failure_mode": "unreproduced_failure", "when": "",
         "evidence": "tests/mvp/venture/test_workforce_bench.py::test_a_test_no_oracle_lists"}]}}
    with pytest.raises(ValueError, match="not a curated oracle test of this op"):
        check({**contracts, ("workforce", "run_tests"): wcap.ParamContract.from_dict(planted)})
    # ... and so does a failure mode the routing class does not know, and a read op that writes the repository.
    strange = {**raw, "operational": {**raw["operational"], "introduces_failure_modes": ["made_up"]}}
    with pytest.raises(ValueError, match="are not failure surfaces of code_change"):
        check({**contracts, ("workforce", "run_tests"): wcap.ParamContract.from_dict(strange)})
    writer = {**raw, "operational": {**raw["operational"], "state_writes": ["venture_repo"]}}
    with pytest.raises(ValueError, match="a read op writes"):
        check({**contracts, ("workforce", "run_tests"): wcap.ParamContract.from_dict(writer)})


@pytest.mark.parametrize("block", [
    "not an object", {"mitigates_failure_modes": []},
    {"mitigates_failure_modes": [{"failure_mode": "x", "when": "sometimes", "evidence": "tests/a.py::test_a"}],
     "introduces_failure_modes": [], "state_reads": [], "state_writes": [], "state_evidence": "tests/a.py::test_a"},
    {"mitigates_failure_modes": [], "introduces_failure_modes": [], "state_reads": ["the_internet"],
     "state_writes": [], "state_evidence": "tests/a.py::test_a"},
    {"mitigates_failure_modes": [], "introduces_failure_modes": [], "state_reads": [], "state_writes": [],
     "state_evidence": "not a test id"}])
def test_a_malformed_operational_block_is_refused(block):
    with pytest.raises(ValueError, match="operational") as refused:
        wcap.ParamContract.from_dict({**_shipped("run_tests"), "operational": block})
    assert "unknown field" not in str(refused.value), "a known field, refused for its shape"


def _option(oid: str, coverage: tuple[str, ...]) -> scheduler.ExecutionOption:
    return scheduler.ExecutionOption(id=oid, work_unit_id="reproduce", strategy_type="deterministic",
                                     resource_tier="T1_DETERMINISTIC", mechanism="test_execution",
                                     risk_coverage=coverage)


def test_choose_rejects_a_route_that_does_not_mitigate_the_required_failure_mode():
    """R2-T2: two T1 options, ``requires=("unreproduced_failure",)``."""
    needed = ("unreproduced_failure",)
    first, second = _option("first", needed), _option("second", needed)
    assert scheduler.choose([first, second], requires=needed).selected is first
    bare = _option("first", ())
    choice = scheduler.choose([bare, second], requires=needed)
    assert choice.selected is second
    assert ("first", scheduler.NOT_MITIGATED.format(mode="unreproduced_failure")) in choice.rejected
    with pytest.raises(scheduler.NoRoute) as none:
        scheduler.choose([bare, _option("second", ())], requires=needed)
    assert [oid for oid, _ in none.value.rejected] == ["first", "second"]
    assert scheduler.choose([bare, second]).selected is bare, "requires=() leaves every caller's choice as it was"
