# SPDX-License-Identifier: Apache-2.0
"""Keep the roots checked by a caller through its read, even after a folder swap."""
from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused
from g6.code.component_creator import block as creator
from g6.code.component_creator import conformance
from g6.code.swe_diagnostics import block as diagnostics
from g6.code.swe_diagnostics import read

# Explicit captures, rather than roots rebuilt from a file's parent at read time.
SELF_ROOTED = {
    ("g6.code._roots", "parse"): "Validate a person's root declaration before capturing its folder identity.",
    ("g6.code._roots", "for_folder"): "Capture an installed package folder once at the caller's check.",
    ("g6.sources.ctx_git.config", "parse"): "Validate the person's declared git roots, not a read-time parent.",
    ("g6.sources.australian_legal_corpus.block", "_register"):
        "Validate a person-selected corpus file; its size and pinned digest authorize registration.",
}


def test_read_requires_declared_roots():
    assert inspect.signature(_roots.read_text).parameters["roots"].default is inspect.Parameter.empty


def _unchecked_calls(text, module):
    """Return calls missing roots; module is the file's dotted module name."""
    missing = []
    targets = {"g6.code._roots.read_text": 3, "g6.core.storage.confined_path": 2}
    tree = ast.parse(text)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = module.split(".")[:-node.level]
                base = ".".join([*package, *([base] if base else [])])
            for alias in node.names:
                if alias.name == "*":
                    for target in targets:
                        if target.rpartition(".")[0] == base:
                            aliases[target.rpartition(".")[2]] = target
                else:
                    aliases[alias.asname or alias.name] = f"{base}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{qualified(node.value)}.{node.attr}"
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            return f"{qualified(node.args[0])}.{node.args[1].value}"
        return ""

    # Direct assignment aliases, including a saved getattr result.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and qualified(node.value) in targets:
                    aliases[target.id] = qualified(node.value)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name, args = qualified(node.func), node.args
        if name == "functools.partial" and args:
            name, args = qualified(args[0]), args[1:]
        if name not in targets:
            continue
        required = targets[name]
        value = next((k.value for k in node.keywords if k.arg == "roots"), None)
        if value is None and len(args) >= required:
            value = args[required - 1]
        invalid = value is None or isinstance(value, ast.Constant) and value.value is None
        if isinstance(value, (ast.Tuple, ast.List)):
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            capture = (module, owner.name if owner else "") in SELF_ROOTED
            invalid = (not value.elts or not capture or any(
                isinstance(part, ast.Attribute) and part.attr in {"parent", "parents"}
                for part in ast.walk(value)))
        if invalid:
            missing.append(node.lineno)
    return missing


def test_no_source_call_omits_roots():
    """Census imported helpers, including aliases, without mistaking Path.read_text for G6's helper."""
    missing = []
    source = Path(__file__).resolve().parents[2] / "src"
    for file in source.rglob("*.py"):
        relative = file.relative_to(source)
        module = ".".join(relative.with_suffix("").parts)
        missing.extend(f"{relative}:{line}" for line in _unchecked_calls(file.read_text(encoding="utf-8"), module))
    assert not missing, missing


@pytest.mark.parametrize("text", [
    "from g6.core.storage import confined_path\nconfined_path(p, (p.parent,))",
    "from g6.core.storage import confined_path\nconfined_path(p, ())",
    "from .._roots import read_text\nread_text(p, 10)",
    "from g6.code._roots import *\nread_text(p, 10)",
    "import functools\nfrom g6.code import _roots\nr = functools.partial(_roots.read_text, roots=None)",
    'from g6.code import _roots\ngetattr(_roots, "read_text")(p, 10)',
])
def test_census_rejects_unchecked_roots_through_indirection(text):
    assert _unchecked_calls(text, "g6.code.x.probe"), "unchecked roots escaped the census"


@pytest.fixture
def checked_tree(tmp_path):
    root = tmp_path.resolve() / "root"
    folder = root / "pkg"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("", encoding="utf-8")
    (folder / "mod.py").write_text("INSIDE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "mod.py").write_text("OUTSIDE = 1\n", encoding="utf-8")
    return root, folder, outside, _roots.Roots((root,), "test")


@pytest.fixture
def swap_folder(checked_tree):
    """No symlink fallback or permission changes; always remove the junction itself."""
    import _winapi

    _, folder, outside, _ = checked_tree
    made = []

    def swap():
        folder.rename(folder.with_name("pkg-was"))
        _winapi.CreateJunction(str(outside), str(folder))
        made.append(folder)

    yield swap
    for link in reversed(made):
        os.rmdir(link)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction swap")
@pytest.mark.parametrize("caller", ["creator_file", "creator_package", "conformance_package", "diagnostics_file",
                                  "json", "toml", "go", "version", "conformance_tests"])
def test_each_reader_refuses_a_folder_swapped_after_its_check(caller, checked_tree, swap_folder, monkeypatch):
    _, folder, outside, roots = checked_tree
    names = {"json": ("package.json", '{"scripts": {"test": "jest"}}'),
             "toml": ("pyproject.toml", '[project]\nrequires-python = ">=3.12"'),
             "go": ("go.mod", "module sample\ngo 1.22\n"), "version": (".python-version", "3.12")}
    if caller in names:
        name, contents = names[caller]
        (folder / name).write_text(contents, encoding="utf-8")
        (outside / name).write_text(contents.replace("3.12", "0.00"), encoding="utf-8")
        check = read._inside

        def checked_marker(*args, **kwargs):
            result = check(*args, **kwargs)
            if args[1] == name:
                swap_folder()
            return result

        monkeypatch.setattr(read, "_inside", checked_marker)

        def call():
            return read.detect_build_env(folder, roots)
    elif caller == "conformance_tests":
        (folder / "tests").mkdir()
        (outside / "tests").mkdir()
        for parent in (folder, outside):
            (parent / "tests" / "test_pkg.py").write_text("def test_x(): pass\n", encoding="utf-8")
        check = conformance.confined_path

        swapped = False

        def checked_test(*args, **kwargs):
            nonlocal swapped
            result = check(*args, **kwargs)
            if not swapped:
                swap_folder()
                swapped = True
            return result

        monkeypatch.setattr(conformance, "confined_path", checked_test)
        problems = []
        assert conformance._tests_near(folder, "pkg", "", roots, problems) == {}
        assert any(_roots.OUTSIDE_ROOT in p["message"] for p in problems), problems
        return
    else:
        is_package = caller.endswith("package")
        check = _roots.walk if is_package else _roots.existing

        def checked(*args, **kwargs):
            result = check(*args, **kwargs)
            swap_folder()
            return result

        monkeypatch.setattr(_roots, "walk" if is_package else "existing", checked)
        data = SimpleNamespace(source="", path=str(folder if is_package else folder / "mod.py"), module="")

        def call():
            if caller.startswith("creator"):
                return creator._modules(data, roots)
            if caller.startswith("conformance"):
                return conformance._files(folder, roots)
            return diagnostics._file(data, roots)
    with pytest.raises(CodeRefused) as refused:
        call()
    assert refused.value.code == _roots.OUTSIDE_ROOT


def test_controls_read_files_and_markers_inside_roots(checked_tree):
    _, folder, _, roots = checked_tree
    assert _roots.read_text(folder / "mod.py", 1000, roots=roots) == "INSIDE = 1\n"
    (folder / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
    assert read.detect_build_env(folder, roots)["test_command"] == "jest"


def test_explicit_none_does_not_establish_a_read_root(checked_tree):
    _, folder, _, _ = checked_tree
    with pytest.raises(CodeRefused) as refused:
        _roots.read_text(folder / "mod.py", 1000, roots=None)
    assert refused.value.code == _roots.NO_ROOT
