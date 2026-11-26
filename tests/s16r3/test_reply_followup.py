"""The reply follow-up: review 4 of the reply change, findings 1 and 2, each held at its class.

Finding 2's class: a decoder resource error (``RecursionError``, ``MemoryError``) translated into a bare ``ValueError``,
which the module's own malformed-JSON handler (``except json.JSONDecodeError``) does not catch, so the module's refusal
is bypassed. The translation now raises ``json.JSONDecodeError``: still a ``ValueError`` for every handler that caught
one, and caught by the module's malformed-input handler as well.

Finding 1's class: a non-bound caller of a bound sharing-state reader (``core/cloud_consent``) that does not hold the
decoder's limits itself, so a sharing state too deep for the decoder ends in a raw error instead of G6's refusal.
"""
import ast
from pathlib import Path

import pytest

from tests.s16r3.certificate_inputs import certificate_inputs

SOURCE = Path(__file__).resolve().parents[2] / 'src' / 'g6'
DEEP = b'[' * 200000 + b']' * 200000
PINNED_CHILD = {'harness/behaviour_runner.py'}
LIMITS = {'RecursionError', 'MemoryError'}
REGISTRATION = 'src-' + '0' * 16


def _names(handler):
    if handler.type is None:
        return {'BaseException'}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {n.id if isinstance(n, ast.Name) else getattr(n, 'attr', '') for n in nodes}


def _sources():
    bound = certificate_inputs()
    for path in sorted(SOURCE.rglob('*.py')):
        rel = path.relative_to(SOURCE).as_posix()
        if rel not in bound and rel not in PINNED_CHILD:
            yield rel, ast.parse(path.read_text(encoding='utf-8'))


def bare_translations(tree):
    """Lines where a handler of the decoder resource errors alone translates them into a bare ``ValueError`` (a handler
    that also catches ``ValueError`` is the module's own refusal, not a translation)."""
    found = []
    for handler in ast.walk(tree):
        if isinstance(handler, ast.ExceptHandler) and _names(handler) and _names(handler) <= LIMITS:
            for node in ast.walk(handler):
                if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                        and isinstance(node.exc.func, ast.Name) and node.exc.func.id == 'ValueError':
                    found.append(node.lineno)
    return found


#: A refusal of its own, reading no JSON, with where it is documented.
NOT_A_DECODER = {
    'complexity/inputs.py': "atlas_of: Python's own parser past its depth; ValueError is its documented refusal",
}


def test_no_decoder_limit_handler_raises_a_bare_value_error():
    missing = [f'{rel}:{line}' for rel, tree in _sources() if rel not in NOT_A_DECODER
               for line in bare_translations(tree)]
    assert not missing, '\n' + '\n'.join(missing)


def test_the_translation_check_sees_the_pattern():
    assert bare_translations(ast.parse(
        'try:\n x = json.loads(s)\nexcept (RecursionError, MemoryError):\n raise ValueError("limits") from None'))
    assert not bare_translations(ast.parse(
        'try:\n x = json.loads(s)\nexcept (RecursionError, MemoryError):\n'
        ' raise json.JSONDecodeError("limits", "", 0) from None'))


# --- finding 1's class: the sharing state's readers --------------------------------------------------------------------

#: core/cloud_consent's functions that read the sharing-state ledger.
READERS = {'preview', 'is_granted', 'refuse_unshared', 'admission', 'revoke', 'grants', 'disclosures'}
#: Calls held by a handler the scan cannot see, each with where that handler is.
HELD_ELSEWHERE = {
    ('mcp/cloud_consent.py', 'revoke'): 'g6_cloud_revoke runs it inside reply(), whose handler refuses in G6 words',
}
#: A model send's sharing check (``admission``, and the check that runs ahead of it). A handler around the ``with`` would
#: take the send's own errors for the sharing state's, and the consent inventories name these sends by their ``with``
#: statements; the bound reader's own fix (``core/cloud_consent`` ``_take``/``_written``, the issuance hunk of this
#: change) holds them. Listed exactly, so a new unheld read fails here.
WAITS_FOR_ISSUANCE = {
    ('agents/_runtime/consent.py', 'admission'),
    ('harness/completion_loop.py', 'admission'),
    ('self_maintenance/usage.py', 'admission'),
    ('self_maintenance/usage.py', 'refuse_unshared'),
}
GUARDS = LIMITS | {'Exception', 'BaseException'}


def _rethrows(handler):
    return any(isinstance(n, ast.Raise) and (n.exc is None or (
        isinstance(n.exc, ast.Name) and n.exc.id == handler.name)) for n in ast.walk(handler))


def unheld_reads(tree):
    """(line, reader) of each call to a sharing-state reader of ``core/cloud_consent`` that no enclosing handler in its
    own function holds against ``RecursionError``."""
    modules, direct = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'g6.core':
            modules.update(a.asname or a.name for a in node.names if a.name == 'cloud_consent')
        elif isinstance(node, ast.ImportFrom) and node.module == 'g6.core.cloud_consent':
            direct.update({a.asname or a.name: a.name for a in node.names if a.name in READERS})
        elif isinstance(node, ast.Import):
            modules.update(a.asname for a in node.names if a.name == 'g6.core.cloud_consent' and a.asname)
    found = []

    def visit(node, held):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            held = False
        if isinstance(node, ast.Try):
            inner = held or any(_names(h) & GUARDS and not _rethrows(h) for h in node.handlers)
            for child in node.body:
                visit(child, inner)
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                visit(child, held)
            return
        if isinstance(node, ast.Call):
            f = node.func
            name = (f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id in modules and f.attr in READERS else
                    direct.get(f.id) if isinstance(f, ast.Name) else None)
            if name and not held:
                found.append((node.lineno, name))
        for child in ast.iter_child_nodes(node):
            visit(child, held)
    visit(tree, False)
    return found


def test_every_non_bound_sharing_state_read_holds_the_decoder_limits():
    found = [(rel, line, name) for rel, tree in _sources() for line, name in unheld_reads(tree)
             if (rel, name) not in HELD_ELSEWHERE]
    missing = [f'{rel}:{line} {name}' for rel, line, name in found if (rel, name) not in WAITS_FOR_ISSUANCE]
    assert not missing, '\n' + '\n'.join(missing)
    assert {(rel, name) for rel, _line, name in found} == WAITS_FOR_ISSUANCE


@pytest.mark.parametrize('source,count', [
    ('from g6.core import cloud_consent as c\ndef f():\n return c.is_granted(s, p)', 1),
    ('from g6.core.cloud_consent import admission as a\ndef f():\n with a(p, s, h):\n  pass', 1),
    ('import g6.core.cloud_consent as c\ndef f():\n try:\n  c.refuse_unshared(p, s)\n except Exception:\n  raise', 1),
    ('from g6.core import cloud_consent as c\ndef f():\n try:\n  pass\n except RecursionError:\n  pass\n'
     ' else:\n  c.grants()', 1),
    ('from g6.core import cloud_consent as c\ndef f():\n try:\n  c.grants()\n except (RecursionError, MemoryError):\n'
     '  return []', 0),
])
def test_the_sharing_read_check_sees_aliases_and_rethrows(source, count):
    assert len(unheld_reads(ast.parse(source))) == count


@pytest.fixture
def deep_sharing_state(tmp_path, monkeypatch):
    """A sharing state whose one ledger line is nested past what the decoder reads (its head file kept)."""
    from g6.core import cloud_consent as cloud
    from g6.core.storage import g6_home

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    cloud.revoke('source:a', 'anthropic:claude_code')        # writes the ledger and its head
    events = g6_home() / 'cloud_consent' / 'events.jsonl'
    events.write_bytes(DEEP + b'\n')
    return events



def test_a_sharing_state_too_deep_shares_no_workflow_record(deep_sharing_state):
    from g6.harness.workflow import view

    assert view.shared('0' * 64, 'anthropic:claude_code') is False


def test_a_sharing_state_too_deep_grants_no_indexed_source(deep_sharing_state):
    from types import SimpleNamespace

    from g6._context_engine import _engine

    acc = SimpleNamespace(local=False, destination='anthropic:claude_code')
    assert _engine._granted(acc, REGISTRATION) is False
