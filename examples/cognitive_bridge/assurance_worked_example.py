# SPDX-License-Identifier: Apache-2.0
"""A REPRODUCIBLE LOCAL WORKED EXAMPLE, not a G6 product certificate.

Run with: PYTHONPATH=_local_shims:src python examples/assurance_worked_example.py
Uses an illustrative operator-owned acceptance rubric, a stubbed G6 executor,
and a small independent truth-table verifier. It does not establish separated
process custody, domain accreditation or G6's production promotion law.
"""
import itertools
import json
from pathlib import Path

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, EvidenceArtifact,
    FrozenVerifier, IndependentAssuranceEvaluator, MetacognitiveMethodSelector,
    MethodVariant, ProblemSignature,
)
from g6.cognitive_bridge.assurance import DOCUMENT_FIELDS, canonical_json, sha256


class _Success:
    def __init__(self, value):
        self.value = value

    def unwrap(self):
        return self.value


class StubRegistry:
    """Demonstrates the real G6 registry invoke signature; no G6 solver here."""
    def invoke(self, name, op, params=None, *, block_sensitive=False):
        assert (name, op, block_sensitive) == ("formal", "check_validity", True)
        return _Success({"component": name, "op": op,
                         "completion_state": "qualified-draft",
                         "formula": params["formula"],
                         "premises": params["premises"],
                         "sorts": params["sorts"]})


def _evaluate(proposition, symbols):
    # A micro-language frozen by the illustrative demo verifier; only these
    # Boolean formulae are supported. Unknown formulae must fail closed.
    if proposition == "A":
        return symbols["A"]
    if proposition == "B":
        return symbols["B"]
    if proposition == "(=> A B)":
        return not symbols["A"] or symbols["B"]
    raise ValueError("unsupported proposition under this demonstration checker")


def independent_truth_table_check(artifact_bytes: bytes) -> bool:
    """Recomputes semantic entailment rather than trusting 'valid: true'."""
    try:
        document = json.loads(artifact_bytes)
        steps = document["steps"]
        if len(steps) != 1 or steps[0]["component"] != "formal" or steps[0]["op"] != "check_validity":
            return False
        model = steps[0]["result"]
        if model["sorts"] != {"A": "Bool", "B": "Bool"}:
            return False
        premises = model["premises"]
        goal = model["formula"]
        # Any countermodel disproves the entailment in this finite 2-bit scope.
        for a, b in itertools.product((False, True), repeat=2):
            assignment = {"A": a, "B": b}
            if all(_evaluate(p, assignment) for p in premises):
                if not _evaluate(goal, assignment):
                    return False
        return True
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return False


def main() -> None:
    std_text = b"LOCAL ILLUSTRATIVE RUBRIC v1. Confirm A=>B and A entail B by truth table."
    std = DomainStandard(
        id="software_research_demo", edition="demo-v1", jurisdiction="example",
        authority="Illustrative local rubric; NOT a professional or accredited standard",
        source_sha256=sha256(std_text), risk=1,
        criteria=(Criterion("logical_entailment", "Prove entailment over the two Boolean symbols",
                            "tiny_truth_table", "demo-v1/clause-1", "critical",
                            ("false_confidence",), "report", ("formal",)),))
    alternatives = [MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2.0)]
    ctx = ProblemSignature("software_research_demo", "logical_entailment", "validity", 1, 5)
    claim = ClaimIR("claim1", "(A => B) and A entail B", ("formal",), {})
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=StubRegistry())
    compiled = orchestrator.compile(
        claim=claim, context=ctx, standard=std, variants=alternatives,
        variant_models={"smt": {"premises": ["(=> A B)", "A"], "formula": "B",
                                "sorts": {"A": "Bool", "B": "Bool"}}})
    run = orchestrator.execute(compiled, approved=True)
    actual = orchestrator.execution_artifact(run, actor="research_agent", claim_id="claim1")
    # The negative control changes an evidential premise: it must be rejected.
    false_run = json.loads(canonical_json(run))
    false_run["steps"][0]["result"]["premises"] = ["(=> A B)"]
    negative = canonical_json(false_run)
    assert independent_truth_table_check(actual.content)
    assert not independent_truth_table_check(negative)
    checker = FrozenVerifier("tiny_truth_table", "external_evaluator", sha256(b"tiny-demo-verifier-v1"),
                             independent_truth_table_check, (actual.content,), (negative,))
    documentation = {
        name: "Demo only. Frozen reference and truth-table obligations. No claim of domain accreditation."
        for name in DOCUMENT_FIELDS
    }
    documentation["objective_and_scope"] = "Check one finite propositional entailment with two Boolean symbols."
    documentation["methodology"] = "Enumerate every valuation of A and B; reject any countermodel."
    documentation["standards_and_sources"] = "Illustrative local rubric demo-v1; source sha256 " + std.source_sha256
    documentation["data_provenance"] = "G6 registry is stubbed; artifact bytes are bound to execution report hash."
    documentation["assumptions"] = "Classical Boolean logic only, two named variables."
    documentation["uncertainty_and_limitations"] = "No real G6 solver, no outside-process custody, no professional review."
    documentation["counterexamples_and_refutations"] = "Negative control removes premise A, admitting A=False B=False etc."
    documentation["reproduction_instructions"] = "Run this script and re-evaluate truth table + controls."
    documentation["exceptions_and_unresolved_items"] = "Unresolved: actual G6 registry integration and external trust root."
    output = orchestrator.assess(
        compiled, standard=std, run_result=run,
        evaluator=IndependentAssuranceEvaluator(evaluator_id="review_runner"),
        verifiers={"tiny_truth_table": checker}, artifacts={"logical_entailment": actual},
        documentation=documentation, candidate_actor="research_agent")
    assert output["completion_state"] == "qualified-draft"
    assert output["verified"] is False
    report_path = Path(__file__).resolve().parent / "illustrative_candidate_dossier.json"
    report_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"completion_state": output["completion_state"],
                      "verified": output["verified"],
                      "dossier_digest": output["dossier"]["content_sha256"],
                      "negative_controls_detected": output["dossier"]["metrics"]["negative_controls_detected"],
                      "sample_path": str(report_path)}, indent=2))


if __name__ == "__main__":
    main()
