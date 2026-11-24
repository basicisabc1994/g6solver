import json
import random
import string

import pytest

from g6.harness import behaviour_runner as runner, verification_search as search, verification_templates as vt
from tests.harness.test_template_runtime_binding import Tree


def bindings_for(template):
    bindings = {'constructor': {'call': 'runtime_fixture.make'}, 'function': 'runtime_fixture.Machine.act'}
    if template == 'example.v1':
        bindings['cases'] = [{'args': [''], 'returns': 0}]
        slot = bindings['cases'][0]['args']
    elif template == 'relation.v1':
        bindings.update(relation='idempotence', inputs=[''])
        slot = bindings['inputs']
    elif template == 'access_rule.v1':
        bindings.pop('function')
        bindings.update(operation='runtime_fixture.Machine.act', refusal='falsy_return', cases=[
            {'who': 'owner', 'resource': 'record', 'args': [''], 'expected': 'allowed'},
            {'who': 'guest', 'resource': 'record', 'args': [0], 'expected': 'refused'}])
        slot = bindings['cases'][0]['args']
    elif template == 'differential.v1':
        bindings.update(mode='outputs', calls=[{'args': ['']}])
        slot = bindings['calls'][0]['args']
    else:
        bindings.pop('function')
        bindings.update(
            model={'variables': {'n': 'Int', 'act': 'Int'}, 'init': '(and (= n 0) (= act 0))',
                   'transition': '(and (= next_act 1) (= next_n (- 1)))',
                   'invariants': {'nonnegative': '(>= n 0)'}}, action='act',
            actions={'1': {'call': 'runtime_fixture.Machine.act', 'args': ['']}},
            observe={'n': {'call': 'runtime_fixture.Machine.read'}})
        slot = bindings['actions']['1']['args']
    return bindings, slot


@pytest.mark.parametrize("template", tuple(vt.REGISTRY))
@pytest.mark.parametrize("alphabet", [string.ascii_letters + string.digits + string.punctuation, "\u2603\U0001f600"])
def test_maximal_bindings_fit_every_template(template, alphabet):
    definition = vt.REGISTRY[template]
    if definition.applies is None:
        assert not definition.text
        return
    bindings, slot = bindings_for(template)
    rng = random.Random(31)
    text = ''.join(rng.choice(alphabet) for _ in range(vt.MAX_BINDINGS_CHARS))
    if template == 'differential.v1':
        # Each differential call is limited on its own, so the maximal bindings are many calls, each within that limit.
        calls, used = bindings['calls'], 0
        calls.clear()
        while len(calls) < vt.MAX_INPUTS and len(json.dumps(bindings)) < vt.MAX_BINDINGS_CHARS - 12:
            call = {'args': ['']}
            calls.append(call)
            low, high = 0, len(text) - used
            while low < high:
                middle = (low + high + 1) // 2
                call['args'][0] = text[used:used + middle]
                if len(json.dumps(call)) <= 280 and len(json.dumps(bindings)) <= vt.MAX_BINDINGS_CHARS:
                    low = middle
                else:
                    high = middle - 1
            call['args'][0] = text[used:used + low]
            used += low
            if low == 0:
                calls.pop()
                break
        slot = None
    low, high = 0, len(text) if slot is not None else 0
    while low < high:
        middle = (low + high + 1) // 2
        slot[0] = text[:middle]
        if len(json.dumps(bindings)) <= vt.MAX_BINDINGS_CHARS:
            low = middle
        else:
            high = middle - 1
    if slot is not None:
        slot[0] = text[:low]
    if slot is None:   # differential: the most calls the model admits, each as long as its own limit lets it be
        assert len(bindings['calls']) == vt.MAX_INPUTS and len(json.dumps(bindings)) <= vt.MAX_BINDINGS_CHARS
    else:
        assert vt.MAX_BINDINGS_CHARS - 12 <= len(json.dumps(bindings)) <= vt.MAX_BINDINGS_CHARS
    [item] = vt.template_instances([{'id': 'maximal', 'template': template, 'requirement_id': 'R1',
                                    'intent': 'preserve', 'bindings': bindings}], {'R1'})
    data = definition.applies(Tree(), definition.bindings.model_validate(item.bindings), vt.BASE).data
    if template == 'state_invariant.v1':
        data['traces'] = [{'actions': [1], 'ghost': {}}]
    source = vt.compile_text(definition, data)
    assert len(source.encode("utf-8")) <= search.MAX_SOURCE_BYTES
    compile(source, "generated.py", "exec")
    expanded = runner.expanded(source)
    assert "BINDINGS = " in expanded
