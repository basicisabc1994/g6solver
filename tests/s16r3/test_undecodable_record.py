"""Repair 1 of the reply follow-up: a record past the decoder's limits never lets another record decide in its place.

The follow-up turned a decoder resource error (``RecursionError``, ``MemoryError``) into ``json.JSONDecodeError`` so
each module's own malformed-input refusal handles it. Where that handler passes over the record and goes on (the last
result printed, the latest audit, the first object in a reply, a stored list a gate reads, a worker's last line), the
record it could not read may be the one that decides, so passing over it lets an earlier, older or nested one decide.
Those readers decode through ``g6.core.decoder_limits.decode``: its ``UndecodableRecord`` is a ``ValueError`` the
passing-over handler does not catch.
"""
import ast
import json
from pathlib import Path

import pytest

from tests.s16r3.certificate_inputs import certificate_inputs

SOURCE = Path(__file__).resolve().parents[2] / 'src' / 'g6'
DEEP = '[' * 200000 + '0' + ']' * 200000


def test_decode_refuses_past_the_limits_and_is_no_json_decode_error():
    from g6.core.decoder_limits import UndecodableRecord, decode

    assert decode('{"a": [1]}') == {'a': [1]}
    with pytest.raises(UndecodableRecord) as raised:
        decode(DEEP)
    assert isinstance(raised.value, ValueError) and not isinstance(raised.value, json.JSONDecodeError)
    with pytest.raises(json.JSONDecodeError):
        decode('{"a": ')


# --- finding 1: the latest audit -----------------------------------------------------------------------------------

def _store_with_unreadable_latest(tmp_path):
    from g6.self_training.persistence import ScorecardStore
    from g6.self_training.schema import AuditResult

    store = ScorecardStore(tmp_path)
    store.append_audit(AuditResult(component='example', tier=5, passed_threshold=True, rubric_reviewed=True))
    failing = ('{"component":"example","tier":2,"passed_threshold":false,"rubric_reviewed":true,"detail":'
               + DEEP + '}')
    with open(store.store_dir / store.AUDIT_LOG, 'a', encoding='utf-8') as log:
        log.write(failing + '\n')
    return store


def test_an_unreadable_latest_audit_refuses_a_publication_promotion(tmp_path):
    from g6.self_training.persistence import PromotionRefused

    store = _store_with_unreadable_latest(tmp_path)
    with pytest.raises(PromotionRefused, match='latest audit is not known'):
        store.promote('example', 6)
    assert store.get_score('example').current_tier == 5


def test_an_unreadable_audit_is_not_passed_over_by_the_history(tmp_path):
    from g6.core.decoder_limits import UndecodableRecord

    with pytest.raises(UndecodableRecord):
        _store_with_unreadable_latest(tmp_path).audit_history('example')


def test_a_promotion_that_reads_no_audit_still_stands(tmp_path):
    assert _store_with_unreadable_latest(tmp_path).promote('example', 3).current_tier == 3


def test_a_malformed_audit_line_is_still_passed_over(tmp_path):
    from g6.self_training.persistence import ScorecardStore
    from g6.self_training.schema import AuditResult

    store = ScorecardStore(tmp_path)
    store.append_audit(AuditResult(component='example', tier=5, passed_threshold=True, rubric_reviewed=True))
    with open(store.store_dir / store.AUDIT_LOG, 'a', encoding='utf-8') as log:
        log.write('{"component": \n')
    assert len(store.audit_history('example')) == 1


# --- finding 2: the result Claude Code printed last -------------------------------------------------------------

def test_an_unreadable_final_result_never_resurrects_an_earlier_one():
    from g6.agents.agent_claude.adapter import _result

    earlier = json.dumps({'type': 'result', 'is_error': False, 'result': 'old success'})
    final = '{"type":"result","is_error":true,"result":"failed","details":' + DEEP + '}'
    assert _result(earlier + '\n' + final) is None


def test_an_unreadable_line_before_the_last_result_does_not_hide_it():
    from g6.agents.agent_claude.adapter import _result

    final = {'type': 'result', 'is_error': False, 'result': 'done'}
    assert _result('{"type":"assistant","x":' + DEEP + '}\n' + json.dumps(final)) == final


# --- finding 3: the first object in a model's reply -------------------------------------------------------------

def test_an_unreadable_outer_object_never_promotes_an_inner_answer():
    from g6.reasoning.steps import DirectStep, first_json_object

    reply = '{"answer":"outer answer","data":' + '[' * 200000 + '{"answer":"inner"}' + ']' * 200000 + '}'
    with pytest.raises(ValueError):
        first_json_object(reply)

    class Model:
        def complete(self, *args, **kwargs):
            return reply

    assert DirectStep(Model()).solve('What is the answer?') == ''


# --- the audit's other sites ----------------------------------------------------------------------------------------

def test_a_theory_whose_sources_cannot_be_read_is_not_read_as_having_none(tmp_path):
    from g6.core.decoder_limits import UndecodableRecord
    from g6.learning.schema import Theory
    from g6.learning.theory_store import TheoryStore

    store = TheoryStore(tmp_path / 'theories.db', scope='s')
    theory = Theory.new('G6_E_X', 'do the thing', source_instances=['i1'])
    store.add_candidates([theory])
    store._conn.execute('UPDATE theories SET source_instances=? WHERE theory_id=?', (DEEP, theory.theory_id))
    store._conn.commit()
    with pytest.raises(UndecodableRecord):
        store.get(theory.theory_id)
    store.close()


def test_a_stopped_workers_unreadable_last_line_is_refused_not_dropped(monkeypatch):
    from g6.algebra import runner
    from g6.algebra.envelope import OUTPUT_UNRECOGNISED, Refusal

    out = json.dumps({'stage': 'answer', 'answer': 'x'}) + '\n{"stage":"error","reason":' + DEEP + '}'
    monkeypatch.setattr(runner, '_execute', lambda *a, **k: runner.Run(None, out, '', 'time'))
    with pytest.raises(Refusal) as refused:
        runner.call_worker({'op': 'x'}, seconds=1, memory_mb=64, cap=1 << 20, record={})
    assert refused.value.code == OUTPUT_UNRECOGNISED


def test_a_stopped_workers_cut_off_last_line_is_still_dropped(monkeypatch):
    from g6.algebra import runner

    out = json.dumps({'stage': 'answer', 'answer': 'x'}) + '\n{"stage": "rech'
    monkeypatch.setattr(runner, '_execute', lambda *a, **k: runner.Run(None, out, '', 'time'))
    lines, stopped = runner.call_worker({'op': 'x'}, seconds=1, memory_mb=64, cap=1 << 20, record={})
    assert [line['stage'] for line in lines] == ['answer'] and stopped == 'time'


# --- the class: a translation inside a handler of the module's own ----------------------------------------------------

LIMITS = {'RecursionError', 'MemoryError'}
OWN = {'JSONDecodeError', 'ValueError', 'Exception', 'BaseException'}
#: Each function where a decoder-limit translation to ``json.JSONDecodeError`` sits inside a handler of the module's own
#: in the same function, with what that handler does: each refuses or reads the record as missing, never passes over
#: it for another. A new one is reviewed here before it is listed.
REVIEWED = {
    ('algebra/worker.py', 'main'): 'emits an error stage and ends the worker',
    ('cegis/runner.py', '_call'): 'RunnerError',
    ('code/adapt_healing/cases.py', '_run'): 'no report: the run reported nothing, loaded False',
    ('code/meta_programming/runner.py', 'run_tests'): 'no report: no results, collected unknown',
    ('core/skills.py', 'skill_examples'): 'ValueError naming the example',
    ('integrations/database/config.py', 'parse'): 'ConfigError',
    ('learning/dataset_builder.py', 'from_jsonl'): 'G6_E_LEARNING_DATASET_INVALID',
    ('llm_router/providers.py', '_json'): 'PROVIDER_ERROR',
    ('mcp/__init__.py', 'invoke_reply'): 'G6_E_INPUT_VALIDATION blocked reply',
    ('mcp/__init__.py', 'g6_compose'): 'G6_E_INPUT_VALIDATION blocked reply',
    ('mcp/__init__.py', 'metaharness_start'): 'G6_E_INPUT_VALIDATION blocked reply',
    ('mcp/__init__.py', 'metaharness_propose'): 'G6_E_META_SCHEMA blocked reply',
    ('mcp/cloud_consent.py', 'protect_reply'): 'the withheld reply',
    ('skills/routing.py', 'loads'): 'ManifestError',
}


def _names(handler):
    if handler.type is None:
        return {'BaseException'}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {n.id if isinstance(n, ast.Name) else getattr(n, 'attr', '') for n in nodes}


def _translates(handler):
    return _names(handler) <= LIMITS and any(
        isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and isinstance(n.exc.func, ast.Attribute)
        and n.exc.func.attr == 'JSONDecodeError' for n in ast.walk(handler))


def held_translations(tree):
    """(function, own handler) for each decoder-limit translation whose nearest enclosing handler in its own function
    catches what it raises."""
    found = []

    def visit(node, func, held):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func, held = node.name, None
        if isinstance(node, ast.ExceptHandler) and _translates(node) and held is not None:
            found.append((func, held))
        if isinstance(node, ast.Try):
            own = next((h for h in node.handlers if _names(h) & OWN), None)
            for child in node.body:
                visit(child, func, own or held)
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                visit(child, func, held)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, func, held)

    visit(tree, None, None)
    return found


def test_every_held_translation_is_reviewed_and_passes_nothing_over():
    bound = certificate_inputs()
    seen = {}
    for path in sorted(SOURCE.rglob('*.py')):
        rel = path.relative_to(SOURCE).as_posix()
        if rel not in bound:
            for func, handler in held_translations(ast.parse(path.read_text(encoding='utf-8'))):
                seen[(rel, func)] = handler
    assert set(seen) == set(REVIEWED), sorted(set(seen) ^ set(REVIEWED))
    passing_over = [key for key, handler in seen.items()
                    if any(isinstance(n, (ast.Continue, ast.Break, ast.Pass)) for n in ast.walk(handler))]
    assert not passing_over, passing_over


def test_the_held_translation_check_sees_the_pattern():
    source = ('def f(rows):\n for r in rows:\n  try:\n   try:\n    x = json.loads(r)\n'
              '   except (RecursionError, MemoryError):\n    raise json.JSONDecodeError("l", "", 0) from None\n'
              '  except json.JSONDecodeError:\n   continue\n')
    assert [func for func, _ in held_translations(ast.parse(source))] == ['f']
    assert not held_translations(ast.parse('def f(r):\n try:\n  x = json.loads(r)\n'
                                           ' except (RecursionError, MemoryError):\n'
                                           '  raise json.JSONDecodeError("l", "", 0) from None\n'))
