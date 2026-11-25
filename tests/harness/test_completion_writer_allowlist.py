# SPDX-License-Identifier: Apache-2.0
"""Static completion writer inventory with explicit collaborator operations."""
import ast
from pathlib import Path

import pytest


READONLY = set('absolute as_posix exists expanduser glob is_absolute is_dir is_file is_relative_to is_symlink '
               'iterdir joinpath lstat read_bytes read_text relative_to resolve rglob stat with_name with_suffix'.split())
VALUES = set('add append clear count decode encode endswith extend format fromkeys fullmatch get get_grouped_opcodes '
             'hexdigest items join keys lower lstrip match partition pop removeprefix rfind rsplit rstrip search '
             'setdefault split splitlines start startswith strip sub update values set reset'.split())
BUILTINS = set('all any bool bytes chr dict enumerate float frozenset getattr int isinstance len list map max min next '
               'object print range reversed set sorted str sum super tuple type zip ValueError SystemExit'.split())
OPERATIONS = {
    'hashlib': 'sha256', 'json': 'loads dumps', 'os': 'environ.get fspath', 're': 'compile escape finditer fullmatch match search sub',
    'sys': 'stdin.buffer.read', 'time': 'time monotonic', 'uuid': 'uuid4', 'threading': 'RLock',
    'bisect': 'bisect_left bisect_right', 'difflib': 'SequenceMatcher', 'contextvars': 'ContextVar',
    'pathlib': 'Path', 'dataclasses': 'asdict dataclass', 'contextlib': 'contextmanager',
    'g6.harness._completion_storage': ('home_path mkdir path read_task task_path task_present task_publication touch write_retained write_text '
        'append_text check_tree remove_tree replace retained_paths sync_state temporary_root unlink visible_copy write_bytes'),
    'g6.core.cloud_consent': ('Source Source.text admission bind_output boundary_sources collect_sources freeze_output '
        'input_source prompt_sources read_retained record_sources repository_source request_sources sha256 current_capture'),
    'g6.core.refusals': 'code_of current_watch watching_consent resolve',
    'g6.core.keys': 'get path', 'g6.core.selector': 'hint',
    'g6.core.host_models': 'CodexCaller _environment _folders _plain _run',
    'g6.core.consent_callers': 'guard session_admission provider_from_env',
    'g6.core.llm': '_start_model_call', 'g6.core.model_calls': 'counting', 'g6.core.ledger': 'digest',
    'g6.harness.completion_loop': ('_load _save _writing advance attempted prepare reply_for settle source_refs '
        'refusal_reason Step _held read_report'),
    'g6.harness.completion_hook': 'folder',
    'g6.workforce.repo_tests': ('_git checked_repo _base_revision _bytes_line _cut_lines _decoded _normal _paths '
        'change diff_read diff_text now_line read_phases read_report repository_copy shown_name snapshot RepoRefused'),
    'g6.harness.verification_proposer': '_PLUGIN.search _backend boundary',
    'g6.harness.verification_search': 'LOADED_ELSEWHERE.format _run_check classify placements proposed_checks',
    'g6.harness.patch_report': 'code completion_report findings',
    'g6.harness.patch_review': '_loaded_elsewhere review_patch',
    'g6.workspace._coordination': 'Coordination',
    'g6.harness.workflow.policy': 'enabled', 'g6.harness.workflow.adapters': 'observe_step',
    'g6.harness.workflow.view': 'appended', 'g6.harness.workflow.obstacle': 'none_sent',
    'g6.skills.corpus': 'load', 'g6.cegis': 'InputDomain.of SearchResult cegis',
    'g6.context.redact': 'mask_paths secret_spans', 'g6.core.door': 'blocked_reply',
    'g6.core.reliability_envelope': 'envelope_fields warning_card',
    'g6.mcp.consent_controls': 'public_instructions', 'g6.workspace._envelope': 'WorkspaceRefused',
    'g6.invariants': 'ERROR_CODE_PATTERN.match',
}
IMPORTED_CALLS = {f'{module}.{name}' for module, names in OPERATIONS.items() for name in names.split()}
LOCAL_METHODS = {
    'self': {'_launch', '_reply', 'check', 'locate', 'total', 'tree'},
    'run': {'into', 'total'}, 'watch': {'mark', 'refused', 'told_since'},
    'record': {'body'}, 'report': {'full', 'wire'},
}
WRITERS = set('write_bytes write_text write_retained mkdir unlink touch replace rename rmtree copytree open mkdtemp '
              'makedirs remove rmdir write writelines symlink_to hardlink_to link symlink mkfifo chmod lchmod chown '
              'truncate ftruncate utime removedirs move copy copy2 copyfile copyfileobj mkstemp NamedTemporaryFile '
              'TemporaryDirectory TemporaryFile SpooledTemporaryFile dump fdopen flush fsync fdatasync fchmod fchown '
              'lchown chflags lchflags mknod posix_fallocate pwrite writev pwritev sendfile copy_file_range renames '
              'copymode copystat'.split())
# Reviewed data fields and type/constant references are not captured operations.
# Keep the older assignment check below as well: this cannot relax its rules.
DATA_ATTRIBUTES = set('stem readable reason action note text code name ending declared kind fix_by words argv __name__ '
    'stderr exit ran review_ask size model keep_env valid nodes withheld collection_errors id source repo base_commit '
    'modes added files refusal rel unread old new limit alike cuts rows hidden masked masked_parents sources critic '
    'answered asked rounds pending round waiting why call_ran version item_id content_sha256 settled_here active guard '
    'session state parent parents hidden_scouts scout fixer results critic_note status review_unsent refused_provider '
    'usable tail file boundary '
    'ATTEMPTED WAIT CONTINUE STOP BUSY ALREADY_RUNNING ALREADY_ENDED NO_PYTHON NEW_SESSION TASK_UNREADABLE UNREADABLE_TASK '
    'NOT_JUDGED NOT_PASSED COUNTEREXAMPLE S M NO_SERVERS MAX_CHECKS UNKNOWN RAN FAILED NO_AUTHORITY MIN_RUN_S NO_MODE '
    'REQUIRED STATE_UNAVAILABLE KEY_MISSING _RetainedState TaskUnreadable Capture ConsentClaude ClaudeCodeCaller '
    'ProposedCheck Change CopyNotRun FileRead DiffRead'.split())


class _Declarations(ast.NodeVisitor):
    """Bindings local to one scope, including bindings later in its source."""

    def __init__(self):
        self.bindings = {}
        self.import_lines = {}
        self.stars = []
        self.globals = set()
        self.nonlocals = set()
        self.values = {}
        self.loop_loads = set()

    def value(self, target, value):
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                self.values.setdefault(node.id, []).append(value)

    def visit_Assign(self, node):
        for target in node.targets:
            self.value(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.value(node.target, node.value)
        self.generic_visit(node)

    visit_AugAssign = visit_AnnAssign
    visit_NamedExpr = visit_AnnAssign

    def visit_For(self, node):
        self.value(node.target, node.iter)
        self.visit_While(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        self.loop_loads.update(child for child in ast.walk(node) if isinstance(child, ast.Name))
        self.generic_visit(node)

    def bind(self, name, origin=None):
        self.bindings.setdefault(name, set()).add(origin)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id)

    def visit_Import(self, node):
        for alias in node.names:
            name = alias.asname or alias.name.split('.')[0]
            self.bind(name, alias.name if alias.asname else alias.name.split('.')[0])
            self.import_lines[name] = min(node.lineno, self.import_lines.get(name, node.lineno))

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == '*' and (node.level or node.module != 'g6.harness._completion_storage'):
                self.stars.append(node.lineno)
            name = alias.asname or alias.name
            self.bind(name, f'{node.module}.{alias.name}' if not node.level else None)
            self.import_lines[name] = min(node.lineno, self.import_lines.get(name, node.lineno))

    def visit_Global(self, node):
        self.globals.update(node.names)

    def visit_Nonlocal(self, node):
        self.nonlocals.update(node.names)

    def visit_FunctionDef(self, node):
        self.bind(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass

    def visit_ListComp(self, node):
        # A comprehension's iteration targets are local to it; walrus targets
        # belong to the containing non-comprehension scope.
        for child in ast.walk(node):
            if isinstance(child, ast.NamedExpr):
                self.visit(child.target)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bind(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.bind(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.bind(node.rest)
        self.generic_visit(node)


class _GateFlow:
    """Gate imports established on every path reaching a load in one scope."""

    def __init__(self, nodes):
        self.before = {}
        self.final = self.block(nodes, set()) or set()

    @staticmethod
    def join(paths):
        reaching = [path for path in paths if path is not None]
        return set.intersection(*reaching) if reaching else None

    def block(self, nodes, incoming):
        state = incoming
        for node in nodes:
            outgoing = self.statement(node, state if state is not None else set())
            if state is not None:
                state = outgoing
        return state

    def statement(self, node, state):
        # Also retain the enclosing state for eager loads in nested scopes.
        # Their own bodies are analysed separately, without exporting imports.
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                self.before[child] = state
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports = _Declarations()
            imports.visit(node)
            if imports.stars:
                return set()
            return state | {name for name, origins in imports.bindings.items()
                            if any(origin and origin.startswith('g6.harness._completion_storage')
                                   for origin in origins)}
        if isinstance(node, ast.If):
            return self.join([self.block(node.body, state), self.block(node.orelse, state)])
        if isinstance(node, (ast.Try, ast.TryStar)):
            normal = self.block(node.orelse, self.block(node.body, state))
            # An exception may precede any import in the try body. No partial
            # try-body binding is guaranteed at the start of a handler.
            handled = [self.block(handler.body, state) for handler in node.handlers]
            reaching = self.join([normal, *handled])
            outgoing = self.block(node.finalbody, reaching)
            # Finally also runs on exceptional exits that do not reach the
            # following statement (including a failed import in a handler).
            self.block(node.finalbody, self.join([state, reaching]))
            return outgoing
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            # Include the first iteration and the zero-iteration path. A star
            # on a back edge can invalidate a preceding iteration's imports.
            writes = _Declarations()
            for statement in node.body:
                writes.visit(statement)
            entry = set() if writes.stars else state
            self.block(node.body, entry)
            otherwise = self.block(node.orelse, entry)
            # A possible break skips else. Nested-loop breaks conservatively
            # keep the entry path too; they never grant an extra allowance.
            breaks = any(isinstance(child, ast.Break) for statement in node.body for child in ast.walk(statement))
            return self.join([entry, otherwise]) if breaks else otherwise
        if isinstance(node, (ast.With, ast.AsyncWith)):
            # A context manager can suppress an exception before an import.
            return self.join([state, self.block(node.body, state)])
        if isinstance(node, ast.Match):
            paths = [self.block(case.body, state) for case in node.cases]
            if not any(case.guard is None and isinstance(case.pattern, ast.MatchAs)
                       and case.pattern.pattern is None for case in node.cases):
                paths.append(state)
            return self.join(paths)
        if isinstance(node, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return None
        return state


class _BindingScope:
    """Lexical import allowances; any other binding refuses every use in scope."""

    def __init__(self, kind, parent, nodes, args=None):
        declarations = _Declarations()
        for node in nodes:
            declarations.visit(node)
        if args is not None:
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
                if arg is not None:
                    declarations.bind(arg.arg)
        self.kind, self.parent = kind, parent
        self.values = declarations.values
        self.loop_loads = declarations.loop_loads
        self.import_lines = declarations.import_lines
        self.stars = declarations.stars
        self.gates = _GateFlow(nodes)
        self.globals, self.nonlocals = declarations.globals, declarations.nonlocals
        self.local = declarations.bindings.keys() - self.globals - self.nonlocals
        self.stable = {name: next(iter(origins)) if len(origins) == 1 else None
                       for name, origins in declarations.bindings.items() if name in self.local}
        # Execution order, callbacks and loop back edges give no allowance.
        # Any descendant's write to an enclosing name removes that allowance
        # throughout this scope, even if the descendant is never called.
        for node in nodes:
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    writes = _Declarations()
                    for statement in child.body:
                        writes.visit(statement)
                    for name in (writes.globals | writes.nonlocals) & writes.bindings.keys():
                        if name in self.stable:
                            self.stable[name] = None

    def origin(self, name, node, *, deferred=False):
        origin = self.stable.get(name)
        imported = self.import_lines.get(name, 0)
        if origin and origin.startswith('g6.harness._completion_storage'):
            established = self.gates.final if deferred else self.gates.before.get(node, set())
            # All reaching arms must bind the gate. Preserve the independent
            # rule that a later star invalidates its exported names.
            if name not in established or any(line >= imported and (deferred or line <= node.lineno)
                                               for line in self.stars):
                return None
            return origin
        return origin if deferred or imported <= node.lineno else None

    def lookup(self, name, node, *, deferred=False):
        if name in self.globals:
            root = self
            while root.parent is not None:
                root = root.parent
            return root.origin(name, node, deferred=deferred or self.kind == 'function')
        if name in self.nonlocals:
            outer = self.parent
            while outer is not None and (outer.kind == 'class' or name not in outer.local):
                outer = outer.parent
            return outer.origin(name, node, deferred=True) if outer is not None else None
        if name in self.local:
            origin = self.origin(name, node, deferred=deferred and self.kind == 'module')
            if (self.kind != 'class' or origin is not None or self.stable.get(name) is None
                    or node in self.loop_loads):
                return origin
            # Class loads without an established import fall through to the
            # globals, never to an enclosing class's namespace.
        outer = self.parent
        if self.kind in {'function', 'comprehension', 'class'}:
            while outer is not None and outer.kind == 'class':
                outer = outer.parent
        return outer.lookup(name, node, deferred=deferred or self.kind in {'function', 'comprehension'}) if outer else None

    def value_scope(self, name):
        if name in self.globals:
            root = self
            while root.parent is not None:
                root = root.parent
            return root
        if name in self.local:
            return self
        outer = self.parent
        while outer is not None and outer.kind == 'class':
            outer = outer.parent
        return outer.value_scope(name) if outer is not None else None


class _Bindings(ast.NodeVisitor):
    """Locate lexical scopes and their guaranteed imports, without call effects."""

    def __init__(self, tree):
        self.scope = _BindingScope('module', None, tree.body)
        self.origins = {}
        self.scopes = {}
        self.visit(tree)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.origins[node] = self.scope.lookup(node.id, node)
            self.scopes[node] = self.scope

    def block(self, nodes):
        for node in nodes:
            self.visit(node)

    def visit_FunctionDef(self, node):
        self.block(node.decorator_list)
        self.block([value for value in (*node.args.defaults, *node.args.kw_defaults) if value is not None])
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs, node.args.vararg, node.args.kwarg):
            if arg is not None and arg.annotation is not None:
                self.visit(arg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        outer = self.scope
        self.scope = _BindingScope('function', outer, node.body, node.args)
        self.defaults(node.args)
        self.block(node.body)
        self.scope = outer

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.block([value for value in (*node.args.defaults, *node.args.kw_defaults) if value is not None])
        outer = self.scope
        self.scope = _BindingScope('function', outer, [node.body], node.args)
        self.defaults(node.args)
        self.visit(node.body)
        self.scope = outer

    def visit_ClassDef(self, node):
        self.block([*node.decorator_list, *node.bases, *node.keywords])
        outer = self.scope
        self.scope = _BindingScope('class', outer, node.body)
        self.block(node.body)
        self.scope = outer

    def visit_ListComp(self, node):
        # Python evaluates the first iterable in the enclosing lexical scope.
        self.visit(node.generators[0].iter)
        outer = self.scope
        self.scope = _BindingScope('comprehension', outer, [part.target for part in node.generators])
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self.block(generator.ifs)
        self.block([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
        self.scope = outer

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def defaults(self, args):
        positional = [*args.posonlyargs, *args.args]
        for arg, value in zip(positional[len(positional) - len(args.defaults):], args.defaults):
            self.scope.values.setdefault(arg.arg, []).append(value)
        for arg, value in zip(args.kwonlyargs, args.kw_defaults):
            if value is not None:
                self.scope.values.setdefault(arg.arg, []).append(value)

    def module_value(self, node, seen=frozenset()):
        """Conservatively retain module values through simple lexical bindings.

        Containers carry their elements' possibilities; indexing cannot make a
        module inside a literal container into text. Calls normally produce
        data, rather than lending their receiver's module identity to the result.
        """
        if isinstance(node, ast.Name):
            if self.origins.get(node) is not None:
                return True
            scope = self.scopes.get(node)
            owner = scope.value_scope(node.id) if scope is not None else None
            key = (owner, node.id)
            if owner is None or key in seen:
                return False
            return any(self.module_value(value, seen | {key}) for value in owner.values.get(node.id, []))
        if isinstance(node, ast.Subscript):
            return self.module_value(node.value, seen)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return any(self.module_value(value, seen) for value in node.elts)
        if isinstance(node, ast.Dict):
            return any(self.module_value(value, seen) for value in node.values)
        if isinstance(node, ast.Attribute):
            return self.module_value(node.value, seen)
        if isinstance(node, ast.BinOp):
            return self.module_value(node.left, seen) or self.module_value(node.right, seen)
        if isinstance(node, ast.IfExp):
            return self.module_value(node.body, seen) or self.module_value(node.orelse, seen)
        if isinstance(node, ast.Call):
            return ast.unparse(node.func) in {'__import__', 'importlib.import_module'}
        return False


class _TextBindings:
    """Require string values at every binding in the module, including nested writes."""

    def __init__(self, tree):
        self.values = {}
        covered = set()

        def bind(target, value):
            if isinstance(target, ast.Name):
                self.values.setdefault(target.id, []).append(value)
                covered.add(target)
            elif isinstance(target, (ast.Tuple, ast.List)):
                paired = isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts)
                for index, part in enumerate(target.elts):
                    bind(part, value.elts[index] if paired else None)
            elif isinstance(target, ast.Starred):
                bind(target.value, None)
            elif isinstance(target, (ast.Subscript, ast.Attribute)):
                while isinstance(target, (ast.Subscript, ast.Attribute)):
                    target = target.value
                if isinstance(target, ast.Name):
                    self.values.setdefault(target.id, []).append(None)

        def named(name):
            if name:
                self.values.setdefault(name, []).append(None)

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    bind(target, node.value)
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                bind(node.target, node.value)
            elif isinstance(node, ast.AugAssign):
                # Adding strings preserves the type only if all the other
                # bindings are strings too. Every other update is unknown.
                bind(node.target, node.value if isinstance(node.op, ast.Add) else None)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                if isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)) and node.iter.elts:
                    for value in node.iter.elts:
                        bind(node.target, value)
                else:
                    bind(node.target, None)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                bind(node.optional_vars, None)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    named(alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                named(node.name)
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
                named(node.name)
            elif isinstance(node, ast.MatchMapping):
                named(node.rest)
            elif isinstance(node, ast.arguments):
                positional = [*node.posonlyargs, *node.args]
                for arg, value in zip(positional[len(positional) - len(node.defaults):], node.defaults):
                    self.values.setdefault(arg.arg, []).append(value)
                for arg, value in zip(node.kwonlyargs, node.kw_defaults):
                    if value is not None:
                        self.values.setdefault(arg.arg, []).append(value)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node not in covered:
                bind(node, None)

    def string(self, node, seen=frozenset(), indices=()):
        if isinstance(node, ast.Name):
            if node.id in seen or node.id not in self.values or '*' in self.values:
                return False
            # A named mutable container can change through an alias without
            # rebinding its name. Its initial elements do not establish str.
            if any(isinstance(value, (ast.List, ast.Dict)) for value in self.values[node.id]):
                return False
            return all(self.string(value, seen | {node.id}, indices) for value in self.values[node.id])
        if isinstance(node, ast.Subscript):
            return self.string(node.value, seen, (node.slice, *indices))
        if indices and isinstance(node, (ast.List, ast.Tuple, ast.Dict)):
            index, *rest = indices
            if isinstance(node, ast.Dict):
                if not isinstance(index, ast.Constant) or any(not isinstance(key, ast.Constant) for key in node.keys):
                    return False
                matches = [value for key, value in zip(node.keys, node.values) if key.value == index.value]
                return bool(matches) and all(self.string(value, seen, tuple(rest)) for value in matches)
            if isinstance(index, ast.Constant) and type(index.value) is int:
                if not -len(node.elts) <= index.value < len(node.elts):
                    return False
                return self.string(node.elts[index.value], seen, tuple(rest))
            if isinstance(index, ast.Slice):
                return bool(rest) and all(self.string(value, seen, tuple(rest[1:])) for value in node.elts)
            return bool(node.elts) and all(self.string(value, seen, tuple(rest)) for value in node.elts)
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str)
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.string(node.left, seen) and self.string(node.right, seen)
        if isinstance(node, ast.IfExp):
            return self.string(node.body, seen, indices) and self.string(node.orelse, seen, indices)
        if isinstance(node, ast.BoolOp):
            return all(self.string(value, seen, indices) for value in node.values)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return node.func.id == 'str' and 'str' not in self.values and '*' not in self.values
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in {'decode', 'read_text'}:
                    return True
                return (node.func.attr in {'capitalize', 'casefold', 'center', 'expandtabs', 'format', 'format_map',
                                          'join', 'ljust', 'lower', 'lstrip', 'removeprefix', 'removesuffix', 'replace',
                                          'rjust', 'rstrip', 'strip', 'swapcase', 'title', 'translate', 'upper', 'zfill'}
                        and self.string(node.func.value, seen))
        return False

    def receiver(self, node):
        root = node
        while isinstance(root, ast.Subscript):
            root = root.value
        # The committed allowance includes injected names and parameters with
        # no binding anywhere in the module. A binding in any scope ends it.
        if (isinstance(root, ast.Name) and root.id not in self.values and '*' not in self.values
                and root.id not in {'os', 'shutil', 'tempfile', 'pathlib', 'json', 'builtins'}):
            return True
        return self.string(node)


def unguarded_calls(source):
    """Admit named collaborator operations; refuse unknown or indirect calls."""
    tree = ast.parse(source)
    analysis = _Bindings(tree)
    strings = _TextBindings(tree)
    bindings = analysis.origins
    # Retain the earlier import-map refusals alongside lexical allowances.
    imports = {alias.asname or alias.name.split('.')[0]: alias.name
               for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imports.update({alias.asname or alias.name: f'{node.module}.{alias.name}'
                    for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names})
    defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    declared = (defined | imports.keys()
                | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
                | {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)})
    # These callback parameters are reviewed by name, not any unknown plain name.
    callbacks = {'host', 'scout', 'report_reader', 'make', 'say', 'mask', 'full', 'literal', 'own', 'by_row', 'numbered'}
    special = {'consent.guard(caller).complete', '_seen(rest).masked', '_seen(task).masked', '_seen(text).masked',
               'super().__init__', 'HOOKS[args[0]]'}
    data = {'g6.core.cloud_consent.WITHHELD', 'g6.harness.completion_loop.NEW_SESSION', 'uuid.uuid4().hex'}
    aliases = {target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and isinstance(node.value, ast.Attribute) and ast.unparse(node.value) == 'time.monotonic'
               for target in node.targets if isinstance(target, ast.Name)}
    found = set()
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def writer_reference(node):
        if isinstance(node, ast.Attribute):
            return node.attr in WRITERS
        if isinstance(node, ast.Name):
            origin = bindings.get(node)
            previous = imports.get(node.id)
            # A local data value named 'copy' is not a reference to shutil.copy.
            # Imported writer aliases and their capture sites are still refused.
            return (node.id == 'open' or node.id in WRITERS and node.id not in declared
                    or any(value is not None and value.rsplit('.', 1)[-1] in WRITERS
                           for value in (origin, previous)))
        return False

    def gate_writer(node):
        receiver = node
        while isinstance(receiver, ast.Attribute):
            receiver = receiver.value
        if not isinstance(receiver, ast.Name):
            return False
        origin = bindings.get(receiver)
        canonical = (origin or '') + ast.unparse(node)[len(receiver.id):]
        return canonical.startswith('g6.harness._completion_storage.') and allowed(node)

    def text_replace(node, parent):
        if not (isinstance(node, ast.Attribute) and node.attr == 'replace'
                and isinstance(parent, ast.Call) and parent.func is node and len(parent.args) >= 2):
            return False
        return strings.receiver(node.value)

    def allowed(node, *, captured=False):
        return permitted(node, captured=captured, scoped=True) and permitted(node, captured=captured, scoped=False)

    def permitted(node, *, captured, scoped):
        call = ast.unparse(node)
        if captured and call in data:
            return True
        if call in special:
            return True
        receiver = node
        while isinstance(receiver, ast.Attribute):
            receiver = receiver.value
        origin = (bindings.get(receiver) if scoped else imports.get(receiver.id)) if isinstance(receiver, ast.Name) else None
        if isinstance(receiver, ast.Name) and (origin is not None or receiver.id in {
            'os', 'shutil', 'tempfile', 'pathlib', 'json', 'builtins',
        }):
            canonical = (origin or receiver.id) + call[len(receiver.id):]
            return canonical in IMPORTED_CALLS or captured and canonical in data
        if isinstance(node, ast.Attribute):
            if node.attr in READONLY | VALUES:
                return True
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                constructor = ((bindings.get(node.value.func) if scoped else imports.get(node.value.func.id))
                               or node.value.func.id)
                if constructor == 'g6.workspace._coordination.Coordination' and node.attr == 'mutex':
                    return True
                if constructor == 'ToolSession' and node.attr == 'run':
                    return True
            return isinstance(node.value, ast.Name) and node.attr in LOCAL_METHODS.get(node.value.id, ())
        if isinstance(node, ast.Name):
            return node.id in defined | BUILTINS | callbacks | aliases
        return False

    for node in ast.walk(tree):
        parent = parents.get(node)
        callee = isinstance(parent, ast.Call) and parent.func is node
        receiver = isinstance(parent, ast.Attribute) and parent.value is node
        if not callee:
            dynamic = ({'getattr', '__import__', 'exec', 'eval', 'compile', 'importlib.import_module'}
                       | {'builtins.' + name for name in ('getattr', '__import__', 'exec', 'eval', 'compile')})
            if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(getattr(node, 'ctx', None), ast.Load):
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                origin = bindings.get(root) if isinstance(root, ast.Name) else None
                canonical = (origin + ast.unparse(node)[len(root.id):]) if origin else ast.unparse(node)
                if canonical in dynamic or ast.unparse(node) in dynamic:
                    found.add((node.lineno, ast.unparse(node)))
        if isinstance(node, ast.Attribute) and node.attr in {'__dict__', '__globals__', '__builtins__', '__self__'}:
            found.add((node.lineno, ast.unparse(node)))
        key = (node.slice if isinstance(node, ast.Subscript) else
               node.args[0] if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == 'get' and node.args else None)
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in WRITERS | {'os', '__import__'}:
            found.add((node.lineno, ast.unparse(node)))
        if (writer_reference(node) and (not callee or not gate_writer(node) and not text_replace(node, parent))):
            found.add((node.lineno, ast.unparse(node)))
        if not callee and (isinstance(node, ast.Attribute) and not receiver
                           and node.attr not in DATA_ATTRIBUTES and ast.unparse(node) != 'run.reserve'
                           and not allowed(node, captured=True)):
            found.add((node.lineno, ast.unparse(node)))
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in {'exec', 'eval', '__import__', 'open'}:
                found.add((node.lineno, name))
            elif name == 'getattr':
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant) or node.args[1].value not in {
                    'model', 'program', 'name',
                }:
                    found.add((node.lineno, name))
            elif (isinstance(node.func, ast.Attribute) and node.func.attr == 'replace' and len(node.args) >= 2
                  and not allowed(node.func)):
                if not text_replace(node.func, node):
                    found.add((node.lineno, name))
            elif not allowed(node.func):
                found.add((node.lineno, name))
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Attribute):
            if node.value.attr not in {'modes', 'old', 'text', 'session', 'ran', 'pending', 'refused_provider'}:
                if not allowed(node.value, captured=True):
                    found.add((node.lineno, ast.unparse(node.value)))
    return found


@pytest.mark.parametrize("body", [
    "shutil.move(a, b)", "os.truncate(p, 0)", "tempfile.mkstemp(dir=session)", "shutil.copyfileobj(a, b)",
    "json.dump(state, handle)", "os.utime(p)", "os.removedirs(p)", "p.chmod(0)",
    "getattr(p, 'write_text')('x')", "w = p.write_bytes\nw(b'')",
    "p.future_filesystem_method()", "from os import truncate as cut\ncut(p, 0)",
    "import shutil as fs\nfs.move(a, b)", "from pathlib import Path\nPath(p).chmod(0)",
    "os.replace(a, b)", "import os as fs\nfs.replace(a, b)", "Path(p).touch()", "open(p, 'w')",
    "os.environ.update({'x': 'y'})",
    "import shutil as store\nstore.move(a, b)",
])
def test_inventory_refuses_unlisted_and_indirect_writers(body):
    assert unguarded_calls(body), body


def test_completion_files_use_only_readonly_calls_or_the_gate():
    from g6.harness import completion_hook, completion_loop
    from g6.mcp import review_loop

    for module in (completion_hook, completion_loop, review_loop):
        assert not unguarded_calls(Path(module.__file__).read_text(encoding="utf-8")), module.__name__
