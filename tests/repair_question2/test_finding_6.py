"""A review cannot publish evidence under a standard superseded while it ran."""
from g6.harness import patch_review
from g6.harness import verification_search as vs
from g6.mcp import behaviour
from .helpers import Dialog, NODE, REQUIREMENTS, TASK, ask, candidate, repo, result, stated


def test_answer_superseded_during_execution_is_not_enforced_or_counted(tmp_path, monkeypatch):
    root = repo(tmp_path)
    assert ask(root, dialog=Dialog({"choice": "A", "name": "Maintainer", "further": "one_more"}))["status"] == "confirmed"
    previous = behaviour.enforced(str(root), TASK)
    stated(monkeypatch)
    replaced = False

    def execute(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            reply = ask(root, data=[candidate(scope="replacement confirmation of the same case")],
                        dialog=Dialog({"choice": "B", "name": "Maintainer"}))
            assert reply["status"] == "confirmed", reply
        return result(NODE)

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(vs, "_run_check", lambda change, placed, source, node, *, at_base, **k:
                        result(node + "::test_example", "failed" if at_base else "passed"))
    got = behaviour.reviewed(lambda root, **kwargs: patch_review.review_patch(root, **kwargs), str(root), TASK,
                              requirements=REQUIREMENTS)
    current = behaviour.enforced(str(root), TASK)
    assert current.supersedes == previous.sha256
    assert current.expectations[0].option == "B"
    if "assessment" not in got:
        assert got.get("ok") is False
        return
    assessment = got["assessment"]
    held = assessment["confirmed_expectations"]
    uncounted = not any(item["checked"] or item.get("refute") for item in held["evidence"]["expectations"])
    refused = assessment["outcome"] in (patch_review.INSUFFICIENT, patch_review.INTEGRITY) and uncounted
    assert held["standard_sha256"] == current.sha256 or refused, "review enforced and counted the superseded A standard"
