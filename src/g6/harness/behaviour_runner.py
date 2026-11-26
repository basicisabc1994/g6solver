# SPDX-License-Identifier: Apache-2.0
"""G6's isolated observation runner. Repository test configuration is never loaded.

The result channel excludes repository test configuration. The bootstrap shares its interpreter with the code
under test: the channel is not an authentication boundary inside Python.
The observation.v2 frame carries canonical value digests and byte sizes with bounded diagnostic prefixes. Existing
observation.v1 frames keep their comparison semantics; neither format authenticates its producer inside the child.
A tree folder sharing an installed package's name may load as an empty namespace package. If no import fails,
the isolated runner has no missing-import evidence to distinguish its later attribute failure from the code's.
An availability flag from find_spec returning None is not a failed import. Imports made by exec text without a
tree filename may also leave no usable provenance; those fallback paths remain a limit of this observation.
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
import re
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


def path_key(path):
    """One absolute filesystem identity for repository, archive and interpreter paths."""
    value = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    if os.name == 'nt':
        if value.startswith('\\\\?\\unc\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
    return value


def path_inside(root, path):
    root, path = path_key(root), path_key(path)
    try:
        return os.path.commonpath((root, path)) == root
    except ValueError:
        return False


def ownership_location(path):
    """Directories have filesystem identity; archive member directories retain exact spelling."""
    archives = globals().get('_ownership_archives', ())
    if archives:
        folder, members = os.path.abspath(path), []
        while True:
            key = path_key(folder)
            if key in archives:
                return key, tuple(reversed(members))
            parent, entry = os.path.split(folder)
            if parent == folder:
                break
            folder = parent
            members.append(entry)
    return path_key(path), ()


def find_owned(name, locations):
    """Only exact entries at locations Python used, or its selected closed package, can own a find."""
    if locations is None:
        return False
    candidates = globals().get('_owned_module_entries', ())
    leaf = name.rsplit('.', 1)[-1]
    return (any((ownership_location(location or os.getcwd()), leaf + suffix) in candidates
                for location in locations if type(location) is str for suffix in ('.py', ''))
            or own_import_absent(ModuleNotFoundError(name=name), (), locations=locations))


def own_import_absent(exc, args, *, locations=None):
    """Read explicit host module ownership first, then unambiguous regular packages."""
    if not exception_matches(exc, ImportError):
        return False
    name = ImportError.__dict__['name'].__get__(exc)
    path = ImportError.__dict__['path'].__get__(exc)
    if type(name) is not str or not all(part.isidentifier() for part in name.split('.')):
        return False
    missing_module = exception_matches(exc, ModuleNotFoundError)
    if missing_module and args and type(args[0]) is str and name == args[0] and '.' not in name:
        return True
    tree = path_key(globals().get('root', os.getcwd()))
    parts = name.split('.')
    # A recorded find, not a potential-name inventory or a replay of precedence,
    # decides which held/deleted file could supply the missing module.
    owners = {name for name, owned in globals().get('_find_ownership', {}).items() if owned}
    if missing_module and locations is None and name in owners:
        return True
    if not missing_module:
        # A closed package owns absent child modules, but that inference cannot bypass
        # the guarded-export check for an attribute. Only a held/deleted child can.
        trace = BaseException.__dict__['__traceback__'].__get__(exc)
        while trace is not None:
            instruction = next((one for one in dis.get_instructions(trace.tb_frame.f_code)
                                if one.offset == trace.tb_lasti), None)
            if (instruction is not None and instruction.opname == 'IMPORT_FROM'
                    and name + '.' + instruction.argval in owners
                    and name + '.' + instruction.argval in globals().get('data', {}).get('own_modules', ())):
                return True
            trace = trace.tb_next

    def guarded_export(source):
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
            if source is None or len(bytes(source, 'utf-8')) > 1048576:
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

    def owned_spec(name):
        loaded = sys.modules.get(name)
        spec = globals().get('_selected_import_specs', {}).get(name)
        if spec is None and type(loaded) is type(sys):
            spec = type(sys).__dict__['__dict__'].__get__(loaded).get('__spec__')
        if spec is None:
            # Portable setup can fail before installing the observer. An
            # initializer's traceback retains Python's actual selected spec.
            trace = BaseException.__dict__['__traceback__'].__get__(exc)
            while trace is not None:
                space = trace.tb_frame.f_globals
                if space.get('__name__') == name:
                    spec = space.get('__spec__')
                    if spec is not None:
                        break
                trace = trace.tb_next
        if spec is None:
            return None
        archive = getattr(spec.loader, 'archive', None)
        origin = archive if type(archive) is str else spec.origin
        return spec if type(origin) is str and path_inside(tree, origin) else None

    def regular(spec):
        if spec is None or spec.submodule_search_locations is None:
            return False
        if locations is not None and (type(spec.origin) is not str or not any(
                type(where) is str and ownership_location(where or os.getcwd()) ==
                ownership_location(os.path.dirname(spec.origin)) for where in locations)):
            return False
        try:
            source = spec.loader.get_source(spec.name)
            # These packages may have portions G6's isolated interpreter cannot load.
            return source is not None and len(bytes(source, 'utf-8')) <= 1048576 and not any(marker in source for marker in (
                '__path__', 'extend_path', 'declare_namespace'))
        except (OSError, ImportError, AttributeError):
            return False

    # Preserve the established closed-package/export failure decisions from the
    # package Python loaded, constrained to its used search location when observing a find.
    if missing_module:
        if len(parts) > 1 and regular(owned_spec(name.rpartition('.')[0])):
            return True
        # Portable setup has no find observer. A direct, named code exception
        # in the requested initializer is still a code failure, not a failed find.
        if len(parts) > 1 and args and type(args[0]) is str and '_find_ownership' not in globals():
            selected = owned_spec(args[0])
            trace = BaseException.__dict__['__traceback__'].__get__(exc)
            while trace is not None and trace.tb_next is not None:
                trace = trace.tb_next
            if (selected is not None and trace is not None
                    and trace.tb_frame.f_globals.get('__spec__') is selected
                    and type(selected.origin) is str
                    and path_key(trace.tb_frame.f_code.co_filename) == path_key(selected.origin)):
                from dis import opmap as portable_opcodes

                return trace.tb_frame.f_code.co_code[trace.tb_lasti] == portable_opcodes['RAISE_VARARGS']
        return False
    spec = owned_spec(name)
    if type(path) is str and spec is not None and type(spec.origin) is str:
        if path_key(spec.origin) == path_key(path) and (spec.submodule_search_locations is None or regular(spec)):
            return not ((globals().get('_missing_preparation_imports') or globals().get('_missing_imports')
                         or globals().get('_pending_imports', lambda: False)())
                        and guarded_export(spec.loader.get_source(name)))
    return False


def track_imports():
    """Record actual failed finds and the outer request, before repository loading."""
    import builtins
    import importlib._bootstrap
    import threading

    global _missing_preparation_imports, _missing_imports, _missing_import_requests, _pending_imports
    global _missing_request_identities, _failed_import_finds, _run_failed_finds, _find_ownership, _owned_module_entries
    global _selected_import_specs, _ownership_archives
    _missing_preparation_imports, _missing_imports, _missing_import_requests = set(), [], set()
    _missing_request_identities, _failed_import_finds = set(), []
    _run_failed_finds, _find_ownership = [], {}
    _selected_import_specs = {}
    tree = globals().get('root', os.getcwd())
    inventory = globals().get('data', {})
    # The host supplies decoded copy spellings only, never guarded git names.
    files = {name: list(paths) for name, paths in inventory.get('own_module_files', {}).items()}
    for prefix, masks in inventory.get('own_module_layout', ()):
        for suffix, mask in zip(('.py', '/__init__.py'), masks):
            bits = int(mask, 16)
            while bits:
                bit = bits & -bits
                name = inventory['own_modules'][bit.bit_length() - 1]
                files.setdefault(name, []).append(prefix + name.replace('.', '/') + suffix)
                bits -= bit
    _ownership_archives = {path_key(os.path.join(tree, prefix))
                          for prefix in inventory.get('own_module_paths', {})
                          if not os.path.isdir(os.path.join(tree, prefix))}
    entries = set()
    for name, paths in files.items():
        for path in paths:
            folder, entry = os.path.split(os.path.join(tree, path))
            entries.add((folder, entry))
            levels = len(name.split('.')) - (entry != '__init__.py')
            for _ in range(levels):
                folder, entry = os.path.split(folder)
                entries.add((folder, entry))
    # Shared namespace ancestors and multiple import roots need one directory identity each.
    locations = {folder: ownership_location(folder) for folder in {folder for folder, _ in entries}}
    _owned_module_entries = {(locations[folder], entry) for folder, entry in entries}
    load = importlib._bootstrap._find_and_load
    find = importlib._bootstrap._find_spec
    namespace_path = importlib._bootstrap_external._NamespacePath
    unlocked = importlib._bootstrap._find_and_load_unlocked.__code__
    importing, reloading = builtins.__import__, importlib.reload
    library_roots = tuple(path_key(prefix) for prefix in (sys.base_prefix, sys.prefix))
    machinery = path_key(os.path.dirname(importlib.__file__))
    active = threading.local()
    wrapped = {}

    def _pending_imports():
        return any(getattr(active, 'stack', ()))

    def requests():
        if not hasattr(active, 'requests'):
            active.requests = []
        return active.requests

    def failed_find(name, path, target=None):
        used = sys.path if path is None else path
        # Exact built-in containers and the interpreter's reusable namespace path only.
        # Subclasses/custom iterables belong to the code: the observer must not consume them.
        locations = (tuple(used) if type(used) is list or type(used) is tuple
                     or type(used) is namespace_path else None)
        spec = find(name, path, target)
        if spec is None:
            if not hasattr(active, 'finds'):
                active.finds = []
            active.finds.append(name)
            owned = find_owned(name, locations)
            _find_ownership[name] = owned
            if requester(sys._getframe(1)) is not False:
                _run_failed_finds.append((name, owned))
        else:
            _find_ownership.pop(name, None)
            _selected_import_specs[name] = spec
        return spec

    def failed_cause(exc):
        seen, pending = set(), [exc]
        while pending and len(seen) < 1024:
            one = pending.pop()
            if id(one) in seen:
                continue
            seen.add(id(one))
            if any(one is failed for failed in _failed_import_finds):
                return True
            pending.extend(error for error in (BaseException.__dict__['__cause__'].__get__(one),
                                                BaseException.__dict__['__context__'].__get__(one))
                           if error is not None)
        return False

    def retain(exc, name, identity):
        _missing_imports.append(exc)
        _missing_import_requests.add(name)
        _missing_request_identities.add(identity)
        if globals().get('_preparation_depth', 0):
            _missing_preparation_imports.add(ImportError.__dict__['name'].__get__(exc))

    def requested_absent(name):
        # Only absence of the requested owned module is a code failure here. Never let the missing name
        # of a dependency erase an escaping failure of an existing requested module.
        parts = name.split('.')
        for end in range(1, len(parts) + 1):
            parent = '.'.join(parts[:end])
            if parent in _find_ownership:
                return _find_ownership[parent]
        return False

    def requester(frame):
        tree = path_key(globals().get('root', os.getcwd()))
        # None means no caller could be attributed (for example a library callback on a worker thread).
        requested_here = None
        helper = False
        # Attribute library helpers to their nearest repository caller. An import made while a library module
        # itself is loading is that module's request, not an optional dependency requested by the repository.
        while frame is not None:
            if frame.f_code in observer_codes:
                frame = frame.f_back
                continue
            filename = frame.f_code.co_filename
            # Frozen stdlib modules retain their installed file in the module namespace, not co_filename.
            if filename.startswith('<frozen ') and filename not in (
                    '<frozen importlib._bootstrap>', '<frozen importlib._bootstrap_external>'):
                origin = frame.f_globals.get('__file__')
                if type(origin) is str:
                    filename = origin
            file = path_key(filename) if os.path.isabs(filename) else ''
            # The exact G6 bootstrap file is never a repository requester, even in portable fixtures
            # that place it beneath root. This exclusion is by file identity, not its module name.
            if file and file == path_key(__file__):
                if not helper:
                    requested_here = False
                    break
                frame = frame.f_back
                continue
            if file and path_inside(tree, file):
                requested_here = True
                break
            if filename in ('<frozen importlib._bootstrap>', '<frozen importlib._bootstrap_external>') or (
                    file and path_inside(machinery, file)):
                frame = frame.f_back
                continue
            helper = True
            # Our bootstrap/helper frames are never an outside requester. A known library's module
            # load can own its imports; a callback with only runner frames above it is unattributable.
            library = file and any(path_inside(prefix, file) for prefix in library_roots)
            # A library's own import statement is its dependency, including an optional import inside
            # a function. Calls forwarding a repository-supplied name still follow the caller stack.
            if library and (not frame.f_code.co_flags & inspect.CO_NEWLOCALS
                            or frame.f_code.co_code[frame.f_lasti] == dis.opmap['IMPORT_NAME']):
                requested_here = False
                break
            if not library:
                requested_here = True
                break
            frame = frame.f_back
        del frame
        return requested_here

    def scoped(identity, module, function, *args, **kwargs):
        boundary = requests()
        boundary.append(identity)
        try:
            return function(*args, **kwargs)
        except ImportError as exc:
            # Reload and module attribute lookup can escape without an enclosing
            # _find_and_load. A failed find is still required, including through
            # a library's loader or a translated exception.
            if (failed_cause(exc) and not requested_absent(module)
                    and not any(exc is missing for missing in _missing_imports)):
                item = (exc, module, boundary[0])
                stack = getattr(active, 'stack', ())
                if stack:
                    stack[-1].append(item)
                else:
                    retain(*item)
            raise
        finally:
            boundary.pop()

    def wrap_lazy(module, name):
        if type(module) is not type(sys):
            return
        namespace = type(sys).__dict__['__dict__'].__get__(module)
        original = namespace.get('__getattr__')
        if not callable(original) or wrapped.get(id(module)) is original:
            return

        def lazy(attribute):
            return scoped('attribute:' + name + ':' + attribute, name, original, attribute)

        observer_codes.add(lazy.__code__)
        namespace['__getattr__'] = lazy
        wrapped[id(module)] = lazy

    def tracked_import(name, globals=None, locals=None, fromlist=(), level=0):
        requested = requester(sys._getframe(1))
        absolute = name
        if level and type(globals) is dict:
            package = globals.get('__package__')
            if type(package) is str:
                absolute = importlib._bootstrap._resolve_name(name, package, level)
        if requested is False:
            return importing(name, globals, locals, fromlist, level)
        identity = 'import:' + absolute + (':' + ','.join(fromlist) if fromlist else '')
        result = scoped(identity, absolute, importing, name, globals, locals, fromlist, level)
        wrap_lazy(sys.modules.get(absolute), absolute)
        return result

    def tracked_reload(module):
        namespace = type(sys).__dict__['__dict__'].__get__(module) if type(module) is type(sys) else {}
        spec = namespace.get('__spec__')
        name = getattr(spec, 'name', None) or namespace.get('__name__')
        if type(name) is not str or requester(sys._getframe(1)) is False:
            return reloading(module)
        result = scoped('reload:' + name, name, reloading, module)
        wrap_lazy(result, name)
        return result

    def tracked(name, import_):
        requested_here = requester(sys._getframe(1))
        stack = getattr(active, 'stack', None)
        if stack is None:
            stack = active.stack = []
        pending = []
        stack.append(pending)
        boundary = requests()
        outer = requested_here is not False and not boundary
        if outer:
            boundary.append('import:' + name)
        start = len(getattr(active, 'finds', ()))
        try:
            result = load(name, import_)
            wrap_lazy(result, name)
            return result
        except ImportError as exc:
            trace = BaseException.__dict__['__traceback__'].__get__(exc)
            while trace is not None and trace.tb_next is not None:
                trace = trace.tb_next
            # A None spec is a failed find, not yet a failed import (find_spec
            # queries may return None deliberately). Pair it with the import
            # system's own escaping exception; repository metadata cannot supply it.
            missing = ImportError.__dict__['name'].__get__(exc)
            if (trace is not None and trace.tb_frame.f_code is unlocked
                    and missing == name and name in getattr(active, 'finds', ())[start:]):
                _failed_import_finds.append(exc)
            if (requested_here is not False and failed_cause(exc)
                    and not requested_absent(name)):
                # An escaping dependency failure belongs to the outer repository request. A caught inner
                # failure still survives if that outer import completes successfully.
                pending[:] = [(exc, name, boundary[0])]
            raise
        finally:
            if outer:
                boundary.pop()
            stack.pop()
            if stack:
                stack[-1].extend(pending)
            else:
                for item in pending:
                    retain(*item)

    observer_codes = {scoped.__code__, tracked.__code__, tracked_import.__code__, tracked_reload.__code__,
                      failed_find.__code__}
    importlib._bootstrap._find_spec = failed_find
    importlib._bootstrap._find_and_load = tracked
    builtins.__import__ = tracked_import
    importlib.reload = tracked_reload
    for name, module in tuple(sys.modules.items()):
        wrap_lazy(module, name)


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
        # Preserve the marked outer import through preparation.
        if any(exc is missing for missing in globals().get('_missing_imports', ())):
            raise
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
    if any(exc is missing for missing in globals().get('_missing_imports', ())):
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

def shown_value(value, limit=200):
    """Normalize object displays while retaining nested text and byte content."""
    def display(item, active):
        kind = type(item)
        if isinstance(item, (str, bytes, bytearray)):
            return repr(item)
        if kind not in (list, tuple, dict, set, frozenset):
            return re.sub(r" at 0x[0-9A-Fa-f]+", "", repr(item))
        if id(item) in active:
            return '{...}' if kind is dict else '[...]' if kind is list else '(...)'
        active.add(id(item))
        try:
            if kind is dict:
                return '{' + ', '.join(display(key, active) + ': ' + display(part, active)
                                       for key, part in item.items()) + '}'
            parts = ', '.join(display(part, active) for part in item)
            if kind is list:
                return '[' + parts + ']'
            if kind is tuple:
                return '(' + parts + (',' if len(item) == 1 else '') + ')'
            if kind is set:
                return '{' + parts + '}' if item else 'set()'
            return 'frozenset({' + parts + '})' if item else 'frozenset()'
        finally:
            active.remove(id(item))

    try:
        text = display(value, set())
    except BaseException as exc:
        if raised_during_setup(exc) and execution_control(exc):
            raise
        if execution_control(exc):
            raise
        text = "<a value whose repr raised " + setup_call("exception_description", exception_name, type(exc)) + ">"
    text = ''.join(ch if ch.isprintable() else json.dumps(ch, ensure_ascii=True)[1:-1] for ch in text)
    text = text.replace(';', r'\u003b').replace('G6_E_', r'\u00476_E_')
    return text if len(text) <= limit else text[:limit - 3] + "..."


SHOWN_HELPER = inspect.getsource(shown_value).replace('def shown_value(', 'def _shown(', 1)


def _fixed_source(source: str) -> str:
    """Emit fixed code without carrying its comments or documentation into reproductions."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and type(first.value.value) is str:
                node.body.pop(0)
                if not node.body and not isinstance(node, ast.Module):
                    node.body.append(ast.Pass())
    return ast.unparse(tree) + '\n'


BOOTSTRAP = (CODE_CARRIER_HELPERS + inspect.getsource(observable_exception) + '\n' + inspect.getsource(_fingerprint) + '\n'
             + inspect.getsource(raised_during_setup) + '\n' + inspect.getsource(execution_control) + '\n'
             + inspect.getsource(setup_outcome) + '\n'
             + inspect.getsource(exception_name) + '\n'
             + inspect.getsource(exception_matches) + '\n' + inspect.getsource(exception_detail) + '\n'
             + inspect.getsource(exception_message) + '\n'
             + inspect.getsource(_exception_observation) + '\n' + inspect.getsource(CheckSetupError) + '\n'
             + inspect.getsource(ObservationUnavailable) + '\n'
             + inspect.getsource(path_key) + '\n' + inspect.getsource(path_inside) + '\n'
             + inspect.getsource(ownership_location) + '\n' + inspect.getsource(find_owned) + '\n'
             + inspect.getsource(own_import_absent) + '\n'
             + inspect.getsource(track_imports) + '\n'
             + inspect.getsource(setup_call) + '\n' + inspect.getsource(runtime_method) + '\n'
             + inspect.getsource(call_absent) + '\n'
             + inspect.getsource(target_attribute) + '\n'
             + inspect.getsource(bind_method) + '\n'
             + inspect.getsource(_import_roots)) + r'''
import ast, builtins, collections.abc, concurrent.futures, copy, dis, hashlib, importlib, importlib.machinery, inspect, json, os, random, re, struct, subprocess, sys, time
_SETUP_CODE_CARRIERS = ()
_SETUP_CONTROLS = ()
_IMPORT_OPS = (dis.opmap['IMPORT_NAME'], dis.opmap['IMPORT_FROM'])

def unavailable_import(exc):
    # A fallback's exception is an outcome even inside its handler. Its caught import remains a comparison mark.
    if not exception_matches(exc, ImportError):
        return False
    if any(exc is missing for missing in _missing_imports):
        return True
    if own_import_absent(exc, ()):
        return False
    # IMPORT_FROM can fail after __import__ returned. Require a failed import
    # recorded for this module's requested child as well as the actual boundary.
    trace = BaseException.__dict__['__traceback__'].__get__(exc)
    while trace is not None and trace.tb_next is not None:
        trace = trace.tb_next
    name = ImportError.__dict__['name'].__get__(exc)
    return (trace is not None and trace.tb_frame.f_code.co_code[trace.tb_lasti] == dis.opmap['IMPORT_FROM']
            and type(name) is str and any(
                type(ImportError.__dict__['name'].__get__(failed)) is str
                and ImportError.__dict__['name'].__get__(failed).startswith(name + '.')
                for failed in _failed_import_finds))

def import_mark():
    names = sorted(_missing_import_requests)
    if not names:
        return {}
    details, seen, pending = set(), set(), list(_missing_imports)
    while pending and len(seen) < 1024:
        exc = pending.pop()
        if id(exc) in seen:
            continue
        seen.add(id(exc))
        if exception_matches(exc, ImportError):
            name = ImportError.__dict__['name'].__get__(exc)
            if (type(name) is str and len(name) <= 200 and 'G6_E_' not in name
                    and all(part.isidentifier() for part in name.split('.'))):
                details.add(name)
        pending.extend(one for one in (BaseException.__dict__['__cause__'].__get__(exc),
                                       BaseException.__dict__['__context__'].__get__(exc)) if one is not None)
    return {'missing_import': ObservationUnavailable.DETAIL, 'missing_imports': names,
            'missing_import_details': sorted(details), 'import_requests': sorted(_missing_request_identities)}
def _name(kind):
    return setup_call("exception_description", exception_name, kind)

''' + SHOWN_HELPER + r'''

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
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": _name(type(value)), "bytes": bytes(value).hex()}
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
        return {"type": _name(type(value)), "repr": _shown(value, sys.maxsize)}
    return {"type": _name(type(value)), "attributes": {key: _plain(item, depth + 1) for key, item in found.items()}}

def _bounded(value):
    text = json.dumps(value, sort_keys=True)
    if len(text) <= 500:
        return value
    return {"sha256": hashlib.sha256(bytes(text, "utf-8")).hexdigest(), "start": text[:200]}
root, channel, raw = sys.argv[1:]
if raw == '@channel':
    with open(channel, 'rb') as request_channel:
        raw = request_channel.read(1048577).decode('utf-8')
    if len(raw.encode('utf-8')) > 1048576:
        raise ValueError('request too large')
    with open(channel, 'wb'):
        pass
data = json.loads(raw)
end = time.monotonic() + data.pop('budget_s', 10)
# A new interpreter must replay the original PRNG startup, not redraw module-level choices. Capture before
# repository loading; state after a marked call (including any reseeding) is never copied to a descendant.
random_start = data.pop('_startup_random', None)
if random_start is not None:
    random.setstate((random_start[0], tuple(random_start[1]), random_start[2]))
random_start = random.getstate()
# Descendants inherit the current OS container/job and its existing resource/deadline limits. Capture launch
# state before repository loading: module eviction cannot undo environment, cwd, path or library mutations.
startup = (sys.executable, os.path.abspath(__file__), os.path.abspath(root), os.path.abspath(channel),
           os.getcwd(), dict(os.environ))
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
        if resolved is None or path_key(resolved) != path_key(wanted):
            raise CheckSetupError("import_or_binding")
    found = setup_call('module_import', importlib.import_module, module)
    where = os.path.realpath(getattr(found, "__file__", ""))
    if path_key(where) != path_key(wanted):
        raise CheckSetupError("import_or_binding")
    target = bind_method(found, spec, load) if kind in ("method", "state_method") else target_attribute(found, attribute)
    if not callable(target):
        raise CheckSetupError("binding: target is not callable")
    return target
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
        if kind_of(where) is not str or path_key(where) != path_key(wanted):
            return None
        found = held(namespace, spec[2])
        return found if observable_exception(found, resolved=True) else None
    except Exception:
        return None
def resolved():
    global import_error_types
    import_error_types = {}
    found = {}
    for spec in data["errors"]:
        key = dumps(spec, sort_keys=True)
        error = fixed[key] if key in fixed else named(spec)
        alive.append(error)
        found[key] = None if error is None else identity(error)
        if error is not None and any(base is ImportError for base in type.__dict__['__mro__'].__get__(error)):
            import_error_types[key] = identity(error)
    return found

def setup_failed(exc, *, constructor=False):
    # Ordinary probe setup stays unavailable; a constructor signal keeps its setup outcome.
    if data['probe'] and not constructor and type(exc) is not ObservationUnavailable:
        exc = ObservationUnavailable(detail=ObservationUnavailable.NO_OBSERVATION)
    version = 'observation_unavailable.v1' if type(exc) is ObservationUnavailable else 'check_setup.v1'
    return {"version": version, "detail": exception_detail(exc, "setup")}

def call_failed(exc):
    try:
        detail = exception_detail(exc, 'call failed')
    except CheckSetupError as unavailable:
        return setup_failed(unavailable)
    if exception_matches(exc, ImportError) and any(exc is missing for missing in _missing_imports):
        # Keep the fixed protocol vocabulary even for a repository's ImportError subclass.
        kind = ModuleNotFoundError if exception_matches(exc, ModuleNotFoundError) else ImportError
        detail = 'call failed: ' + exception_name(kind, qualified=True)
    # A genuinely absent held export stays the code's failure. A marked outer import must retain its
    # escaping dependency failure, even when the inner name happens to be owned.
    return {'version': 'call_failure.v1', 'detail': detail,
            **(import_mark() if exception_matches(exc, AttributeError)
               or any(exc is missing for missing in _missing_imports) else {})}

def observe(request):
    global data, _missing_preparation_imports, _missing_imports, _missing_import_requests
    global _missing_request_identities, _failed_import_finds
    global errors, outcomes, alive
    data = request
    _missing_preparation_imports, _missing_imports, _missing_import_requests = set(), [], set()
    _missing_request_identities, _failed_import_finds = set(), []
    errors, outcomes, alive = {}, [], []
    def repository_spec(name):
        # A preloaded library can shadow a held entry point (including importlib.py). Resolve what the
        # current import roots would load without consulting sys.modules, then decide by that spec's paths.
        locations = sys.path
        parts = name.split('.')
        for end in range(1, len(parts) + 1):
            spec = importlib.machinery.PathFinder.find_spec('.'.join(parts[:end]), locations)
            if spec is None or not any(type(path) is str and os.path.isabs(path) and path_inside(root, path)
                                       for path in (spec.origin, *(spec.submodule_search_locations or ()))):
                return None
            locations = spec.submodule_search_locations
            if end < len(parts) and locations is None:
                return None
        return spec

    # Reload every repository module, including helpers whose mutable globals a previous call changed.
    for name, loaded in list(sys.modules.items()):
        space = namespace_of(loaded) if type(loaded) is type(sys) else {}
        spec = held(space, '__spec__')
        locations = getattr(spec, 'submodule_search_locations', None) or ()
        paths = (held(space, '__file__'), getattr(spec, 'origin', None),
                 getattr(getattr(spec, 'loader', None), 'archive', None), *locations)
        if any(type(where) is str and os.path.isabs(where) and path_key(where) == path_key(__file__)
               for where in paths):
            continue
        if (any(type(where) is str and os.path.isabs(where) and path_inside(root, where) for where in paths)
                or repository_spec(name) is not None):
            del sys.modules[name]
    return observe_calls()

def observe_calls():
    global errors, outcomes, alive
    global _missing_imports, _missing_import_requests
    global _missing_request_identities, _failed_import_finds
    try:
        function = load(data["function"])
    except CheckSetupError as exc:
        return setup_failed(exc)
    except (AttributeError, ImportError) as exc:
        return call_failed(exc)
    except BaseException as exc:
        if execution_control(exc) or (raised_during_setup(exc) and exception_matches(exc, CheckSetupError)):
            raise
        preparation = setup_outcome(exc)
        if preparation is not None:
            return setup_failed(preparation, constructor=raised_during_setup(exc, 'constructor'))
        raise
    errors, outcomes, alive = {}, [], []
    # Preparation affects every case; imports attempted by one call belong to that case alone.
    preparation_imports = list(_missing_imports)
    preparation_requests = set(_missing_import_requests)
    preparation_identities = set(_missing_request_identities)
    preparation_finds = list(_failed_import_finds)
    for call in data["calls"]:
        _missing_imports = list(preparation_imports)
        _missing_import_requests = set(preparation_requests)
        _missing_request_identities = set(preparation_identities)
        _failed_import_finds = list(preparation_finds)
        args, kwargs = copy.deepcopy(call["args"]), copy.deepcopy(call.get("kwargs", {}))
        # R1'-W retains relevant finds from loading, setup and earlier calls, including cached failures.
        try:
            result = function(*args, **kwargs)
        except CheckSetupError as exc:
            return setup_failed(exc)
        except BaseException as exc:
            if ((execution_control(exc) and (raised_during_setup(exc) or not exception_matches(exc, KeyboardInterrupt)))
                    or (raised_during_setup(exc) and exception_matches(exc, CheckSetupError))):
                raise
            call_finds = tuple(_run_failed_finds)
            preparation = setup_outcome(exc)
            if preparation is not None:
                return setup_failed(preparation, constructor=raised_during_setup(exc, 'constructor'))
            if call_absent(exc):
                return call_failed(exc)
            errors = resolved()
            unavailable = None
            if unavailable_import(exc):
                unavailable = ObservationUnavailable(type(exc))
            alive.append(exc)
            outcomes.append(_exception_observation(exc, errors, data['probe']))
            if unavailable is not None:
                # An import that did not run cannot establish even a confirmed raises-ImportError outcome.
                outcomes[-1]['unavailable_import'] = exception_detail(unavailable, 'setup')
        else:
            call_finds = tuple(_run_failed_finds)
            errors = resolved()
            one = {"returned": encode(result), "arguments": [encode(arg) for arg in args]}
            if data["probe"]:
                one["probe"] = ({"raised": None} if data["mode"] == "exceptions" else
                                {"returned": _bounded(_plain(result))})
            outcomes.append(one)
        # Retain shared preparation and this call's imports without marking independent cases.
        outcomes[-1].update(import_mark())
        names = sorted({name for name, owned in call_finds if not owned})
        if names and 'raised' in outcomes[-1]:
            matches = {key: names for key, token in import_error_types.items() if token in outcomes[-1]['raised']}
            if matches:
                outcomes[-1]['import_error_finds'] = matches
    return {"version": "observation.v2", "outcomes": outcomes, "errors": errors}

def publish(value):
    payload = dumps(value, sort_keys=True, allow_nan=False).encode('utf-8')
    if len(payload) > 1048576:
        raise RuntimeError('observation too large')
    sink.seek(0)
    sink.truncate()
    write(b'G6R1\n' + pack('>I', len(payload)) + payload)

def fresh_observation(request, index):
    executable, boot, tree, original, cwd, environment = startup
    channel = original + '.rerun-' + str(index)
    remaining = end - time.monotonic()
    if remaining <= 0:
        return None
    try:
        with open(channel, 'xb') as handle:
            handle.write(dumps({**request, 'calls': [request['calls'][index]],
                                'budget_s': remaining, '_startup_random': random_start}, sort_keys=True).encode('utf-8'))
        ran = subprocess.run([executable, '-I', '-S', '-B', '-X', 'utf8', boot, tree, channel, '@channel'],
                             cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, timeout=max(0.001, end - time.monotonic()))
        with open(channel, 'rb') as handle:
            raw = handle.read(1048586)
        if ran.returncode != 0 or raw[:5] != b'G6R1\n' or len(raw) < 9:
            return None
        if len(raw) != 9 + struct.unpack('>I', raw[5:9])[0] or len(raw) > 1048585:
            return None
        return json.loads(raw[9:])
    except (OSError, ValueError):
        return None
    except Exception as exc:
        # The handler inventory resolves built-in exception names. Keep the library type in this predicate.
        if not isinstance(exc, subprocess.TimeoutExpired):
            raise
        return None

request_data = data
observed = observe(request_data)
if observed['version'] == 'observation.v2':
    prior_unavailable = False
    reruns = []
    for index, outcome in enumerate(observed['outcomes']):
        if prior_unavailable:
            # Expectations stay in the host; only a new interpreter can supply an independent candidate.
            rerun = {'case': index, 'ran': False, 'observation': None}
            reruns.append(rerun)
            observed['reruns'] = reruns
            if time.monotonic() < end:
                rerun['ran'] = True
                # If the bounded child expires here, the channel still says this rerun is unavailable.
                publish(observed)
                rerun['observation'] = fresh_observation(request_data, index)
        prior_unavailable = prior_unavailable or 'missing_import' in outcome or 'unavailable_import' in outcome
publish(observed)
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
    def valid_request(identity):
        if type(identity) is not str or len(identity) > 4096 or 'G6_E_' in identity:
            return False
        parts = identity.split(':')
        if (len(parts) not in (2, 3) or parts[0] not in ('import', 'reload', 'attribute')
                or not all(part.isidentifier() for part in parts[1].split('.'))):
            return False
        if parts[0] == 'reload':
            return len(parts) == 2
        if parts[0] == 'attribute':
            return len(parts) == 3 and parts[2].isidentifier()
        return len(parts) == 2 or all(name == '*' or all(part.isidentifier() for part in name.split('.'))
                                      for name in parts[2].split(','))

    def valid_marks(one):
        names = one.get('missing_imports')
        details = one.get('missing_import_details', [])
        requests = one.get('import_requests', [])
        return (type(requests) is list and len(requests) <= 1024 and all(valid_request(one) for one in requests)
                and requests == sorted(set(requests))
                and type(details) is list and len(details) <= 1024
                and all(type(name) is str and len(name) <= 200 and 'G6_E_' not in name
                        and all(part.isidentifier() for part in name.split('.')) for name in details)
                and details == sorted(set(details))
                and type(names) is list and bool(names) and len(names) <= 1024
                and all(type(name) is str and len(name) <= 200 and 'G6_E_' not in name
                        and all(part.isidentifier() for part in name.split('.')) for name in names)
                and names == sorted(set(names)) and one.get('missing_import') == ObservationUnavailable.DETAIL)

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
            marked = {'missing_import', 'missing_imports'}
            return value if (set(value) in ({'version', 'detail'}, {'version', 'detail', *marked},
                                           {'version', 'detail', *marked, 'missing_import_details'},
                                           {'version', 'detail', *marked, 'import_requests'},
                                           {'version', 'detail', *marked, 'missing_import_details', 'import_requests'})
                             and (not marked.intersection(value) or valid_marks(value))
                             and type(value['detail']) is str
                             and value['detail'] in CheckSetupError.CALL_FAILURES) else None
        if (not isinstance(value, dict) or set(value) not in ({"version", "outcomes", "errors"},
                                                            {"version", "outcomes", "errors", "reruns"})
                or value["version"] not in {"observation.v1", "observation.v2"}
                or not isinstance(value["outcomes"], list)
                or not isinstance(value["errors"], dict)
                # observation.v2: null says the call loaded no exception type of that name (it is never imported).
                or any(type(v) is not int and (v is not None or value["version"] != "observation.v2")
                       for v in value["errors"].values())):
            return None
        if 'reruns' in value:
            reruns = value['reruns']
            if value['version'] != 'observation.v2' or type(reruns) is not list or len(reruns) > len(value['outcomes']):
                return None
            seen = set()
            for rerun in reruns:
                if (type(rerun) is not dict or set(rerun) != {'case', 'ran', 'observation'}
                        or type(rerun['case']) is not int or not 0 <= rerun['case'] < len(value['outcomes'])
                        or rerun['case'] in seen or type(rerun['ran']) is not bool):
                    return None
                seen.add(rerun['case'])
                fresh = rerun['observation']
                if fresh is not None:
                    if not rerun['ran'] or type(fresh) is not dict or 'reruns' in fresh:
                        return None
                    raw_fresh = bytes(_encoded(fresh), 'utf-8')
                    if read_frame(MAGIC + struct.pack('>I', len(raw_fresh)) + raw_fresh) is None:
                        return None
                    if fresh['version'] in ('observation.v1', 'observation.v2') and len(fresh['outcomes']) != 1:
                        return None
        for one in value["outcomes"]:
            if not isinstance(one, dict):
                return None
            fields = set(one) - {"probe"}
            if 'missing_import' in fields:
                if value['version'] != 'observation.v2' or one['missing_import'] != ObservationUnavailable.DETAIL:
                    return None
                fields.remove('missing_import')
            if 'missing_imports' in fields:
                if not valid_marks(one):
                    return None
                fields.remove('missing_imports')
            if 'missing_import_details' in fields:
                if not valid_marks(one):
                    return None
                fields.remove('missing_import_details')
            if 'import_requests' in fields:
                if not valid_marks(one):
                    return None
                fields.remove('import_requests')
            if 'unavailable_import' in fields:
                if (value['version'] != 'observation.v2' or 'raised' not in fields
                        or not ObservationUnavailable.valid_detail(one['unavailable_import'])
                        or not one['unavailable_import'].startswith(ObservationUnavailable.DETAIL + ': ')):
                    return None
                fields.remove('unavailable_import')
            if 'import_error_finds' in fields:
                finds = one['import_error_finds']
                if (value['version'] != 'observation.v2' or 'raised' not in fields
                        or type(finds) is not dict or not finds or len(finds) > len(value['errors'])
                        or any(key not in value['errors'] or type(value['errors'][key]) is not int
                               or value['errors'][key] not in one['raised']
                               or not valid_marks({'missing_import': ObservationUnavailable.DETAIL,
                                                   'missing_imports': names})
                               for key, names in finds.items())):
                    return None
                fields.remove('import_error_finds')
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
    observed = read_frame(raw)
    calls = data.get("cases", data.get("calls", []))
    reruns, uncertain = [], set()

    if code != 0 and not (observed and observed.get('reruns')):
        # Only a completed original observation published before a rerun can survive a stopped child.
        observed = None

    def marked(found, outcomes, failures):
        marks = [one.get('missing_imports', []) for one in outcomes]
        unavailable = [index for index, one in enumerate(outcomes) if 'unavailable_import' in one]
        dependent = sorted(uncertain.intersection(failures))
        affected = (any('missing_import' in outcomes[index] for index in failures)
                    or bool(unavailable) or bool(dependent))
        found = {**found, **({'case_reruns': reruns} if reruns else {})}
        if not any('missing_import' in one for one in outcomes) and not unavailable and not dependent:
            return found
        # Retain the provisional comparison without granting it authority in a single run. The host can use it
        # only with matching per-outcome evidence from the other tree. Legacy marks have no comparable names.
        evidence = {'outcomes': marks, 'failures': failures, 'unavailable': unavailable, 'dependent': dependent,
                    'details': [one.get('missing_import_details', []) for one in outcomes],
                    **({'requests': [one.get('import_requests', one.get('missing_imports', [])) for one in outcomes]}
                       if any('import_requests' in one for one in outcomes) else {}),
                    'reading': {key: value for key, value in found.items() if key != 'observed'}}
        if affected:
            detail = (outcomes[unavailable[0]]['unavailable_import'] if unavailable else ObservationUnavailable.DETAIL)
            return {'result': 'error', 'detail': detail,
                    'reason_code': 'G6_E_OBSERVATION_UNAVAILABLE',
                    **({'owned_probe': True} if found.get('owned_probe') else {}),
                    **({'case_reruns': reruns} if reruns else {}), 'import_evidence': evidence}
        names = sorted({name for group in marks for name in group})
        return {**found, 'import_evidence': evidence,
                **({'fallback_observation': {'missing_imports': names, 'satisfied_import_path': 'not observed'},
                    'detail': found['detail'] + '; the fallback was taken; the path with the missing imports '
                      'satisfied was not observed'} if found['result'] == 'passed' and names else {})}

    if observed is not None and observed["version"] == "check_setup.v1":
        return {"result": "error", "detail": observed["detail"], 'reason_code': 'G6_E_CHECK_SETUP'}
    if observed is not None and observed['version'] == 'observation_unavailable.v1':
        return {'result': 'error', 'detail': observed['detail'], 'reason_code': 'G6_E_OBSERVATION_UNAVAILABLE'}
    if observed is not None and observed['version'] == 'call_failure.v1':
        outcome = observed
        if 'missing_import' in observed and observed['detail'] in (
                'call failed: builtins.ImportError', 'call failed: builtins.ModuleNotFoundError'):
            # The fixed call-failure vocabulary retains the escaping type; matching marks cannot admit it.
            outcome = {**observed, 'unavailable_import': ObservationUnavailable.DETAIL + ': '
                       + observed['detail'].removeprefix('call failed: ')}
        return marked({'result': 'failed', 'detail': observed['detail']}, [outcome] * max(1, len(calls)),
                      list(range(max(1, len(calls)))))
    if observed is None or len(observed["outcomes"]) != len(calls) or not calls:
        return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: no single completed observation"}
    expected_errors = {_encoded(spec) for spec in data.get("errors", [
        case["error"] for case in calls if case.get("expect") == "raises"])}
    if set(observed["errors"]) != expected_errors or any(
            "returned" in one and len(one["arguments"]) != len(call.get("args", []))
            for call, one in zip(calls, observed["outcomes"], strict=True)):
        return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: observation does not match the scenario"}
    prior_unavailable = False
    fresh_results = {}
    fresh_by_case = {one['case']: one for one in observed.get('reruns', [])}
    for index, outcome in enumerate(observed['outcomes']):
        if prior_unavailable:
            fresh = fresh_by_case.get(index, {'ran': False, 'observation': None})
            record = fresh['observation']
            fresh_outcomes = record.get('outcomes', []) if record else []
            names = sorted({name for one in fresh_outcomes for name in one.get('missing_imports', [])}
                           | set(record.get('missing_imports', []) if record else []))
            clean = (record is not None and record['version'] == 'observation.v2' and len(fresh_outcomes) == 1
                     and not any('missing_import' in one or 'unavailable_import' in one for one in fresh_outcomes)
                     and set(record['errors']) == expected_errors)
            # Exception identity tokens are local to one load. Compare value digests, effects and type names,
            # then let the ordinary host comparator read the fresh case against its own expected error tokens.
            keys = ('returned', 'arguments', 'probe', 'raised_name')
            same = clean and {k: outcome[k] for k in keys if k in outcome} == {
                k: fresh_outcomes[0][k] for k in keys if k in fresh_outcomes[0]}
            if same:
                one_data = {**data, ('cases' if 'cases' in data else 'calls'): [calls[index]],
                            'errors': data.get('errors', [case['error'] for case in calls
                                                         if case.get('expect') == 'raises'])}
                raw_fresh = bytes(_encoded(record), 'utf-8')
                reading = finish(0, MAGIC + struct.pack('>I', len(raw_fresh)) + raw_fresh, one_data)
                same = reading['result'] in ('passed', 'failed')
                fresh_results[index] = reading['result']
            result = 'reproduced' if same else 'not_reproduced' if clean else 'unavailable'
            reruns.append({'case': index, 'ran': fresh['ran'], 'result': result, 'outcome': record, 'marks': names})
            if not same:
                uncertain.add(index)
        prior_unavailable = prior_unavailable or 'missing_import' in outcome or 'unavailable_import' in outcome
    if "cases" not in data:
        if any("probe" not in one for one in observed["outcomes"]):
            return {"result": "error", "detail": "G6_E_OBSERVATION_UNAVAILABLE: missing probe observation"}
        return marked({"result": "passed", "detail": "G6 observed the calls in its isolated runner", "observed": observed,
                "owned_probe": True, "observations": [{"case": index, "input": call, "output": one["probe"]}
                    for index, (call, one) in enumerate(zip(calls, observed["outcomes"], strict=True))]},
                      observed['outcomes'], list(range(len(calls))))
    # Only a matching ImportError-family expectation can consume the full find
    # window. Apply this after the existing independence decisions so this one
    # confirmation rule does not alter another call's failure or rerun policy.
    for case, outcome in zip(calls, observed['outcomes'], strict=True):
        names = outcome.get('import_error_finds', {}).get(_encoded(case.get('error')))
        if case.get('expect') == 'raises' and names and 'unavailable_import' not in outcome:
            outcome.update(unavailable_import=ObservationUnavailable.DETAIL + ': builtins.ImportError',
                           missing_import=ObservationUnavailable.DETAIL,
                           missing_imports=sorted(set(outcome.get('missing_imports', ())) | set(names)),
                           missing_import_details=sorted(set(outcome.get('missing_import_details', ())) | set(names)))
    failures = []
    for index, (case, outcome) in enumerate(zip(calls, observed["outcomes"], strict=True)):
        expected = case["expect"]
        if 'unavailable_import' in outcome:
            failures.append(index)
            continue
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
            if index in fresh_results and fresh_results[index] != 'failed':
                uncertain.add(index)
                next(one for one in reruns if one['case'] == index)['result'] = 'not_reproduced'
    return marked({"result": "failed" if failures else "passed", "observed": observed,
            "detail": ("the confirmed outcome did not match the observed call(s): " + str(failures)) if failures
                      else "the confirmed outcome matched what the entry point returned or raised in G6's runner"},
                  observed['outcomes'], failures)


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
        if resolved is None or len(spec) >= 4 and path_key(copy / resolved) != path_key(copy / spec[3]):
            raise ValueError("G6_E_OBSERVATION_UNAVAILABLE: the copied entry point does not match")
        spec[:] = [*spec[:3], resolved, *spec[4:]]
    return data


def request(data: dict, *, budget_s: float = 10) -> dict:
    """Only setup and inputs reach the child. The chosen outcome stays in G6's process."""
    calls = data.get("cases", data.get("calls", []))
    ownership = {'own_module_files': data['own_module_files']} if 'own_module_files' in data else {}
    if len(_encoded(ownership)) > 8192:
        # Lossless indices into names already in the request, grouped by decoded copy root and file kind.
        indices = {name: index for index, name in enumerate(data.get('own_modules', ()))}
        layout, remaining = {}, {}
        for name, paths in ownership['own_module_files'].items():
            for path in paths:
                for kind, suffix in enumerate(('.py', '/__init__.py')):
                    stem = name.replace('.', '/') + suffix
                    if name in indices and path.endswith(stem):
                        masks = layout.setdefault(path[:-len(stem)], [0, 0])
                        masks[kind] |= 1 << indices[name]
                        break
                else:
                    remaining.setdefault(name, []).append(path)
        ownership = {'own_module_layout': [[prefix, [format(bits, 'x') for bits in masks]]
                                         for prefix, masks in sorted(layout.items())]}
        if remaining:
            ownership['own_module_files'] = remaining
    return {"function": data["function"], "calls": [{"args": c["args"], "kwargs": c.get("kwargs", {})} for c in calls],
            "errors": data.get("errors", [case["error"] for case in calls if case.get("expect") == "raises"]),
            "probe": "cases" not in data, "mode": data.get("mode", "outputs"), 'budget_s': budget_s,
            **({'own_modules': data['own_modules']} if 'own_modules' in data else {}),
            **({'own_module_paths': data['own_module_paths']} if 'own_module_paths' in data else {}),
            **ownership,
            **({'import_roots': data['import_roots']} if 'import_roots' in data else {})}


def _portable_source(function) -> str:
    """Keep helper semantics while omitting documentation and compacting fixed-code indentation."""
    return '\n'.join(' ' * ((len(line) - len(line.lstrip(' '))) // 4) + line.lstrip(' ')
                     for line in _fixed_source(inspect.getsource(function)).splitlines())


def example_text(slot: str) -> str:
    """Portable examples use the same fresh interpreter and comparison as product enforcement.

    Pytest is only a convenience for displaying a reproduction. Product evidence comes from ``run`` and its
    host-owned result channel, never from collection, plugins or the wrapper's pytest status.
    """
    bootstrap = base64.b85encode(zlib.compress(
        (confine.CHILD_PRELUDE + _fixed_source(BOOTSTRAP)).encode('utf-8'), 9)).decode('ascii')
    carriers = base64.b85encode(zlib.compress(CODE_CARRIER_HELPERS.encode('utf-8'), 9)).decode('ascii')
    preparation = '\n\n'.join(_portable_source(one) for one in (
        CheckSetupError, ObservationUnavailable, raised_during_setup, execution_control,
        exception_name, exception_matches, exception_detail, exception_message,
        setup_call, call_absent, register_setup_error,
        _unique, _encoded, _fingerprint, _valid_encoding, _matches_encoding,
        _import_roots, path_key, path_inside, ownership_location, located, request, own_import_absent,
        setup_outcome, read_frame, finish))
    packed = base64.b85encode(zlib.compress(preparation.encode('utf-8'), 9)).decode('ascii')
    return ("import base64, zlib, concurrent.futures, hashlib, importlib.machinery, json, struct, subprocess, tempfile, sys, os\nfrom pathlib import Path\n"
            "TEMPLATE = 'example.v1'\nBINDINGS = " + slot
            + "\nMAGIC = b'G6R1\\n'\nMAX_FRAME = 1048576\nBOOTSTRAP = "
            + 'str(zlib.decompress(base64.b85decode(' + ascii(bootstrap) + ")), 'utf-8')\n"
            + 'exec(zlib.decompress(base64.b85decode(' + ascii(carriers) + ')))\n'
            + '\nexec(zlib.decompress(base64.b85decode(' + ascii(packed) + ')))\n' + '''
register_setup_error(TEMPLATE)
def _check_example():
    data = located(Path.cwd(), BINDINGS)
    with tempfile.TemporaryDirectory() as own:
        channel = os.path.join(own, 'result.bin')
        boot = os.path.join(own, 'bootstrap.py')
        with open(boot, 'w', encoding='utf-8', newline='\\n') as handle:
            handle.write(BOOTSTRAP)
        with open(channel, 'xb') as handle:
            handle.write(bytes(_encoded(request(data)), 'utf-8'))
        ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', boot,
                              os.getcwd(), channel, '@channel'], capture_output=True, timeout=10)
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
                or not parts or ".." in parts or not path_inside(tree, target) or os.name == "nt" and ":" in rel):
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
    """One confined observation G6 starts, in a folder G6 writes from the copy's manifest (``_made``), and one
    channel G6 creates before starting it. The folder is read again after the child ends: its names and bytes by the
    ordinary executor's comparison, and what a path in it carries beyond them (``_beyond``). Independent-case
    interpreters are descendants inside this same confinement and bounded process tree, with the time left over.
    """
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
        program = data.get("_program", confine.CHILD_PRELUDE + _fixed_source(BOOTSTRAP))
        boot.write_text(program, encoding="utf-8", newline="\n")
        with channel.open("xb") as handle:
            handle.write(_encoded(request(data, budget_s=remaining)).encode('utf-8'))
        plan = confine.plan(confine.Confinement(read_write=(str(own),),
                            read_only=confine.interpreter_paths(python), network=False), python)
        # the observation record is G6's data, read back by its exact-type schema (repo_tests.read_observation): a
        # text marked for the agent is a str subclass that schema refuses, so it is marked where a reply is built
        isolation = plan.record()
        if confine_refused(plan) or not plan.backend:
            plan.release()
            raise rt.RepoRefused(ISOLATION_UNAVAILABLE, confine_refusal(plan))
        try:
            # Both program forms read transient host ownership from the same request channel.
            payload = '@channel'
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
