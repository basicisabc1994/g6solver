# SPDX-License-Identifier: Apache-2.0
"""Bounded, data-only discovery of changed Python behaviour; never requirement evidence."""
from __future__ import annotations

import ast
import hashlib
import itertools
import json
import re
import time
from dataclasses import dataclass, field, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, create_model

from g6.core import semantic
from g6.harness import verification_search as vs, verification_templates as vt
from g6.harness.verification_proposer import boundary
from g6.workforce import repo_tests

ACTIVITY_S = 15.0
MAX_TEST_FILES = 200
MAX_FUNCTIONS = 200


@dataclass
class Discovery:
    checks: list = field(default_factory=list)
    placed: dict = field(default_factory=dict)
    origins: dict = field(default_factory=dict)
    activities: list = field(default_factory=list)


def _activity(reason, *, target='', result='skipped', **extra):
    return {'activity': 'automatic discovery', 'target': target, 'result': result, 'reason': reason,
            'model_calls': 0, 'seconds': 0.0, **extra}


def _module(rel, tree):
    for folder in sorted(tree.folders(), key=len, reverse=True):
        if rel.startswith(folder):
            name = rel[len(folder):].removesuffix('.py').removesuffix('/__init__').replace('/', '.')
            if (all(p.isidentifier() and not p.startswith('_') for p in name.split('.')) and
                    all(vt._module_file(tree, name.split('.'), side) == rel for side in (vt.BASE, vt.PATCH))):
                return name
    return ''


def _functions(text):
    return {node.name: node for node in ast.parse(text).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith('_')}


def _methods(text):
    return {f'{node.name}.{method.name}': method for node in ast.parse(text).body
            if isinstance(node, ast.ClassDef) and not node.name.startswith('_') for method in node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith('_')}


def _values(annotation):
    if annotation is None:
        return []
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode='eval').body
        except SyntaxError:
            return []
    if isinstance(annotation, ast.Name):
        return {'int': [0, -1, 1_000_000], 'float': [0.0, -1.0, 1_000_000.0],
                'str': ['', 'a'], 'bool': [False, True], 'list': [[]], 'dict': [{}]}.get(annotation.id, [])
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _values(annotation.left) + _values(annotation.right)
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return [None]
    if isinstance(annotation, ast.Subscript):
        name = annotation.value.id if isinstance(annotation.value, ast.Name) else (
            annotation.value.attr if isinstance(annotation.value, ast.Attribute) else '')
        if name == 'Optional':
            return _values(annotation.slice) + [None]
        if name in ('list', 'List', 'Sequence'):
            return [[]]
        if name in ('dict', 'Dict', 'Mapping'):
            return [{}]
    return []


def _literal(node):
    value = ast.literal_eval(node)
    # JSON round trip refuses sets, bytes, complex numbers and non-finite values.
    text = json.dumps(value, allow_nan=False)
    if len(text) > vt.MAX_CALL_CHARS:
        raise ValueError('literal exceeds call limit')
    return json.loads(text)


def _literal_calls(tree, target, until, activities):
    module, name = target.rsplit('.', 1)
    calls = []
    tests = sorted(rel for rel in tree.at_base if repo_tests.is_test_file(rel) and rel.endswith('.py'))
    if len(tests) > MAX_TEST_FILES:
        activities.append(_activity('test literal scan capped at 200 files', target=target))
    for rel in tests[:MAX_TEST_FILES]:
        if time.monotonic() >= until:
            activities.append(_activity('time limit during test literal scan', target=target))
            break
        try:
            parsed = ast.parse(tree.text(rel, vt.BASE))
        except (ValueError, SyntaxError):
            continue
        aliases = {a.asname or a.name for n in parsed.body if isinstance(n, ast.ImportFrom)
                   and n.module == module and n.level == 0 for a in n.names if a.name == name}
        modules = {a.asname or a.name for n in parsed.body if isinstance(n, ast.Import)
                   for a in n.names if a.name == module}
        for node in ast.walk(parsed):
            if not isinstance(node, ast.Call):
                continue
            matched = isinstance(node.func, ast.Name) and node.func.id in aliases
            matched |= isinstance(node.func, ast.Attribute) and node.func.attr == name and (
                isinstance(node.func.value, ast.Name) and node.func.value.id in modules)
            if not matched or any(k.arg is None for k in node.keywords):
                continue
            try:
                calls.append({'args': [_literal(a) for a in node.args],
                              'kwargs': {k.arg: _literal(k.value) for k in node.keywords}})
            except (ValueError, TypeError, SyntaxError):
                continue
            if len(calls) >= 8:
                return calls
    return calls


def _boundaries(node, literals):
    positional = [*node.args.posonlyargs, *node.args.args]
    params = [*positional, *node.args.kwonlyargs]
    if node.args.vararg or node.args.kwarg or len(params) > 8:
        return []
    values = [_values(p.annotation) for p in params]
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    defaults += list(node.args.kw_defaults)
    for i, (param, default) in enumerate(zip(params, defaults)):
        if not values[i] and default is not None:
            try:
                values[i] = [_literal(default)]
            except (ValueError, TypeError):
                pass
        if not values[i] and literals:
            call = literals[0]
            if i < len(call['args']):
                values[i] = [call['args'][i]]
            elif param.arg in call['kwargs']:
                values[i] = [call['kwargs'][param.arg]]
    if any(not v for v in values):
        return []
    seed = [v[0] for v in values]
    rows = [seed]
    for i, choices in enumerate(values):
        for choice in choices[1:]:
            rows.append([*seed[:i], choice, *seed[i + 1:]])
    return [{'args': row[:len(positional)], 'kwargs': {
        p.arg: value for p, value in zip(node.args.kwonlyargs, row[len(positional):])}} for row in rows]


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    args: list[JsonValue] = Field(default_factory=list, max_length=8)
    kwargs: dict[str, JsonValue] = Field(default_factory=dict, max_length=8)


def _model_inputs(targets, deadline, llm, activities):
    if not targets or deadline - time.monotonic() < 2 * vs.MIN_RUN_S:
        activities.append(_activity('time limit: no model input proposal'))
        return []
    # The schema closes target selection over exactly the statically discovered functions.
    suggestion = create_model('DiscoveryInput', __base__=_Input, target=(Literal[tuple(targets)], ...))
    reply = create_model('DiscoveryInputs', __config__=ConfigDict(extra='forbid', strict=True),
                         value=(list[suggestion], Field(max_length=32)))
    context = json.dumps({
        'task': 'Propose JSON input values only for edge cases of these functions. Never supply code.',
        'hypotheses': ['boundary', 'kept behaviour', 'misread intent'], 'targets': targets}, ensure_ascii=False)
    if len(context) > semantic.MAX_TEXT:
        activities.append(_activity('model context limit; deterministic inputs retained'))
        return []
    question = semantic.Question('reformulation', context,
        reply, lambda: [], 'choose')  # target's Literal enum is the closed candidate set; nested values are data
    with semantic.use_request(**({'llm': llm} if llm is not None else {})) as scope:
        before = scope.calls
        original_timeout = scope.timeout
        try:
            scope.timeout = min(original_timeout, max(0.001, deadline - time.monotonic()))
            reading = semantic.read(question)
        finally:
            scope.timeout = original_timeout
        activities.append(_activity(reading.reason, result='proposed' if reading.by == 'model' else 'skipped',
                                    model_calls=scope.calls - before, proposed_by='default model; input data only'))
    return [suggestion.model_validate(v).model_dump() for v in reading.value]


def discover(change, requirements, *, room, deadline, roots=(), llm=None, skip='', reserved_ids=(), confirmed=(),
             model_budget=1):
    """Compile at most ``room`` probes without importing repository code or executing model text."""
    found = Discovery()
    until = min(deadline, time.monotonic() + ACTIVITY_S)
    if not change.sources:
        found.activities.append(_activity('no changed public Python function: no probe'))
        return found
    why = skip or (f'shared {vs.MAX_CHECKS}-check limit' if room <= 0 else '')
    if not why and time.monotonic() >= until:
        why = 'review time limit'
    why = why or boundary(change.repo)
    if why:
        found.activities.append(_activity(why))
        return found
    tree = vt.Tree.read(change.repo, change, deadline=until, roots=roots)
    targets = {}
    for rel in sorted(change.sources):
        if time.monotonic() >= until:
            found.activities.append(_activity('time limit before source scan', target=rel))
            continue
        try:
            old = _functions(tree.text(rel, vt.BASE)) if tree.has(rel, vt.BASE) else {}
            new = _functions(tree.text(rel, vt.PATCH)) if tree.has(rel, vt.PATCH) else {}
            module = _module(rel, tree)
            old_methods = _methods(tree.text(rel, vt.BASE)) if tree.has(rel, vt.BASE) else {}
            new_methods = _methods(tree.text(rel, vt.PATCH)) if tree.has(rel, vt.PATCH) else {}
            for name in sorted(old_methods.keys() | new_methods.keys()):
                if name not in old_methods or name not in new_methods or ast.dump(old_methods[name]) != ast.dump(new_methods[name]):
                    found.activities.append(_activity('method receiver construction is unsupported', target=module + '.' + name))
            for name in sorted(old.keys() | new.keys()):
                target = module + '.' + name
                if name in old and name in new and ast.dump(old[name]) == ast.dump(new[name]):
                    continue
                if not module or name not in old or name not in new or any(
                        isinstance(n, ast.AsyncFunctionDef) for n in (old[name], new[name])):
                    found.activities.append(_activity('no comparable synchronous public function', target=target))
                    continue
                if len(targets) >= min(room, MAX_FUNCTIONS):
                    found.activities.append(_activity('shared 20-check limit', target=target))
                    continue
                literals = _literal_calls(tree, target, until, found.activities)
                calls = _boundaries(new[name], literals) + literals
                targets[target] = {'calls': calls, 'signature': ast.unparse(new[name].args)[:500],
                                   'source': ast.unparse(new[name])[:1500]}
        except (SyntaxError, ValueError, vt.Inapplicable) as exc:
            found.activities.append(_activity(f'unsupported source: {type(exc).__name__}', target=rel))
    # Keep time for validating and compiling the deterministic floor even when the model times out.
    model_inputs = []
    if model_budget > 0:
        model_inputs = _model_inputs({t: {'signature': v['signature'], 'source': v['source']}
                                      for t, v in targets.items()}, until - 2.0, llm, found.activities)
    else:
        found.activities.append(_activity('shared model call budget exhausted; deterministic inputs retained'))
    for target, details in targets.items():
        own = [v for v in model_inputs if v['target'] == target]
        calls, labels, seen = [], [], set()
        for call, label in itertools.chain(((v, 'boundary or existing test literal') for v in details['calls']),
                                           (({'args': v['args'], 'kwargs': v['kwargs']}, 'model-proposed input') for v in own)):
            try:
                key = json.dumps(call, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                found.activities.append(_activity('refused input: not bounded JSON data', target=target, input_origin=label))
                continue
            if key not in seen and len(key) <= vt.MAX_CALL_CHARS and len(calls) < 32:
                try:
                    binding = vt.DifferentialBindings(function=target, mode='outputs', calls=[call])
                    vt._differential(tree, binding, vt.BASE)
                    vt._differential(tree, binding, vt.PATCH)
                except (ValueError, vt.Inapplicable) as exc:
                    found.activities.append(_activity(f'refused input: {exc}', target=target, input_origin=label))
                    continue
                seen.add(key)
                calls.append(call)
                labels.append(label)
        if not calls or time.monotonic() >= until:
            found.activities.append(_activity('no supported inputs or activity time limit', target=target))
            continue
        name = target.rsplit('.', 1)[1]
        names = [target]
        if sum(t.rsplit('.', 1)[1] == name for t in targets) == 1:
            names.append(name)
        preservation = next((r for r in requirements if r.kind == 'preserve' and any(
            re.search(r'(?<![\w.])' + re.escape(n) + r'(?![\w.])', r.text) for n in names)), None)
        confirmed_keep = next((q for q in confirmed if q.intent == 'preserve' and q.function == target), None)
        req = preservation or next(iter(requirements), None)
        ident = 'discover-' + hashlib.sha256(target.encode()).hexdigest()[:12]
        if ident in reserved_ids:
            found.activities.append(_activity('check id collision with an existing check', target=target))
            continue
        keeping = preservation is not None or confirmed_keep is not None
        hypothesis = {'id': f'{ident}.boundary' if keeping else f'{ident}.misread-intent',
                      'text': f'{target}: boundary inputs may change ' + (
                          'behaviour the task says to keep' if keeping else 'behaviour whose intent needs confirmation')}
        hypotheses = [{'id': f'{ident}.{kind.replace(" ", "-")}', 'kind': kind, 'target': target,
                       'text': text} for kind, text in (
                           ('boundary', 'Typed zero, negative, empty and optional inputs may expose an untested branch.'),
                           ('kept behaviour', 'Existing literal test calls may expose behaviour the task asks to keep.'),
                           ('misread intent', 'A changed output may be intended; compare both outputs before deciding.'))]
        try:
            instance = vt.TemplateInstance(id=ident, template='differential.v1', requirement_id=(
                preservation.id if preservation else confirmed_keep.requirement_id if confirmed_keep else req.id if req else ident),
                intent='preserve' if keeping else 'change', provenance='model_hypothesis', bindings={
                    'function': target, 'mode': 'outputs', 'calls': calls})
            bindings = vt.DifferentialBindings.model_validate(instance.bindings)
            data = vt._differential(tree, bindings, vt.BASE).data
            # Validate each call on the patch as well: an incompatible signature is a skipped activity.
            vt._differential(tree, bindings, vt.PATCH)
            check, placed, origin = vt.compiled_check(change.repo, instance, data, [])
        except (ValueError, vt.Inapplicable) as exc:
            found.activities.append(_activity(f'unsupported probe: {exc}', target=target))
            continue
        origin = replace(origin, activity='automatic discovery probe', proposed_by='G6-generated input probe',
                         unconfirmed='G6 generated it; it decides no requirement',
                         label='G6-generated discovery',
                         caution=('G6 generated this check from fixed differential.v1 text and labelled input values. '
                                  'It runs repository code only inside the operating-system boundary, with no network. '
                                  'Read it before you run it, or run it in a container.'),
                         about={**origin.about, 'hypothesis': hypothesis, 'hypotheses': hypotheses,
                                'input_origins': labels, 'target': target})
        found.checks.append(check)
        found.placed[check.id] = placed
        found.origins[check.id] = origin
    if not targets:
        found.activities.append(_activity('no comparable changed public Python functions: no probe'))
    return found
