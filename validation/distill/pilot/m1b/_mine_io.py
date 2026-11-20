"""Small data-only IO helpers shared by the mining commands."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+\Z")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def repository_id(value: str) -> str:
    if (not isinstance(value, str) or not _REPOSITORY.fullmatch(value)
            or value.split("/")[1] in {".", ".."}):
        raise ValueError("repository id refused")
    return value.lower()


def entry_id(entry: dict) -> str:
    name = repository_id(entry["repository"])
    if type(entry["pr"]) is not int or entry["pr"] < 1:
        raise ValueError("pull request number refused")
    for key in ("merge_commit", "parent"):
        if not isinstance(entry[key], str) or not _SHA.fullmatch(entry[key]):
            raise ValueError("commit id refused")
    return f"{name}#{entry['pr']}"


def local_path(path: Path, *, sealed: bool = False) -> Path:
    path = Path(path).absolute()
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError("linked path refused")
        if not sealed and component.name.lower().startswith("final-"):
            raise ValueError("sealed path refused")
    return path.resolve()


def external_path(path: Path, repository_root: Path | None = None) -> Path:
    path = local_path(path)
    if path.is_relative_to((repository_root or ROOT).resolve()):
        raise ValueError("expected path outside repository")
    return path


def read_json(path: Path):
    return json.loads(local_path(path).read_text(encoding="utf-8"))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value) -> None:
    path = local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(prefix=".m1b-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def cohort_ids(data) -> set[str]:
    """Accept ID lists or cohort manifests containing repository lists or bugs."""
    if isinstance(data, dict):
        if "repositories" in data:
            data = data["repositories"]
        elif "bugs" in data:
            data = data["bugs"]
        else:
            raise ValueError("cohort repository list missing")
    if not isinstance(data, list):
        raise ValueError("expected repository list")
    result = set()
    for row in data:
        if isinstance(row, dict):
            row = row.get("repository", row.get("repo"))
        result.add(repository_id(row))
    return result


def pool_entries(data: dict) -> list[dict]:
    entries = data["entries"]
    if not isinstance(entries, list):
        raise ValueError("expected pool entries")
    seen = set()
    for row in entries:
        identity = entry_id(row)
        if identity in seen:
            raise ValueError("duplicate pool entry")
        seen.add(identity)
    return entries


def pool_ids(data: dict) -> set[str]:
    return ({repository_id(row["repository"]) for row in data.get("repositories", [])}
            | {repository_id(row["repository"]) for row in pool_entries(data)})
