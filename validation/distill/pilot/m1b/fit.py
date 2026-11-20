"""Static fit filter; candidate Python is parsed, never imported or executed.

CLI inputs are local JSON: a list of Candidate objects and one Environment object.
Changed-file before/after values and test_package_files values are base64 bytes;
null denotes an absent side. Module/package lists contain top-level names. Optional
prepared_environment plus interpreter_exists enable an actual static recount.
Only codes, flags, counts and decisions leave the filter; source stays in memory.
"""
from __future__ import annotations

import argparse
import ast
import base64
import binascii
import difflib
import json
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from g6.distill import operators
from g6.distill.held_out.case_gate import CaseRecord, Provenance, python_path
from g6.distill.held_out.oracle import OraclePolicy
from validation.distill.pilot import census
from validation.worked_examples.selection import is_test

MANDATORY = tuple(f"M{i}" for i in range(1, 7))
RULES = (*MANDATORY, "V1", "V2", "V3")


@dataclass(frozen=True)
class ChangedFile:
    before: bytes | None
    after: bytes | None


@dataclass(frozen=True)
class Candidate:
    repository: str
    bug_id: str
    parent_tree: Path
    changed_files: Mapping[str, ChangedFile] = field(repr=False)
    test_package_files: Mapping[str, bytes] = field(repr=False)


@dataclass(frozen=True)
class Environment:
    resolvable_modules: frozenset[str]
    loaded_packages: frozenset[str]
    pytest_dependencies: frozenset[str]


@dataclass(frozen=True)
class FitRecord:
    repository: str
    bug_id: str
    failed_rules: tuple[str, ...]
    flags: tuple[str, ...]
    mutant_count: int
    variants: tuple[str, ...]
    environment: Environment = field(repr=False)
    candidate: Candidate = field(repr=False)
    program: Mapping[str, str] = field(repr=False)
    tests: Mapping[str, tuple[str, str]] = field(repr=False)
    helpers: Mapping[str, str] = field(repr=False)
    target: str
    changed_lines: tuple[int, ...]
    known_wrong: str = field(repr=False)
    repository_modules: frozenset[str] = field(repr=False)

    def public(self) -> dict:
        # Deliberately whitelist fields; dataclass serialization would leak material.
        return {"repository": self.repository, "bug_id": self.bug_id,
                "failed_rules": list(self.failed_rules), "flags": list(self.flags),
                "mutant_count": self.mutant_count, "variants": list(self.variants)}


def _ordinary(path: str) -> bool:
    try:
        python_path(path)
        return True
    except ValueError:
        return False


def _text(data: bytes) -> str:
    return data.decode("utf-8")


def _module(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if len(parts) > 1 and parts[:1] == ["src"]:
        parts.pop(0)
    if parts[-1:] == ["__init__"]:
        parts.pop()
    return ".".join(parts)


def _top_modules(names: frozenset[str]) -> frozenset[str]:
    return frozenset(name.split(".")[0] for name in names)


def _changed_lines(before: str, after: str) -> tuple[int, ...]:
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(), autojunk=False)
    return tuple(line + 1 for kind, _, _, start, end in matcher.get_opcodes()
                 if kind in {"replace", "insert"} for line in range(start, end))


def _case(files: Mapping[str, str], target: str, lines: tuple[int, ...],
          tests: tuple[str, str], wrong: str, *, unit_id: str = "static-fit") -> CaseRecord:
    return CaseRecord(unit_id=unit_id,
                      provenance=Provenance(source="maintainer", cohort="development",
                                            source_ref="local-static-input", source_sha256="0" * 64),
                      files=dict(files), target=target, changed_lines=lines,
                      before_tests=tests[0], after_tests=tests[1], known_wrong=(wrong,))


def _program_fits(files: Mapping[str, str], target: str) -> bool:
    try:
        # Use the unchanged validator, including its path, byte and aggregate checks.
        _case(files, target, (1,), ("pass", "pass"), "pass")
        return True
    except ValueError:
        return False


def _texts_fit(tests: Mapping[str, tuple[str, str]], wrong: str) -> bool:
    try:
        if not tests:
            return False
        for pair in tests.values():
            _case({"target.py": "pass"}, "target.py", (1,), pair, wrong)
        return True
    except ValueError:
        return False


def _imports(sources: Mapping[str, str], roots: tuple[str, ...], test_paths: frozenset[str],
             resolvable: frozenset[str]) -> tuple[set[str], set[str]]:
    """Reach local modules and package initializers only through direct imports."""
    modules = {_module(path): path for path in sources if path.endswith(".py")}
    failures, flags, seen = set(), set(), set()
    pending = list(roots)

    def inspect(name: str, guarded: bool):
        if not name:
            return
        parts = name.split(".")
        reached = [modules[".".join(parts[:i])] for i in range(1, len(parts) + 1)
                   if ".".join(parts[:i]) in modules]
        own = any(path in test_paths for path in reached)
        if own:
            (flags if guarded else failures).add("guarded_V2" if guarded else "V2")
        namespace = any(module.startswith(name + ".") for module in modules)
        if reached or namespace:
            if not guarded:
                pending.extend(reached)
        elif parts[0] not in resolvable:
            (flags if guarded else failures).add("guarded_V3" if guarded else "V3")

    while pending:
        path = pending.pop()
        if path in seen or path not in sources:
            continue
        seen.add(path)
        try:
            tree = ast.parse(sources[path])
        except (SyntaxError, ValueError):
            flags.add("unparseable_module")
            continue
        package = _module(path).split(".")
        if not path.endswith("/__init__.py"):
            package = package[:-1]
        # Importing a module also executes its package markers.
        for i in range(1, len(package) + 1):
            marker = modules.get(".".join(package[:i]))
            if marker and marker.endswith("/__init__.py"):
                pending.append(marker)
        direct = {id(node) for node in tree.body}
        for node in ast.walk(tree):
            guarded = id(node) not in direct
            if isinstance(node, ast.Import):
                for alias in node.names:
                    inspect(alias.name, guarded)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    if node.level > len(package):
                        flags.add("unresolved_relative_import")
                        continue
                    base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
                inspect(base, guarded)
                for alias in node.names:
                    child = f"{base}.{alias.name}" if base else alias.name
                    if child in modules:
                        inspect(child, guarded)
    return failures, flags


def _test_flags(tests: Mapping[str, tuple[str, str]]) -> set[str]:
    flags = set()
    for _, after in tests.values():
        try:
            tree = ast.parse(after)
        except (SyntaxError, ValueError):
            flags.add("unparseable_tests")
            continue
        fixtures = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    dec = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if isinstance(dec, ast.Attribute) and dec.attr == "fixture" or (
                            isinstance(dec, ast.Name) and dec.id == "fixture"):
                        fixtures.add(node.name)
            if isinstance(node, ast.Attribute) and node.attr in {"skip", "skipif", "xfail"}:
                flags.add("xfail" if node.attr == "xfail" else "skip")
            if isinstance(node, ast.Call) and (isinstance(node.func, ast.Name) and node.func.id == "open" or
                                             isinstance(node.func, ast.Attribute) and node.func.attr in
                                             {"open", "read", "read_text", "read_bytes"}):
                flags.add("file_read")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                requested = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
                requested -= {"self", "cls"}
                if requested - fixtures:
                    flags.add("fixture_not_in_module")
    return flags


def _read_program(root: Path) -> tuple[dict[str, str], bool]:
    program = {}
    invalid_program = False
    # Walk only the explicitly supplied local parent, rejecting links before descent.
    def walk(folder: Path):
        nonlocal invalid_program
        for source in sorted(folder.iterdir()):
            if source.is_symlink() or source.is_junction():
                invalid_program = True
                continue
            if source.name.startswith("."):
                continue
            if source.is_dir():
                yield from walk(source)
            elif source.suffix == ".py":
                yield source

    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError("expected an ordinary local parent directory")
    for source in walk(root):
        path = source.relative_to(root).as_posix()
        if not is_test(path):
            try:
                program[path] = _text(source.read_bytes())
            except UnicodeError:
                invalid_program = True
    return program, invalid_program


def assess(candidate: Candidate, environment: Environment) -> FitRecord:
    program, invalid_program = _read_program(Path(candidate.parent_tree))
    return _assess_sources(candidate, environment, program, invalid_program=invalid_program)


def _assess_sources(candidate: Candidate, environment: Environment, parent_program: Mapping[str, str], *,
                    invalid_program: bool = False) -> FitRecord:
    """Static engine over an already-read parent; separate from directory traversal."""
    failures, flags = set(), set()
    program, helpers, tests = dict(parent_program), {}, {}
    targets = [path for path in candidate.changed_files if path.endswith(".py") and not is_test(path)]
    target = targets[0] if len(targets) == 1 else ""
    pair = candidate.changed_files.get(target, ChangedFile(None, None))
    if not _ordinary(target) or pair.before is None or pair.after is None:
        failures.add("M1")
    wrong = ""
    for path, change in candidate.changed_files.items():
        if not path.endswith(".py"):
            continue
        try:
            if is_test(path):
                tests[path] = (_text(change.before) if change.before is not None else "",
                               _text(change.after) if change.after is not None else "")
            elif change.after is None:
                program.pop(path, None)
            else:
                program[path] = _text(change.after)
        except UnicodeError:
            failures.add("M3" if is_test(path) else "M2")
    try:
        wrong = _text(pair.before) if pair.before is not None else ""
    except UnicodeError:
        failures.add("M3")
    for path, data in candidate.test_package_files.items():
        if PurePosixPath(path).name == "conftest.py":
            continue
        try:
            helpers[path] = _text(data)
        except UnicodeError:
            flags.add("unparseable_helper")
            # Preserve a failing path for complete staged-record validation.
            helpers["invalid-helper"] = ""
    if invalid_program or not _program_fits(program, target if target in program else next(iter(program), "")):
        failures.add("M2")
    if not _texts_fit(tests, wrong):
        failures.add("M3")
    lines = _changed_lines(wrong, program.get(target, ""))
    mutant_count = 0
    if lines and target in program:
        try:
            mutant_count = len(census.sample(program[target], lines, OraclePolicy(min_mutants=1)))
        except (census.oracle.mutation_check.Unfinished, SyntaxError, ValueError):
            flags.add("mutation_sample_unavailable")
    if mutant_count < 1:
        failures.add("M4")
    repo_modules = frozenset(_module(path).split(".")[0] for path in program if _module(path))
    if repo_modules & _top_modules(environment.loaded_packages):
        failures.add("M5")
    variants = set()
    for _, after in tests.values():
        for name in (*operators.TRAINING, *operators.LEGITIMATE):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    operators.apply_operator(after, name)
                variants.add(name)
            except (ValueError, SyntaxError, TypeError):
                pass
    if not variants:
        failures.add("M6")
    if len(tests) > 1:
        failures.add("V1")
    sources = {**program, **helpers, **{path: pair[1] for path, pair in tests.items()}}
    imported, import_flags = _imports(sources, tuple(tests), frozenset(helpers) | frozenset(tests),
                                      environment.resolvable_modules)
    failures.update(imported)
    flags.update(import_flags | _test_flags(tests))
    return FitRecord(candidate.repository, candidate.bug_id, tuple(rule for rule in RULES if rule in failures),
                     tuple(sorted(flags)), mutant_count, tuple(sorted(variants)), environment, candidate,
                     program, tests, helpers, target, lines, wrong, repo_modules)


def _candidate(data: dict) -> Candidate:
    def decode(value):
        return base64.b64decode(value, validate=True) if value is not None else None
    return Candidate(data["repository"], data["bug_id"], Path(data["parent_tree"]),
                     {path: ChangedFile(decode(pair["before"]), decode(pair["after"]))
                      for path, pair in data["changed_files"].items()},
                     {path: base64.b64decode(value, validate=True)
                      for path, value in data["test_package_files"].items()})


def _environment(data: dict) -> Environment:
    return Environment(*(frozenset(data[key]) for key in
                         ("resolvable_modules", "loaded_packages", "pytest_dependencies")))


def main(argv=None, *, repository_root: Path | None = None) -> int:
    from .chooser import choose
    from .counts import summarize

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    repository = (repository_root or Path(__file__).resolve().parents[4]).resolve()
    if args.output.resolve().is_relative_to(repository):
        parser.exit(2, "expected output outside repository\n")
    try:
        inputs = json.loads(args.candidates.read_text(encoding="utf-8"))
        env_data = json.loads(args.environment.read_text(encoding="utf-8"))
        env = _environment(env_data)
        rows = [assess(_candidate(data), env) for data in inputs]
        report = summarize(rows)
        prepared_count = None
        exists = env_data.get("interpreter_exists") is True
        if exists and "prepared_environment" in env_data:
            recount = summarize(rows, prepared_environment=_environment(env_data["prepared_environment"]))
            prepared_count = recount["counts"]["K_3"]
            report["prepared_counts"] = recount["counts"]
        report["selection"] = choose(report["counts"], interpreter_exists=exists,
                                      prepared_count=prepared_count).public()
        report["records"] = [row.public() for row in rows]
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, binascii.Error):
        parser.exit(2, "local static inventory input or output refused\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
