"""A later non-confirming answer does not remove the existing enforced cases."""
from g6.mcp import behaviour
from tests.repair_question2.helpers import TASK, Dialog, ask, candidate, repo


def test_unconfirmed_answer_does_not_report_that_all_enforcement_stopped(tmp_path):
    root = repo(tmp_path)
    first = ask(root, dialog=Dialog({"choice": "A", "name": "Maintainer", "further": "one_more"}))
    assert first["status"] == "confirmed"
    second = ask(root, data=[candidate(args=[4, 2])],
                 dialog=Dialog({"choice": "neither", "name": "Maintainer"}))
    assert second["status"] == "answered_unconfirmed"
    held = behaviour.enforced(str(root), TASK)
    assert held is not None and len(held.expectations) == 1
    assert held.predicates == tuple(second["standard"]["predicates"])
    inaccurate = "The person's answer confirms no outcome, so nothing is enforced; tell the person what you will do."
    assert second["next_action"] != inaccurate, (
        "reply says nothing is enforced while the earlier confirmed case remains enforced")
