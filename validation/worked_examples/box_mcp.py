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
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

KEPT = (".git", ".venv")    # the container's own, at any depth, as the export's ``--exclude`` leaves them: never sent, never touched
MAX_OUTPUT = 20000
TOOL = {"name": "run",
        "description": ("Run a shell command in the repository's test environment: a container holding the repository with its "
                        ".venv and its git history, and no network. Your working folder is mirrored into it before each "
                        "command, so the command sees your latest edits; what a command changes in files is not kept, so make "
                        "every change with your own editing tools. Returns the exit code and the last 20000 characters of the "
                        "command's combined output."),
        "inputSchema": {"type": "object", "properties": {"command": {"type": "string", "description": "The shell command (sh -c)."}},
                        "required": ["command"]}}

#: Run in the container as root, ``python3 -I -c MIRROR REPO USER``, with ``pack``'s tar on standard input: every member checked
#: before anything changes; then what is new or changed written (a changed file in place, keeping its mode and owner), what the tar
#: does not hold removed, ``.git`` and ``.venv`` left as they are, and what it made given to USER. Prints what it wrote and removed.
MIRROR = r'''import io, json, os, shutil, sys, tarfile
try:
    import pwd
except ImportError:          # not a POSIX system: the script's own tests, on the host
    pwd = None
repo, user = os.path.abspath(sys.argv[1]), sys.argv[2]
kept = {".git", ".venv"}
ids = (pwd.getpwnam(user).pw_uid, pwd.getpwnam(user).pw_gid) if pwd else None
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


def clear(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def own(path):
    if ids:
        os.chown(path, *ids)


for name in sorted(dirs):
    path = at(name)
    if os.path.lexists(path) and (os.path.islink(path) or not os.path.isdir(path)):
        clear(path)
    if not os.path.isdir(path):
        os.mkdir(path, 0o755)
        own(path)
for name, m in sorted(files.items()):
    path, data = at(name), tar.extractfile(m).read()
    if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path)):
        clear(path)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            if f.read() == data:
                continue
        with open(path, "wb") as f:
            f.write(data)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        own(path)
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
    """A workspace that cannot be sent: a link, a junction or another reparse point, or an entry that is not inside it."""


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


def pack(workspace: Path) -> bytes:
    """The workspace as a tar, in memory: its plain files and folders, without ``.git`` or ``.venv`` (``KEPT``), each named relative
    to it with ``/``. Anything else refuses the whole workspace (``Refused``), so nothing is sent; so does a folder that cannot be
    read, which would otherwise look empty and have its files removed in the container."""
    root = Path(workspace).resolve()
    out = io.BytesIO()

    def unreadable(exc: OSError) -> None:
        raise Refused(f"the workspace could not be read: {exc}")

    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for here, dirs, files in os.walk(root, onerror=unreadable):
            dirs[:] = sorted(d for d in dirs if d not in KEPT)
            for name in [*dirs, *sorted(f for f in files if f not in KEPT)]:
                path = Path(here) / name
                st = _plain(path, root)
                info = tarfile.TarInfo(path.relative_to(root).as_posix())
                if stat.S_ISDIR(st.st_mode):
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                else:
                    data = path.read_bytes()
                    info.size, info.mode = len(data), 0o644
                    tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def mirror(workspace: Path, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float) -> str:
    """The workspace into the container's repository (``MIRROR``, as root). ``docker`` is called as ``run.Docker`` is. A refused
    workspace sends nothing (``Refused``); a docker step that fails raises ``BoxError``. Returns what the script printed."""
    data = pack(workspace)
    done = docker("exec", "-i", "-u", "root", container, "python3", "-I", "-c", MIRROR, repo, user, input=data, timeout=timeout)
    if done.returncode != 0:
        raise BoxError(f"the mirror failed ({done.returncode}): {(_text(done.stdout) + ' ' + _text(done.stderr)).strip()[-2000:]}")
    return _text(done.stdout).strip()


def run_command(command: str, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float) -> tuple[int, str]:
    """The command in the container, as ``user`` in ``repo``, stopped there (with its children) at ``timeout`` seconds: its exit
    code, and the last ``MAX_OUTPUT`` characters of its combined output after a line giving the code."""
    seconds = str(max(1, int(timeout)))
    done = docker("exec", "-u", user, "-w", repo, container, "timeout", "-k", "10", seconds, "sh", "-c", command,
                  timeout=timeout + 60, merged=True)
    note = f" (124 is what the box gives a command it stopped at {seconds} seconds)" if done.returncode == 124 else ""
    return done.returncode, f"exit code {done.returncode}{note}\n{_text(done.stdout)[-MAX_OUTPUT:]}"


def _result(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def call(opts: Any, params: dict[str, Any], docker: Callable[..., Any]) -> dict[str, Any]:
    """One ``tools/call``: the mirror, then the command. A docker failure, docker's own exit 125 included, is a tool error."""
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
    return _result(text, error=code == 125)


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
