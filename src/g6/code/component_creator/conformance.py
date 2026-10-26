# SPDX-License-Identifier: Apache-2.0
"""A package held to the library's standard, with execution recorded for installed components.

Folder checks parse text only. Installed checks import the package, inspect the block class and invoke its ops op.

:func:`check_package` takes a package's files (its ``.py`` files and ``skill/SKILL.md``, by path relative to the
package folder) and its tests, and returns each problem found, with the check that found it:

* ``headers``: every ``.py`` file starts with the SPDX line;
* ``declarations``: the package ``__init__.py`` declares ``__g6_component__``, ``__g6_block__``, ``__g6_family__``,
  ``__g6_sensitive_ops__``, ``__g6_requires__``, ``__g6_mitigates__``, ``__g6_introduces__``, ``__g6_outside__`` and
  ``__g6_extras__``;
* ``failure_modes``: the declarations as ``g6.core.failure_modes`` reads them (``declarations_in`` and
  ``problems_in``, the rule ``tests/core`` and the conformance kit hold every component to);
* ``block``: the class ``__g6_block__`` names exists, its ``infer`` takes an input model that refuses extra fields
  (``extra="forbid"``) and whose ``op`` field lists ``ops`` and ``help``; every sensitive op is one of its ops;
* ``skill``: the sections ``g6.core.skills.SECTIONS`` names, in order and written; its name and description; its Ops
  exactly the block's; a ``g6-example`` for every op that is not sensitive, each naming an op; every failure mode the
  package declares named where it belongs; the four open problems; within ``MAX_WORDS``; and no ``TODO`` left;
* ``tests``: a test file for the component with at least one test, which is not the scaffold's placeholder.

For an installed component (:func:`check_component`) the ops are the block's own ``ops`` answer and the input model
is the class itself; for a folder (:func:`check_folder`) both are read from the source. Passing these checks is not
conformance: the conformance kit (``tests/conformance``) and the package's own tests exercise behaviour, which this never
does.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from typing import Any

from g6.code import _roots
from g6.code._types import CodeRefused
from g6.core import skills
from g6.core.failure_modes import declarations_in, problems_in
from g6.core.storage import confined_path

SPDX = "# SPDX-License-Identifier: Apache-2.0"
DECLARATIONS = ("__g6_component__", "__g6_block__", "__g6_family__", "__g6_sensitive_ops__", "__g6_requires__",
                "__g6_mitigates__", "__g6_introduces__", "__g6_outside__", "__g6_extras__")
NOT_A_PACKAGE = "G6_E_CONFORMANCE_NOT_A_PACKAGE"
UNKNOWN_COMPONENT = "G6_E_CONFORMANCE_UNKNOWN_COMPONENT"
MAX_FILES = 200
MAX_FILE_BYTES = 1_000_000
_TODO = re.compile(r"\bTODO\b")
_PLACEHOLDER = "write the test that proves"


def _problem(check: str, file: str, message: str) -> dict[str, str]:
    return {"check": check, "file": file, "message": message}


def _assigned(tree: ast.Module) -> dict[str, ast.expr]:
    found: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            found[node.target.id] = node.value
    return found


def _module_file(files: dict[str, str], module: str, level: int, package: str) -> str | None:
    """The file in ``files`` a ``from <module> import`` in the package ``__init__.py`` names."""
    if level:
        tail = module
    elif module.startswith(package + "."):
        tail = module[len(package) + 1:]
    else:
        tail = module.rsplit(".", 1)[-1] if module.split(".")[-2:-1] == [package.rsplit(".", 1)[-1]] else ""
    if not tail:
        return None
    rel = tail.replace(".", "/")
    return next((f for f in (f"{rel}.py", f"{rel}/__init__.py") if f in files), None)


def _literal(init: ast.Module, value: ast.expr, files: dict[str, str], package: str) -> Any:
    """``value`` as a literal, or what a name imported into the package ``__init__.py`` is bound to as a literal in
    the module it comes from; ``None`` when it cannot be read without running the code."""
    try:
        return ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    if not isinstance(value, ast.Name):
        return None
    for node in init.body:
        if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == value.id for a in node.names):
            original = next(a.name for a in node.names if (a.asname or a.name) == value.id)
            rel = _module_file(files, node.module or "", node.level, package)
            if rel is None:
                return None
            try:
                bound = _assigned(ast.parse(files[rel])).get(original)
                return ast.literal_eval(bound) if bound is not None else None
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                return None
    return None


def _classes(files: dict[str, str]) -> dict[str, tuple[str, ast.ClassDef]]:
    found: dict[str, tuple[str, ast.ClassDef]] = {}
    for rel, text in files.items():
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                continue
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    found.setdefault(node.name, (rel, node))
    return found


def _forbids_extra(model: ast.ClassDef) -> bool:
    for node in model.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id == "model_config" and isinstance(node.value, ast.Call):
                return any(k.arg == "extra" and isinstance(k.value, ast.Constant) and k.value.value == "forbid"
                           for k in node.value.keywords)
    return False


def _op_literal(model: ast.ClassDef) -> list[str] | None:
    for node in model.body:
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "op"
                and isinstance(node.annotation, ast.Subscript)):
            inner = node.annotation.slice
            values = inner.elts if isinstance(inner, ast.Tuple) else [inner]
            if all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in values):
                return [v.value for v in values]  # type: ignore[union-attr]
    return None


def _block_ops(block: str, files: dict[str, str]) -> tuple[list[str] | None, list[dict[str, str]]]:
    """The ops the block's input model lists (without ``ops`` and ``help``), read from the source, and the problems."""
    classes = _classes(files)
    if block not in classes:
        return None, [_problem("block", "__init__.py", f"__g6_block__ names {block!r}, which the package does not "
                                                       "define")]
    rel, cls = classes[block]
    infer = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "infer"), None)
    params = [a for a in (infer.args.args if infer else []) if a.arg != "self"]
    annotation = params[0].annotation if params else None
    model_name = (annotation.id if isinstance(annotation, ast.Name) else annotation.value
                  if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str) else "")
    if model_name not in classes:
        return None, [_problem("block", rel, f"{block}.infer does not take an input model the package defines")]
    model_rel, model = classes[model_name]
    problems = [] if _forbids_extra(model) else [
        _problem("block", model_rel, f"the input model {model_name} does not refuse extra fields (extra=\"forbid\")")]
    listed = _op_literal(model)
    if listed is None:
        return None, [*problems, _problem("block", model_rel, f"{model_name} has no op field listing its ops as a "
                                                              "Literal")]
    problems += [_problem("block", model_rel, f"the op field does not list {op!r}") for op in ("ops", "help")
                 if op not in listed]
    return [op for op in listed if op not in ("ops", "help")], problems


def _skill_problems(name: str, text: str, ops: list[str], sensitive: set[str], mitigates: tuple[str, ...],
                    introduces: tuple[str, ...]) -> list[dict[str, str]]:
    where, out = "skill/SKILL.md", []
    meta = skills.front_matter(text)
    if meta.get("name") != skills.skill_name(name):
        out.append(_problem("skill", where, f"its name is {meta.get('name')!r}, not {skills.skill_name(name)!r}"))
    if not meta.get("description", "").startswith("Use when"):
        out.append(_problem("skill", where, "its description does not start with \"Use when\""))
    at = [m.start() if (m := re.search(rf"^## {re.escape(s)}[ \t]*$", text, re.MULTILINE)) else -1
          for s in skills.SECTIONS]
    out += [_problem("skill", where, f"no section \"## {s}\"") for s, a in zip(skills.SECTIONS, at) if a == -1]
    if -1 not in at and at != sorted(at):
        out.append(_problem("skill", where, "its sections are out of order"))
    out += [_problem("skill", where, f"the section \"{s}\" is empty") for s, a in zip(skills.SECTIONS, at)
            if a != -1 and not skills.section(text, s).strip()]
    listed = skills.skill_ops(text)
    if sorted(listed) != sorted(ops) or len(listed) != len(set(listed)):
        out.append(_problem("skill", where, f"its Ops list {listed} is not the block's {ops}"))
    try:
        examples = skills.skill_examples(text)
    except ValueError as exc:
        examples = []
        out.append(_problem("skill", where, str(exc)))
    shown = {op for op, _ in examples}
    out += [_problem("skill", where, f"the g6-example for {op!r} names no op of the block") for op in sorted(shown)
            if op not in ops]
    out += [_problem("skill", where, f"op {op!r} has no g6-example") for op in ops
            if op not in sensitive and op not in shown]
    catches, blind = skills.section(text, "Failure modes it catches"), skills.section(text, "Blind spots")
    out += [_problem("skill", where, f"\"Failure modes it catches\" does not name `{c}`") for c in mitigates
            if f"`{c}`" not in catches]
    out += [_problem("skill", where, f"\"Blind spots\" does not name `{c}`") for c in introduces if f"`{c}`" not in blind]
    body = skills.section(text, "The four open problems")
    out += [_problem("skill", where, f"\"The four open problems\" does not name {p}") for p in skills.OPEN_PROBLEMS
            if p not in body]
    if len(text.split()) > skills.MAX_WORDS:
        out.append(_problem("skill", where, f"it has {len(text.split())} words, more than {skills.MAX_WORDS}"))
    todo = [n for n, line in enumerate(text.splitlines(), 1) if _TODO.search(line)]
    if todo:
        out.append(_problem("skill", where, f"{len(todo)} line(s) still say TODO (first at line {todo[0]})"))
    return out


def _test_problems(name: str, tests: dict[str, str]) -> list[dict[str, str]]:
    if not tests:
        return [_problem("tests", f"test_{name}.py", "no test file for the component was found beside it")]
    out, found = [], 0
    for rel, text in tests.items():
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError) as exc:
            out.append(_problem("tests", rel, f"does not parse: {exc}"))
            continue
        found += sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name.startswith("test"))
        if _PLACEHOLDER in text:
            out.append(_problem("tests", rel, "the \"prove it fails\" test is still the scaffold's placeholder"))
    if not found:
        out.append(_problem("tests", next(iter(tests)), "the test files hold no test"))
    return out


def check_package(name: str, package: str, files: dict[str, str], tests: dict[str, str], *,
                  ops: list[str] | None = None, input_forbids_extra: bool | None = None) -> dict[str, Any]:
    """Every problem :mod:`this module <g6.code.component_creator.conformance>` finds in a package's files, with the
    ops and sensitive ops it read. ``ops`` and ``input_forbids_extra``, when given, are the installed block's own."""
    problems: list[dict[str, str]] = []
    for rel, text in files.items():
        if rel.endswith(".py"):
            try:
                ast.parse(text)
            except (SyntaxError, ValueError) as exc:
                problems.append(_problem("parse", rel, f"does not parse: {exc}"))
    problems += [_problem("headers", rel, "does not start with the SPDX line") for rel, text in sorted(files.items())
                 if rel.endswith(".py") and text.lstrip("﻿").splitlines()[:1] != [SPDX]]
    init_text = files.get("__init__.py")
    if init_text is None:
        return {"problems": [*problems, _problem("declarations", "__init__.py", "the package has no __init__.py")],
                "ops": ops or [], "sensitive": []}
    try:
        init = ast.parse(init_text)
    except (SyntaxError, ValueError) as exc:
        return {"problems": [*problems, _problem("declarations", "__init__.py", f"does not parse: {exc}")],
                "ops": ops or [], "sensitive": []}
    assigned = _assigned(init)
    problems += [_problem("declarations", "__init__.py", f"declares no {d}") for d in DECLARATIONS if d not in assigned]
    found = declarations_in(init_text)
    problems += [_problem("failure_modes", "__init__.py", p) for p in problems_in(found, package)]
    block = _literal(init, assigned["__g6_block__"], files, package) if "__g6_block__" in assigned else None
    static_ops: list[str] | None = None
    if isinstance(block, str):
        static_ops, block_problems = _block_ops(block, files)
        if input_forbids_extra is not None:  # the installed class answers for itself
            block_problems = [p for p in block_problems if "extra fields" not in p["message"]]
            if not input_forbids_extra:
                block_problems.append(_problem("block", "block.py", "the input model does not refuse extra fields "
                                                                    "(extra=\"forbid\")"))
        problems += block_problems
    elif "__g6_block__" in assigned:
        problems.append(_problem("block", "__init__.py", "__g6_block__ is not a class name that can be read"))
    ops = ops if ops is not None else static_ops or []
    raw = _literal(init, assigned["__g6_sensitive_ops__"], files, package) if "__g6_sensitive_ops__" in assigned else ()
    if raw is None:
        problems.append(_problem("declarations", "__init__.py", "__g6_sensitive_ops__ cannot be read without running "
                                                                "the code; make it a literal"))
        raw = ()
    sensitive = {str(s) for s in raw} if isinstance(raw, (tuple, list, set, frozenset)) else set()
    problems += [_problem("block", "__init__.py", f"sensitive op {op!r} is not one of the block's ops")
                 for op in sorted(sensitive) if op not in ops]
    skill = files.get("skill/SKILL.md")
    if skill is None:
        problems.append(_problem("skill", "skill/SKILL.md", "the package ships no skill"))
    else:
        problems += _skill_problems(name, skill, ops, sensitive, found.get("__g6_mitigates__", ()),
                                    found.get("__g6_introduces__", ()))
    problems += _test_problems(name, tests)
    return {"problems": problems, "ops": ops, "sensitive": sorted(sensitive)}


def _package_name(folder: Path) -> str:
    """``g6.<family>.<name>`` or ``g6.<name>`` when the folder sits under a ``g6`` folder; otherwise its own name."""
    parts = folder.parts
    if len(parts) >= 3 and parts[-3] == "g6":
        return f"g6.{parts[-2]}.{parts[-1]}"
    if len(parts) >= 2 and parts[-2] == "g6":
        return f"g6.{parts[-1]}"
    return folder.name


def _tests_near(folder: Path, name: str, family: str, roots: _roots.Roots,
                problems: list[dict[str, str]]) -> dict[str, str]:
    """The test files for ``name`` beside the package: ``tests/test_<name>.py`` or ``tests/<family>/test_<name>.py``
    under the package folder or any of its four nearest parents that ``allowed`` lets be read."""
    found: dict[str, str] = {}
    for ancestor in [folder, *list(folder.parents)[:4]]:
        if not _roots.under(ancestor, roots.roots):
            break
        for candidate in (ancestor / "tests" / f"test_{name}.py", ancestor / "tests" / family / f"test_{name}.py"):
            try:
                real = confined_path(candidate, roots.roots)
                if real.is_file():
                    found.setdefault(real.as_posix(), _roots.read_text(candidate, MAX_FILE_BYTES, roots=roots))
            except (ValueError, OSError, CodeRefused) as exc:
                problems.append(_problem("read", str(candidate), f"test file was not read: {exc}"))
        if found:
            break
    return found


def _files(folder: Path, roots: _roots.Roots) -> tuple[dict[str, str], _roots.Walk]:
    walked = _roots.walk(folder, roots, max_files=MAX_FILES, max_file_bytes=MAX_FILE_BYTES, suffixes=(".py", ".md"))
    return ({p.relative_to(folder).as_posix(): _roots.read_text(p, MAX_FILE_BYTES) for p in walked.files}, walked)


def _incomplete(out: dict[str, Any], walked: _roots.Walk) -> None:
    skipped = walked.truncated or walked.skipped_links or walked.skipped_large or walked.skipped_unreadable
    if skipped:
        out["problems"].append(_problem("read", ".", f"package walk was incomplete: {walked.describe()}"))
    out["complete"] = not skipped and not any(
        p["check"] == "read" or "parse" in p["message"] for p in out["problems"])


def check_folder(path: str, roots: _roots.Roots) -> dict[str, Any]:
    """A package folder under a declared root, checked as text."""
    folder = _roots.existing(path, roots, kind="folder")
    if not (folder / "__init__.py").is_file():
        raise CodeRefused(NOT_A_PACKAGE, f"{folder} holds no __init__.py, so it is not a package")
    files, walked = _files(folder, roots)
    package = _package_name(folder)
    family = package.split(".")[1] if package.count(".") == 2 else ""
    unread: list[dict[str, str]] = []
    tests = _tests_near(folder, folder.name, family, roots, unread)
    out = check_package(folder.name, package, files, tests)
    out["problems"].extend(unread)
    _incomplete(out, walked)
    out.update(imported=[], invoked=[])
    return {**out, "name": folder.name, "package": package, "folder": str(folder), "read": walked.describe(),
            "tests": sorted(tests)}


def check_component(name: str) -> dict[str, Any]:
    """An installed component, checked through the registry: its ops are the block's own answer, its input model the
    class itself, its files read from where it is installed (G6's own, so no declared root is needed)."""
    from g6.core import discovery
    from g6.core.registry import get_registry

    reg = get_registry()
    if name not in reg.refs:
        raise CodeRefused(UNKNOWN_COMPONENT, f"no component named {name!r} is installed")
    package = reg.refs[name].module.split(":", 1)[0]
    spec = importlib.util.find_spec(package)
    locations = list(spec.submodule_search_locations or ()) if spec else []
    if not locations:
        raise CodeRefused(NOT_A_PACKAGE, f"{package} is not a package folder")
    folder = Path(locations[0]).resolve()
    every = _roots.Roots((folder,), "the installed package")
    files, walked = _files(folder, every)
    cls = reg.get_class(name)
    forbids = None
    if cls is not None:
        import inspect
        import typing

        params = [p for p in inspect.signature(cls.infer).parameters.values() if p.name != "self"]
        model = typing.get_type_hints(cls.infer).get(params[0].name) if params else None
        forbids = bool(model is not None and getattr(model, "model_config", {}).get("extra") == "forbid")
    family = reg.refs[name].family or ""
    home = folder.parents[min(3, len(folder.parents) - 1)]
    unread: list[dict[str, str]] = []
    tests = _tests_near(folder, name, family, _roots.Roots((home,), "installed tests"), unread)
    out = check_package(name, package, files, tests, ops=list(discovery.ops(name) or []), input_forbids_extra=forbids)
    out["problems"].extend(unread)
    _incomplete(out, walked)
    out.update(imported=sorted({package, *([cls.__module__] if cls is not None else [])}),
               invoked=[f"{name}.ops"])
    return {**out, "name": name, "package": package, "folder": str(folder), "read": walked.describe(),
            "tests": sorted(tests)}


__all__ = ["DECLARATIONS", "NOT_A_PACKAGE", "UNKNOWN_COMPONENT", "check_component", "check_folder", "check_package"]
