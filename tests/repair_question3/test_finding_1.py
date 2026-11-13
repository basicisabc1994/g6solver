"""An unavailable collection must not become an observed failed receipt."""
from g6.harness import patch_review
from g6.harness import verification_search as vs
from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, result, standard, stated


def test_unavailable_collection_is_not_sent_to_the_issuer_as_a_failure(tmp_path, monkeypatch):
    root = repo(tmp_path)
    stated(monkeypatch)
    calls = []

    def run_check(change, placed, source, node, *, at_base, **kwargs):
        calls.append(at_base)
        if len(calls) < 3:
            return result(node + "::test_example", "failed" if at_base else "passed")
        # A missing installed dependency prevents collection of the generated check.
        # This is the executor's collection_error, explicitly excluded by _to_a_result.
        return {"verdict": "collection_error", "test_outcomes": {},
                "failure_messages": {node: "E   ModuleNotFoundError: No module named 'optional_dependency'"},
                "seconds": 0.01, "output": "", "said": "could not collect"}

    monkeypatch.setattr(vs, "_run_check", run_check)
    reply = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=standard())
    assert calls == [False, True, False]
    assessment = reply["assessment"]
    assert assessment["outcome"] == patch_review.INSUFFICIENT
    held = assessment["confirmed_expectations"]
    assert held["expectations"][0]["check"]["result"] == "not_run"
    failures = [r for r in held["evidence"]["certificate"]["receipts"] if r["result"] == "fail"]
    assert failures == [], "an unavailable collection was issued as an observed fail receipt"
