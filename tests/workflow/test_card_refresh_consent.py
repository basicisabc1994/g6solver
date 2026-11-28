"""A status poll refreshes the card from the workflow record; without the person's grant it reads nothing from it."""
from __future__ import annotations

from g6.core import cloud_consent as cloud
from g6.harness.workflow import evidence
from tests.workflow.kit import make_repo
from tests.workflow.test_card_release import TASK, _assessment


def _workflow_sources(sources) -> list:
    return [s for s in sources or () if s.source_id.startswith("workflow:")]


def test_refreshed_status_reply_without_the_workflow_grant_carries_no_record_counts(home, tmp_path):
    from g6.mcp import review_reply, workflow
    from g6.metaharness.jobs import Jobs

    repo = make_repo(tmp_path / "repo")
    jobs = Jobs()
    try:
        with cloud.collect_sources() as submitting:     # the job is submitted by a client with no workflow grant
            submitting.client = ("openai:codex", None)
            ident = jobs.start(lambda: {"assessment": _assessment()},
                               compact=workflow.finishing(review_reply.finish, str(repo), TASK))["job_id"]
        jobs._jobs[ident].result(timeout=60)
        assert evidence.read(repo, TASK)["justification"]     # the record does hold this task's events
        for detail in ("compact", "full"):
            with cloud.collect_sources() as polling:
                polling.client = ("openai:codex", None)
                reply = jobs.status(ident, detail)
                assessment = reply["result"]["assessment"]
                card = assessment["card"]
                assert card["checked"]["record_status"] == "withheld"
                assert card["justification"] == [] and card["caught"]["reproduced"] is None
                assert card["repaired"] is None and card["open"] is None
                assert "workflow_evidence" not in assessment or assessment["workflow_evidence"]["events"] == []
                assert "Record events: `" not in assessment["report"]
                assert not _workflow_sources(cloud.output_sources(reply))
                assert not _workflow_sources(polling.sources)
        # Control: the same poll with no client named (a local read) counts the recorded events, and the status
        # reply then carries the record's source, so its egress to a provider without the grant is withheld.
        with cloud.collect_sources():
            local = jobs.status(ident)
            assert local["result"]["assessment"]["card"]["caught"]["reproduced"] == 1
            assert local["result"]["assessment"]["card"]["justification"]
            assert _workflow_sources(cloud.output_sources(local))
    finally:
        jobs.close()
