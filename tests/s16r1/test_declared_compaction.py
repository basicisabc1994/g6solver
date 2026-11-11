# SPDX-License-Identifier: Apache-2.0
"""Compaction keeps a producer declaration and never replaces an explicit origin."""
import time

import pytest

from g6.core import cloud_consent as cloud
from g6.core.llm import LLMError
from g6.mcp import review_reply
from g6.metaharness.jobs import Jobs


@pytest.mark.parametrize('origin', ['declared', 'unknown', 'revoked'])
def test_compaction_uses_declared_scope_without_erasing_carried_origin(
        tmp_path, monkeypatch, accepted_cloud_dialog, origin):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(review_reply, 'save', lambda *args: None)
    scope = cloud.Source.text('fixture:declared-report', 'report', 'SYNTHETIC_REPORT')
    accepted_cloud_dialog(scope.source_id, 'openai:codex', text='synthetic report')
    revoked = cloud.Source.text('fixture:revoked-report', 'report', 'SYNTHETIC_REPORT')
    cloud.revoke(revoked.source_id, 'openai:codex')

    def operation():
        reply = {'assessment': {'report': 'SYNTHETIC_REPORT', 'outcome': 'supported_within_scope'}}
        return reply if origin == 'declared' else cloud.bind_output(
            reply, (cloud.UNKNOWN if origin == 'unknown' else revoked,))

    jobs = Jobs()
    try:
        with cloud.collect_sources(), cloud.request_sources((scope,)):
            started = jobs.start(operation, compact=review_reply.finish)
            for _ in range(500):
                result = jobs.status(started['job_id'])
                if result['status'] == 'completed':
                    break
                time.sleep(.01)
            assert result['status'] == 'completed'
            refs = cloud.output_sources(result)
            assert scope in refs
            if origin == 'declared':
                assert cloud.UNKNOWN not in refs, 'compaction manufactured unknown despite a declared producer'
                with cloud.admission('openai:codex', refs, cloud.sha256('result')):
                    pass
            else:
                assert (cloud.UNKNOWN if origin == 'unknown' else revoked) in refs
                with pytest.raises(LLMError, match=cloud.REQUIRED):
                    with cloud.admission('openai:codex', refs, cloud.sha256('result')):
                        pytest.fail('compaction erased an unshared origin')
    finally:
        jobs.close()
