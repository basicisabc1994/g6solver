# SPDX-License-Identifier: Apache-2.0
"""The requirement adapter resolves host observations rather than restating outcomes."""
from g6.harness import requirement_evidence as r
from g6.evidence.mapping import environment_sha256
from tests.evidence.test_execution_custody import A, OTHER, CODE, observed

CHECK = "tests/test_case.py::test_case"
ENVIRONMENT = {"python": "fixture"}


def run(result, artifact):
    ref = observed(check=CHECK, artifact=artifact, result=result,
                   environment=environment_sha256(ENVIRONMENT))
    return {"custody_receipt": ref.receipt_id, "source_before": artifact}


def assess(*, observe=True, extra=()):
    control = run("fail", OTHER) if observe else {}
    first = run("pass", A) if observe else {}
    again = run("pass", A) if observe else {}
    req = {"id": "R1", "text": "the case holds", "checks": [{"id": CHECK, "verdict": "passed"}]}
    runs = [{"tier": 1, "tests": [CHECK], **first},
            {"tier": 2, "check": r.AT_BASE, "outcomes": {CHECK: "failed"}, **control}]
    return r.assess([req], runs, artifact=A, base_commit="0" * 40, check_sha={CHECK: CODE},
                    written=lambda _: False, environment=ENVIRONMENT,
                    again={"verdicts": {CHECK: "passed"}, **again}, observed=extra)


def test_declared_outcomes_do_not_authenticate_requirement_runs():
    got = assess(observe=False)
    assert not got["requirements"][0]["checked"]
    assert got["refused"]


def test_host_observed_requirement_pass_keeps_its_authority():
    got = assess()
    assert got["requirements"][0]["checked"]
    assert got["state"] == "verified"


def test_forged_intermediate_failure_stays_visible_without_grounding_a_refutation():
    got = assess(extra=[{"run": ["derived"], "verdicts": {CHECK: "failed"}}])
    statement = got["requirements"][0]
    assert statement["support"] and not statement["refute"] and statement["caveats"]
    assert not statement["checked"]


def test_observed_run_normalization_preserves_the_host_reference():
    raw = {"run": ["derived"], "verdicts": {CHECK: "failed"}, **run("fail", A)}
    normalized = r.observed_runs([raw])[0]
    assert normalized["custody_receipt"] == raw["custody_receipt"]
    assert normalized["source_before"] == A
