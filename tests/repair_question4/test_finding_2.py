"""A control-stage error cannot erase an already observed confirmed failure."""
from g6.harness import patch_review
from g6.harness import verification_search as vs
from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, result, standard, stated


def test_error_in_later_control_keeps_confirmed_patch_failure(tmp_path, monkeypatch):
    root = repo(tmp_path)
    stated(monkeypatch)
    calls = []

    def run_check(change, placed, source, node, *, at_base, **kwargs):
        calls.append(at_base)
        if at_base:
            raise OSError("fixture: base copy could not be opened")
        return result(node + "::test_example", "failed")

    monkeypatch.setattr(vs, "_run_check", run_check)
    reply = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=standard())
    assessment = reply["assessment"]
    [one] = assessment["confirmed_expectations"]["expectations"]
    assert calls == [False, True]
    assert one["check"]["runs"]["patch"]["result"] == "failed"
    assert assessment["outcome"] == patch_review.DEFECT, (
        "a later control error erased the confirmed failure: " + assessment["outcome"])
