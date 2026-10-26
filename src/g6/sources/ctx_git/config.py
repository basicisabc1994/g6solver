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
``objects/info/alternates``, followed through each alternate's own list (git borrows objects from those). This is
checked before any git command runs. :func:`inside` does the same for every file an op names in the repository, and
refuses the ``.git`` folder itself.
:func:`remote_problem` says why a remote's address may not be reached (only https and ssh, only a declared host, no
password in the address, and the host must resolve to public addresses only); ``""`` when it may.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from g6.core import http
from g6.core.storage import confined_path, state_path

CONFIG_ENV = "G6_GIT_CONFIG"
CONFIG_FILE = ("ctx_git", "config.json")
NO_ROOT = "G6_E_GIT_NO_ROOT"
OUTSIDE_ROOT = "G6_E_GIT_OUTSIDE_ROOT"
NOT_A_REPOSITORY = "G6_E_GIT_NOT_A_REPOSITORY"
CONFIG_INVALID = "G6_E_GIT_CONFIG_INVALID"
REMOTE_REFUSED = "G6_E_GIT_REMOTE_REFUSED"
#: How deep git follows alternates of alternates; a list nested deeper is refused.
ALTERNATES_DEPTH = 5
#: The address forms a network op may reach: an https URL, an ssh URL, or ssh's ``user@host:path`` form.
_SCP = re.compile(r"^(?:(?P<user>[A-Za-z0-9._-]+)@)?(?P<host>[A-Za-z0-9.-]+):(?!//)(?P<path>[^\s]+)$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


class GitRefused(Exception):
    """An op ctx_git will not run, with its stable code and the reason in words."""

    def __init__(self, code: str, reason: str) -> None:
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


def parse(text: str, source: str) -> GitConfig:
    """The configuration in ``text`` (JSON); :class:`GitRefused` (``G6_E_GIT_CONFIG_INVALID``) when it cannot be used.
    The message never repeats a value from it."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GitRefused(CONFIG_INVALID, f"{source} is not JSON ({exc.msg} at line {exc.lineno})") from None
    if not isinstance(data, Mapping) or set(data) - {"roots", "remote_hosts"}:
        raise GitRefused(CONFIG_INVALID, f"{source} must be a JSON object with only roots and remote_hosts")
    roots, hosts = data.get("roots", []), data.get("remote_hosts", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) and r.strip() for r in roots):
        raise GitRefused(CONFIG_INVALID, f"{source}: roots must be a list of folder paths")
    if not isinstance(hosts, list) or not all(isinstance(h, str) and _HOST.fullmatch(h.lower()) for h in hosts):
        raise GitRefused(CONFIG_INVALID, f"{source}: remote_hosts must be a list of host names, such as github.com")
    resolved = []
    for raw in roots:
        path = Path(raw)
        if raw.startswith("~") or not path.is_absolute():
            raise GitRefused(CONFIG_INVALID, f"{source}: each root is an absolute path (~ is not expanded)")
        try:
            resolved.append(confined_path(raw, (path,)))
        except (ValueError, OSError) as exc:
            raise GitRefused(CONFIG_INVALID, f"{source}: {exc}") from None
    return GitConfig(tuple(resolved), tuple(h.lower() for h in hosts), source)


def load(environ: Mapping[str, str] | None = None) -> GitConfig:
    """The person's configuration: ``G6_GIT_CONFIG``, else the file under the G6 home, else none (no root)."""
    env = os.environ if environ is None else environ
    raw = env.get(CONFIG_ENV, "").strip()
    if raw:
        return parse(raw, CONFIG_ENV)
    file = state_path(*CONFIG_FILE)
    if file.is_file():
        return parse(file.read_text(encoding="utf-8"), "the ctx_git configuration file under the G6 home")
    return GitConfig((), (), "no configuration")


def _confined(raw: str | Path, roots: tuple[Path, ...], what: str = "") -> Path:
    try:
        return confined_path(raw, roots)
    except (ValueError, OSError) as exc:
        raise GitRefused(OUTSIDE_ROOT, f"{what}: {exc}" if what else str(exc)) from None


def _under(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _how_to_declare() -> str:
    return (f"the person who runs G6 declares the folders ctx_git may use in {CONFIG_ENV} or in "
            f"{state_path(*CONFIG_FILE)}, as {{\"roots\": [\"<absolute folder>\"]}}")


def repository(raw: str, config: GitConfig) -> tuple[Path, Path]:
    """The working copy ``raw`` names and its git folder, both resolved; :class:`GitRefused` unless both are at or
    under a declared root."""
    if not config.roots:
        raise GitRefused(NO_ROOT, f"no folder is declared for ctx_git, so git is not run anywhere: {_how_to_declare()}")
    if not raw.strip():
        raise GitRefused(NOT_A_REPOSITORY, "give repo: the absolute path of a repository under a declared root")
    if raw.startswith("~") or not Path(raw).is_absolute():
        raise GitRefused(NOT_A_REPOSITORY, "repo is an absolute path (a relative one, or ~, is never resolved against "
                                           "the folder G6 runs in)")
    real = _confined(raw, config.roots)
    if not _under(real, config.roots):
        raise GitRefused(OUTSIDE_ROOT, f"{real} is not at or under a declared root (a link out of one counts as "
                                       f"outside): {_how_to_declare()}")
    dotgit = real / ".git"
    if not real.is_dir() or not dotgit.exists():
        raise GitRefused(NOT_A_REPOSITORY, f"{real} is not the top folder of a git working copy (it has no .git)")
    gitdir = _confined(dotgit, config.roots)
    if dotgit.is_file():
        line = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        if not line.startswith("gitdir:"):
            raise GitRefused(NOT_A_REPOSITORY, f"{dotgit} is a file that names no git folder")
        named = Path(line.partition(":")[2].strip())
        gitdir = _confined(named if named.is_absolute() else real / named, config.roots)
    if not _under(gitdir, config.roots) or not gitdir.is_dir():
        raise GitRefused(OUTSIDE_ROOT, f"the git folder of {real} is outside every declared root, or missing (its .git "
                                       "links out of the root)")
    common = _common(gitdir, config)
    for name in ("objects", "refs"):
        if (common / name).exists() and not _under(_confined(common / name, config.roots), config.roots):
            raise GitRefused(OUTSIDE_ROOT, f"the {name} folder of {real}'s git folder links outside every declared "
                                           "root")
    _alternates(common / "objects", config, 0)
    return real, gitdir


def _common(gitdir: Path, config: GitConfig) -> Path:
    """The common git folder ``gitdir``'s ``commondir`` file names (``gitdir`` itself when there is none), resolved;
    :class:`GitRefused` when it is outside every root."""
    named = gitdir / "commondir"
    if not named.exists():
        return gitdir
    text = named.read_text(encoding="utf-8", errors="replace").rstrip("\r\n") if named.is_file() else ""
    if not text.strip():
        raise GitRefused(OUTSIDE_ROOT, f"{named} is not a commondir file naming a folder")
    common = _confined(Path(text) if Path(text).is_absolute() else gitdir / text, config.roots,
                       f"the commondir file of {gitdir} names a git folder outside every declared root")
    if not _under(common, config.roots) or not common.is_dir():
        raise GitRefused(OUTSIDE_ROOT, f"the commondir file of {gitdir} names a git folder outside every declared "
                                       "root, or none: git would read and write that folder's refs and objects")
    return common


def _alternates(objects: Path, config: GitConfig, depth: int) -> None:
    """:class:`GitRefused` unless every entry of ``objects/info/alternates`` (and of each alternate's own list)
    resolves inside a root. Comment lines are skipped, as git skips them; a quoted entry, or one with white space
    around it, is refused rather than read differently from git."""
    listed = objects / "info" / "alternates"
    if not listed.exists():
        return
    if depth >= ALTERNATES_DEPTH or not listed.is_file():
        raise GitRefused(OUTSIDE_ROOT, f"{listed} nests alternates deeper than git follows, or is not a file")
    for line in listed.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not line or line.startswith("#"):
            continue
        if line.startswith('"') or line != line.strip():
            raise GitRefused(OUTSIDE_ROOT, f"{listed} holds an alternates entry that is quoted or has white space "
                                           "around it, which G6 does not read")
        borrowed = _confined(Path(line) if Path(line).is_absolute() else objects / line, config.roots,
                             f"{listed} borrows objects from a folder outside every declared root "
                             "(objects/info/alternates)")
        if not _under(borrowed, config.roots):
            raise GitRefused(OUTSIDE_ROOT, f"{listed} borrows objects from a folder outside every declared root "
                                           "(objects/info/alternates)")
        _alternates(borrowed, config, depth + 1)


def destination(raw: str, config: GitConfig) -> Path:
    """Where ``clone`` may write: an absolute path under a declared root that does not exist yet (or is an empty
    folder), whose parent folder exists."""
    if not config.roots:
        raise GitRefused(NO_ROOT, f"no folder is declared for ctx_git, so nothing is cloned: {_how_to_declare()}")
    if not raw.strip() or raw.startswith("~") or not Path(raw).is_absolute():
        raise GitRefused(OUTSIDE_ROOT, "dest is the absolute path of a new folder under a declared root")
    real = _confined(raw, config.roots)
    if not _under(real, config.roots) or real in config.roots:
        raise GitRefused(OUTSIDE_ROOT, f"{real} is not inside a declared root: {_how_to_declare()}")
    if real.exists() and (not real.is_dir() or any(real.iterdir())):
        raise GitRefused(OUTSIDE_ROOT, f"{real} already exists and is not an empty folder")
    if not real.parent.is_dir():
        raise GitRefused(OUTSIDE_ROOT, f"the folder {real.parent} does not exist")
    return real


def inside(repo: Path, raw: str) -> str:
    """``raw`` (a path in the repository) as git's path relative to ``repo``; :class:`GitRefused` when it resolves
    outside the repository, into its ``.git`` folder, or reads as an option."""
    if not raw.strip() or raw.startswith("-") or any(ch in raw for ch in "\x00\r\n"):
        raise GitRefused(OUTSIDE_ROOT, f"{raw!r} is not a path in the repository")
    given = Path(raw)
    real = _confined(given if given.is_absolute() else repo / given, (repo,))
    if real != repo and repo not in real.parents:
        raise GitRefused(OUTSIDE_ROOT, f"{raw!r} is outside the repository {repo} (a link out of it counts as outside)")
    dotgit = repo / ".git"
    if real == dotgit or dotgit in real.parents:
        raise GitRefused(OUTSIDE_ROOT, f"{raw!r} is inside the repository's .git folder, which no op touches")
    return real.relative_to(repo).as_posix() or "."


def remote_host(url: str) -> tuple[str, str]:
    """``(scheme, host)`` of a remote address: ``https`` or ``ssh``; :class:`GitRefused` for any other form."""
    text = url.strip()
    if any(ch.isspace() or ord(ch) < 32 for ch in text) or text.startswith("-") or "::" in text:
        raise GitRefused(REMOTE_REFUSED, "the remote address is not an https or ssh address")
    scp = _SCP.fullmatch(text)
    if scp and len(scp.group("host")) == 1:  # C:/work/repo is a path on this machine, not host C
        raise GitRefused(REMOTE_REFUSED, "only https and ssh remotes are reached, not a local path")
    if scp:
        return "ssh", scp.group("host").lower()
    parts = urlsplit(text)
    if parts.scheme not in ("https", "ssh") or not parts.hostname:
        raise GitRefused(REMOTE_REFUSED, f"only https and ssh remotes are reached, not {parts.scheme or 'a local path'}")
    if parts.password:
        raise GitRefused(REMOTE_REFUSED, "the remote address carries a password; G6 does not send one")
    return parts.scheme, parts.hostname.lower()


def remote_problem(url: str, config: GitConfig, *, resolver: http.Resolver | None = None) -> str:
    """Why ``url`` may not be reached (in words), or ``""`` when it may."""
    try:
        _, host = remote_host(url)
    except GitRefused as exc:
        return exc.reason
    if host not in config.remote_hosts:
        return (f"{host} is not one of the remote hosts the person declared ({', '.join(config.remote_hosts) or 'none'}"
                f"); they add it to remote_hosts in {CONFIG_ENV} or {state_path(*CONFIG_FILE)}")
    try:
        http.check_url(f"https://{host}/", resolver=resolver)
    except http.HTTPError as exc:
        return f"{host} is refused: {str(exc).partition(': ')[2] or exc.code}"
    return ""


def shown(url: str) -> str:
    """``url`` as a reply may show it: a user name or password in an https address hidden."""
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and "@" in parts.netloc:
        return parts._replace(netloc="***@" + parts.netloc.rpartition("@")[2]).geturl()
    return url


__all__ = ["ALTERNATES_DEPTH", "CONFIG_ENV", "CONFIG_FILE", "CONFIG_INVALID", "NOT_A_REPOSITORY", "NO_ROOT",
           "OUTSIDE_ROOT", "REMOTE_REFUSED", "GitConfig", "GitRefused", "destination", "inside", "load", "parse",
           "remote_host", "remote_problem", "repository", "shown"]
