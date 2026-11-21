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


def _test_functions(trees):
    """Each repository test function, its node id, and the module imports visible inside it (and all of them)."""
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
                    yield from walk(test.body, f'{prefix}{test.name}::')
                if not isinstance(test, (ast.FunctionDef, ast.AsyncFunctionDef)) or not test.name.startswith('test'):
                    continue
                # Local bindings hide module imports. A conservative miss is safer than invented coverage.
                local = {n.id for n in ast.walk(test) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                local.update(a.arg for a in (*test.args.posonlyargs, *test.args.args, *test.args.kwonlyargs))
                local.update(n.name for n in ast.walk(test) if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                             and n is not test)
                visible = {key: value for key, value in aliases.items() if key not in local}
                yield f'{shown(rel)}::{prefix}{test.name}', test, aliases, visible
        yield from walk(tree.body)


def _case(call):
    """A call written with literal arguments as an example case's input, or None."""
    try:
        if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            return None
        case = dict(args=[ast.literal_eval(a) for a in call.args])
        kwargs = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if kwargs:
        case['kwargs'] = kwargs
    return None if vt.literal_problem(case) else case


def _asserted(test, function, visible):
    """The cases a test asserts as ``function(literal arguments) == literal``, read from its syntax tree."""
    found = []
    for assertion in ast.walk(test):
        compare = assertion.test if isinstance(assertion, ast.Assert) else None
        if not (isinstance(compare, ast.Compare) and len(compare.ops) == 1 and isinstance(compare.ops[0], ast.Eq)
                and isinstance(compare.left, ast.Call) and _name(compare.left.func, visible) == function):
            continue
        case = _case(compare.left)
        try:
            value = ast.literal_eval(compare.comparators[0])
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
        if case is not None and not vt.literal_problem(value):
            found.append(dict(case, returns=value))
    return found


def _full(case):
    return dict(args=case.get('args', []), kwargs=case.get('kwargs', {}),
                **{k: v for k, v in case.items() if k not in ('args', 'kwargs')})


def _tests(trees, function, cases=()):
    """Map imports/calls, but concrete examples require an assertion of that case."""
    found = []
    for node, test, aliases, visible in _test_functions(trees):
        if not cases:
            called = any(isinstance(n, ast.Call) and _name(n.func, aliases) == function for n in ast.walk(test))
            if called or function in aliases.values():
                found.append(node)
            continue
        stated = [_full(one) for one in _asserted(test, function, visible)]
        if all(any(_same(one, _full(case)) for one in stated) for case in cases):
            found.append(node)
    return sorted(set(found))


MAX_CALL = 500


def _stated_calls(task, symbol):
    """Each call of ``symbol`` the task writes as Python: its text, its syntax tree and where it ends."""
    found = []
    for match in re.finditer(rf'(?<![\w.]){re.escape(symbol)}(?=\()', task):
        depth, end = 0, None
        for index in range(match.end(), min(len(task), match.end() + MAX_CALL)):
            depth += {'(': 1, ')': -1}.get(task[index], 0)
            if depth == 0:
                end = index + 1
                break
        if end is None:
            continue
        try:
            call = ast.parse(task[match.start():end], mode='eval').body
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            continue
        if isinstance(call, ast.Call):
            found.append((task[match.start():end], call, end))
    return found


_NO_VALUE = object()


def _literal(text):
    """The shortest Python literal ``text`` starts with (a sentence's closing full stop left out), or ``_NO_VALUE``."""
    for end in range(1, min(len(text), MAX_CALL) + 1):
        if end < len(text) and not (text[end].isspace() or text[end] in ',;)'):
            continue
        piece = text[:end]
        for written in ((piece[:-1], piece) if piece.endswith('.') else (piece,)):
            try:
                value = ast.literal_eval(written)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                continue
            if not vt.literal_problem(value):
                return value
    return _NO_VALUE


def _floor(task, symbols):
    """Only what the task writes as Python: a named function's signature, and examples ``f(args) == value``.
    No word of the task is read for meaning (owner direction, 2026-11-17): what a task says in prose is the shared
    reader's to propose and the person's to confirm."""
    named = [name for name in symbols if re.search(rf'\b{re.escape(name.rsplit(".", 1)[-1])}\s*\(', task)]
    # A bare named symbol is sufficient only when unambiguous in the repository.
    if not named:
        named = [name for name in symbols if re.search(rf'\b{re.escape(name.rsplit(".", 1)[-1])}\b', task)]
    named = [name for name in named if sum(n.rsplit('.', 1)[-1] == name.rsplit('.', 1)[-1] for n in named) == 1]
    items = []
    for target in named[:4]:
        function = shown(target)
        symbol = function.rsplit('.', 1)[-1]
        calls = _stated_calls(task, symbol)
        # A call whose arguments are all plain names is the signature the task writes; two different ones state none.
        signatures = {tuple(a.id for a in call.args) for _, call, _ in calls
                      if not call.keywords and all(isinstance(a, ast.Name) for a in call.args)}
        if len(signatures) == 1:
            parameters, = signatures
            items.append(dict(text=f'Keep the signature {symbol}({", ".join(parameters)}) the task writes, and '
                                   'preserve its existing tests', kind='preservation', function=function, cases=[],
                              parameters=list(parameters)))
        for text, call, end in calls:
            equals = re.match(r'\s*==\s*', task[end:end + MAX_CALL])
            case = _case(call) if equals else None
            value = _literal(task[end + equals.end():]) if case is not None else _NO_VALUE
            if value is not _NO_VALUE:
                example = dict(text=f'The task states {text} == {json.dumps(value)}', kind='example',
                               function=function, cases=[dict(case, returns=value)])
                if example not in items:
                    items.append(example)
        if not any(i['function'] == function and i['kind'] == 'example' for i in items):
            items.append(dict(text=f'{symbol}: {task[:350]}', kind='example', function=function, cases=[]))
    if not items:
        items.append(dict(text=task[:500], kind='example', function='', cases=[]))
    return items


def _twin(case):
    """The stated example's call with its first whole-number argument written as the equal float, or None. Whether
    the function must then give the stated result or refuse the input is the question only the person settles."""
    args = case.get('args', [])
    at = next((n for n, value in enumerate(args) if type(value) is int and abs(value) <= 2 ** 53), None)
    if at is None or set(case) != {'args', 'returns'}:
        return None
    return [*args[:at], float(args[at]), *args[at + 1:]]


def _boundaries(items, trees, symbols):
    """One boundary candidate per named function, from its first explicit example with a whole-number argument: the
    task's own examples first, then an assertion in the repository's tests. Each with the line that is its evidence."""
    found = []
    for function in dict.fromkeys(i['function'] for i in items if i['function']):
        defined = shown(symbols[function][0]) if function in symbols else function
        stated = [(case, 'task_text', f'{defined}: defines {function}; the task states '
                   f'{function.rsplit(".", 1)[-1]}({_args(case)}) == {json.dumps(case["returns"])}')
                  for i in items if i['function'] == function and i['kind'] == 'example' for case in i['cases']]
        stated += [(case, 'repository_test', f'{node}: asserts {function.rsplit(".", 1)[-1]}({_args(case)}) == '
                    f'{json.dumps(case["returns"])}')
                   for node, test, _, visible in _test_functions(trees) for case in _asserted(test, function, visible)]
        for case, origin, evidence in stated:
            twin = _twin(case)
            if twin is not None:
                symbol = function.rsplit('.', 1)[-1]
                found.append((dict(text=f'{symbol}({_args(dict(args=twin))}) gives {json.dumps(case["returns"])} '
                                        f'as {symbol}({_args(case)}) does: candidate boundary case only',
                                   kind='boundary', function=function,
                                   cases=[dict(args=twin, returns=case['returns'])], origin=origin),
                              case, evidence))
                break
    return found


def _args(case):
    return json.dumps(case.get('args', []))[1:-1]


def _ident(raw):
    """A model proposal's id binds its executable content alone, so the call its reply advertised is checked against
    the id it names (``advertised``), never against a later model reply; every other id binds the whole item."""
    bound = ({key: raw[key] for key in ('origin', 'function', 'cases')} if raw['origin'] == 'model_proposal'
             else raw)
    return 'candidate.' + hashlib.sha256(json.dumps(bound, sort_keys=True).encode()).hexdigest()[:12]


def _advertised(instances, symbols, confirmed=None):
    """Model proposals an earlier reply advertised as ``cheapest_check`` calls: each supplied example.v1 instance whose
    candidate id is the digest of its own function and cases (``_ident``), for a function the repository defines. Once
    the person confirmed a case for that candidate, the call G6 advertises carries the confirmed case under the
    candidate's id (``with_confirmed``): that instance is admitted when ``confirmed`` holds exactly its function and
    cases for that id, and the candidate keeps the id."""
    held = []
    for answer in confirmed.expectations if confirmed is not None else ():
        try:
            held.append((answer.requirement_id, {key: answer.instance.bindings[key] for key in ('function', 'cases')}))
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            continue
    found = []
    for one in instances if isinstance(instances, (list, tuple)) else ():
        try:
            if not isinstance(one, dict) or one.get('template') != 'example.v1':
                continue
            bindings = one['bindings']
            proposal = Proposal.model_validate(dict(
                text=f'{bindings["function"]}: a model proposal an earlier reply advertised', kind='example',
                function=bindings['function'], cases=bindings['cases'])).model_dump(exclude_unset=True)
            item = dict(proposal, origin='model_proposal')
            if proposal['function'] not in symbols:
                continue
            if one['requirement_id'] == _ident(item):
                found.append(item)
            elif any(ident == one['requirement_id'] and _same(stated, {key: bindings[key] for key in stated})
                     for ident, stated in held):
                found.append(dict(item, id=one['requirement_id']))
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            continue
    return found


def _instance(item):
    # A boundary case is a question's offered outcome, not a behaviour: it runs only once the person states it.
    if not item['function'] or not item['cases'] or item['kind'] == 'boundary' and item['status'] != 'stated':
        return None
    return dict(id=item['id'] + '.example', requirement_id=item['id'], template='example.v1', intent='change',
                bindings=dict(function=item['function'], cases=item['cases']))


def from_sources(texts, task, *, repo, advertised=None, confirmed=None):
    """Pure static input seam, also used by the bounded repository adapter. ``advertised``: the template instances a
    review was given, whose model candidates an earlier reply advertised (``_advertised``); ``confirmed``: the
    person's confirmed answers, which keep a confirmed model candidate's advertised call admissible."""
    trees = _trees(texts)
    symbols = _symbols(trees)
    floor = [dict(i, origin='task_text') for i in _floor(task, symbols)]
    boundaries = _boundaries(floor, trees, symbols)
    with semantic.use_request() as scope:
        calls = scope.calls
        context = json.dumps({'task': task, 'functions': sorted(symbols)}, ensure_ascii=True)
        reading = semantic.read(semantic.Question('candidate_standard', context, Reply, lambda: [], 'choose'))
        model_calls = scope.calls - calls
    model = [p.model_dump(exclude_unset=True) if isinstance(p, Proposal) else p for p in reading.value]
    # choose governs the model reading; task-text items are an independent source and always retained.
    items = [*floor, *(item for item, _, _ in boundaries), *_advertised(advertised, symbols, confirmed)]
    for proposal in model:
        if proposal['function'] in symbols:
            items.append(dict(proposal, origin='model_proposal'))
    standard, unknowns, seen, basis = [], [], set(), {}
    for raw in items:
        ident = raw.pop('id', None) or _ident(raw)
        if ident in seen:
            continue
        seen.add(ident)
        if len(standard) >= MAX_ITEMS:
            break
        function = raw['function']
        item = dict(raw, id=ident, status='candidate')
        basis.update({ident: (case, evidence) for boundary, case, evidence in boundaries if boundary is raw})
        checks = _tests(trees, function, item['cases']) if function else []
        item['checks'] = checks or 'missing'
        instance = _instance(item)
        # These calls omit requirements deliberately; the review reconstructs the same candidate ids: the task's from
        # the task and the repository, a model proposal's from the instance the call carries (``_advertised``).
        args = dict(repo=str(repo), task=task)
        if instance:
            args['template_instances'] = [instance]
        item['cheapest_check'] = dict(tool='g6_review_patch', arguments=args)
        if not checks:
            unknowns.append(dict(id=ident, text=item['text'], cheapest_check=item['cheapest_check'],
                                 reason='No static repository test maps to this observable candidate.'))
        standard.append(item)
    question = None

    def outcomes(item):
        return {'A': {'returns': item['cases'][0]['returns']}, 'B': {'raises': 'TypeError'}}

    def affected(item):
        """Count check identities for this input and either offered output, not unrelated imports."""
        changed = set()
        for other in standard:
            if other['function'] != item['function']:
                continue
            for case in other['cases']:
                if (_same(case.get('args', []), item['cases'][0]['args']) and not case.get('kwargs')
                        and any(_same({k: v for k, v in case.items() if k not in ('args', 'kwargs')}, one)
                                for one in outcomes(item).values())):
                    changed.add(other['id'] + '.example')   # its example.v1 check, now or once the person states it
                    if isinstance(other['checks'], list):
                        changed.update(other['checks'])
        return changed

    # Rank the bounded concrete ambiguities, each from an explicit example, by checks whose expected outcome can change.
    ranked = sorted((i for i in standard if i['id'] in basis), key=lambda i: (-len(affected(i)), i['id']))
    if ranked:
        from g6.harness.behaviour_question import _citation
        item = ranked[0]
        stated, evidence = basis[item['id']]
        symbol = item['function'].rsplit('.', 1)[-1]
        candidate = dict(requirement_id=item['id'], function=item['function'], args=item['cases'][0]['args'],
                         kwargs={}, outcomes=outcomes(item), cited=_citation(task, item['function']),
                         evidence=[evidence])
        question = dict(text=f'For {item["function"]}({_args(item["cases"][0])}), should it return '
                             f'{json.dumps(stated["returns"])} as {symbol}({_args(stated)}) does, or raise TypeError?',
                        candidate=candidate, changes_checks=len(affected(item)), affected_checks=sorted(affected(item)))
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
    rejected, outcomes = set(), []   # the mapped tests and the (function, case) pairs that assert a rejected outcome
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
            # The repository tests mapped to the candidate assert the case as it was proposed (``_tests`` maps a case by
            # its whole outcome): once the person states another outcome for that call, they check the one rejected,
            # so none of them is a check of this candidate or of any other.
            if not _same(proposed, answered):
                rejected.update(item['checks'] if isinstance(item['checks'], list) else ())
                outcomes.append((item['id'], item['function'], _full(proposed)))
                item['checks'] = 'missing'
            item.update(status='stated', cases=deepcopy(bindings['cases']), text=one.scope, scope=one.scope,
                        confirmed_by=one.answered_by, expectation_id=one.expectation_id)
            # The call it offers runs what the person confirmed, never the alternative they did not choose.
            if _instance(item) and 'cheapest_check' in item:
                item['cheapest_check']['arguments']['template_instances'] = [_instance(item)]
    for item in shown['standard']:
        if isinstance(item['checks'], list) and rejected & set(item['checks']):
            item['checks'] = [node for node in item['checks'] if node not in rejected] or 'missing'
        # Another candidate's own example of the same typed call with the rejected outcome checks it too: that case
        # goes, and the call the candidate offers carries only what is left (none when nothing is).
        kept = [case for case in item['cases'] if not _rejects(outcomes, item, case)]
        if len(kept) != len(item['cases']):
            item['cases'] = kept
            arguments = item.get('cheapest_check', {}).get('arguments', {})
            if _instance(item):
                arguments['template_instances'] = [_instance(item)]
            else:
                arguments.pop('template_instances', None)
    shown['rejected'] = [dict(function=function, case=case) for _, function, case in outcomes]
    return shown


def _rejects(outcomes, item, case):
    """Whether ``case`` of ``item`` asserts an outcome the person rejected for that same typed call."""
    return any(ident != item['id'] and function == item['function'] and _same(_full(case), rejected)
               for ident, function, rejected in outcomes)


def owns(found, one):
    """Whether a supplied example.v1 check is one of a candidate's own, whatever its id: every one for a candidate the
    person confirmed (their answer replaces them all), and, for one not yet confirmed, one whose case is a call the
    candidate states (the same function, arguments and keyword arguments). The candidate's checks are then the ones
    its standard states (``checks``)."""
    if getattr(one, 'template', '') != 'example.v1':
        return False
    bindings = one.bindings if isinstance(one.bindings, dict) else {}
    cases = bindings.get('cases') if isinstance(bindings.get('cases'), list) else []
    if any(isinstance(case, dict) and gone['function'] == bindings.get('function') and _same(_full(case), gone['case'])
           for case in cases for gone in found.get('rejected', ())):
        return True   # a saved call carrying an outcome the person rejected only advertised its candidate
    item = next((i for i in found['standard'] if i['id'] == getattr(one, 'requirement_id', None)), None)
    if item is None or not item['function']:
        return False
    if item['status'] == 'stated':
        return True
    return bindings.get('function') == item['function'] and any(
        isinstance(case, dict) and _same(case.get('args', []), own.get('args', []))
        and _same(case.get('kwargs', {}), own.get('kwargs', {})) for case in cases for own in item['cases'])


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
            if 'parameters' in item and item['parameters'] != [a.arg for a in (*node.args.posonlyargs,
                                                                                 *node.args.args)]:
                skipped.append(dict(activity='candidate check', check=ident, origin=item['origin'],
                    candidate_id=item['id'], result='skipped', reason='the base does not have the stated signature',
                    model_calls=0, seconds=0.0))
                continue
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
