"""Saved JSON readers refuse excessive nesting and exhausted decoder memory."""
import json

import pytest

from g6.core.storage import g6_home
from g6.harness import behaviour_question as bq, behaviour_runner
from g6.mcp import behaviour, review_binding, review_reply


# The old four-module reader heuristic is replaced by test_home_json_census.py, over all src/g6.


@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
@pytest.mark.parametrize('reader', ['binding', 'saved', 'sources', 'probes'])
def test_home_record_decoder_resource_failure_fails_closed(tmp_path, monkeypatch, failure, reader):
    saved = g6_home() / 'reviews' / ('a' * 32 + '.json')
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(b'{}')
    for suffix in ('.binding.json', '.sources.json', '.observations.json'):
        saved.with_suffix(suffix).write_bytes(b'{}')

    def exhausted(*args, **kwargs):
        raise failure('decoder limit')
    monkeypatch.setattr(json, 'loads', exhausted)
    if reader == 'binding':
        assert review_binding.matches(saved, 'identity') is False
    elif reader == 'sources':
        assert review_reply.carried(saved.stem) is None
    elif reader == 'probes':
        assert behaviour_runner.read_probes(saved, b'{}') == {}
    else:
        with pytest.raises(bq.CandidateRefused) as refused:
            behaviour._review_candidates('task', saved.stem, [], identity='identity', artifact='tree')
        assert refused.value.code == behaviour.NOT_THIS_REVIEW
