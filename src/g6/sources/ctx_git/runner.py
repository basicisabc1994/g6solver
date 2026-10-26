# SPDX-License-Identifier: Apache-2.0
"""Running git for ctx_git, and the audit record of every call.

:func:`session` opens one working copy (already confined by :func:`~g6.sources.ctx_git.config.repository`) and yields
``run(*args)``: each git command runs in :func:`g6.core.git_env.safe_git`'s environment (no hooks, no fsmonitor, no
pager, no credential helper, no filter, diff or merge driver the repository's config names, no transport, no prompt; a
submodule refused) with the git folder and the working tree pinned (``GIT_DIR``, ``GIT_WORK_TREE``), so a planted
``core.worktree`` cannot point git elsewhere, with signing switched off for tags and pushes too, and with no signature
verifier of any type (``log`` and ``show`` also pass ``--no-show-signature``). It runs as a
bounded child (:mod:`g6.core.component_process`): a time limit, the whole process tree ended, the reply capped; each
call keeps at most :data:`MAX_OUTPUT` bytes of git's output, and more is refused (``G6_E_GIT_OUTPUT_TOO_LARGE``) unless
the op takes a cut (a patch, which says it was cut). A
network session (``network=True``) lets git use https and ssh only, with the ssh program found on PATH (never the
repository's ``core.sshCommand``), TLS certificates checked, no HTTP redirect followed and no
``core.alternateRefsCommand`` run. :func:`clone_session` is the same for ``clone``, which has no working copy yet.

A failed command raises :class:`~g6.sources.ctx_git.config.GitRefused` with a code read from git's message
(:func:`classify`). The audit log is ``ctx_git/audit.jsonl`` under the G6 home: two JSON lines per call, its intent
before it runs and its outcome after, sharing the call's id, with the op, the repository, what it was asked, the
outcome's code, HEAD before and after, and the commits the op made, so ``changes_since`` can tell a commit made
through ctx_git from one made any other way. :func:`records` joins each call's lines; a call whose outcome is missing
keeps its intent alone, so nothing it changed counts as made through ctx_git.
"""
from __future__ import annotations

import json
import os
import shlex
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

from g6.core import component_process
from g6.core.git_env import SUBMODULE_REFUSED, UnsafeGitConfig, git_program, safe_git, safe_git_args, safe_git_env
from g6.core.outside import OUTSIDE_PROGRAM_MISSING, OutsideProgram, find_program
from g6.core.storage import state_path
from g6.sources.ctx_git.config import GitRefused

GIT = OutsideProgram(name="git", purpose="ctx_git reads and changes repositories with the git program",
                     install="Install git 2.31 or later from git-scm.com and put it on PATH.", licence="GPL-2.0",
                     command="git")
DEFAULT_TIMEOUT = 60.0
NETWORK_TIMEOUT = 300.0
#: The transports a network session may use. GIT_ALLOW_PROTOCOL outranks every protocol.* key a repository sets.
PROTOCOLS = "https:ssh"
#: Set in the command scope beside g6.core.git_env's own settings: no signing program for a tag or a push, and no
#: signature verifier of any type (git picks the verifier from a signature's type: OpenPGP, X.509 or ssh), so a
#: planted ``log.showSignature`` or ``merge.verifySignatures`` starts nothing.
_MORE = (("tag.gpgSign", "false"), ("push.gpgSign", "false"), ("gpg.program", ""), ("log.showSignature", "false"),
         ("merge.verifySignatures", "false"), ("gpg.x509.program", ""), ("gpg.ssh.program", ""))
UNSAFE = "G6_E_GIT_UNSAFE_CONFIG"
TIMEOUT = "G6_E_GIT_TIMEOUT"
FAILED = "G6_E_GIT_FAILED"
TOO_LARGE = "G6_E_GIT_OUTPUT_TOO_LARGE"
#: The most bytes of git's output one call keeps (its error stream: :data:`MAX_ERROR`).
MAX_OUTPUT = 8 * 1024 * 1024
MAX_ERROR = 64 * 1024
_AUDIT = ("ctx_git", "audit.jsonl")
_LOCK = threading.Lock()
#: git's own words for each failure, first match wins.
_CLASSES = (
    ("G6_E_GIT_NOT_A_REPOSITORY", ("not a git repository",)),
    ("G6_E_GIT_AUTH_FAILED", ("authentication failed", "could not read username", "could not read password",
                              "permission denied (publickey", "terminal prompts disabled", "host key verification")),
    ("G6_E_GIT_UNREACHABLE", ("could not resolve host", "unable to access", "connection timed out", "failed to connect",
                              "network is unreachable", "connection refused", "could not read from remote")),
    ("G6_E_GIT_REJECTED", ("non-fast-forward", "[rejected]", "fetch first", "pre-receive hook declined",
                           "protected branch", "not possible to fast-forward", "[remote rejected]")),
    ("G6_E_GIT_CONFLICT", ("conflict",)),
    ("G6_E_GIT_NOTHING_TO_COMMIT", ("nothing to commit", "no changes added to commit", "nothing added to commit")),
    ("G6_E_GIT_NOTHING_TO_STASH", ("no local changes to save", "no stash entries")),
    ("G6_E_GIT_IDENTITY", ("please tell me who you are", "author identity unknown", "empty ident")),
    ("G6_E_GIT_BAD_REVISION", ("unknown revision", "bad revision", "invalid object name", "not a valid object name",
                               "did not match any", "invalid reference", "not a commit", "no such ref",
                               "couldn't find remote ref", "no such remote")),
    ("G6_E_GIT_EXISTS", ("already exists",)),
    ("G6_E_GIT_TRANSPORT_REFUSED", ("transport", "not allowed")),
)


@dataclass(frozen=True)
class Done:
    """One git command's exit code and output (``cut`` when the output was longer than :data:`MAX_OUTPUT`)."""

    code: int
    out: bytes
    err: bytes
    cut: bool = False

    @property
    def text(self) -> str:
        return self.out.decode("utf-8", "replace")

    @property
    def said(self) -> str:
        """git's message (its error stream, else its output), one line, at most 500 characters."""
        text = (self.err or self.out).decode("utf-8", "replace")
        return " ".join(text.split())[:500]


def classify(message: str) -> str:
    """The ``G6_E_GIT_*`` code for git's message."""
    lower = message.lower()
    for code, words in _CLASSES:
        if code == "G6_E_GIT_TRANSPORT_REFUSED":
            if all(w in lower for w in words):
                return code
        elif any(w in lower for w in words):
            return code
    return FAILED


def _with(env: dict[str, str], pairs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """``env`` with ``pairs`` added to the command-scope settings (``GIT_CONFIG_COUNT``)."""
    out = dict(env)
    count = int(out.get("GIT_CONFIG_COUNT", "0"))
    for key, value in pairs:
        out[f"GIT_CONFIG_KEY_{count}"], out[f"GIT_CONFIG_VALUE_{count}"] = key, value
        count += 1
    out["GIT_CONFIG_COUNT"] = str(count)
    return out


def ssh_program() -> str | None:
    """The ssh program on PATH (never in the current folder), or ``None``."""
    return find_program("ssh")


def _network(env: dict[str, str]) -> dict[str, str]:
    out = _with(env, (("http.sslVerify", "true"), ("http.followRedirects", "false"), ("core.alternateRefsCommand", "")))
    out["GIT_ALLOW_PROTOCOL"] = PROTOCOLS
    ssh = ssh_program()
    if ssh:  # GIT_SSH_COMMAND outranks core.sshCommand, so a repository's own ssh command is never run
        out["GIT_SSH_COMMAND"] = shlex.quote(ssh.replace("\\", "/"))
        out["GIT_SSH_VARIANT"] = "ssh"
    if os.environ.get("SSH_AUTH_SOCK"):  # where the person's ssh agent listens; it holds no key itself
        out["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    return out


def missing_git() -> GitRefused:
    return GitRefused(OUTSIDE_PROGRAM_MISSING, f"git ({GIT.purpose}) is not on PATH. G6 never installs it; install it "
                                               f"yourself: {GIT.install}")


def _runner(where: Path, env: dict[str, str], timeout: float) -> Callable[..., Done]:
    def run(*args: str, ok: tuple[int, ...] = (0,), input_bytes: bytes = b"", cut_ok: bool = False) -> Done:
        argv = [git_program(), "-C", str(where), *safe_git_args(*args)]
        try:
            with component_process.window(timeout):
                done = component_process.run_contained(argv, env=env, input_bytes=input_bytes)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped:
                raise GitRefused(FAILED, "G6's stop is engaged, so git was ended") from None
            raise GitRefused(TIMEOUT, f"git {args[0]} did not finish within {timeout:g} seconds and was ended") from None
        except ConnectionResetError:   # the contained process wrote more than its reply protocol carries
            raise GitRefused(TOO_LARGE, f"git {args[0]} wrote more than G6 reads from one command: narrow the request "
                                        "(paths, rev, max_count)") from None
        except OSError as exc:
            raise GitRefused(FAILED, f"git {args[0]} could not be run to the end: {type(exc).__name__}") from None
        cut = len(done.stdout) > MAX_OUTPUT
        if cut and not cut_ok:
            raise GitRefused(TOO_LARGE, f"git {args[0]} wrote {len(done.stdout)} bytes, more than the {MAX_OUTPUT} one "
                                        "call keeps: narrow the request (paths, rev, max_count)")
        found = Done(done.returncode, done.stdout[:MAX_OUTPUT], done.stderr[:MAX_ERROR], cut)
        if found.code not in ok:
            raise GitRefused(classify(found.said), f"git {args[0]} failed: {found.said or f'exit {found.code}'}")
        return found
    return run


@contextmanager
def session(repo: Path, gitdir: Path, *, network: bool = False,
            timeout: float = DEFAULT_TIMEOUT) -> Iterator[Callable[..., Done]]:
    """``run(*args)`` for git commands in ``repo``; see the module docstring."""
    if find_program("git") is None:
        raise missing_git()
    try:
        with safe_git(repo, ceiling=repo.parent, timeout=timeout) as base:
            env = _with({**base, "GIT_DIR": str(gitdir), "GIT_WORK_TREE": str(repo)}, _MORE)
            yield _runner(repo, _network(env) if network else env, NETWORK_TIMEOUT if network else timeout)
    except UnsafeGitConfig as exc:
        raise GitRefused(SUBMODULE_REFUSED if "submodule" in str(exc) else UNSAFE, str(exc)) from None
    except TimeoutExpired:
        raise GitRefused(TIMEOUT, f"git did not answer within {timeout:g} seconds while G6 read the repository's "
                                  "config") from None


@contextmanager
def clone_session(parent: Path) -> Iterator[Callable[..., Done]]:
    """``run(*args)`` for ``git clone`` into a folder of ``parent``: a network session with no repository config to
    read yet (a clone copies none from the remote) and no search above ``parent``."""
    if find_program("git") is None:
        raise missing_git()
    with tempfile.TemporaryDirectory(prefix="g6-git-clone-") as scratch:
        env = _network(_with(safe_git_env(Path(scratch), ceiling=parent), _MORE))
        yield _runner(parent, env, NETWORK_TIMEOUT)


def audit_path() -> Path:
    """The audit log: ``ctx_git/audit.jsonl`` under the G6 home."""
    return state_path(*_AUDIT)


def record(entry: dict[str, Any]) -> None:
    """Append one call to the audit log (created on first use)."""
    path = audit_path()
    line = json.dumps({"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), **entry}, sort_keys=True)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def records(repo: Path) -> list[dict[str, Any]]:
    """Every call the audit log holds for ``repo``, oldest first, each call's intent and outcome joined into one entry
    (a line that does not parse is skipped)."""
    path = audit_path()
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    calls: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("repo") != str(repo):
            continue
        call = entry.get("call")
        if isinstance(call, str) and call in calls:
            calls[call].update(entry)   # the outcome after its intent
            continue
        if isinstance(call, str):
            calls[call] = entry
        out.append(entry)
    return out


__all__ = ["DEFAULT_TIMEOUT", "FAILED", "GIT", "MAX_ERROR", "MAX_OUTPUT", "NETWORK_TIMEOUT", "PROTOCOLS", "TIMEOUT",
           "TOO_LARGE", "UNSAFE", "Done", "audit_path", "classify", "clone_session", "missing_git", "record", "records",
           "session", "ssh_program"]
