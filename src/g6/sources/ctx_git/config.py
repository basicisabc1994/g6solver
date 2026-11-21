# SPDX-License-Identifier: Apache-2.0
"""Where ctx_git may run git: the folders the person who runs G6 declares, and the remote hosts it may reach.

The person declares them as JSON, in the environment variable ``G6_GIT_CONFIG`` or in the file ``ctx_git/config.json``
under the G6 home (``g6.core.storage.state_path``); the variable wins when both are there::

    {"roots": ["C:/work/project", "/home/me/src"], "remote_hosts": ["github.com"]}

* ``roots``: folders. A repository at or under one may be used, and ``clone`` may make one inside one.
* ``remote_hosts``: the hosts ``fetch``, ``pull``, ``push`` and ``clone`` may reach over https or ssh; none by default.

With no configuration there is no root, so every op that runs git is refused (``G6_E_GIT_NO_ROOT``); git is never
pointed at the folder G6 happens to run in. A configuration that does not parse is refused whole.

An agent names a repository by its absolute path. :func:`repository` resolves it (``..`` and links followed) and
refuses it unless it is at or under a root and is the top folder of a working copy whose ``.git`` also resolves
inside a root, and so does every other folder git would read or write for it: the common git folder a ``commondir``
file names (a worktree's refs and objects live there), the ``objects`` and ``refs`` folders, and every entry of
``objects/info/alternates``, followed through each alternate's own list (git borrows objects from those), and the
repository and worktree configs' nested include paths. Configs have byte and time budgets; alternates also have
line budgets. This is checked before any git command runs. :func:`inside` does the same for every file an op names
in the repository, and
refuses the ``.git`` folder itself.
:func:`remote_problem` says why a remote's address may not be reached (only https and ssh, only a declared host, no
password in the address, and the host must resolve to public addresses only); ``""`` when it may.
"""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from time import monotonic
from urllib.parse import urlsplit

from g6.code import _roots
from g6.code._types import CodeRefused
from g6.core import http
from g6.core.storage import _elsewhere, confined_path, plain_file, state_path
from g6.sources.ctx_git import _config_includes, _refusals

CONFIG_ENV = "G6_GIT_CONFIG"
CONFIG_FILE = ("ctx_git", "config.json")
NO_ROOT = "G6_E_GIT_NO_ROOT"
OUTSIDE_ROOT = "G6_E_GIT_OUTSIDE_ROOT"
PATH_INVALID = "G6_E_GIT_PATH_INVALID"
NOT_A_REPOSITORY = "G6_E_GIT_NOT_A_REPOSITORY"
CONFIG_INVALID = "G6_E_GIT_CONFIG_INVALID"
REMOTE_REFUSED = "G6_E_GIT_REMOTE_REFUSED"
#: How deep git follows alternates of alternates; a list nested deeper is refused.
ALTERNATES_DEPTH = 5
#: Distinct object folders, including the repository's own and missing targets; repeated identities spend once.
ALTERNATES_FOLDERS = 64
CONFIG_MAX_BYTES = 1024 * 1024
METADATA_MAX_BYTES = 64 * 1024
METADATA_FILE_LINES = 1024
METADATA_TOTAL_LINES = 4096
ADMISSION_SECONDS = 10.0
CONFIG_INCLUDE_FILES = 64
CONFIG_INCLUDE_DEPTH = 10
#: The address forms a network op may reach: an https URL, an ssh URL, or ssh's ``user@host:path`` form.
_SCP = re.compile(r"^(?:(?P<user>[A-Za-z0-9._-]+)@)?(?P<host>[A-Za-z0-9.-]+):(?!//)(?P<path>[^\s]+)$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


class GitRefused(Exception):
    """An op ctx_git will not run, with a stable code and a registered reason.

    Unknown identifiers produce a fixed fallback, never their supplied text.
    """

    def __init__(self, code: str, identifier: str, *, key: str = "", scope: str = "", metadata: str = "") -> None:
        reason = _refusals.render(identifier, key=key, scope=scope, metadata=metadata)
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason = code, reason


@dataclass(frozen=True)
class GitConfig:
    """The declared roots (resolved) and remote hosts (lower case), and where they came from."""

    roots: tuple[Path, ...]
    remote_hosts: tuple[str, ...]
    source: str

    def describe(self) -> dict[str, object]:
        return {"roots": [str(r) for r in self.roots], "remote_hosts": list(self.remote_hosts), "source": self.source}


@dataclass
class _Admission:
    """One repository's aggregate work budget, shared by includes and alternates."""

    deadline: float = field(default_factory=lambda: monotonic() + ADMISSION_SECONDS)
    lines: int = 0
    configs: set[Path] = field(default_factory=set)
    active_configs: set[Path] = field(default_factory=set)
    missing_alternates: set[Path] = field(default_factory=set)

    def check(self) -> None:
        if monotonic() >= self.deadline:
            raise GitRefused(PATH_INVALID, 'config_check_1')

    def account(self, text: str) -> str:
        self.check()
        # Count physical lines, including repeated, blank and comment lines, before parsing or target checks.
        count = text.count("\n") + bool(text and not text.endswith("\n"))
        self.lines += count
        if count > METADATA_FILE_LINES or self.lines > METADATA_TOTAL_LINES:
            raise GitRefused(PATH_INVALID, 'config_account_1')
        return text

    def alternate_limit(self, checked: set[tuple[int, int]]) -> None:
        if len(checked) + len(self.missing_alternates) >= ALTERNATES_FOLDERS:
            raise GitRefused(PATH_INVALID, 'alternate_folders')


def parse(text: str, source: str) -> GitConfig:
    """The configuration in ``text`` (JSON); :class:`GitRefused` (``G6_E_GIT_CONFIG_INVALID``) when it cannot be used.
    The message never repeats a value from it."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError, TypeError, MemoryError):
        raise GitRefused(CONFIG_INVALID, 'config_json') from None
    if not isinstance(data, Mapping) or set(data) - {"roots", "remote_hosts"}:
        raise GitRefused(CONFIG_INVALID, 'config_object')
    roots, hosts = data.get("roots", []), data.get("remote_hosts", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) and r.strip() for r in roots):
        raise GitRefused(CONFIG_INVALID, 'config_roots')
    if not isinstance(hosts, list) or not all(isinstance(h, str) and _HOST.fullmatch(h.lower()) for h in hosts):
        raise GitRefused(CONFIG_INVALID, 'config_hosts')
    resolved = []
    for raw in roots:
        path = Path(raw)
        if raw.startswith("~") or not path.is_absolute():
            raise GitRefused(CONFIG_INVALID, 'config_absolute_root')
        try:
            resolved.append(confined_path(raw, (path,)))
        except (ValueError, OSError):
            raise GitRefused(CONFIG_INVALID, 'config_root_check') from None
    return GitConfig(tuple(resolved), tuple(h.lower() for h in hosts), source)


def load(environ: Mapping[str, str] | None = None) -> GitConfig:
    """The person's configuration: ``G6_GIT_CONFIG``, else the file under the G6 home, else none (no root)."""
    env = os.environ if environ is None else environ
    raw = env.get(CONFIG_ENV, "").strip()
    if raw:
        return parse(raw, CONFIG_ENV)
    try:
        file = state_path(*CONFIG_FILE)
        if file.exists():
            plain_file(file)
            with file.open("rb") as handle:
                data = handle.read(CONFIG_MAX_BYTES + 1)
            if len(data) > CONFIG_MAX_BYTES:
                raise ValueError("configuration too large")
            return parse(data.decode("utf-8"), "the ctx_git configuration file under the G6 home")
    except (OSError, ValueError, TypeError, RecursionError, MemoryError):
        raise GitRefused(CONFIG_INVALID, 'config_load_1') from None
    return GitConfig((), (), "no configuration")


def _confined(raw: str | Path, roots: tuple[Path, ...], what: str = "") -> Path:
    try:
        return confined_path(raw, roots)
    except (ValueError, OSError, MemoryError) as exc:
        code = OUTSIDE_ROOT if getattr(exc, "outside", False) else PATH_INVALID
        if code == OUTSIDE_ROOT:
            raise GitRefused(code, "path_outside") from None
        raise GitRefused(code, 'path_check') from None


def _name(path: Path, roots: tuple[Path, ...]) -> str:
    for root in roots:
        if path == root or root in path.parents:
            return path.relative_to(root).as_posix()
    return path.name or "path"


def _under(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _metadata_refusal(exc: Exception, name: str) -> GitRefused:
    outside = isinstance(exc, CodeRefused) and exc.code == _roots.OUTSIDE_ROOT
    if outside:
        return GitRefused(OUTSIDE_ROOT, "metadata_outside", metadata=name)
    return GitRefused(PATH_INVALID, 'metadata_check', metadata=name)


def _metadata_info(path: Path, name: str, roots: _roots.Roots) -> os.stat_result | None:
    """Optional git metadata, checked root to leaf without following a component."""
    try:
        return _roots.path_info(path, roots=roots)
    except (CodeRefused, OSError, ValueError, MemoryError) as exc:
        raise _metadata_refusal(exc, name) from None


def _metadata_text(path: Path, name: str, roots: _roots.Roots) -> str:
    """Read metadata through the same checked, capped reader as repository content."""
    try:
        _roots.path_info(path, roots=roots)
        return _roots.read_text(path, METADATA_MAX_BYTES, roots=roots)
    except (CodeRefused, OSError, ValueError, MemoryError) as exc:
        raise _metadata_refusal(exc, name) from None


def _metadata_bytes(path: Path, name: str, roots: _roots.Roots) -> bytes:
    """Config syntax must see the original bytes, with a 1 MiB confined read cap."""
    try:
        _roots.path_info(path, roots=roots)
        return _roots.read_bytes(path, CONFIG_MAX_BYTES, roots=roots)
    except (CodeRefused, OSError, ValueError, MemoryError) as exc:
        raise _metadata_refusal(exc, name) from None


def _include_path(entry: bytes) -> str:
    # Git for Windows interprets path bytes as UTF-8. POSIX uses the filesystem
    # encoding; do not let surrogateescape authorise an undecodable include.
    import sys

    return entry.decode("utf-8" if os.name == "nt" else sys.getfilesystemencoding(), "strict")


def _metadata_target(path: Path, name: str, roots: _roots.Roots) -> Path:
    # The no-follow walk precedes even confined_path's preliminary name resolution.
    _metadata_info(path, name, roots)
    return _confined(path, roots.roots, name)


def _metadata_entry(text: str, base: Path, name: str, roots: _roots.Roots) -> Path:
    """Admit only unambiguous relative entries or checked local absolute entries.

    Git for Windows treats a DOS drive prefix as absolute even without its separator. Never join that
    spelling with base: classify the drive-root interpretation by words only, then refuse it either way.
    Rooted, network and device spellings are refused before target I/O.
    """
    if (not text or text != text.strip() or text.startswith('"')
            or any(ord(ch) < 32 or 0xD800 <= ord(ch) <= 0xDFFF for ch in text)):
        raise GitRefused(PATH_INVALID, 'metadata_spelling', metadata=name)
    windows = PureWindowsPath(text)
    if text.startswith(("\\\\", "//")):
        raise GitRefused(OUTSIDE_ROOT, 'metadata_network', metadata=name)
    if windows.drive and not windows.root:
        if os.name == "nt" and re.fullmatch(r"[A-Za-z]:", windows.drive):
            git_path = Path(os.path.normpath(windows.drive + "/" + text[2:]))
            if not _under(git_path, roots.roots):
                raise GitRefused(OUTSIDE_ROOT, 'metadata_drive_outside', metadata=name)
        raise GitRefused(PATH_INVALID, 'metadata_drive_relative', metadata=name)
    if windows.root and not windows.drive and (os.name == "nt" or text.startswith("\\")):
        raise GitRefused(PATH_INVALID, 'metadata_rooted', metadata=name)
    if os.name != "nt" and windows.drive:
        raise GitRefused(PATH_INVALID, 'metadata_drive', metadata=name)
    target = Path(text)
    return _metadata_target(target if target.is_absolute() else base / target, name, roots)


def repository(raw: str, config: GitConfig, *, file_policy: set[str] | None = None) -> tuple[Path, Path]:
    """The working copy ``raw`` names and its git folder, both resolved; :class:`GitRefused` unless both are at or
    under a declared root. ``file_policy`` optionally records denied tree-file keys;
    sessions always neutralise them, even without the collector. Repository
    info/exclude and info/attributes retain Git's native behaviour."""
    admission = _Admission()
    if not config.roots:
        raise GitRefused(NO_ROOT, 'repository_no_roots')
    if not raw.strip():
        raise GitRefused(NOT_A_REPOSITORY, 'config_repository_1')
    if raw.startswith("~") or not Path(raw).is_absolute():
        raise GitRefused(NOT_A_REPOSITORY, 'config_repository_2')
    if _elsewhere(raw):     # on its words, before the roots are captured or anything is resolved
        raise GitRefused(OUTSIDE_ROOT, "metadata_outside", metadata="repo")
    try:
        roots = _roots.Roots(config.roots, "the declared git folders")
        roots.refuse_if_empty("checked", raw)
    except (CodeRefused, OSError, ValueError, MemoryError) as exc:
        raise _metadata_refusal(exc, "repo") from None
    repo_info = _metadata_info(Path(raw), "repo", roots)
    real = _metadata_target(Path(raw), "repo", roots)
    if not _under(real, config.roots):
        raise GitRefused(OUTSIDE_ROOT, 'repository_outside')
    dotgit = real / ".git"
    dotgit_info = _metadata_info(dotgit, ".git", roots)
    if repo_info is None or not stat.S_ISDIR(repo_info.st_mode) or dotgit_info is None:
        raise GitRefused(NOT_A_REPOSITORY, 'repository_top')
    gitdir = _metadata_target(dotgit, ".git", roots)
    if stat.S_ISREG(dotgit_info.st_mode):
        admission.check()
        line = _metadata_text(dotgit, ".git", roots).strip()
        if not line.startswith("gitdir:"):
            raise GitRefused(NOT_A_REPOSITORY, 'repository_gitfile')
        gitdir = _metadata_entry(line.partition(":")[2].strip(), real, ".git", roots)
    if not _under(gitdir, config.roots):
        raise GitRefused(OUTSIDE_ROOT, 'repository_gitdir_outside')
    gitdir_info = _metadata_info(gitdir, ".git", roots)
    if gitdir_info is None or not stat.S_ISDIR(gitdir_info.st_mode):
        raise GitRefused(NOT_A_REPOSITORY, 'repository_gitdir_missing')
    common = _common(gitdir, roots, admission)
    admission.check()
    for file in dict.fromkeys((gitdir / "config", common / "config", gitdir / "config.worktree")):
        _includes(file, roots, admission, worktree=real, file_policy=file_policy)
    for name in ("objects", "refs"):
        info = _metadata_info(common / name, name, roots)
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise GitRefused(PATH_INVALID, 'repository_metadata_folder')
    _alternates(common / "objects", roots, 0, admission=admission)
    admission.check()
    return real, gitdir


def _common(gitdir: Path, roots: _roots.Roots, admission: _Admission | None = None) -> Path:
    """The common git folder ``gitdir``'s ``commondir`` file names (``gitdir`` itself when there is none), resolved;
    :class:`GitRefused` when it is outside every root."""
    if admission is not None:
        admission.check()
    named = gitdir / "commondir"
    info = _metadata_info(named, "commondir", roots)
    if info is None:
        return gitdir
    text = _metadata_text(named, "commondir", roots) if stat.S_ISREG(info.st_mode) else ""
    if admission is not None:
        admission.check()
    text = text.rstrip("\r\n")
    if not text.strip():
        raise GitRefused(NOT_A_REPOSITORY, 'config_common_1')
    common = _metadata_entry(text, gitdir, "commondir", roots)
    if not _under(common, roots.roots):
        raise GitRefused(OUTSIDE_ROOT, 'config_common_2')
    common_info = _metadata_info(common, "commondir", roots)
    if common_info is None or not stat.S_ISDIR(common_info.st_mode):
        raise GitRefused(NOT_A_REPOSITORY, 'config_common_3')
    return common


def _includes(path: Path, roots: _roots.Roots, admission: _Admission, depth: int = 0, *, worktree: Path,
              file_policy: set[str] | None = None) -> None:
    """Confine repository includes and blame paths; neutralise other tree-file keys.

    Only repository-owned config enters this reader. Git follows the person's own
    global/system files and includes itself, with their normal precedence.
    """
    admission.check()
    if path in admission.active_configs or depth > CONFIG_INCLUDE_DEPTH:
        raise GitRefused(PATH_INVALID, 'config_includes_1')
    if path in admission.configs:
        return
    if len(admission.configs) >= CONFIG_INCLUDE_FILES:
        raise GitRefused(PATH_INVALID, 'config_includes_2')
    admission.configs.add(path)
    info = _metadata_info(path, "repository config", roots)
    if info is None:  # Git ignores a missing include, after its spelling and confinement were checked.
        return
    admission.active_configs.add(path)
    try:
        data = _metadata_bytes(path, "repository config", roots)
        for key, raw_entry in _config_includes.file_entries(data, check=admission.check):
            admission.check()
            if key in {b"core.excludesfile", b"core.attributesfile"}:
                # These are unconditionally denied before every Git query.
                # Never expand or inspect a target that cannot be used anyway.
                if file_policy is not None:
                    file_policy.add(key.decode("ascii"))
                continue
            entry = _include_path(raw_entry)
            include = key in {b"include.path", b"includeif.path"}
            if not entry and not include:
                continue  # No target; blame's CLI option clears the accumulated list.
            if entry.startswith("~"):
                # Git uses HOME. Without a bound home spelling, do not guess another user's expansion.
                home = os.environ.get("HOME", "")
                if (entry != "~" and not entry.startswith("~/")) or not home or not Path(home).is_absolute():
                    raise ValueError("config home expansion cannot be classified")
                entry = home if entry == "~" else home.rstrip("/\\") + "/" + entry[2:]
            if entry.startswith("%(prefix)"):
                raise ValueError("git installation prefix cannot be classified")
            label = "repository config include" if include else "repository config file"
            base = path.parent if include else worktree
            # Decide a lexically outside path before asking about its metadata.
            # Includes and blame retain their fail-closed admission refusal.
            candidate = Path(os.path.normpath(base / entry))
            if not _under(candidate, roots.roots):
                raise GitRefused(OUTSIDE_ROOT, 'config_file_outside')
            target = _metadata_entry(entry, base, label, roots)
            if include:
                _includes(target, roots, admission, depth + 1, worktree=worktree, file_policy=file_policy)
            else:
                info = _metadata_info(target, label, roots)
                if info is not None:
                    _metadata_bytes(target, label, roots)
    except (ValueError, OSError, MemoryError, RecursionError):
        raise GitRefused(PATH_INVALID, 'config_includes_3') from None
    finally:
        admission.active_configs.remove(path)


def _alternates(objects: Path, roots: _roots.Roots, depth: int,
                checked: set[tuple[int, int]] | None = None, *, admission: _Admission | None = None) -> None:
    """:class:`GitRefused` unless every entry of ``objects/info/alternates`` (and of each alternate's own list)
    resolves inside a root. Comment lines are skipped, as git skips them; a quoted entry, or one with white space
    around it, is refused rather than read differently from git."""
    if checked is None:
        checked = set()
    if admission is None:
        admission = _Admission()
    admission.check()
    folder = _metadata_info(objects, "objects/info/alternates", roots)
    if folder is None:
        if depth == 0:
            raise GitRefused(PATH_INVALID, 'config_alternates_1')
        if objects not in admission.missing_alternates:
            admission.alternate_limit(checked)
            admission.missing_alternates.add(objects)
        return  # Git skips missing alternate folders; both lines and targets still consume the budget.
    if (not stat.S_ISDIR(folder.st_mode) or not getattr(folder, "st_ino", 0)
            or getattr(folder, "st_dev", None) is None):
        raise GitRefused(PATH_INVALID, 'config_alternates_2')
    identity = (folder.st_dev, folder.st_ino)
    if identity in checked:
        return
    admission.alternate_limit(checked)
    checked.add(identity)
    listed = objects / "info" / "alternates"
    info = _metadata_info(listed, "objects/info/alternates", roots)
    if info is None:
        return
    if depth >= ALTERNATES_DEPTH or not stat.S_ISREG(info.st_mode):
        raise GitRefused(PATH_INVALID, 'config_alternates_3')
    seen_lines = set()
    text = admission.account(_metadata_text(listed, "objects/info/alternates", roots))
    for line in text.removeprefix("\ufeff").split("\n"):
        if line in seen_lines:
            continue
        seen_lines.add(line)
        admission.check()
        if not line or line.startswith("#"):
            continue
        borrowed = _metadata_entry(line, objects, "objects/info/alternates", roots)
        if not _under(borrowed, roots.roots):
            raise GitRefused(OUTSIDE_ROOT, 'config_alternates_4')
        _alternates(borrowed, roots, depth + 1, checked, admission=admission)


def destination(raw: str, config: GitConfig) -> Path:
    """Where ``clone`` may write: an absolute path under a declared root that does not exist yet (or is an empty
    folder), whose parent folder exists."""
    if not config.roots:
        raise GitRefused(NO_ROOT, 'clone_no_roots')
    if not raw.strip() or raw.startswith("~") or not Path(raw).is_absolute():
        raise GitRefused(PATH_INVALID, 'config_destination_1')
    real = _confined(raw, config.roots)
    if not _under(real, config.roots):
        raise GitRefused(OUTSIDE_ROOT, 'destination_outside')
    if real in config.roots:
        raise GitRefused(PATH_INVALID, 'config_destination_2')
    if real.exists() and (not real.is_dir() or any(real.iterdir())):
        raise GitRefused(PATH_INVALID, 'destination_occupied')
    if not real.parent.is_dir():
        raise GitRefused(PATH_INVALID, 'destination_parent')
    return real


def inside(repo: Path, raw: str) -> str:
    """``raw`` (a path in the repository) as git's path relative to ``repo``; :class:`GitRefused` when it resolves
    outside the repository, into its ``.git`` folder, or reads as an option."""
    if not raw.strip() or raw.startswith("-") or any(ch in raw for ch in "\x00\r\n"):
        raise GitRefused(PATH_INVALID, 'config_inside_1')
    given = Path(raw)
    real = _confined(given if given.is_absolute() else repo / given, (repo,))
    if real != repo and repo not in real.parents:
        raise GitRefused(OUTSIDE_ROOT, 'config_inside_2')
    dotgit = repo / ".git"
    if real == dotgit or dotgit in real.parents:
        raise GitRefused(PATH_INVALID, 'config_inside_3')
    return real.relative_to(repo).as_posix() or "."


def remote_host(url: str) -> tuple[str, str]:
    """``(scheme, host)`` of a remote address: ``https`` or ``ssh``; :class:`GitRefused` for any other form."""
    text = url.strip()
    if any(ch.isspace() or ord(ch) < 32 for ch in text) or text.startswith("-") or "::" in text:
        raise GitRefused(REMOTE_REFUSED, 'config_remote_host_1')
    scp = _SCP.fullmatch(text)
    if scp and len(scp.group("host")) == 1:  # C:/work/repo is a path on this machine, not host C
        raise GitRefused(REMOTE_REFUSED, 'config_remote_host_2')
    if scp:
        return "ssh", scp.group("host").lower()
    parts = urlsplit(text)
    if parts.scheme not in ("https", "ssh") or not parts.hostname:
        raise GitRefused(REMOTE_REFUSED, 'remote_scheme')
    if parts.password:
        raise GitRefused(REMOTE_REFUSED, 'config_remote_host_3')
    return parts.scheme, parts.hostname.lower()


def remote_problem(url: str, config: GitConfig, *, resolver: http.Resolver | None = None) -> str:
    """Why ``url`` may not be reached (in words), or ``""`` when it may."""
    try:
        _, host = remote_host(url)
    except GitRefused as exc:
        return exc.reason
    if host not in config.remote_hosts:
        return _refusals.render("remote_host_policy")
    try:
        http.check_url(f"https://{host}/", resolver=resolver)
    except http.HTTPError:
        return _refusals.render("remote_address_policy")
    return ""


def shown(url: str) -> str:
    """``url`` as a reply may show it: a user name or password in an https address hidden."""
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and "@" in parts.netloc:
        return parts._replace(netloc="***@" + parts.netloc.rpartition("@")[2]).geturl()
    return url


__all__ = ["ALTERNATES_DEPTH", "ALTERNATES_FOLDERS", "CONFIG_ENV", "CONFIG_FILE", "CONFIG_INVALID", "NOT_A_REPOSITORY", "NO_ROOT",
           "OUTSIDE_ROOT", "PATH_INVALID", "REMOTE_REFUSED", "GitConfig", "GitRefused", "destination", "inside", "load", "parse",
           "remote_host", "remote_problem", "repository", "shown"]
