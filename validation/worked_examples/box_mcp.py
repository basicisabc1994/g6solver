# SPDX-License-Identifier: Apache-2.0
"""The box (PLAN.md, 2026-11-04): the one tool the host's Claude Code has for running anything, an MCP server on standard input
and output (newline-delimited JSON-RPC 2.0). Claude Code reads and edits the workspace, a folder on the host; each call of ``run``
first mirrors the workspace into the bug's container (``mirror``), then runs the command there, as the container's user, in the
repository. The container has no network, and the subscription's sign-in never enters it.

``python -I box_mcp.py --container NAME --repo PATH_IN_CONTAINER --workspace HOST_DIR --user USER --timeout SECONDS --docker EXE``

Only the standard library, so ``-I`` holds.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tarfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

KEPT = (".git", ".venv")    # the container's own, at any depth, as the export's ``--exclude`` leaves them: never sent, never touched
NAME_MAX = 255              # the bytes one part of a name may have on the container's Linux file systems
PATH_MAX = 4096             # the bytes of a path the container's kernel takes, its terminating NUL included
#: A name part Windows would refuse or change (a device name, a reserved character, a trailing dot or space): the one rule for a
#: name the host holds as it is named, read by the export to the host (``run.workspace_from``) and by ``pack`` from it.
HOST_UNSAFE = re.compile(r'[<>:"|?*\\\x00-\x1f]|[. ]$|^(?:con|prn|aux|nul|com\d|lpt\d)(?:\..*)?$', re.I)
MAX_OUTPUT = 20000
START = "g6-box-start"      # the start line's word, before the call's own nonce (``run_command``)
#: What ``sh -c`` runs in the container, with the start line as ``$0`` and the command as ``$1``: the start line, then the command.
SHELL = 'printf "%s\\n" "$0"; exec sh -c "$1"'
TOOL = {"name": "run",
        "description": ("Run a shell command in the repository's test environment: a container holding the repository with its "
                        ".venv and its git history, and no network. Your working folder is mirrored into it before each "
                        "command, so the command sees your latest edits; what a command changes in files is not kept, so make "
                        "every change with your own editing tools, and a process a command leaves running is stopped before the "
                        "next mirror. Returns the exit code and the last 20000 characters of the command's combined output."),
        "inputSchema": {"type": "object", "properties": {"command": {"type": "string", "description": "The shell command (sh -c)."}},
                        "required": ["command"]}}

#: The first step of ``MIRROR``, as root in the container: every process of the container's user is killed, but the container's
#: first (``tail``, which runs nothing of the agent's), until none runs (a zombie runs nothing); else the mirror is refused. So
#: nothing the agent started can change a name between the mirror's check of it and its act on it.
STOP = r'''

def stop(uid):
    for _ in range(50):
        left = []
        for name in os.listdir("/proc"):
            if not name.isdigit() or int(name) in (1, os.getpid()):
                continue
            try:
                with open("/proc/" + name + "/status") as f:
                    fields = dict(line.split(":", 1) for line in f.read().splitlines() if ":" in line)
            except OSError:
                continue
            if str(uid) in fields.get("Uid", "").split() and fields.get("State", "").split()[:1] not in (["Z"], ["X"]):
                left.append(int(name))
        if not left:
            return
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.1)
    sys.exit("refused: processes of uid %d still run: %s" % (uid, left[:10]))
'''

#: Run in the container as root, ``python3 -I -c MIRROR REPO USER``, with ``pack``'s tar on standard input. First every process of
#: USER is stopped (``STOP``); then the repository's root must be a folder of its own, not a link, and its real path, resolved once,
#: the path it was given, so no ancestor is a link or a junction; every member is checked,
#: and no folder holding ``.git`` or ``.venv`` at any depth may be removed, all before anything changes. Then what is new or
#: changed is written: never through a name already there, which is removed (a folder, a link, another name of a file) and the
#: file made fresh, exclusively, keeping a replaced file's mode and owner; what the tar does not hold removed, ``.git`` and
#: ``.venv`` left as they are, and what it made given to USER. Prints what it wrote and removed.
MIRROR = r'''import io, json, os, shutil, signal, stat, sys, tarfile, time
try:
    import pwd
except ImportError:          # not a POSIX system: the script's own tests, on the host
    pwd = None
''' + STOP + r'''
repo, user = os.path.abspath(sys.argv[1]), sys.argv[2]
kept = {".git", ".venv"}
ids = (pwd.getpwnam(user).pw_uid, pwd.getpwnam(user).pw_gid) if pwd else None
if ids:
    if ids[0] in (0, os.geteuid()):
        sys.exit("refused: the mirror stops another user's processes, never its own")
    stop(ids[0])


def kind(path):
    """What holds a name: (None, None), or ("dir", st) for a folder of its own, ("file", st) for a regular file with one name,
    and ("other", st) for anything else: a link, a junction, another name of a file, a device."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, None
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return "other", st
    if stat.S_ISDIR(st.st_mode):
        return "dir", st
    return ("file" if stat.S_ISREG(st.st_mode) and st.st_nlink == 1 else "other"), st


if kind(repo)[0] != "dir":
    sys.exit("refused: the repository's root " + repr(repo) + " is not a folder of its own")
real = os.path.realpath(repo)      # resolved once, now that nothing of the user's runs: no ancestor may be a link or junction
if os.path.normcase(real) != os.path.normcase(repo):
    sys.exit("refused: the repository's path " + repr(repo) + " passes through a link to " + repr(real))
tar = tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read()))
files, dirs = {}, set()
for m in tar.getmembers():
    parts = m.name.split("/")
    if m.name.startswith("/") or {"", ".", ".."} & set(parts) or kept & set(parts) or not (m.isfile() or m.isdir()):
        sys.exit("refused: " + repr(m.name))
    if m.isdir():
        dirs.add(m.name)
    else:
        files[m.name] = m
written = removed = 0


def at(name):
    return os.path.join(repo, *name.split("/"))


for name in files:
    if kind(at(name))[0] == "dir" and any(kept & {*subdirs, *names} for _, subdirs, names in os.walk(at(name))):
        sys.exit("refused: " + repr(name) + " would replace a folder holding .git or .venv")


def clear(path):
    if kind(path)[0] == "dir":
        shutil.rmtree(path)
    else:
        os.remove(path)


def own(path):
    if ids:
        os.chown(path, *ids)


def excl(path, flags):
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)


def fresh(path, data, mode, owner):
    for _ in range(3):
        try:
            f = open(path, "wb", opener=excl)
        except FileExistsError:
            clear(path)        # the name is held, by the old file or one made since: removed, never written through
            continue
        with f:
            if owner:
                os.fchown(f.fileno(), *owner)
                os.fchmod(f.fileno(), mode)
            f.write(data)
        return
    sys.exit("refused: " + repr(path) + " was held again each time it was cleared")


for name in sorted(dirs):
    path = at(name)
    if kind(path)[0] not in (None, "dir"):
        clear(path)
    if kind(path)[0] is None:
        os.mkdir(path, 0o755)
        own(path)
for name, m in sorted(files.items()):
    path, data = at(name), tar.extractfile(m).read()
    held, st = kind(path)
    if held == "file":
        with open(path, "rb") as f:
            if f.read() == data:
                continue
    elif held is not None:
        clear(path)
    fresh(path, data, *((stat.S_IMODE(st.st_mode), (st.st_uid, st.st_gid)) if held == "file" and ids else (0o644, ids)))
    written += 1
for here, subdirs, names in os.walk(repo, topdown=False):
    rel = os.path.relpath(here, repo).replace(os.sep, "/")
    parts = [] if rel == "." else rel.split("/")
    if kept & set(parts):
        continue
    for n in names + [d for d in subdirs if os.path.islink(os.path.join(here, d))]:
        if n not in kept and "/".join(parts + [n]) not in files:
            os.remove(os.path.join(here, n))
            removed += 1
    if parts and rel not in dirs and not os.listdir(here):
        os.rmdir(here)
        removed += 1
print(json.dumps({"written": written, "removed": removed}))
'''


class Refused(RuntimeError):
    """A workspace that cannot be sent: a link, a junction or another reparse point, an entry that is not inside it, a name the
    container cannot hold or the host cannot hold as named, a tree changed while it was packed, or anything that could not be
    packed."""


class BoxError(RuntimeError):
    """A docker step that failed."""


def _text(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")


def _plain(path: Path, root: Path) -> os.stat_result:
    rel = path.relative_to(root).as_posix()
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise Refused(f"{rel} is a link, a junction or another reparse point")
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        raise Refused(f"{rel} is not a plain file or folder")
    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        raise Refused(f"{rel} has another name (a hard link)")
    if not path.resolve().is_relative_to(root):
        raise Refused(f"{rel} resolves outside the workspace")
    return st


def _read(path: Path, checked: os.stat_result, rel: str) -> bytes:
    """A file's bytes, read from a descriptor that is the file ``_plain`` checked: the same device and inode, a regular file with
    one name. A name given to another file in between refuses the workspace."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if (st.st_dev, st.st_ino) != (checked.st_dev, checked.st_ino) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise Refused(f"{rel} is not the file that was checked: its name was given to another between the check and the read")
        return f.read()


def _same_folder(path: Path, checked: os.stat_result, rel: str) -> None:
    """A folder the walk went through, checked again once the walk is done: still a folder of its own, not a link, a junction or
    another reparse point, and the folder that was checked (the same device and inode). Anything else refuses the workspace."""
    st = os.lstat(path)
    if (stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != (checked.st_dev, checked.st_ino)):
        raise Refused(f"{rel} is not the folder that was checked: it was changed while the workspace was packed")


def unsafe_part(parts: Iterable[str]) -> str | None:
    """The first of ``parts`` that Windows would refuse or change (``HOST_UNSAFE``), else None: the one rule for a name the host
    holds as it is named, which the export to the host (``run.workspace_from``) and every path part the box reads, packs or sends
    (``_root``, ``_holdable``) pass. A part Windows changes names another file or folder: ``name.`` and ``name `` are read as
    ``name``."""
    return next((part for part in parts if HOST_UNSAFE.search(part)), None)


def _unsafe(what: str, part: str) -> Refused:
    return Refused(f"{what} cannot be read by its name: its part {part!r} is one Windows would refuse or change (a device name, a "
                   "reserved character, a trailing dot or space)")


def _holdable(rel: str, repo: str) -> None:
    """A name the host holds as named and the container can hold, else ``Refused``: no part of it is one Windows would refuse or
    change (``unsafe_part``); each part encodes as UTF-8 strictly, in at most ``NAME_MAX`` bytes; and the path the mirror opens,
    ``repo`` and the name (the name alone when no ``repo`` is given), is shorter than ``PATH_MAX``, so with its NUL it fits."""
    if (bad := unsafe_part(rel.split("/"))) is not None:
        raise _unsafe(repr(rel), bad)
    for part in rel.split("/"):
        try:
            size = len(part.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise Refused(f"{rel!r} cannot be named in the container: a part of it is not UTF-8 ({exc.reason})") from exc
        if size > NAME_MAX:
            raise Refused(f"{rel}: File name too long: its part {part!r} is {size} bytes in UTF-8, and the container holds "
                          f"{NAME_MAX} at most")
    whole = len((f"{repo.rstrip('/')}/{rel}" if repo else rel).encode("utf-8"))
    if whole >= PATH_MAX:
        raise Refused(f"{rel}: File name too long: its path in the container is {whole} bytes, and the container opens "
                      f"{PATH_MAX - 1} at most")


def _root(path: Path) -> os.stat_result:
    """The workspace's own folder, as a walk of it starts: every part of its path after the drive or root (``..`` apart) one the
    host holds as named (``unsafe_part``), checked before anything is read through it, so ``workspace.`` is never read as
    ``workspace``; then a folder of its own, not a link, a junction or another reparse point. Else ``Refused``."""
    path = Path(path)
    if (bad := unsafe_part(part for part in path.parts[1 if path.anchor else 0:] if part != "..")) is not None:
        raise _unsafe(f"the workspace {path}", bad)
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise Refused(f"the workspace could not be read: {exc}") from exc
    if (stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not stat.S_ISDIR(st.st_mode)):
        raise Refused(f"the workspace {path} is a link, a junction or another reparse point, or not a folder")
    return st


def _tree(root: Path, repo: str) -> Iterator[tuple[Path, str, os.stat_result, bytes | None]]:
    """The workspace's entries in the order they are packed. First the root itself is checked (``_root``), so a walk, the re-walk
    among them, reads nothing through a root that became a link. Then each plain file and folder (``_plain``) but ``.git`` and
    ``.venv`` (``KEPT``), its name relative to the workspace with ``/``, checked before it is used to read anything as one the
    host holds as named and the container can hold (``_holdable``), and a file's bytes, read as the file that was checked
    (``_read``); a folder's are None. A folder that cannot be read refuses the workspace."""
    def unreadable(exc: OSError) -> None:
        raise Refused(f"the workspace could not be read: {exc}")

    _root(root)
    for here, dirs, files in os.walk(root, onerror=unreadable):
        dirs[:] = sorted(d for d in dirs if d not in KEPT)
        for name in [*dirs, *sorted(f for f in files if f not in KEPT)]:
            path = Path(here) / name
            rel = path.relative_to(root).as_posix()
            _holdable(rel, repo)
            st = _plain(path, root)
            yield path, rel, st, None if stat.S_ISDIR(st.st_mode) else _read(path, st, rel)


def _digest(data: bytes | None) -> bytes | None:
    """A file's SHA-256, None for a folder."""
    return None if data is None else hashlib.sha256(data).digest()


def pack(workspace: Path, repo: str = "") -> bytes:
    """The workspace as a tar, in memory: its plain files and folders, without ``.git`` or ``.venv`` (``KEPT``), each named relative
    to it with ``/``, every name one the container can hold under ``repo`` (``_holdable``). Anything else refuses the whole
    workspace (``Refused``), so nothing is sent; so does a workspace that is itself a link, a junction or another reparse point,
    and a folder that cannot be read, which would otherwise look empty and have its files removed in the container. Each file is
    read as the file that was checked (``_read``); once the walk is done every folder it went through, the workspace's own
    included, is checked again (``_same_folder``), and then the whole tree is walked again, its root checked first (``_tree``):
    its names, what each is, and each file's SHA-256 must be what was packed, else the workspace changed while it was packed and
    is refused, so the next call sends it as it is then. Any failure to pack it (a name the tar cannot hold, a read that fails,
    the tar's own error) refuses it too: nothing is sent or run."""
    given = Path(workspace)
    top = _root(given)
    try:
        root = given.resolve()
        out = io.BytesIO()
        walked = [(given, top, "the workspace")]
        packed: list[tuple[str, bytes | None]] = []
        with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path, rel, st, data in _tree(root, repo):
                info = tarfile.TarInfo(rel)
                if data is None:
                    walked.append((path, st, rel))
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                else:
                    info.size, info.mode = len(data), 0o644
                    tar.addfile(info, io.BytesIO(data))
                packed.append((rel, _digest(data)))
        for path, st, rel in walked:
            _same_folder(path, st, rel)
        now = [(rel, _digest(data)) for _, rel, _, data in _tree(root, repo)]
        if now != packed:
            at = next((i for i, (was, is_) in enumerate(zip(packed, now)) if was != is_), min(len(packed), len(now)))
            raise Refused(f"{(packed if at < len(packed) else now)[at][0]} changed while the workspace was packed: walked again, "
                          "the tree is not what was packed, so nothing is sent")
    except Refused:
        raise
    except (OSError, ValueError, tarfile.TarError) as exc:       # UnicodeError is a ValueError
        raise Refused(f"the workspace could not be packed: {type(exc).__name__}: {exc}") from exc
    return out.getvalue()


def mirror(workspace: Path, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float) -> str:
    """The workspace into the container's repository (``MIRROR``, as root). ``docker`` is called as ``run.Docker`` is. A refused
    workspace, a name the container cannot hold under ``repo`` among them, sends nothing and runs nothing (``Refused``); a docker
    step that fails raises ``BoxError``. Returns what the script printed."""
    data = pack(workspace, repo)
    done = docker("exec", "-i", "-u", "root", container, "python3", "-I", "-c", MIRROR, repo, user, input=data, timeout=timeout)
    if done.returncode != 0:
        raise BoxError(f"the mirror failed ({done.returncode}): {(_text(done.stdout) + ' ' + _text(done.stderr)).strip()[-2000:]}")
    return _text(done.stdout).strip()


def run_command(command: str, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float) -> tuple[int, str]:
    """The command in the container, as ``user`` in ``repo``, stopped there (with its children) at ``timeout`` seconds, behind a
    start line (``START``) with a nonce chosen for this call, which the container's shell prints before the command runs: its exit
    code, and the last ``MAX_OUTPUT`` characters of its combined output, the start line taken out, after a line giving the code. A
    reply without that line, as a line of its own, raises ``BoxError`` whatever its exit code: the command never ran, and what came
    back is docker's, its daemon's or the container's."""
    seconds = str(max(1, int(timeout)))
    start = f"{START} {secrets.token_hex(16)}"
    done = docker("exec", "-u", user, "-w", repo, container, "timeout", "-k", "10", seconds, "sh", "-c", SHELL, start, command,
                  timeout=timeout + 60, merged=True)
    said = _text(done.stdout)
    before, line, after = said.partition(start + "\n")
    if not line or (before and not before.endswith("\n")):
        raise BoxError(f"the command never ran: its start line did not come back (exit {done.returncode}): {said.strip()[-2000:]}")
    note = f" (124 is what the box gives a command it stopped at {seconds} seconds)" if done.returncode == 124 else ""
    return done.returncode, f"exit code {done.returncode}{note}\n{(before + after)[-MAX_OUTPUT:]}"


def _result(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def call(opts: Any, params: dict[str, Any], docker: Callable[..., Any]) -> dict[str, Any]:
    """One ``tools/call``: the mirror, then the command. A docker failure is a tool error: a mirror that failed, and a reply without
    the call's start line, whatever its exit code (``run_command``); so is a docker client ended by a signal (a return code below 0,
    or none), which gives no exit of the command's."""
    arguments = params.get("arguments") or {}
    if params.get("name") != "run":
        return _result(f"there is no tool {params.get('name')!r}; the one tool is run", error=True)
    command = arguments.get("command") if isinstance(arguments, dict) else None
    if not isinstance(command, str) or not command.strip():
        return _result("run needs a command: a string that is not empty", error=True)
    where = {"container": opts.container, "repo": opts.repo, "user": opts.user, "timeout": float(opts.timeout)}
    try:
        mirror(Path(opts.workspace), docker, **where)
        code, text = run_command(command, docker, **where)
    except Refused as exc:
        return _result(f"nothing was mirrored or run: {exc}", error=True)
    except (BoxError, OSError, subprocess.SubprocessError) as exc:
        return _result(f"{type(exc).__name__}: {exc}", error=True)
    return _result(text, error=code is None or code < 0)


def serve(stdin: BinaryIO, stdout: BinaryIO, opts: Any, docker: Callable[..., Any]) -> None:
    """Answer each request, one line each: ``initialize``, ``tools/list``, ``tools/call``, and ``{}`` for any other request.
    A notification (no id) gets no reply; a line that is not a request is skipped."""
    for raw in stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict) or "id" not in msg or "method" not in msg:
            continue
        method, params = msg["method"], msg.get("params") or {}
        if method == "initialize":
            result: dict[str, Any] = {"protocolVersion": params.get("protocolVersion") or "2024-11-05",
                                      "capabilities": {"tools": {}}, "serverInfo": {"name": "box", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            result = call(opts, params, docker)
        else:
            result = {}
        stdout.write((json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n").encode("utf-8"))
        stdout.flush()


def docker_runner(exe: str, runner: Callable[..., Any] = subprocess.run) -> Callable[..., Any]:
    """The docker CLI (list arguments, no shell), called as ``run.Docker`` is; ``merged`` puts its stderr in its stdout."""
    def docker(*args: str, input: bytes | None = None, timeout: float | None = None, merged: bool = False) -> Any:
        return runner([exe, *args], input=input, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT if merged else subprocess.PIPE, timeout=timeout)
    return docker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for flag in ("--container", "--repo", "--workspace", "--user", "--docker"):
        ap.add_argument(flag, required=True)
    ap.add_argument("--timeout", type=float, required=True)
    opts = ap.parse_args(argv)
    serve(sys.stdin.buffer, sys.stdout.buffer, opts, docker_runner(opts.docker))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
