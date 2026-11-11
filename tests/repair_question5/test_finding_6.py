"""A review in which nothing was confirmed must keep its reply text: the form's encoder must not rewrite it.

`patch_review._one_line` (applied in `_reply` to every reason, unknown, next action and evaluated requirement of
every review) now calls `behaviour_question.caller_text(..., literal=False)`, which turns any text holding a non-ASCII
character, '#', '<', '>', '&' or one of the behaviour form's labels into an escaped JSON literal. Before this change
`_one_line` only replaced control characters. No question store exists here; only stated checks run (faked).
"""
from g6.harness import patch_review
from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, stated

TEXT = "the total is never < 0 & stays in euro (€) for café orders, issue #12"


def test_unconfirmed_review_keeps_requirement_text_in_its_next_actions(tmp_path, monkeypatch):
    root = repo(tmp_path)
    stated(monkeypatch)
    requirements = [*REQUIREMENTS, {"id": "R2", "text": TEXT, "checks": []}]
    reply = patch_review.review_patch(root, task=TASK, requirements=requirements)
    assessment = reply["assessment"]
    assert "confirmed_expectations" not in assessment   # nothing was confirmed
    [r2] = [one for one in assessment["requirements"] if one["id"] == "R2"]
    assert r2["text"] == TEXT   # the requirement record itself still holds the text as given
    assert r2["suggested_test"].endswith(": " + TEXT), (
        "with nothing confirmed, the reply's suggested test no longer carries the requirement text as given: "
        + repr(r2["suggested_test"]))
