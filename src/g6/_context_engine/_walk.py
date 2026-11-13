# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's walker: the files of one folder that may be indexed, and every file that may not, with why.

``_``-prefixed, so it never registers as a component. Every check that can exclude a file runs on its name and its
status before the file is opened: a credential name, a git-ignored path, a link, a cloud placeholder, the size cap, an
unsupported type and the count cap. A file that is opened is hashed and listed; nothing is excluded after its bytes
were read, except a file that could not be read to the end (``unreadable``). ``enumerated`` (entries looked at),
``read`` (files opened) and ``read_bytes`` are reported separately, so a folder full of large or generated files shows
as many entries enumerated and few read.

Ignored files come from ``git ls-files -co --exclude-standard`` run through ``ctx_git``'s hardened runner when the
folder is in a repository under the permitted roots. When git cannot answer, ``ignored_by`` is ``"builtin"`` and
``note`` says ignored files were not excluded beyond the built-in list.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from g6._context_engine._scope import NOT_INDEXABLE, Binding, ScopeDenied, inside

MAX_FILES = 5_000
MAX_FILE_BYTES = 2_000_000
#: Entries looked at before the walk stops: a folder this large is reported, not crawled to the end.
MAX_ENUMERATED = 200_000
Why = Literal["credential", "ignored", "link", "outside_root", "too_large", "unsupported", "placeholder",
              "unreadable", "over_file_limit"]
#: File names that are credentials (matched on the name alone, case folded), and folders nothing is read under.
CREDENTIAL_FILES = (".env*", "*.pem", "*.key", "*.pfx", "id_rsa*", "id_ed25519*", ".npmrc", ".pypirc", ".netrc",
                    "credentials*")
CREDENTIAL_FOLDERS = frozenset({".git", ".ssh", ".aws", ".g6", ".codex", ".claude"})
#: Generated or vendored folders skipped whether or not git answers.
BUILTIN_IGNORED = frozenset({"node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
                             ".ruff_cache"})
#: Suffix to the media type a file is read as; anything else is ``unsupported``.
MEDIA_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain", ".rst": "text/plain",
               ".py": "text/x-python", ".html": "text/html", ".htm": "text/html"}
_PLACEHOLDER = 0x1000 | 0x40000 | 0x400000   # offline, recall on open, recall on data access
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class FileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative: str
    size: int
    sha256: str
    media_type: str


class Excluded(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative: str
    why: Why


class Walked(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    files: tuple[FileEntry, ...]
    excluded: tuple[Excluded, ...]
    enumerated: int
    read: int
    read_bytes: int
    ignored_by: Literal["builtin", "git"] = "builtin"
    note: str = ""


def is_credential(name: str) -> bool:
    folded = name.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern) for pattern in CREDENTIAL_FILES)


def folder_rule(name: str) -> str:
    """Why nothing is read under a folder of this name (``"credential"`` or ``"ignored"``), or ``""``. The one name
    rule for folders: case is folded once, here, and the rule holds for every component from the permitted root
    down, the folder a walk starts in included."""
    folded = name.casefold()
    if folded in CREDENTIAL_FOLDERS:
        return "credential"
    return "ignored" if folded in BUILTIN_IGNORED else ""


def named_under_refused(binding: Binding, path: Path | str) -> bool:
    """Whether ``path``, read as text and without touching the file system, names a folder nothing is read under,
    or something below one: any component under a permitted root (or of a relative path) that the one name rule
    refuses, with the trailing dots and spaces and the stream suffix Windows ignores taken off first."""
    parts = [p for p in re.split(r"[\\/]+", str(path)) if p not in ("", ".")]
    for root in binding.roots:
        above = [p for p in re.split(r"[\\/]+", str(root)) if p not in ("", ".")]
        if [p.casefold() for p in parts[:len(above)]] == [p.casefold() for p in above]:
            parts = parts[len(above):]
            break
    return any(folder_rule(part.split(":", 1)[0].rstrip(". ") or part) for part in parts)


def is_placeholder(found: os.stat_result) -> bool:
    """Whether the status is a cloud placeholder's (offline, or recalled when opened or read): opening one would make
    the system download it, so no reader of source files opens it."""
    return bool(getattr(found, "st_file_attributes", 0) & _PLACEHOLDER)


def shown_name(name: str) -> str:
    """``name`` as it can be written down: one that is not valid Unicode is shown with its odd units escaped."""
    try:
        name.encode("utf-8")
        return name
    except UnicodeEncodeError:
        return name.encode("utf-8", "backslashreplace").decode("ascii", "replace")


def read_file(path: Path, limit: int, expected: os.stat_result | None = None) -> bytes:
    """The bytes of the regular file at ``path``, at most ``limit`` of them; ``OSError`` when it is a link, is not the
    file ``expected`` describes, or is longer than ``limit``. The one place the walker and the engine open a source
    file."""
    if is_placeholder(os.lstat(path)):
        raise OSError("the file is a cloud placeholder; it is not opened")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        found = os.lstat(path)
        if not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(found.st_mode) \
                or getattr(found, "st_file_attributes", 0) & _REPARSE:
            raise OSError("not a plain file")
        if (opened.st_dev, opened.st_ino) != (found.st_dev, found.st_ino):
            raise OSError("the file changed while it was opened")
        if expected is not None and (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise OSError("the file is not the one that was listed")
        chunks, total = [], 0
        while True:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise OSError("the file is longer than the cap")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def tracked(binding: Binding, root: Path) -> tuple[frozenset[str] | None, str]:
    """The files under ``root`` that git tracks or would track (not ignored), as paths relative to ``root``; or
    ``None`` and why git did not answer. Only a repository whose top folder is inside a permitted root is asked."""
    top = next((folder for folder in (root, *root.parents)
                if any(folder == r or r in folder.parents for r in binding.roots) and (folder / ".git").exists()),
               None)
    if top is None:
        return None, "the folder is in no git repository under the permitted roots"
    try:
        from g6.sources.ctx_git import config as git_config, runner
        from g6.sources.ctx_git.config import GitConfig, GitRefused
    except ImportError as exc:  # pragma: no cover - the sources family ships with G6
        return None, f"ctx_git is not importable ({type(exc).__name__})"
    try:
        repo, gitdir = git_config.repository(str(top), GitConfig(roots=binding.roots, remote_hosts=(),
                                                                 source="the Context Engine's host binding"))
        prefix = root.relative_to(repo).as_posix()
        with runner.session(repo, gitdir) as run:
            listed = run("ls-files", "-z", "-co", "--exclude-standard", "--", prefix or ".").out
    except GitRefused as exc:
        return None, f"git did not answer ({exc.code})"
    except (OSError, ValueError) as exc:
        return None, f"git did not answer ({type(exc).__name__})"
    names = set()
    for raw in listed.split(b"\x00"):
        if raw:
            path = raw.decode("utf-8", "surrogateescape")
            names.add(path[len(prefix) + 1:] if prefix and prefix != "." and path.startswith(prefix + "/") else path)
    return frozenset(names), ""


def _is_link(found: os.stat_result) -> bool:
    return stat.S_ISLNK(found.st_mode) or bool(getattr(found, "st_file_attributes", 0) & _REPARSE)


def walk(binding: Binding, root: Path, *, max_files: int = MAX_FILES,
         max_file_bytes: int = MAX_FILE_BYTES) -> Walked:
    """The files under ``root`` that may be indexed and the ones that may not; see the module docstring.
    :class:`ScopeDenied` when ``root`` is not a folder inside a permitted root."""
    if named_under_refused(binding, root):   # by name alone, before anything at or under the path is looked at
        raise ScopeDenied(NOT_INDEXABLE)
    real = inside(binding, root)
    above = next(r for r in binding.roots if real == r or r in real.parents)
    for part in real.relative_to(above).parts:   # the folder itself and every folder between it and the root
        if folder_rule(part):
            raise ScopeDenied(NOT_INDEXABLE)
    if not real.is_dir():
        raise ScopeDenied(NOT_INDEXABLE)
    listed, note = tracked(binding, real)
    files: list[FileEntry] = []
    excluded: list[Excluded] = []
    enumerated = read = read_bytes = 0
    pending = [real]
    stopped = False
    while pending and not stopped:
        folder = pending.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            excluded.append(Excluded(relative=folder.relative_to(real).as_posix() + "/", why="unreadable"))
            continue
        folders = []
        for entry in entries:
            enumerated += 1
            if enumerated > MAX_ENUMERATED:
                excluded.append(Excluded(relative=folder.relative_to(real).as_posix() + "/...", why="over_file_limit"))
                stopped = True
                break
            path = Path(entry.path)
            relative = path.relative_to(real).as_posix()
            if shown_name(relative) != relative:   # a name that is not valid Unicode: listed, never read
                excluded.append(Excluded(relative=shown_name(relative), why="unreadable"))
                continue
            try:
                found = os.lstat(path)
            except OSError:
                excluded.append(Excluded(relative=relative, why="unreadable"))
                continue
            if _is_link(found):
                excluded.append(Excluded(relative=relative, why="link"))
            elif stat.S_ISDIR(found.st_mode):
                if folder_rule(entry.name):
                    excluded.append(Excluded(relative=relative + "/", why=folder_rule(entry.name)))
                else:
                    folders.append(path)
            elif is_credential(entry.name):
                excluded.append(Excluded(relative=relative, why="credential"))
            elif listed is not None and relative not in listed:
                excluded.append(Excluded(relative=relative, why="ignored"))
            elif is_placeholder(found):
                excluded.append(Excluded(relative=relative, why="placeholder"))
            elif not stat.S_ISREG(found.st_mode):
                excluded.append(Excluded(relative=relative, why="unsupported"))
            elif found.st_nlink > 1:
                excluded.append(Excluded(relative=relative, why="link"))
            elif found.st_size > max_file_bytes:
                excluded.append(Excluded(relative=relative, why="too_large"))
            elif path.suffix.lower() not in MEDIA_TYPES:
                excluded.append(Excluded(relative=relative, why="unsupported"))
            elif len(files) >= max_files:
                excluded.append(Excluded(relative=relative, why="over_file_limit"))
            else:
                try:
                    data = read_file(path, max_file_bytes, found)
                except OSError:
                    excluded.append(Excluded(relative=relative, why="unreadable"))
                    continue
                read += 1
                read_bytes += len(data)
                files.append(FileEntry(relative=relative, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                       media_type=MEDIA_TYPES[path.suffix.lower()]))
        pending.extend(reversed(folders))
    if listed is None:
        note = f"{note}; ignored files were not excluded beyond the built-in list"
    return Walked(root=str(real), files=tuple(sorted(files, key=lambda f: f.relative)),
                  excluded=tuple(sorted(excluded, key=lambda e: e.relative)), enumerated=enumerated, read=read,
                  read_bytes=read_bytes, ignored_by="git" if listed is not None else "builtin", note=note)


__all__ = ["BUILTIN_IGNORED", "CREDENTIAL_FILES", "CREDENTIAL_FOLDERS", "MAX_ENUMERATED", "MAX_FILES",
           "MAX_FILE_BYTES", "MEDIA_TYPES", "Excluded", "FileEntry", "Walked", "folder_rule", "is_credential",
           "named_under_refused",
           "is_placeholder", "read_file", "shown_name", "tracked", "walk"]
