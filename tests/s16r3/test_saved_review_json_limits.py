"""Saved JSON readers refuse excessive nesting and exhausted decoder memory."""
import ast
import json
from pathlib import Path

import pytest

from g6.core.storage import g6_home
from g6.harness import behaviour_question as bq, behaviour_runner
from g6.mcp import behaviour, review_binding, review_reply


def test_home_json_readers_catch_recursion_and_memory_errors_by_ast():
    root = Path(behaviour.__file__).resolve().parents[1]
    expected = {'mcp/behaviour.py': {'_review_candidates'}, 'mcp/review_binding.py': {'matches'},
                'mcp/review_reply.py': {'carried'}, 'harness/behaviour_runner.py': {'read_probes'}}
    for rel, names in expected.items():
        tree = ast.parse((root / rel).read_text(encoding='utf-8'))
        # File-backed loads are the ones whose enclosing function reads bytes/text, plus json.load(file).
        found = set()
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            file_reader = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                              and n.func.attr in ('read_bytes', 'read_text', 'open') for n in ast.walk(fn))
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == 'json'
                        and (node.func.attr == 'load' or file_reader and node.func.attr == 'loads')):
                    continue
                found.add(fn.name)
                protected = [t for t in ast.walk(fn) if isinstance(t, ast.Try)
                             and any(node is child for body in t.body for child in ast.walk(body))]
                catches = {n.id for t in protected for handler in t.handlers if handler.type is not None
                           for n in ast.walk(handler.type) if isinstance(n, ast.Name)}
                assert {'RecursionError', 'MemoryError'} <= catches, (rel, fn.name, node.lineno, catches)
        assert found == names, (rel, found)


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
