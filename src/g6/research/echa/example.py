# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A worked, synthetic end-to-end evidence trace (Checkpoint 1). Nothing here is a real result: the task, the oracle and
every number are invented to show how a claim reaches its evidence and what each kind of evidence is allowed to support.

    G6_ECHA_RESEARCH=1 python -P -m g6.research.echa.example OUT.jsonl

writes a ledger holding one supported claim (a differential probe with a negative control) and one inconclusive claim
(three reviews by one model family), plus a check that cannot fail, which is recorded and supports nothing.
"""
from __future__ import annotations

import json
import sys

from .adapters import evidence_from_mechanism_record, known_unknowns
from .claims import evaluate_claim
from .episodes import Episode
from .flag import require_research
from .ledger import Ledger
from .records import ClaimRecord, EvidenceRecord, StandardRef
from .trace import trace_claim

PREDICATE = "p-no-collateral-change"
FIXTURE = "ef" * 32  # the digest of the example's sealed vault
STANDARD = StandardRef.from_text(
    "std-example-collateral", "A change must not alter the behaviour of a function it was not asked to alter.",
    "synthetic example task document", (PREDICATE,))


def _evidence(evidence_id: str, **over) -> EvidenceRecord:
    base = {"evidence_id": evidence_id, "predicate_id": PREDICATE, "oracle_category": "INDEPENDENT_EXECUTABLE",
                "oracle_version": "differential-probe-v1", "fixture_version": FIXTURE, "independent": True,
                "independence_group": "evaluator-a", "independence_rationale": "authored and held by the evaluator, outside "
                "the adapting process", "result": "pass", "scope": "12 held-out inputs for helper h",
                "known_limitations": ("covers only the 12 inputs",), "source_ref": f"synthetic:{evidence_id}",
                "standard_sha256": STANDARD.standard_sha256, "negative_control_ref": "ctl-injected-defect", "purpose": "check"}
    base.update(over)
    return EvidenceRecord(**base)


def build(ledger: Ledger) -> dict:
    """Write the example into ``ledger`` and return the ids of its two claims."""
    ledger.add_standard(STANDARD)
    ledger.add_vault("vault-example", FIXTURE, files=1, roots=1)
    ledger.add_evidence(_evidence("ctl-injected-defect", purpose="negative_control", result="fail",
                                  scope="injected defect: helper h altered; the probe rejected it",
                                  negative_control_ref="", known_limitations=("one injected defect",)))
    ledger.add_evidence(_evidence("chk-probe"))
    bench = evidence_from_mechanism_record(
        {"mechanism": "deterministic_computation", "executed": True, "independent": True, "model_free": True,
         "result": "pass"}, evidence_id="chk-bench-bytes", predicate_id=PREDICATE,
        standard_sha256=STANDARD.standard_sha256, oracle_version="bench-byte-compare", fixture_version=FIXTURE,
        source_ref="synthetic:chk-bench-bytes", scope="every test file compared byte for byte",
        independence_group="bench", known_limitations=("it cannot fail on a delivered patch",))
    ledger.add_evidence(bench)  # no negative control: a structural protection
    for i in (1, 2, 3):
        ledger.add_evidence(_evidence(f"rev-{i}", oracle_category="HEURISTIC_REVIEW", independence_group="model-family-x",
                                      scope="a model's reading of the diff", negative_control_ref="",
                                      known_limitations=("the reviewers share one model family",)))
    episode = Episode(episode_id="ep-example-1", task_id="task-1", task_family="collateral-edit",
                      repository_or_group_id="synthetic-repo", split_id="example", standard_hash=STANDARD.standard_sha256,
                      executor_version="x1", harness_version="h1", oracle_version="differential-probe-v1",
                      component_versions={"differential_probe": "1"}, model_id="local-model",
                      model_configuration={"temperature": 0}, cost_budget=0)
    episode.append("plan", {"steps": 1})
    episode.append("forecast", {"status": "not_available"})
    episode.append("tool_result", {"check": "differential_probe", "executed": True, "counterexamples": 0})
    ledger.add_episode(episode.freeze(
        candidate_artifact_hash="0" * 64, outcome_state="qualified-draft", evidence_refs=("chk-probe", "chk-bench-bytes"),
        known_unknowns=known_unknowns({"remaining_unchecked_surfaces": ["edge_case_missed"]}), run_cost=0,
        latency_seconds=1.0))
    common = {"subject": "patch p1", "predicate_id": PREDICATE, "breadth": "covered_cases", "estimand": "oracle_scoped",
                  "population": "synthetic example task", "sample_unit": "one task", "provenance": "synthetic example",
                  "assumptions": ("the probe's inputs and expected values are correct",),
                  "versions": {"standard": STANDARD.standard_id, "oracle": "differential-probe-v1"},
                  "standard_sha256": STANDARD.standard_sha256}
    ledger.add_claim(ClaimRecord(claim_id="claim-covered-cases", scope="no change to helper h on the 12 held-out inputs",
                                 known_limitations=("not all inputs", "one task"),
                                 evidence_ids=("chk-probe", "chk-bench-bytes"), **common))
    ledger.add_claim(ClaimRecord(claim_id="claim-reviewer-consensus", scope="the diff leaves helper h alone",
                                 known_limitations=("reviewers are one model family",),
                                 evidence_ids=("rev-1", "rev-2", "rev-3"), **common))
    return {"supported": "claim-covered-cases", "inconclusive": "claim-reviewer-consensus"}


def main(argv: list[str]) -> int:
    require_research()
    if not argv:
        print("usage: python -P -m g6.research.echa.example OUT.jsonl", file=sys.stderr)
        return 2
    ledger = Ledger(argv[0])
    claims = build(ledger)
    for label, claim_id in claims.items():
        verdict = evaluate_claim(ledger, claim_id)
        print(json.dumps({"claim": claim_id, "expected": label, "result": verdict["result"],
                          "reasons": verdict["reasons"], "structural": verdict["structural"]}))
    print(json.dumps({"trace_of": claims["supported"], "ledger_ok": trace_claim(ledger, claims["supported"])["ledger_ok"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
