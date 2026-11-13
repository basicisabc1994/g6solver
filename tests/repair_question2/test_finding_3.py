"""Every later run on the patch must propagate a non-result to the assessment."""
from g6.harness import patch_review
from g6.harness import verification_search as vs
import pytest
from .helpers import REQUIREMENTS, TASK, repo, result, standard, stated


@pytest.mark.parametrize("replay", ["not_run", "timed_out", "skipped", "collection_error", "never_seen"])
def test_replay_that_did_not_run_downgrades_confirmed_enforcement(tmp_path, monkeypatch, replay):
    root = repo(tmp_path)
    stated(monkeypatch)
    statuses = iter(("passed", "failed", replay))

    def run_check(change, placed, source, node, **kwargs):
        status = next(statuses)
        if status == replay:
            return {"verdict": replay, "said": "fixture replay did not run to a result"}
        return result(node + "::test_example", status)

    monkeypatch.setattr(vs, "_run_check", run_check)
    got = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=standard())
    assert "assessment" in got, got
    assessment = got["assessment"]
    [one] = assessment["confirmed_expectations"]["expectations"]
    assert one["check"]["runs"]["again"]["result"] == "error"
    assert assessment["outcome"] == patch_review.INSUFFICIENT, (
        "confirmed replay did not run, but review returned " + assessment["outcome"])
