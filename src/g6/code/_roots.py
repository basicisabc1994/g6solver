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
import stat
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from g6.code._types import CodeRefused
from g6.core.storage import PathRefused, _elsewhere, _gate, _text, confined_path, plain_file, state_path

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
#: How many hard-linked files a walk names (it counts them all).
HARD_LINK_NAMES = 10
CONFIG_MAX_BYTES = 1024 * 1024


@contextmanager
def _folder_handle(path: Path) -> Iterator[int]:
    """Open folder metadata, following its current name, and always close the handle."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = create(str(path), 0x80, 7, None, 3, 0x02000000, None)  # attributes; share; open; folder
        if handle == wintypes.HANDLE(-1).value:
            raise OSError("folder metadata unavailable")
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
    else:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        yield fd
    finally:
        os.close(fd)


def _final_path(fd: int) -> Path | None:
    """The opened object's final path, without resolving its original name again."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        get = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        get.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get.restype = wintypes.DWORD
        size = 512
        while size <= 65536:
            buffer = ctypes.create_unicode_buffer(size)
            used = get(msvcrt.get_osfhandle(fd), buffer, size, 0)
            if not used:
                raise OSError("opened path unavailable")
            if used < size:
                text = buffer.value
                if text.startswith("\\\\?\\UNC\\"):
                    raise PathRefused("the opened file is on a network path", outside=True)
                if text.startswith("\\\\?\\"):
                    text = text[4:]
                return Path(os.path.normpath(text))
            size = used + 1
        raise OSError("opened path unavailable")
    descriptor = f"/proc/self/fd/{fd}"
    if os.path.islink(descriptor):
        return Path(os.path.realpath(descriptor))
    return None


def _identity(info: os.stat_result) -> tuple[int, int]:
    if not info.st_ino:
        raise OSError("folder identity unavailable")
    return info.st_dev, info.st_ino


def _folder_state(folder: Path) -> tuple[Path, tuple[int, int]]:
    with _folder_handle(folder) as fd:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("not a folder")
        final = _final_path(fd)
        if final is None:
            # On systems without descriptor paths, match the resolved name to the opened folder.
            final = folder.resolve(strict=True)
            if _identity(os.lstat(final)) != _identity(info):
                raise OSError("folder changed")
        return final, _identity(info)


@dataclass(frozen=True)
class Roots:
    """Final folder paths and their identities, captured once at the caller's check."""

    roots: tuple[Path, ...]
    source: str
    identities: tuple[tuple[int, int], ...] = field(init=False, repr=False)
    #: Declared folders that could not be opened at the check (missing, not a folder, unreadable). They contain nothing:
    #: they are not in ``roots``, so no check admits a path under them; the other declared folders keep working.
    unavailable: tuple[Path, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        captured: list[tuple[Path, tuple[int, int]]] = []
        missing: list[Path] = []
        for root in self.roots:
            try:
                given = Path(_text(root))
            except (OSError, ValueError, TypeError):
                raise CodeRefused(PATH_INVALID, "a declared folder is not a path G6 can read") from None
            try:
                captured.append(_folder_state(given))
            except (OSError, ValueError):
                missing.append(given)
        object.__setattr__(self, "roots", tuple(final for final, _ in captured))
        object.__setattr__(self, "identities", tuple(identity for _, identity in captured))
        object.__setattr__(self, "unavailable", tuple(missing))

    def refuse_if_empty(self, doing: str, path: object = None) -> None:
        """No usable folder: NO_ROOT without a declaration; OUTSIDE_ROOT for a path under no declared folder at all;
        PATH_INVALID for a path under an unavailable declared folder (or one that cannot be read lexically)."""
        if self.roots:
            return
        if self.unavailable:
            if _lexically_outside(path, self.unavailable):
                raise CodeRefused(OUTSIDE_ROOT, f"the path is not at or under a declared root, so nothing is {doing}: "
                                  + how_to_declare())
            raise CodeRefused(PATH_INVALID, "no checked folder could be opened when it was checked, so nothing is "
                              f"{doing}")
        raise CodeRefused(NO_ROOT, f"no folder is declared for the code extensions, so nothing is {doing}: "
                          + how_to_declare())

    def describe(self) -> dict[str, object]:
        return {"roots": [str(r) for r in self.roots], "source": self.source}


def parse(text: str, source: str) -> Roots:
    """The declaration in ``text`` (JSON); :class:`CodeRefused` (``G6_E_CODE_CONFIG_INVALID``) when it cannot be used.
    The message never repeats a value from it."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError, TypeError, MemoryError):
        raise CodeRefused(CONFIG_INVALID, f"{source} is not JSON G6 can read") from None
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
        except (ValueError, OSError):
            raise CodeRefused(CONFIG_INVALID, f"{source}: a root is not a local folder G6 can check") from None
    try:
        return Roots(tuple(resolved), source)
    except CodeRefused:
        raise CodeRefused(CONFIG_INVALID, f"{source}: a root is not a folder G6 can check") from None


def load(environ: Mapping[str, str] | None = None) -> Roots:
    """The person's declaration: ``G6_CODE_CONFIG``, else the file under the G6 home, else none (no root)."""
    env = os.environ if environ is None else environ
    raw = env.get(CONFIG_ENV, "").strip()
    if raw:
        return parse(raw, CONFIG_ENV)
    try:
        file = state_path(*CONFIG_FILE)
        text = None
        if file.exists():
            plain_file(file)
            with file.open("rb") as handle:
                data = handle.read(CONFIG_MAX_BYTES + 1)
            if len(data) > CONFIG_MAX_BYTES:
                raise ValueError("configuration too large")
            text = data.decode("utf-8")
    except (OSError, ValueError, TypeError, RecursionError, MemoryError):
        raise CodeRefused(CONFIG_INVALID, "the code configuration file under the G6 home could not be read as "
                                          "UTF-8 text; check code/config.json") from None
    if text is not None:
        return parse(text, "the code configuration file under the G6 home")
    return Roots((), "no configuration")


def how_to_declare() -> str:
    return (f"the person who runs G6 declares the folders the code extensions may use in {CONFIG_ENV} or in "
            "code/config.json under G6's home, as {\"roots\": [\"<absolute folder>\"]}")


def for_folder(folder: Path, source: str) -> Roots:
    """Capture a caller-owned folder at its check, once, for all subsequent walks and reads.

    For installed packages and their nearby tests, whose roots come from installation rather than configuration.
    Never construct a root from a file's parent at read time.
    """
    try:
        resolved = confined_path(folder, (folder,))
    except (OSError, ValueError):
        raise CodeRefused(PATH_INVALID, "the folder could not be checked for reading") from None
    return Roots((resolved,), source)


def _name(given: Path, roots: Roots) -> str:
    """The caller's lexical name relative to a root, without exposing a resolved machine path."""
    for root in roots.roots:
        if given == root or root in given.parents:
            return given.relative_to(root).as_posix()
    return given.name or "path"


def _refused_path(exc: ValueError | OSError, what: str) -> CodeRefused:
    outside = getattr(exc, "outside", False)
    reason = ("is not at or under a declared root (a link out of one counts as outside)" if outside
              else "is not a path G6 can check")
    return CodeRefused(OUTSIDE_ROOT if outside else PATH_INVALID, f"{what} {reason}")


def _lexically_outside(path: object, roots: tuple[Path, ...]) -> bool:
    """Whether an absolute ``path`` is, by its words alone, under none of ``roots``; False when it cannot be read so."""
    try:
        text = _text(path) if isinstance(path, str) else os.fspath(path) if isinstance(path, Path) else None
        if not text or not Path(text).is_absolute():
            return False
        lexical = Path(os.path.normpath(text))
        return not any(lexical == root or root in lexical.parents for root in roots)
    except (OSError, ValueError, TypeError, CodeRefused):
        return False


def _refuse_unavailable(path: Path, roots: Roots) -> None:
    """A missing declaration stays unavailable even when another declared folder is usable."""
    lexical = Path(os.path.normpath(path))
    if (any(lexical == root or root in lexical.parents for root in roots.unavailable)
            and not any(lexical == root or root in lexical.parents for root in roots.roots)):
        raise CodeRefused(PATH_INVALID, "the declared folder for this path could not be opened when it was checked")


def under(path: Path, roots: tuple[Path, ...]) -> bool:
    """Compare final paths component by component, preserving the file system's own case."""
    return any(path.parts[:len(root.parts)] == root.parts for root in roots)


def _check_roots(roots: Roots) -> None:
    """A changed or unavailable folder never becomes a new declaration."""
    try:
        for root, identity in zip(roots.roots, roots.identities, strict=True):
            final, current = _folder_state(root)
            if current != identity or not under(final, (root,)) or not under(root, (final,)):
                raise OSError("folder changed")
    except (OSError, ValueError):
        raise CodeRefused(OUTSIDE_ROOT, "the folder changed since it was checked") from None


def _given(raw: str, what: str) -> Path:
    # a plain str, read once (storage._text), so a str subclass's own methods cannot show a check one path and open
    # another
    raw = _text(raw) if isinstance(raw, str) else ""
    if not raw.strip() or any(ord(ch) < 32 for ch in raw):
        raise CodeRefused(PATH_INVALID, f"give {what}: an absolute path under a declared root")
    if raw.startswith("~") or not Path(raw).is_absolute():
        raise CodeRefused(PATH_INVALID, f"{what} is an absolute path (a relative one, or ~, is never resolved against "
                                        "the folder G6 runs in)")
    return Path(raw)


def existing(raw: str, roots: Roots, *, what: str = "path", kind: str = "any") -> Path:
    """The file or folder ``raw`` names, resolved; :class:`CodeRefused` unless it exists at or under a declared root.
    ``kind`` is ``file``, ``folder`` or ``any``."""
    roots.refuse_if_empty("read", raw)
    try:
        given = _given(raw, what)
        _refuse_unavailable(given, roots)
        _check_roots(roots)
        real = confined_path(given, roots.roots)
        _check_roots(roots)
    except (ValueError, OSError) as exc:          # labelled by the refusal's own outside flag
        raise _refused_path(exc, what) from None
    name = _name(given, roots)
    if not under(real, roots.roots):
        raise CodeRefused(OUTSIDE_ROOT, f"{what} {name} is not at or under a declared root (a link "
                                        f"out of one counts as outside): {how_to_declare()}")
    try:
        if kind == "file" and not real.is_file():
            raise CodeRefused(PATH_INVALID, f"{what} is not a file: {name}")
        if kind == "folder" and not real.is_dir():
            raise CodeRefused(PATH_INVALID, f"{what} is not a folder: {name}")
        if kind == "any" and not real.exists():
            raise CodeRefused(PATH_INVALID, f"nothing exists at {what}: {name}")
    except (OSError, ValueError):
        raise CodeRefused(PATH_INVALID, f"{name}: the file or folder could not be checked") from None
    return real


def destination(raw: str, roots: Roots, *, what: str = "dest") -> Path:
    """A folder an op may write into: an absolute path under a declared root (never a root itself) that does not exist
    yet or is an empty folder, whose parent folder exists."""
    roots.refuse_if_empty("written", raw)
    try:
        given = _given(raw, what)
        _refuse_unavailable(given, roots)
        _check_roots(roots)
        real = confined_path(given, roots.roots)
        _check_roots(roots)
    except (ValueError, OSError) as exc:          # labelled by the refusal's own outside flag
        raise _refused_path(exc, what) from None
    name = _name(given, roots)
    if not under(real, roots.roots) or real in roots.roots:
        raise CodeRefused(OUTSIDE_ROOT, f"{what} {name} is not inside a declared root: "
                                        f"{how_to_declare()}")
    try:
        if real.exists() and (not real.is_dir() or any(real.iterdir())):
            raise CodeRefused(PATH_INVALID, f"{name} already exists and is not an empty folder; nothing is overwritten")
        if not real.parent.is_dir():
            raise CodeRefused(PATH_INVALID, f"the parent folder of {name} does not exist")
    except (OSError, ValueError):
        raise CodeRefused(PATH_INVALID, f"{name}: the destination folder could not be checked") from None
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
    #: The hard-linked files skipped (also counted in ``skipped_links``), and the first of their names, relative to the
    #: folder walked.
    skipped_hard_links: int = 0
    hard_links: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, object]:
        return {"files": len(self.files), "bytes": self.total_bytes, "skipped_links": self.skipped_links,
                "skipped_large": self.skipped_large, "skipped_unreadable": self.skipped_unreadable,
                "entries": self.entries, "truncated": self.truncated, "truncated_reason": self.truncated_reason,
                "skipped_hard_links": self.skipped_hard_links, "hard_links": list(self.hard_links)}

    def hard_links_said(self) -> str:
        """The hard-linked files skipped, in words a person reads: how many, and the first of their names."""
        count, more = self.skipped_hard_links, self.skipped_hard_links - len(self.hard_links)
        return (f"{count} module{'s' if count != 1 else ''} skipped because "
                f"{'they are hard links' if count != 1 else 'it is a hard link'}: {', '.join(self.hard_links)}"
                + (f" and {more} more" if more else ""))


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def walk(folder: Path, roots: Roots, *, max_files: int, max_file_bytes: int, suffixes: tuple[str, ...] = (),
         skip_dirs: frozenset[str] = SKIP_DIRS, max_entries: int = 50_000, timeout_s: float = 5.0) -> Walk:
    """The files under ``folder`` (already confined by :func:`existing`), each with a suffix in ``suffixes`` (any when
    empty), at most ``max_files`` of them and none larger than ``max_file_bytes``. A link or junction is never
    followed; a file whose resolved path leaves the roots, and a hard-linked file (:func:`read_text` refuses it), is
    skipped and counted in ``skipped_links``; a hard-linked file is also counted in ``skipped_hard_links`` and named
    (the first :data:`HARD_LINK_NAMES`) in ``hard_links``."""
    found = Walk()
    deadline = time.monotonic() + timeout_s
    for path in _files(folder, skip_dirs, found, max_entries, deadline):
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        try:
            _check_roots(roots)
        except CodeRefused:
            found.skipped_links += 1
            continue
        if _is_link(path) or not under(path.resolve(), roots.roots):
            found.skipped_links += 1
            continue
        try:
            info = path.stat()
        except OSError:
            found.skipped_unreadable += 1
            continue
        if info.st_nlink > 1:               # a hard link, which read_text refuses: skipped, counted as a link and named
            found.skipped_links += 1
            found.skipped_hard_links += 1
            if len(found.hard_links) < HARD_LINK_NAMES:
                found.hard_links.append(path.relative_to(folder).as_posix())
            continue
        size = info.st_size
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


def _walk_open_identity(path: Path, opened: os.stat_result, roots: Roots) -> Path:
    """Without descriptor paths, compare the file to an lstat walk anchored by the captured root identity."""
    for root, identity in zip(roots.roots, roots.identities, strict=True):
        if not under(path, (root,)):
            continue
        relative = path.relative_to(root)
        if not relative.parts or any(p in (".", "..") for p in relative.parts):
            break
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if _identity(os.fstat(fd)) != identity:
                break
            for part in relative.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.stat(relative.name, dir_fd=fd, follow_symlinks=False)
            if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and _identity(info) == _identity(opened)):
                return root / relative
        finally:
            os.close(fd)
    raise PathRefused("the opened file could not be matched to a declared folder", outside=True)


def _preopen_components(path: Path, roots: Roots) -> None:
    """Refuse reparse points and special objects before the content open, without following a component."""
    for root, identity in zip(roots.roots, roots.identities, strict=True):
        if not under(path, (root,)):
            continue
        relative = path.parts[len(root.parts):]
        current = root
        for index, part in enumerate((None, *relative)):
            if part is not None:
                current = current / part
            info = os.lstat(current)
            directory = index < len(relative)
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
                raise PathRefused("a read component is a link or a special object", outside=True)
            if index == 0 and _identity(info) != identity:
                raise PathRefused("the declared folder changed", outside=True)
        return
    raise PathRefused("the read is outside the declared folders", outside=True)


def path_info(path: Path, *, roots: Roots) -> os.stat_result | None:
    """Check optional metadata without following any component; None only for a missing name.

    Use the shared lexical gates before lstat, then walk from a captured root. Never resolve an unchecked
    component, including a reparse point whose target might be a pipe or device. Refusing a reparse point
    does not establish that its target is outside, so that refusal is PATH_INVALID.
    """
    roots.refuse_if_empty("checked", path)
    name = "path"
    try:
        text = _text(path)
        if _elsewhere(text):
            raise CodeRefused(OUTSIDE_ROOT, "the metadata path is a network or device path")
        given = _given(text, "path")
        _refuse_unavailable(given, roots)
        name = _name(given, roots)
        lexical = Path(os.path.normpath(given))
        for root, identity in zip(roots.roots, roots.identities, strict=True):
            count = len(root.parts)
            if tuple(map(os.path.normcase, given.parts[:count])) != tuple(map(os.path.normcase, root.parts)):
                continue
            if not (lexical == root or root in lexical.parents):
                raise CodeRefused(OUTSIDE_ROOT, f"{name} is not at or under a checked root")
            relative = given.parts[count:]
            if _gate(*relative):
                raise CodeRefused(PATH_INVALID, f"{name} is not a path G6 can check")
            named_root = Path(*given.parts[:count])
            if named_root.parts != root.parts:
                # Case-insensitive lexical matching must not substitute another folder on a case-sensitive
                # volume. Walk the caller's alternate spelling before querying its root identity.
                for prefix in (*reversed(named_root.parents), named_root):
                    info = os.lstat(prefix)
                    if (not stat.S_ISDIR(info.st_mode)
                            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                        raise CodeRefused(PATH_INVALID, f"{name} has a linked or special root component")
                if _identity(info) != identity:
                    raise CodeRefused(OUTSIDE_ROOT, f"{name} names a different folder at the checked root")
            current = root
            for index, part in enumerate((None, *relative)):
                if part is not None:
                    current = current.parent if part == ".." else current / part
                    if not (current == root or root in current.parents):
                        raise CodeRefused(OUTSIDE_ROOT, f"{name} leaves the checked root")
                try:
                    info = os.lstat(current)
                except FileNotFoundError:
                    if index == 0:
                        raise CodeRefused(PATH_INVALID, "the checked folder is no longer available") from None
                    if ".." in relative[index:]:
                        raise CodeRefused(PATH_INVALID, f"{name} has an unavailable parent before '..'") from None
                    return None
                if (stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                        or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                    raise CodeRefused(PATH_INVALID, f"{name} has a linked or special path component")
                if index == 0 and _identity(info) != identity:
                    raise CodeRefused(OUTSIDE_ROOT, "the folder changed since it was checked")
                if index < len(relative) and not stat.S_ISDIR(info.st_mode):
                    raise CodeRefused(PATH_INVALID, f"{name} has a parent that is not a folder")
                # a second name for a regular file shows in st_nlink; read_text refuses it when content is read
            _check_roots(roots)
            return info
        raise CodeRefused(OUTSIDE_ROOT, f"{name} is not at or under a checked root")
    except (OSError, ValueError, MemoryError):
        raise CodeRefused(PATH_INVALID, f"{name} could not be checked without following links") from None


def _prepare_write_parent(parent: Path, base: Path, recorded: dict[Path, tuple[int, int]]) -> None:
    """Make folders and retain each lstat identity; recheck all of them immediately before the caller opens."""
    if not under(parent, (base,)):
        raise CodeRefused(OUTSIDE_ROOT, "the write parent is outside its checked folder")
    folders = [base]
    for part in parent.parts[len(base.parts):]:
        folders.append(folders[-1] / part)

    def check(folder: Path) -> None:
        info = os.lstat(folder)
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise CodeRefused(OUTSIDE_ROOT, "a write parent is a link or is no longer a folder")
        identity = _identity(info)
        if folder in recorded and recorded[folder] != identity:
            raise CodeRefused(OUTSIDE_ROOT, "a write parent changed since it was made")
        recorded.setdefault(folder, identity)

    for folder in folders:
        if folder in recorded:
            check(folder)
        folder.mkdir(exist_ok=True)
        check(folder)
    for folder in folders:
        check(folder)


def read_text(path: Path, max_bytes: int, *, roots: Roots) -> str:
    """Read only after the opened handle is inside unchanged captured roots and is a regular, single-link file.

    The name check is preliminary. The handle's final path decides containment; neither a path lookup's identity
    nor a root's newly resolved name can authorize the read. Every refusal uses G6's words and a relative name.
    """
    if not isinstance(roots, Roots) or not (roots.roots or roots.unavailable):
        raise CodeRefused(NO_ROOT, "no checked folder was supplied for the read")
    roots.refuse_if_empty("read", path)
    name = "file"
    try:
        path = Path(_text(path))
        _refuse_unavailable(path, roots)
        name = _name(path, roots)
        _check_roots(roots)
        path = confined_path(path, roots.roots)
        try:
            found = plain_file(path)
        except PathRefused as exc:
            raise CodeRefused(PATH_INVALID, f"{path.name}: {exc.reason}") from None
        if found.st_size > max_bytes:
            raise CodeRefused(PATH_INVALID, f"{path.name} exceeds {max_bytes} bytes")
        _preopen_components(path, roots)
        # NONBLOCK keeps a POSIX file replaced by a pipe from blocking before fstat can refuse it.
        options = {} if os.name == "nt" else {"opener": lambda p, flags: os.open(
            p, flags | os.O_NONBLOCK | os.O_NOFOLLOW)}
        with open(path, "rb", **options) as handle:
            opened = os.fstat(handle.fileno())
            final = _final_path(handle.fileno())
            if final is None:
                final = _walk_open_identity(path, opened, roots)
            if not under(final, roots.roots):
                raise PathRefused("the opened file is outside the declared folders", outside=True)
            _check_roots(roots)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise CodeRefused(PATH_INVALID, f"{name} is not a regular file kept under one name")
            if _identity(opened) != _identity(found):
                raise CodeRefused(PATH_INVALID, f"{name} changed between the check and the read")
            if opened.st_size > max_bytes:
                raise CodeRefused(PATH_INVALID, f"{name} exceeds {max_bytes} bytes")
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise CodeRefused(PATH_INVALID, f"{path.name} grew beyond {max_bytes} bytes")
    except ValueError as exc:               # where the name is (confined_path): labelled by its own outside flag
        if getattr(exc, "outside", False):
            raise CodeRefused(OUTSIDE_ROOT, f"{name} is not at or under a declared root (a link out of one counts as "
                                            "outside)") from None
        raise CodeRefused(PATH_INVALID, f"{name} is not a path G6 can check, so it is not read") from None
    except OSError:
        raise CodeRefused(PATH_INVALID, f"cannot read {name}: the file could not be opened or read (access is "
                                        "denied, or another program holds it)") from None
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


__all__ = ["CONFIG_ENV", "CONFIG_FILE", "CONFIG_INVALID", "NO_ROOT", "OUTSIDE_ROOT", "PATH_INVALID", "SKIP_DIRS",
           "Roots", "Walk", "destination", "existing", "for_folder", "how_to_declare", "load", "parse", "path_info", "read_text", "under",
           "walk"]
