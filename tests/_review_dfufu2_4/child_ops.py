"""Child-process operations for review 4: each call is a fresh process on a real disk.

usage: child_ops.py <home> <op> [crash]
The home is passed explicitly; nothing here reads the person's own state folder.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

home, op = Path(sys.argv[1]), sys.argv[2]
crash = sys.argv[3] if len(sys.argv) > 3 else ''
os.environ['G6_HOME'] = str(home)
os.environ['G6_REQUIRE_ISOLATION'] = '1'

from g6.core.ledger import ChainLedger  # noqa: E402
from g6.distill import acceptance_gate as gate  # noqa: E402
from g6.distill import surface  # noqa: E402
from g6.distill.library import Library  # noqa: E402
from g6.distill.rectify_gate import Rectifier  # noqa: E402
from g6.mcp import consent, distill  # noqa: E402
from g6.mcp.distill_backend import Service  # noqa: E402
from tests.distill import test_runtime_identity_boundaries as runtime  # noqa: E402


def tools():
    found = {}

    class Server:
        def tool(self):
            return lambda fn: found.setdefault(fn.__name__, fn)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Review owner'}, ''

    consent.elicit = accept
    distill.register(Server(), object, None, backend=Service())
    return found


def view():
    reply = distill._call(surface.library)
    if 'entries' not in reply:
        return {'reply': reply}
    decisions = []
    journal = home / 'distill' / 'acceptance_gate.jsonl'
    if journal.exists():
        decisions = [row['body']['verdict'] for row in ChainLedger(journal).entries() if row['kind'] == 'decision']
    return {'library': [[e['status'], e['authority']] for e in reply['entries']], 'decisions': decisions,
            'intent': (home / 'distill' / 'authority_intent.json').exists()}


KILL_OPEN = int(os.environ.get('R4_KILL_OPEN', '0'))
if KILL_OPEN or os.environ.get('R4_COUNT_OPEN'):
    # Die right after the Nth write-mode open under the home returned: the file is created or truncated, nothing
    # is written or flushed yet. (The builder's sweep dies after fsync, replace and unlink only.)
    opened, seen = Path.open, [0]

    def counting(path, mode='r', *args, **kwargs):
        stream = opened(path, mode, *args, **kwargs)
        if any(flag in mode for flag in 'wax') and home in Path(path).parents and op != 'setup':
            seen[0] += 1
            if seen[0] == KILL_OPEN:
                os._exit(73)
        return stream

    Path.open = counting
    import atexit
    atexit.register(lambda: sys.stderr.write(f'OPENS={seen[0]}\n'))

if op == 'setup':
    # tests.distill.test_runtime_identity_boundaries.disk, at this home: one admitted A2 method.
    patch = pytest.MonkeyPatch()
    frozen = runtime.disk.__wrapped__(patch, home.parent)
    assert Path(os.environ['G6_HOME']) == home, 'the fixture home is <root>/home'
    entry = runtime.admit(runtime.method(), frozen)
    if crash == 'held':
        entry = gate._change(entry, status='held', authority='A0', reason='owner hold')
    Rectifier().reconcile()
    print(json.dumps(view()))
elif op in {'withdraw', 'restore', 'demote'}:
    [current] = Library().list()
    if crash:
        append = ChainLedger.append

        def die(self, kind, ident, body):
            result = append(self, kind, ident, body)
            if kind == crash:
                os._exit(73)
            return result

        ChainLedger.append = die
    answer = asyncio.run(tools()['g6_library_decide'](current.entry_id, op, 'owner decision', object()))
    print(json.dumps({'answer': answer, **view()}, default=str))
elif op == 'view':
    print(json.dumps(view()))
elif op == 'review':
    from g6.harness import library_review_gate as review
    found = review.check(runtime.BEFORE, runtime.AFTER)
    print(json.dumps({'found': [[f['authority'], f['outcome'], f['verdict']] for f in found], **view()}))
else:
    raise SystemExit('unknown op')
