# SPDX-License-Identifier: Apache-2.0
"""Programs a person installs themselves: a component uses them, and G6 never installs, bundles or stands in for them.

A component declares each one in its package as ``__g6_outside__``, a tuple of :class:`OutsideProgram`: what it is
for, how to install it, its licence, whether it is paid or needs the internet, and how to find it. That is either a
``command`` (the name on PATH, or the program the environment variable ``env`` names) that must answer
``version_args`` (``--version``), or a ``url``, a service on this machine over plain HTTP at 127.0.0.1, ::1 or
localhost (read as 127.0.0.1), with no user name or password in it. ``g6_describe`` lists the declarations and runs
nothing.

``detect(program)`` looks, under a time limit; detecting a command runs it with its version arguments, on this
machine, as you, as a bounded child (:mod:`g6.core.component_process`) with a clean environment
(:func:`clean_environment`: no key, token or setting of G6's) and its answer capped. ``find_program(command)`` is the one
way the library finds a program: a path as given, or a name on PATH, never in the current folder, where Windows (and
``shutil.which`` there) looks first. ``require(program)`` returns what was found, or a failed
:class:`~g6.core.result.Result` starting
``[G6_E_OUTSIDE_PROGRAM_MISSING]`` (the form ``g6_invoke`` reads a code from) that says why and how to install it,
which a component returns as ``blocked-escalated``: never a fallback, a mock or a pretend answer.

A service's base URL is the declared ``url``, or the one its ``env`` variable names: the setting of the person who
runs G6, which may move it but never off this machine. ``call_service(program, method, path)`` talks to it (this is
the one way a component reaches a local service, since ``g6.core.http`` refuses loopback addresses): plain HTTP to
that base only, under a size cap and one time limit for the whole call (connecting, sending and every part of the
reply), raising :class:`ServiceError` with its code: nothing answering is the same ``G6_E_OUTSIDE_PROGRAM_MISSING``
refusal with the install steps; a service that answered but did not finish within the limit is
``G6_E_OUTSIDE_SERVICE_TIMEOUT`` and one whose reply broke off ``G6_E_OUTSIDE_SERVICE_FAILED``; a redirect or an
error status is ``G6_E_OUTSIDE_SERVICE_STATUS``, with the start of the reply's text (its reason), never followed or
read as an empty answer. ``ServiceError.as_failure()`` is the failed ``Result`` a component returns.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from g6.core import component_process
from g6.core.result import Result

OUTSIDE_PROGRAM_MISSING = "G6_E_OUTSIDE_PROGRAM_MISSING"
SERVICE_NOT_LOCAL = "G6_E_OUTSIDE_SERVICE_NOT_LOCAL"
SERVICE_STATUS = "G6_E_OUTSIDE_SERVICE_STATUS"
SERVICE_TOO_LARGE = "G6_E_OUTSIDE_SERVICE_TOO_LARGE"
SERVICE_TIMEOUT = "G6_E_OUTSIDE_SERVICE_TIMEOUT"
SERVICE_FAILED = "G6_E_OUTSIDE_SERVICE_FAILED"
DEFAULT_TIMEOUT = 10.0
DEFAULT_SERVICE_TIMEOUT = 60.0
DEFAULT_SERVICE_MAX_BYTES = 64 * 1024 * 1024
_LOOPBACK = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1", "::1": "::1"}
_SAID = 200
#: The most of an error reply's text a ``SERVICE_STATUS`` error carries.
_REASON = 300

Runner = Callable[[list[str], float], tuple[int, str]]
#: What a program G6 starts is given of G6's environment: enough to start (the search path, the system folders, the
#: shell Windows' ``.cmd`` shims need), the person's home folders, where its own settings live (Windows' OpenSSH reads
#: ``%PROGRAMDATA%\ssh``), and the locale. Nothing else: no key, token or setting of G6's or anyone else's.
BASE_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "HOME", "USERPROFILE", "HOMEDRIVE",
            "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "LANG", "LC_ALL", "LC_CTYPE", "TZ")


def clean_environment(keep: Iterable[str] = (), *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables of :data:`BASE_ENV` and ``keep`` that are set, and no others."""
    source = os.environ if environ is None else environ
    return {name: source[name] for name in (*BASE_ENV, *keep) if source.get(name)}


def find_program(command: str, *, environ: Mapping[str, str] | None = None) -> str | None:
    """Where ``command`` is: a path as given, or a name in one of PATH's folders, never in the current folder. Windows,
    and ``shutil.which`` there, look in the current folder first (unless the client set
    ``NoDefaultCurrentDirectoryInExePath``), so a program left in the folder G6 runs in, a cloned repository's say,
    would be started in place of the one the person installed."""
    if os.path.dirname(command):
        return shutil.which(command)
    for folder in (os.environ if environ is None else environ).get("PATH", "").split(os.pathsep):
        # a name joined to its folder is looked up in that folder only, with PATHEXT on Windows
        if folder.strip() and (found := shutil.which(os.path.join(folder, command))):
            return os.path.abspath(found)
    return None


def _shown(url: str) -> str:
    """``url`` as an error may show it: a user name or password in it hidden."""
    parts = urlsplit(url)
    return parts._replace(netloc="***@" + parts.netloc.rpartition("@")[2]).geturl() if "@" in parts.netloc else url


def _local(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in _LOOPBACK or "@" in parts.netloc:
        raise ValueError(f"an outside service must be plain http on this machine (127.0.0.1, ::1 or localhost), with "
                         f"no user name or password; got {_shown(url)!r}")
    return _LOOPBACK[parts.hostname], parts.port or 80, parts.path or "/"


@dataclass(frozen=True)
class OutsideProgram:
    """One program or local service a component needs and the person installs."""

    name: str
    purpose: str
    install: str
    licence: str
    command: str = ""
    url: str = ""
    env: str = ""
    version_args: tuple[str, ...] = ("--version",)
    paid: bool = False
    online: bool = False

    def __post_init__(self) -> None:
        for key in ("name", "purpose", "install", "licence"):
            if not getattr(self, key).strip():
                raise ValueError(f"an outside program needs a {key}")
        if bool(self.command) == bool(self.url):
            raise ValueError("an outside program names a command or a url, not both")
        if self.url:
            _local(self.url)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "purpose": self.purpose, "install": self.install, "licence": self.licence,
                "command": self.command, "url": self.url, "env": self.env, "paid": self.paid, "online": self.online}


@dataclass(frozen=True)
class Detected:
    """What :func:`detect` found: ``found`` only when the program answered."""

    name: str
    found: bool
    where: str = ""
    version: str = ""
    detail: str = ""


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    """The version probe: a bounded child with a clean environment, its whole tree ended, its answer capped (more than
    :data:`~g6.core.component_process.MAX_MESSAGE_BYTES` is an error, an ``OSError``)."""
    try:
        with component_process.window(timeout):
            done = component_process.run_contained(argv, env=clean_environment())
    except component_process.ExecutionInterrupted as exc:
        if exc.stopped:
            raise OSError("G6's stop is engaged") from exc
        raise subprocess.TimeoutExpired(argv, timeout) from exc
    out = done.stdout.decode("utf-8", "replace").strip()
    return done.returncode, out or done.stderr.decode("utf-8", "replace")


def _command(program: OutsideProgram, which: Callable[[str], str | None], run: Runner, environ: Mapping[str, str],
             timeout: float) -> Detected:
    named = environ.get(program.env, "").strip() if program.env else ""
    if named:
        where = which(named) or (named if Path(named).is_file() else None)
        if not where:
            return Detected(program.name, False, detail=f"{program.env} names {named}, which is not a program here")
    else:
        where = which(program.command)
        if not where:
            return Detected(program.name, False, detail=f"no {program.command} on PATH")
    argv = [where, *program.version_args]
    said = " ".join(argv)
    try:
        code, out = run(argv, timeout)
    except subprocess.TimeoutExpired:
        return Detected(program.name, False, where, detail=f"{said} did not answer within {timeout:g} seconds")
    except OSError as exc:
        return Detected(program.name, False, where, detail=f"{said} could not be run: {exc}")
    if code != 0:
        return Detected(program.name, False, where, detail=f"{said} exited {code}: {out.strip()[:_SAID]}")
    version = next((line.strip() for line in out.splitlines() if line.strip()), "")
    return Detected(program.name, True, where, version[:_SAID], detail=f"{said} answered")


class ServiceError(Exception):
    """A local service call that did not give an answer: its ``G6_E_*`` ``code``, the ``message``, and the ``status``
    when it replied. Its text is ``[code] message``, the form ``g6_invoke`` reads the code from."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code, self.message, self.status = code, message, status

    def as_failure(self) -> Result:
        """The failed ``Result`` a component returns for this error (``blocked-escalated`` with its code)."""
        return Result.fail(str(self))


def _reason(data: bytes) -> str:
    """The start of an error reply's text, for the person: control characters as spaces, at most ``_REASON`` long."""
    text = "".join(ch if ch.isprintable() else " " for ch in data[:_REASON * 4].decode("utf-8", "replace"))
    return " ".join(text.split())[:_REASON]


@dataclass(frozen=True)
class ServiceReply:
    """A 2xx reply from a local service."""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


def service_url(program: OutsideProgram, *, environ: Mapping[str, str] | None = None) -> str:
    """The service's base URL: the one its ``env`` variable names, else its declared ``url``; loopback HTTP only
    (:class:`ServiceError` ``G6_E_OUTSIDE_SERVICE_NOT_LOCAL`` otherwise)."""
    if not program.url:
        raise ValueError(f"{program.name} is a command, not a service")
    environ = os.environ if environ is None else environ
    named = environ.get(program.env, "").strip() if program.env else ""
    url = named or program.url
    try:
        _local(url)
    except ValueError as exc:
        raise ServiceError(SERVICE_NOT_LOCAL, f"{program.env} names {_shown(url)!r}: {exc}") from None
    return url


def call_service(program: OutsideProgram, method: str, path: str, *, body: bytes | None = None,
                 headers: Mapping[str, str] | None = None, environ: Mapping[str, str] | None = None,
                 timeout: float = DEFAULT_SERVICE_TIMEOUT,
                 max_bytes: int = DEFAULT_SERVICE_MAX_BYTES) -> ServiceReply:
    """One request to the local service ``program`` declares; see the module docstring."""
    if not path.startswith("/") or "://" in path or any(ch in path for ch in "\r\n\x00"):
        raise ValueError(f"a service path starts with / and is only a path; got {path!r}")
    base = service_url(program, environ=environ)
    host, port, prefix = _local(base)
    deadline, said = time.monotonic() + timeout, f"{program.name} ({program.purpose})"
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    broken: Exception | None = None
    try:
        try:
            connection.connect()
        except OSError as exc:
            raise ServiceError(OUTSIDE_PROGRAM_MISSING,
                               f"{said} is not ready: nothing answered at {base} ({type(exc).__name__}). G6 never "
                               f"installs it; install it yourself: {program.install}") from None
        sock, late = connection.sock, threading.Event()

        def cut() -> None:  # a read's own timeout restarts with every byte; the deadline ends the call however it trickles
            late.set()
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

        timer = threading.Timer(max(0.0, deadline - time.monotonic()), cut)
        timer.daemon = True
        timer.start()
        try:
            connection.request(method, prefix.rstrip("/") + path, body=body,
                               headers={"User-Agent": "g6", **(headers or {})})
            response = connection.getresponse()
            data = response.read(max_bytes + 1)
        except (OSError, http.client.HTTPException) as exc:
            broken = exc
        finally:
            timer.cancel()
    finally:
        connection.close()
    if late.is_set() or isinstance(broken, TimeoutError):  # answered, but not within the limit: running, not missing
        raise ServiceError(SERVICE_TIMEOUT, f"{said} did not finish answering {method} {path} within {timeout:g} "
                           f"seconds")
    if broken is not None:
        raise ServiceError(SERVICE_FAILED, f"{said} took {method} {path} but its reply broke off "
                           f"({type(broken).__name__})")
    if len(data) > max_bytes:
        raise ServiceError(SERVICE_TOO_LARGE, f"{program.name}'s reply to {method} {path} is over {max_bytes} bytes",
                           status=response.status)
    if not 200 <= response.status < 300:
        reason = _reason(data)
        raise ServiceError(SERVICE_STATUS, f"{program.name} answered {method} {path} with {response.status}"
                           + (" (a redirect, which G6 does not follow)" if 300 <= response.status < 400 else "")
                           + (f": {reason}" if reason else ""), status=response.status)
    return ServiceReply(response.status, {k.lower(): v for k, v in response.getheaders()}, data)


def _service(program: OutsideProgram, timeout: float, environ: Mapping[str, str]) -> Detected:
    try:
        url = service_url(program, environ=environ)
    except ServiceError as exc:
        return Detected(program.name, False, detail=f"{exc}; a service must be on this machine")
    host, port, path = _local(url)
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"User-Agent": "g6"})
        status = connection.getresponse().status
    except (OSError, http.client.HTTPException) as exc:
        return Detected(program.name, False, detail=f"nothing answered at {url} ({type(exc).__name__})")
    finally:
        connection.close()
    return Detected(program.name, True, url, detail=f"{url} answered {status}")


def detect(program: OutsideProgram, *, which: Callable[[str], str | None] = find_program, run: Runner = _run,
           environ: Mapping[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> Detected:
    """Whether ``program`` is on this machine and answers; see the module docstring."""
    environ = os.environ if environ is None else environ
    if program.url:
        return _service(program, timeout, environ)
    return _command(program, which, run, environ, timeout)


def require(program: OutsideProgram, **looking: Any) -> Result:
    """What :func:`detect` found, or the refusal saying why and how to install it."""
    found = detect(program, **looking)
    if found.found:
        return Result.ok(found)
    return Result.fail(f"[{OUTSIDE_PROGRAM_MISSING}] {program.name} ({program.purpose}) is not ready: {found.detail}. "
                       f"G6 never installs it; install it yourself: {program.install}")


__all__ = ["BASE_ENV", "DEFAULT_SERVICE_MAX_BYTES", "DEFAULT_SERVICE_TIMEOUT", "DEFAULT_TIMEOUT",
           "OUTSIDE_PROGRAM_MISSING", "SERVICE_FAILED", "SERVICE_NOT_LOCAL", "SERVICE_STATUS", "SERVICE_TIMEOUT",
           "SERVICE_TOO_LARGE", "Detected", "OutsideProgram", "ServiceError", "ServiceReply", "call_service",
           "clean_environment", "detect", "find_program", "require", "service_url"]
