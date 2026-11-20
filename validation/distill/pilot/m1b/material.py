"""Read acquisition material from immutable git trees without executing source."""
from __future__ import annotations

import os
import subprocess
import tempfile
from copy import copy
from pathlib import Path, PurePosixPath

from .._material import MaterialError, changed_files
from . import access_m1b, gitread
from ._mine_io import digest, external_path, local_path

MAX_SOURCE = 131072
MAX_SUPPORT = 1048576


def _text(path, *, support=False):
    reason = "support_missing_or_large" if support else "fix_source_missing_or_large"
    if not path.is_file() or path.stat().st_size > MAX_SOURCE:
        raise MaterialError(reason)
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE + 1)
    if len(data) > MAX_SOURCE:
        raise MaterialError(reason)
    try:
        return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError:
        raise MaterialError("source_not_utf8") from None


def _checkout_run(objects, *args):
    # Match the image's Linux defaults, retaining all of _Objects.run's safety settings.
    return objects.run("-c", "core.autocrlf=false", "-c", "core.eol=lf",
                       "-c", f"core.attributesFile={os.devnull}", *args)


def _checkout(objects, sha, root):
    root.mkdir()
    reader = copy(objects)
    reader.env = {**objects.env, "GIT_INDEX_FILE": str(root.parent / (root.name + ".index")),
                  "GIT_WORK_TREE": str(root)}
    _checkout_run(reader, "read-tree", sha)
    _checkout_run(reader, "checkout-index", "-a", "-f", f"--prefix={root.as_posix()}/")
    return reader


def _read(entry, cache, *, source=None, repository_root=None):
    cache = external_path(cache, repository_root)
    origin = f"https://github.com/{entry['repository']}.git"
    if source is not None:
        source = local_path(source)
        if not source.is_dir():
            raise MaterialError("extraction_failed")
        origin = source.as_uri()
    cache.mkdir(parents=True, exist_ok=True)
    root = local_path(cache / digest({"repository": entry["repository"], "origin": origin}))
    root.mkdir(exist_ok=True)
    for path in (root / "objects.git", root / "empty-hooks"):
        local_path(path)
    objects = gitread._Objects(root, origin, local=source is not None)
    parent, merge = entry["parent"], entry["merge_commit"]
    return _from_objects(objects, parent, merge)


def _from_objects(objects, parent, merge):
    objects.fetch(merge)
    objects.fetch(parent)
    headers = objects.run("cat-file", "commit", merge).split(b"\n\n", 1)[0]
    parents = [line[7:].decode("ascii") for line in headers.splitlines() if line.startswith(b"parent ")]
    if len(parents) not in {1, 2} or parents[0] != parent:
        raise MaterialError("extraction_failed")
    before, after = objects.tree(parent), objects.tree(merge)
    status = objects.run("diff-tree", "-r", "--no-commit-id", "--name-status", "--no-renames",
                         "--no-ext-diff", "--no-textconv", parent, merge, "--").decode("utf-8")
    # Refuse unsupported descriptors before materialising any source.
    changed_files({"name_status": status})
    # Both trees have been checked for unsafe paths, links and submodules above.
    with tempfile.TemporaryDirectory(prefix="material-", dir=objects.root) as scratch:
        base_root, fixed_root = Path(scratch) / "base", Path(scratch) / "fixed"
        _checkout(objects, parent, base_root)
        fixed_objects = _checkout(objects, merge, fixed_root)
        return _from_checkouts(fixed_objects, parent, merge, before, after, base_root, fixed_root)


def _from_checkouts(objects, parent, merge, before, after, base_root, fixed_root):
    # The image diffs in a checkout of the fixed commit, so the fixed tree's .gitattributes (e.g. -diff) apply;
    # --attr-source (git 2.40+) reads them from that tree here, where the object cache is bare.
    attributes = f"--attr-source={merge}"
    status = _checkout_run(objects, attributes, "diff", "--name-status", "--no-renames", "--no-ext-diff",
                           "--no-textconv", parent, merge, "--").decode("utf-8")
    names = changed_files({"name_status": status})
    base, fixed = {}, {}
    for name in names:
        gitread._safe_name(name)
        if name not in before and name not in after:
            raise MaterialError("extraction_failed")
        if PurePosixPath(name).suffix != ".py":
            continue
        if name in before:
            base[name] = _text(base_root / name)
        if name in after:
            fixed[name] = _text(fixed_root / name)
    # Match extraction support mode, including fixed import context but excluding hidden support paths.
    total = sum(len(text.encode("utf-8")) for text in fixed.values())
    # rglob('*.py') also visits directories; include blob parents to match that refusal.
    support_paths = set(after)
    for name in after:
        support_paths.update(path.as_posix() for path in PurePosixPath(name).parents if path.parts)
    for name in sorted(support_paths):
        if not name.endswith(".py") or any(part.startswith(".") for part in PurePosixPath(name).parts):
            continue
        if name not in fixed:
            fixed[name] = _text(fixed_root / name, support=True)
            total += len(fixed[name].encode("utf-8"))
        if total > MAX_SUPPORT:
            raise MaterialError("support_source_too_large")
    patch = _checkout_run(objects, attributes, "diff", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv",
                          "--src-prefix=a/", "--dst-prefix=b/", parent, merge, "--").decode("utf-8")
    patch = patch.replace("\r\n", "\n").replace("\r", "\n")
    body = {"name_status": status, "patch": patch, "base": base, "fixed": fixed}
    changed_files(body)
    return body


def material_from_git(manifest: dict, bug_id: str, cache: Path, *, source: Path | None = None,
                      repository_root: Path | None = None, inventory=None, pool=None, environment=None) -> dict:
    """Return the image extractor's four keys, for acquisition only.

    source and repository_root have the git reader's offline fixture semantics;
    pool/manifest data cannot supply a local path or transport.
    """
    entry = access_m1b.acquisition_bug(manifest, bug_id, inventory=inventory, pool=pool, environment=environment)
    try:
        return _read(entry, cache, source=source, repository_root=repository_root)
    except MaterialError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        reason = {"git path refused": "unsupported_fix_path",
                  "links and submodules refused": "support_linked_path"}.get(str(exc), "extraction_failed")
        raise MaterialError(reason) from None
