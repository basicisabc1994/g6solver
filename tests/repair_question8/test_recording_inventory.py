"""Recording an exception runs no hook of its class's metaclass, of its instance, or of the module that names it.

Every hook is armed by the called function just before it raises, and prints a marker when it runs after that
function's frame is gone: what the class's own creation, the function and its own ``raise`` statement run (Python's
``raise`` asks the metaclass's subclass check itself) is the call, not G6's recording. The controls plant the earlier
reads back into the bootstrap, which shows the markers are seen when G6 does run a hook.
"""
import ast
import inspect
import subprocess
import sys

import pytest

from g6.core import confine
from g6.harness import behaviour_runner as runner
from g6.harness.observation_contract import observable_exception
from tests.repair_question8.test_setup_inventory import observe

#: The metaclass hooks a lookup, a display, a comparison or an instance or subclass check can reach.
HOOKS = ['__getattribute__', '__getattr__', '__repr__', '__str__', '__format__', '__instancecheck__',
         '__subclasscheck__', '__subclasses__', 'mro', '__eq__', '__ne__', '__hash__', '__bool__', '__len__',
         '__iter__', '__contains__', '__dir__', '__setattr__', '__delattr__', '__or__', '__ror__', '__call__']
#: The names a metaclass can answer with a property of its own instead of the class's namespace.
PROPERTIES = ['__module__', '__name__', '__mro__', '__bases__', '__dict__', '__doc__', '__class__',
              '__text_signature__', '__annotations__', '__base__', '__flags__', '__basicsize__']

META = '''import sys
ARMED = []
def hit(name):
    if not ARMED:
        return
    frame = sys._getframe(1)
    while frame is not None and frame.f_code is not add.__code__:
        frame = frame.f_back
    if frame is None:   # the called function's frame is gone: G6 is recording
        print("G6_HOOK:" + name, flush=True)
def hook(name):
    def run(cls, *args, **kwargs):
        hit(name)
        return getattr(type, name)(cls, *args, **kwargs)
    return run
def answer(name):
    def get(cls):
        hit("property:" + name)
        return "forged"
    return property(get)
class Meta(type):
    locals().update({name: hook(name) for name in %r})
    locals().update({name: answer(name) for name in %r})
class DomainError(ValueError, metaclass=Meta):
    def __getattribute__(self, name):
        hit("instance:" + name)
        return ValueError.__getattribute__(self, name)
    __class__ = property(lambda self: hit("instance:__class__") or KeyError)
    def __str__(self):
        hit("instance:__str__")
        return "text"
    def __repr__(self):
        hit("instance:__repr__")
        return "text"
def add():
    made = ValueError.__new__(DomainError)
    ARMED.append(True)
    raise made
''' % (HOOKS, PROPERTIES)

SPECS = {'builtin': ['builtin', '', 'ValueError'], 'module': ['module', 'calc', 'DomainError', 'calc.py'],
         'another': ['builtin', '', 'KeyError']}


def test_the_metaclass_under_test_overrides_every_named_hook_and_property():
    namespace = {}
    exec(META, namespace)
    meta = namespace['Meta']
    assert all(name in vars(meta) for name in HOOKS + PROPERTIES)
    # __qualname__ cannot be made a metaclass property (the class statement takes it as the class's own name); a
    # metaclass answers it through __getattribute__, which HOOKS holds.
    with pytest.raises(TypeError):
        exec('class Other(type):\n    __qualname__ = property(lambda cls: "forged")\n', {})


@pytest.mark.parametrize('expected', ['builtin', 'module', 'another', 'returns'])
def test_no_hook_runs_while_g6_records_the_exception(tmp_path, expected):
    case = ({'args': [], 'expect': 'returns', 'value': 5} if expected == 'returns' else
            {'args': [], 'expect': 'raises', 'error': SPECS[expected]})
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [case]}
    got, output = observe(tmp_path, {'calc.py': META}, data)
    assert 'G6_HOOK' not in output, ('a hook ran while G6 recorded the exception', output)
    assert got['result'] == ('passed' if expected in ('builtin', 'module') else 'failed'), got
    [one] = got['observed']['outcomes']
    assert one['raised_name'] == 'calc.DomainError', 'the names are the class\'s own, never a metaclass\'s answer'
    assert set(one['exception_detail'].values()) == {'<unavailable>'}


def test_no_hook_runs_while_g6_records_a_probe(tmp_path):
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'calls': [{'args': [], 'kwargs': {}}],
            'errors': [SPECS['module']], 'mode': 'exceptions'}
    got, output = observe(tmp_path, {'calc.py': META}, data)
    assert 'G6_HOOK' not in output, output
    assert got['result'] == 'passed' and got['observations'][0]['output'] == {'raised': 'calc.DomainError'}, got


#: An earlier read, planted back into the bootstrap in place of the present one, and the hook it runs.
EARLIER = {
    'attribute lookup': ('value = own[field].__get__(kind)', 'value = getattr(kind, field)',
                         'G6_HOOK:__getattribute__'),
    'display': ('value = own[field].__get__(kind)', 'value = str(kind)', 'G6_HOOK:__str__'),
    'instance check': ("return (any(base is type for base in ancestry(type(kind)))",
                       "return (isinstance(kind, type) and issubclass(kind, kind)", 'G6_HOOK:__subclasscheck__'),
    'instance display': ("one = {'raised': [id(kind)],", "str(exc)\n    one = {'raised': [id(kind)],",
                         'G6_HOOK:instance:__str__'),
}


@pytest.mark.parametrize('read', EARLIER)
def test_control_a_hook_that_g6_does_run_is_seen(tmp_path, read):
    present, earlier, marker = EARLIER[read]
    assert runner.BOOTSTRAP.count(present) == 1
    data = {'function': ['module', 'calc', 'add', 'calc.py'],
            'cases': [{'args': [], 'expect': 'raises', 'error': SPECS['module']}]}
    root, channel, boot = tmp_path / 'copy', tmp_path / 'observation.bin', tmp_path / 'bootstrap.py'
    root.mkdir()
    (root / 'calc.py').write_text(META, encoding='utf-8')
    channel.write_bytes(b'')
    outputs = {}
    for label, program in (('present', runner.BOOTSTRAP), ('earlier', runner.BOOTSTRAP.replace(present, earlier))):
        boot.write_text(confine.CHILD_PRELUDE + program, encoding='utf-8')
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(root), str(channel),
                                runner._encoded(runner.request(data))], capture_output=True, text=True, timeout=60)
        outputs[label] = child.stdout + child.stderr
        channel.write_bytes(b'')
    assert 'G6_HOOK' not in outputs['present'], outputs['present']
    assert marker in outputs['earlier'], (read, outputs['earlier'])


class _Trap(type):
    def __instancecheck__(cls, instance):
        raise AssertionError('instance check')

    def __subclasscheck__(cls, sub):
        raise AssertionError('subclass check')

    def __getattribute__(cls, name):
        raise AssertionError('attribute lookup: ' + name)


def test_the_shared_predicate_judges_a_resolved_object_without_its_hooks():
    class Raised(Exception, metaclass=_Trap):
        pass

    class Plain(metaclass=_Trap):
        pass

    class Lying:
        __class__ = property(lambda self: (_ for _ in ()).throw(AssertionError('__class__')))

        def __getattribute__(self, name):
            raise AssertionError('attribute lookup: ' + name)

    assert observable_exception(Raised, resolved=True) is True
    assert observable_exception(Plain, resolved=True) is False
    for other in (Lying(), None, 5, 'ValueError', BaseException.__new__(Raised), type, object):
        assert observable_exception(other, resolved=True) is False
    for kind in (BaseException, Exception, KeyError, SystemExit, GeneratorExit, KeyboardInterrupt):
        assert observable_exception(kind, resolved=True) is True
    assert observable_exception('calc.DomainError') is True and observable_exception('not a name') is False


#: Lookups that ask an object's class or metaclass: none may be made on what the repository defines.
ASKING = {'getattr', 'hasattr', 'isinstance', 'issubclass', 'repr', 'format', 'vars', 'dir', 'hash', 'callable',
          'print', 'bool', 'iter', 'next', 'sorted', 'setattr', 'delattr'}


def _asks(tree):
    """(calls that ask an object, attribute reads by owner) in a function's code."""
    calls = sorted(ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id in ASKING | {'str'})
    owners = sorted({ast.unparse(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)})
    return calls, owners


def test_inventory_the_recorder_reads_only_through_the_built_in_types_own_descriptors():
    calls, owners = _asks(ast.parse(inspect.getsource(runner._exception_observation)))
    assert calls == [], calls
    # every attribute read is of the built-in type's namespace, a descriptor from it, G6's own values or a module
    assert owners == ['dict', 'errors', 'hashlib', "hashlib.sha256(text.encode('utf-8'))", 'json',
                      "one['raised']", "own['__mro__']", 'own[field]', 'str', 'text', 'type'], owners
    resolved = ast.parse(inspect.getsource(observable_exception)).body[0].body[-2:]
    calls, owners = _asks(ast.Module(body=resolved, type_ignores=[]))
    assert calls == [] and owners == ['type', "type.__dict__['__mro__']"], (calls, owners)


def test_inventory_the_bootstrap_finds_an_outcome_type_without_asking_what_the_repository_defines():
    functions = {node.name: node for node in ast.walk(ast.parse(runner.BOOTSTRAP)) if isinstance(node, ast.FunctionDef)}
    for name, expected in {'held': ['dict'], 'resolved': ['alive'],
                           'named': ['os', 'os.path', 'spec[3]', 'sys']}.items():
        calls, owners = _asks(functions[name])
        assert calls == [] and owners == expected, (name, calls, owners)
    # the call's handler passes the caught exception to the one recorder and reads nothing of it itself
    [handler] = [node for node in ast.walk(ast.parse(runner.BOOTSTRAP)) if isinstance(node, ast.ExceptHandler)
                 and node.name == 'exc' and any(isinstance(inner, ast.Call) and ast.unparse(inner.func) ==
                                                'outcomes.append' for inner in ast.walk(node))]
    assert [ast.unparse(line) for line in handler.body] == [
        'alive.append(exc)', 'errors = resolved()',
        "outcomes.append(_exception_observation(exc, errors, data['probe']))"]
