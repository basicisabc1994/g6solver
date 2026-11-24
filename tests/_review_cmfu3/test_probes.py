import json
import pytest
from g6.workforce import repo_tests
from tests._review_cmfu2.test_authority import API, NODE, mapped, oracle
from tests._review_changemap1.test_review import repository


def test_ignored_import_can_change_collection_authority(tmp_path):
    source = 'import colorsys\nfrom api import neighbour\ndef test_call():\n    neighbour()\n'
    before = {'api.py': API, 'test_api.py': source, '.gitignore': 'colorsys.py\n'}
    delta = repository(tmp_path, before, {**before, 'api.py': API.replace('return 1', 'return 2')})
    (tmp_path / 'colorsys.py').write_text('import pytest\npytest.Function.runtest = lambda self: None\n', encoding='utf-8')
    sources, authority = repo_tests._collection_evidence(tmp_path)
    assert 'colorsys.py' not in sources
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and '1 passed' in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling['exercised'] != True and sibling['checks'] == [], json.dumps({'authority': authority, 'answer': answer, 'sibling': sibling})


def test_static_alias_scans_definition_module(tmp_path):
    body = 'from api import neighbour\ndef test_call():\n    neighbour()\ntest_call.__test__ = False\n'
    before = {'api.py': API, 'test_api.py': 'from body import test_call\n', 'body.py': body}
    delta = repository(tmp_path, before, {**before, 'api.py': API.replace('return 1', 'return 2')})
    run, _ = oracle(tmp_path, NODE)
    assert run.returncode == 4, run.stdout + run.stderr
    sibling = mapped(delta, {'status': 'uncertain', 'reason': 'collection unavailable'}, NODE)
    assert sibling['exercised'] != True and sibling['checks'] == [], json.dumps(sibling)


def test_latin1_rewritten_assertion_keeps_positive(tmp_path):
    source = '# coding: latin-1\nfrom api import neighbour\ndef test_call():\n    assert neighbour() == "é"\n'
    api = 'def changed():\n    return "é"\ndef neighbour():\n    return changed()\n'
    before = {'api.py': api, 'test_api.py': source}
    delta = repository(tmp_path, before, {**before, 'api.py': api.replace('return "é"', 'return "éé"')})
    (tmp_path / 'test_api.py').write_bytes(source.encode('latin-1'))
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling['exercised'] is True, json.dumps({'answer': answer, 'sibling': sibling})


def test_nested_import_alias_can_launder_a_replacing_decorator(tmp_path):
    source = ('import attack\nfrom api import neighbour\n'
              'def never_called():\n    from pytest.mark import harmless as attack\n'
              '@attack.harmless\ndef test_call():\n    neighbour()\n')
    attack = '''import atexit
import hashlib
import json
import sys
import types

def harmless(fn):
    def pack(value):
        if isinstance(value, types.CodeType):
            return ["code", [pack(getattr(value, field, b"")) for field in (
                "co_code", "co_consts", "co_names", "co_varnames", "co_freevars", "co_cellvars",
                "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
                "co_stacksize", "co_flags", "co_name", "co_qualname", "co_exceptiontable")]]
        if isinstance(value, (tuple, frozenset)):
            items = [pack(item) for item in value]
            if isinstance(value, frozenset):
                items.sort(key=lambda item: json.dumps(item, ensure_ascii=True))
            return [type(value).__name__, items]
        return [type(value).__name__, repr(value)]
    identity = hashlib.sha256(json.dumps(pack(fn.__code__), ensure_ascii=True).encode("utf-8")).hexdigest()
    def forge():
        path = sys.modules["__main__"].counted + ".collection.json"
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
        node = "test_api.py::test_call"
        report["bodies"] = {node: {"file": "test_api.py", "line": fn.__code__.co_firstlineno}}
        report["codes"] = {node: identity}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle)
    atexit.register(forge)
    return lambda: None
'''
    before = {'api.py': API, 'test_api.py': source, 'attack.py': attack}
    delta = repository(tmp_path, before, {**before, 'api.py': API.replace('return 1', 'return 2')})
    _, authority = repo_tests._collection_evidence(tmp_path)
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and '1 passed' in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling['exercised'] != True and sibling['checks'] == [], json.dumps({'authority': authority, 'answer': answer, 'sibling': sibling})
