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


def unguarded_calls(source):
    """Admit named collaborator operations; refuse unknown or indirect calls."""
    tree = ast.parse(source)
    imports = {alias.asname or alias.name.split('.')[0]: alias.name
               for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imports.update({alias.asname or alias.name: f'{node.module}.{alias.name}'
                    for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names})
    defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
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
            return node.id == 'open' or node.id in imports and imports[node.id].rsplit('.', 1)[-1] in WRITERS
        return False

    def allowed(node, *, captured=False):
        call = ast.unparse(node)
        if captured and call in data:
            return True
        if call in special:
            return True
        receiver = node
        while isinstance(receiver, ast.Attribute):
            receiver = receiver.value
        if isinstance(receiver, ast.Name) and (receiver.id in imports or receiver.id in {
            'os', 'shutil', 'tempfile', 'pathlib', 'json', 'builtins',
        }):
            canonical = imports.get(receiver.id, receiver.id) + call[len(receiver.id):]
            return canonical in IMPORTED_CALLS or captured and canonical in data
        if isinstance(node, ast.Attribute):
            if node.attr in READONLY | VALUES:
                return True
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                constructor = imports.get(node.value.func.id, node.value.func.id)
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
        if not callee and (writer_reference(node) or isinstance(node, ast.Attribute) and not receiver
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
