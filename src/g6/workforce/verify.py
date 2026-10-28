# SPDX-License-Identifier: Apache-2.0

"""Independent verification: "done" only when a check the agent did not write says so.

Three kinds in v1 (plan D2/D3):

* ``pytest``: a run set the verifier derives itself (``run_set``) from
  ``git diff --name-only`` plus the test files that name a changed module. A
  test path the agent hands over is ignored (a hand-picked suite is a known
  overclaim pattern). The run itself belongs to ``repo_tests`` or
  ``truth.run_isolated``, which install the network guard before any of the
  repository's code is imported (audit 011, VEN-002: this module's own plain
  ``python -m pytest`` runners had no guard and were removed).
* ``result_schema``: ``out/result.json`` has the contract's shape.
* ``queued_action``: an outward action the agent claims to have queued exists
  as a row (the lookup is injected; the queue lands in S4).

A verdict names its failure KIND with the codes the failure taxonomy (S5) uses.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from g6.core.git_env import UnsafeGitConfig, git_program, safe_git, safe_git_args
from g6.safety_engine import detect_test_weakening

OUTWARD_TYPES = frozenset({"email", "post", "publish_copy", "price_test", "spend", "experiment"})


@dataclass(frozen=True)
class Verdict:
    ok: bool
    kind: str            # VERIFY_TESTS | VERIFY_SCHEMA | VERIFY_QUEUE
    reason: str = ""
    detail: dict = field(default_factory=dict)


# --- code changes -----------------------------------------------------------------

GIT_TIMEOUT_S = 60


def changed_files(workdir: Path) -> list[str]:
    """Tracked files changed since HEAD plus untracked files, as git reports them. A workdir that
    is not a git repository (a seed copy in a round) counts every .py file as changed: the run
    set is then every test that names any module, which is wider, never hand-picked. A git state
    git cannot diff (canary2 2026-09-27: the agent ran `git init` in its copy, so HEAD was unborn)
    is treated the same way: an agent with a shell may leave any state, and a sensor records, it
    never halts the round. Git runs under ``g6.core.git_env.safe_git``: the working copy's config (or a nested
    repository's) cannot make it start a program, and a copy holding a submodule reads as not diffable."""
    def _git(*args: str) -> list[str]:
        with safe_git(workdir) as env:
            out = subprocess.run([git_program(), "--no-pager", "-C", str(workdir), *safe_git_args(*args)], capture_output=True,
                                 text=True, encoding="utf-8", timeout=GIT_TIMEOUT_S, check=True, env=env).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    def _every_py() -> list[str]:
        return sorted(p.relative_to(workdir).as_posix() for p in Path(workdir).rglob("*.py")
                      if "__pycache__" not in p.parts)
    if not (Path(workdir) / ".git").exists():
        return _every_py()
    try:
        return sorted(set(_git("diff", "--name-only", "--no-ext-diff", "--no-textconv", "HEAD")
                          + _git("ls-files", "--others", "--exclude-standard")))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, UnsafeGitConfig):
        return _every_py()


def head_snapshot(workdir: Path) -> Path | None:
    """The tracked files at HEAD, exported to a fresh directory: the pristine copy a REAL operator's
    working copy is judged against (test integrity, protected paths). None for a workdir that is
    not a git repository (a seed copy in a round uses the seed itself)."""
    workdir = Path(workdir)
    if not (workdir / ".git").exists():
        return None
    out = Path(tempfile.mkdtemp(prefix="venture-head-"))
    tar = out / "head.tar"
    with safe_git(workdir) as env:   # the working copy's config cannot make git start a program
        subprocess.run([git_program(), "--no-pager", "-C", str(workdir), "archive", "--format=tar", "-o", str(tar), "HEAD"],
                       capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=True, env=env)
    with tarfile.open(tar) as archive:
        archive.extractall(out, filter="data")
    tar.unlink()
    return out


def _is_test_file(rel: str) -> bool:
    return Path(rel).match("test_*.py")   # with the system's case rule, as pytest matches it (review 14b)


def run_set(workdir: Path, changed: list[str], candidates: list[str] | None = None, *,
            read_file: Callable[[Path], str] | None = None) -> list[str]:
    """Changed test files, plus every test file anywhere in the workdir that names a changed
    module's stem (a seed repo keeps its tests at the root; a product repo under tests/).
    ``candidates``: the files to look in, relative to the workdir, instead of walking it (a large
    repository lists them from git; plan 2026-10-05-verify-task-real-repos). A changed ``__init__``, ``conftest``
    or ``setup`` names no test by its stem: a test file that mentions the word is not a test of it."""
    reader = read_file or (lambda path: path.read_text(encoding="utf-8", errors="replace"))
    tests = {rel for rel in changed if _is_test_file(rel)}
    stems = {Path(rel).stem for rel in changed
             if rel.endswith(".py") and not _is_test_file(rel) and Path(rel).stem not in ("__init__", "conftest", "setup")}
    if stems:
        pattern = re.compile(r"\b(" + "|".join(re.escape(s) for s in sorted(stems)) + r")\b")
        files = (sorted(Path(workdir) / rel for rel in candidates if _is_test_file(rel)) if candidates is not None
                 else sorted(p for p in Path(workdir).rglob("test_*.py") if "__pycache__" not in p.parts))
        for tf in files:
            if tf.is_file() and pattern.search(reader(tf)):
                tests.add(tf.relative_to(workdir).as_posix())
    return sorted(tests)


# --- coding-agent sensors (spec section 7 rows 1, 3, 6): code over the diff, never an opinion ----

_SKIP_NAMES = frozenset({"skip", "skipif", "xfail", "importorskip"})
_MOCK_NAMES = frozenset({"mock", "monkeypatch", "MagicMock", "patch"})
_CATCH_ALL = frozenset({"AssertionError", "Exception", "BaseException"})
#: Files that change what pytest collects or how it runs; new or changed ones are weakening.
_PYTEST_CONFIG = ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")


def _tests_in(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in Path(root).rglob("test_*.py") if "__pycache__" not in p.parts and "out" not in p.parts}


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    return ""


def _test_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    out: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef) and sub.name.startswith("test"):
                    out[f"{node.name}::{sub.name}"] = sub
    return out


def _asserts(fn: ast.AST) -> list[str]:
    """The asserts a test really makes: one guarded by a catch-all handler is no assert."""
    found: list[str] = []

    def visit(node: ast.AST, guarded: bool) -> None:
        # Each statement is examined ITSELF, not only through its children: a ``try`` directly inside another ``try``'s body
        # (or in an ``else:`` or ``finally:``) keeps its own handlers, and an assert directly there is counted (Gate W D-W7;
        # the earlier walk treated such a statement as a parent, so a nested catch-all was ignored and an assert in
        # ``else:`` or ``finally:`` was never counted).
        if isinstance(node, ast.Assert):
            if not guarded:
                found.append(ast.dump(node.test))
        elif isinstance(node, ast.Try | getattr(ast, "TryStar", ast.Try)):
            catch_all = any(h.type is None or _name_of(h.type) in _CATCH_ALL for h in node.handlers)
            for stmt in node.body:  # only the body is guarded by this try's own handlers
                visit(stmt, guarded or catch_all)
            for part in (*node.handlers, *node.orelse, *node.finalbody):
                visit(part, guarded)
        else:
            for child in ast.iter_child_nodes(node):
                visit(child, guarded)

    visit(fn, False)
    return found


def _markers(tree: ast.AST) -> dict[str, int]:
    """Skip / xfail / mock shapes anywhere in the module (or one test), counted, so a new one is a diff."""
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        name = ""
        if isinstance(node, ast.Call | ast.Attribute | ast.Name):
            name = _name_of(node)
        elif isinstance(node, ast.ImportFrom | ast.Import):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            name = next((n for n in names if n and n.split(".")[-1] in _MOCK_NAMES or n == "unittest.mock"), "")
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            name = "pytestmark"
        if name in _SKIP_NAMES or name in _MOCK_NAMES or name in ("pytestmark", "unittest.mock"):
            counts[name] = counts.get(name, 0) + 1
    return counts


def _returns(fn: ast.AST) -> int:
    """Returns in the test's own body. One inside a function or class the test defines (a fake's method) ends the
    fake, not the test, so it skips none of the test's asserts (dogfood episode 4, 2026-10-05)."""
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    def count(node: ast.AST) -> int:
        return sum(0 if isinstance(child, nested) else int(isinstance(child, ast.Return)) + count(child)
                   for child in ast.iter_child_nodes(node))

    return count(fn)


def test_integrity(original: Path, workdir: Path) -> Verdict:
    """A test may gain asserts, never lose or change one, by AST: a removed or rewritten assert, a
    removed test, a deleted file, an assert guarded by a catch-all handler, an early return, a new
    skip / xfail / importorskip / pytestmark / mock, or a new or changed pytest config file fails the
    verdict (claude-code #33781, #7074, #6193; review C1). A ``conftest.py``, pytest configuration or test-runner file in
    any directory is compared too, by ``g6.safety_engine.detect_test_weakening``."""
    original, workdir = Path(original), Path(workdir)
    before, after = _tests_in(original), _tests_in(workdir)
    findings = weakened_tests(before, after)
    covered = set(before) | set(after)
    for name in _PYTEST_CONFIG:
        a, b = original / name, workdir / name
        if b.exists() and (not a.exists() or a.read_bytes() != b.read_bytes()):
            findings.append(f"{name}: pytest config added or changed")
            covered.add(name)
    findings += _config_findings(original, workdir, covered)
    return Verdict(not findings, "VERIFY_TEST_INTEGRITY", "; ".join(findings)[:300], {"findings": findings})


def _config_findings(original: Path, workdir: Path, covered: set[str]) -> list[str]:
    """Audit 010 (test-integrity gap): the loop above reads pytest's config files at the repository root only, so a
    nested ``tests/sub/conftest.py`` that deselects tests was never seen. ``detect_test_weakening`` reads every
    ``conftest.py`` and pytest or runner config file in any directory. Its reasons about a path already judged above (a
    ``test_*.py`` file, a root config file flagged by the loop) are left out, so those findings and their words stay as
    they were, and so is anything under ``out/`` (``_tests_in``'s rule). A missing directory holds nothing to compare,
    as ``_tests_in`` reads nothing there; a tree that cannot be read is a finding: an unread test is never reported as
    unchanged."""
    if not (original.is_dir() and workdir.is_dir()):
        return []
    try:
        reasons = detect_test_weakening(original, workdir).reasons
    except OSError as exc:
        return [f"{exc.filename or 'a test directory'}: could not be read, so its tests were not compared"]
    out = []
    for reason in reasons:
        path = reason.split(": ", 1)[0]
        if path not in covered and "out" not in PurePosixPath(path).parts:
            out.append(reason)
    return out


def weakened_tests(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """``test_integrity``'s rules over test SOURCES (path -> text), before and after a change: what verify_task
    has when a caller gives its test file as it was and as it is (E10a; FE §10, TS §18, §43, 2026-10-05)."""
    findings: list[str] = []
    for rel, src in before.items():
        now = after.get(rel)
        if now is None:
            findings.append(f"{rel}: test file deleted")
            continue
        try:
            old_tree, new_tree = ast.parse(src), ast.parse(now)
        except SyntaxError as exc:
            findings.append(f"{rel}: unparsable ({exc.msg})")
            continue
        old_fns, new_fns = _test_functions(old_tree), _test_functions(new_tree)
        for name, fn in old_fns.items():
            new_fn = new_fns.get(name)
            if new_fn is None:
                findings.append(f"{rel}: test removed {name}")
                continue
            have = list(_asserts(new_fn))
            for a in _asserts(fn):
                if a in have:
                    have.remove(a)
                else:
                    findings.append(f"{rel}: {name}: an assert was removed or changed")
                    break
            if _returns(new_fn) > _returns(fn):
                findings.append(f"{rel}: {name}: early return added")
        old_m, new_m = _markers(old_tree), _markers(new_tree)
        # A test that did not exist before cannot weaken one that did: its own skip or mock (a new test
        # taking the monkeypatch fixture) is not counted. One in an old test, a fixture or at module level is.
        for name, fn in new_fns.items():
            if name not in old_fns:
                for marker, n in _markers(fn).items():
                    new_m[marker] -= n
        for name, n in new_m.items():
            if n > old_m.get(name, 0):
                findings.append(f"{rel}: added {name}")
    return findings


def scope_check(original: Path, workdir: Path, protected: tuple[str, ...]) -> Verdict:
    """A protected file (named by the seed) that differs from the pristine copy is an undisclosed
    scope breach (claude-code #64473): the task never named it."""
    edited = []
    for rel in protected:
        a, b = Path(original) / rel, Path(workdir) / rel
        if a.exists() != b.exists() or (a.exists() and a.read_bytes() != b.read_bytes()):
            edited.append(rel)
    return Verdict(not edited, "VERIFY_SCOPE", f"protected file edited: {edited}" if edited else "", {"edited": edited})


_REPO_CITE = (".py", ".md", ".json", ".txt", ".html", ".yaml", ".yml", ".toml", ".cfg", ".ini")


def _cite_list(value) -> tuple[list[str], bool]:
    """(the string cites, whether the field was malformed)."""
    if value is None:
        return [], False
    if not isinstance(value, list):
        return [], True
    return [c.strip() for c in value if isinstance(c, str) and c.strip()], any(not isinstance(c, str) for c in value)


def cited(data: dict) -> tuple[list[str], bool]:
    """Top-level cites are checked as written. An outward action's cite is checked only when it is
    shaped like a repository artefact (a code/doc extension, or a test id); a ruling, a section or a
    URL is the grounding check's business."""
    out, malformed = _cite_list(data.get("cites")) if isinstance(data, dict) else ([], True)
    actions = data.get("outward_actions") if isinstance(data, dict) else None
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict):
            malformed = True
            continue
        cites, bad = _cite_list(action.get("cites"))
        malformed = malformed or bad
        out += [c for c in cites if "::" in c or c.lower().endswith(_REPO_CITE)]
    return sorted(set(out)), malformed


def contained(workdir: Path, rel: str, protected: tuple[str, ...] = ()) -> Path | str:
    """``rel`` as a path inside the working directory, resolved through every link and junction, or the
    error text: outside it (an escape, an absolute path, a link or junction that points out), the
    directory itself, or a protected path. The one rule for every write into a workspace (the local
    executor's tools and result file, venture_report's result file); callers check before they create
    a directory or write."""
    root = Path(workdir).resolve()
    try:
        path = (Path(workdir) / rel).resolve()
    except (OSError, ValueError):
        return f"error: bad path {rel!r}"
    if path == root or not path.is_relative_to(root):
        return f"error: {rel!r} is outside the working directory"
    posix = path.relative_to(root).as_posix()
    if any(posix == p.strip("/") or posix.startswith(p.strip("/") + "/") for p in protected):
        return f"error: {rel!r} is protected; the task never named it"
    return path


def _inside(workdir: Path, rel: str) -> Path | None:
    """The cite as a file inside the working copy, or None (a directory, an absolute path, an escape)."""
    try:
        path = (workdir / rel).resolve()
    except (OSError, ValueError):
        return None
    if not path.is_relative_to(workdir.resolve()) or not path.is_file():
        return None
    return path


def _has_test(path: Path, segments: list[str]) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    last = re.sub(r"\[.*\]$", "", segments[-1])
    if len(segments) == 1:
        return any(isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == last for n in tree.body)
    if len(segments) == 2:
        return any(isinstance(c, ast.ClassDef) and c.name == segments[0]
                   and any(isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == last for n in c.body)
                   for c in tree.body)
    return False


def cites_exist(workdir: Path, data: dict, run_set: list[str]) -> Verdict:
    """Everything the result cites must exist: a file inside the working copy, a test id whose file is
    in the run set and whose function (or class method) is defined there, or a symbol (function, class
    method) defined in a non-test file, all by AST (claude-code #92505: provenance written by the
    harness, never by the model). A test outside the run set never ran, so it stays missing (canary4
    2026-09-27: `gst.py::gst_inclusive` was real provenance, read as an unrun test). No cites is no
    claim; a malformed field is a failed verdict, never a crash; a URL is the grounding check's."""
    workdir = Path(workdir)
    cites, malformed = cited(data)
    missing = ["<malformed cites field>"] if malformed else []
    for cite in cites:
        if cite.startswith(("http://", "https://")):
            continue
        if "::" in cite:
            rel, _, rest = cite.partition("::")
            path = _inside(workdir, rel)
            segments = [s for s in rest.split("::") if s]
            if path is None or not segments or not _has_test(path, segments):
                missing.append(cite)  # no symbol named ("gst.py::") is missing, never a crash (Codex P2)
            elif rel not in run_set and _is_test_file(rel):
                missing.append(cite)  # a test the verifier did not run is not evidence
        elif _inside(workdir, cite) is None:
            missing.append(cite)
    return Verdict(not missing, "VERIFY_CITES", f"cited but absent: {missing[:3]}" if missing else "", {"missing": missing})


# --- the result contract -----------------------------------------------------------

def result_schema(path: Path) -> Verdict:
    if not path.exists():
        return Verdict(False, "VERIFY_SCHEMA", f"{path.name} was not written")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Verdict(False, "VERIFY_SCHEMA", f"{path.name} is not JSON: {exc}")
    if not isinstance(data, dict):
        return Verdict(False, "VERIFY_SCHEMA", "result is not an object")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return Verdict(False, "VERIFY_SCHEMA", "summary must be a non-empty string")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not all(isinstance(a, str) for a in artifacts):
        return Verdict(False, "VERIFY_SCHEMA", "artifacts must be a list of paths")
    actions = data.get("outward_actions")
    if not isinstance(actions, list):
        return Verdict(False, "VERIFY_SCHEMA", "outward_actions must be a list")
    for i, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("type") not in OUTWARD_TYPES:
            return Verdict(False, "VERIFY_SCHEMA",
                           f"outward_actions[{i}].type must be one of {sorted(OUTWARD_TYPES)}, "
                           f"got {action.get('type') if isinstance(action, dict) else action!r}")
        body = action.get("body")
        if action.get("type") == "experiment" and not (isinstance(body, str) and body.strip()):
            # canary7 2026-09-28: an experiment is a registration (id, metric, rule), not prose; the contract
            # itself asks for it without a body, and the g6 arm died on day 1 for writing exactly that.
            if not (str(action.get("experiment_id") or "").strip() or str(action.get("metric") or "").strip()):
                return Verdict(False, "VERIFY_SCHEMA",
                               f"outward_actions[{i}]: an experiment needs an experiment_id or a metric")
            continue
        if not isinstance(body, str) or not body.strip():
            return Verdict(False, "VERIFY_SCHEMA", f"outward_actions[{i}].body must be text")
    return Verdict(True, "VERIFY_SCHEMA", "", {"outward_actions": len(actions)})


def queued_action(claimed: list[dict], exists: Callable[[dict], bool]) -> Verdict:
    missing = [a for a in claimed if not exists(a)]
    if missing:
        return Verdict(False, "VERIFY_QUEUE", f"{len(missing)} claimed action(s) not in the queue",
                       {"missing": missing})
    return Verdict(True, "VERIFY_QUEUE", "", {"queued": len(claimed)})
