# SPDX-License-Identifier: Apache-2.0
"""review-maskfu3's finding on claude/masking-followup, as its reviewer wrote it, and every crossing a git name still made.

1. A held folder is looked up as it was held: every lookup made after a containment decision uses the path in the form
   the decision used, the system's own extended form (``\\\\?\\``), which Win32 does not normalise, so a trailing dot or
   space, a reserved device name or a short (8.3) name can never turn the folder G6 holds into another one
   (``repo_tests._where``, ``_link_inside``).
2. A git name is a type that cannot become text by accident (``g6.core.git_name.GitName``): made only from git's bytes by
   a reader of git's listings, it raises where Python offers a hook (``str()``, ``format()``, ``%``, ``repr()``), and the
   inventory below follows the type, by its flow through the code, to every other place it could become text (``join``,
   ``+``, an object made to hold text). Text for a person or a model comes only from ``shown_name``; git is given ``raw``.
3. Every name-to-path crossing in ``repo_tests`` goes through the one disk rule (``_on_disk``) and every containment
   decision through ``_within``.

Fixtures (folders, junctions, odd names) are made only in this test's own scratch folder; git runs only on scratch
repositories; no model and no test of a reviewer's runs."""
import ast
import copy
import ctypes
import importlib
import importlib.util
import inspect
import json
import os
import pickle
import struct
from ctypes import wintypes
from pathlib import Path

import pytest

from g6.workforce import repo_tests as rt
from tests.harness.test_name_crossings import _base, _case_sensitive, git_bytes

WINDOWS = os.name == 'nt'
_EXTENDED = '\\\\?\\'


def _names():
    """The module of the type and its one rule (read when a test runs, so this file is collected where it is missing)."""
    return importlib.import_module('g6.core.git_name')


# --- the inventory, by type: where a value of the git name type flows, from the code ---------------------------------------------

#: Where the type is made or handed on: the readers of git's listings and the functions that return their names, by the
#: name they are called by and the module they are called from. Each returns names, or a container of them, or a tuple one
#: of whose parts is one (``UNPACKED``).
SOURCES = {**dict.fromkeys(('GitName', '_paths', '_entries', '_tagged', '_listing', 'derive_tests', 'test_related', 'related_texts',
                            'snapshot', '_untracked', '_tracked_new', '_held', '_moves', '_gitlinks', '_gitlink_entries',
                            'loadable_changes', 'submodules_not_hashed', 'texts_at', 'run_before', 'ignored_inputs'),
                           'g6.workforce.repo_tests'), 'run_set': 'g6.workforce.verify'}
#: Which parts of a source's tuple hold names, where it returns one: ``(names, how many more)``, ``(records, patch)``.
UNPACKED = {'derive_tests': (True, False), '_listing': (True, False), 'related_texts': (True, True), '_held': (True, True)}
#: The fields that hold names: a change's (``change.files``), a read file's (``one.rel``), an ignored-file scan's.
FIELDS = frozenset({'files', 'sources', 'runner_files', 'outside_links', 'submodules', 'submodule_changes', 'held', 'unseen',
                    'renamed', 'before', 'after', 'rel', 'loadable', 'other', 'named_like_modules', 'known', 'at_base'})
#: What a field is read from: an object a change, a read or a scan is called by (``tree.change.files`` too).
HOLDERS = frozenset({'change', 'changed', 'one', 'read', 'scan', 'patch', 'tree'})
#: The types of what holds names in its fields, and the calls that make one: a value annotated as one, or bound to what
#: one of these calls returns, holds names whatever it is called.
HOLDER_TYPES = frozenset({'Change', 'DiffRead', 'FileRead', 'Ignored'})
HOLDER_CALLS = frozenset({'change', 'diff_read', 'ignored_inputs', 'Change', 'FileRead', 'DiffRead', 'replace'})
#: The fields no other object has: whatever holds one is a change.
OWN_FIELDS = frozenset({'sources', 'runner_files', 'outside_links', 'submodules', 'submodule_changes', 'unseen',
                        'renamed', 'named_like_modules', 'rel'})
#: The mappings whose values are names too (an old path to its new one, and the other way).
NAMED_VALUES = frozenset({'renamed', 'moved', 'old_path'})
#: The fields of a record, named by a word, that hold names (a run's record of the tests it ran, a reply's changed files).
NAMED_KEYS = frozenset({'tests', 'changed_files'})
#: What turns a name into text by the rule (``shown_name``; ``name_line``, the copy's ``_name`` and ``shown`` through it).
RULES = frozenset({'shown_name', 'name_line', '_name', 'shown'})
#: What reads no name out of its arguments: a length, a truth value, a digest, a test of a path, a read by git or the
#: system, git's bytes (``_raw``, ``_at``), a path for the system (``_disk``).
QUIET = frozenset({'len', 'bool', 'isinstance', 'any', 'all', 'sum', 'hash', 'id', 'callable', 'is_test_file',
                   'is_test_related', '_scanned', 'startswith', 'endswith', 'isidentifier', 'is_file', 'is_dir', 'exists',
                   'lexists', 'is_symlink', 'is_junction', '_linked', 'links_outside', '_leads_out', 'encode', 'sha256',
                   'digest', 'hexdigest', 'count', 'index', 'find', '_python_file', '_disk', '_raw', '_at', 'match',
                   'fullmatch', 'search', '_covers', '_names', 'issubset', 'issuperset', 'isdisjoint', '_weakening_kind',
                   '_unscanned', '_module', '_import_names', 'changed_lines', 'is_relative_to', '_within', 'samefile',
                   'read_text', 'read_bytes', 'stat', 'lstat', 'readlink', '_link_of', '_digest', '_submodule',
                   'shape_problem', '_declares_plugin', 'blob_bytes', '_git', '_read_inside', '_link_inside', 'dumps',
                   'loads', 'Path', 'joinpath', 'resolve', 'absolute', 'open', 'is_reserved', 'mkdir', 'unlink', 'rmdir',
                   'walk', 'symlink', 'copymode', 'write_bytes', 'write_text'})
#: What hands its argument's names on: a container made of them, a part of one (a pure path of one, a piece of its text).
CARRIES = frozenset({'sorted', 'list', 'set', 'tuple', 'frozenset', 'reversed', 'iter', 'next', 'min', 'max', 'dict',
                     'enumerate', 'zip', 'filter', 'fromkeys', 'get', 'pop', 'copy', 'union', 'intersection',
                     'difference', 'symmetric_difference', 'items', 'keys', 'values', 'setdefault', 'PurePosixPath',
                     'PurePath', 'PureWindowsPath', 'relative_to', 'as_posix', 'with_suffix', 'removeprefix',
                     'removesuffix', 'strip', 'rstrip', 'lstrip', 'replace', 'lower', 'upper', 'casefold', 'partition',
                     'rpartition', 'split', 'rsplit', 'splitlines', 'chain', 'unquoted_name', '_unquoted', '_diff_name',
                     'normcase', 'fsdecode', 'decode', 'fspath', 'deque', 'Counter', 'OrderedDict', 'defaultdict', 'map',
                     'accumulate'})
#: Carriers read from their receiver alone: what a key or an index finds in a container is that container's.
RECEIVED = frozenset({'get', 'pop', 'setdefault', 'items', 'keys', 'values', 'copy'})
#: Calls that put their arguments' names into their receiver (``found.append(rel)``).
INTO = frozenset({'append', 'extend', 'add', 'update', 'insert', 'setdefault'})
#: Path parts that are no name: a suffix is the type of a file, not which one; ``raw`` is the bytes git is given.
PARTLESS = frozenset({'suffix', 'suffixes', 'drive', 'root', 'anchor', 'raw'})
#: Where a name becomes text that Python gives a hook for (the type raises there too).
TEXT_CALLS = frozenset({'str', 'repr', 'ascii', 'print', '__str__', '__format__', 'debug', 'info', 'warning', 'error',
                        'exception', 'critical', 'log'})
#: The classes made to hold names as names (the read's records, paths): any other class a name is handed to is taken to
#: hold text (an ``Evidence``'s ref, an exception's message), and a name reaches it only by the rule.
HOLDING = frozenset({'GitName', 'Change', 'FileRead', 'DiffRead', 'Ignored', 'Path', 'PurePosixPath', 'PurePath',
                     'PureWindowsPath', 'Counter', 'OrderedDict', 'ExitStack'})
#: The origin of a name that is one whatever a function is given (a source's, a field's).
_MADE = '*'
#: The origin of a name's spelling for the system (``repo_tests._on_disk``, ``_in``): still the name in text, but the one
#: rule for a path.
_DISK = 'disk'
#: The disk rule's own functions.
DISK_RULES = frozenset({'_on_disk', '_in'})


def _called(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, 'id', '')


def _text(node: ast.AST) -> bool:
    """Whether ``node`` is plainly text: a string constant, an f-string, a ``format`` or ``join`` of one, a name as the rule
    writes it."""
    if (isinstance(node, ast.Constant) and isinstance(node.value, str)) or isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('format', 'join'):
        return _text(node.func.value)
    return isinstance(node, ast.Call) and _called(node) in RULES


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _own(statement: ast.AST, defs: bool = False):
    """Every node of ``statement`` but those of a function or class defined in it, each walked as its own scope (with
    ``defs``, each such definition itself too, and a class's methods)."""
    if isinstance(statement, (*_DEFS, ast.ClassDef)):
        if defs:
            yield statement
            if isinstance(statement, ast.ClassDef):
                for inner in statement.body:
                    yield from _own(inner, defs)
        return
    yield statement
    for child in ast.iter_child_nodes(statement):
        yield from _own(child, defs)


def _is_package(name: str) -> bool:
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    return spec is not None and spec.submodule_search_locations is not None


def _guard(test: ast.AST):
    """``(name, True)`` where ``test`` is ``not isinstance(name, GitName)`` or ``not isinstance(name, str)`` (its body sees
    no git name in ``name``), ``(name, False)`` where it is ``isinstance(name, GitName)`` or ``isinstance(name, str)`` (its
    ``else`` sees none), else None."""
    negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
    call = test.operand if negated else test
    if not (isinstance(call, ast.Call) and _called(call) == 'isinstance' and len(call.args) == 2
            and isinstance(call.args[0], ast.Name) and isinstance(call.args[1], ast.Name)):
        return None
    return (call.args[0].id, negated) if call.args[1].id in ('GitName', 'str') else None


def _holding(annotation: ast.AST) -> bool:
    """Whether an annotation names a type that holds names (``repo_tests.Change``, ``Change | None``)."""
    return any((isinstance(part, ast.Name) and part.id in HOLDER_TYPES)
               or (isinstance(part, ast.Attribute) and part.attr in HOLDER_TYPES)
               or (isinstance(part, ast.Constant) and isinstance(part.value, str) and part.value.split('.')[-1] in HOLDER_TYPES)
               for part in ast.walk(annotation))


def _either(one, other):
    """A part of a tuple that either of two branches gives: its origins, or whether it holds the value's, from both."""
    if isinstance(one, set) and isinstance(other, set):
        return one | other
    return bool(one) or bool(other)


def _plain(value: ast.AST) -> bool:
    """Whether ``value`` is a name or a container of names as such (not a record that holds some among other fields): a
    name read, a part of one, a container made of them; never a mapping written out or a function's own result."""
    if isinstance(value, ast.Call):
        return _called(value) in CARRIES or _called(value) in SOURCES
    return isinstance(value, (ast.Name, ast.Attribute, ast.Subscript, ast.Starred, ast.List, ast.Tuple, ast.Set,
                              ast.ListComp, ast.SetComp, ast.GeneratorExp))


def _params(function: ast.AST) -> list[str]:
    return [arg.arg for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]]


class _Flow:
    """One module of a ``_Program``. Each value's origins: ``_MADE`` where it holds a name whatever its function is given,
    ``_DISK`` where it holds a name as the disk rule spells it, and ``(function, parameter)`` where it holds one when that
    parameter does; a parameter does when a call in the program hands it one. So a function that hands back what it is
    given (``_confined(ran)``) gives a name only where it was given one, and every other place is read once, wherever it
    is called from. A branch only a value that is not a git name reaches (``if not isinstance(x, GitName):``) sees none
    in ``x``."""

    def __init__(self, program: '_Program', name: str):
        self.program, self.name = program, name
        self.tree = ast.parse(inspect.getsource(importlib.import_module(name)))
        self.functions = [node for node in ast.walk(self.tree) if isinstance(node, _DEFS)]
        self.owner: dict[ast.AST, ast.AST] = {}
        self.methods: dict[str, list] = {}
        for scope in [self.tree, *self.functions]:
            for statement in scope.body:
                for child in _own(statement, defs=True):
                    if isinstance(child, _DEFS) and child is not scope:
                        self.owner.setdefault(child, scope)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                for inner in node.body:
                    if isinstance(inner, _DEFS):
                        self.methods.setdefault(inner.name, []).append(inner)
                        self.owner[inner] = self.tree
        self.imports: dict[str, tuple[str, str]] = {}   # a name here: (module, function or "")
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('g6.'):
                        self.imports[alias.asname or alias.name] = (alias.name, '')
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('g6') and not node.level:
                for alias in node.names:
                    full = f'{node.module}.{alias.name}'
                    module = _is_package(node.module) and importlib.util.find_spec(full) is not None
                    self.imports[alias.asname or alias.name] = (full, '') if module else (node.module, alias.name)
        for scope in [self.tree, *self.functions]:
            program.named.setdefault(scope, {})
            program.parts.setdefault(scope, {})
            if isinstance(scope, _DEFS):
                for param in _params(scope):
                    program.named[scope].setdefault(param, set()).add((scope, param))
        self.holders: dict[ast.AST, set[str]] = {}   # each scope's names annotated as, or bound to, a holder of names
        for scope in [self.tree, *self.functions]:
            held = self.holders.setdefault(scope, set())
            if isinstance(scope, _DEFS):
                held.update(arg.arg for arg in [*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs]
                            if arg.annotation is not None and _holding(arg.annotation))
            for statement in scope.body:
                for node in _own(statement):
                    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and _holding(node.annotation):
                        held.add(node.target.id)
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            pairs = (zip(target.elts, node.value.elts) if isinstance(target, ast.Tuple)
                                     and isinstance(node.value, ast.Tuple) else [(target, node.value)])
                            held.update(one.id for one, value in pairs if isinstance(one, ast.Name)
                                        and isinstance(value, ast.Call) and _called(value) in HOLDER_CALLS)
        self.guarded: set[int] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.If, ast.IfExp)):
                found = _guard(node.test)
                if found is not None:
                    branch = node.body if found[1] else node.orelse
                    for part in (branch if isinstance(branch, list) else [branch]):
                        self.guarded.update(id(use) for use in ast.walk(part)
                                            if isinstance(use, ast.Name) and use.id == found[0])

    def chain(self, scope: ast.AST):
        while scope is not None:
            yield scope
            scope = self.owner.get(scope)

    def top(self, name: str) -> list:
        return [node for node in self.tree.body if isinstance(node, _DEFS) and node.name == name]

    def callees(self, call: ast.Call, scope: ast.AST, load: bool) -> list:
        """The functions ``call`` may reach: a bare name's, in the scopes it sees or as imported; ``self.x``'s, this module's
        methods of that name; ``module.x``'s, that module's own (read into the program, with ``load``, when it is G6's)."""
        func = call.func
        if isinstance(func, ast.Name):
            for outer in self.chain(scope):
                found = [node for node in outer.body if isinstance(node, _DEFS) and node.name == func.id]
                if found:
                    return found
            if func.id in self.imports and self.imports[func.id][1]:
                return self.program.functions(*self.imports[func.id], load=load)
            return []
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in self.imports and not self.imports[func.value.id][1]:
                return self.program.functions(self.imports[func.value.id][0], func.attr, load=load)
        if isinstance(func, ast.Attribute):   # ``self.x``, ``tree.text``: this module's methods of that name
            return self.methods.get(func.attr, [])
        return []

    def _targets(self, call: ast.Call, module: str) -> bool:
        """Whether ``call`` calls a function of ``module`` (by a bare name there or imported from it, or through the module)."""
        func = call.func
        if isinstance(func, ast.Name):
            return self.name == module or self.imports.get(func.id, ('', ''))[0] == module
        return (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and self.imports.get(func.value.id) == (module, ''))

    def _origins(self, name: str, scope: ast.AST) -> set:
        """A name's origins in ``scope`` and the scopes around it, as far as the first that binds it as a parameter (an
        enclosing function's own name of that spelling is another value)."""
        found = set()
        for outer in self.chain(scope):
            found |= self.program.named.get(outer, {}).get(name, set())
            if isinstance(outer, _DEFS) and name in _params(outer):
                break
        return found

    def _shape(self, name: str, scope: ast.AST):
        return next((self.program.parts[outer][name] for outer in self.chain(scope)
                     if name in self.program.parts.get(outer, {})), None)

    def origins(self, node: ast.AST, scope: ast.AST) -> set:
        """Where ``node``'s value may hold a git name from (``_MADE``, ``_DISK``, or a parameter's)."""
        if isinstance(node, ast.Name):
            return set() if id(node) in self.guarded else self._origins(node.id, scope)
        if isinstance(node, ast.Attribute):
            if node.attr in PARTLESS:
                return set()
            if node.attr in FIELDS and (node.attr in OWN_FIELDS or self._holder(node.value, scope)):
                return {_MADE}
            return self.origins(node.value, scope) if node.attr in ('name', 'stem', 'parent', 'parts') else set()
        if isinstance(node, ast.Starred):
            return self.origins(node.value, scope)
        if isinstance(node, ast.Subscript):
            return self._entry(node.value, node.slice, None, scope)
        if isinstance(node, ast.Call):
            return self._call_origins(node, scope)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.BitOr, ast.BitAnd, ast.Sub, ast.BitXor, ast.Add)):
                return self.origins(node.left, scope) | self.origins(node.right, scope)
            return set()
        if isinstance(node, ast.IfExp):
            return self.origins(node.body, scope) | self.origins(node.orelse, scope)
        if isinstance(node, ast.BoolOp):
            return set().union(*(self.origins(value, scope) for value in node.values))
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return set().union(*(self.origins(element, scope) for element in node.elts))
        if isinstance(node, ast.Dict):   # its keys, and its values stored under a word a name was ever stored under
            return set().union(*(self.origins(key, scope) for key in node.keys if key is not None),
                               *(self.origins(value, scope) for key, value in zip(node.keys, node.values)
                                 if isinstance(key, ast.Constant) and key.value in self.program.keys))
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):   # its targets are bound in ``scope``
            return self.origins(node.elt, scope)
        if isinstance(node, ast.DictComp):
            return self.origins(node.key, scope) | self.origins(node.value, scope)
        if isinstance(node, ast.NamedExpr):
            return self.origins(node.value, scope)
        return set()

    def _call_origins(self, node: ast.Call, scope: ast.AST) -> set:
        called = _called(node)
        if called in RULES or called in QUIET:
            return set()
        if called in SOURCES and self._targets(node, SOURCES[called]):
            return {_MADE}
        if called in DISK_RULES:
            return {_DISK} if any(self.origins(arg, scope) for arg in node.args) else set()
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        if called in CARRIES:
            if called in RECEIVED and receiver is not None:   # what a key finds in a mapping is that mapping's
                if called == 'values':
                    return self.origins(receiver, scope) if self._named_values(receiver) else set()
                if called in ('get', 'pop', 'setdefault') and node.args:
                    return self._entry(receiver, node.args[0], node.args[1] if len(node.args) > 1 else None, scope)
                return self.origins(receiver, scope)
            return set().union(*(self.origins(part, scope) for part in [
                *node.args, *(k.value for k in node.keywords), *([receiver] if receiver is not None else [])]))
        found = set()
        for callee in self.callees(node, scope, load=False):
            returned = self.program.returning.get(callee, set())
            found |= returned & {_MADE, _DISK}
            given = dict(zip(self._given(callee, node), [*node.args]))
            given.update({k.arg: k.value for k in node.keywords if k.arg})
            for origin in returned - {_MADE, _DISK}:
                if origin[0] is callee and origin[1] in given and not isinstance(given[origin[1]], ast.Starred):
                    found |= self.origins(given[origin[1]], scope)
        return found

    def _entry(self, container: ast.AST, key: ast.AST, default: ast.AST | None, scope: ast.AST) -> set:
        """What ``container[key]`` (or ``.get(key, default)``) holds: a part of a container of names (a slice, an index)
        is one; a field a record names by a word holds one only where ``NAMED_KEYS`` (or a name stored under it) says;
        what a mapping keyed by names finds by a name is its value, a name only in a mapping of names (``NAMED_VALUES``)."""
        found = self.origins(default, scope) if default is not None else set()
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            named = key.value in NAMED_KEYS or key.value in self.program.keys
            return found | (self.origins(container, scope) if named else set())
        if not isinstance(key, ast.Slice) and self.origins(key, scope):
            return found | (self.origins(container, scope) if self._named_values(container) else set())
        return found | self.origins(container, scope)

    def _named_values(self, node: ast.AST) -> bool:
        return ((isinstance(node, ast.Attribute) and node.attr in NAMED_VALUES)
                or (isinstance(node, ast.Name) and node.id in NAMED_VALUES))

    def holds(self, node: ast.AST, scope: ast.AST) -> bool:
        """Whether ``node``'s value may hold a git name, as git gave it or as the disk rule spells it: an origin that is
        one whatever, or a parameter some call hands one."""
        return any(origin in (_MADE, _DISK) or origin[1] in self.program.given.get(origin[0], ()) for origin in
                   self.origins(node, scope))

    def names(self, node: ast.AST, scope: ast.AST) -> bool:
        """Whether ``node``'s value may hold a git name as git gave it, not as the disk rule spells it for the system."""
        return any(origin == _MADE or (origin != _DISK and origin[1] in self.program.named_given.get(origin[0], ()))
                   for origin in self.origins(node, scope))

    def _given(self, callee: ast.AST, call: ast.Call) -> list[str]:
        params = [arg.arg for arg in [*callee.args.posonlyargs, *callee.args.args]]
        if params and params[0] in ('self', 'cls') and isinstance(call.func, ast.Attribute):
            params = params[1:]
        return params

    def _holder(self, node: ast.AST, scope: ast.AST) -> bool:
        """Whether ``node`` is a change, a read or a scan: by what it is called, by the call that makes it, or, for a name,
        by its annotation or the call it was bound to (``found = repo_tests.change(...)``)."""
        if isinstance(node, ast.Attribute):
            return node.attr in HOLDERS
        if isinstance(node, ast.Call):      # ``repo_tests.change(repo).files``: a holder read where it is made
            return _called(node) in HOLDER_CALLS
        return isinstance(node, ast.Name) and (node.id in HOLDERS or any(
            node.id in self.holders.get(outer, ()) for outer in self.chain(scope)))

    def elements(self, node: ast.AST, scope: ast.AST):
        """Where each part of each element of the iterable ``node`` holds a name from (a set of origins, or the parts of a
        part), where each is a tuple of a plain shape; None where that is not known."""
        while (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)) or (
                isinstance(node, ast.Call) and _called(node) in ('sorted', 'list', 'tuple', 'reversed', 'iter', 'set')
                and len(node.args) == 1):
            node = node.value if isinstance(node, ast.Subscript) else node.args[0]
        if isinstance(node, ast.Name):
            return self._shape(node.id, scope)
        if isinstance(node, ast.Call) and _called(node) == 'items' and isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            held = self.origins(receiver, scope)
            return (held, held if self._named_values(receiver) else set())
        if isinstance(node, ast.Call) and _called(node) == 'enumerate' and node.args:
            return (set(), self.elements(node.args[0], scope) or self.origins(node.args[0], scope))
        if isinstance(node, ast.Call) and _called(node) == 'zip':
            return tuple(self.elements(arg, scope) or self.origins(arg, scope) for arg in node.args)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)) and isinstance(node.elt, ast.Tuple):
            return tuple(self.origins(part, scope) for part in node.elt.elts)
        if isinstance(node, (ast.Tuple, ast.List)) and node.elts and all(
                isinstance(element, ast.Tuple) and len(element.elts) == len(node.elts[0].elts) for element in node.elts):
            return tuple(set().union(*(self.origins(element.elts[at], scope) for element in node.elts))
                         for at in range(len(node.elts[0].elts)))
        return None

    def _unpacked(self, node: ast.AST, scope: ast.AST):
        if isinstance(node, ast.IfExp):    # either branch's tuple: a part holds what either's does
            body, orelse = self._unpacked(node.body, scope), self._unpacked(node.orelse, scope)
            if body is not None and orelse is not None and len(body) == len(orelse):
                return tuple(_either(one, other) for one, other in zip(body, orelse))
            return None
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(self.origins(part, scope) for part in node.elts)
        if isinstance(node, ast.Call) and _called(node) in UNPACKED:
            return UNPACKED[_called(node)]
        return None

    def _bind(self, target: ast.AST, scope: ast.AST, origins: set, shape=None) -> None:
        """``target`` bound to a value of ``origins``; where its parts' own are known (``shape``: a set of origins, the
        parts of a part, or whether it holds the value's), each part to its own."""
        if isinstance(target, (ast.Tuple, ast.List)) and shape is not None and len(shape) == len(target.elts):
            for part, held in zip(target.elts, shape):
                if isinstance(held, set):
                    self._bind(part, scope, held)
                elif isinstance(held, tuple):
                    self._bind(part, scope, origins, held)
                elif held:
                    self._bind(part, scope, origins)
            return
        if not origins:
            return
        if isinstance(target, (ast.Subscript, ast.Attribute)):
            target = target.value if isinstance(target, ast.Subscript) else None   # ``found[rel] = ...``: found holds one
            if target is None:
                return
        for name in ast.walk(target):
            if isinstance(name, ast.Name):
                self.program.named[scope].setdefault(name.id, set()).update(origins)

    def walk(self) -> None:
        for scope in [self.tree, *self.functions]:
            for node in (child for statement in scope.body for child in _own(statement)):
                self._step(node, scope)
        for function in self.functions:   # a default is the enclosing scope's value: ``def run(rel: str = rel)``
            outer = self.owner.get(function, self.tree)
            args = [*function.args.posonlyargs, *function.args.args]
            pairs = [*zip(args[len(args) - len(function.args.defaults):], function.args.defaults),
                     *((arg, default) for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
                       if default is not None)]
            for arg, default in pairs:
                if self.holds(default, outer):
                    self.program.given.setdefault(function, set()).add(arg.arg)
                if self.names(default, outer):
                    self.program.named_given.setdefault(function, set()).add(arg.arg)

    def _step(self, node: ast.AST, scope: ast.AST) -> None:
        if isinstance(node, ast.Dict):     # a word a name is stored under: ``{"file": rel}``, ``{"tests": list(ids)}``
            self.program.keys.update(key.value for key, value in zip(node.keys, node.values)
                                     if isinstance(key, ast.Constant) and isinstance(key.value, str)
                                     and _plain(value) and self.holds(value, scope))
        elif isinstance(node, ast.Assign) and _plain(node.value) and self.holds(node.value, scope):
            self.program.keys.update(target.slice.value for target in node.targets if isinstance(target, ast.Subscript)
                                     and isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str))
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                shape = self.elements(node.value, scope)
                if isinstance(target, ast.Name) and shape is not None:
                    self.program.parts[scope][target.id] = shape
                keyed = self.origins(target.slice, scope) if isinstance(target, ast.Subscript) else set()
                unpacked = self._unpacked(node.value, scope)
                if unpacked is None and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 1 \
                        and isinstance(node.value, ast.Name):   # ``[(folder, names)] = held``: its one element's parts
                    element = self._shape(node.value.id, scope)
                    unpacked = (element,) if isinstance(element, tuple) else None
                self._bind(target, scope, self.origins(node.value, scope) | keyed, unpacked)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            self._bind(node.target, scope, self.origins(node.iter, scope), self.elements(node.iter, scope))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            self._bind(node.optional_vars, scope, self.origins(node.context_expr, scope))
        elif isinstance(node, ast.NamedExpr):
            self._bind(node.target, scope, self.origins(node.value, scope))
        elif isinstance(node, ast.Return) and node.value is not None and isinstance(scope, _DEFS):
            self.program.returning.setdefault(scope, set()).update(self.origins(node.value, scope))
        elif isinstance(node, ast.Call):
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            values = [*node.args, *(k.value for k in node.keywords)]
            if _called(node) in INTO and isinstance(receiver, ast.Name):
                self._bind(receiver, scope, set().union(*(self.origins(value, scope) for value in values)))
                if _called(node) in ('append', 'add') and len(node.args) == 1 and isinstance(node.args[0], ast.Tuple):
                    shape = tuple(self.origins(part, scope) for part in node.args[0].elts)   # each element's parts
                    known = self.program.parts[scope].get(receiver.id)
                    if isinstance(known, tuple) and len(known) == len(shape) and all(isinstance(k, set) for k in known):
                        shape = tuple(one | other for one, other in zip(known, shape))
                    self.program.parts[scope][receiver.id] = shape
            loaded = any(self.holds(value, scope) for value in values)
            for callee in self.callees(node, scope, load=loaded):
                for param, arg in zip(self._given(callee, node), node.args):
                    if not isinstance(arg, ast.Starred) and self.holds(arg, scope):
                        self.program.given.setdefault(callee, set()).add(param)
                    if not isinstance(arg, ast.Starred) and self.names(arg, scope):
                        self.program.named_given.setdefault(callee, set()).add(param)
                for keyword in node.keywords:
                    if keyword.arg and self.holds(keyword.value, scope):
                        self.program.given.setdefault(callee, set()).add(keyword.arg)
                    if keyword.arg and self.names(keyword.value, scope):
                        self.program.named_given.setdefault(callee, set()).add(keyword.arg)


class _Program:
    """The modules a git name reaches, from ``roots``, each read once a call hands one of its functions a name, followed
    to a fixed point across them."""

    def __init__(self, roots):
        self.named: dict[ast.AST, dict[str, set]] = {}
        self.parts: dict[ast.AST, dict[str, tuple]] = {}
        self.returning: dict[ast.AST, set] = {}
        self.given: dict[ast.AST, set[str]] = {}
        self.named_given: dict[ast.AST, set[str]] = {}
        self.keys: set[str] = set()
        self.flows: dict[str, _Flow] = {}
        for root in roots:
            self.module(root)
        while True:
            before = self._size()
            for flow in list(self.flows.values()):
                flow.walk()
            if before == self._size():
                break

    def _size(self):
        return (sum(len(origins) for names in self.named.values() for origins in names.values()),
                sum(map(len, self.parts.values())), sum(map(len, self.returning.values())),
                sum(map(len, self.given.values())), sum(map(len, self.named_given.values())), len(self.flows),
                len(self.keys))

    def module(self, name: str) -> '_Flow | None':
        if name not in self.flows:
            try:
                self.flows[name] = _Flow(self, name)
            except (ImportError, OSError, TypeError):
                return None
        return self.flows[name]

    def functions(self, module: str, name: str, load: bool) -> list:
        if not module.startswith('g6.') or (module not in self.flows and not load):
            return []
        flow = self.module(module)
        return flow.top(name) if flow is not None else []

    def sinks(self, allowed: dict[str, str]) -> list[str]:
        return sorted({line for flow in self.flows.values() for line in _sinks(flow, allowed)},
                      key=lambda line: (line.split(':')[0], int(line.split(':')[1])))

    def places(self) -> set[str]:
        """Every place the walk finds, by its module and source text (the key ``ALLOWED`` rules by)."""
        return {key for flow in self.flows.values() for key in _sinks(flow, None)}


def _sinks(flow: _Flow, allowed: dict[str, str] | None) -> list[str]:
    """Each place in ``flow``'s module that turns a value that may hold a git name into text other than by the rule: an
    f-string's value, a ``format`` or ``%`` argument, ``str()``, ``repr()``, ``print`` or a log call (hooks the type raises
    in too), a ``join`` of names or a ``+`` with text (which have no hook), and a name handed to an object made to hold
    text. A place ``allowed`` lists, by its module and source text, is one the inventory read and ruled on, with why."""
    found = []
    for scope in [flow.tree, *flow.functions]:
        texty = {target.id for statement in scope.body for node in _own(statement)
                 if isinstance(node, ast.Assign) and _text(node.value) for target in node.targets
                 if isinstance(target, ast.Name)}
        for statement in scope.body:
            for node in _own(statement):
                hit = None
                if isinstance(node, ast.FormattedValue) and flow.holds(node.value, scope):
                    hit = node.value
                elif isinstance(node, ast.Call):
                    called = _called(node)
                    named = any(flow.holds(value, scope) for value in [*node.args, *(k.value for k in node.keywords)])
                    if named and ((called == 'format' and isinstance(node.func, ast.Attribute)) or called in TEXT_CALLS
                                  or (called == 'join' and isinstance(node.func, ast.Attribute)
                                      and _text(node.func.value))
                                  or (called[:1].isupper() and called not in HOLDING)):   # an object made to hold text
                        hit = node
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and flow.holds(node.right, scope):
                    hit = node
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and (
                        (_text(node.left) and flow.holds(node.right, scope))
                        or (_text(node.right) and flow.holds(node.left, scope))):
                    hit = node
                elif (isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name)
                      and node.target.id in texty and flow.holds(node.value, scope)):
                    hit = node
                if hit is not None and (allowed is None or f'{flow.name}: {ast.unparse(hit)}' not in allowed):
                    found.append(f'{flow.name}: {ast.unparse(hit)}' if allowed is None else
                                 f'{flow.name}:{getattr(hit, "lineno", "?")}: {ast.unparse(hit)[:160]}')
    return found


#: What reaches the file system by a path: a look or an open.
OS_CALLS = frozenset({'open', 'lexists', 'exists', 'isfile', 'isdir', 'islink', 'walk', 'listdir', 'scandir', 'stat',
                      'lstat', 'readlink', 'mkdir', 'symlink', 'unlink', 'rmdir', 'realpath', 'samefile', 'Path'})


def _disk_sinks(flow: _Flow) -> list[str]:
    """Each place in ``flow``'s module where a git name, as git gave it, is made a path for the system: joined to a folder
    (``/``, ``joinpath``) or given to a look or an open, other than by the disk rule (``_on_disk``, ``_in``, ``_disk``)."""
    found = []
    for scope in [flow.tree, *flow.functions]:
        for statement in scope.body:
            for node in _own(statement):
                hit = None
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and flow.names(node.right, scope):
                    hit = node
                elif isinstance(node, ast.Call) and (_called(node) == 'joinpath' or _called(node) in OS_CALLS) and any(
                        flow.names(arg, scope) for arg in node.args):
                    hit = node
                if hit is not None:
                    found.append(f'{flow.name}:{hit.lineno}: {ast.unparse(hit)[:160]}')
    return found


#: The modules a git name starts from: the reader and every module that imports it.
ROOTS = ('g6.workforce.repo_tests', 'g6.harness.patch_review', 'g6.harness.completion_loop', 'g6.harness.completion_hook',
         'g6.harness.verification_templates', 'g6.harness.verification_search', 'g6.harness.verification_proposer',
         'g6.harness.verification_controller', 'g6.workforce.verify', 'g6.bundles.agent_change', 'g6.mcp.review_loop',
         'g6.mcp.mission', 'g6.metaharness.coding', 'g6.code.swe_diagnostics.block', 'g6.workforce.receipt_store',
         'g6.workforce.coding', 'g6.workforce.bench', 'g6.workforce.objective', 'g6.workforce.truth',
         'g6.algorithm_design.autonomous', 'g6.coding_agent_gate.coding_agent_gate_block')


def crossings(roots=ROOTS, allowed: dict[str, str] | None = None) -> list[str]:
    """The places, in the modules a git name reaches from ``roots``, that turn one into text other than by the rule."""
    return _Program(roots).sinks(allowed or {})


def _junction(target: Path, link: Path) -> None:
    """``link`` made a junction to ``target`` (the reviewer's helper): the NT name of the target is written as given, so a
    trailing dot or space in it is kept, and ``link`` itself is made by its literal name."""
    os.mkdir(_EXTENDED + str(link))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    ioctl = kernel.DeviceIoControl
    ioctl.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                      ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    handle = create(_EXTENDED + str(link), 0x40000000, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    substitute = ('\\??\\' + str(target)).encode('utf-16le')
    printed = str(target).encode('utf-16le')
    names = substitute + b'\0\0' + printed + b'\0\0'
    data = struct.pack('<LHHHHHH', 0xa0000003, 8 + len(names), 0, 0, len(substitute),
                       len(substitute) + 2, len(printed)) + names
    try:
        returned = wintypes.DWORD()
        if not ioctl(handle, 0x900a4, data, len(data), None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def _mkdir(path: Path) -> Path:
    os.mkdir(_EXTENDED + str(path))
    return path


def _target(link: Path) -> bytes:
    """What the junction ``link`` names, read by its literal name, as ``os.readlink`` gives it."""
    return os.fsencode(os.readlink(_EXTENDED + str(link)))


def _remove(links: list[Path]) -> None:
    for link in reversed(links):
        os.rmdir(_EXTENDED + str(link))


# --- review-maskfu3's finding, as its reviewer wrote it (its scratch folder is this test's own) ----------------------------------

@pytest.mark.skipif(not WINDOWS, reason='NTFS literal and Win32 normalised names')
@pytest.mark.parametrize('suffix', ['.', ' '], ids=['trailing-dot', 'trailing-space'])
def test_finding_1_link_is_read_in_the_literal_folder_that_passed_containment(tmp_path, suffix):
    repo, outside = tmp_path / 'repo', tmp_path / 'outside'
    repo.mkdir()
    outside.mkdir()
    literal_folder = Path(_EXTENDED + str(repo / ('held' + suffix)))
    literal_folder.mkdir()
    inside_target, outside_target = repo / 'inside-target', outside / 'outside-target'
    inside_target.mkdir()
    outside_target.mkdir()
    links = []
    try:
        for target, link in [(inside_target, repo / ('held' + suffix) / 'link'), (outside_target, outside / 'link'),
                             (outside, repo / 'held'), (repo / ('held' + suffix), repo / 'route')]:
            _junction(target, link)
            links.append(link)
        expected = os.fsencode(os.readlink(literal_folder / 'link'))
        external = os.fsencode(os.readlink(outside / 'link'))
        assert expected != external
        # route traverses to the literal in-repository held. directory. No rename or race is involved.
        assert (repo / 'route').resolve() == literal_folder.resolve()
        got = rt._link_inside(repo.resolve(), repo / 'route' / 'link')
        assert got in (None, expected), 'the held in-repository folder was replaced by its Win32 alias outside the repository'
    finally:
        _remove(links)


# --- item 1, at its root: each lookup after the decision is in the form the decision used ----------------------------------------

@pytest.mark.skipif(not WINDOWS, reason='NTFS literal and Win32 normalised names')
@pytest.mark.parametrize('folder', ['held.', 'held ', 'held..', 'held. .', 'aux', 'con', 'nul.txt', 'COM1'],
                         ids=['dot', 'space', 'dots', 'dot-space-dot', 'aux', 'con', 'nul-txt', 'com1'])
def test_a_held_folder_reached_through_a_link_is_the_one_its_link_is_read_in(tmp_path, folder):
    """The folder G6 holds, whatever Win32 would make of its name, reached through a link inside the repository; beside it,
    the name Win32 would read it as is a junction that leads out, to a folder holding a link of the same name."""
    repo, outside = _mkdir(tmp_path / 'repo'), _mkdir(tmp_path / 'outside')
    held = _mkdir(repo / folder)
    inside_target, outside_target = _mkdir(repo / 'inside-target'), _mkdir(outside / 'outside-target')
    alias = folder.rstrip('. ')
    links = []
    try:
        for target, link in [(inside_target, held / 'link'), (outside_target, outside / 'link'), (held, repo / 'route')]:
            _junction(target, link)
            links.append(link)
        if alias != folder:
            _junction(outside, repo / alias)
            links.append(repo / alias)
        assert rt._link_inside(repo.resolve(), repo / 'route' / 'link') == _target(held / 'link')
    finally:
        _remove(links)


@pytest.mark.skipif(not WINDOWS, reason='NTFS literal and Win32 normalised names')
@pytest.mark.parametrize('name', ['link.', 'link ', 'link..', 'aux', 'nul', 'COM1.txt'],
                         ids=['dot', 'space', 'dots', 'aux', 'nul', 'com1-txt'])
def test_a_link_is_read_by_its_own_name_in_the_folder_held(tmp_path, name):
    """A link whose own name Win32 would read as another (a trailing dot or space dropped) or as a device: read by its name
    as git gives it, never as the sibling Win32 would find (the sibling, where there is one, names another target)."""
    repo = _mkdir(tmp_path / 'repo')
    held = _mkdir(repo / 'held')
    mine, other = _mkdir(repo / 'mine'), _mkdir(repo / 'other')
    links = []
    try:
        _junction(mine, held / name)
        links.append(held / name)
        if name.rstrip('. ') != name:
            _junction(other, held / name.rstrip('. '))
            links.append(held / name.rstrip('. '))
        assert rt._link_inside(repo.resolve(), repo / 'held' / name) == _target(held / name)
    finally:
        _remove(links)


def _short(path: Path) -> str | None:
    """The 8.3 short name the system gives ``path``'s last part, or None where it gives none (8.3 names off)."""
    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 32768):
        return None
    short = Path(buffer.value).name
    return None if short.lower() == path.name.lower() else short


@pytest.mark.skipif(not WINDOWS, reason='NTFS 8.3 short names')
@pytest.mark.parametrize('where', ['folder', 'link'])
def test_a_short_name_on_the_way_reads_the_link_in_the_folder_held(tmp_path, where):
    """A short (8.3) name for the folder on the way, or for the link itself: the file system's own alias of an entry in the
    folder held, which git's own lookup reads too; the link read is that entry, in the folder held, and never another."""
    repo = _mkdir(tmp_path / 'repo')
    held = _mkdir(repo / 'heldfoldername-long')
    mine = _mkdir(repo / 'mine')
    link = held / 'linkwithlongname'
    _junction(mine, link)
    try:
        short = _short(held if where == 'folder' else link)
        if short is None:
            pytest.skip('this volume gives no 8.3 short names')
        path = repo / short / link.name if where == 'folder' else held / short
        assert rt._link_inside(repo.resolve(), path) == _target(link)
    finally:
        _remove([link])


def test_the_folder_held_is_named_in_the_form_the_system_gives_and_containment_compares_that_form(tmp_path):
    """``_within`` compares the system's own form of each path, its extended form on Windows, whichever form it is given, part
    by part with no case folded; on Windows a held folder's final path is that extended form."""
    root = tmp_path.resolve()
    assert rt._within(root, root / 'x') and not rt._within(root, root.parent / 'other' / 'x')
    (tmp_path / 'held.py').write_bytes(b'x = 1\n')
    fd = os.open(tmp_path / 'held.py', os.O_RDONLY | getattr(os, 'O_BINARY', 0))
    try:
        where = rt._where(fd)
    finally:
        os.close(fd)
    assert rt._within(root, where) and where.name == 'held.py'
    if WINDOWS:
        assert str(where).startswith(_EXTENDED)
        assert rt._within(root, Path(_EXTENDED + str(root / 'x'))) and rt._within(Path(_EXTENDED + str(root)), root / 'x')
        assert not rt._within(root, Path(_EXTENDED + str(root.parent / (root.name + '.') / 'x')))
        assert not rt._within(root, root.parent / root.name.upper() / 'x')


# --- item 2: a git name is a type that cannot become text by accident --------------------------------------------------------------

#: Each place the inventory finds that turns a name into text for something other than a person or a model, ruled on: what
#: it is for, and why that text is not the rule's to write. Any place not here fails the inventory; an entry no place
#: matches any more fails it too.
_NEVER_SHOWN = "never shown to a person or a model"
ALLOWED = {
    "g6.core.git_name: ''.join((_ESCAPED.get(ch) or _octal(ch) for ch in path))": "the rule itself: the text it quotes",
    "g6.workforce.repo_tests: str.__str__(rel)": ("_on_disk: the name given to the system as given, where its bytes name "
                                                   "another file for git there: looked at, never opened; " + _NEVER_SHOWN),
    "g6.workforce.repo_tests: ctypes.windll.kernel32.GetFullPathNameW(given, size, buffer, None)":
        ("_as_named: the name, below a made-up folder, given to Windows' own rewriting of a path, which says how an open "
         "would read it (review-maskfu4): no disk is looked at; " + _NEVER_SHOWN),
    "g6.context.redact: ''.join(parts)": ("the scrubber reading a name as git gave it for a value in it (review-maskfu2, "
                                          "finding 2): what it builds is the spans it found; " + _NEVER_SHOWN),
    "g6.context.redact: ''.join(parts) + shown[at:]": "the same scrubber's masked text, read for its spans; " + _NEVER_SHOWN,
    "g6.harness.patch_review: rel.rstrip('/') + '/'": "_each_once: a prefix compared with the other names; " + _NEVER_SHOWN,
    "g6.workforce.repo_tests: rel + '/'": "change's ours: a prefix compared with the caller's paths; " + _NEVER_SHOWN,
    "g6.workforce.repo_tests: '/'.join(named[:end])": "_submodule: what a link's digest is of; " + _NEVER_SHOWN,
    "g6.workforce.repo_tests: '/'.join(parts[start:])": ("shadowed: each tail of a changed source's path, compared with a "
                                                         "loaded module's file; " + _NEVER_SHOWN),
    "g6.workforce.repo_tests: '.'.join(parts)": "_import_names: a module's dotted name, compared; " + _NEVER_SHOWN,
    "g6.workforce.repo_tests: str(folder)": "loadable_changes: a package folder, compared with the listing; " + _NEVER_SHOWN,
    "g6.workforce.repo_tests: folder.as_posix()": "loadable_changes: the same folder, as a key; " + _NEVER_SHOWN,
    "g6.workforce.repo_tests: '/'.join([*done, part])": ("_resolve_inside: a key looked up among the links being "
                                                         "copied; " + _NEVER_SHOWN),
    "g6.workforce.repo_tests: '/'.join(resolved)": ("_copy_links: the path a link's target was copied from, a persisted "
                                                    "record's value (links_materialised), kept as git's text as every "
                                                    "persisted field is"),
    "g6.workforce.repo_tests: old + '::'": "run_before's moved: a pytest node id compared with pytest's own; " + _NEVER_SHOWN,
    "g6.safety_engine.weakening: TestWeakeningReport(bool(reasons), tuple(reasons), tuple(checked), (*not_checked, *LIMITS))":
        ("the report's record of the paths it compared (``checked``), kept as data, a persisted field; its reasons name "
         "each path by the rule"),
    # the import-linter check reads a package's files by their paths below the package's folder (parts of the names git
    # listed): the graph keys modules by dotted names and records each import's file as data; the check names each file
    # by the name git listed, by the rule (verification_templates' ``listed``), before any text
    "g6.code.design_governance.graph: '.'.join(parts)": "module_of: a module's dotted name, all identifiers; " + _NEVER_SHOWN,
    "g6.code.design_governance.graph: '.'.join([*base, *([node.module] if node.module else [])])":
        "_resolve: an imported module's dotted name; " + _NEVER_SHOWN,
    "g6.code.design_governance.graph: Graph(package, modules)": "the graph's map of module names to files, data",
    "g6.code.design_governance.graph: Edge(name, hit, path, node.lineno, lazy, guarded)": "an import's file, as data",
    "g6.code.design_governance.graph: path": ("build's refusal of a .py path that names no module: the check gives the graph "
                                              "only paths module_of accepts, so no name git listed reaches it"),
}


def test_every_place_a_git_name_becomes_text_is_the_rule_or_its_bytes_or_ruled_on():
    """The inventory, by type: from the readers of git's listings, the flow of the type through every module it reaches
    (the six repair 2 left open, and each that imports the reader or is handed a name). Each f-string value, ``format``,
    ``%``, ``str()``, ``repr()``, ``join``, ``+`` with text, log call or object made to hold text that may be handed a
    git name puts it there by ``shown_name`` (or ``shown``), gives git its bytes, or is ruled on above."""
    program = _Program(ROOTS)
    assert {'g6.harness.patch_review', 'g6.harness.verification_templates', 'g6.workforce.verify', 'g6.workforce.repo_tests',
            'g6.harness.verification_search', 'g6.harness.verification_proposer'} <= set(program.flows)
    assert program.sinks(ALLOWED) == []
    stale = set(ALLOWED) - program.places()
    assert not stale, f'ruled on, but no place makes this text any more: {sorted(stale)}'


def test_a_git_name_cannot_become_text_by_accident():
    names = _names()
    name = names.GitName(b'odd"name\n\xff.py')
    for make in (str, repr, ascii, lambda one: f'{one}', lambda one: '{}'.format(one), lambda one: '%s' % one,
                 lambda one: '%s and %s' % ('x', one), lambda one: format(one, '>4'), lambda one: f'{one!r}',
                 lambda one: one % ()):
        with pytest.raises(names.GitNameAsText):
            make(name)
    assert names.shown_name(name) == '"odd\\"name\\n\\377.py"' and type(names.shown_name(name)) is str
    assert names.shown(name) == names.shown_name(name) and names.shown('caller\\path.py') == 'caller\\path.py'
    assert name.raw == b'odd"name\n\xff.py'
    assert name == 'odd"name\n\udcff.py' and hash(name) == hash('odd"name\n\udcff.py') and 'x' + name == 'xodd"name\n\udcff.py'
    assert json.loads(json.dumps({name: [name]})) == {'odd"name\n\udcff.py': ['odd"name\n\udcff.py']}   # a persisted field
    for again in (pickle.loads(pickle.dumps(name)), copy.deepcopy(name), copy.copy(name)):
        assert type(again) is names.GitName and again.raw == name.raw
    assert Path('repo') / names.GitName(b'a.py') == Path('repo', 'a.py')
    with pytest.raises(TypeError):
        names.GitName('text, not the bytes git gave')
    assert not issubclass(names.GitNameAsText, (TypeError, ValueError))   # no handler of untrusted input reads it as one


def test_the_readers_of_gits_listings_make_the_type_from_gits_own_bytes():
    names = _names()
    listed = rt._paths(b'a.py\0odd\xff.py\0')
    assert [type(one) for one in listed] == [names.GitName] * 2 and listed[1].raw == b'odd\xff.py'
    entries = rt._entries(b'100644 ' + b'a' * 40 + b' 0\todd\tname.py\0')
    assert entries == [('100644 ' + 'a' * 40 + ' 0', 'odd\tname.py')] and type(entries[0][1]) is names.GitName
    tag, = rt._tagged(b'h marked.py\0')
    assert tag == ('h', 'marked.py') and type(tag[1]) is names.GitName
    records, _ = rt._listing(b':100644 100644 ' + b'a' * 40 + b' ' + b'0' * 40 + b' M\0odd\xff.py\0\0', 40)
    assert type(records[0][0]) is names.GitName and records[0][0].raw == b'odd\xff.py'


def _repo(tmp_path: Path, entries: dict[bytes, bytes]) -> tuple[Path, str]:
    """A scratch repository whose base commit holds ``entries`` (flat names), checked out as HEAD and the index; no working
    file is written. Its base commit."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    base = _base(repo, entries)
    git_bytes(repo, 'update-ref', 'HEAD', base)   # only this test's scratch repository
    git_bytes(repo, 'read-tree', base)
    return repo, base


def test_a_change_read_from_git_holds_the_type_wherever_it_names_a_file(tmp_path):
    names = _names()
    repo, base = _repo(tmp_path, {b'calc.py': b'value = 0\n', b'test_calc.py': b'import calc\n\ndef test_a():\n    pass\n'})
    (repo / 'calc.py').write_bytes(b'value = 1\n')
    (repo / 'test_calc.py').write_bytes(b'import calc\n\ndef test_a():\n    assert calc.value\n')
    (repo / 'new.py').write_bytes(b'x = 1\n')
    change = rt.change(repo, base)
    assert set(change.files) == {'calc.py', 'test_calc.py', 'new.py'}
    held = [*change.files, *change.sources, *change.before, *change.after, *rt.derive_tests(change)[0],
            *(one.rel for one in rt.diff_read(change).files), *rt.related_texts(change)[0]]
    assert held and all(type(one) is names.GitName for one in held), [type(one).__name__ for one in held]


def test_each_text_a_git_name_reaches_names_it_by_the_rule(tmp_path):
    """Reports, refusals and what a model reads, for names the rule quotes: the wording as it was, the name in its quoted
    form, and no ``GitNameAsText`` on the way."""
    from g6.harness import patch_report, patch_review, verification_proposer
    from g6.safety_engine.weakening import detect_test_weakening
    from g6.workforce import verify

    names = _names()
    odd = names.GitName(b'tests/test_odd"\x07\xff.py')
    shown = names.shown_name(odd)
    source = 'def test_a():\n    assert 1\n'
    assert verify.weakened_tests({odd: source}, {}) == [f'{shown}: test file deleted']
    assert any(reason.startswith(f'{shown}: ') for reason in detect_test_weakening({odd: source}, {}, tests=[odd]).reasons)
    assert rt._moved({odd: 'a'}, {odd: 'b'}) == [shown]
    with pytest.raises(rt.RepoRefused) as refused:
        rt._index_path(tmp_path, names.GitName(b'.git/x\x07'))
    assert repr(names.shown_name(names.GitName(b'.git/x\x07'))) in str(refused.value)
    assert names.shown_name(odd) in patch_report.code(odd, 200)
    repo, base = _repo(tmp_path, {b'calc.py': b'value = 0\n'})
    runner, link = names.GitName(b'odd\x07/conftest.py'), names.GitName(b'odd\x1b/linked.py')
    change = rt.Change(repo, base, base, base, (odd, runner, link), {}, {}, (odd,), (runner,), outside_links=(link,))
    assert patch_review._runner_changes(change) == [names.shown_name(runner)]
    context = verification_proposer.context(change, [], max_chars=20_000)
    assert f'{names.shown_name(link)}: a link to a path outside the repository, never read' in context.left_out
    assert f'{shown}: could not be read' in context.left_out
    from g6.harness import verification_templates as vt
    with pytest.raises(ValueError) as unread:
        vt.Tree(repo, change, frozenset({odd}), frozenset()).text(odd, vt.PATCH)
    assert str(unread.value) == f'{shown} is not a file of at most {vt.MAX_MODULE_BYTES} bytes'


# --- item 3: every name-to-path crossing in repo_tests is the one disk rule, every containment decision the one rule ---------

def test_every_name_to_path_crossing_in_repo_tests_is_the_one_disk_rule():
    """From the code, by type: in ``repo_tests``, no git name as git gave it is joined to a folder, or given to a look or an
    open, other than by the disk rule (``_on_disk``, through ``_in`` and ``_disk``)."""
    program = _Program(ROOTS)
    assert _disk_sinks(program.flows['g6.workforce.repo_tests']) == []


#: The comparisons of paths left in ``repo_tests``, none a containment decision on a git name: what each compares.
_COMPARED = {
    'python_files': "the caller's test ids' folder against the top, where pytest's configuration search stops",
    '_config_args': "the caller's test ids' common folder",
    '_run': ("where the interpreter G6 runs is, against the repository's own environment folders, and the interpreter's "
             "environment against the folders a confined run reads"),
    '_import_names': "a name against an import root, as pure paths: a module's dotted name, no file looked at",
    'outside_temp': "G6's own scratch folder against the repository, so a copy is made outside it",
}


def test_every_containment_decision_on_a_name_in_repo_tests_is_the_one_rule():
    functions: dict[str, list] = {}
    for node in ast.walk(ast.parse(inspect.getsource(rt))):
        if isinstance(node, ast.FunctionDef):
            functions.setdefault(node.name, []).append(node)

    def calls(name: str) -> set[str]:   # every function of that name (``result`` is one of several)
        return {_called(call) for one in functions[name] for call in ast.walk(one) if isinstance(call, ast.Call)}

    for name in ('_read_inside', '_link_inside', '_leads_out', 'links_outside', '_test_id', '_base_revision',
                 'repository_copy', '_submodule'):
        assert '_within' in calls(name) and not calls(name) & {'is_relative_to', 'commonpath', 'contained'}, name
    compared = {name for name in functions if calls(name) & {'is_relative_to', 'commonpath', 'samefile'}}
    assert compared == set(_COMPARED), compared ^ set(_COMPARED)


def _surrogate_repo(tmp_path: Path, name: str, before: bytes, now: bytes) -> tuple[Path, str]:
    """A repository whose base commit holds ``calc.py`` and ``name``, a name holding a lone surrogate, which the system holds
    in UTF-16 and git lists as its three WTF-8 bytes; the working file of ``name`` holds ``now``. Its base commit."""
    repo, base = _repo(tmp_path, {b'calc.py': b'value = 0\n', name.encode('utf-8', 'surrogatepass'): before})
    (repo / 'calc.py').write_bytes(b'value = 0\n')
    Path(_EXTENDED + str(repo / name)).write_bytes(now)
    return repo, base


_BEFORE, _NOW = b'def test_a():\n    assert 1\n', b'def test_a():\n    assert 2\n'


@pytest.mark.skipif(not WINDOWS, reason='Windows UTF-16 file names and git for Windows\' WTF-8 listing')
def test_a_test_file_git_names_by_wtf8_bytes_is_read_where_git_looks(tmp_path):
    name = 'test_odd-\udcff.py'
    repo, base = _surrogate_repo(tmp_path, name, _BEFORE, _NOW)
    change = rt.change(repo, base)
    rel = next(one for one in change.files if one != 'calc.py')
    assert rt._on_disk(rel) == (name, True)
    assert change.before.get(rel) == _BEFORE.decode(), 'the base text of a test file git names by its WTF-8 bytes was never read'
    assert change.after.get(rel) == _NOW.decode(), 'the test file was read by a spelling the system does not hold'
    before, after = rt.related_texts(change)
    assert (before.get(rel), after.get(rel)) == (_BEFORE.decode(), _NOW.decode())


@pytest.mark.skipif(not WINDOWS, reason='Windows UTF-16 file names and git for Windows\' WTF-8 listing')
def test_the_snapshot_hashes_a_file_git_names_by_wtf8_bytes_where_git_looks(tmp_path):
    import hashlib
    repo, _ = _surrogate_repo(tmp_path, 'odd-\udcff.py', b'x = 1\n', b'x = 2\n')
    found = rt.snapshot(repo)
    rel = next(key for key in found if key.endswith('.py') and key != 'calc.py')
    assert found[rel] == hashlib.sha256(b'x = 2\n').hexdigest(), 'the run would not see this file change: it is hashed as absent'


@pytest.mark.skipif(not WINDOWS, reason='Windows UTF-16 file names and git for Windows\' WTF-8 listing')
def test_an_index_entry_beyond_a_link_named_by_wtf8_bytes_is_refused(tmp_path):
    repo, base = _repo(tmp_path, {b'calc.py': b'value = 0\n'})
    outside = _mkdir(tmp_path / 'outside')
    folder = 'linked-\udcff'
    _junction(outside, repo / folder)
    try:
        blob = git_bytes(repo, 'hash-object', '-w', '--stdin', data=b'x = 1\n').strip()
        git_bytes(repo, 'update-index', '--index-info',
                  data=b'100644 ' + blob + b'\t' + (folder + '/x.py').encode('utf-8', 'surrogatepass') + b'\n')
        with pytest.raises(rt.RepoRefused) as refused:
            rt._tracked_new(repo, base)
        assert refused.value.code == 'G6_E_GIT_REPO_INVALID', 'an index entry beyond a link was read through it'
    finally:
        _remove([repo / folder])


@pytest.mark.skipif(not WINDOWS, reason='Windows UTF-16 file names and git for Windows\' WTF-8 listing')
def test_the_repository_copy_holds_a_file_git_names_by_wtf8_bytes(tmp_path, monkeypatch):
    import tempfile
    import time
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(scratch))   # the copy is made in this test's own folder
    name = 'odd-\udcff.py'
    repo, _ = _surrogate_repo(tmp_path, name, b'x = 1\n', b'x = 2\n')
    with rt.repository_copy(repo, deadline=time.monotonic() + 120) as copy:
        held = Path(_EXTENDED + str(copy / name))
        assert held.is_file() and held.read_bytes() == b'x = 2\n', 'the copy left out a tracked file git names by its bytes'


@pytest.mark.skipif(not WINDOWS, reason='Windows case sensitive NTFS folder')
def test_a_link_out_to_a_sibling_that_differs_only_in_case_is_outside(tmp_path, tmp_path_factory):
    import _winapi
    _case_sensitive(tmp_path, tmp_path_factory.getbasetemp())
    repo, sibling = tmp_path / 'repo', tmp_path / 'Repo'
    repo.mkdir()
    sibling.mkdir()
    (sibling / 'payload.py').write_bytes(b'outside = 1\n')
    _winapi.CreateJunction(str(sibling), str(repo / 'linked'))
    try:
        assert rt.links_outside(repo, 'linked/payload.py'), 'a sibling differing from the repository only in case passed as inside it'
        assert not rt.links_outside(repo, 'calc.py')
    finally:
        os.rmdir(repo / 'linked')
