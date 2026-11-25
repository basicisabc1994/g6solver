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
        self.globals = set()
        self.nonlocals = set()

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
        self.import_lines = declarations.import_lines
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

    def lookup(self, name, lineno):
        if name in self.globals:
            root = self
            while root.parent is not None:
                root = root.parent
            return root.stable.get(name) if root.import_lines.get(name, 0) <= lineno else None
        if name in self.nonlocals:
            outer = self.parent
            while outer is not None and (outer.kind == 'class' or name not in outer.local):
                outer = outer.parent
            return (outer.stable.get(name) if outer is not None and outer.import_lines.get(name, 0) <= lineno
                    else None)
        if name in self.local:
            return self.stable.get(name) if self.import_lines.get(name, 0) <= lineno else None
        outer = self.parent
        if self.kind in {'function', 'comprehension'}:
            while outer is not None and outer.kind == 'class':
                outer = outer.parent
        return outer.lookup(name, lineno) if outer is not None else None


class _Bindings(ast.NodeVisitor):
    """Locate lexical scopes without simulating control flow or call effects."""

    def __init__(self, tree):
        self.scope = _BindingScope('module', None, tree.body)
        self.origins = {}
        self.visit(tree)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.origins[node] = self.scope.lookup(node.id, node.lineno)

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
        self.block(node.body)
        self.scope = outer

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.block([value for value in (*node.args.defaults, *node.args.kw_defaults) if value is not None])
        outer = self.scope
        self.scope = _BindingScope('function', outer, [node.body], node.args)
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


def unguarded_calls(source):
    """Admit named collaborator operations; refuse unknown or indirect calls."""
    tree = ast.parse(source)
    bindings = _Bindings(tree).origins
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
        root = node.value
        return (isinstance(root, (ast.Constant, ast.JoinedStr, ast.Subscript))
                or isinstance(root, ast.Name) and root.id in {'text', 'data'}
                or isinstance(root, ast.Call) and isinstance(root.func, ast.Attribute) and root.func.attr == 'replace')

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
                root = node.func.value
                # Only a literal or known text variable can use str.replace.
                if not (isinstance(root, (ast.Constant, ast.JoinedStr, ast.Subscript)) or
                        isinstance(root, ast.Name) and root.id in {'text', 'data'} or
                        isinstance(root, ast.Call) and isinstance(root.func, ast.Attribute) and root.func.attr == 'replace'):
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
