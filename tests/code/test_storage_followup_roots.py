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
ROOT_CAPTURES = {
    ("g6.code._roots", "parse"): "Capture the declared folders after validating their names.",
    ("g6.code._roots", "for_folder"): "Capture the installed folder at its explicit check.",
    ("g6.code.component_creator.conformance", "check_component"):
        "Capture installed package and nearby test folders before their walks and reads.",
    ("g6.sources.ctx_git.config", "repository"):
        "Capture declared git folders once at admission; reuse through all metadata and alternate reads.",
}

# Exact operation/owner exceptions, not module-wide exclusions. Caller metadata must use path_info/read_text.
# These retain existing state I/O, low-level implementations, checked callers and exclusive writers. The bound
# preliminary resolution limitations and same-user replacement windows remain listed in the repair report.
PLAIN_IO = {
    ("g6.code._roots", "__post_init__", "object.__setattr__"):
        "Initial capture of frozen Roots fields; never used to rebind checked Roots at read time.",
    ("g6.code._roots", "_final_path", "os.path.realpath"):
        "Read the already-open descriptor's /proc name, not the caller's original path.",
    ("g6.code._roots", "_folder_state", "folder.resolve"):
        "Fallback final name must match the opened folder's identity before capture.",
    ("g6.code._roots", "walk", "path.resolve"):
        "Walk classification after link exclusion; each content read checks the captured Roots again.",
    ("g6.code._roots", "destination", "real.iterdir"):
        "Shared destination admission checks emptiness after confinement, before the checked exclusive writer.",
    ("g6.code._roots", "_files", "os.scandir"):
        "Shared bounded walk excludes links and checks children before content reads.",
    ("g6.sources.ctx_git.config", "destination", "real.iterdir"):
        "Clone destination emptiness after confinement, before git writes.",
    ("g6.code._build", "write_files", "output.resolve"):
        "Writer's preliminary containment check; retained parent identities checked again before exclusive open.",
    ("g6.code._build", "write_files", "target.resolve"):
        "Captured destination's preliminary check; retained parent identities checked before exclusive open.",
    ("g6.code.component_creator.render", "write", "(dest / rel).resolve"):
        "Writer rejects links and rechecks retained parent identities before exclusive open.",
    ("g6.code.adapt_webpage.checks", "html_script", "root.rglob"):
        "Child checker enumerates generated artefacts inside its sandbox.",
    ("g6.code.adapt_webpage.checks", "css_script", "root.rglob"):
        "Child checker enumerates generated artefacts inside its sandbox.",
    ("g6.code.adapt_webpage.checks", "route_local", "(root / unquote(url.path.lstrip('/'))).resolve"):
        "Child checker resolves generated artefacts then checks containment before serving.",
    ("g6.code.adapt_webpage.checks", "render_script", "root.rglob"):
        "Child checker enumerates generated HTML inside its sandbox.",
    ("g6.code.meta_programming.edits", "_target", "scopes.resolve"):
        "AST lexical binding lookup, not a file-system query.",
    ("g6.code.meta_programming.edits", "rename", "scopes.resolve"):
        "AST lexical binding lookup, not a file-system query.",
    ("g6.code.meta_programming.extract", "_boundary", "scopes.resolve"):
        "AST lexical binding lookup, not a file-system query.",
    ("g6.code._roots", "_folder_handle", "os.open"): "Open a declared folder to capture its identity.",
    ("g6.code._roots", "load", "file.exists"): "Trusted state_path configuration, bounded below.",
    ("g6.code._roots", "load", "file.open"): "Trusted state_path config, plain_file and 1 MiB read cap.",
    ("g6.code._roots", "existing", "real.is_file"): "Legacy shared admission after confined_path/root checks.",
    ("g6.code._roots", "existing", "real.is_dir"): "Legacy shared admission after confined_path/root checks.",
    ("g6.code._roots", "existing", "real.exists"): "Legacy shared admission after confined_path/root checks.",
    ("g6.code._roots", "destination", "real.exists"): "Shared destination admission, before checked writer.",
    ("g6.code._roots", "destination", "real.is_dir"): "Shared destination admission, before checked writer.",
    ("g6.code._roots", "destination", "real.parent.is_dir"): "Shared destination admission, before checked writer.",
    ("g6.code._roots", "walk", "path.stat"): "Walk skip/size accounting; content reader checks again.",
    ("g6.code._roots", "_files", "entry.is_dir"): "Walk classification after link exclusion.",
    ("g6.code._roots", "_files", "entry.is_file"): "Walk classification after link exclusion.",
    ("g6.code._roots", "_walk_open_identity", "os.open"): "Descriptor-relative no-follow identity fallback.",
    ("g6.code._roots", "_walk_open_identity", "os.stat"): "Descriptor-relative stat with follow_symlinks=False.",
    ("g6.code._roots", "read_text", "os.open"): "No-follow nonblocking opener used by the confined reader.",
    ("g6.code._roots", "read_text", "open"): "The capped, identity-checked content reader itself.",
    ("g6.code._build", "write_files", "output.open"): "Exclusive write after retained parent identity checks.",
    ("g6.code._build", "write_files", "output.read_bytes"): "Read back bytes this writer just created.",
    ("g6.code.component_creator.render", "write", "target.exists"): "Writer overwrite refusal; exclusive open below.",
    ("g6.code.component_creator.render", "write", "open"): "Exclusive write after retained parent identity checks.",
    ("g6.code.component_creator.block", "_modules", "found.is_file"): "Admitted by existing; shared reader rechecks.",
    ("g6.code.component_creator.block", "_modules", "found.stat"): "Admitted module's size cap before shared read.",
    ("g6.code.swe_diagnostics.block", "_file", "path.stat"): "Admitted file's size check, caught as CodeRefused.",
    ("g6.code.swe_diagnostics.read", "_inside", "real.exists"): "Legacy marker admission, wrapped; shared read rechecks.",
    ("g6.code.swe_diagnostics.read", "_inside", "real.is_file"): "Legacy marker admission, wrapped; shared read rechecks.",
    ("g6.code.swe_diagnostics.read", "_inside", "real.stat"): "Legacy marker size cap, wrapped; shared read rechecks.",
    ("g6.sources.ctx_git.config", "load", "file.exists"): "Trusted state_path configuration, bounded below.",
    ("g6.sources.ctx_git.config", "load", "file.open"): "Trusted state_path config, plain_file and 1 MiB read cap.",
    ("g6.sources.ctx_git.config", "destination", "real.exists"): "Clone destination admission, not metadata content.",
    ("g6.sources.ctx_git.config", "destination", "real.is_dir"): "Clone destination admission, not metadata content.",
    ("g6.sources.ctx_git.config", "destination", "real.parent.is_dir"): "Clone destination admission, not metadata content.",
    ("g6.sources.ctx_git.runner", "record", "open"): "Append the trusted state_path audit, not repository metadata.",
    ("g6.sources.ctx_git.runner", "records", "path.is_file"): "Trusted state_path audit history.",
    ("g6.sources.ctx_git.runner", "records", "path.read_text"): "Trusted state_path audit history.",
    ("g6.code.adapt_webpage.checks", "html_script", "file.read_text"):
        "Checker reads generated artefacts in the child sandbox, not caller repository metadata.",
    ("g6.code.adapt_webpage.checks", "css_script", "f.read_text"):
        "Checker reads generated artefacts in the child sandbox.",
    ("g6.code.adapt_webpage.checks", "css_script", "file.read_text"):
        "Checker reads generated artefacts in the child sandbox.",
    ("g6.code.adapt_webpage.checks", "route_local", "path.is_file"):
        "Child checker serves only generated artefacts after root containment check.",
    ("g6.code.adapt_webpage.checks", "route_local", "path.read_bytes"):
        "Child checker serves only generated artefacts after root containment check.",
}


def test_read_requires_declared_roots():
    assert inspect.signature(_roots.read_text).parameters["roots"].default is inspect.Parameter.empty


def _unchecked_calls(text, module):
    """Flag unchecked roots, recaptures, plain content I/O and following metadata queries."""
    missing = []
    targets = {"g6.code._roots.read_text": 3, "g6.core.storage.confined_path": 2,
               "g6.code._roots.Roots": 0, "g6.code._roots.for_folder": 0, "g6.code._roots.path_info": 2,
               "g6.code._roots.walk": 2}
    io_methods = {"read_text", "read_bytes", "open", "exists", "is_file", "is_dir", "stat",
                  "isfile", "isdir", "getsize", "realpath", "resolve", "access", "samefile",
                  "iterdir", "scandir", "listdir", "glob", "rglob", "walk", "fwalk", "iglob",
                  "getmtime", "ismount", "is_mount", "owner", "copyfile", "copy", "copy2", "copytree"}
    recaptures = {"dataclasses.replace", "__roots_class__", "object.__setattr__", "builtins.setattr", "setattr"}
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

    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            if node.attr == "__dict__":
                return "__roots_dict__"
            base = qualified(node.value)
            return base if node.attr == "__call__" else f"{base}.{node.attr}"
        if isinstance(node, ast.Call) and qualified(node.func) in {"vars", "builtins.vars"}:
            return "__roots_dict__"
        if isinstance(node, ast.Call) and qualified(node.func) in {"type", "builtins.type"}:
            return "__roots_class__"
        if (isinstance(node, ast.Call) and qualified(node.func) == "importlib.import_module"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            return node.args[0].value
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            return f"{qualified(node.args[0])}.{node.args[1].value}"
        return ""

    # Resolve assignment and annotated aliases to a fixed point, including saved getattr results.
    for _ in range(len(bindings) + 1):
        changed = False
        for target, value in bindings.items():
            name = qualified(value)
            if (name in targets or name in recaptures or name == "__roots_dict__"
                    or name.startswith("__roots_dict__.") or name.endswith((".__class__", ".__new__"))
                    or name.rpartition(".")[2] in io_methods) and aliases.get(target) != name:
                aliases[target] = name
                changed = True
        if not changed:
            break

    def bound(node):
        seen = set()
        while isinstance(node, ast.Name) and node.id in bindings and node.id not in seen:
            seen.add(node.id)
            node = bindings[node.id]
        return node

    def owner_of(node):
        owner = parents.get(node)
        while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = parents.get(owner)
        return module, owner.name if owner else ""

    for node in ast.walk(tree):
        scoped = module.startswith(("g6.code.", "g6.sources.ctx_git."))
        if (scoped and isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del))
                and qualified(node.value) == "__roots_dict__"):
            missing.append(node.lineno)
        if not isinstance(node, ast.Call):
            continue
        name, args = qualified(node.func), node.args
        if name == "functools.partial" and args:
            name, args = qualified(args[0]), args[1:]
        if scoped:
            method = name.rpartition(".")[2]
            # ast.walk and local recursive walk functions visit syntax trees, not folders.
            plain = (method in io_methods and name not in targets and name != "ast.walk"
                     and not (method == "walk" and "." not in name))
            if plain and (*owner_of(node), ast.unparse(node.func)) not in PLAIN_IO:
                missing.append(node.lineno)
            rebuilt = (name == "dataclasses.replace" and (any(k.arg == "roots" for k in node.keywords)
                       or args and isinstance(args[0], ast.Name) and "roots" in args[0].id)
                       or name == "__roots_class__" or name.endswith(".__class__")
                       or name == "g6.code._roots.Roots.__new__"
                       or isinstance(node.func, ast.Call) and qualified(node.func.func) in {"type", "builtins.type"})
            mutated = (name in {"object.__setattr__", "builtins.setattr", "setattr"}
                       and len(args) >= 2 and (not isinstance(bound(args[1]), ast.Constant)
                       or bound(args[1]).value in {"roots", "identities", "unavailable"})
                       or name.startswith("__roots_dict__."))
            if rebuilt or mutated and (*owner_of(node), ast.unparse(node.func)) not in PLAIN_IO:
                missing.append(node.lineno)
        if name not in targets:
            continue
        required = targets[name]
        if required == 0:
            # Empty declarations contain nothing (inline and dry-run ops). Nonempty captures need a named check.
            empty = (name.endswith(".Roots") and args and isinstance(args[0], (ast.Tuple, ast.List))
                     and not args[0].elts)
            ancestor = parents.get(node)
            while ancestor is not None and not isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(ancestor, ast.Call) and qualified(ancestor.func) in targets:
                    break
                ancestor = parents.get(ancestor)
            nested_read = (isinstance(ancestor, ast.Call) and targets.get(qualified(ancestor.func), 0) > 0)
            if nested_read or not empty and owner_of(node) not in ROOT_CAPTURES:
                missing.append(node.lineno)
            continue
        value = next((k.value for k in node.keywords if k.arg == "roots"), None)
        if value is None and len(args) >= required:
            value = args[required - 1]
        value = bound(value)
        invalid = value is None or isinstance(value, ast.Constant) and value.value is None
        if isinstance(value, (ast.Tuple, ast.List)):
            capture = owner_of(node) in SELF_ROOTED
            invalid = (not value.elts or not capture or any(
                isinstance(part, ast.Attribute) and part.attr in {"parent", "parents"}
                for part in ast.walk(value)))
        if invalid:
            missing.append(node.lineno)
    return missing


def test_no_source_call_omits_roots():
    """Census helpers plus content opens/following checks in code and ctx_git, including aliases."""
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


@pytest.mark.parametrize("text", [
    'from g6.code import _roots\ndef f(p):\n return _roots.read_text(p, 10, roots=_roots.Roots((p.parent,), "x"))',
    'from g6.code import _roots\ndef f(p):\n return _roots.read_text(p, 10, roots=_roots.for_folder(p.parent, "x"))',
    'from g6.core.storage import confined_path\ndef f(p):\n bases = (p.parent,)\n return confined_path(p, bases)',
    'from g6.code import _roots\nreader: object = _roots.read_text\nreader(p, 10, roots=None)',
    'from g6.code import _roots\n_roots.read_text.__call__(p, 10, roots=None)',
    'import importlib\nimportlib.import_module("g6.code._roots").read_text(p, 10, roots=None)',
    'from g6.code._roots import Roots as R\ndef f(p):\n bases = R((p.parent,), "x")\n return read(p, bases)',
    'from g6.code._roots import for_folder as capture\ndef f(p):\n bases = capture(p.parent, "x")',
])
def test_census_rejects_read_time_captures_and_dynamic_calls(text):
    assert _unchecked_calls(text, "g6.code.x.probe"), "read-time roots escaped the census"


@pytest.mark.parametrize("text", [
    'import dataclasses\ndef f(p, roots):\n return dataclasses.replace(roots, roots=(p.parent,))',
    'from dataclasses import replace as r\ndef f(p, roots):\n return r(roots, roots=(p.parent,))',
    'def f(p, roots):\n return type(roots)((p.parent,), "x")',
    'def f(p, roots):\n return roots.__class__((p.parent,), "x")',
    'from pathlib import Path\ndef f(p):\n return Path(p).read_text()',
    'def f(p):\n return p.read_bytes()',
    'def f(p):\n return p.open("rb")',
    'def f(p):\n return open(p).read()',
    'from builtins import open as read\ndef f(p):\n return read(p)',
    'def f(p):\n return p.exists()',
    'def f(p):\n return p.is_file()',
    'def f(p):\n return p.is_dir()',
    'def f(p):\n return p.stat()',
    'import os\ndef f(p):\n return os.stat(p)',
    'import dataclasses\ndef f(p, roots):\n return dataclasses.replace(roots)',
    'def f(p):\n reader = p.read_text\n return reader()',
    'def f(p):\n check = p.exists\n return check()',
    'import builtins\ndef f(p):\n reader = builtins.open\n return reader(p)',
])
@pytest.mark.parametrize("module", ["g6.code.x.probe", "g6.sources.ctx_git.probe"])
def test_census_rejects_plain_io_following_checks_and_rebuilt_roots(text, module):
    assert _unchecked_calls(text, module), "metadata I/O or rebuilt roots escaped the census"


@pytest.mark.parametrize("text", [
    'import os\ndef f(p):\n return os.path.exists(p)',
    'def f(p):\n return list(p.rglob("*"))',
    'from os.path import realpath as check\ndef f(p):\n return check(p)',
    'import os\ndef f(p):\n scan = os.scandir\n return list(scan(p))',
    'def f(p):\n scan = p.rglob\n return list(scan("*"))',
    'def f(p, roots):\n cls = type(roots)\n saved = cls\n return saved((p.parent,), "x")',
    'def f(p, roots):\n cls = roots.__class__\n return cls((p.parent,), "x")',
    'import copy\ndef f(p, roots):\n r = copy.deepcopy(roots)\n setattr(r, "roots", (p.parent,))',
    'def f(p, roots):\n mutate = object.__setattr__\n mutate(roots, "identities", ())',
])
@pytest.mark.parametrize("module", ["g6.code.x.probe", "g6.sources.ctx_git.probe"])
def test_census_followup2_queries_aliases_and_root_mutation(text, module):
    assert _unchecked_calls(text, module), "following query, folder read or Roots mutation escaped the census"


def test_census_accepts_conformance_files_checked_delegation():
    # _files delegates its folder read to _roots.walk with the captured Roots, then uses read_text.
    text = "from g6.code import _roots\n" + inspect.getsource(conformance._files)
    assert not _unchecked_calls(text, conformance.__name__)


@pytest.mark.parametrize("text", [
    'import os\ndef f(p):\n return list(os.walk(p))',
    'def f(p):\n return list(p.walk())',
    'import os\ndef f(p):\n return list(os.fwalk(p))',
    'import glob\ndef f(p):\n return list(glob.iglob(str(p)))',
    'import os\ndef f(p):\n return os.path.getmtime(p)',
    'import os\ndef f(p):\n return os.path.ismount(p)',
    'def f(p):\n return p.is_mount()',
    'def f(p):\n return p.owner()',
    *[f'import shutil\ndef f(p, q):\n return shutil.{method}(p, q)'
      for method in ('copyfile', 'copy', 'copy2', 'copytree')],
    'def f(p, roots):\n roots.__dict__["roots"] = (p,)',
    'def f(p, roots):\n vars(roots)["identities"] = ()',
    'def f(p, roots):\n roots.__dict__.update(roots=(p,))',
    'def f(p, roots, field):\n object.__setattr__(roots, field, (p,))',
    'from g6.code._roots import Roots\ndef f():\n return Roots.__new__(Roots)',
    'def f(p, roots):\n fields = vars(roots)\n fields["roots"] = (p,)',
    'def f(p, roots):\n fields = roots.__dict__\n fields.update(roots=(p,))',
    'def f(p, roots):\n put = object.__setattr__\n field = "roots"\n put(roots, field, (p,))',
])
@pytest.mark.parametrize("module", ["g6.code.x.probe", "g6.sources.ctx_git.probe"])
def test_census_repair1_reads_and_root_rewrites(text, module):
    assert _unchecked_calls(text, module), "folder read, query, copy or Roots rewrite escaped the census"


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
