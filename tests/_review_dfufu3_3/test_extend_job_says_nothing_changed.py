"""Review 3 of fu-3: on the server, ``g6_extend`` still answers "blocked before any state change" for a request it
recorded.

Repair 2 publishes the gap record and the capability request together and names both in the reply
(``error.committed_steps`` set in ``Service._extend``, rendered by ``mcp.distill._refusal``). ``_refusal`` is reached
only when ``Service.extend`` runs ``_extend`` in the calling thread, which it does when the service has no job store:
the fixture of review 2's file (``Service()``). The server registers the distillation doors with its job store
(``g6.mcp.__init__``: ``distill.register(server, context_type, jobs)``), so ``Service.extend`` returns
``self.jobs.start(lambda: self._extend(request, repo))`` and the ``PartialAuthorityWrite`` is caught by
``Jobs._run``'s ``except Exception``. The person polling the job is told: status ``failed``, G6_E_JOB_FAILED, and
``rollback_or_containment`` "The operation was blocked before any state change; no rollback is required."; no
``committed_steps``, no ``publication_committed``, no durable state. The gap record and the capability request are
both in their logs. Asked again, as a failed job invites, the same request is recorded a second time.

Real disk, the server's own ``Jobs``. The condition is review 2's: the committed intent cannot be removed once. The
controls are the same door with a removal that works, and the same failed removal with no job store (repair 2's
reply, which names both records).
"""
import json
from pathlib import Path
import time

import pytest

from g6.distill.ledger import Stores
from g6.distill.records import CapabilityExtension, FailureRecord
from g6.mcp import distill
from g6.mcp.distill_backend import Service
from g6.metaharness.jobs import Jobs


def doors(jobs):
    found = {}

    class Server:
        def tool(self):
            return lambda fn: found.setdefault(fn.__name__, fn)

    # ``backend=None`` is the server's own construction: Service(jobs=jobs).
    distill.register(Server(), object, jobs, backend=None if jobs is not None else Service())
    return found


def cleanup_fails_once(monkeypatch):
    """The first committed intent cannot be removed, once (another program has the file open)."""
    unlink, failed = Path.unlink, []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and not failed and path.exists():
            body = json.loads(path.read_text(encoding='utf-8'))['body']
            if body['committed']:
                failed.append([row['path'] for row in body['writes']])
                raise PermissionError(13, 'The process cannot access the file because it is being used by another '
                                          'process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


def answer(door, reply):
    """What the person reads: the job's result once it is no longer pending or running."""
    if 'job_id' not in reply or reply.get('status') not in {'pending', 'running'}:
        return reply
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        polled = door['g6_distill_job'](reply['job_id'], 'full')
        if polled.get('status') not in {'pending', 'running'}:
            return polled
        time.sleep(.02)
    raise AssertionError('the job did not finish')


@pytest.mark.parametrize('with_jobs, held', [(True, True), (True, False), (False, True)],
                         ids=['server_job_removal_fails_once', 'control_server_job_works',
                              'control_no_job_store_removal_fails_once'])
def test_extend_never_says_nothing_changed_of_a_request_it_recorded(tmp_path, monkeypatch, with_jobs, held):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    (tmp_path / 'home' / 'distill').mkdir(parents=True)
    door = doors(Jobs() if with_jobs else None)
    failed = cleanup_fails_once(monkeypatch) if held else []

    reply = answer(door, door['g6_extend']('G6 should check migrations', ''))

    assert len(failed) == (1 if held else 0)
    requests = Stores().for_model(CapabilityExtension).list()
    gaps = Stores().for_model(FailureRecord).list()
    assert len(requests) == len(gaps) == 1                          # repair 2: both or neither, and here both
    words = str(reply.get('rollback_or_containment', ''))
    named = [step.get('id') for step in reply.get('committed_steps', [])]
    assert 'before any state change' not in words.lower(), (
        f'the capability log holds {len(requests)} request and the failure log {len(gaps)} gap record of this call; '
        f"the person is answered status={reply.get('status')!r}, ok={reply.get('ok')!r}, "
        f"{reply.get('warning_card', {}).get('code')}, {words!r}, publication_committed="
        f"{reply.get('publication_committed')!r}, committed_steps={named}; error text: {reply.get('error')!r}")
    if held:
        assert named == [gaps[0].record_id, requests[0].content_id]
    else:
        assert reply.get('status') == 'completed' and reply['result']['gap_record'] == gaps[0].record_id
