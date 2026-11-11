"""Snapshotting must keep the review's report-first presentation contract."""
import pytest

from g6.core import cloud_consent as cloud
from g6.metaharness.jobs import Jobs


@pytest.mark.parametrize('detail', ['full', 'compact'])
def test_completion_snapshot_preserves_the_producers_field_order(detail):
    value = {'assessment': {'report': 'report first', 'assessment': 'supported_within_scope'},
             'artifact_digest': 'synthetic', 'completion_state': 'qualified-draft'}
    jobs = Jobs()
    try:
        with cloud.collect_sources():
            started = jobs.start(lambda: cloud.bind_output(value, ()), compact=lambda *_: value)
        jobs.close()
        result = jobs.status(started['job_id'], detail)['result']
        assert list(result) == list(value)
        assert list(result['assessment']) == ['report', 'assessment']
    finally:
        jobs.close()
