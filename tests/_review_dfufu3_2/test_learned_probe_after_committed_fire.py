"""Review 2 of fu-3: the learned probe does not name an unavailable library once a fire of its own committed.

Repair 1 keeps a fire this review committed when a later library read is unavailable, and marks it
``library_unavailable_for_further_application`` instead of ``library_unavailable``. ``apply_review`` was taught the new
key and still adds its ``library_unavailable`` reason. ``check_learned`` (the ``regression.learned`` door of
``g6.workforce.patch_checks.run``) was not: it looks for ``library_unavailable`` only, finds none, and, because the
committed fire makes ``findings`` non-empty, also skips its own ``status_snapshot()`` probe ("An unavailable library must
still be named by the learned probe"). The reply's reason says "Library unavailable for further application" and
carries no ``code``: by its code the reply reads like "No in-scope library method ran".

The controls: a library that is unavailable before any fire (the builder's case, the code is there) and a removal
that works (nothing is unavailable, no code).
"""
import base64
import json
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger, canonical, digest
from g6.core.storage import state_path
from g6.distill import ledger
from g6.harness import library_review_gate as review
from tests.distill import test_runtime_identity_boundaries as runtime

NAME = 'testvalue.py'       # a file the tests are made of that bench._files lets a patch write
BEFORE, AFTER = (text['tests/test_value.py'] for text in (runtime.BEFORE, runtime.AFTER))


def cleanup_fails(monkeypatch, times):
    """The committed intent cannot be removed ``times`` times (another process has it open)."""
    unlink, failed = Path.unlink, []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and len(failed) < times and path.exists() \
                and json.loads(path.read_text(encoding='utf-8'))['body']['committed']:
            failed.append(True)
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


@pytest.mark.parametrize('case', ['unavailable_after_its_fire', 'control_unavailable_before', 'control_works'])
def test_learned_probe_names_an_unavailable_library_by_its_code(monkeypatch, tmp_path, case):
    from g6.workforce import bench

    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / NAME).write_bytes(BEFORE.encode('utf-8'))
    monkeypatch.setattr(bench, '_repo', lambda *args: repo)
    failed = cleanup_fails(monkeypatch, 2 if case == 'unavailable_after_its_fire' else 0)
    if case == 'control_unavailable_before':
        body = {'version': 1, 'committed': False, 'writes': [
            {'path': '../outside.dat', 'operation': 'replace', 'after': None,
             'before': base64.b64encode(b'x').decode('ascii')}]}
        ledger._intent_path().write_text(canonical({'body': body, 'sha256': digest(body)}) + '\n', encoding='utf-8')
    params = {'repo': 'example', 'files': {NAME: AFTER}, 'regression': {'learned': {'entry_id': entry.entry_id}}}

    result = review.check_learned(params)

    fires = ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries() \
        if case != 'control_unavailable_before' else []
    assert len(failed) == (2 if case == 'unavailable_after_its_fire' else 0)
    assert len(fires) == (0 if case == 'control_unavailable_before' else 1)
    says_unavailable = 'library unavailable' in result['reason'].lower()
    assert says_unavailable == (case != 'control_works')
    assert (result.get('code') == 'library_unavailable') == says_unavailable, (
        f"the learned probe's reason is {result['reason'][:90]!r}..., status={result['status']!r}, authority="
        f"{result['authority']!r}, {len(result['library'])} library finding, and its code is {result.get('code')!r}")
