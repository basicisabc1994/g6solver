"""Frozen 68ca0eb presentation is the off-path text contract; no production import of the fixture."""
import copy
import inspect
from types import SimpleNamespace
from uuid import UUID

import pytest

from g6.harness import patch_report, patch_review
from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, stated
from tests.repair_question5 import guidance_68ca0eb as old_guidance, renderer_68ca0eb as old_report


TEXTS = ['café €', '# heading', '<result>', 'x > 0 & y < 3',
         'a\n b\r c\t d\x00 e\x7f f\x85 g\u2028 h\u2029',
         'Requirement: Scope: Rule: G6 observed: Answer:']


def strings(value, path=()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from strings(item, (*path, index))


@pytest.mark.parametrize('text', TEXTS)
def test_every_reply_and_report_text_field_matches_frozen_renderer(tmp_path, monkeypatch, text):
    root = repo(tmp_path)
    stated(monkeypatch)
    original = patch_review._reply
    captured = []
    def capture(*args, **kwargs):
        captured.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
        return original(*args, **kwargs)
    monkeypatch.setattr(patch_review, '_reply', capture)
    reply = patch_review.review_patch(root, task=TASK, requirements=[
        *REQUIREMENTS, {'id': 'R2', 'text': text, 'checks': []}])
    assert 'confirmed_expectations' not in reply['assessment']
    args, kwargs = captured[-1]
    bound = inspect.signature(original).bind(*args, **kwargs)
    bound.apply_defaults()
    # These slots are read from the real reply producer's signature and captured assessment, rather than a
    # hand-written replacement reply. Plant the same text in every guidance input and retain every text leaf.
    bound.arguments['reasons'] = [{**reason, 'detail': text} for reason in bound.arguments['reasons']]
    bound.arguments['unknowns'] = [text]
    bound.arguments['follow_ups'] = [text]
    bound.arguments['first_action'] = text
    bound.arguments['evaluated'] = [{**item, **{key: text for key in ('text', 'reason', 'suggested_test')}}
                                     for item in bound.arguments['evaluated']]
    args, kwargs = bound.args, bound.kwargs
    # Compare the request-ID field too, with the same external ID source on both render calls.
    monkeypatch.setattr(patch_review, 'uuid', SimpleNamespace(uuid4=lambda: UUID(int=1)))
    # The report states the seconds since the review started, to a tenth: both renders read one fixed instant.
    instant = bound.arguments['started'] + 1.0
    monkeypatch.setattr(patch_review, 'time', SimpleNamespace(monotonic=lambda: instant))
    reply = original(*args, **kwargs)
    monkeypatch.setattr(patch_review, '_one_line', old_guidance._one_line)
    monkeypatch.setattr(patch_report, 'render', old_report.render)
    frozen = original(*args, **kwargs)
    assert dict(strings(reply)) == dict(strings(frozen))


@pytest.mark.parametrize('text', TEXTS)
def test_ordinary_report_code_fields_equal_frozen_fixture(text):
    assert patch_report.code(text) == old_report.code(text)
