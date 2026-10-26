# SPDX-License-Identifier: Apache-2.0
"""Operating-system isolation for the children that run untrusted code: patches, candidates, generated scripts and a
repository's tests (``g6.core.sandbox``, ``g6.workforce.truth``, ``g6.workforce.repo_tests``).

A caller says what the child may touch (:class:`Confinement`): the folders it may read and write, which must be folders
G6 made for the run (:func:`run_folder`), and the paths it may only read (the interpreter, its standard library, the
virtual environment's packages, the copy or repository under test). :func:`plan` picks this platform's backend for that
interpreter, :mod:`g6.core.component_process` starts the child through :func:`popen`, and the caller ends the run's
identity with :meth:`Plan.release`. No administrator rights, nothing installed by G6: the operating system's own
isolation.

* Windows: an AppContainer, through ``ctypes``. Each run gets its own container profile (``g6.confined.<n>``, the
  lowest number no live run holds, since Windows keeps a little of every container it has started until logoff), made
  for the run and deleted after it, with no capabilities, so no network at all, this machine's loopback included.
  The child starts suspended inside the Job Object every owned child already gets (:mod:`g6.core.process_tree`), so it
  and every process it starts are confined alike and end with the run. G6 grants the run's container read and write on
  the run's folders, and read on a copy G6 made, and on nothing else: it never changes the access control of anything
  it did not create. The container must be able to read the interpreter and the code under test; until it can, the
  backend is unavailable and its reason names the owner's one-time commands (``icacls`` grants of read and execute to
  ALL APPLICATION PACKAGES: one on each folder, inherited by what is in it, and one with ``/T`` on every file already
  in it, which a file whose access list is protected needs), which ``g6 doctor`` prints too. Each native extension (``.pyd``, ``.dll``) under the interpreter's folders is judged by its own access
  list as well: a file whose list is protected never takes its folder's grant, so the folder alone would pass while
  the child cannot load it. G6 never names a folder that is the home
  folder, holds it, or holds G6's own folder with the key file: such a run is not confined, and the reason says why.
  The first confined run of an interpreter is preceded by a test start of it in the container; until that succeeds
  nothing is reported as confined.
* Linux: unavailable, "not tested on this platform": the bubblewrap backend is not built until its tests run.
* macOS: unavailable.

Threat model, where a backend is active. Closed: reading files outside the declared read paths (the owner's home and
``~/.g6`` with its key file among them), writing outside the run's folders, the network by any route (not only
Python's socket class), the owner's other processes, another run's folders, and the parent's records and result files
other than the ones the run itself is given to write. Not closed: anything inside the child itself. A repository's
tests run in the same process as the code under test, and their record is written there (pytest's own report, G6's
runner record and its positive control), so code aimed at that record from inside the child is not excluded. Nor are
what the declared read paths hold; what Windows lets every AppContainer read (the system folders, Program Files, and
anything the owner or a program has granted to ALL APPLICATION PACKAGES); the run's own container folder, which
Windows gives every AppContainer and G6 deletes with the profile; a run folder G6 could not remove, which keeps its
grant, so a later run under the same container name could open it if it knew the folder's random name; and the
processor and memory the child uses within the Job Object's and the deadline's bounds.

With no backend the run goes ahead as before, as the owner, and its result says so: ``isolation: {"backend": null,
"why": ..., "warning": ...}``. ``G6_REQUIRE_ISOLATION=1`` refuses such a run instead (:func:`refusal`,
``G6_E_ISOLATION_UNAVAILABLE``). The warning (:data:`UNCONFINED_WARNING`, the same words wherever G6 says it):

Where no operating-system backend confines it (Linux and macOS today, and Windows before the one-time access grant),
untrusted code that G6 runs for you (a patch, a generated script, a repository's tests) has your own rights: it can
read your key file ~/.g6/.env and your other files, and its network guard works inside Python only. You are
responsible for what you run.

A child of this interpreter also reads the folder that holds the imported ``g6`` package when it lies outside the
interpreter's own folders (an editable install), so that folder is one the access check covers (:func:`interpreter_paths`).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from g6.core.errors import ISOLATION_UNAVAILABLE

#: The setting that refuses a run no backend can confine.
REQUIRE = "G6_REQUIRE_ISOLATION"
#: What G6 tells the person wherever untrusted code may run unconfined (CORE_SANDBOX-01). Owner ruling, 2026-10-23: a
#: warning, never a restriction; nothing refuses a run because of it. Every place that says it uses these words.
UNCONFINED_WARNING = (
    "Where no operating-system backend confines it (Linux and macOS today, and Windows before the one-time access "
    "grant), untrusted code that G6 runs for you (a patch, a generated script, a repository's tests) has your own "
    "rights: it can read your key file ~/.g6/.env and your other files, and its network guard works inside Python "
    "only. You are responsible for what you run.")
APPCONTAINER = "appcontainer"
#: The group every AppContainer reads through; the owner's one-time grant names it.
ALL_APPLICATION_PACKAGES = "S-1-15-2-1"
#: Each run's AppContainer profile is this followed by a number, the lowest no live run holds.
PROFILE_PREFIX = "g6.confined."
_MAX_PROFILES = 4096
_ALREADY_EXISTS = 0x800700B7   # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
UNTESTED = "unavailable: not tested on this platform (G6's bubblewrap backend for Linux is not built yet)"
NO_MACOS = "unavailable: G6 has no operating-system isolation backend for macOS"
#: How long the test start of an interpreter in the container may take.
PROBE_S = 60.0
#: What a confined child's environment says (set by :func:`popen`).
CONFINED_ENV = "G6_CONFINED"
#: Put first in the boot code G6 runs in a child, before anything else (``g6.core.sandbox``, ``g6.workforce.truth``,
#: ``g6.workforce.repo_tests``); it does something only in a confined child on Windows. Python gives a folder made with
#: mode 0o700 (``tempfile.mkdtemp``, pytest's ``tmp_path``) an access list for its owner only, which an AppContainer
#: cannot pass, so the child could not use the folders it makes: such a folder takes the access of the folder it is made
#: in instead. And a path the container may not read reads as absent to ``pathlib`` (as it already does to
#: ``os.path``), so pytest's look at the folder above its root does not stop the run. Conveniences for the child, not
#: guards: the operating system still refuses the access, and code in the child can undo them.
CHILD_PRELUDE = (
    "import os as _g6_os\n"
    "if _g6_os.name == 'nt' and _g6_os.environ.get(" + repr(CONFINED_ENV) + ") == '1':\n"
    "    def _g6_mkdir(path, mode=0o777, *, dir_fd=None, _mkdir=_g6_os.mkdir):\n"
    "        return _mkdir(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)\n"
    "    _g6_os.mkdir = _g6_mkdir\n"
    "    import pathlib as _g6_pathlib\n"
    "    def _g6_absent_if_refused(method):\n"
    "        def checked(self, *args, **kwargs):\n"
    "            try:\n"
    "                return method(self, *args, **kwargs)\n"
    "            except PermissionError:\n"
    "                return False\n"
    "        return checked\n"
    "    for _g6_name in ('exists', 'is_dir', 'is_file'):\n"
    "        setattr(_g6_pathlib.Path, _g6_name, _g6_absent_if_refused(getattr(_g6_pathlib.Path, _g6_name)))\n"
    "del _g6_os\n")


class NotStarted(OSError):
    """The confined child could not be started: nothing ran, confined or not."""


@dataclass(frozen=True)
class Confinement:
    """What a confined child may touch. ``read_write``: folders G6 made for this run (:func:`run_folder`), which the
    child may read, write and delete in. ``read_only``: what it may read besides (the interpreter's folders, the code
    under test). ``network``: whether it may reach other machines (on Windows the container is given the internetClient
    and privateNetworkClientServer capabilities; this machine's loopback stays closed); off, no network at all."""

    read_write: tuple[str, ...]
    read_only: tuple[str, ...] = ()
    network: bool = False


@dataclass(frozen=True)
class Plan:
    """The backend that confines one run; ``backend`` None when none can, and ``why`` says why. A plan with a backend
    holds the run's own container until :meth:`release`."""

    backend: str | None
    why: str
    confinement: Confinement | None = field(default=None, repr=False)
    identity: Any = field(default=None, repr=False, compare=False)

    def record(self) -> dict[str, Any]:
        """What a result carries as its ``isolation``: the backend (or None and why), whether the network was allowed,
        and the run's container (its SID and profile name); an unconfined run also carries :data:`UNCONFINED_WARNING`."""
        identity = self.identity
        record = {"backend": self.backend, "why": self.why,
                  "network": bool(self.confinement is not None and self.confinement.network),
                  "container": identity.sid_text if identity is not None else None,
                  "profile": identity.name if identity is not None else None}
        if self.backend is None:
            record["warning"] = UNCONFINED_WARNING
        return record

    def release(self) -> str:
        """End the run's container (after the run: its profile and folder are deleted); "" or what could not be."""
        return self.identity.close() if self.identity is not None else ""


def required() -> bool:
    """Whether ``G6_REQUIRE_ISOLATION`` asks that a run no backend can confine be refused."""
    return os.environ.get(REQUIRE, "").strip().lower() in {"1", "true", "yes", "on"}


def not_started(record: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """``record`` for a run whose confined child never started: not confined, and why."""
    return {**record, "backend": None, "why": str(exc), "warning": UNCONFINED_WARNING}


_SEEN: ContextVar[list[dict[str, Any]] | None] = ContextVar("g6_confine_seen", default=None)


@contextmanager
def recording() -> Iterator[list[dict[str, Any]]]:
    """The ``record()`` of every plan made in the block (in this thread or task): how each run there was confined. An
    enclosing recording sees them too."""
    outer = _SEEN.get()
    seen: list[dict[str, Any]] = []
    token = _SEEN.set(seen)
    try:
        yield seen
    finally:
        _SEEN.reset(token)
        if outer is not None:
            outer.extend(seen)


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """One ``isolation`` for several runs: the first run that was not confined, else the first; no run, not confined."""
    for record in records:
        if not record.get("backend"):
            return dict(record)
    return dict(records[0]) if records else {"backend": None, "why": "no untrusted code was run", "network": False,
                                             "container": None, "profile": None}


def import_failure(stderr: str) -> str:
    """Why a confined child could not import G6 ("" when its output does not say it could not): the folder that holds
    the ``g6`` package this process imports, and the owner's one-time grant on it (S5). Never only "No module named
    'g6'"."""
    if "No module named 'g6'" not in stderr:
        return ""
    folder = _g6_folder()
    return (f"[{ISOLATION_UNAVAILABLE}] the confined child could not import G6: the operating system's container could "
            f"not read {folder}, the folder that holds the g6 package. To let G6 confine runs that import it, run once "
            f"in a terminal: {_grant_command(folder)}")


def refusal(plan: Plan) -> str:
    """The refusal of a run that isolation was required for and no backend can give (its code first)."""
    return (f"[{ISOLATION_UNAVAILABLE}] {REQUIRE} is set and no operating-system isolation can confine this run, so the "
            f"untrusted code was not run: {plan.why}")


# --- the folders G6 made, and the folders G6 never asks the owner to open ----------------------------------------------

_MADE: set[str] = set()
_LOCK = threading.Lock()


def _key(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def owned(path: str | os.PathLike) -> bool:
    """Whether ``path`` is, or is inside, a folder :func:`run_folder` made and has not removed yet."""
    key = _key(path)
    with _LOCK:
        return any(key == made or key.startswith(made.rstrip(os.sep) + os.sep) for made in _MADE)


@contextmanager
def run_folder(prefix: str = "g6_run_", dir: str | os.PathLike | None = None, *,
               ignore_cleanup_errors: bool = False) -> Iterator[str]:
    """A fresh temporary folder G6 made, so G6 may grant a confined child access to it; removed afterwards."""
    with tempfile.TemporaryDirectory(prefix=prefix, dir=dir, ignore_cleanup_errors=ignore_cleanup_errors) as path:
        key = _key(path)
        with _LOCK:
            _MADE.add(key)
        try:
            yield path
        finally:
            with _LOCK:
                _MADE.discard(key)


def _guarded(path: str | os.PathLike) -> bool:
    """Whether ``path`` is the home folder, holds it, or holds G6's own folder (``~/.g6``, with the key file): G6 never
    asks the owner to open such a folder to the container."""
    from g6.core.storage import g6_home

    target = _key(path)
    for kept in (os.path.expanduser("~"), g6_home()):
        kept = _key(kept)
        if kept == target or kept.startswith(target.rstrip(os.sep) + os.sep):
            return True
    return False


def _g6_folder() -> str:
    """The folder that holds the imported ``g6`` package (with an editable install, the source tree's ``src``)."""
    import g6

    return os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(g6.__file__))))


def _inside(path: str, folder: str) -> bool:
    path, folder = _key(path), _key(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def interpreter_paths(python: str) -> tuple[str, ...]:
    """The folders a child of ``python`` reads to start: its base installation (with the standard library) and, for a
    virtual environment, the environment (with its packages). For this interpreter, also the folder that holds the
    imported ``g6`` package when it is outside both (S5: an editable install), since its children import G6 from there."""
    from g6.core.outside import find_program   # outside -> component_process -> confine: imported here, not above

    exe = find_program(python) or python
    if _key(exe) == _key(sys.executable):
        found = [sys.base_prefix, sys.prefix]
        source = _g6_folder()
        if not any(_inside(source, folder) for folder in found):
            found.append(source)
    else:
        folder = Path(exe).parent
        found = [str(folder)]
        for cfg in (folder / "pyvenv.cfg", folder.parent / "pyvenv.cfg"):
            if cfg.is_file():
                found = [*_home(cfg), str(cfg.parent)]
                break
    return tuple(dict.fromkeys(os.path.realpath(path) for path in found))


def _home(cfg: Path) -> list[str]:
    try:
        lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip().lower() == "home" and value.strip():
            return [value.strip()]
    return []


# --- the backend --------------------------------------------------------------------------------------------------------

def _select() -> tuple[Any, str]:
    """This platform's backend, or None and why."""
    if sys.platform == "win32":
        return _windows()
    if sys.platform.startswith("linux"):
        return None, UNTESTED
    if sys.platform == "darwin":
        return None, NO_MACOS
    return None, f"unavailable: G6 has no operating-system isolation backend for {sys.platform}"


def backend(python: str | None = None, *, probe: bool = True) -> tuple[str | None, str]:
    """The backend that would confine a child of ``python`` (default: this interpreter), or None and the reason.
    ``probe=False`` only reads the folders' access lists: it starts nothing and creates nothing (``g6 doctor``)."""
    chosen, why = _select()
    if chosen is None:
        return None, why
    why = chosen.ready(python or sys.executable, probe=probe)
    return (None, why) if why else (chosen.name, "")


def started(python: str) -> bool:
    """Whether a child of ``python`` was test-started in the container in this process, and started."""
    chosen, _ = _select()
    return chosen is not None and chosen.started(python)


def plan(confinement: Confinement, python: str) -> Plan:
    """How one run of ``python`` is confined, granting the run's own container what ``confinement`` names (the folders
    G6 made only); a ``Plan`` with no backend, and the reason, when none can confine it. Noted for :func:`recording`.
    The caller calls :meth:`Plan.release` after the run."""
    strays = [path for path in confinement.read_write if not owned(path)]
    if strays:
        raise ValueError("G6 changes the access only of folders it created for the run (confine.run_folder), "
                         f"not {strays[0]!r}")
    made = _plan(confinement, python)
    seen = _SEEN.get()
    if seen is not None:
        seen.append(made.record())
    return made


def _plan(confinement: Confinement, python: str) -> Plan:
    chosen, why = _select()
    if chosen is None:
        return Plan(None, why, confinement)
    reads = [path for path in confinement.read_only if os.path.exists(path)]
    unread = [path for path in dict.fromkeys([*interpreter_paths(python), *reads])
              if not owned(path) and not chosen.readable(path)]
    if unread:
        return Plan(None, chosen.cannot_read(unread), confinement)
    why = chosen.ready(python)
    if why:
        return Plan(None, why, confinement)
    try:
        identity = chosen.identity()
    except OSError as exc:
        return Plan(None, f"unavailable: G6 could not make the run's container ({exc})", confinement)
    try:
        for path in reads:
            if owned(path):
                chosen.grant(path, identity.sid, write=False)
        for path in confinement.read_write:
            chosen.grant(path, identity.sid, write=True)
    except OSError as exc:
        left = identity.close()
        return Plan(None, f"unavailable: G6 could not give the container its run folders ({exc})"
                    + (f"; {left}" if left else ""), confinement)
    return Plan(chosen.name, "", confinement, identity)


def popen(plan: Plan, argv: list[str], **options: Any) -> Any:
    """The hook :mod:`g6.core.component_process` starts a confined child with, inside the Job Object on Windows: it takes
    ``subprocess.Popen``'s ``creationflags``, ``stdin``, ``stdout``, ``stderr`` (files or ``DEVNULL``), ``env`` and
    ``cwd``, and returns a process with ``pid``, ``returncode``, ``poll``, ``wait`` and ``kill``. A child that cannot be
    started raises :class:`NotStarted`."""
    chosen, why = _select()
    if chosen is None or plan.confinement is None or plan.identity is None or plan.backend != chosen.name:
        raise NotStarted(f"the confined child did not start: {why or 'its plan has no backend'}")
    try:
        return chosen.start(argv, plan.identity, network=plan.confinement.network, **options)
    except OSError as exc:
        raise NotStarted(f"the confined child did not start: {exc}") from exc


def _grant_command(path: str) -> str:
    """The owner's one-time grant on ``path``, as two commands: the folder's, inherited by what is in it or made in it
    later, then a plain grant on everything already in it (``/T``; ``/C`` goes on past an item it cannot change, ``/Q``
    is quiet). A file whose access list is protected keeps none of the folder's grant, and ``icacls`` drops an
    inheritable entry on a file, so the second command is what reaches it."""
    everything = path.rstrip("\\/") + "\\*"
    return (f'icacls "{path}" /grant "*{ALL_APPLICATION_PACKAGES}:(OI)(CI)(RX)", then '
            f'icacls "{everything}" /grant "*{ALL_APPLICATION_PACKAGES}:(RX)" /T /C /Q')


#: The files a confined child loads as native code: each must let the container read and execute it by its own access
#: list, since a file whose list is protected (inheritance off) never takes its folder's grant.
_NATIVE = (".pyd", ".dll")
#: How many unreadable files a reason names.
_NAMED = 5
#: An access list's rights as SDDL writes them: generic, standard and file rights.
_SDDL_RIGHTS = {"GA": 0x10000000, "GR": 0x80000000, "GW": 0x40000000, "GX": 0x20000000, "RC": 0x00020000,
                "SD": 0x00010000, "WD": 0x00040000, "WO": 0x00080000, "FA": 0x001F01FF, "FR": 0x00120089,
                "FW": 0x00120116, "FX": 0x001200A0, "CC": 0x1, "DC": 0x2, "LC": 0x4, "SW": 0x8, "RP": 0x10,
                "WP": 0x20, "DT": 0x40, "LO": 0x80, "CR": 0x100}
#: ALL APPLICATION PACKAGES, as SDDL writes it.
_PACKAGES_SDDL = ("AC", ALL_APPLICATION_PACKAGES)


def _rights(text: str) -> int | None:
    """An ACE's rights as a mask (generic rights mapped to the file rights), or None when G6 cannot read them."""
    if text.lower().startswith("0x"):
        try:
            mask = int(text, 16)
        except ValueError:
            return None
    elif text and len(text) % 2 == 0 and all(text[i:i + 2] in _SDDL_RIGHTS for i in range(0, len(text), 2)):
        mask = 0
        for i in range(0, len(text), 2):
            mask |= _SDDL_RIGHTS[text[i:i + 2]]
    else:
        return None
    for generic, specific in _GENERIC:
        if mask & generic:
            mask |= specific
    return mask


def _lets_containers_read(sddl: str) -> bool:
    """Whether an access list's text (``_sddl``) lets every AppContainer (ALL APPLICATION PACKAGES) read and execute
    the file it is on: allowed by an entry for that group that is not inherit-only, and not denied. A right G6 cannot
    read grants nothing and, in a denial, denies everything."""
    dacl = sddl.partition("D:")[2] if "D:" in sddl else ""
    if dacl.startswith("NO_ACCESS_CONTROL"):
        return True
    allowed = denied = 0
    for ace in dacl.split("(")[1:]:
        parts = ace.rstrip(")").split(";")
        if len(parts) < 6 or parts[0] not in ("A", "D") or "IO" in parts[1] or parts[5] not in _PACKAGES_SDDL:
            continue
        mask = _rights(parts[2])
        if parts[0] == "A":
            allowed |= mask or 0
        else:
            denied |= 0xFFFFFFFF if mask is None else mask
    return (allowed & ~denied) & _READ_EXECUTE == _READ_EXECUTE


def _unreadable_beneath(folders: Iterable[str]) -> dict[str, list[str]]:
    """The native extensions (``.pyd``, ``.dll``) under each folder whose own access list (``_sddl``) does not let the
    container read and execute them, by folder; a list that cannot be read, or a folder that cannot be listed, counts
    as unreadable. Links and junctions are not followed."""
    found: dict[str, list[str]] = {}
    for top in folders:
        pending, bad = [top], []
        while pending:
            folder = pending.pop()
            try:
                entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
            except OSError:
                bad.append(folder)
                continue
            for entry in entries:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir():
                    pending.append(entry.path)
                elif entry.name.lower().endswith(_NATIVE):
                    try:
                        readable = _lets_containers_read(_sddl(entry.path))
                    except OSError:
                        readable = False
                    if not readable:
                        bad.append(entry.path)
        if bad:
            found[top] = bad
    return found


def _readable(path: str) -> bool:
    chosen, _ = _windows()
    return chosen is not None and chosen.readable(path)


def _readable_all(paths: Iterable[str]) -> bool:
    return all(_readable(path) for path in paths)


def _sddl(path: str) -> str:
    chosen, why = _windows()
    if chosen is None:
        raise OSError(why)
    return chosen.sddl(path)


_WIN: tuple[Any, str] | None = None


def _windows() -> tuple[Any, str]:
    global _WIN
    if _WIN is None:
        try:
            _WIN = (_AppContainer(), "")
        except (OSError, AttributeError) as exc:
            _WIN = (None, f"unavailable: the Windows AppContainer interface could not be loaded ({exc})")
    return _WIN


_READ_EXECUTE = 0x001200A9   # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE
_MODIFY = 0x001301BF         # read, write, execute and delete
_GENERIC = ((0x80000000, 0x00120089), (0x40000000, 0x00120116), (0x20000000, 0x001200A0), (0x10000000, 0x001F01FF))
_FILE_OBJECT, _DACL = 1, 0x4
_CAPABILITIES = ("S-1-15-3-1", "S-1-15-3-3")   # internetClient, privateNetworkClientServer: only when network is asked


class _Identity:
    """One run's AppContainer: its profile name and SID, until :meth:`close` deletes the profile."""

    def __init__(self, owner: _AppContainer, name: str, sid: Any, sid_text: str) -> None:
        self.owner, self.name, self.sid, self.sid_text = owner, name, sid, sid_text
        self._closed: str | None = None

    def close(self) -> str:
        if self._closed is None:
            self._closed = self.owner.close_identity(self)
        return self._closed


class _AppContainer:
    """The Windows backend (``ctypes`` over userenv, advapi32 and kernel32; no dependency)."""

    name = APPCONTAINER

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        self._c, self._w = ctypes, w
        vp, ptr = ctypes.c_void_p, ctypes.POINTER
        self._k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._adv = adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self._env = env = ctypes.WinDLL("userenv", use_last_error=True)
        env.CreateAppContainerProfile.argtypes = (w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, vp, w.DWORD, ptr(vp))
        env.CreateAppContainerProfile.restype = ctypes.c_long
        env.DeleteAppContainerProfile.argtypes = (w.LPCWSTR,)
        env.DeleteAppContainerProfile.restype = ctypes.c_long
        adv.FreeSid.argtypes = (vp,)
        adv.FreeSid.restype = vp
        adv.ConvertStringSidToSidW.argtypes = (w.LPCWSTR, ptr(vp))
        adv.ConvertSidToStringSidW.argtypes = (vp, ptr(vp))
        adv.GetNamedSecurityInfoW.argtypes = (w.LPCWSTR, ctypes.c_int, w.DWORD, ptr(vp), ptr(vp), ptr(vp), ptr(vp),
                                              ptr(vp))
        adv.GetNamedSecurityInfoW.restype = w.DWORD
        adv.SetNamedSecurityInfoW.argtypes = (w.LPWSTR, ctypes.c_int, w.DWORD, vp, vp, vp, vp)
        adv.SetNamedSecurityInfoW.restype = w.DWORD
        adv.SetEntriesInAclW.argtypes = (w.ULONG, vp, vp, ptr(vp))
        adv.SetEntriesInAclW.restype = w.DWORD
        adv.GetAclInformation.argtypes = (vp, vp, w.DWORD, ctypes.c_int)
        adv.GetAce.argtypes = (vp, w.DWORD, ptr(vp))
        adv.EqualSid.argtypes = (vp, vp)
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (vp, w.DWORD, w.DWORD, ptr(vp), vp)
        k32.LocalFree.argtypes = (vp,)
        k32.LocalFree.restype = vp
        k32.InitializeProcThreadAttributeList.argtypes = (vp, w.DWORD, w.DWORD, ptr(ctypes.c_size_t))
        k32.UpdateProcThreadAttribute.argtypes = (vp, w.DWORD, ctypes.c_size_t, vp, ctypes.c_size_t, vp, vp)
        k32.DeleteProcThreadAttributeList.argtypes = (vp,)
        k32.DeleteProcThreadAttributeList.restype = None
        k32.CreateProcessW.argtypes = (w.LPCWSTR, w.LPWSTR, vp, vp, w.BOOL, w.DWORD, vp, w.LPCWSTR, vp, vp)
        k32.CloseHandle.argtypes = (w.HANDLE,)
        self._aap = self.all_packages = self._string_sid(ALL_APPLICATION_PACKAGES)
        self._probed: dict[str, str] = {}
        self._beneath_ok: set[str] = set()   # interpreters whose native extensions the container can read (ready)

    # access lists

    def _error(self, what: str, code: int | None = None) -> OSError:
        code = self._c.get_last_error() if code is None else code
        return OSError(code, f"{what} failed: {self._c.FormatError(code).strip()}")

    def _string_sid(self, text: str) -> Any:
        sid = self._c.c_void_p()
        if not self._adv.ConvertStringSidToSidW(text, self._c.byref(sid)):
            raise self._error(f"ConvertStringSidToSid({text})")
        return sid   # held for the life of the process

    def _sid_text(self, sid: Any) -> str:
        text = self._c.c_void_p()
        if not self._adv.ConvertSidToStringSidW(sid, self._c.byref(text)):
            raise self._error("ConvertSidToStringSid")
        try:
            return self._c.wstring_at(text.value)
        finally:
            self._k32.LocalFree(text)

    def sddl(self, path: str) -> str:
        """The path's access list as text (to compare before and after)."""
        c = self._c
        sd, text = c.c_void_p(), c.c_void_p()
        code = self._adv.GetNamedSecurityInfoW(path, _FILE_OBJECT, _DACL, None, None, None, None, c.byref(sd))
        if code:
            raise self._error(f"GetNamedSecurityInfo({path})", code)
        try:
            if not self._adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, _DACL, c.byref(text), None):
                raise self._error("ConvertSecurityDescriptorToStringSecurityDescriptor")
            try:
                return c.wstring_at(text.value)
            finally:
                self._k32.LocalFree(text)
        finally:
            self._k32.LocalFree(sd)

    def _granted(self, path: str) -> int:
        """The access the path's own access list gives every AppContainer (ALL APPLICATION PACKAGES)."""
        c = self._c
        sd, dacl = c.c_void_p(), c.c_void_p()
        if self._adv.GetNamedSecurityInfoW(path, _FILE_OBJECT, _DACL, None, None, c.byref(dacl), None, c.byref(sd)):
            return 0
        try:
            if not dacl.value:
                return 0x001F01FF   # no access list at all: everyone has everything
            count = (c.c_uint32 * 3)()   # ACL_SIZE_INFORMATION: AceCount, AclBytesInUse, AclBytesFree
            if not self._adv.GetAclInformation(dacl, count, c.sizeof(count), 2):
                return 0
            allowed = denied = 0
            for index in range(count[0]):
                ace = c.c_void_p()
                if not self._adv.GetAce(dacl, index, c.byref(ace)):
                    return 0
                kind, flags = c.string_at(ace.value, 2)
                if kind not in (0, 1) or flags & 0x08:   # allowed or denied, and not inherit-only
                    continue
                if not self._adv.EqualSid(c.c_void_p(ace.value + 8), self._aap):
                    continue
                mask = c.c_uint32.from_address(ace.value + 4).value
                for generic, specific in _GENERIC:
                    if mask & generic:
                        mask |= specific
                if kind == 0:
                    allowed |= mask
                else:
                    denied |= mask
            return allowed & ~denied
        finally:
            self._k32.LocalFree(sd)

    def readable(self, path: str) -> bool:
        return self._granted(path) & _READ_EXECUTE == _READ_EXECUTE

    def cannot_read(self, paths: list[str], files: list[str] | None = None) -> str:
        """Why a run cannot be confined when the container cannot read ``paths`` (or, given ``files``, those files
        beneath them), with the owner's one-time grant; no grant at all when one of them is guarded (the home folder,
        what holds it, what holds G6's own folder)."""
        guarded = [path for path in paths if _guarded(path)]
        if guarded:
            from g6.core.storage import g6_home

            return ("unavailable: " + ", ".join(guarded) + " is your home folder, holds it, or holds G6's own folder ("
                    f"{g6_home()}) with your key file, so G6 never asks you to open it to the container and this run "
                    "cannot be confined; keep the repository and the interpreter in folders of their own")
        what = ", ".join(paths)
        if files:
            what = (f"{len(files)} native extension{'' if len(files) == 1 else 's'} under {what} (each file's own "
                    "access list leaves the container out, whatever the folder's grant says), among them "
                    + ", ".join(files[:_NAMED]) + ("" if len(files) <= _NAMED else f" and {len(files) - _NAMED} more"))
        return ("unavailable: the Windows AppContainer cannot read " + what + ", so untrusted code would run with your "
                "permissions; to let G6 confine it, run once in a terminal (it grants read and execute to ALL "
                "APPLICATION PACKAGES on those folders and everything in them, and nothing else): "
                + ", then ".join(_grant_command(path) for path in paths))

    def grant(self, path: str, sid: Any, *, write: bool) -> None:
        """Give ``sid`` (a run's container) read (and, with ``write``, write and delete) on a folder G6 made, inherited
        by what is in it."""
        c, w = self._c, self._w

        class Trustee(c.Structure):
            _fields_ = (("pMultipleTrustee", c.c_void_p), ("MultipleTrusteeOperation", c.c_int),
                        ("TrusteeForm", c.c_int), ("TrusteeType", c.c_int), ("ptstrName", c.c_void_p))

        class ExplicitAccess(c.Structure):
            _fields_ = (("grfAccessPermissions", w.DWORD), ("grfAccessMode", c.c_int), ("grfInheritance", w.DWORD),
                        ("Trustee", Trustee))

        entry = ExplicitAccess(_MODIFY if write else _READ_EXECUTE, 1, 3,   # GRANT_ACCESS; objects and containers
                               Trustee(None, 0, 0, 0, sid.value))   # TRUSTEE_IS_SID
        sd, old, new = c.c_void_p(), c.c_void_p(), c.c_void_p()
        code = self._adv.GetNamedSecurityInfoW(path, _FILE_OBJECT, _DACL, None, None, c.byref(old), None, c.byref(sd))
        if code:
            raise self._error(f"GetNamedSecurityInfo({path})", code)
        try:
            code = self._adv.SetEntriesInAclW(1, c.byref(entry), old, c.byref(new))
            if code:
                raise self._error("SetEntriesInAcl", code)
            try:
                code = self._adv.SetNamedSecurityInfoW(path, _FILE_OBJECT, _DACL, None, None, new, None)
                if code:
                    raise self._error(f"SetNamedSecurityInfo({path})", code)
            finally:
                self._k32.LocalFree(new)
        finally:
            self._k32.LocalFree(sd)

    # identities

    def identity(self) -> _Identity:
        """A fresh AppContainer profile for one run, deleted by ``close_identity``: the lowest-numbered name no profile
        has now and no container folder is left under, so no two live runs share a SID. Windows keeps each container
        SID's named-object folder until logoff (three directories, two links and a file in the System process) and a
        SID follows from its name, so a random name for every run used them up until no container would start."""
        packages = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), "Packages")
        for number in range(_MAX_PROFILES):
            name = f"{PROFILE_PREFIX}{number}"
            if (packages / name).exists():   # a profile Windows could not fully delete: its temporary files are there
                continue
            sid = self._c.c_void_p()
            hr = self._env.CreateAppContainerProfile(name, "G6 confined run",
                                                     "Untrusted code G6 runs: no network, only its run folder",
                                                     None, 0, self._c.byref(sid))
            if hr & 0xFFFFFFFF != _ALREADY_EXISTS:   # another live run's
                break
        else:
            raise OSError(f"every container name {PROFILE_PREFIX}0 to {_MAX_PROFILES - 1} is in use")
        if hr != 0:
            raise OSError(f"CreateAppContainerProfile failed (0x{hr & 0xFFFFFFFF:08X})")
        try:
            return _Identity(self, name, sid, self._sid_text(sid))
        except OSError:
            self._env.DeleteAppContainerProfile(name)
            self._adv.FreeSid(sid)
            raise

    def close_identity(self, identity: _Identity) -> str:
        hr = self._env.DeleteAppContainerProfile(identity.name)
        self._adv.FreeSid(identity.sid)
        return "" if hr == 0 else (f"G6 could not delete the run's container profile {identity.name} "
                                   f"(0x{hr & 0xFFFFFFFF:08X})")

    # readiness

    def ready(self, python: str, *, probe: bool = True) -> str:
        """"" when a child of ``python`` can be confined; else why not."""
        folders = interpreter_paths(python)
        unread = [folder for folder in folders if not self.readable(folder)]
        if unread:
            return self.cannot_read(unread)
        key = _key(python)
        if key not in self._beneath_ok:   # once it passes it is not read again in this process, as the test start is not
            beneath = _unreadable_beneath(folders)
            if beneath:
                return self.cannot_read(list(beneath), [path for paths in beneath.values() for path in paths])
            self._beneath_ok.add(key)
        if not probe:
            return ""
        if key not in self._probed:
            self._probed[key] = self._probe(python, folders)
        return self._probed[key]

    def started(self, python: str) -> bool:
        return self._probed.get(_key(python)) == ""

    def _probe(self, python: str, folders: tuple[str, ...]) -> str:
        """Start ``python`` once in a container of its own, in a Job Object, before any run is reported as confined."""
        from g6.core.process_tree import ProcessTreeError, _Tree

        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        with run_folder("g6_confine_probe_") as cwd, tempfile.TemporaryFile() as said:
            try:
                identity = self.identity()
            except OSError as exc:
                return f"unavailable: G6 could not make a container to test-start {python} in ({exc})"
            try:
                try:
                    self.grant(cwd, identity.sid, write=True)
                    tree = _Tree([python, "-I", "-c", "import sys"], env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=said, stderr=said,
                                 spawn=lambda argv, **kw: self.start(argv, identity, network=False, **kw))
                except (OSError, ProcessTreeError) as exc:
                    return f"unavailable: G6 could not start {python} in the Windows AppContainer ({exc})"
                try:
                    code = tree.proc.wait(timeout=PROBE_S)
                except subprocess.TimeoutExpired:
                    code = None
                finally:
                    tree.kill()
            finally:
                identity.close()
            if code == 0:
                return ""
            said.seek(0)
            text = said.read(400).decode("utf-8", "replace").strip()
            failed = (f"unavailable: {python} does not start in the Windows AppContainer (exit code {code}"
                      + (f": {text}" if text else "") + ")")
            if any(_guarded(folder) for folder in folders):
                return failed + "; " + self.cannot_read(list(folders)).removeprefix("unavailable: ")
            return (failed + "; the owner's grant of read and execute on its folders may be missing: "
                    + ", then ".join(_grant_command(folder) for folder in folders))

    # starting a child

    def start(self, argv: list[str], identity: _Identity, *, network: bool = False, creationflags: int = 0,
              stdin: Any = None, stdout: Any = None, stderr: Any = None, env: dict[str, str] | None = None,
              cwd: str | os.PathLike | None = None, **_ignored: Any) -> _Child:
        import _winapi
        import msvcrt

        c, w = self._c, self._w

        class SidAndAttributes(c.Structure):
            _fields_ = (("Sid", c.c_void_p), ("Attributes", w.DWORD))

        class SecurityCapabilities(c.Structure):
            _fields_ = (("AppContainerSid", c.c_void_p), ("Capabilities", c.c_void_p),
                        ("CapabilityCount", w.DWORD), ("Reserved", w.DWORD))

        class StartupInfo(c.Structure):
            _fields_ = (("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                        ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                        ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                        ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                        ("lpReserved2", c.c_void_p), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                        ("hStdError", w.HANDLE), ("lpAttributeList", c.c_void_p))   # STARTUPINFOEXW

        class ProcessInformation(c.Structure):
            _fields_ = (("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD),
                        ("dwThreadId", w.DWORD))

        opened: list[int] = []
        copies: list[int] = []
        me = _winapi.GetCurrentProcess()

        def handle(stream: Any) -> int:
            if stream is None or stream == subprocess.DEVNULL:
                descriptor = os.open(os.devnull, os.O_RDWR)
                opened.append(descriptor)
                raw = msvcrt.get_osfhandle(descriptor)
            elif stream in (subprocess.PIPE, subprocess.STDOUT):
                raise ValueError("a confined child's streams are files or DEVNULL, not pipes")
            else:
                raw = msvcrt.get_osfhandle(stream if isinstance(stream, int) else stream.fileno())
            copy = _winapi.DuplicateHandle(me, raw, me, 0, True, _winapi.DUPLICATE_SAME_ACCESS)
            copies.append(copy)
            return copy

        attributes = None
        try:
            given = handle(stdin)
            out = handle(stdout)
            err = out if stderr == subprocess.STDOUT else handle(stderr)
            capabilities = (SidAndAttributes * len(_CAPABILITIES))(
                *(SidAndAttributes(self._string_sid(text).value, 0x4) for text in _CAPABILITIES)) if network else None
            security = SecurityCapabilities(identity.sid.value, c.cast(capabilities, c.c_void_p) if network else None,
                                            len(_CAPABILITIES) if network else 0, 0)
            inherited = list(dict.fromkeys((given, out, err)))
            handles = (w.HANDLE * len(inherited))(*inherited)
            size = c.c_size_t()
            self._k32.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
            attributes = (c.c_char * size.value)()
            if not self._k32.InitializeProcThreadAttributeList(attributes, 2, 0, c.byref(size)):
                attributes = None
                raise self._error("InitializeProcThreadAttributeList")
            if not self._k32.UpdateProcThreadAttribute(attributes, 0, 0x00020009, c.byref(security),   # capabilities
                                                       c.sizeof(security), None, None):
                raise self._error("UpdateProcThreadAttribute(security capabilities)")
            if not self._k32.UpdateProcThreadAttribute(attributes, 0, 0x00020002, handles,   # only these handles
                                                       c.sizeof(handles), None, None):
                raise self._error("UpdateProcThreadAttribute(handle list)")
            info = StartupInfo()
            info.cb = c.sizeof(info)
            info.dwFlags = 0x00000100   # STARTF_USESTDHANDLES
            info.hStdInput, info.hStdOutput, info.hStdError = given, out, err
            info.lpAttributeList = c.addressof(attributes)
            block = None
            if env is not None:
                # Windows starts an AppContainer only when the block names LOCALAPPDATA, and then points it, TEMP and
                # TMP at the container's own folder (in the run's profile), whatever they said.
                env = {"LOCALAPPDATA": os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), **env,
                       CONFINED_ENV: "1"}
                text = "".join(f"{key}={value}\0" for key, value in sorted(env.items(), key=lambda kv: kv[0].upper()))
                text += "\0"
                block = (c.c_wchar * len(text))(*text)
            command = c.create_unicode_buffer(subprocess.list2cmdline([os.fspath(arg) for arg in argv]))
            process = ProcessInformation()
            flags = creationflags | 0x00080000 | 0x00000400   # EXTENDED_STARTUPINFO_PRESENT, CREATE_UNICODE_ENVIRONMENT
            if not self._k32.CreateProcessW(None, command, None, None, True, flags, block,
                                            None if cwd is None else os.fspath(cwd), c.byref(info), c.byref(process)):
                raise self._error("CreateProcess in the AppContainer")
            self._k32.CloseHandle(process.hThread)
            return _Child(process.hProcess, process.dwProcessId, argv)
        finally:
            if attributes is not None:
                self._k32.DeleteProcThreadAttributeList(attributes)
            for copy in copies:
                _winapi.CloseHandle(copy)
            for descriptor in opened:
                os.close(descriptor)


class _Child:
    """A confined child, with the part of ``subprocess.Popen``'s interface the process tree uses."""

    def __init__(self, handle: int, pid: int, args: list[str]) -> None:
        import _winapi

        self._winapi, self._handle, self.pid, self.args, self.returncode = _winapi, handle, pid, args, None

    def poll(self) -> int | None:
        if self.returncode is None and self._winapi.WaitForSingleObject(self._handle, 0) == self._winapi.WAIT_OBJECT_0:
            self.returncode = self._winapi.GetExitCodeProcess(self._handle)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            wait = self._winapi.INFINITE if timeout is None else max(0, int(timeout * 1000))
            if self._winapi.WaitForSingleObject(self._handle, wait) != self._winapi.WAIT_OBJECT_0:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.returncode = self._winapi.GetExitCodeProcess(self._handle)
        return self.returncode

    def kill(self) -> None:
        if self.poll() is None:
            try:
                self._winapi.TerminateProcess(self._handle, 1)
            except PermissionError:
                if self.poll() is None:
                    raise

    terminate = kill

    def __del__(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle:
            self._winapi.CloseHandle(handle)


__all__ = ["APPCONTAINER", "Confinement", "NotStarted", "Plan", "REQUIRE", "UNCONFINED_WARNING", "backend",
           "import_failure", "interpreter_paths", "not_started", "owned", "plan", "popen", "recording", "refusal",
           "required", "run_folder", "started", "summary"]
