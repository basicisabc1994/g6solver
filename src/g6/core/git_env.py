# SPDX-License-Identifier: Apache-2.0
"""Git in a working copy someone else may have controlled (an agent's copy, or a repository whose files an agent
changed), without letting that copy make git start a program.

Such a copy can carry a planted ``.git/config``, a nested repository's own config, or a ``.gitattributes`` that names a
program git would run on this machine, as you: ``core.fsmonitor``, a hook, a filter, diff or merge driver, a pager, a
signing program, or a remote git fetches a missing object from (``ext::<program>``, or ``uploadpack``). Every git
command in such a copy runs in the environment ``safe_git`` gives:

* only ``_KEPT`` of this process's environment (none of git's own redirections such as ``GIT_DIR``), temp in a scratch
  folder, no prompt;
* in the command scope (``GIT_CONFIG_COUNT``, as ``-c`` does, which outranks the system, global and repository config):
  an empty hooks folder, every key in ``_NO_PROGRAMS`` set, and each driver key the repository's config defines
  emptied (``DRIVER_QUERY``, read first, and ``driver_settings``), with a sentinel read back so that a git which
  ignores these settings (before 2.31) is refused (``UnsafeGitConfig``);
* no transport (``GIT_ALLOW_PROTOCOL``, which outranks every ``protocol.*`` key) and no lazy fetch of a missing object
  (``GIT_NO_LAZY_FETCH``, git 2.44 and later; an earlier git still finds no transport it may use);
* a copy whose index or HEAD holds a gitlink (a submodule, or a nested repository added as one) is refused
  (``refuse_gitlinks``, ``SUBMODULE_REFUSED``): git starts itself inside one, under that repository's own config, which
  none of the settings above reaches. ``safe_git_args`` adds ``--ignore-submodules=all`` to every ``status`` and
  ``diff`` as well.

``safe_git(workdir)`` asks git those questions itself; a caller with its own process control (``g6.workforce.
repo_tests``) runs ``DRIVER_QUERY`` and ``GITLINK_QUERIES`` its own way and uses ``safe_git_env``, ``driver_settings``
and ``refuse_gitlinks``.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from g6.core.outside import find_program

GIT_TIMEOUT_S = 60
SUBMODULE_REFUSED = "G6_E_GIT_SUBMODULE_REFUSED"

#: Config keys that make git start a program (or start itself in a submodule), set in the command scope. Transports
#: are also refused by ``_NO_TRANSPORT``; a caller's patch diff passes ``--no-ext-diff`` and ``--no-textconv``. Signing
#: (commits, tags, pushes) would start the signing program the config names, and so would checking a signature while
#: reading history (``log.showSignature``, ``merge.verifySignatures``), with the verifier git picks from the signature's
#: own type: each program is emptied, which git cannot start. An editor is a program too (``:`` is git's own "no
#: editor"; ``_KEPT`` drops ``GIT_EDITOR``, so without it a planted ``core.editor`` would run).
_NO_PROGRAMS = (("core.fsmonitor", "false"), ("core.askPass", ""), ("credential.helper", ""), ("core.pager", ""),
                ("protocol.allow", "never"), ("commit.gpgSign", "false"), ("tag.gpgSign", "false"),
                ("tag.forceSignAnnotated", "false"), ("push.gpgSign", "false"), ("log.showSignature", "false"),
                ("merge.verifySignatures", "false"), ("gpg.program", ""), ("gpg.x509.program", ""),
                ("gpg.ssh.program", ""), ("core.editor", ":"), ("sequence.editor", ":"), ("submodule.recurse", "false"),
                ("diff.ignoreSubmodules", "all"))
#: Set with the others and read back first: a git that ignores the environment's settings (before 2.31) is refused.
_SENTINEL = "g6.k1guard"
#: The query ``driver_settings`` reads: the sentinel, and each driver key (a filter, diff or merge driver that
#: .gitattributes names and the config defines) that names a program.
DRIVER_QUERY = ("config", "--null", "--name-only", "--get-regexp",
                rf"^({re.escape(_SENTINEL)}|(filter|diff|merge)\..+\.(clean|smudge|process|textconv|command|driver))$")
#: ``GIT_ALLOW_PROTOCOL`` names the only transports git may use, and it outranks every ``protocol.*`` key (a planted
#: ``protocol.ext.allow=always`` included). This value names none: ``_`` cannot appear in a URL scheme or in a
#: ``<transport>::`` name, so only a planted ``remote.<name>.vcs`` together with a program ``git-remote-g6_none`` on
#: PATH could answer to it.
_NO_TRANSPORT = "g6_none"
#: The listings ``refuse_gitlinks`` reads, ``<mode> ...<TAB><path>`` records: the index, and HEAD (an unborn HEAD,
#: where ``ls-tree`` fails, holds nothing).
GITLINK_QUERIES = (("ls-files", "--stage", "-z"), ("ls-tree", "-r", "-z", "HEAD"))

#: What git keeps from this process's environment: the program path, the system folders, where the person's own
#: (system and global) config lives, which says how their repositories read (line endings, ignored files), and who a
#: commit is by. Nothing of git's own (GIT_DIR, GIT_WORK_TREE, GIT_CONFIG_PARAMETERS...) and nothing else.
_KEPT = ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "XDG_CONFIG_HOME", "LANG",
         "LC_ALL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL")


class UnsafeGitConfig(ValueError):
    """G6 cannot switch off the programs a repository's git config may name, so git is not run there."""


def safe_git_env(scratch: Path, drivers: tuple[tuple[str, str], ...] = (), *,
                 ceiling: Path | None = None) -> dict[str, str]:
    """A clean environment for git in a working copy an agent may have controlled: ``_KEPT`` only, temp in
    ``scratch``, no prompt, no transport and no lazy fetch, an empty hooks folder, every key in ``_NO_PROGRAMS`` and
    ``drivers`` (from ``driver_settings``) set in the command scope, which outranks the system, global and repository
    config; git searches no folder at or above ``ceiling`` for a repository."""
    (scratch / "hooks").mkdir(exist_ok=True)
    env = {key: os.environ[key] for key in _KEPT if key in os.environ}
    env.update({"TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch), "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": _NO_TRANSPORT, "GIT_NO_LAZY_FETCH": "1"})
    if ceiling is not None:
        env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    settings = ((_SENTINEL, "on"), ("core.hooksPath", str(scratch / "hooks")), *_NO_PROGRAMS, *drivers)
    env["GIT_CONFIG_COUNT"] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{index}"], env[f"GIT_CONFIG_VALUE_{index}"] = key, value
    return env


def driver_settings(code: int | None, found: bytes) -> tuple[tuple[str, str], ...]:
    """From ``DRIVER_QUERY``'s exit code and output: every driver key the repository's config names, emptied (git runs
    no empty command), and each such filter made optional. ``UnsafeGitConfig`` when the sentinel is missing (git did
    not take the environment's settings) or the config could not be read."""
    keys = [key for key in found.decode("utf-8", "replace").split("\0") if key]
    if code != 0 or _SENTINEL not in keys:
        raise UnsafeGitConfig("git did not confirm it took G6's settings from the environment (git 2.31 or later is "
                              "needed), or could not read the repository's config, so G6 cannot switch off the "
                              "programs that config may name")
    settings: list[tuple[str, str]] = []
    for key in keys:
        if key != _SENTINEL:
            settings.append((key, ""))
            if key.lower().startswith("filter."):
                settings.append((f"{key.rsplit('.', 1)[0]}.required", "false"))
    return tuple(settings)


def refuse_gitlinks(*listings: bytes) -> None:
    """``UnsafeGitConfig`` when a ``GITLINK_QUERIES`` listing holds a gitlink (mode 160000), naming the first few."""
    found = sorted({record.partition(b"\t")[2].decode("utf-8", "replace") for listing in listings
                    for record in listing.split(b"\0") if record.startswith(b"160000 ")})
    if found:
        raise UnsafeGitConfig(f"the repository holds a submodule, or a nested repository added as one "
                              f"({', '.join(found[:3])}): git would run inside it under that repository's own config, "
                              f"which G6 cannot switch off, so git is not run there")


def safe_git_args(*command: str) -> tuple[str, ...]:
    """``command`` (a git subcommand and its arguments) with ``--ignore-submodules=all`` after ``status`` or ``diff``,
    so that neither starts git inside a submodule."""
    if command and command[0] in ("status", "diff"):
        return (command[0], "--ignore-submodules=all", *command[1:])
    return command


def git_program() -> str:
    """Where git is, as :func:`g6.core.outside.find_program` finds it (on PATH, never the current folder);
    ``FileNotFoundError`` when it is not there, as starting a missing program raises."""
    found = find_program("git")
    if found is None:
        raise FileNotFoundError("git is not on PATH")
    return found


@contextmanager
def safe_git(workdir: Path, *, ceiling: Path | None = None, timeout: float = GIT_TIMEOUT_S) -> Iterator[dict[str, str]]:
    """The environment for git commands in ``workdir``: ``safe_git_env`` with the drivers its config names switched
    off, found with one bounded ``DRIVER_QUERY``, after the ``GITLINK_QUERIES`` found no gitlink. ``UnsafeGitConfig``
    as ``driver_settings`` and ``refuse_gitlinks`` say."""
    with tempfile.TemporaryDirectory(prefix="g6-git-") as scratch:
        def ask(args: tuple[str, ...], env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run([git_program(), "-C", str(workdir), *args], capture_output=True, timeout=timeout, env=env,
                                  check=False)

        found = ask(DRIVER_QUERY, safe_git_env(Path(scratch), ceiling=ceiling))
        env = safe_git_env(Path(scratch), driver_settings(found.returncode, found.stdout), ceiling=ceiling)
        listed = [ask(query, env) for query in GITLINK_QUERIES]
        refuse_gitlinks(*(done.stdout for done in listed if done.returncode == 0))
        yield env


__all__ = ["DRIVER_QUERY", "GITLINK_QUERIES", "GIT_TIMEOUT_S", "SUBMODULE_REFUSED", "UnsafeGitConfig",
           "driver_settings", "git_program", "refuse_gitlinks", "safe_git", "safe_git_args", "safe_git_env"]
