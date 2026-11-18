# SPDX-License-Identifier: Apache-2.0
"""Observable proposals from task text and the shared semantic reader; never authority.

Repository text is parsed, never imported. Only declared schema positions supply
behaviour data; G6 owns ids, provenance, status, checks and executable templates.
"""
from __future__ import annotations

import ast
import hashlib
import json
import keyword
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core import semantic
from g6.core.git_name import shown
from g6.harness import verification_templates as vt
from g6.workforce import repo_tests

MAX_FILES = 500
MAX_BYTES = 1_000_000
MAX_TOTAL = 4_000_000
MAX_ITEMS = 12


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str = Field(min_length=1, max_length=500)
    kind: Literal['example', 'preservation', 'boundary']
    function: vt.Name
    cases: list[vt.ExampleCase] = Field(min_length=1, max_length=8)


class Reply(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: list[Proposal] = Field(max_length=MAX_ITEMS)


def sources(root):
    """Bounded git inventory, exact names and contained reads; never follow a listed link."""
    names = repo_tests._paths(repo_tests._git(root, 'ls-files', '-z'))
    found, total = {}, 0
    for rel in names:
        parts = PurePosixPath(rel).parts
        if (not rel.endswith('.py') or any(p.startswith('.') or p in {'venv', 'node_modules', '__pycache__'}
                                          for p in parts)):
            continue
        if len(found) >= MAX_FILES:
            break
        repo_tests._index_path(root, rel)
        path = repo_tests._disk(root, rel)
        if path is None or repo_tests._linked(path) or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        raw = repo_tests._read_inside(root, path)
        if raw is None or len(raw) > MAX_BYTES:
            continue
        total += len(raw)
        if total > MAX_TOTAL:
            break
        found[rel] = raw.decode('utf-8', 'replace')
    return found


def baseline(root, change, texts):
    """Original Python blobs, bounded before reading; Change.before holds tests only."""
    if not getattr(change, 'base_commit', ''):
        return {}
    original, total = {}, 0
    for rel, entry in sorted(repo_tests._listed_at(root, change.base_commit, texts).items()):
        mode, kind, oid, size = entry
        if kind != 'blob' or mode == '120000' or int(size) > MAX_BYTES or total + int(size) > MAX_TOTAL:
            continue
        raw = repo_tests.blob_bytes(root, oid)
        total += len(raw)
        if len(raw) <= MAX_BYTES and total <= MAX_TOTAL:
            original[rel] = raw.decode('utf-8', 'replace')
    return original


def _module(rel):
    # Only an unquoted, importable spelling can name a Python module. Odd git
    # bytes are shown by the rule, then refused as identifiers, never decoded ad hoc.
    parts = list(PurePosixPath(shown(rel)).with_suffix('').parts)
    if len(parts) > 1 and parts[0] == 'src':
        parts.pop(0)
    if parts and parts[-1] == '__init__':
        parts.pop()
    if not all(part.isascii() and part.isidentifier() and not keyword.iskeyword(part) for part in parts):
        return ''
    return '.'.join(parts)


def _trees(texts):
    found = {}
    for rel, text in texts.items():
        if len(text.encode('utf-8')) > MAX_BYTES:
            continue
        try:
            found[rel] = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            continue
    return found


def _symbols(trees):
    found = {f'{_module(rel)}.{node.name}': (rel, node) for rel, tree in trees.items()
            if not repo_tests.is_test_related(rel) for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _module(rel)}
    return {name: target for name, target in found.items()
            if all(part.isascii() and part.isidentifier() and not keyword.iskeyword(part) for part in name.split('.'))}


def _same(left, right):
    """Template inputs and results distinguish JSON types (including bool versus int)."""
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return False


def _name(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, '')
    if isinstance(node, ast.Attribute):
        parent = _name(node.value, aliases)
        return f'{parent}.{node.attr}' if parent else ''
    return ''


def _tests(trees, function, cases=()):
    """Map imports/calls, but concrete examples require an assertion of that case."""
    found = []
    for rel, tree in trees.items():
        if not repo_tests.is_test_related(rel):
            continue
        aliases = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                aliases.update({a.asname or a.name: f'{node.module}.{a.name}' for a in node.names})
            if isinstance(node, ast.Import):
                aliases.update({a.asname or a.name.split('.')[0]: a.name if a.asname else a.name.split('.')[0]
                                for a in node.names})

        def walk(body, prefix=''):
            for test in body:
                if isinstance(test, ast.ClassDef) and test.name.startswith('Test'):
                    walk(test.body, f'{prefix}{test.name}::')
                if not isinstance(test, (ast.FunctionDef, ast.AsyncFunctionDef)) or not test.name.startswith('test'):
                    continue
                # Local bindings hide module imports. A conservative miss is safer than invented coverage.
                local = {n.id for n in ast.walk(test) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                local.update(a.arg for a in (*test.args.posonlyargs, *test.args.args, *test.args.kwonlyargs))
                local.update(n.name for n in ast.walk(test) if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                             and n is not test)
                visible = {key: value for key, value in aliases.items() if key not in local}
                called = any(isinstance(n, ast.Call) and _name(n.func, aliases) == function for n in ast.walk(test))
                imported = function in aliases.values()
                if not cases and (called or imported):
                    found.append(f'{shown(rel)}::{prefix}{test.name}')
                    continue
                matched = []
                for case in cases:
                    hit = False
                    for assertion in ast.walk(test):
                        if not isinstance(assertion, ast.Assert):
                            continue
                        compare = assertion.test
                        if not (isinstance(compare, ast.Compare) and len(compare.ops) == 1
                                and isinstance(compare.ops[0], ast.Eq) and isinstance(compare.left, ast.Call)):
                            continue
                        call = compare.left
                        if _name(call.func, visible) != function or any(k.arg is None for k in call.keywords):
                            continue
                        try:
                            hit |= _same(dict(args=[ast.literal_eval(a) for a in call.args],
                                         kwargs={k.arg: ast.literal_eval(k.value) for k in call.keywords},
                                         returns=ast.literal_eval(compare.comparators[0])),
                                    dict(args=case.get('args', []), kwargs=case.get('kwargs', {}),
                                         **{k: v for k, v in case.items() if k not in ('args', 'kwargs')}))
                        except (ValueError, TypeError, RecursionError):
                            pass
                    matched.append(hit)
                if matched and all(matched):
                    found.append(f'{shown(rel)}::{prefix}{test.name}')
        walk(tree.body)
    return sorted(set(found))


def _floor(task, symbols):
    named = [name for name in symbols if re.search(rf'\b{re.escape(name.rsplit(".", 1)[-1])}\s*\(', task)]
    # A bare named symbol is sufficient only when unambiguous in the repository.
    if not named:
        named = [name for name in symbols if re.search(rf'\b{re.escape(name.rsplit(".", 1)[-1])}\b', task)]
    named = [name for name in named if sum(n.rsplit('.', 1)[-1] == name.rsplit('.', 1)[-1] for n in named) == 1]
    items = []
    for target in named[:4]:
        function = shown(target)
        symbol = function.rsplit('.', 1)[-1]
        if re.search(r'\b(without changing|keep|preserv\w*|unchanged)\b', task, re.I):
            items.append(dict(text=f'Keep {symbol} signature unchanged and preserve its existing tests',
                              kind='preservation', function=function, cases=[]))
        if re.search(r'\b(adds?|sum|addition)\b', task, re.I) and re.search(r'\bintegers?\b', task, re.I):
            for label, args in [('positive', [2, 3]), ('negative', [-2, -3])]:
                if label in task.lower():
                    items.append(dict(text=f'{symbol} adds {label} integers', kind='example', function=function,
                                      cases=[dict(args=args, returns=sum(args))]))
            items.append(dict(text=f'{symbol}(1.5, 2) returns 3.5: candidate boundary case only',
                              kind='boundary', function=function, cases=[dict(args=[1.5, 2], returns=3.5)]))
        if not any(i['function'] == function and i['kind'] == 'example' for i in items):
            items.append(dict(text=f'{symbol}: {task[:350]}', kind='example', function=function, cases=[]))
    if not items:
        items.append(dict(text=task[:500], kind='example', function='', cases=[]))
    return items


def _instance(item):
    if not item['function'] or not item['cases']:
        return None
    return dict(id=item['id'] + '.example', requirement_id=item['id'], template='example.v1', intent='change',
                bindings=dict(function=item['function'], cases=item['cases']))


def from_sources(texts, task, *, repo):
    """Pure static input seam, also used by the bounded repository adapter."""
    trees = _trees(texts)
    symbols = _symbols(trees)
    floor = _floor(task, symbols)
    with semantic.use_request() as scope:
        calls = scope.calls
        context = json.dumps({'task': task, 'functions': sorted(symbols)}, ensure_ascii=True)
        reading = semantic.read(semantic.Question('candidate_standard', context, Reply, lambda: [], 'choose'))
        model_calls = scope.calls - calls
    model = [p.model_dump(exclude_unset=True) if isinstance(p, Proposal) else p for p in reading.value]
    # choose governs the model reading; task-text items are an independent source and always retained.
    items = [dict(i, origin='task_text') for i in floor]
    for proposal in model:
        if proposal['function'] in symbols:
            items.append(dict(proposal, origin='model_proposal'))
    standard, unknowns = [], []
    for raw in items[:MAX_ITEMS]:
        function = raw['function']
        ident = 'candidate.' + hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:12]
        item = dict(raw, id=ident, status='candidate')
        checks = _tests(trees, function, item['cases']) if function else []
        item['checks'] = checks or 'missing'
        instance = _instance(item)
        # These calls omit requirements deliberately; the review reconstructs the same candidate ids.
        args = dict(repo=str(repo), task=task)
        if instance:
            args['template_instances'] = [instance]
        item['cheapest_check'] = dict(tool='g6_review_patch', arguments=args)
        if not checks:
            unknowns.append(dict(id=ident, text=item['text'], cheapest_check=item['cheapest_check'],
                                 reason='No static repository test maps to this observable candidate.'))
        standard.append(item)
    question = None
    def affected(item):
        """Count check identities for this input and either offered output, not unrelated imports."""
        changed = set()
        for other in standard:
            if other['function'] != item['function']:
                continue
            for case in other['cases']:
                if (_same(case.get('args', []), [1.5, 2]) and not case.get('kwargs')
                        and ({k: v for k, v in case.items() if k not in ('args', 'kwargs')}
                             in ({'returns': 3.5}, {'raises': 'TypeError'}))):
                    changed.add(_instance(other)['id'])
                    if isinstance(other['checks'], list):
                        changed.update(other['checks'])
        return changed

    # Rank the bounded concrete ambiguities by checks whose expected outcome can change.
    ranked = sorted((i for i in standard if i['kind'] == 'boundary' and i['origin'] == 'task_text' and i['cases']),
                    key=lambda i: (-len(affected(i)), i['id']))
    if ranked:
        item = ranked[0]
        if re.search(r'\bintegers?\b', task, re.I) and re.search(r'\b(adds?|sum)\b', task, re.I):
            candidate = dict(requirement_id=item['id'], function=item['function'], args=[1.5, 2], kwargs={},
                outcomes={'A': {'returns': 3.5}, 'B': {'raises': 'TypeError'}}, cited=task,
                evidence=[f'{shown(symbols[item["function"]][0])}: named function; integer-only versus numeric API'])
            question = dict(text=f'For {item["function"]}(1.5, 2), should it return 3.5 or raise TypeError?',
                            candidate=candidate, changes_checks=len(affected(item)),
                            affected_checks=sorted(affected(item)))
    mechanism = reading.mechanism()
    if reading.by == 'floor' and reading.reason in {'invalid model reply', 'model failed'}:
        mechanism['reason'] = 'model reading failed: ' + reading.reason
    return dict(standard=standard, unknowns=unknowns, question=question,
                test_files=sorted(shown(rel) for rel in trees if repo_tests.is_test_related(rel)),
                mechanisms=[mechanism], model_calls=model_calls)


def with_confirmed(found, standard):
    """Only an exactly matching, one-case answer can change a candidate's displayed status."""
    shown = deepcopy(found)
    if standard is None:
        return shown
    for one in standard.expectations:
        bindings = one.instance.bindings
        for item in shown['standard']:
            if (item['id'] != one.requirement_id or item['kind'] == 'preservation'
                    or item['function'] != bindings['function'] or len(item['cases']) != 1):
                continue
            answered, = bindings['cases']
            proposed, = item['cases']
            if (not _same(proposed.get('args', []), answered.get('args', []))
                    or not _same(proposed.get('kwargs', {}), answered.get('kwargs', {}))):
                continue
            item.update(status='stated', cases=deepcopy(bindings['cases']), text=one.scope, scope=one.scope,
                        confirmed_by=one.answered_by, expectation_id=one.expectation_id)
    return shown


def build(root, task, *, before=None):
    texts = sources(root)
    for rel, text in (before or {}).items():
        if rel.endswith('.py') and text and rel not in texts:
            texts[rel] = text
    return from_sources(texts, task, repo=root)


_SIGNATURE = '''import ast
import json
from pathlib import Path
BINDINGS = json.loads(@BINDINGS@)

def test_signature():
    path, name, signature, returns, asynchronous = BINDINGS
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    assert ast.dump(node.args) == signature
    assert (ast.dump(node.returns) if node.returns is not None else "") == returns
    assert isinstance(node, ast.AsyncFunctionDef) == asynchronous
'''


def checks(found, texts, *, before=None, budget=20):
    """G6 constructs proposed checks; no candidate is added to normative inputs."""
    from g6.harness import verification_search as vs

    proposed, instances, origins, skipped = [], [], {}, []
    trees = _trees(before or {})
    symbols = _symbols(trees)

    def admit(item, ident):
        if len(proposed) + len(instances) >= budget:
            skipped.append(dict(activity='candidate check', check=ident, origin=item['origin'], candidate_id=item['id'],
                                result='skipped', reason='candidate check limit', model_calls=0, seconds=0.0))
            return False
        origins[ident] = vs.Origin(activity='candidate check', label='candidate',
            proposed_by=item['origin'], about={'origin': item['origin'], 'candidate_id': item['id']},
            unconfirmed='this behaviour is still a candidate and decides no requirement')
        return True

    # Concrete missing examples and API signatures precede potentially numerous mapped tests.
    for item in found['standard']:
        instance = _instance(item)
        if instance and admit(item, instance['id']):
            instances.extend(one.model_copy(update={'provenance': 'model_hypothesis'})
                             for one in vt.template_instances([instance], [item['id']]))
        if item['kind'] == 'preservation' and item['function'] not in symbols:
            skipped.append(dict(activity='candidate check', check=item['id'] + '.signature', origin=item['origin'],
                candidate_id=item['id'],
                result='skipped', reason='original signature unavailable at the requested base',
                model_calls=0, seconds=0.0))
        if item['kind'] == 'preservation' and item['function'] in symbols:
            rel, node = symbols[item['function']]
            ident = item['id'] + '.signature'
            path, exact = repo_tests._on_disk(rel)
            if not exact:
                skipped.append(dict(activity='candidate check', check=ident, origin=item['origin'],
                    candidate_id=item['id'], result='skipped', reason='original signature path is not exact on disk',
                    model_calls=0, seconds=0.0))
                continue
            if admit(item, ident):
                bindings = [path, node.name, ast.dump(node.args), ast.dump(node.returns) if node.returns else '',
                            isinstance(node, ast.AsyncFunctionDef)]
                source = _SIGNATURE.replace('@BINDINGS@', ascii(json.dumps(bindings, ensure_ascii=True)))
                proposed.append(vs.ProposedCheck(id=ident, requirement_id=item['id'], intent='preserve',
                    file='test_g6_candidate_signature.py', source=source))
    seen = set()
    for item in found['standard']:
        for node in item['checks'] if isinstance(item['checks'], list) else []:
            if node in seen:
                continue
            rel, _, test = node.partition('::')
            if rel not in texts:
                continue
            ident = item['id'] + '.mapped.' + hashlib.sha256(node.encode()).hexdigest()[:8]
            if admit(item, ident):
                seen.add(node)
                try:
                    proposed.extend(vs.proposed_checks([dict(id=ident, requirement_id=item['id'], intent='preserve',
                        file=rel, source=texts[rel], test=test)], [item['id']]))
                except ValueError as exc:
                    origins.pop(ident, None)
                    skipped.append(dict(activity='candidate check', check=ident, origin=item['origin'],
                        candidate_id=item['id'],
                        result='skipped', reason=str(exc), model_calls=0, seconds=0.0))
    return proposed, instances, origins, skipped


def annotate(reply, found, claim, confirmed=None):
    """Readable observations are appended only after the existing authority decides."""
    assessment = reply.get('assessment')
    if not isinstance(assessment, dict):
        return reply
    found = with_confirmed(found, confirmed)
    assessment['standard'] = found['standard']
    assessment['candidate_unknowns'] = found['unknowns']
    assessment['question'] = found['question']
    assessment['mechanisms'] = found['mechanisms']
    acts = assessment['completion']['activities']
    outcomes = {}
    for activity in acts:
        if activity.get('candidate_id'):
            outcomes[activity.get('result', 'unknown')] = outcomes.get(activity.get('result', 'unknown'), 0) + 1
    observed = ', '.join(f'{count} {result}' for result, count in sorted(outcomes.items())) or 'no candidate check result'
    # Caller assertions are displayed as one line of data, never interpreted as a report field.
    from g6.harness.behaviour_question import caller_text
    completed = sum(count for result, count in outcomes.items() if result not in {'error', 'refused', 'skipped'})
    summary = [f'G6 ran {completed} candidate checks to a result; recorded: {observed}.',
               'The agent asserted: ' + (' '.join(caller_text(claim, literal=False).split())[:500] or 'no claim supplied'),
               f'Still candidate: {sum(c["status"] == "candidate" for c in found["standard"])} behaviours; '
               'candidate checks pass no requirement.']
    assessment['report'] = '\n'.join([*summary, '', assessment['report']])
    return reply
