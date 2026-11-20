"""Read acquisition material from immutable git trees without executing source."""
from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from .._material import MaterialError, changed_files
from . import access_m1b, gitread
from ._mine_io import digest, external_path, local_path

MAX_SOURCE = 131072
MAX_SUPPORT = 1048576


def _text(objects, oid, *, support=False):
    reason = "support_missing_or_large" if support else "fix_source_missing_or_large"
    if int(objects.run("cat-file", "-s", oid)) > MAX_SOURCE:
        raise MaterialError(reason)
    data = objects.blob(oid)
    if len(data) > MAX_SOURCE:
        raise MaterialError(reason)
    try:
        return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError:
        raise MaterialError("source_not_utf8") from None


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
    names = objects.run("diff-tree", "-r", "--no-commit-id", "--name-only", "-z", "--no-renames",
                        "--no-ext-diff", "--no-textconv", parent, merge, "--")
    status, base, fixed = [], {}, {}
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8")
        gitread._safe_name(name)
        if name not in before and name not in after:
            raise MaterialError("extraction_failed")
        kind = "M" if name in before and name in after else "A" if name in after else "D"
        status.append(f"{kind}\t{name}\n")
        if not name.endswith(".py"):
            continue
        if name in before:
            base[name] = _text(objects, before[name])
        if name in after:
            fixed[name] = _text(objects, after[name])
    # Match extraction support mode, including fixed import context but excluding hidden support paths.
    total = sum(len(text.encode("utf-8")) for text in fixed.values())
    for name in sorted(after):
        if not name.endswith(".py") or any(part.startswith(".") for part in PurePosixPath(name).parts):
            continue
        if name not in fixed:
            fixed[name] = _text(objects, after[name], support=True)
            total += len(fixed[name].encode("utf-8"))
        if total > MAX_SUPPORT:
            raise MaterialError("support_source_too_large")
    patch = objects.run("-c", "core.quotePath=false", "diff-tree", "-r", "-p", "--no-commit-id",
                        "--no-renames", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/",
                        parent, merge, "--").decode("utf-8")
    body = {"name_status": "".join(status), "patch": patch, "base": base, "fixed": fixed}
    changed_files(body)
    return body


def material_from_git(manifest: dict, bug_id: str, cache: Path, *, source: Path | None = None,
                      repository_root: Path | None = None) -> dict:
    """Return the image extractor's four keys, for acquisition only.

    source and repository_root have the git reader's offline fixture semantics;
    pool/manifest data cannot supply a local path or transport.
    """
    entry = access_m1b.acquisition_bug(manifest, bug_id)
    try:
        return _read(entry, cache, source=source, repository_root=repository_root)
    except MaterialError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        reason = {"git path refused": "unsupported_fix_path",
                  "links and submodules refused": "support_linked_path"}.get(str(exc), "extraction_failed")
        raise MaterialError(reason) from None
