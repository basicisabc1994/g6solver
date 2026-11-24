# SPDX-License-Identifier: Apache-2.0
"""G6's isolated observation runner. Repository test configuration is never loaded.

The result channel excludes repository test configuration. The bootstrap shares its interpreter with the code
under test: the channel is not an authentication boundary inside Python.
The observation.v2 frame carries canonical value digests and byte sizes with bounded diagnostic prefixes. Existing
observation.v1 frames keep their comparison semantics; neither format authenticates its producer inside the child.
A tree folder sharing an installed package's name may load as an empty namespace package. If no import fails,
the isolated runner has no missing-import evidence to distinguish its later attribute failure from the code's.
"""
from __future__ import annotations
from g6.core.guidance_builders import confine_refusal, confine_refused

import ast
import base64
import concurrent.futures
import dis
import hashlib
import inspect
import json
import os
import stat
import struct
import sys
import time
import zlib
from pathlib import Path, PurePosixPath

from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.workforce import repo_tests as rt
from g6.harness.observation_contract import observable_exception

RUN_TEMPLATES = frozenset({"example.v1", "differential.v1"})
CONFIRMED = "# G6-CONFIRMED-RUNNER\n"
LIMIT = ("Code under test runs in the same process as the bootstrap and can shape its own observed outcome, "
         "as with any test run.")
MAX_FRAME = 1 << 20
MAGIC = b"G6R1\n"
COMPACT = '# G6-COMPACT-REPRODUCTION\n'
_SETUP_CONTROLS = (component_process.ExecutionInterrupted, component_process.ContainmentFailure)


class ExecutableSource(str):
    """Compiler custody held in the host, outside the Python text and its JSON representation.

    Free-form admission keeps only the string. Adding arbitrary code also produces an ordinary string; only the
    historical confirmed label can be prepended without losing custody already held by this compiler object.
    """

    def __new__(cls, source: str, observation: dict):
        made = super().__new__(cls, source)
        made._observation = json.dumps(observation, sort_keys=True, ensure_ascii=True, allow_nan=False)
        return made

    @property
    def observation(self) -> dict:
        return json.loads(self._observation)

    def __radd__(self, prefix):
        text = prefix + str(self)
        return ExecutableSource(text, self.observation) if prefix == CONFIRMED else text


def expanded(source: str) -> str:
    """Decode a compact portable reproduction for compiler inspection; it supplies no execution authority."""
    import base64
    import zlib

    if source.startswith(CONFIRMED):
        source = source[len(CONFIRMED):]
    if source.startswith(COMPACT):
        first = ast.parse(source).body[0]
        if not (isinstance(first, ast.Assign) and len(first.targets) == 1
                and isinstance(first.targets[0], ast.Name) and first.targets[0].id == 'CODE_Z85'):
            raise ValueError('missing compact reproduction')
        try:
            source = zlib.decompress(base64.b85decode(ast.literal_eval(first.value))).decode('utf-8')
        except zlib.error as exc:
            raise ValueError('malformed compact reproduction') from exc
    return source


def _fingerprint(text: str) -> dict:
    raw = bytes(text, 'utf-8')
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'prefix': text[:200]}


def _exception_observation(exc, errors, probe):
    """Record the caught type first; unavailable diagnostics can never erase that observation.

    Instance display hooks are not needed for type matching and are deliberately not invoked. Names are diagnostic.
    The type's ancestry, module and qualified name are read through the built-in type's own descriptors, which read
    the class's own slots and namespace: a metaclass's attribute lookup, name properties, display hooks and instance
    or subclass checks are never asked.
    """
    kind = type(exc)
    own = type.__dict__
    unavailable = '<unavailable>'
    one = {'raised': [id(kind)], 'raised_name': unavailable + '.' + unavailable,
           'exception_detail': dict.fromkeys(('str', 'repr', 'arguments', 'metaclass'), unavailable)}
    if probe:
        one['probe'] = {'raised': one['raised_name']}
    try:
        relevant = set(errors.values())
        ancestry = own['__mro__'].__get__(kind)
        one['raised'].extend(id(parent) for parent in ancestry if parent is not kind and id(parent) in relevant)
    except BaseException:
        pass

    try:
        one['raised_name'] = exception_name(kind, qualified=True)
        if probe:
            shown = exception_name(kind)
            text = json.dumps(shown, sort_keys=True)
            one['probe'] = {'raised': shown if len(text) <= 500 else {
                'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'start': text[:200]}}
    except BaseException:
        pass
    return one


class Activity(dict):
    """Process-local probe custody; JSON contains exactly the activity's existing fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owned_runs: list[bool] = []
        self.execution_artifacts: list[dict] = []


def write_probes(path: Path, reply: dict, data: bytes) -> None:
    """Publish observation custody separately, after the exact reply bytes have been saved."""
    activities = (reply.get("assessment", {}).get("completion", {}).get("activities", []))
    probes = {one["check"]: digest(one) for one in activities if isinstance(one, Activity)
              and one.owned_runs and all(one.owned_runs) and one.get("template") == "differential.v1"}
    if not probes:
        return
    executions = {one['check']: one.execution_artifacts for one in activities
                  if isinstance(one, Activity) and one.get('check') in probes}
    record = {"version": "review-observations.v1", "reply_sha256": hashlib.sha256(data).hexdigest(),
              "probes": probes, 'executions': executions}
    side = path.with_suffix(".observations.json")
    part = side.with_name(side.name + ".part")
    with part.open("xb") as handle:
        handle.write(json.dumps(record, sort_keys=True).encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, side)


def read_probes(path: Path, data: bytes) -> dict[str, str]:
    """Missing, damaged, foreign or incompletely published custody never admits observations."""
    try:
        side = path.with_suffix(".observations.json")
        if not stat.S_ISREG(os.lstat(side).st_mode):
            return {}
        value = json.loads(side.read_bytes(), object_pairs_hook=_unique)
        if (set(value) != {"version", "reply_sha256", "probes", 'executions'} or value["version"] != "review-observations.v1"
                or value["reply_sha256"] != hashlib.sha256(data).hexdigest() or not isinstance(value["probes"], dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in value["probes"].items())
                or not isinstance(value['executions'], dict) or set(value['executions']) != set(value['probes'])
                or not all(isinstance(runs, list) for runs in value['executions'].values())):
            return {}
        return value["probes"]
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, MemoryError):
        return {}


def probe_sha256() -> str:
    return hashlib.sha256(BOOTSTRAP.encode("utf-8")).hexdigest()


class CheckSetupError(TypeError):
    """A generated check could not prepare a call; the operation has not run."""

    REASONS = ('import_or_binding', 'constructor', 'constructor_type', 'method_lookup', 'owner', 'binding',
               'exception_type', 'exception_description', 'scenario_binding', 'setup')
    # G6's own fixed wording for a reason; never text from the repository under check.
    DESCRIPTIONS = {'constructor_type': 'constructor did not return the named class'}
    CALL_FAILURES = ('call failed: builtins.AttributeError', 'call failed: builtins.ImportError',
                     'call failed: builtins.ModuleNotFoundError')

    @classmethod
    def details(cls):
        """Every exact detail G6's check writes: a reason alone, or with its fixed G6 description."""
        return (tuple('G6_E_CHECK_SETUP: ' + reason for reason in cls.REASONS)
                + tuple('G6_E_CHECK_SETUP: ' + reason + ': ' + text for reason, text in cls.DESCRIPTIONS.items()))

    def __init__(self, reason):
        # A direct instance of G6's own class retains its diagnostic, including nested refusals.
        if type(reason) is str and reason.startswith('G6_E_'):
            super().__init__(reason[:300])
            return
        phase = reason.partition(":")[0] if type(reason) is str else "exception_description"
        if phase == 'module_import':
            phase = 'import_or_binding'
        phase = phase if phase in CheckSetupError.REASONS else 'setup'
        described = CheckSetupError.DESCRIPTIONS.get(phase)
        super().__init__("G6_E_CHECK_SETUP: " + phase + (": " + described if described else ""))


class ObservationUnavailable(CheckSetupError):
    """Import preparation yielded no observation of the repository callable."""

    DETAIL = 'G6_E_OBSERVATION_UNAVAILABLE: import or lazy lookup did not complete'
    NO_OBSERVATION = 'G6_E_OBSERVATION_UNAVAILABLE: no single completed observation'

    @classmethod
    def valid_detail(cls, detail):
        if type(detail) is not str:
            return False
        if detail in (cls.DETAIL, cls.NO_OBSERVATION):
            return True
        prefix = cls.DETAIL + ': '
        name = detail[len(prefix):]
        return (detail.startswith(prefix) and 0 < len(name) <= 200
                and all((part.isidentifier() or part in ('<locals>', '<unavailable>'))
                        and 'G6_E_' not in part for part in name.split('.')))

    def __init__(self, kind=None, *, detail=None):
        if detail is None:
            detail = self.DETAIL + (': ' + exception_name(kind, qualified=True) if kind is not None else '')
        TypeError.__init__(self, detail if self.valid_detail(detail) else self.DETAIL)


CODE_CARRIERS = (
    ('g6._context_engine._registry', 'Refused'), ('g6._context_engine._scope', 'ScopeDenied'),
    ('g6.abstraction.server', 'FileRefused'), ('g6.action_logic.block', '_Refused'),
    ('g6.agents._runtime.model', 'AgentRefusal'), ('g6.algebra.envelope', 'Refusal'),
    ('g6.causal.envelope', 'Refusal'), ('g6.code._types', 'CodeRefused'),
    ('g6.cogarch.cog_arch_actr.memory', 'Refused'), ('g6.cogarch.cog_arch_aixi.block', '_Refusal'),
    ('g6.cogarch.cog_arch_gps.block', '_Refusal'), ('g6.cognitive_bridge.codes', 'NotPlainData'),
    ('g6.core.http', 'HTTPError'), ('g6.core.keys', 'KeyMissing'), ('g6.core.outside', 'ServiceError'),
    ('g6.csf.schema', 'CSFError'), ('g6.csf.schema', 'CSFInputError'),
    ('g6.datalog.envelope', 'Refusal'), ('g6.formal.envelope', 'Refusal'),
    ('g6.graph_reasoning.envelope', 'Refusal'), ('g6.harness.behaviour_question', 'CandidateRefused'),
    ('g6.harness.workflow._store', 'RecordOff'), ('g6.harness.workflow._store', 'RecordUnreadable'),
    ('g6.integrations.database.backends', 'BackendError'), ('g6.knowledge._index', 'IndexRefused'),
    ('g6.knowledge.ctx_contexthub.hub', 'HubError'), ('g6.knowledge.ctx_elastic.elastic', 'ElasticError'),
    ('g6.knowledge.ctx_elastic.writer', 'Held'), ('g6.knowledge.ctx_search.web', 'SearchRefused'),
    ('g6.knowledge.knowledge_chunker.chunker', 'ChunkError'),
    ('g6.knowledge.knowledge_fetcher.fetcher', 'FetchRefused'),
    ('g6.knowledge.knowledge_manifest.manifest', 'StoreError'), ('g6.lean.envelope', 'Refusal'),
    ('g6.learning.strategy', 'StrategyError'), ('g6.media._types', 'MediaError'),
    ('g6.memory.ctx_ace.store', 'StoreError'), ('g6.memory.ctx_cognee.client', 'CogneeError'),
    ('g6.ml._types', 'MLError'), ('g6.model_checking.envelope', 'Refusal'),
    ('g6.optimisation.envelope', 'Refusal'), ('g6.prolog.envelope', 'Refusal'),
    ('g6.self_maintenance.codes', 'SelfMaintenanceRefused'),
    ('g6.sources.australian_legal_corpus.store', 'IndexProblem'), ('g6.sources.ctx_git.config', 'GitRefused'),
    ('g6.sources.ctx_langextract.extract', 'ExtractStopped'), ('g6.sources.ctx_vision.block', '_Stop'),
    ('g6.sources.ctx_vision.images', 'ImageRefused'), ('g6.sources.ctx_vision.ocr', 'OcrFailed'),
    ('g6.workforce.repo_tests', 'RepoRefused'), ('g6.workspace._envelope', 'WorkspaceRefused'),
)


def code_carriers():
    """Resolve fixed G6 exports already imported by the host; exception metadata grants no identity."""
    return tuple(kind for module, name in CODE_CARRIERS
                 if (loaded := sys.modules.get(module)) is not None
                 and type(kind := vars(loaded).get(name)) is type)


def typed_code(exc):
    """Read raw code storage only after matching an imported G6 class object in the ancestry."""
    own = type.__dict__
    ancestry = own['__mro__'].__get__(type(exc))
    carriers = globals().get('_SETUP_CODE_CARRIERS')
    if carriers is None:
        carriers = code_carriers()
    if not any(base is carrier for base in ancestry for carrier in carriers):
        return None
    storage = BaseException.__dict__['__dict__'].__get__(exc)
    namespaces = [own['__dict__'].__get__(kind) for kind in ancestry]
    for items in (dict.items(storage), *(namespace.items() for namespace in namespaces)):
        for key, value in items:
            if (type(key) is str and key == 'code' and type(value) is str and value.startswith('G6_E_')
                    and len(value) <= 80 and all(ch in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for ch in value)):
                return value
    return None


def register_setup_error(template=None):
    """Publish the actual generated class before repository code loads; names confer no custody."""
    global _SETUP_CODE_CARRIERS, _SETUP_CONTROLS
    if template is not None:
        _SETUP_CODE_CARRIERS = code_carriers()
        controls = sys.modules.get('g6.core.component_process')
        _SETUP_CONTROLS = (() if controls is None else tuple(vars(controls)[name] for name in (
            'ExecutionInterrupted', 'ContainmentFailure')))
    if template is not None and '__name__' not in setup_call.__globals__:
        # exec without a module still writes the same qualified diagnostic as a pytest-loaded check.
        module = 'test_g6_' + template.partition('.')[0]
        CheckSetupError.__module__ = module
        CheckSetupError.__name__ = module + '.CheckSetupError'
    registry = vars(sys).get('_g6_check_setup_types')
    if type(registry) is list:
        registry.append((CheckSetupError, 'G6_E_CHECK_SETUP'))
        unavailable = globals().get('ObservationUnavailable')
        if unavailable is not None:
            registry.append((unavailable, 'G6_E_OBSERVATION_UNAVAILABLE'))


def raised_during_setup(exc, phase=None):
    """Identify preparation by G6's actual frame, without reading repository exception attributes.

    Method wrappers prepare immediately before invocation. Their operation handlers surround both phases, so
    let a preparation failure through there. Follow only this generated program's frames, stopping before the
    repository operation. That also excludes older tracebacks retained on a reused exception. Neither repository
    functions named setup_call nor an operation's own calls into G6 can claim the outer preparation boundary.
    """
    trace = BaseException.__dict__['__traceback__'].__get__(exc)
    while trace is not None and trace.tb_frame.f_globals is setup_call.__globals__:
        if (trace.tb_frame.f_code is setup_call.__code__
                and (phase is None or trace.tb_frame.f_locals.get('phase') == phase)):
            return True
        trace = trace.tb_next
    return False


def execution_control(exc):
    """Only keyboard interruption and controls captured before repository imports bypass recording."""
    return any(exception_matches(exc, kind) for kind in (KeyboardInterrupt, *globals().get('_SETUP_CONTROLS', ())))


def setup_outcome(exc):
    """One outcome boundary for every preparation phase, after setup_call preserves signal identity."""
    if execution_control(exc):
        if raised_during_setup(exc) or not exception_matches(exc, KeyboardInterrupt):
            raise exc
        return
    if exception_matches(exc, CheckSetupError):
        return
    if call_absent(exc):
        return
    trace, phase = BaseException.__dict__['__traceback__'].__get__(exc), None
    while trace is not None and trace.tb_frame.f_globals is setup_call.__globals__:
        if trace.tb_frame.f_code is setup_call.__code__:
            phase = trace.tb_frame.f_locals.get('phase')
        trace = trace.tb_next
    if phase is not None:
        return CheckSetupError(phase)


def exception_name(kind, qualified=False):
    """Bounded type metadata; missing or non-name fields retain a fixed placeholder."""
    own = type.__dict__

    def field(key):
        try:
            value = own[key].__get__(kind)
            if type(value) is not str and any(base is str for base in own['__mro__'].__get__(type(value))):
                # Copy the base string without invoking subclass display, length or arithmetic hooks.
                value = str.__str__(value)
            if type(value) is str and all((part.isidentifier() or part == '<locals>') and 'G6_E_' not in part
                                          for part in value.split('.')):
                return value
        except Exception:
            pass
        return '<unavailable>'

    module, name = field('__module__'), field('__qualname__')
    full = name if module == 'builtins' and not qualified else module + '.' + name
    return full if len(full) <= 200 else '<unavailable>'


def exception_matches(exc, kind):
    """Exception ancestry matching without an instance's __class__ override."""
    return any(base is kind for base in type.__dict__['__mro__'].__get__(type(exc)))


def exception_message(exc, fallback):
    """A stored, single exact string needs no display or attribute hook."""
    args = BaseException.__dict__['args'].__get__(exc)
    return args[0] if len(args) == 1 and type(args[0]) is str else fallback


def exception_detail(exc, fallback):
    """Only actual G6 classes carry codes; repository messages never enter G6 detail."""
    if type(exc) is ObservationUnavailable:
        return exception_message(exc, ObservationUnavailable.DETAIL)
    if type(exc) is CheckSetupError:
        return exception_message(exc, 'G6_E_CHECK_SETUP: setup')
    code = typed_code(exc)
    if code is not None:
        return code + ': ' + fallback[:200]
    return fallback[:16] + ': ' + exception_name(type(exc), qualified=True)


def own_import_absent(exc, args):
    """Missing modules/names belong only to held modules and unambiguous regular packages."""
    if not exception_matches(exc, ImportError):
        return False
    name = ImportError.__dict__['name'].__get__(exc)
    path = ImportError.__dict__['path'].__get__(exc)
    if type(name) is not str or not all(part.isidentifier() for part in name.split('.')):
        return False
    missing_module = exception_matches(exc, ModuleNotFoundError)
    if missing_module and args and type(args[0]) is str and name == args[0] and '.' not in name:
        return True
    tree = os.path.normcase(os.path.realpath(globals().get('root', os.getcwd())))
    parts = name.split('.')
    if missing_module and len(parts) == 1 and name in globals().get('data', {}).get('own_modules', ()):
        return True

    def guarded_export(file):
        # IMPORT_FROM supplies the requested name without parsing repository exception text.
        # An unrelated optional dependency cannot supply a name this module never defines.
        exported = None
        trace = BaseException.__dict__['__traceback__'].__get__(exc)
        while trace is not None:
            instruction = next((one for one in dis.get_instructions(trace.tb_frame.f_code)
                                if one.offset == trace.tb_lasti), None)
            if instruction is not None and instruction.opname == 'IMPORT_FROM':
                exported = instruction.argval
            trace = trace.tb_next
        if exported is None:
            return True
        try:
            with open(file, 'rb') as handle:
                source = handle.read(1048577)
            if len(source) > 1048576:
                return True
            pending = [ast.parse(source)]
            nodes = []
            while pending:
                node = pending.pop()
                nodes.append(node)
                # Bindings inside these scopes are not exports of the containing module. The definition's
                # own name is considered below, without visiting its body, parameters or decorators.
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
                                         ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    pending.extend(ast.iter_child_nodes(node))
        except (OSError, SyntaxError, ValueError):
            return True
        return any((isinstance(node, ast.Name) and node.id == exported and isinstance(node.ctx, ast.Store))
                   or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                       and node.name in (exported, '__getattr__'))
                   or (isinstance(node, ast.alias) and (node.asname or node.name.split('.')[0]) in (exported, '*'))
                   or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                       and node.func.id in ('exec', 'globals', 'locals', 'setattr')) for node in nodes)

    def regular(package):
        try:
            with open(os.path.join(package, '__init__.py'), 'rb') as handle:
                source = handle.read(1048577)
            # These packages may have portions G6's isolated interpreter cannot load.
            return len(source) <= 1048576 and not any(marker in source for marker in (
                b'__path__', b'extend_path', b'declare_namespace'))
        except OSError:
            return False

    for prefix in sys.path:
        if type(prefix) is not str:
            continue
        folder = os.path.normcase(os.path.realpath(prefix or os.getcwd()))
        if os.path.splitdrive(folder)[0] != os.path.splitdrive(tree)[0] or os.path.commonpath((tree, folder)) != tree:
            continue
        package = folder
        for part in parts[:-1]:
            package = os.path.join(package, part)
            if not regular(package):
                break
        else:
            target = os.path.join(package, parts[-1])
            if missing_module:
                if len(parts) > 1 and not os.path.exists(target) and not os.path.isfile(target + '.py'):
                    return True
                continue
            if type(path) is not str:
                continue
            held = (target + '.py', os.path.join(target, '__init__.py'))
            actual = os.path.normcase(os.path.realpath(path))
            if any(os.path.isfile(file) and os.path.normcase(os.path.realpath(file)) == actual
                   and (file == held[0] or regular(target)) for file in held):
                return not ((globals().get('_missing_preparation_imports') or globals().get('_missing_imports'))
                            and guarded_export(path))
    return False


def track_imports():
    """Install before repository loading; retain failed external imports even if a guard catches them."""
    import importlib._bootstrap

    global _missing_preparation_imports, _missing_imports
    _missing_preparation_imports, _missing_imports = set(), []
    load = importlib._bootstrap._find_and_load

    def tracked(name, import_):
        frame = sys._getframe(1)
        while frame is not None and (frame.f_code.co_filename.startswith('<frozen importlib.')
                                     or frame.f_globals.get('__name__') == 'importlib'):
            frame = frame.f_back
        filename = frame.f_code.co_filename if frame is not None else ''
        file = os.path.normcase(os.path.realpath(filename)) if os.path.isabs(filename) else ''
        tree = os.path.normcase(os.path.realpath(globals().get('root', os.getcwd())))
        requested_here = bool(file and os.path.splitdrive(file)[0] == os.path.splitdrive(tree)[0]
                              and os.path.commonpath((tree, file)) == tree)
        del frame
        try:
            return load(name, import_)
        except ModuleNotFoundError as exc:
            if requested_here and ImportError.__dict__['name'].__get__(exc) == name and not own_import_absent(exc, ()):
                _missing_imports.append(exc)
                if globals().get('_preparation_depth', 0):
                    _missing_preparation_imports.add(ImportError.__dict__['name'].__get__(exc))
            raise

    importlib._bootstrap._find_and_load = tracked


def setup_call(phase, function, *args, **kwargs):
    """Keep preparation exceptions out of the operation's outcome handlers."""
    LETS_THROUGH = (CheckSetupError, concurrent.futures.CancelledError, *globals().get('_SETUP_CONTROLS', ()))
    global _preparation_depth
    _preparation_depth = globals().get('_preparation_depth', 0) + 1
    try:
        return function(*args, **kwargs)
    except LETS_THROUGH:
        raise
    except Exception as exc:
        if call_absent(exc) or phase == 'missing_callable' and exception_matches(exc, AttributeError):
            kind = (ModuleNotFoundError if exception_matches(exc, ModuleNotFoundError) else
                    ImportError if exception_matches(exc, ImportError) else AttributeError)
            metadata = {'name': ImportError.__dict__['name'].__get__(exc)} if kind is ModuleNotFoundError else {}
            clean = kind('call failed: ' + exception_name(kind, qualified=True), **metadata)
            raise clean from None
        if exception_matches(exc, ImportError) or phase == 'module_import' and exception_matches(exc, AttributeError):
            raise ObservationUnavailable(type(exc)) from None
        code = typed_code(exc)
        if code is not None:
            raise CheckSetupError(code + ': ' + phase) from exc
        raise CheckSetupError(phase) from exc
    finally:
        _preparation_depth -= 1


def call_absent(exc):
    """Only G6's import or missing-name boundary can identify absence across nested preparation."""
    if not (exception_matches(exc, AttributeError) or exception_matches(exc, ImportError)):
        return False
    trace = BaseException.__dict__['__traceback__'].__get__(exc)
    while trace is not None and trace.tb_frame.f_globals is setup_call.__globals__:
        if trace.tb_frame.f_code is setup_call.__code__:
            # The exact replacement raised by this G6 frame keeps its absence origin across outer setup calls.
            if trace.tb_frame.f_locals.get('clean') is exc:
                return True
            phase = trace.tb_frame.f_locals.get('phase')
            if (phase == 'missing_callable' and exception_matches(exc, AttributeError)
                    or exception_matches(exc, ImportError)
                    and own_import_absent(exc, trace.tb_frame.f_locals.get('args', ()) if phase == 'module_import' else ())):
                return True
        trace = trace.tb_next
    return False


def target_attribute(owner, name):
    """A statically absent name fails the call; an existing attribute's failing lookup is setup."""
    try:
        inspect.getattr_static(owner, name)
    except AttributeError:
        if type(owner) is type(sys) and '__getattr__' in vars(owner):
            return setup_call('module_import', getattr, owner, name)
        return setup_call('missing_callable', getattr, owner, name)
    except Exception as exc:
        raise CheckSetupError('method_lookup') from exc
    return setup_call('method_lookup', getattr, owner, name)


def runtime_method(state, cls, method_name):
    """Ordinary instance lookup, after construction and immediately before each invocation."""
    if type(state) is not cls:
        raise CheckSetupError("constructor_type: constructor did not return the named class")
    method = target_attribute(state, method_name)
    if not callable(method):
        raise CheckSetupError("method_lookup: the instance attribute is not callable")
    return method


def bind_method(module, spec, load):
    """Fixed child code: construct once and look up the current instance method on every call."""
    class_name, method_name = spec[2].split(".")
    cls = target_attribute(module, class_name)
    if type(cls) is not type:
        raise CheckSetupError("owner: method owner is not an ordinary class")
    if spec[0] == "state_method":
        def operation(state, *args, **kwargs):
            return runtime_method(state, cls, method_name)(*args, **kwargs)
        return operation
    constructor = spec[4]
    setup = load(constructor["call"])
    args, kwargs = json.loads(json.dumps([constructor["args"], constructor["kwargs"]]))
    state = setup_call("constructor", setup, *args, **kwargs)
    if type(state) is not cls:
        raise CheckSetupError("constructor_type: constructor did not return the named class")

    def operation(*args, **kwargs):
        return runtime_method(state, cls, method_name)(*args, **kwargs)
    return operation


def _import_roots(spec, roots=None):
    """Use held scenario roots, then an ordinary example's resolved ordered roots."""
    if len(spec) == 5 and spec[0] == 'module':
        return spec[4]['import_roots']
    if roots is not None:
        if (type(roots) is not list or not roots or any(type(one) is not str or one != '.' and (
                ':' in one or '\\' in one or any(part in ('', '.', '..') for part in one.split('/')))
                for one in roots)):
            raise ValueError('G6_E_OBSERVATION_UNAVAILABLE: invalid resolved import roots')
        return roots
    roots = ['.', 'src']
    if len(spec) >= 4 and spec[0] in ('module', 'method'):
        path = spec[3]
        if (not isinstance(path, str) or ':' in path or '\\' in path
                or any(part in ('', '.', '..') for part in path.split('/'))):
            raise ValueError('G6_E_OBSERVATION_UNAVAILABLE: invalid resolved repository file')
        stem = spec[1].replace('.', '/')
        for suffix in (stem + '/__init__.py', stem + '.py'):
            if path.endswith('/' + suffix):
                prefix = path[:-len(suffix) - 1]
                return roots if prefix in roots else [prefix, *roots]
    return roots

# Loaded before any repository module. There is no site initialisation, PYTHONPATH, pytest, plugin or ini reader.
CODE_CARRIER_HELPERS = ('CODE_CARRIERS = ' + ascii(CODE_CARRIERS) + '\n'
                        + inspect.getsource(code_carriers) + '\n' + inspect.getsource(typed_code) + '\n')

BOOTSTRAP = (CODE_CARRIER_HELPERS + inspect.getsource(observable_exception) + '\n' + inspect.getsource(_fingerprint) + '\n'
             + inspect.getsource(raised_during_setup) + '\n' + inspect.getsource(execution_control) + '\n'
             + inspect.getsource(setup_outcome) + '\n'
             + inspect.getsource(exception_name) + '\n'
             + inspect.getsource(exception_matches) + '\n' + inspect.getsource(exception_detail) + '\n'
             + inspect.getsource(exception_message) + '\n'
             + inspect.getsource(_exception_observation) + '\n' + inspect.getsource(CheckSetupError) + '\n'
             + inspect.getsource(ObservationUnavailable) + '\n' + inspect.getsource(own_import_absent) + '\n'
             + inspect.getsource(track_imports) + '\n'
             + inspect.getsource(setup_call) + '\n' + inspect.getsource(runtime_method) + '\n'
             + inspect.getsource(call_absent) + '\n'
             + inspect.getsource(target_attribute) + '\n'
             + inspect.getsource(bind_method) + '\n'
             + inspect.getsource(_import_roots)) + r'''
import ast, builtins, collections.abc, concurrent.futures, copy, dis, hashlib, importlib, importlib.machinery, inspect, json, os, re, struct, sys
_SETUP_CODE_CARRIERS = ()
_SETUP_CONTROLS = ()
_IMPORT_OPS = (dis.opmap['IMPORT_NAME'], dis.opmap['IMPORT_FROM'])

def unavailable_import(exc):
    pending, seen = [exc], set()
    while pending:
        one = pending.pop()
        if id(one) in seen:
            continue
        seen.add(id(one))
        for field in ('__cause__', '__context__'):
            linked = BaseException.__dict__[field].__get__(one)
            if linked is not None:
                pending.append(linked)
        if not exception_matches(one, ImportError) or own_import_absent(one, ()):
            continue
        if any(one is missing for missing in _missing_imports):
            return True
        # IMPORT_FROM can fail after loading a module. Metadata on an explicit raise is not import evidence.
        trace = BaseException.__dict__['__traceback__'].__get__(one)
        while trace is not None:
            if trace.tb_frame.f_code.co_code[trace.tb_lasti] in _IMPORT_OPS:
                return True
            trace = trace.tb_next
    return False
def _name(kind):
    return setup_call("exception_description", exception_name, kind)

def _shown(value, limit=200):
    try:
        text = repr(value)
    except BaseException as exc:
        if raised_during_setup(exc) and execution_control(exc):
            raise
        if execution_control(exc):
            raise
        text = "<a value whose repr raised " + _name(type(exc)) + ">"
    text = re.sub(r" at 0x[0-9A-Fa-f]+", "", text)
    text = ''.join(ch if ch.isprintable() else json.dumps(ch, ensure_ascii=True)[1:-1] for ch in text)
    text = text.replace(';', r'\u003b').replace('G6_E_', r'\u00476_E_')
    return text if len(text) <= limit else text[:limit - 3] + "..."

def _attributes(value):
    # an object's own attributes, from its __dict__ and the __slots__ of its classes, or None when it has neither
    found, seen = {}, False
    if isinstance(getattr(value, "__dict__", None), dict):
        found.update(vars(value))
        seen = True
    for kind in type(value).__mro__:
        slots = kind.__dict__.get("__slots__", ())
        for slot in [slots] if isinstance(slots, str) else slots:
            if slot not in ("__dict__", "__weakref__") and hasattr(value, slot):
                found[slot] = getattr(value, slot)
                seen = True
    return {str(key): item for key, item in found.items()} if seen else None

def _sorted(items):
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))

def _plain(value, depth=0):
    # the value in a canonical structural form, never its repr: a tuple as a list; any mapping as its items, sorted;
    # a set sorted; another object as its type's qualified name and its attributes (__dict__, __slots__), so an
    # insertion order, a hash order or a changed repr is no difference; one with neither by its type and its repr
    if depth > 8:
        return {"deeper": _name(type(value))}
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if value == value and abs(value) != float("inf") else {"float": repr(value)}
    if isinstance(value, type):
        return {"class": _name(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return _sorted(_plain(item, depth + 1) for item in value)
    if isinstance(value, collections.abc.Mapping):
        if all(type(key) is str for key in value):
            return {key: _plain(item, depth + 1) for key, item in value.items()}
        return _sorted([_plain(key, depth + 1), _plain(item, depth + 1)] for key, item in value.items())
    found = _attributes(value)
    if found is None:
        return {"type": _name(type(value)), "repr": _shown(value)}
    return {"type": _name(type(value)), "attributes": {key: _plain(item, depth + 1) for key, item in found.items()}}

def _bounded(value):
    text = json.dumps(value, sort_keys=True)
    if len(text) <= 500:
        return value
    return {"sha256": hashlib.sha256(bytes(text, "utf-8")).hexdigest(), "start": text[:200]}
root, channel, raw = sys.argv[1:]
data = json.loads(raw)
sink = open(channel, "ab", buffering=0)
write, dumps, pack = sink.write, json.dumps, struct.pack
is_instance, identity, kind_of = isinstance, id, type
sys.dont_write_bytecode = True
# The channel lives in a fresh G6-owned folder. -B prevents writes; a fresh prefix also prevents cache reads.
sys.pycache_prefix = os.path.join(os.path.dirname(channel), "pycache")
def _no_sourceless(self, fullname):
    raise ImportError("G6_E_OBSERVATION_UNAVAILABLE: sourceless bytecode is outside the execution manifest")
importlib.machinery.SourcelessFileLoader.get_code = _no_sourceless
sys.path[:0] = [os.path.join(root, part) for part in _import_roots(data["function"], data.get("import_roots"))]
track_imports()
def encode(value):
    try:
        return _fingerprint(dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        return None
def load(spec):
    return setup_call("import_or_binding", load_target, spec)

def load_target(spec):
    kind, module, attribute = spec[:3]
    if kind == "builtin":
        return getattr(builtins, attribute)
    if len(spec) not in (4, 5):
        raise CheckSetupError("import_or_binding")
    wanted = os.path.realpath(os.path.join(root, *spec[3].split("/")))
    if len(spec) == 5 and kind == "module":
        stem = module.replace(".", "/")
        choices = [os.path.join(root, prefix, stem + suffix)
                   for prefix in _import_roots(spec) for suffix in ("/__init__.py", ".py")]
        resolved = next((os.path.realpath(path) for path in choices if os.path.isfile(path)), None)
        if resolved is None or os.path.normcase(resolved) != os.path.normcase(wanted):
            raise CheckSetupError("import_or_binding")
    found = setup_call('module_import', importlib.import_module, module)
    where = os.path.realpath(getattr(found, "__file__", ""))
    if os.path.normcase(where) != os.path.normcase(wanted):
        raise CheckSetupError("import_or_binding")
    target = bind_method(found, spec, load) if kind in ("method", "state_method") else target_attribute(found, attribute)
    if not callable(target):
        raise CheckSetupError("binding: target is not callable")
    return target
# Evict the resolved module and its parents, including any same-named standard-library cache entry. The encoder,
# writer and import machinery above are already held by the bootstrap.
module = data["function"][1]
for name in list(sys.modules):
    if name == module or name.startswith(module + ".") or module.startswith(name + "."):
        del sys.modules[name]
# A built-in outcome type is read now, before any repository code runs. Nothing else an outcome names is imported or
# initialised before the call: the person confirmed a fresh import of the function's module and the call, no more.
fixed = {}
for spec in data["errors"]:
    if spec[0] == "builtin":
        error = getattr(builtins, spec[2])
        if not observable_exception(error, resolved=True):
            raise RuntimeError("expected error is not an exception type")
        fixed[dumps(spec, sort_keys=True)] = error
namespace_of = type(sys).__dict__["__dict__"].__get__
def held(mapping, name):
    # What an exact dict holds under an exact str key, compared by str's own equality: no key's own hooks run.
    if kind_of(mapping) is dict:
        for key, value in dict.items(mapping):
            if kind_of(key) is str and key == name:
                return value
    return None
def named(spec):
    # After a call: the exception type an outcome names, among the modules that call loaded, by qualified name and
    # from the file G6 resolved. Never an import, and never a lookup that a repository module, class or metaclass
    # can intercept. None when no such exception type is loaded: that outcome cannot be compared with a raise.
    try:
        if len(spec) != 4:
            return None
        namespace = namespace_of(held(sys.modules, spec[1]))
        where = held(namespace, "__file__")
        wanted = os.path.realpath(os.path.join(root, *spec[3].split("/")))
        if kind_of(where) is not str or os.path.normcase(os.path.realpath(where)) != os.path.normcase(wanted):
            return None
        found = held(namespace, spec[2])
        return found if observable_exception(found, resolved=True) else None
    except Exception:
        return None
def resolved():
    found = {}
    for spec in data["errors"]:
        key = dumps(spec, sort_keys=True)
        error = fixed[key] if key in fixed else named(spec)
        alive.append(error)
        found[key] = None if error is None else identity(error)
    return found

def setup_failed(exc, *, constructor=False):
    # Ordinary probe setup stays unavailable; a constructor signal keeps its setup outcome.
    if data['probe'] and not constructor and type(exc) is not ObservationUnavailable:
        exc = ObservationUnavailable(detail=ObservationUnavailable.NO_OBSERVATION)
    version = 'observation_unavailable.v1' if type(exc) is ObservationUnavailable else 'check_setup.v1'
    payload = dumps({"version": version, "detail": exception_detail(exc, "setup")},
                    sort_keys=True, allow_nan=False).encode("utf-8")
    if len(payload) > 1048576:
        raise RuntimeError("setup error too large")
    write(b"G6R1\n" + pack(">I", len(payload)) + payload)
    sink.close()
    raise SystemExit(0)

def call_failed(exc):
    try:
        detail = exception_detail(exc, 'call failed')
    except CheckSetupError as unavailable:
        setup_failed(unavailable)
    payload = dumps({'version': 'call_failure.v1', 'detail': detail},
                    sort_keys=True, allow_nan=False).encode('utf-8')
    write(b'G6R1\n' + pack('>I', len(payload)) + payload)
    sink.close()
    raise SystemExit(0)

try:
    function = load(data["function"])
except CheckSetupError as exc:
    setup_failed(exc)
except (AttributeError, ImportError) as exc:
    call_failed(exc)
except BaseException as exc:
    if execution_control(exc) or (raised_during_setup(exc) and exception_matches(exc, CheckSetupError)):
        raise
    preparation = setup_outcome(exc)
    if preparation is not None:
        setup_failed(preparation, constructor=raised_during_setup(exc, 'constructor'))
    raise
errors, outcomes, alive = {}, [], []
for call in data["calls"]:
    args, kwargs = copy.deepcopy(call["args"]), copy.deepcopy(call.get("kwargs", {}))
    try:
        result = function(*args, **kwargs)
    except CheckSetupError as exc:
        setup_failed(exc)
    except BaseException as exc:
        if ((execution_control(exc) and (raised_during_setup(exc) or not exception_matches(exc, KeyboardInterrupt)))
                or (raised_during_setup(exc) and exception_matches(exc, CheckSetupError))):
            raise
        preparation = setup_outcome(exc)
        if preparation is not None:
            setup_failed(preparation, constructor=raised_during_setup(exc, 'constructor'))
        if call_absent(exc):
            call_failed(exc)
        errors = resolved()
        unavailable = None
        if unavailable_import(exc):
            unavailable = ObservationUnavailable(type(exc))
            if data['probe']:
                setup_failed(unavailable)
        alive.append(exc)
        outcomes.append(_exception_observation(exc, errors, data['probe']))
        if unavailable is not None:
            # An import that did not run cannot establish even a confirmed raises-ImportError outcome.
            outcomes[-1]['unavailable_import'] = exception_detail(unavailable, 'setup')
    else:
        errors = resolved()
        one = {"returned": encode(result), "arguments": [encode(arg) for arg in args]}
        if data["probe"]:
            one["probe"] = ({"raised": None} if data["mode"] == "exceptions" else
                            {"returned": _bounded(_plain(result))})
        outcomes.append(one)
    if _missing_imports:
        # The host alone knows whether a fallback matched. Keep provenance even when no exception escaped.
        outcomes[-1]['missing_import'] = ObservationUnavailable.DETAIL
payload = dumps({"version": "observation.v2", "outcomes": outcomes, "errors": errors},
                sort_keys=True, allow_nan=False).encode("utf-8")
if len(payload) > 1048576:
    raise RuntimeError("observation too large")
write(b"G6R1\n" + pack(">I", len(payload)) + payload)
sink.close()
'''


def frame(value: dict) -> bytes:
    payload = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    return MAGIC + struct.pack(">I", len(payload)) + payload


def _unique(pairs):
    found = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key")
        found[key] = value
    return found


def read_frame(raw: bytes) -> dict | None:
    """Total reader: exactly one bounded, recognised frame, with no prefix or trailing bytes."""
    try:
        if not isinstance(raw, bytes) or not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
            return None
        size = struct.unpack(">I", raw[len(MAGIC):len(MAGIC) + 4])[0]
        if size > MAX_FRAME or len(raw) != len(MAGIC) + 4 + size:
            return None
        value = json.loads(raw[len(MAGIC) + 4:], object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if isinstance(value, dict) and value.get("version") == "check_setup.v1":
            return value if (set(value) == {"version", "detail"} and isinstance(value["detail"], str)
                             and len(value['detail']) <= 300
                             and value['detail'] in CheckSetupError.details()) else None
        if isinstance(value, dict) and value.get('version') == 'observation_unavailable.v1':
            return value if (set(value) == {'version', 'detail'}
                             and ObservationUnavailable.valid_detail(value['detail'])) else None
        if isinstance(value, dict) and value.get('version') == 'call_failure.v1':
            return value if (set(value) == {'version', 'detail'} and type(value['detail']) is str
                             and value['detail'] in CheckSetupError.CALL_FAILURES) else None
        if (not isinstance(value, dict) or set(value) != {"version", "outcomes", "errors"}
                or value["version"] not in {"observation.v1", "observation.v2"}
                or not isinstance(value["outcomes"], list)
                or not isinstance(value["errors"], dict)
                # observation.v2: null says the call loaded no exception type of that name (it is never imported).
                or any(type(v) is not int and (v is not None or value["version"] != "observation.v2")
                       for v in value["errors"].values())):
            return None
        for one in value["outcomes"]:
            if not isinstance(one, dict):
                return None
            fields = set(one) - {"probe"}
            if 'missing_import' in fields:
                if value['version'] != 'observation.v2' or one['missing_import'] != ObservationUnavailable.DETAIL:
                    return None
                fields.remove('missing_import')
            if 'unavailable_import' in fields:
                if (value['version'] != 'observation.v2' or 'raised' not in fields
                        or not ObservationUnavailable.valid_detail(one['unavailable_import'])
                        or not one['unavailable_import'].startswith(ObservationUnavailable.DETAIL + ': ')):
                    return None
                fields.remove('unavailable_import')
            if 'exception_detail' in fields:
                detail = one['exception_detail']
                if (value['version'] != 'observation.v2'
                        or fields != {'raised', 'raised_name', 'exception_detail'}
                        or not isinstance(detail, dict) or set(detail) != {'str', 'repr', 'arguments', 'metaclass'}
                        or any(not isinstance(text, str) or len(text) > 200 for text in detail.values())):
                    return None
                fields.remove('exception_detail')
            if fields == {"returned", "arguments"}:
                if not isinstance(one['arguments'], list):
                    return None
            elif fields == {"raised", "raised_name"}:
                if (not isinstance(one["raised"], list) or not one["raised"]
                        or any(type(v) is not int for v in one["raised"])
                        or not isinstance(one["raised_name"], str)):
                    return None
            else:
                return None
        for one in value["outcomes"]:
            for encoded in ([one["returned"], *one["arguments"]] if "returned" in one else []):
                if (value['version'] == 'observation.v1' and encoded is not None and not isinstance(encoded, str)
                        or not _valid_encoding(encoded)):
                    return None
        return value
    except (ValueError, TypeError, RecursionError, OverflowError, struct.error, UnicodeError):
        return None


def _encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _valid_encoding(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):  # Existing observation.v1 frames retain their meaning, including in-process witnesses.
        return _encoded(json.loads(value)) == value
    return (isinstance(value, dict) and set(value) == {'sha256', 'size', 'prefix'}
            and isinstance(value['sha256'], str) and len(value['sha256']) == 64
            and all(ch in '0123456789abcdef' for ch in value['sha256'])
            and type(value['size']) is int and value['size'] >= 0
            and isinstance(value['prefix'], str) and len(value['prefix']) <= 200
            and len(bytes(value['prefix'], 'utf-8')) <= value['size'])


def _matches_encoding(observed, expected) -> bool:
    text = _encoded(expected)
    if isinstance(observed, dict):
        wanted = _fingerprint(text)
        return observed['sha256'] == wanted['sha256'] and observed['size'] == wanted['size']
    return observed == text


def finish(code: int | None, raw: bytes, data: dict) -> dict:
    """G6 compares the observation here, in its own process; child exit status supplies no support or failure."""
    observed = read_frame(raw) if code == 0 else None
    calls = data.get("cases", data.get("calls", []))
    if observed is not None and observed["version"] == "check_setup.v1":
        return {"result": "error", "detail": observed["detail"], 'reason_code': 'G6_E_CHECK_SETUP'}
    if observed is not None and observed['version'] == 'observation_unavailable.v1':
        return {'result': 'error', 'detail': observed['detail'], 'reason_code': 'G6_E_OBSERVATION_UNAVAILABLE'}
    if observed is not None and observed['version'] == 'call_failure.v1':
        return {'result': 'failed', 'detail': observed['detail']}
    if observed is None or len(observed["outcomes"]) != len(calls) or not calls:
        return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: no single completed observation"}
    expected_errors = {_encoded(spec) for spec in data.get("errors", [
        case["error"] for case in calls if case.get("expect") == "raises"])}
    if set(observed["errors"]) != expected_errors or any(
            "returned" in one and len(one["arguments"]) != len(call.get("args", []))
            for call, one in zip(calls, observed["outcomes"], strict=True)):
        return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: observation does not match the scenario"}
    if "cases" not in data:
        if any('unavailable_import' in one or 'missing_import' in one for one in observed['outcomes']):
            return {'result': 'error', 'detail': ObservationUnavailable.DETAIL,
                    'reason_code': 'G6_E_OBSERVATION_UNAVAILABLE'}
        if any("probe" not in one for one in observed["outcomes"]):
            return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: missing probe observation"}
        return {"result": "passed", "detail": "G6 observed the calls in its isolated runner", "observed": observed,
                "owned_probe": True, "observations": [{"input": call, "output": one["probe"]}
                    for call, one in zip(calls, observed["outcomes"], strict=True)]}
    failures = []
    for index, (case, outcome) in enumerate(zip(calls, observed["outcomes"], strict=True)):
        expected = case["expect"]
        if 'unavailable_import' in outcome:
            return {'result': 'error', 'detail': outcome['unavailable_import'],
                    'reason_code': 'G6_E_OBSERVATION_UNAVAILABLE'}
        if expected == "raises":
            token = observed["errors"].get(_encoded(case["error"]))
            if "raised" not in outcome:
                matches = False   # the call returned: it raised no exception of any type
            elif type(token) is not int:
                # The call raised, and loaded no exception type of the confirmed name to compare it with.
                return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: unresolved expected error"}
            else:
                matches = token in outcome["raised"]
        elif expected == "returns":
            matches = "returned" in outcome and _matches_encoding(outcome["returned"], case["value"])
        elif expected == "effect":
            arguments = outcome.get("arguments", [])
            arg = case["argument"]
            matches = ("returned" in outcome and 0 <= arg < len(arguments)
                       and _matches_encoding(arguments[arg], case["value"]))
        else:
            return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: unknown comparison"}
        if not matches:
            failures.append(index)
    if failures and any('missing_import' in one for one in observed['outcomes']):
        return {'result': 'error', 'detail': ObservationUnavailable.DETAIL,
                'reason_code': 'G6_E_OBSERVATION_UNAVAILABLE'}
    return {"result": "failed" if failures else "passed", "observed": observed,
            "detail": ("the confirmed outcome did not match the observed call(s): " + str(failures)) if failures
                      else "the confirmed outcome matched what the entry point returned or raised in G6's runner"}


def bindings(source: str) -> tuple[str, dict] | None:
    """Read only G6's own generated Python literal slots; never execute a proposed check to learn its bindings."""
    try:
        fields = {}
        for one in ast.parse(expanded(source)).body:
            if isinstance(one, ast.Assign) and len(one.targets) == 1 and isinstance(one.targets[0], ast.Name):
                if one.targets[0].id in {"TEMPLATE", "BINDINGS"}:
                    if one.targets[0].id in fields:
                        return None
                    fields[one.targets[0].id] = ast.literal_eval(one.value)
            elif (isinstance(one, ast.Assign) and len(one.targets) == 1
                  and ast.unparse(one.targets[0]) == "BINDINGS['option']"):
                fields["option"] = ast.literal_eval(one.value)
        if fields.get("TEMPLATE") in RUN_TEMPLATES and isinstance(fields.get("BINDINGS"), dict):
            data = fields["BINDINGS"]
            if '_program' in data:
                return None  # only Scenario below can construct G6's executable program
            if "scenario" in data:
                from g6.harness.executable_scenario import Scenario

                executable = Scenario(data["scenario"].encode("ascii"))
                data = {**executable.comparison(data.get("option", fields.get("option"))),
                        "_program": executable.program}
            return fields["TEMPLATE"], data
    except (ValueError, TypeError, SyntaxError, RecursionError, KeyError, IndexError):
        pass
    return None


def located(copy: Path, data: dict) -> dict:
    """Resolve against the held scenario roots or the ordinary example's resolved file root."""
    data = json.loads(_encoded(data))
    roots = _import_roots(data['function'], data.get('import_roots'))
    specs = [data["function"], *data.get("errors", []),
             *(case["error"] for case in data.get("cases", []) if "error" in case)]
    if data['function'][0] == 'method':
        specs.append(data['function'][4]['call'])
    for spec in specs:
        if spec[0] == "builtin":
            continue
        stem = spec[1].replace(".", "/")
        choices = [('/'.join((prefix, stem)) if prefix != '.' else stem) + suffix
                   for prefix in roots for suffix in ("/__init__.py", ".py")]
        resolved = next((rel for rel in choices if (copy / rel).is_file()), None)
        if resolved is None or len(spec) >= 4 and resolved != spec[3]:
            raise ValueError("G6_E_OBSERVATION_UNAVAILABLE: the copied entry point does not match")
        spec[:] = [*spec[:3], resolved, *spec[4:]]
    return data


def request(data: dict) -> dict:
    """Only setup and inputs reach the child. The chosen outcome stays in G6's process."""
    calls = data.get("cases", data.get("calls", []))
    return {"function": data["function"], "calls": [{"args": c["args"], "kwargs": c.get("kwargs", {})} for c in calls],
            "errors": data.get("errors", [case["error"] for case in calls if case.get("expect") == "raises"]),
            "probe": "cases" not in data, "mode": data.get("mode", "outputs"),
            **({'own_modules': data['own_modules']} if 'own_modules' in data else {}),
            **({'import_roots': data['import_roots']} if 'import_roots' in data else {})}


def _portable_source(function) -> str:
    """Keep helper semantics while omitting documentation and compacting fixed-code indentation."""
    tree = ast.parse(inspect.getsource(function))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and type(first.value.value) is str:
                node.body.pop(0)
    return '\n'.join(' ' * ((len(line) - len(line.lstrip(' '))) // 4) + line.lstrip(' ')
                     for line in ast.unparse(tree).splitlines())


def example_text(slot: str) -> str:
    """Portable examples use the same fresh interpreter and comparison as product enforcement.

    Pytest is only a convenience for displaying a reproduction. Product evidence comes from ``run`` and its
    host-owned result channel, never from collection, plugins or the wrapper's pytest status.
    """
    helpers = "\n\n".join(_portable_source(one) for one in (
        CheckSetupError, ObservationUnavailable, raised_during_setup, execution_control,
        exception_name, exception_matches,
        exception_detail, exception_message,
        setup_call, call_absent, register_setup_error,
        _unique, _encoded, _fingerprint, _valid_encoding, _matches_encoding,
        _import_roots, located, request))
    bootstrap = base64.b85encode(zlib.compress((confine.CHILD_PRELUDE + BOOTSTRAP).encode('utf-8'), 9)).decode('ascii')
    carriers = base64.b85encode(zlib.compress(CODE_CARRIER_HELPERS.encode('utf-8'), 9)).decode('ascii')
    preparation = '\n\n'.join(_portable_source(one) for one in (own_import_absent, setup_outcome, read_frame, finish))
    packed = base64.b85encode(zlib.compress(preparation.encode('utf-8'), 9)).decode('ascii')
    return ("import base64, zlib, concurrent.futures, hashlib, json, struct, subprocess, tempfile, sys, os\nfrom pathlib import Path\n"
            "TEMPLATE = 'example.v1'\nBINDINGS = " + slot
            + "\nMAGIC = b'G6R1\\n'\nMAX_FRAME = 1048576\nBOOTSTRAP = "
            + 'str(zlib.decompress(base64.b85decode(' + ascii(bootstrap) + ")), 'utf-8')\n"
            + 'exec(zlib.decompress(base64.b85decode(' + ascii(carriers) + ')))\n' + helpers
            + '\nexec(zlib.decompress(base64.b85decode(' + ascii(packed) + ')))\n' + '''
register_setup_error(TEMPLATE)
def _check_example():
    data = located(Path.cwd(), BINDINGS)
    with tempfile.TemporaryDirectory() as own:
        channel = os.path.join(own, 'result.bin')
        boot = os.path.join(own, 'bootstrap.py')
        with open(boot, 'w', encoding='utf-8', newline='\\n') as handle:
            handle.write(BOOTSTRAP)
        with open(channel, 'xb'):
            pass
        ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', boot,
                              os.getcwd(), channel, _encoded(request(data))], capture_output=True, timeout=10)
        with open(channel, 'rb') as handle:
            raw = handle.read(MAX_FRAME + len(MAGIC) + 5)
    reading = finish(ran.returncode, raw, data)
    if reading.get('reason_code') == 'G6_E_CHECK_SETUP':
        raise CheckSetupError(reading['detail'].partition(': ')[2])
    if reading.get('reason_code') == 'G6_E_OBSERVATION_UNAVAILABLE':
        raise ObservationUnavailable(detail=reading['detail'])
    if reading['result'] != 'passed':
        raise AssertionError(reading['detail'])

def test_example():
    import pytest
    try:
        _check_example()
    except (AssertionError, ValueError) as exc:
        pytest.fail(str(exc), pytrace=False)
''')


def _made(tree: Path, copy: Path, files: dict[str, str], deadline: float) -> None:
    """Write the folder the child runs in from the manifest, never by walking the copy: exactly the files ``files``
    lists (``_copy_snapshot``'s record of the copy), each a new regular file holding the bytes read from the copy's
    file of that name and hashed as they are written. Bytes that do not hash to the manifest's value, a record that
    is not a file's hash (a link the copy holds) and a name that cannot be made new below ``tree`` (another spelling
    of one already written) refuse the run. Nothing else the copy or its file system holds reaches the child:
    another stream of a file or a folder, another name for a file, an entry the manifest does not list."""
    tree.mkdir()
    size = 0
    for count, (rel, wanted) in enumerate(sorted(files.items()), 1):
        parts = PurePosixPath(rel).parts
        target = tree.joinpath(*parts)
        if (not isinstance(wanted, str) or len(wanted) != 64 or any(ch not in "0123456789abcdef" for ch in wanted)
                or not parts or ".." in parts or not target.is_relative_to(tree) or os.name == "nt" and ":" in rel):
            raise rt.CopyNotRun("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: not a file G6 can write from its bytes: " + rel)
        written = hashlib.sha256()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with copy.joinpath(*parts).open("rb") as source, target.open("xb") as made:
                while block := source.read(1 << 20):
                    size += len(block)
                    rt._copy_caps(count, size, deadline)
                    written.update(block)
                    made.write(block)
        except FileExistsError as exc:
            raise rt.CopyNotRun("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: two names of the manifest are one name in the "
                                "folder the check runs in: " + rel) from exc
        if written.hexdigest() != wanted:
            raise rt.CopyNotRun("G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: the copy changed while G6 wrote the folder "
                                "the check runs in: " + rel)


def _beyond(tree: Path) -> dict[str, str]:
    """What a path in ``tree`` carries beyond the files' names and bytes ``_copy_snapshot`` reads, as entries no
    manifest holds: a folder that holds nothing (its name is all it carries, and no file's path implies it), a file
    that has another name (a hard link: its bytes can be written through a path outside the folder), and every
    stream beyond a file's unnamed one (NTFS: ``name:stream`` on a file or on a folder, the top folder included).
    Read with the names and bytes after the child ends, so each is a change like any other. A stream listing that
    fails is an entry too (an unread stream is never read as none). No stream is listed where the volume has no
    named streams, or off Windows."""
    found: dict[str, str] = {}
    for folder, dirs, files in os.walk(tree):
        component_process.checkpoint()
        here = Path(folder).relative_to(tree).as_posix()
        if not dirs and not files:
            found[here + "/"] = "empty folder"
        dirs[:] = [name for name in dirs if not rt._linked(Path(folder) / name)]   # a link is the snapshot's entry
        for name in files:
            if os.lstat(os.path.join(folder, name)).st_nlink != 1:
                found[(Path(folder) / name).relative_to(tree).as_posix() + " (another name)"] = "hard link"
    return {**found, **_streams(tree)}


def _streams(tree: Path) -> dict[str, str]:
    """``_beyond``'s streams: every one beyond a file's unnamed stream, on each file and folder of ``tree``."""
    if os.name != "nt":
        return {}
    import ctypes
    from ctypes import wintypes

    class Found(ctypes.Structure):   # WIN32_FIND_STREAM_DATA: the name reads ":name:$DATA", the unnamed one "::$DATA"
        _fields_ = [("size", ctypes.c_longlong), ("name", ctypes.c_wchar * 296)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(Found), wintypes.DWORD]
    kernel.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Found)]
    kernel.FindClose.argtypes = [wintypes.HANDLE]
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPDWORD,
                                             wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPWSTR, wintypes.DWORD]
    top = os.path.abspath(tree)
    volume, flags = ctypes.create_unicode_buffer(len(top) + 2), wintypes.DWORD()
    if (kernel.GetVolumePathNameW(top, volume, len(volume))
            and kernel.GetVolumeInformationW(volume.value, None, 0, None, None, ctypes.byref(flags), None, 0)
            and not flags.value & 0x00040000):   # FILE_NAMED_STREAMS: this volume has none to list
        return {}
    found: dict[str, str] = {}
    end, invalid = 38, wintypes.HANDLE(-1).value   # ERROR_HANDLE_EOF: no (further) stream
    for folder, dirs, files in os.walk(top):
        component_process.checkpoint()
        dirs[:] = [name for name in dirs if not rt._linked(Path(folder) / name)]   # a link is the snapshot's entry
        for path in (folder, *(os.path.join(folder, name) for name in files)):
            rel = Path(path).relative_to(top).as_posix()
            one = Found()
            handle = kernel.FindFirstStreamW(path if path.startswith("\\\\") else "\\\\?\\" + path, 0,
                                             ctypes.byref(one), 0)
            if handle in (None, invalid):
                if ctypes.get_last_error() != end:
                    found[rel + ":"] = f"streams not read (error {ctypes.get_last_error()})"
                continue
            try:
                while True:
                    if one.name != "::$DATA":
                        found[rel + one.name] = "stream"
                    if not kernel.FindNextStreamW(handle, ctypes.byref(one)):
                        if ctypes.get_last_error() != end:
                            found[rel + ":"] = f"streams not read (error {ctypes.get_last_error()})"
                        break
            finally:
                kernel.FindClose(handle)
    return found


def run(copy: Path, data: dict, node: str, *, timeout_s: int, deadline: float) -> dict:
    """One confined interpreter G6 starts, in a folder G6 writes from the copy's manifest (``_made``), and one
    channel G6 creates before starting it. The folder is read again after the child ends: its names and bytes by the
    ordinary executor's comparison, and what a path in it carries beyond them (``_beyond``)."""
    started = time.monotonic()
    before = rt._copy_snapshot(copy, deadline=deadline)
    data = located(copy, data)
    # G6's own Python, never a repository interpreter or repository site packages.
    python = sys.executable
    with confine.run_folder(prefix="g6_observation_", dir=copy.parent, ignore_cleanup_errors=True) as top:
        own = Path(top)
        channel, boot, log, tree = own / "result.bin", own / "bootstrap.py", own / "output.log", own / "tree"
        _made(tree, copy, before, deadline)
        remaining = min(timeout_s, deadline - time.monotonic())
        if remaining <= 0:
            return {"verdict": "not_run", "said": "G6_E_OBSERVATION_UNAVAILABLE: timeout before startup"}
        program = data.get("_program", confine.CHILD_PRELUDE + BOOTSTRAP)
        boot.write_text(program, encoding="utf-8", newline="\n")
        with channel.open("xb"):
            pass
        plan = confine.plan(confine.Confinement(read_write=(str(own),),
                            read_only=confine.interpreter_paths(python), network=False), python)
        # the observation record is G6's data, read back by its exact-type schema (repo_tests.read_observation): a
        # text marked for the agent is a str subclass that schema refuses, so it is marked where a reply is built
        isolation = plan.record()
        if confine_refused(plan) or not plan.backend:
            plan.release()
            raise rt.RepoRefused(ISOLATION_UNAVAILABLE, confine_refusal(plan))
        try:
            # A canonical program already holds its input. Repeating it on argv also hits Windows' command limit.
            payload = '{}' if '_program' in data else _encoded(request(data))
            command = [python, "-I", "-S", "-B", "-X", "utf8", str(boot), str(tree), str(channel), payload]
            code = rt._bounded(command, cwd=tree, env=rt.child_env(own, network=False),
                               timeout_s=remaining, log=log, confined=plan)
            with channel.open("rb") as handle:
                raw = handle.read(MAX_FRAME + len(MAGIC) + 5)
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
            raise
        except OSError:
            code, raw = None, b""
        finally:
            cleanup = plan.release()
            if cleanup:
                isolation["cleanup"] = cleanup
        found = finish(code, raw, data)
        after = {**rt._copy_snapshot(tree, deadline=deadline), **_beyond(tree)}
    moved = rt._moved(before, after)
    if moved:
        return {"verdict": rt.CHANGED, "changed": moved, "source_before": digest(before),
                "isolation": isolation, "said": "the execution copy changed; its observation is not counted"}
    return {"verdict": found["result"] if found["result"] in {"passed", "failed"} else "not_run",
            "g6_observation": found, "source_before": digest(before), "isolation": isolation,
            "execution_limit": LIMIT,
            "python": python, "interpreter_source": "g6",
            "import_path": _import_roots(data['function'], data.get('import_roots')), "own_config": True,
            "seconds": round(time.monotonic() - started, 3), "said": found["detail"], "output": "",
            "command": "python -I -S -B <G6 bootstrap>",
            "bootstrap_sha256": hashlib.sha256(program.encode("utf-8")).hexdigest()}
