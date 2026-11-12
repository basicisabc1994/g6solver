"""A review must not ignore damage to an existing unconfirmed question store."""
import pytest

from g6.harness import patch_review, question_store as qs
from g6.mcp import behaviour
from tests.repair_question2.helpers import Dialog, NODE, REQUIREMENTS, TASK, ask, repo, result, stated


def test_unconfirmed_store_damaged_during_review_refuses_publication(tmp_path, monkeypatch):
    root = repo(tmp_path)
    assert ask(root, dialog=Dialog({"choice": "skip", "name": "Maintainer"}))["status"] == "pending"
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    assert store.state().pending is not None
    assert behaviour.enforced(str(root), TASK) is None
    stated(monkeypatch)
    damaged = False

    def execute(*args, **kwargs):
        nonlocal damaged
        if not damaged:
            damaged = True
            with store.path.open("ab") as out:
                out.write(b"torn-entry\n")
        return result(NODE)

    monkeypatch.setattr(patch_review, "_execute", execute)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assert damaged
    with pytest.raises(qs.StoreUnreadable):
        store.state()
    assert reply.get("ok") is False and "assessment" not in reply, (
        "review published an assessment after its existing question store became unreadable")
    assert reply["warning_card"]["code"] == qs.UNREADABLE
