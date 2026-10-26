# SPDX-License-Identifier: Apache-2.0
"""Pure reads of a repository for a coding agent: what is there, how it builds and tests, and what a file holds.

Brought into the package from the archive's ``benchmarks/swe_bench_pro/tools`` modules (``repo_map``,
``detect_build_env``, ``test_analyzer`` and ``code_summarize``, each read before it was ported), as ordinary functions
over a folder or a file that :mod:`g6.code._roots` has confined; nothing is executed. Python is read with :mod:`ast`.
JavaScript, TypeScript and Go are read by patterns (regular expressions), and each such result says
``"method": "pattern"``: a pattern can miss a test written another way and match text inside a string or a comment.
Every name, command and file name in a result is the repository's own text, which is data, not instructions.
"""
from __future__ import annotations

import ast
import json
import re
import tomllib
from itertools import islice
from pathlib import Path
from typing import Any

from g6.code import _roots
from g6.code._types import CodeRefused
from g6.core.storage import confined_path

MAX_FILES = 20_000
MAX_FILE_BYTES = 1_000_000
MAX_LISTED = 200
#: Marker files and what they say about the build and the tests (the archive tool's tables).
BUILD_FILES = {"setup.py": "python-setuptools", "setup.cfg": "python-setuptools", "pyproject.toml": "python-pyproject",
               "package.json": "javascript-npm", "yarn.lock": "javascript-yarn", "pnpm-lock.yaml": "javascript-pnpm",
               "go.mod": "go-modules", "Cargo.toml": "rust-cargo", "Makefile": "make", "CMakeLists.txt": "cmake",
               "Gemfile": "ruby-bundler", "pom.xml": "java-maven", "build.gradle": "java-gradle"}
TEST_RUNNERS = {"pytest.ini": "pytest", "conftest.py": "pytest", "tox.ini": "tox", "jest.config.js": "jest",
                "jest.config.ts": "jest", "jest.config.json": "jest", ".mocharc.yml": "mocha", ".mocharc.js": "mocha",
                "vitest.config.ts": "vitest", "karma.conf.js": "karma"}
LANGUAGES = {".py": "python", ".js": "javascript", ".ts": "typescript", ".go": "go", ".rs": "rust", ".java": "java",
             ".rb": "ruby", ".c": "c", ".cpp": "c++", ".cs": "c#", ".php": "php", ".swift": "swift", ".kt": "kotlin"}
_JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_ASSERT_METHODS = re.compile(r"^assert[A-Z]\w*$")
_JS_TEST = re.compile(r"""\b(it|test|describe)\s*\(\s*['"`](.{1,200}?)['"`]""")
_JS_EXPECT = re.compile(r"expect\s*\((.{1,200}?)\)\s*\.(to\w+)\s*\(")
_GO_TEST = re.compile(r"^func\s+(Test\w+)\s*\(", re.MULTILINE)
_GO_ASSERT = re.compile(r"\b(?:assert|require)\s*\.\s*(\w+)\s*\(")
_JS_SYMBOLS = ((re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE), "function"),
               (re.compile(r"^(?:export\s+)?class\s+(\w+)", re.MULTILINE), "class"),
               (re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=", re.MULTILINE), "variable"))
_GO_SYMBOLS = ((re.compile(r"^func\s+(?:\([^)]*\)\s+)?(\w+)", re.MULTILINE), "function"),
               (re.compile(r"^type\s+(\w+)\s+struct", re.MULTILINE), "struct"),
               (re.compile(r"^type\s+(\w+)\s+interface", re.MULTILINE), "interface"))


def _matches(pattern: re.Pattern, text: str):
    """Matches with line numbers, counting each intervening span only once."""
    offset, line = 0, 1
    for match in pattern.finditer(text):
        line += text.count("\n", offset, match.start())
        offset = match.start()
        yield match, line


def repo_map(folder: Path, roots: _roots.Roots) -> dict[str, Any]:
    """Languages, build systems, test runners, key files, the top two levels of folders and file counts."""
    walked = _roots.walk(folder, roots, max_files=MAX_FILES, max_file_bytes=10 ** 12)
    builds, runners, key_files, by_ext, tree = [], [], [], {}, {}
    for path in walked.files:
        rel = path.relative_to(folder)
        depth = len(rel.parts) - 1
        if depth <= 2:
            parent = rel.parent.as_posix()
            tree[parent] = tree.get(parent, 0) + 1
        name = path.name
        if name in BUILD_FILES:
            if BUILD_FILES[name] not in builds:
                builds.append(BUILD_FILES[name])
            if depth <= 1:
                key_files.append(rel.as_posix())
        if name in TEST_RUNNERS and TEST_RUNNERS[name] not in runners:
            runners.append(TEST_RUNNERS[name])
        if depth == 0 and name.lower().startswith("readme"):
            key_files.append(name)
        if path.suffix:
            by_ext[path.suffix.lower()] = by_ext.get(path.suffix.lower(), 0) + 1
    languages = {LANGUAGES[ext]: count for ext, count in sorted(by_ext.items(), key=lambda kv: -kv[1])
                 if ext in LANGUAGES}
    return {"root": str(folder), "languages": languages, "build_systems": builds, "test_runners": runners,
            "key_files": sorted(set(key_files))[:MAX_LISTED],
            "folders": dict(sorted(tree.items())[:MAX_LISTED]),
            "file_counts_by_extension": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])[:15]),
            "read": walked.describe()}


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_roots.read_text(path, MAX_FILE_BYTES))
    except (OSError, ValueError, CodeRefused):
        return None
    return value if isinstance(value, dict) else None


def _toml(path: Path) -> dict[str, Any] | None:
    try:
        return tomllib.loads(_roots.read_text(path, MAX_FILE_BYTES))
    except (OSError, tomllib.TOMLDecodeError, ValueError, CodeRefused):
        return None


def _inside(folder: Path, name: str, roots: _roots.Roots, reasons: dict[str, str]) -> Path | None:
    """``folder/name`` when it is a file whose resolved path stays inside the roots."""
    try:
        real = confined_path(folder / name, roots.roots)
        if not real.exists():
            return None
        if not real.is_file():
            raise ValueError("marker is not a file")
        if real.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"marker exceeds {MAX_FILE_BYTES} bytes")
        return real
    except (ValueError, OSError) as exc:
        reasons[name] = str(exc)
        return None


def detect_build_env(folder: Path, roots: _roots.Roots) -> dict[str, Any]:
    """The package manager, the test runner and command the repository declares, language versions, and the files
    read; a declared command is the repository's own text and is never run."""
    found: dict[str, Any] = {"root": str(folder), "package_manager": None, "test_runner": None, "test_command": None,
                             "language_versions": {}, "notes": [], "detected_files": [], "unreadable": [],
                             "unreadable_reasons": {}}
    package = _inside(folder, "package.json", roots, found["unreadable_reasons"])
    if package is not None:
        pkg = _json(package)
        if pkg is None:
            found["unreadable"].append("package.json")
        else:
            found["detected_files"].append("package.json")
            pnpm = _inside(folder, "pnpm-lock.yaml", roots, found["unreadable_reasons"])
            yarn = _inside(folder, "yarn.lock", roots, found["unreadable_reasons"])
            found["package_manager"] = "pnpm" if pnpm is not None else "yarn" if yarn is not None else "npm"
            scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
            if isinstance(scripts.get("test"), str):
                command = scripts["test"][:300]
                found["test_command"] = command
                found["test_runner"] = next((r for r in ("jest", "mocha", "vitest", "karma") if r in command), None)
            engines = pkg.get("engines") if isinstance(pkg.get("engines"), dict) else {}
            if isinstance(engines.get("node"), str):
                found["language_versions"]["node"] = engines["node"][:50]
            if "workspaces" in pkg:
                found["notes"].append("a monorepo (workspaces in package.json): tests may run from one workspace")
    pyproject = _inside(folder, "pyproject.toml", roots, found["unreadable_reasons"])
    if pyproject is not None:
        data = _toml(pyproject)
        if data is None:
            found["unreadable"].append("pyproject.toml")
        else:
            found["detected_files"].append("pyproject.toml")
            found["package_manager"] = found["package_manager"] or "pip"
            tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
            if "pytest" in tool:
                found["test_runner"], found["test_command"] = "pytest", "python -m pytest"
            project = data.get("project") if isinstance(data.get("project"), dict) else {}
            if isinstance(project.get("requires-python"), str):
                found["language_versions"]["python"] = project["requires-python"][:50]
    for name, runner in (("pytest.ini", "pytest"), ("tox.ini", "tox"), ("setup.py", None), ("setup.cfg", None)):
        if _inside(folder, name, roots, found["unreadable_reasons"]) is not None:
            found["detected_files"].append(name)
            if runner == "pytest" or (runner and not found["test_runner"]):
                found["test_runner"] = runner
                found["test_command"] = "python -m pytest" if runner == "pytest" else "tox"
            if name.startswith("setup"):
                found["package_manager"] = found["package_manager"] or "pip"
    go_mod = _inside(folder, "go.mod", roots, found["unreadable_reasons"])
    if go_mod is not None:
        found["detected_files"].append("go.mod")
        found["package_manager"], found["test_runner"], found["test_command"] = "go modules", "go test", "go test ./..."
        version = re.search(r"^go\s+(\S+)", _roots.read_text(go_mod, MAX_FILE_BYTES), re.MULTILINE)
        if version:
            found["language_versions"]["go"] = version.group(1)[:20]
    for name, language in ((".nvmrc", "node"), (".node-version", "node"), (".python-version", "python"),
                           (".ruby-version", "ruby")):
        path = _inside(folder, name, roots, found["unreadable_reasons"])
        if path is not None:
            found["language_versions"][language] = _roots.read_text(path, MAX_FILE_BYTES).strip()[:50]
            found["detected_files"].append(name)
    for name in found["unreadable"]:
        found["unreadable_reasons"].setdefault(name, "could not read or parse the marker")
    found["unreadable"] = sorted(found["unreadable_reasons"])
    return found


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:200]
    except (ValueError, TypeError):  # a node ast cannot write back is shown as such
        return "<?>"


def _assertions(body: list[ast.stmt]) -> list[dict[str, Any]]:
    found = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Assert):
            found.append({"kind": "assert", "test": _unparse(node.test), "line": node.lineno})
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(
                node.func, ast.Name) else ""
            if _ASSERT_METHODS.match(name) or name in ("raises", "warns", "approx"):
                found.append({"kind": name, "args": [_unparse(a) for a in node.args[:3]], "line": node.lineno})
    return found[:MAX_LISTED]


def _decorators(nodes: list[ast.expr]) -> list[str]:
    return [_unparse(d.func if isinstance(d, ast.Call) else d) for d in nodes]


def _test_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    return {"name": node.name, "line": node.lineno, "params": [a.arg for a in node.args.args if a.arg != "self"],
            "decorators": _decorators(node.decorator_list), "assertions": _assertions(node.body)}


def analyse_python_tests(source: str) -> dict[str, Any]:
    """A Python test file's tests, test classes, fixtures, assertions and imports, from its syntax tree."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return {"method": "ast", "parses": False, "error": f"{getattr(exc, 'msg', exc)} (line "
                                                           f"{getattr(exc, 'lineno', '?')})"}
    functions, classes, fixtures, imports = [], [], [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            base = (node.module or "") if isinstance(node, ast.ImportFrom) else ""
            imports += [f"{base}.{a.name}" if base else a.name for a in node.names]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                functions.append(_test_function(node))
            elif any("fixture" in name for name in _decorators(node.decorator_list)):
                fixtures.append({"name": node.name, "line": node.lineno})
        elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test")
                                                 or any("TestCase" in _unparse(b) for b in node.bases)):
            methods = [_test_function(n) for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
            setup = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name in ("setUp", "tearDown", "setUpClass", "tearDownClass", "setup_method",
                                    "teardown_method")]
            classes.append({"name": node.name, "line": node.lineno, "bases": [_unparse(b) for b in node.bases],
                            "setup": setup, "tests": methods})
    return {"method": "ast", "parses": True, "total_tests": len(functions) + sum(len(c["tests"]) for c in classes),
            "tests": functions[:MAX_LISTED], "classes": classes[:MAX_LISTED], "fixtures": fixtures[:MAX_LISTED],
            "imports": imports[:MAX_LISTED]}


def analyse_pattern_tests(source: str, language: str) -> dict[str, Any]:
    """Tests and assertions found by pattern in a JavaScript, TypeScript or Go test file."""
    if language == "go":
        tests = [{"name": m.group(1), "line": line} for m, line in _matches(_GO_TEST, source)]
        checks = [{"method": m.group(1), "line": line} for m, line in _matches(_GO_ASSERT, source)]
    else:
        tests = [{"kind": m.group(1), "name": m.group(2), "line": line}
                 for m, line in _matches(_JS_TEST, source)]
        checks = [{"actual": m.group(1)[:80], "matcher": m.group(2), "line": line}
                  for m, line in _matches(_JS_EXPECT, source)]
    total = sum(1 for t in tests if t.get("kind", "test") != "describe")
    return {"method": "pattern", "language": language, "total_tests": total, "tests": tests[:MAX_LISTED],
            "assertions": checks[:MAX_LISTED],
            "note": "read by pattern, not parsed: a test written another way is missed, and text in a string or a "
                    "comment can match"}


def language_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return "python" if suffix == ".py" else "go" if suffix == ".go" else (
        "typescript" if suffix in (".ts", ".tsx") else "javascript") if suffix in _JS else ""


def test_analyze(source: str, name: str) -> dict[str, Any]:
    language = language_of(name)
    if language == "python":
        return {"language": "python", **analyse_python_tests(source)}
    if language:
        return analyse_pattern_tests(source, language)
    return {"language": "", "method": "none", "note": f"{Path(name).suffix or 'a file with no suffix'} is not read"}


def code_summarize(source: str, name: str) -> dict[str, Any]:
    """A source file's classes, functions and imports (Python, from the syntax tree) or its top-level symbols
    (JavaScript, TypeScript and Go, by pattern)."""
    language, lines = language_of(name), source.count("\n") + (1 if source and not source.endswith("\n") else 0)
    if language == "python":
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            return {"language": "python", "method": "ast", "lines": lines, "parses": False,
                    "error": f"{getattr(exc, 'msg', exc)} (line {getattr(exc, 'lineno', '?')})"}
        classes, functions, imports = [], [], []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append({"name": node.name, "line": node.lineno, "bases": [_unparse(b) for b in node.bases],
                                "methods": [n.name for n in node.body
                                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({"name": node.name, "line": node.lineno, "params": [a.arg for a in node.args.args]})
            elif isinstance(node, ast.Import):
                imports += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports.append(f"{'.' * node.level}{node.module or ''}: {', '.join(a.name for a in node.names)}"[:200])
        return {"language": "python", "method": "ast", "lines": lines, "parses": True, "classes": classes[:MAX_LISTED],
                "functions": functions[:MAX_LISTED], "imports": imports[:MAX_LISTED]}
    if language:
        patterns = _GO_SYMBOLS if language == "go" else _JS_SYMBOLS
        symbols = sorted(({"name": m.group(1), "kind": kind, "line": line}
                          for pattern, kind in patterns for m, line in islice(_matches(pattern, source), MAX_LISTED)), key=lambda s: s["line"])
        return {"language": language, "method": "pattern", "lines": lines, "symbols": symbols[:MAX_LISTED],
                "note": "top-level symbols found by pattern, not parsed"}
    return {"language": "", "method": "none", "lines": lines, "note": "no structured reading for this kind of file"}


__all__ = ["BUILD_FILES", "LANGUAGES", "MAX_FILES", "MAX_FILE_BYTES", "TEST_RUNNERS", "analyse_pattern_tests",
           "analyse_python_tests", "code_summarize", "detect_build_env", "language_of", "repo_map", "test_analyze"]
