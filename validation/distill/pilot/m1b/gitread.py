"""Read commit objects into a static Candidate without checking out or running code.

The bare object cache and materialized Python parent tree must live outside this
repository. Only ordinary parent Python files are materialized: those are exactly
the files fit.assess reads. Changed blobs retain their bytes, including non-Python
files. Links and submodules are refused. The optional source Path is for offline
local fixtures; pool entries cannot choose a transport or a source URL.
"""
from __future__ import annotations

import ast
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from validation.worked_examples.selection import is_test

from . import fit
from ._mine_io import digest, entry_id, external_path, local_path, repository_id


def _safe_name(name: str) -> None:
    path = PurePosixPath(name)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in "123456789¹²³"),
                *(f"LPT{i}" for i in "123456789¹²³")}
    if not name or not path.parts or path.is_absolute() or "\\" in name or path.as_posix() != name:
        raise ValueError("git path refused")
    for part in path.parts:
        if (part in {".", ".."} or part.lower() == ".git" or part.lower().startswith(".git~")
                or part.rstrip(" .") != part
                or any(c in part for c in ':<>"|?*') or any(ord(c) < 32 for c in part)
                or part.split(".")[0].upper() in reserved):
            raise ValueError("git path refused")


class _Objects:
    def __init__(self, root: Path, origin: str, *, local: bool):
        self.root = root
        self.hooks = root / "empty-hooks"
        self.hooks.mkdir(parents=True, exist_ok=True)
        if any(self.hooks.iterdir()):
            raise ValueError("expected empty hook directory")
        self.objects = root / "objects.git"
        self.origin = origin
        self.local = local
        # Ignore inherited git routing/configuration, credential helpers and templates.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
        if not self.objects.exists():
            self.run("init", "--bare", f"--template={self.hooks}", str(self.objects), in_cache=False)

    def run(self, *args: str, in_cache=True) -> bytes:
        command = ["git", "-c", f"core.hooksPath={self.hooks}", "-c", "core.fsmonitor=false",
                   "-c", "submodule.recurse=false", "-c", "credential.helper=",
                   "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                   "-c", f"protocol.file.allow={'always' if self.local else 'never'}",
                   "-c", f"remote.origin.url={self.origin}", "-c", "remote.origin.promisor=true",
                   "-c", "remote.origin.partialclonefilter=blob:none", "-c", "maintenance.auto=false",
                   "-c", "gc.auto=0"]
        if in_cache:
            command.extend(["--git-dir", str(self.objects)])
        done = subprocess.run([*command, *args], env=self.env, capture_output=True, timeout=600)
        if done.returncode:
            raise ValueError("git object read refused")
        return done.stdout

    def fetch(self, sha: str):
        self.run("fetch", "--no-tags", "--no-recurse-submodules", "--depth=2", "--filter=blob:none", "origin", sha)
        if self.run("cat-file", "-t", sha).strip() != b"commit":
            raise ValueError("expected commit object")

    def tree(self, sha: str) -> dict[str, str]:
        result, folded = {}, set()
        for row in self.run("ls-tree", "-rz", "--full-tree", sha).split(b"\0"):
            if not row:
                continue
            head, name_bytes = row.split(b"\t", 1)
            mode, kind, oid = head.split()
            name = name_bytes.decode("utf-8")
            _safe_name(name)
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                raise ValueError("links and submodules refused")
            if name.casefold() in folded:
                raise ValueError("case-colliding tree refused")
            folded.add(name.casefold())
            result[name] = oid.decode("ascii")
        # Windows also aliases directory components independently of the leaf.
        prefixes = {}
        for name in result:
            parts = PurePosixPath(name).parts
            for size in range(1, len(parts) + 1):
                prefix = "/".join(parts[:size])
                previous = prefixes.setdefault(prefix.casefold(), prefix)
                if previous != prefix:
                    raise ValueError("case-colliding tree refused")
        return result

    def blob(self, oid: str) -> bytes:
        # cat-file does not apply attributes, text conversion, filters or LFS smudge.
        return self.run("cat-file", "blob", oid)


def _reachable_tests(tree: dict[str, str], changed: dict[str, fit.ChangedFile], objects: _Objects) -> dict[str, bytes]:
    modules = {fit._module(path): path for path in tree if path.endswith(".py")}
    test_paths = {path for path in tree if is_test(path)}
    roots = {path for path in changed if path in test_paths}
    pending, seen, helpers = list(roots), set(), {}
    data = {path: pair.after for path, pair in changed.items() if pair.after is not None}

    def reach(name):
        if not name:
            return
        parts = name.split(".")
        for size in range(1, len(parts) + 1):
            path = modules.get(".".join(parts[:size]))
            if path is not None:
                pending.append(path)

    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if path not in data:
            data[path] = objects.blob(tree[path])
        content = data[path]
        if path in test_paths and path not in roots:
            helpers[path] = content
        package = fit._module(path).split(".")
        if not path.endswith("/__init__.py"):
            package = package[:-1]
        for size in range(1, len(package) + 1):
            marker = modules.get(".".join(package[:size]))
            if marker and marker.endswith("/__init__.py"):
                pending.append(marker)
        try:
            syntax = ast.parse(content.decode("utf-8"))
        except (SyntaxError, ValueError, UnicodeError):
            # Keep bytes: the unchanged fit filter decides parse and decode flags.
            continue
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    reach(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    if node.level > len(package):
                        continue
                    base = ".".join(package[:len(package) - node.level + 1] + ([base] if base else []))
                reach(base)
                for alias in node.names:
                    reach(f"{base}.{alias.name}" if base else alias.name)
    return helpers


def _changes(objects: _Objects, parent: str, merge: str, before: dict[str, str],
             after: dict[str, str]) -> dict[str, fit.ChangedFile]:
    # Name-only tree diff includes mode changes even when both blob IDs are equal.
    names = objects.run("diff-tree", "-r", "--no-commit-id", "--name-only", "-z", "--no-renames",
                        "--no-ext-diff", "--no-textconv", parent, merge, "--")
    changes = {}
    for raw in names.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        _safe_name(path)
        if path not in before and path not in after:
            raise ValueError("changed path missing from trees")
        changes[path] = fit.ChangedFile(objects.blob(before[path]) if path in before else None,
                                        objects.blob(after[path]) if path in after else None)
    return changes


def read_candidate(entry: dict, cache: Path, *, source: Path | None = None,
                   repository_root: Path | None = None) -> fit.Candidate:
    identity = entry_id(entry)
    name = repository_id(entry["repository"])
    cache = external_path(cache, repository_root)
    origin = f"https://github.com/{name}.git"
    if source is not None:
        source = local_path(source)
        if not source.is_dir():
            raise ValueError("expected local fixture repository")
        origin = source.as_uri()
    cache.mkdir(parents=True, exist_ok=True)
    root = local_path(cache / digest({"repository": name, "origin": origin}))
    root.mkdir(exist_ok=True)
    # Check pre-existing cache components before any git command can follow them.
    for path in (root / "objects.git", root / "empty-hooks"):
        local_path(path)
    objects = _Objects(root, origin, local=source is not None)
    merge, parent = entry["merge_commit"], entry["parent"]
    objects.fetch(merge)
    objects.fetch(parent)
    raw = objects.run("cat-file", "commit", merge).split(b"\n\n", 1)[0]
    parents = [line.removeprefix(b"parent ").decode("ascii") for line in raw.splitlines()
               if line.startswith(b"parent ")]
    if len(parents) not in {1, 2} or parents[0] != parent:
        raise ValueError("pool parent differs from commit")
    before, after = objects.tree(parent), objects.tree(merge)
    changes = _changes(objects, parent, merge, before, after)
    parent_tree = Path(tempfile.mkdtemp(prefix="parent-", dir=root))
    for path, oid in before.items():
        if not path.endswith(".py"):
            continue
        target = parent_tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(objects.blob(oid))
    helpers = _reachable_tests(after, changes, objects)
    return fit.Candidate(name, identity, parent_tree, changes, helpers)
