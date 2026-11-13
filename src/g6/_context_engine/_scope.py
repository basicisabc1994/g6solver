# SPDX-License-Identifier: Apache-2.0
"""Host-bound scope for the Context Engine (design 12.6): who is asking, for which project, under which roots.

``_``-prefixed, so it never registers as a component. A :class:`Binding` is made by :func:`host_binding` from host
facts only: the folder the server was started in (the project) and the folders the person listed in
``G6_KNOWLEDGE_ROOTS`` in the client's server configuration (the permitted roots). No tool argument can make or widen
one. An :class:`Access` is one operation under a binding, for one disclosure destination, optionally narrowed to some
source registrations; a caller can narrow, never widen. Absent or unknown bindings deny.

The permitted roots are only the ones the person names: with :data:`START_IS_A_ROOT` false (the stricter of the two
options the design leaves to the owner) the start folder names the project and is not itself a root.

This is a host and API boundary. Code running as the same OS user outside G6 is not confined by it.
"""
from __future__ import annotations

import getpass
import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from g6.core.storage import PathRefused, contained

SCOPE_DENIED = "G6_E_KNOWLEDGE_SCOPE"
#: The person's list of permitted roots: absolute folders, separated as the system separates PATH entries.
ROOTS_ENV = "G6_KNOWLEDGE_ROOTS"
#: Whether the folder the server started in is a permitted root without being listed. False: only listed roots.
START_IS_A_ROOT = False
Operation = Literal["register", "list", "search", "fetch", "count", "traverse", "context",
                    "refresh", "revoke", "delete", "draft", "plan"]
LOCAL = "local"
UNKNOWN_DESTINATION = "cloud:unknown"
_DESTINATION = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_PROJECT = re.compile(r"[0-9a-f]{64}\Z")
_REGISTRATION = re.compile(r"src-[0-9a-f]{16}\Z")


#: The one refusal for every path that is not a folder G6 may index: it says the same whatever is, or is not, at the
#: path, so a refusal never tells a caller which names exist under a permitted root.
NOT_INDEXABLE = ("the path is not a folder G6 may index here: it is outside every permitted root, or a link leads "
                 "out of one, or it is not a folder inside a permitted root, or it is, or lies under, a folder "
                 f"nothing is read under (a credential folder or a generated one). {ROOTS_ENV} lists the permitted "
                 "roots")


class ScopeDenied(Exception):
    """An operation outside the host binding, with its stable code and a reason that names no source content."""

    def __init__(self, reason: str, code: str = SCOPE_DENIED) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason = code, reason


@dataclass(frozen=True)
class Binding:
    """Made only by :func:`host_binding`; never from a tool argument."""

    principal: str            # sha256 of the OS account name: an attribution of the session, not authentication
    project: str              # the 64-hex part of cloud_consent.repository_source(start folder)
    roots: tuple[Path, ...]
    start: Path | None = None  # the start folder, for labels only


@dataclass(frozen=True)
class Access:
    binding: Binding
    operation: Operation
    destination: str                        # "local", a provider id such as "anthropic:claude_code", or "cloud:unknown"
    sources: frozenset[str] | None = None   # None: every registration of the project; a caller may only narrow

    @property
    def local(self) -> bool:
        return self.destination == LOCAL


def _is_drive_root(path: Path) -> bool:
    return path == Path(path.anchor)


def _plain_folder(raw: str) -> Path | None:
    """``raw`` as a real, existing folder that is neither the home folder nor a filesystem root and has no link in its
    path; ``None`` otherwise."""
    if not raw or not Path(raw).is_absolute():
        return None
    try:
        real = contained(Path(raw).anchor, raw, refuse_links=True)
        home = Path.home().resolve()
    except (PathRefused, OSError, RuntimeError):
        return None
    if not real.is_dir() or _is_drive_root(real) or real == home:
        return None
    return real


def host_binding(*, environ: Mapping[str, str] | None = None, start: Path | None = None) -> Binding:
    """The binding of this server process; :class:`ScopeDenied` when the start folder cannot be resolved, or is the
    home folder or a filesystem root (a project that wide is no scope)."""
    environ = os.environ if environ is None else environ
    try:
        raw = str(Path.cwd() if start is None else start)
    except OSError:
        raise ScopeDenied("the folder the server started in cannot be resolved, so no project is bound") from None
    folder = _plain_folder(raw)
    if folder is None:
        raise ScopeDenied("the folder the server started in is missing, is a link, or is the home folder or a "
                          "filesystem root; start the server in the project's folder")
    from g6.core.cloud_consent import repository_source

    try:
        project = repository_source(folder).partition(":")[2]
        account = getpass.getuser()
    except (ValueError, OSError, KeyError):
        raise ScopeDenied("the project or the OS account cannot be resolved, so nothing is bound") from None
    roots = []
    for item in (environ.get(ROOTS_ENV) or "").split(os.pathsep):
        root = _plain_folder(item.strip())
        if root is not None and root not in roots:
            roots.append(root)
    if START_IS_A_ROOT and folder not in roots:
        roots.append(folder)
    return Binding(principal=hashlib.sha256(account.encode("utf-8")).hexdigest(), project=project,
                   roots=tuple(roots), start=folder)


def bound(binding: object) -> Binding:
    """``binding`` when it is one a host could have made; :class:`ScopeDenied` otherwise."""
    if not isinstance(binding, Binding) or not _PROJECT.fullmatch(binding.project or "") or not binding.principal:
        raise ScopeDenied("no host binding: the session is bound to no project")
    return binding


def access(binding: Binding, operation: Operation, destination: str,
           sources: Iterable[str] | None = None) -> Access:
    """One operation under ``binding`` for ``destination``; ``sources`` narrows it to those registrations (an unknown
    id simply matches nothing)."""
    bound(binding)
    if operation not in get_args(Operation):
        raise ScopeDenied("the operation is not one the Context Engine knows")
    if not isinstance(destination, str) or not _DESTINATION.fullmatch(destination):
        destination = UNKNOWN_DESTINATION
    narrowed = None
    if sources is not None:
        narrowed = frozenset(s for s in sources if isinstance(s, str) and _REGISTRATION.fullmatch(s))
    return Access(binding=binding, operation=operation, destination=destination, sources=narrowed)


def inside(binding: Binding, path: Path | str) -> Path:
    """Where ``path`` really is, when that is one of the binding's roots or inside one, with no link on the way;
    :class:`ScopeDenied` otherwise. A relative path is joined onto each root in turn."""
    bound(binding)
    if not binding.roots:
        raise ScopeDenied(f"no permitted root is listed: the person who runs G6 names the folders it may index in "
                          f"{ROOTS_ENV}, in the client's server configuration")
    for root in binding.roots:
        try:
            real = contained(root, path, refuse_links=True)
        except (PathRefused, OSError, ValueError):
            continue
        if Path(str(path)).is_absolute() or real.exists():
            return real
    raise ScopeDenied(NOT_INDEXABLE)


def consent_scope(registration_id: str) -> str:
    """The cloud-consent source id of one registration."""
    if not _REGISTRATION.fullmatch(registration_id or ""):
        raise ScopeDenied("not a source registration id")
    return "knowledge:" + registration_id


__all__ = ["LOCAL", "NOT_INDEXABLE", "ROOTS_ENV", "SCOPE_DENIED", "START_IS_A_ROOT", "UNKNOWN_DESTINATION", "Access", "Binding",
           "Operation", "ScopeDenied", "access", "bound", "consent_scope", "host_binding", "inside"]
