# SPDX-License-Identifier: Apache-2.0
"""weakening.py -- did a change weaken the tests? A static comparison of file text, before and after; nothing runs.

``detect_test_weakening(before, after)`` takes each side as a mapping of repository-relative path to file text, or as
a directory, which :func:`read_snapshot` reads into one. Both sides must cover the same paths: a path only in
``before`` was deleted, a path only in ``after`` was added.

The archive's heuristic read only test files that existed before the change, recognised a test directory by the
substring ``"/tests/"`` (so ``tests/conftest.py`` was not one), counted the bare word ``unittest`` as an assertion
and never looked for a skip marker (audit 007, SE-005). This module compares:

* test files (``test_*.py``, ``*_test.py``, any ``.py`` under a ``tests``, ``test`` or ``testing`` directory): a
  deleted file; a removed or renamed test; fewer tests; a check removed, changed or put where it cannot fail (an
  ``assert``, an ``assert*`` / ``fail`` / ``raises`` / ``warns`` call, a ``parametrize`` case; a catch-all handler or
  ``suppress`` around it); an early return; a skip, xfail, importorskip, ``pytestmark``, mock or autouse fixture added
  outside a new test; a module-level patch, which runs when pytest imports the file. A new test file is read for
  module-level patches and for a name that shadows a module the existing tests import.
* ``conftest.py`` in any directory, ``sitecustomize.py`` and ``usercustomize.py``: any addition, removal or change to
  the code (a comment or layout change is not one), naming the hooks, autouse fixtures and ``collect_ignore`` lists it
  adds or changes, and the same check comparison as a test file.
* pytest configuration in any directory (``pytest.ini``, ``.pytest.ini``, ``[tool.pytest]`` in ``pyproject.toml``,
  ``[tool:pytest]`` in ``setup.cfg``): any change to the pytest settings (the rest of ``pyproject.toml`` is not read);
  test-runner and CI configuration (``tox.ini``, ``noxfile.py``, ``.github/workflows/``, ``.gitlab-ci.yml``,
  ``.circleci/``, ``azure-pipelines.yml``, ``Jenkinsfile``): any change to the file.

A finding is evidence that the tests may now prove less; it is never ``verified`` and never proof. A clean report says
only that none of these signs was found in the files it lists as ``checked``, and ``not_checked`` lists what it could
not see: a static scan cannot show that the tests are as strong as before.
"""
from __future__ import annotations

import ast
import configparser
import os
import posixpath
import tomllib
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from g6.core.reliability_envelope import envelope_fields, warning_card

DETECTED = "G6_E_TEST_WEAKENING_DETECTED"
STATIC_ONLY = "G6_E_TEST_WEAKENING_STATIC_ONLY"
NOTHING_COMPARED = "G6_E_TEST_WEAKENING_NOTHING_COMPARED"
INPUT_INVALID = "G6_E_TEST_WEAKENING_INPUT_INVALID"

#: What a static comparison cannot establish, whatever it finds. Every report lists these in ``not_checked``.
LIMITS: tuple[str, ...] = (
    "Nothing was run: whether the tests pass, or are collected at all, is not established.",
    "A check whose text is unchanged but proves less is not seen: a fixture or test data made trivial, a tautology "
    "such as `assert x or True`, a helper outside the files given.",
    "Test files outside pytest's default names (test_*.py, *_test.py) and outside a tests/ directory are not read; a "
    "custom python_files pattern is not followed.",
    "Options and plugins pytest gets from outside these files (command line, environment, installed plugins) are not "
    "seen.",
)

Source = Mapping[str, str | bytes] | str | os.PathLike

_TEST_DIRS = frozenset({"tests", "test", "testing"})
_STARTUP = {"conftest.py": "pytest runs it for every test under {where}",
            "sitecustomize.py": "Python runs it at start-up when {where} is on the path",
            "usercustomize.py": "Python runs it at start-up when {where} is on the path"}
#: pytest config files and the section pytest reads; pyproject.toml is read as TOML (``[tool.pytest]``).
_INI_SECTION = {"pytest.ini": "pytest", ".pytest.ini": "pytest", "setup.cfg": "tool:pytest"}
_RUNNER_FILES = frozenset({"tox.ini", "noxfile.py", ".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile"})
_SKIP_DIRS = frozenset({".git", ".hg", "__pycache__", ".venv", "venv", "node_modules", ".tox", ".nox", ".mypy_cache",
                        ".pytest_cache", ".ruff_cache"})

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_CATCH_ALL = frozenset({"AssertionError", "Exception", "BaseException"})
_CHECK_CALLS = frozenset({"fail", "raises", "warns", "deprecated_call"})
_SKIPS = frozenset({"skip", "skipif", "xfail", "importorskip", "pytestmark", "skipTest", "SkipTest", "skipIf",
                    "skipUnless", "expectedFailure"})
_MOCKS = frozenset({"mock", "Mock", "MagicMock", "AsyncMock", "patch", "monkeypatch", "mocker"})
_COLLECTION_NAMES = frozenset({"collect_ignore", "collect_ignore_glob", "pytest_plugins"})
_HOOK_EFFECT = {
    "pytest_collection_modifyitems": "can deselect or drop collected tests",
    "pytest_ignore_collect": "can stop files from being collected",
    "pytest_collect_file": "can change which files are collected",
    "pytest_pycollect_makeitem": "can change which tests are collected",
    "pytest_generate_tests": "can parametrize a test with no cases",
    "pytest_runtest_setup": "can skip tests before they run",
    "pytest_runtest_call": "can replace the test call",
    "pytest_pyfunc_call": "can replace the test call",
    "pytest_runtest_makereport": "can rewrite a test's outcome",
    "pytest_report_teststatus": "can rewrite how an outcome is reported",
    "pytest_sessionfinish": "can change the exit status",
    "collect_ignore": "drops paths from collection",
    "collect_ignore_glob": "drops paths from collection",
    "pytest_plugins": "loads plugins for every test",
}


@dataclass(frozen=True)
class TestWeakeningReport:
    """What :func:`detect_test_weakening` found, what it compared and what it could not see.

    ``weakened`` is True when any reason was found. ``checked`` lists the test, conftest and configuration paths it
    compared; ``not_checked`` lists what it could not compare and the limits of any static scan (``LIMITS``).
    """

    __test__ = False  # pytest: a report, not a test class

    weakened: bool
    reasons: tuple[str, ...] = ()
    checked: tuple[str, ...] = ()
    not_checked: tuple[str, ...] = LIMITS

    @property
    def completion_state(self) -> str:
        # audit 007, SE-005: a detection is evidence for a reviewer, and a clean static scan is at best a qualified
        # draft. Nothing here returns "verified".
        return "blocked-escalated" if self.weakened else "qualified-draft"

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        """The report as a library envelope (``g6.core.reliability_envelope``) with a ``G6_E_*`` warning card."""
        if self.weakened:
            code, action = DETECTED, "Do not land the change on this evidence; a reviewer reads the reasons and decides."
            message = f"{len(self.reasons)} sign(s) that the change weakens the tests; first: {self.reasons[0]}"
        elif not self.checked:
            code, action = NOTHING_COMPARED, "Pass the files the change touches, before and after, or two snapshots."
            message = "No test file, conftest.py or test configuration was among the files given; nothing was compared."
        else:
            code, action = STATIC_ONLY, "Run the tests, and review what this scan did not check."
            message = (f"No sign of weakening in the {len(self.checked)} file(s) compared; a static scan does not show "
                       "that the tests are as strong as before.")
        card = warning_card(code, message, action)
        card["severity"] = "blocked" if self.weakened else "amber"
        card["risks"] = list(self.reasons if self.weakened else self.not_checked)
        evidence = [{"kind": "test_weakening_scan", "basis": "static comparison of file text; nothing was run",
                     "checked": list(self.checked), "reasons": list(self.reasons),
                     "not_checked": list(self.not_checked)}]
        envelope = envelope_fields(reliability_label=self.completion_state, warning_card=card, evidence=evidence,
                                   request_id=request_id, run_id=run_id, degradation_reason=message)
        return {**asdict(self), **envelope}


def detect_test_weakening(before: Source, after: Source, *, tests: Collection[str] = ()) -> TestWeakeningReport:
    """Compare the tests and their configuration before and after a change; see the module docstring for the rules.

    ``before`` and ``after`` are each a mapping of path to text (``bytes`` is read as UTF-8) or a directory. Paths
    are compared after normalisation (forward slashes, ``.`` and ``..`` folded). ``tests``: Python files to compare as
    test files whatever they are called (a repository's own ``python_files``, a file a test id names; patch review
    gives them). Raises ``TypeError`` for any other input, and ``OSError`` when a snapshot directory cannot be read.
    """
    old, new = _files(before, "before"), _files(after, "after")
    named = frozenset(_normal(path) for path in tests if path.lower().endswith(".py"))

    def kind_of(path: str) -> str:   # the path's kind, and a named Python file is a test file
        kind = _kind(path)
        return "test" if kind in ("other", "data") and path in named else kind

    added_tests = any(kind_of(p) == "test" for p in new.keys() - old.keys())
    imported = _imported(old, named) if added_tests else frozenset()
    reasons: list[str] = []
    checked: list[str] = []
    unread: list[str] = []
    data: list[str] = []
    others = 0
    for path in sorted(old.keys() | new.keys()):
        kind, a, b = kind_of(path), old.get(path), new.get(path)
        if kind == "other":
            others += 1
            continue
        if kind == "data":
            if a != b:
                data.append(path)
            continue
        checked.append(path)
        if a == b:
            continue
        if kind == "runner":
            reasons.append(f"{path}: test-runner or CI configuration {_how(a, b)}: it decides whether and how the "
                           "tests run")
        elif kind == "config":
            reasons += _config_reasons(path, a, b, unread)
        else:
            reasons += _python_reasons(path, kind, a, b, imported, unread)
    not_checked = list(unread)
    if data:
        listed = ", ".join(data[:5]) + (", ..." if len(data) > 5 else "")
        not_checked.append(f"{len(data)} non-Python file(s) under a test directory changed and were not compared "
                           f"(expected outputs, fixtures): {listed}")
    if others:
        not_checked.append(f"{others} other file(s) were not examined: not tests, conftest.py or test configuration")
    return TestWeakeningReport(bool(reasons), tuple(reasons), tuple(checked), (*not_checked, *LIMITS))


def read_snapshot(root: str | os.PathLike) -> dict[str, str]:
    """The files under ``root`` that :func:`detect_test_weakening` compares, as posix path relative to ``root`` -> text.

    Version-control, cache and virtual-environment directories are not entered (``.git``, ``__pycache__``, ``.venv``,
    ``node_modules``, ...). A directory or file that cannot be read raises ``OSError``: an unread test is never
    reported as unchanged. Reading never executes anything.
    """
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"{INPUT_INVALID}: {os.fspath(root)!r} is not a directory")
    out: dict[str, str] = {}

    def fail(exc: OSError) -> None:
        raise exc

    for dirpath, dirnames, filenames in os.walk(base, onerror=fail):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            file = Path(dirpath) / name
            rel = file.relative_to(base).as_posix()
            if _kind(rel) != "other":
                out[rel] = file.read_text(encoding="utf-8", errors="replace")
    return out


# --- what a path is --------------------------------------------------------------------------------------------------

def _normal(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/"))


def _kind(path: str) -> str:
    """``test``, ``startup``, ``config``, ``runner``, ``data`` or ``other``, from the path's segments (lower-cased).

    audit 007, SE-005: the archive wanted ``"/tests/"`` in the path, so a repository-relative ``tests/conftest.py``
    did not count; here a ``tests`` segment counts wherever it is, and ``conftest.py`` counts in any directory.
    """
    parts = path.lower().split("/")
    name, dirs = parts[-1], parts[:-1]
    if name in _STARTUP:
        return "startup"
    if name in _INI_SECTION or name == "pyproject.toml":
        return "config"
    if name in _RUNNER_FILES or ".circleci" in dirs or any(dirs[i:i + 2] == [".github", "workflows"]
                                                            for i in range(len(dirs))):
        return "runner"
    in_tests = any(d in _TEST_DIRS for d in dirs)
    if name.endswith(".py") and (in_tests or name.startswith("test_") or name.endswith("_test.py")):
        return "test"
    return "data" if in_tests else "other"


def _how(a: str | None, b: str | None) -> str:
    return "added" if a is None else "removed" if b is None else "changed"


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _files(side: Source, label: str) -> dict[str, str]:
    if isinstance(side, Mapping):
        out: dict[str, str] = {}
        for key, text in side.items():
            if not isinstance(key, str) or not key.strip():
                raise TypeError(f"{INPUT_INVALID}: every {label} path must be a non-empty string, got {key!r}")
            if isinstance(text, bytes):
                text = text.decode("utf-8", errors="replace")
            if not isinstance(text, str):
                raise TypeError(f"{INPUT_INVALID}: {label}[{key!r}] must be the file's text, got {type(text).__name__}")
            out[_normal(key)] = text
        return out
    if isinstance(side, str | os.PathLike):
        return read_snapshot(side)
    raise TypeError(f"{INPUT_INVALID}: {label} must be a mapping of path to text or a directory, "
                    f"got {type(side).__name__}")


# --- configuration ---------------------------------------------------------------------------------------------------

def _pytest_settings(path: str, text: str) -> Any:
    """The pytest settings one config file holds (None when it holds none). ``ValueError`` when it cannot be read."""
    name = posixpath.basename(path.lower())
    if name == "pyproject.toml":
        try:
            tool = tomllib.loads(text).get("tool")
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(str(exc)) from exc
        return tool.get("pytest") if isinstance(tool, dict) else None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ValueError(str(exc).splitlines()[0]) from exc
    section = _INI_SECTION[name]
    if parser.has_section(section):
        return dict(parser[section])
    # pytest.ini is pytest's config file whenever it exists, even with no [pytest] section.
    return {} if section == "pytest" else None


def _config_reasons(path: str, a: str | None, b: str | None, unread: list[str]) -> list[str]:
    try:
        settings_after = None if b is None else _pytest_settings(path, b)
    except ValueError as exc:
        return [f"{path}: cannot be read after the change ({exc}); a static scan cannot tell what pytest would do"]
    try:
        settings_before = None if a is None else _pytest_settings(path, a)
    except ValueError:
        unread.append(f"{path}: could not be read before the change, so its pytest settings were not compared")
        return []
    if settings_before == settings_after:
        return []
    how = "added" if settings_before is None else "removed" if settings_after is None else "changed"
    return [f"{path}: pytest configuration {how}: it decides what pytest collects and how it runs"]


# --- Python files ----------------------------------------------------------------------------------------------------

def _python_reasons(path: str, kind: str, a: str | None, b: str | None, imported: frozenset[str],
                    unread: list[str]) -> list[str]:
    head: list[str] = []
    if kind == "startup":
        name = posixpath.basename(path)
        where = f"{posixpath.dirname(path)}/" if posixpath.dirname(path) else "the root"
        head.append(f"{path}: {name} {_how(a, b)}: {_STARTUP[name.lower()].format(where=where)}")
    if b is None:
        return head + ([f"{path}: test file deleted"] if kind == "test" else [])
    try:
        new_tree = ast.parse(b)
    except SyntaxError as exc:
        return head + [f"{path}: does not parse after the change (line {exc.lineno}: {exc.msg}); its tests cannot be "
                       "collected or compared"]
    try:
        old_tree = ast.parse(a) if a is not None else ast.Module(body=[], type_ignores=[])
    except SyntaxError:
        unread.append(f"{path}: did not parse before the change, so it was not compared")
        return head
    if a is not None and ast.dump(old_tree) == ast.dump(new_tree):
        return []  # only comments or layout changed
    old, new = _facts(old_tree), _facts(new_tree)
    found = head
    if kind == "startup":
        found += [f"{path}: adds or changes {name}, which {_HOOK_EFFECT.get(name, _effect_of(name))}"
                  for name, src in new.hooks.items() if old.hooks.get(name) != src]
    found += [f"{path}: module-level patch added ({_short(stmt)}): it runs when the file is imported, before the "
              "tests" for stmt in new.patches - old.patches]
    if a is None and kind == "test":
        # audit 007, SE-005: the archive never read a file that did not exist before the change.
        stem = posixpath.basename(path)[:-3]
        if stem != "__init__" and stem in imported:
            found.append(f"{path}: new module named like {stem!r}, which the existing tests import: it can shadow "
                         "the module they test")
        return found  # a new test cannot weaken an old one; its own skips and mocks are its own
    return found + _test_reasons(path, old, new) + _marker_reasons(path, old, new)


def _effect_of(name: str) -> str:
    return "is a pytest hook: it can change what is collected or reported" if name.startswith("pytest_") else \
        "is an autouse fixture: it runs around every test it reaches"


@dataclass
class _Facts:
    checks: dict[str, Counter[str]] = field(default_factory=dict)  # function (qualified) -> its checks
    tests: dict[str, int] = field(default_factory=dict)            # collected-looking test -> returns in its body
    test_markers: dict[str, Counter[str]] = field(default_factory=dict)
    markers: Counter[str] = field(default_factory=Counter)        # skip / mock / autouse shapes in the whole file
    patches: Counter[str] = field(default_factory=Counter)        # module-level patch statements
    hooks: dict[str, str] = field(default_factory=dict)           # pytest hooks, autouse fixtures, collect_ignore


def _facts(tree: ast.Module) -> _Facts:
    facts = _Facts(markers=_markers(tree))

    def function(qual: str, fn: ast.FunctionDef | ast.AsyncFunctionDef, collected: bool) -> None:
        facts.checks[qual] = _checks(fn)
        if collected and fn.name.startswith("test"):
            facts.tests[qual] = _returns(fn)
            facts.test_markers[qual] = _markers(fn)

    def klass(prefix: str, cls: ast.ClassDef) -> None:
        collected = cls.name.startswith("Test") or any(_name(b).endswith("TestCase") for b in cls.bases)
        for node in cls.body:
            if isinstance(node, _FUNCS):
                function(f"{prefix}{cls.name}::{node.name}", node, collected)
            elif isinstance(node, ast.ClassDef):
                klass(f"{prefix}{cls.name}::", node)

    module_level: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, _FUNCS):
            function(node.name, node, True)
            if node.name.startswith("pytest_") or _autouse(node):
                facts.hooks[node.name] = ast.unparse(node)
        elif isinstance(node, ast.ClassDef):
            klass("", node)
        else:
            module_level.append(node)
    facts.checks["<module>"] = sum((_checks(n) for n in module_level), Counter())
    for node in module_level:
        for name in sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & _COLLECTION_NAMES):
            facts.hooks[name] = facts.hooks.get(name, "") + ast.unparse(node) + "\n"
        facts.patches.update(ast.unparse(n) for n in ast.walk(node) if _is_patch(n))
    return facts


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def _catches_all(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_catches_all(e) for e in node.elts)
    return _name(node) in _CATCH_ALL


def _cases(call: ast.Call) -> list[str]:
    """One check per ``parametrize`` case, so a dropped case shows and an added one does not."""
    kw = {k.arg: k.value for k in call.keywords}
    names = call.args[0] if call.args else kw.get("argnames")
    values = call.args[1] if len(call.args) > 1 else kw.get("argvalues")
    label = ast.unparse(names) if names is not None else "?"
    if isinstance(values, ast.List | ast.Tuple):
        return [f"parametrize {label}: {ast.unparse(v)}" for v in values.elts]
    return [f"parametrize {ast.unparse(call)}"]


def _checks(root: ast.AST) -> Counter[str]:
    """Every check under ``root`` that can still fail: one under a catch-all handler or ``suppress`` is no check."""
    found: Counter[str] = Counter()

    def visit(node: ast.AST, guarded: bool) -> None:
        if isinstance(node, ast.Assert):
            if not guarded:
                found["assert " + ast.unparse(node.test)] += 1  # the message is not part of the check
            return
        if isinstance(node, ast.Call):
            name = _name(node.func)
            if name == "parametrize":
                found.update(_cases(node))
            elif not guarded and (name.startswith("assert") or name in _CHECK_CALLS):
                # audit 007, SE-005: the archive's regex counted the bare word "unittest" as an assertion.
                found[ast.unparse(node)] += 1
        if isinstance(node, ast.Try | ast.TryStar):
            swallows = any(_catches_all(h.type) for h in node.handlers)
            for stmt in node.body:
                visit(stmt, guarded or swallows)
            for part in (*node.handlers, *node.orelse, *node.finalbody):
                visit(part, guarded)
            return
        if isinstance(node, ast.With | ast.AsyncWith):
            swallows = any(_name(i.context_expr) == "suppress"
                           and any(_catches_all(arg) for arg in getattr(i.context_expr, "args", ()))
                           for i in node.items)
            for item in node.items:
                visit(item, guarded)
            for stmt in node.body:
                visit(stmt, guarded or swallows)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(root, False)
    return found


def _returns(fn: ast.AST) -> int:
    """Returns in the test's own body; one in a function or class it defines ends that, not the test."""
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    return sum(0 if isinstance(child, nested) else int(isinstance(child, ast.Return)) + _returns(child)
               for child in ast.iter_child_nodes(fn))


def _markers(root: ast.AST) -> Counter[str]:
    """Skip, xfail, mock and autouse shapes where they are used (an import alone patches nothing)."""
    found: Counter[str] = Counter()
    for node in ast.walk(root):
        if isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.keyword) and node.arg == "autouse":
            name = "" if isinstance(node.value, ast.Constant) and not node.value.value else "autouse"
        else:
            continue
        if name in _SKIPS or name in _MOCKS or name == "autouse":
            found[name] += 1
    return found


def _autouse(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(isinstance(d, ast.Call)
               and any(k.arg == "autouse" and not (isinstance(k.value, ast.Constant) and not k.value.value)
                       for k in d.keywords)
               for d in fn.decorator_list)


def _is_patch(node: ast.AST) -> bool:
    """A statement that changes another module's state when the file is imported."""
    if isinstance(node, ast.Assign | ast.Delete):
        targets = node.targets
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets = [node.target]
    else:
        return isinstance(node, ast.Call) and _name(node.func) in ("setattr", "delattr", "start", "setenv", "putenv")
    return any(isinstance(t, ast.Attribute)
               or (isinstance(t, ast.Subscript) and _name(t.value) in ("modules", "environ")) for t in targets)


_MARKER_NOTE = {**{n: "a skip or expected failure: the tests it reaches stop counting" for n in _SKIPS},
                **{n: "a mock or patch: the tests it reaches may stop exercising the real code" for n in _MOCKS},
                "autouse": "an autouse fixture: it runs around every test in the file"}


def _test_reasons(path: str, old: _Facts, new: _Facts) -> list[str]:
    out = [f"{path}: test removed or renamed: {t}" for t in old.tests if t not in new.tests]
    if len(new.tests) < len(old.tests):
        out.append(f"{path}: fewer tests: {len(old.tests)} -> {len(new.tests)}")
    for scope, before in old.checks.items():
        if scope in old.tests and scope not in new.tests:
            continue  # reported above
        after = new.checks.get(scope)
        if after is None:
            if before:
                out.append(f"{path}: {scope} removed, and its {sum(before.values())} check(s) with it")
            continue
        lost = before - after
        if lost:
            more = sum(lost.values()) - 1
            out.append(f"{path}: {scope}: a check was removed, changed or can no longer fail: {_short(next(iter(lost)))}"
                       + (f" (and {more} more)" if more else ""))
    out += [f"{path}: {t}: early return added" for t, n in old.tests.items() if t in new.tests and new.tests[t] > n]
    return out


def _marker_reasons(path: str, old: _Facts, new: _Facts) -> list[str]:
    now = Counter(new.markers)
    for test, markers in new.test_markers.items():
        if test not in old.tests:
            now -= markers  # a new test's own skip or mock weakens no test that existed
    return [f"{path}: adds {name} ({_MARKER_NOTE[name]})" for name in sorted(now) if now[name] > old.markers[name]]


def _imported(files: Mapping[str, str], named: frozenset[str] = frozenset()) -> frozenset[str]:
    """Top-level module names the test and conftest files import (absolute imports only); ``named``: files to read
    as test files whatever they are called."""
    names: set[str] = set()
    for path, text in files.items():
        if _kind(path) not in ("test", "startup") and path not in named:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return frozenset(names)


__all__ = ["DETECTED", "INPUT_INVALID", "LIMITS", "NOTHING_COMPARED", "STATIC_ONLY", "TestWeakeningReport",
           "detect_test_weakening", "read_snapshot"]
