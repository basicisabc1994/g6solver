# SPDX-License-Identifier: Apache-2.0
"""Where a code extension may read and write files: the folders the person who runs G6 declares.

``_``-prefixed, so it never registers as a component. The person declares the folders as JSON, in the environment
variable ``G6_CODE_CONFIG`` or in the file ``code/config.json`` under the G6 home (``g6.core.storage.state_path``);
the variable wins when both are there::

    {"roots": ["C:/work/project", "/home/me/src"]}

With no declaration there is no root, so every op that names a path is refused (``G6_E_CODE_NO_ROOT``): nothing is
ever read from, or written to, the folder G6 happens to run in. A declaration that does not parse is refused whole.

A path an agent names is absolute. :func:`existing` resolves it (``..`` and links followed) and refuses it unless what
it resolves to is at or under a root; :func:`destination` does the same for a folder an op would create. :func:`walk`
lists the files under a folder, each resolved and checked again (a link out of the root, or a junction, is skipped and
counted), with the folders that hold no source of the repository's own left out, and a cap on files and bytes that
is said, never silent. The same rule as ``g6.sources.ctx_git``'s roots: a link out of a root counts as outside.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from g6.code._types import CodeRefused
from g6.core.storage import confined_path, state_path

CONFIG_ENV = "G6_CODE_CONFIG"
CONFIG_FILE = ("code", "config.json")
NO_ROOT = "G6_E_CODE_NO_ROOT"
OUTSIDE_ROOT = "G6_E_CODE_OUTSIDE_ROOT"
CONFIG_INVALID = "G6_E_CODE_CONFIG_INVALID"
PATH_INVALID = "G6_E_CODE_PATH_INVALID"
#: Folders a walk never enters: version control, environments, caches and installed packages are not the repository's
#: own source.
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".tox", ".nox", "venv", ".venv",
                       ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".eggs", "site-packages",
                       ".next", ".nyc_output", "coverage"})


@dataclass(frozen=True)
class Roots:
    """The declared roots (resolved) and where the declaration came from."""

    roots: tuple[Path, ...]
    source: str

    def describe(self) -> dict[str, object]:
        return {"roots": [str(r) for r in self.roots], "source": self.source}


def parse(text: str, source: str) -> Roots:
    """The declaration in ``text`` (JSON); :class:`CodeRefused` (``G6_E_CODE_CONFIG_INVALID``) when it cannot be used.
    The message never repeats a value from it."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodeRefused(CONFIG_INVALID, f"{source} is not JSON ({exc.msg} at line {exc.lineno})") from None
    if not isinstance(data, Mapping) or set(data) - {"roots"}:
        raise CodeRefused(CONFIG_INVALID, f"{source} must be a JSON object with only roots")
    roots = data.get("roots", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) and r.strip() for r in roots):
        raise CodeRefused(CONFIG_INVALID, f"{source}: roots must be a list of folder paths")
    resolved = []
    for raw in roots:
        if raw.startswith("~") or not Path(raw).is_absolute():
            raise CodeRefused(CONFIG_INVALID, f"{source}: each root is an absolute path (~ is not expanded)")
        try:
            resolved.append(confined_path(raw, (Path(raw),)))
        except (ValueError, OSError) as exc:
            raise CodeRefused(CONFIG_INVALID, f"{source}: {exc}") from None
    return Roots(tuple(resolved), source)


def load(environ: Mapping[str, str] | None = None) -> Roots:
    """The person's declaration: ``G6_CODE_CONFIG``, else the file under the G6 home, else none (no root)."""
    env = os.environ if environ is None else environ
    raw = env.get(CONFIG_ENV, "").strip()
    if raw:
        return parse(raw, CONFIG_ENV)
    file = state_path(*CONFIG_FILE)
    if file.is_file():
        return parse(file.read_text(encoding="utf-8"), "the code configuration file under the G6 home")
    return Roots((), "no configuration")


def how_to_declare() -> str:
    return (f"the person who runs G6 declares the folders the code extensions may use in {CONFIG_ENV} or in "
            f"{state_path(*CONFIG_FILE)}, as {{\"roots\": [\"<absolute folder>\"]}}")


def under(path: Path, roots: tuple[Path, ...]) -> bool:
    """Whether the resolved ``path`` is a root or inside one."""
    return any(path == root or root in path.parents for root in roots)


def _given(raw: str, what: str) -> Path:
    if not isinstance(raw, str) or not raw.strip() or any(ord(ch) < 32 for ch in raw):
        raise CodeRefused(PATH_INVALID, f"give {what}: an absolute path under a declared root")
    if raw.startswith("~") or not Path(raw).is_absolute():
        raise CodeRefused(PATH_INVALID, f"{what} is an absolute path (a relative one, or ~, is never resolved against "
                                        "the folder G6 runs in)")
    return Path(raw)


def existing(raw: str, roots: Roots, *, what: str = "path", kind: str = "any") -> Path:
    """The file or folder ``raw`` names, resolved; :class:`CodeRefused` unless it exists at or under a declared root.
    ``kind`` is ``file``, ``folder`` or ``any``."""
    if not roots.roots:
        raise CodeRefused(NO_ROOT, f"no folder is declared for the code extensions, so nothing is read: "
                                   f"{how_to_declare()}")
    try:
        real = confined_path(_given(raw, what), roots.roots)
    except (ValueError, OSError) as exc:
        raise CodeRefused(OUTSIDE_ROOT, f"{what}: {exc}") from None
    if not under(real, roots.roots):
        raise CodeRefused(OUTSIDE_ROOT, f"{what} resolves to {real}, which is not at or under a declared root (a link "
                                        f"out of one counts as outside): {how_to_declare()}")
    if kind == "file" and not real.is_file():
        raise CodeRefused(PATH_INVALID, f"{what} is not a file: {real}")
    if kind == "folder" and not real.is_dir():
        raise CodeRefused(PATH_INVALID, f"{what} is not a folder: {real}")
    if kind == "any" and not real.exists():
        raise CodeRefused(PATH_INVALID, f"nothing exists at {what}: {real}")
    return real


def destination(raw: str, roots: Roots, *, what: str = "dest") -> Path:
    """A folder an op may write into: an absolute path under a declared root (never a root itself) that does not exist
    yet or is an empty folder, whose parent folder exists."""
    if not roots.roots:
        raise CodeRefused(NO_ROOT, f"no folder is declared for the code extensions, so nothing is written: "
                                   f"{how_to_declare()}")
    try:
        real = confined_path(_given(raw, what), roots.roots)
    except (ValueError, OSError) as exc:
        raise CodeRefused(OUTSIDE_ROOT, f"{what}: {exc}") from None
    if not under(real, roots.roots) or real in roots.roots:
        raise CodeRefused(OUTSIDE_ROOT, f"{what} resolves to {real}, which is not inside a declared root: "
                                        f"{how_to_declare()}")
    if real.exists() and (not real.is_dir() or any(real.iterdir())):
        raise CodeRefused(PATH_INVALID, f"{real} already exists and is not an empty folder; nothing is overwritten")
    if not real.parent.is_dir():
        raise CodeRefused(PATH_INVALID, f"the folder {real.parent} does not exist")
    return real


@dataclass
class Walk:
    """What :func:`walk` found: the files (resolved, in a stable order), and what it left out and why."""

    files: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    skipped_links: int = 0
    skipped_large: int = 0
    truncated: bool = False
    entries: int = 0
    skipped_unreadable: int = 0
    truncated_reason: str = ""

    def describe(self) -> dict[str, object]:
        return {"files": len(self.files), "bytes": self.total_bytes, "skipped_links": self.skipped_links,
                "skipped_large": self.skipped_large, "skipped_unreadable": self.skipped_unreadable,
                "entries": self.entries, "truncated": self.truncated, "truncated_reason": self.truncated_reason}


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def walk(folder: Path, roots: Roots, *, max_files: int, max_file_bytes: int, suffixes: tuple[str, ...] = (),
         skip_dirs: frozenset[str] = SKIP_DIRS, max_entries: int = 50_000, timeout_s: float = 5.0) -> Walk:
    """The files under ``folder`` (already confined by :func:`existing`), each with a suffix in ``suffixes`` (any when
    empty), at most ``max_files`` of them and none larger than ``max_file_bytes``. A link or junction is never
    followed; a file whose resolved path leaves the roots is skipped."""
    found = Walk()
    deadline = time.monotonic() + timeout_s
    for path in _files(folder, skip_dirs, found, max_entries, deadline):
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        if _is_link(path) or not under(path.resolve(), roots.roots):
            found.skipped_links += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            found.skipped_unreadable += 1
            continue
        if size > max_file_bytes:
            found.skipped_large += 1
            continue
        if len(found.files) >= max_files:
            found.truncated = True
            found.truncated_reason = "file cap"
            break
        found.files.append(path)
        found.total_bytes += size
    return found


def _files(folder: Path, skip_dirs: frozenset[str], found: Walk, max_entries: int,
           deadline: float) -> Iterator[Path]:
    """Every file under ``folder``, folders in name order, never entering a link, a junction or a skipped folder."""
    def exhausted() -> bool:
        reason = ("deadline" if time.monotonic() >= deadline
                  else "entry cap" if found.entries >= max_entries else "")
        if reason:
            found.truncated, found.truncated_reason = True, reason
        return bool(reason)

    stack = [folder]
    while stack:
        if exhausted():
            return
        current = stack.pop()
        entries = []
        try:
            with os.scandir(current) as scan:
                while not exhausted():
                    entry = next(scan, None)
                    if entry is None:
                        break
                    found.entries += 1
                    entries.append(Path(entry.path))
        except OSError:
            found.skipped_unreadable += 1
            continue
        dirs = []
        for entry in sorted(entries):
            if time.monotonic() >= deadline:
                found.truncated, found.truncated_reason = True, "deadline"
                return
            if _is_link(entry):
                found.skipped_links += 1
                continue
            try:
                if entry.is_dir():
                    if entry.name not in skip_dirs:
                        dirs.append(entry)
                elif entry.is_file():
                    yield entry
            except OSError:
                found.skipped_unreadable += 1
        if found.truncated:
            return
        stack.extend(reversed(dirs))


def read_text(path: Path, max_bytes: int, *, roots: Roots | None = None) -> str:
    """A contained read, refusing oversized files before opening them. At most ``max_bytes`` of a file, as text with the line endings ``Path.read_text`` gives (undecodable bytes
    replaced)."""
    try:
        path = confined_path(path, roots.roots if roots is not None else (path.parent,))
        if path.stat().st_size > max_bytes:
            raise CodeRefused(PATH_INVALID, f"{path.name} exceeds {max_bytes} bytes")
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise CodeRefused(PATH_INVALID, f"{path.name} grew beyond {max_bytes} bytes")
    except ValueError as exc:
        raise CodeRefused(OUTSIDE_ROOT, str(exc)) from None
    except OSError as exc:
        raise CodeRefused(PATH_INVALID, f"cannot read {path.name}: {exc}") from None
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


__all__ = ["CONFIG_ENV", "CONFIG_FILE", "CONFIG_INVALID", "NO_ROOT", "OUTSIDE_ROOT", "PATH_INVALID", "SKIP_DIRS",
           "Roots", "Walk", "destination", "existing", "how_to_declare", "load", "parse", "read_text", "under",
           "walk"]
