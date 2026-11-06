# SPDX-License-Identifier: Apache-2.0
"""The box (PLAN.md, 2026-11-04): the one tool the host's Claude Code has for running anything, an MCP server on standard input
and output (newline-delimited JSON-RPC 2.0). Claude Code reads and edits the workspace, a folder on the host; each call of ``run``
first mirrors the workspace into the bug's container (``mirror``), then runs the command there, as the container's user, in the
repository, then brings what the command changed in files back into the workspace (``pull``), so the call ends with one tree in
both places (replay cohort, 2026-11-06). The container has no network, and the subscription's sign-in never enters it.

A symbolic link is carried as git carries one where links cannot be made (PLAN.md, 2026-11-06 evening, ruling 1): in the
workspace it is a plain file holding its target, listed in the workspace's link record (``links_of``) as git's index lists mode
120000 under ``core.symlinks=false``; in the container it is a link again. It is never followed on either side, whatever its
target, and two trees compare a link by its target and kind (``_mark``), never by what it points to.

``python -I box_mcp.py --container NAME --repo PATH_IN_CONTAINER --workspace HOST_DIR --user USER --timeout SECONDS --docker EXE
[--state FILE]``

``--state``: a file outside the workspace where each call's phase is kept (``write_state``), so a call ended before its pull is
found and settled by the runner (``settle``). Only the standard library, so ``-I`` holds.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path, PurePosixPath
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
                        "command, so the command sees your latest edits, and when the command ends what it changed in files "
                        "comes back into your working folder, so a file a command writes, edits or removes is part of your "
                        "change. A symbolic link in the repository is a plain file in your working folder holding the link's "
                        "target, as git holds a link where links cannot be made: writing that file changes where the link "
                        "points; to change what it points to, edit the file its target names. A process a command leaves "
                        "running is stopped when the command ends. Returns the exit code and the last 20000 characters of the "
                        "command's combined output."),
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
#: the path it was given, so no ancestor is a link or a junction; every member is checked (a plain file, a folder or a symbolic
#: link, each named once, and each inside a folder the tar holds, so nothing is made through a link or a file), and no folder
#: holding ``.git`` or ``.venv`` at any depth may be removed, all before anything changes. Then what is new or changed is
#: written: never through a name already there, which is removed (a folder, a link, another name of a file) and the file or link
#: made fresh, exclusively, keeping a replaced file's mode and owner; a link already there with the same target is left as it is;
#: what the tar does not hold removed (a link by itself, never what it points to), ``.git`` and ``.venv`` left as they are, and
#: what it made given to USER. A link's target is its text: never read, followed or checked. Prints what it wrote and removed.
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
files, dirs, links = {}, set(), {}
for m in tar.getmembers():
    parts = m.name.split("/")
    if (m.name.startswith("/") or {"", ".", ".."} & set(parts) or kept & set(parts) or not (m.isfile() or m.isdir() or m.issym())
            or m.name in files or m.name in dirs or m.name in links or (m.issym() and not m.linkname)):
        sys.exit("refused: " + repr(m.name))
    if m.isdir():
        dirs.add(m.name)
    elif m.issym():
        links[m.name] = m.linkname
    else:
        files[m.name] = m
for name in [*dirs, *files, *links]:
    if "/" in name and name.rsplit("/", 1)[0] not in dirs:
        sys.exit("refused: " + repr(name) + " is not inside a folder the tar holds")
written = removed = 0


def at(name):
    return os.path.join(repo, *name.split("/"))


for name in [*files, *links]:
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


def link(path, target):
    for _ in range(3):
        try:
            os.symlink(target, path)
        except FileExistsError:
            clear(path)        # the name is held: removed, never written through
            continue
        if ids:
            os.chown(path, *ids, follow_symlinks=False)
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
for name, target in sorted(links.items()):
    path = at(name)
    held, st = kind(path)
    if held == "other" and stat.S_ISLNK(st.st_mode) and os.readlink(path) == target:
        continue               # the same link: left as it is
    if held is not None:
        clear(path)
    link(path, target)
    written += 1
for here, subdirs, names in os.walk(repo, topdown=False):
    rel = os.path.relpath(here, repo).replace(os.sep, "/")
    parts = [] if rel == "." else rel.split("/")
    if kept & set(parts):
        continue
    for n in names + [d for d in subdirs if os.path.islink(os.path.join(here, d))]:
        if n not in kept and "/".join(parts + [n]) not in files and "/".join(parts + [n]) not in links:
            os.remove(os.path.join(here, n))
            removed += 1
    if parts and rel not in dirs and not os.listdir(here):
        os.rmdir(here)
        removed += 1
print(json.dumps({"written": written, "removed": removed}))
'''

#: The container's tree, as the one export to the host makes it: run in the repository as the container's user, every plain file
#: and folder but ``.git`` and ``.venv`` at any depth (GNU tar's exclusions match any name part). ``run.workspace_from`` takes the
#: parent's tree out with it, and ``PULL`` a command's tree after the command.
EXPORT = ("tar", "-c", "--exclude=.git", "--exclude=.venv", ".")

#: Run in the container as root, ``python3 -I -c PULL REPO USER``, once a command has ended: every process of USER is stopped
#: (``STOP``), so nothing the command left running changes a file after the export reads it, then the export (``EXPORT``) runs as
#: USER in the repository and writes the tar on standard output.
PULL = r'''import os, signal, sys, time
try:
    import pwd
except ImportError:          # not a POSIX system: the script's own tests, on the host
    pwd = None
''' + STOP + r'''
repo, user = os.path.abspath(sys.argv[1]), sys.argv[2]
ids = (pwd.getpwnam(user).pw_uid, pwd.getpwnam(user).pw_gid) if pwd else None
if ids:
    if ids[0] in (0, os.geteuid()):
        sys.exit("refused: the pull stops another user's processes, never its own")
    stop(ids[0])
    os.setgroups([])
    os.setgid(ids[1])
    os.setuid(ids[0])
os.chdir(repo)
sys.stdout.flush()
os.execvp(''' + repr(EXPORT[0]) + ", " + repr(list(EXPORT)) + r''')
'''


class Refused(RuntimeError):
    """A workspace that cannot be sent: a real link, a junction or another reparse point (a symbolic link of the repository's is
    a plain file there, ``links_of``), an entry that is not inside it, a name the container cannot hold or the host cannot hold
    as named, a link's target the container cannot make, a link record that cannot be read, a tree changed while it was packed,
    or anything that could not be packed. And a container's tree that cannot be brought back (``bring_in``): an entry the
    workspace cannot hold as it is, or a workspace that is not, after it, exactly that tree."""


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


def root_named(path: Path) -> Path:
    """``path``, when every part of it after the drive or root (``..`` apart) is one the host holds as named (``unsafe_part``),
    else ``Refused``: the one check of a workspace's own path, made before anything is read through it (``_root``) or written
    into it (``run.workspace_from``), so ``workspace.`` is never read or written as ``workspace``."""
    path = Path(path)
    if (bad := unsafe_part(part for part in path.parts[1 if path.anchor else 0:] if part != "..")) is not None:
        raise _unsafe(f"the workspace {path}", bad)
    return path


def _root(path: Path) -> os.stat_result:
    """The workspace's own folder, as a walk of it starts: its path one the host holds as named (``root_named``), then a folder
    of its own, not a link, a junction or another reparse point. Else ``Refused``."""
    path = root_named(path)
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise Refused(f"the workspace could not be read: {exc}") from exc
    if (stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not stat.S_ISDIR(st.st_mode)):
        raise Refused(f"the workspace {path} is a link, a junction or another reparse point, or not a folder")
    return st


FOLDER = "/"          # what a folder holds in a tree's map (``tree_map``), where a file holds its SHA-256 in hex
LINK = "@"            # before a link's mark in a tree's map: the SHA-256 of its target, in hex, after it


def _mark(data: bytes | None, link: bool = False) -> str:
    """The one rule for what an entry is in a tree's map, on either side: ``FOLDER`` for a folder, a file's SHA-256 in hex, and for
    a symbolic link ``LINK`` and its target's SHA-256, so a link and a file holding the same bytes differ, and a link is compared by
    its target and kind, never by what it points to."""
    if data is None:
        return FOLDER
    return (LINK if link else "") + hashlib.sha256(data).hexdigest()


def _link_target(rel: str, target: bytes) -> str:
    """A link's target as the container can make it, else ``Refused``: UTF-8, as every name is (``_holdable``), not empty, with no
    NUL, and shorter than ``PATH_MAX`` bytes. Every other target is carried, one that leaves the repository too: it is the link's
    text, which nothing reads, follows or checks."""
    try:
        text = target.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused(f"{rel} is a link whose target is not UTF-8 ({exc.reason})") from exc
    nul = b"\x00" in target
    if not target or nul or len(target) >= PATH_MAX:
        raise Refused(f"{rel} is a link whose target the container cannot make ({len(target)} bytes{', with a NUL' if nul else ''}): "
                      f"a target is 1 to {PATH_MAX - 1} bytes with no NUL")
    return text


def links_of(workspace: Path) -> Path:
    """The workspace's link record: a file beside it, outside it, so the agent's tools never reach it and no pack reads it as the
    tree's, removed with it at the run's end. It lists which of the workspace's plain files are symbolic links, as git's index
    lists mode 120000 where links cannot be made (``core.symlinks=false``): such a file holds the link's target, and writing it
    changes the target, as it does in git."""
    base = Path(workspace).resolve()
    return base.parent / f"{base.name}.box.links.json"


def _is_mark(mark: Any) -> bool:
    """A mark as ``_mark`` writes one, or None for a name that is not there."""
    return mark is None or mark == FOLDER or (isinstance(mark, str)
                                              and re.fullmatch(f"{re.escape(LINK)}?[0-9a-f]{{64}}", mark) is not None)


def read_links(workspace: Path) -> dict[str, Any]:
    """The workspace's link record (``links_of``): ``links``, the names of its plain files that are links, and ``pending``, by name,
    what a pull that may not have ended was changing a link-or-file to (``{"from": mark, "to": mark}``). None there: no link. One
    that cannot be read as the box writes it refuses (``Refused``): which files are links is then not known, and nothing may
    guess, since a link read as a file would reach the container as a file."""
    path = links_of(workspace)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"links": set(), "pending": {}}
    except OSError as exc:
        raise Refused(f"the workspace's link record {path.name} could not be read: {exc}") from exc
    try:
        got = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refused(f"the workspace's link record {path.name} cannot be read: {exc}") from exc
    links, pending = (got.get("links"), got.get("pending")) if isinstance(got, dict) else (None, None)
    if not (isinstance(links, list) and all(isinstance(x, str) for x in links) and isinstance(pending, dict)
            and all(isinstance(v, dict) and set(v) == {"from", "to"} and _is_mark(v["from"]) and _is_mark(v["to"])
                    for v in pending.values())):
        raise Refused(f"the workspace's link record {path.name} is not a list of links with the changes a pull was making")
    return {"links": set(links), "pending": pending}


def write_links(workspace: Path, links: Iterable[str], pending: Mapping[str, Mapping[str, str | None]] | None = None) -> None:
    """The workspace's link record written whole to a file of its own beside it, flushed to disk, then put in its place in one step
    (``os.replace``), so a reader finds it before or after the write, never part of it."""
    path = links_of(workspace)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with open(temp, "wb") as f:
        f.write(json.dumps({"links": sorted(links), "pending": dict(pending or {})}, indent=1, sort_keys=True).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def _is_link(rel: str, data: bytes, record: Mapping[str, Any]) -> bool:
    """Whether the workspace's plain file ``rel``, holding ``data``, is a link: by the record (``read_links``). A name a pull was
    changing (``pending``) is what the side its bytes are is: the side it was changing to, or else the side it was changing
    from, so a pull ended after it wrote a name and before it kept the record is read as it left the name. Otherwise, as git
    reads mode 120000, a listed name is a link, whatever its bytes now are."""
    change = record["pending"].get(rel)
    if change is not None:
        for side in (change["to"], change["from"]):
            if side in (_mark(data), _mark(data, True)):
                return side.startswith(LINK)
    return rel in record["links"]


def _tree(root: Path, repo: str) -> Iterator[tuple[Path, str, os.stat_result, bytes | None, bool]]:
    """The workspace's entries in the order they are packed. First the root itself is checked (``_root``), so a walk, the re-walk
    among them, reads nothing through a root that became a link. Then each plain file and folder (``_plain``) but ``.git`` and
    ``.venv`` (``KEPT``), its name relative to the workspace with ``/``, checked before it is used to read anything as one the
    host holds as named and the container can hold (``_holdable``), a file's bytes, read as the file that was checked
    (``_read``), a folder's None, and whether the file is a symbolic link by the workspace's link record (``_is_link``), whose
    target the container must be able to make (``_link_target``). A folder or a record that cannot be read refuses the
    workspace."""
    def unreadable(exc: OSError) -> None:
        raise Refused(f"the workspace could not be read: {exc}")

    _root(root)
    record = read_links(root)
    for here, dirs, files in os.walk(root, onerror=unreadable):
        dirs[:] = sorted(d for d in dirs if d not in KEPT)
        for name in [*dirs, *sorted(f for f in files if f not in KEPT)]:
            path = Path(here) / name
            rel = path.relative_to(root).as_posix()
            _holdable(rel, repo)
            st = _plain(path, root)
            if stat.S_ISDIR(st.st_mode):
                yield path, rel, st, None, False
                continue
            data = _read(path, st, rel)
            link = _is_link(rel, data, record)
            if link:
                _link_target(rel, data)
            yield path, rel, st, data, link


def pack(workspace: Path, repo: str = "") -> bytes:
    """The workspace as a tar, in memory: its plain files and folders, without ``.git`` or ``.venv`` (``KEPT``), each named relative
    to it with ``/``, every name one the container can hold under ``repo`` (``_holdable``), and each file its link record lists
    (``_is_link``) as a symbolic link whose target is the file's bytes, never as a file. Anything else refuses the whole
    workspace (``Refused``), so nothing is sent; so does a workspace that is itself a link, a junction or another reparse point,
    and a folder that cannot be read, which would otherwise look empty and have its files removed in the container. Each file is
    read as the file that was checked (``_read``); once the walk is done every folder it went through, the workspace's own
    included, is checked again (``_same_folder``), and then the whole tree is walked again, its root checked first (``_tree``):
    its names and each one's mark (``_mark``: what it is, and a file's or a link's SHA-256) must be what was packed, else the
    workspace changed while it was packed and is refused, so the next call sends it as it is then. Any failure to pack it (a
    name the tar cannot hold, a read that fails, the tar's own error) refuses it too: nothing is sent or run."""
    given = Path(workspace)
    top = _root(given)
    try:
        root = given.resolve()
        out = io.BytesIO()
        walked = [(given, top, "the workspace")]
        packed: list[tuple[str, str]] = []
        with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path, rel, st, data, link in _tree(root, repo):
                info = tarfile.TarInfo(rel)
                if data is None:
                    walked.append((path, st, rel))
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                elif link:
                    info.type, info.linkname, info.mode = tarfile.SYMTYPE, data.decode("utf-8"), 0o777
                    tar.addfile(info)
                else:
                    info.size, info.mode = len(data), 0o644
                    tar.addfile(info, io.BytesIO(data))
                packed.append((rel, _mark(data, link)))
        for path, st, rel in walked:
            _same_folder(path, st, rel)
        now = [(rel, _mark(data, link)) for _, rel, _, data, link in _tree(root, repo)]
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


def tree_map(workspace: Path, repo: str = "") -> dict[str, str]:
    """The workspace's tree as a map: each entry's name relative to it with ``/``, to its mark (``_mark``): a file's SHA-256 in hex,
    ``LINK`` and a link's target's, or ``FOLDER``. It is read as ``pack`` reads it (``_tree``): the root and every entry checked,
    and anything that could not be packed refuses it."""
    given = Path(workspace)
    _root(given)
    try:
        return {rel: _mark(data, link) for _, rel, _, data, link in _tree(given.resolve(), repo)}
    except Refused:
        raise
    except (OSError, ValueError) as exc:
        raise Refused(f"the workspace could not be read: {type(exc).__name__}: {exc}") from exc


def written(dest: Path, members: Iterable[tarfile.TarInfo]) -> None:
    """Each member of an export brought into ``dest`` is what was written: there under its exact name in its folder's listing, and,
    for a file or a link (a plain file holding its target there), a file no other member is (by device and inode, which NTFS
    gives too). Raises ``Refused`` at the first that is not, so a short-name alias, a name in another case or any other collision
    is never taken for the member."""
    listed: dict[Path, set[str]] = {}
    held: dict[tuple[int, int], str] = {}
    for member in members:
        parts = PurePosixPath(member.name).parts
        if not parts:
            continue
        path = Path(dest).joinpath(*parts)
        if parts[-1] not in listed.setdefault(path.parent, set(os.listdir(path.parent))):
            raise Refused(f"{member.name!r} is not there under its own name: the host gave it another's")
        if not member.isdir():
            st = os.lstat(path)
            if (st.st_dev, st.st_ino) in held:
                raise Refused(f"{member.name!r} and {held[(st.st_dev, st.st_ino)]!r} are one file on the host")
            held[(st.st_dev, st.st_ino)] = member.name


def target_of(member: tarfile.TarInfo) -> bytes:
    """A link member's target as bytes, as the tar holds them (``tarfile`` reads a name it cannot decode with ``surrogateescape``);
    one that cannot be put back into bytes refuses (``Refused``)."""
    try:
        return member.linkname.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError as exc:
        raise Refused(f"{member.name!r} is a link whose target is not UTF-8 ({exc.reason})") from exc


def export_members(tar: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    """The one rule for a tree that comes out of the container (``EXPORT``), into an empty workspace (``run.workspace_from``) or over
    one after a command (``pull``): each member by its name relative to the repository with ``/``, the export's own ``./`` taken
    off (the root itself is no member). Only plain files, folders and symbolic links, a link whatever its target, which is its
    text and is never followed: an absolute name, a ``..`` part, a hard link, a device, a ``.git`` or ``.venv`` part, a name the
    host cannot hold as named (``_holdable``), a link's target the container could not make again (``_link_target``), or two
    names the host reads as one (differing only in case) refuses the whole tree (``Refused``), before anything is written."""
    found: dict[str, tarfile.TarInfo] = {}
    seen: dict[str, str] = {}
    for member in tar.getmembers():
        parts = PurePosixPath(member.name).parts
        if (member.name.startswith(("/", "\\")) or ".." in parts or not (member.isfile() or member.isdir() or member.issym())
                or set(parts) & set(KEPT)):
            raise Refused(f"the container's tree holds {member.name!r}, which the host cannot hold as a plain file, a folder or "
                          "a symbolic link inside the workspace")
        if not parts:
            continue
        rel = "/".join(parts)
        _holdable(rel, "")
        if member.issym():
            _link_target(rel, target_of(member))
        if rel.casefold() in seen:
            raise Refused(f"the container's tree holds {member.name!r} and {seen[rel.casefold()]!r}, which the host holds as one name")
        seen[rel.casefold()] = member.name
        found[rel] = member
    return found


class Partial(Refused):
    """A container's tree that could not be brought into the workspace after the pull began to write it: part of it is there."""


def spare(workspace: Path) -> Path:
    """The box's own folder beside the workspace, outside it, where a file the pull writes is made whole before it is moved into
    its place: no pack reads it, and the run removes it with the workspace."""
    base = Path(workspace).resolve()
    return base.parent / f"{base.name}.box.pull"


def _write_new(path: Path, data: bytes, *, over: Path | None = None) -> None:
    """A file put in place with ``data``, never written through a name that is there. A new one is made fresh, exclusively, so a
    name that is another file's short-name alias meets that file and fails. ``over``: the file replaces one, so it is made whole,
    exclusively, in that folder (``spare``), then moved over its name in one step (``os.replace``): a write that fails leaves the
    old file as it was."""
    if over is None:
        with open(path, "xb") as f:
            f.write(data)
        return
    over.mkdir(exist_ok=True)
    temp = over / f"{secrets.token_hex(8)}.part"
    try:
        with open(temp, "xb") as f:
            f.write(data)
        os.replace(temp, path)
    finally:
        if os.path.lexists(temp):
            os.remove(temp)


def bring_in(workspace: Path, data: bytes, repo: str = "", before: Mapping[str, str] | None = None) -> list[str]:
    """The container's tree (``data``, an ``EXPORT`` tar) made the workspace's, and the names it changed there, sorted. Every member
    is checked first (``export_members``) and the workspace read as ``pack`` reads it, so a tree that cannot be held writes
    nothing. ``before``: the tree the container was given before the command (``tree_map``, as the call's state keeps it). Then
    each name takes the container's side where the workspace still holds what the container was given, and keeps the workspace's
    where the workspace changed since, so nothing written in the workspace after the mirror is lost; with no ``before`` (an empty
    workspace, ``run.workspace_from``) the container's tree is taken whole. Removals go first, deepest first, then folders,
    shallowest first, then files in the export's own order (``_write_new``): a new file made fresh, exclusively, so a name that
    is another file's short-name alias meets that file and refuses the tree, and a changed file made whole beside the workspace
    and moved over the old one, so a write that fails leaves it as it was. A symbolic link is written as git writes one where
    links cannot be made, a plain file holding its target, and listed in the workspace's link record (``write_links``): before
    anything is written, every name whose link-or-file the pull changes is kept there as pending, so a pull ended part way is
    read as it left each name (``_is_link``); once the tree is checked the record is set to the tree's links. Last, the
    workspace is read again: every member is there under its exact name and no two files are one (``written``), and the tree is
    exactly the one decided. A failure before anything is touched is ``Refused``; one after, ``Partial``: part of the tree is in
    the workspace, and the link record is set to what the workspace then holds."""
    root = Path(workspace)
    _root(root)
    base = root.resolve()
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data))
        members = export_members(tar)
        theirs = {rel: None if m.isdir() else target_of(m) if m.issym() else tar.extractfile(m).read()
                  for rel, m in members.items()}
    except tarfile.TarError as exc:
        raise Refused(f"the container's tree could not be read: {type(exc).__name__}: {exc}") from exc
    marks = {rel: _mark(body, members[rel].issym()) for rel, body in theirs.items()}
    record = read_links(base)
    now = tree_map(root, repo)
    if before is None:
        target = dict(marks)
    else:
        target = {}
        for rel in {*before, *marks, *now}:
            side = marks.get(rel) if now.get(rel) == before.get(rel) else now.get(rel)
            if side is not None:
                target[rel] = side
    for rel in list(target):                    # every folder a file is in is a folder of the tree
        parts = rel.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if target.get(parent, FOLDER) != FOLDER:
                raise Refused(f"{rel!r} would be inside {parent!r}, a file or a link: the tree cannot hold it")
            target[parent] = FOLDER
    linked = {rel for rel, mark in target.items() if mark.startswith(LINK)}
    current = {rel for rel, mark in now.items() if mark.startswith(LINK)}     # as the record reads the workspace now
    pending = {rel: {"from": now.get(rel), "to": target.get(rel)} for rel in sorted({*now, *target})
               if now.get(rel) != target.get(rel) and LINK in (str(now.get(rel))[:1], str(target.get(rel))[:1])}
    changed: list[str] = []                     # each name is listed before it is touched, so a failure after it says "in part"

    def failed(why: str) -> Refused:
        if pending:                             # the record set to what the workspace holds now; else its pending stays, read as is
            try:
                write_links(base, {rel for rel, mark in tree_map(root, repo).items() if mark.startswith(LINK)})
            except (Refused, OSError, ValueError):
                pass
        return (Partial if changed else Refused)(why)

    try:
        if pending:                             # before anything is written: which names the pull changes a link of, and to what
            write_links(base, current, pending)
        for rel in sorted(now, key=lambda r: (-r.count("/"), r)):
            if target.get(rel) == now[rel] or (rel in target and FOLDER not in (target[rel], now[rel])):
                continue                        # the same, or a file changed in place: put in place whole below
            path = base.joinpath(*rel.split("/"))
            if not os.path.lexists(path):
                continue                        # gone already with its folder
            changed.append(rel)
            if now[rel] == FOLDER:
                shutil.rmtree(path)
            else:
                os.remove(path)
        order = {rel: i for i, rel in enumerate(members)}      # files in the export's own order, as an extraction writes them
        for rel in sorted(target, key=lambda r: (target[r] != FOLDER, r.count("/") if target[r] == FOLDER else order.get(r, -1), r)):
            if target[rel] == now.get(rel):
                continue
            path = base.joinpath(*rel.split("/"))
            changed.append(rel)
            if target[rel] == FOLDER:
                os.mkdir(path)
            else:                               # over an old file: made whole beside the workspace, then moved over it
                _write_new(path, theirs[rel], over=spare(base) if now.get(rel) not in (None, FOLDER) else None)
        written(base, [m for rel, m in members.items() if target.get(rel) == marks[rel]])
        after = tree_map(root, repo)
    except Refused as exc:
        raise failed(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise failed(f"the container's tree could not be written into the workspace: {type(exc).__name__}: {exc}") from exc
    if after != target:
        wrong = sorted(set(after.items()) ^ set(target.items()))[:5]
        raise failed(f"the workspace is not the tree decided after the container's tree was brought in: {wrong}")
    if pending or record["pending"] or linked != record["links"]:
        try:
            write_links(base, linked)
        except OSError as exc:
            raise failed(f"the workspace's link record could not be kept: {type(exc).__name__}: {exc}") from exc
    return sorted(set(changed))


def pull(workspace: Path, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float,
         state: Path | None = None, call_no: int = 0, before: Mapping[str, str] | None = None) -> list[str]:
    """What a command changed in files, brought from the container into the workspace: ``PULL`` (as root: the user's processes
    stopped, then the export), then ``bring_in``, three-way from ``before``. With ``state``, the call's phase "pulling" is kept
    there (``write_state``) before anything is written, so a box ended in the middle of it is found and settled (``settle``). A
    docker step that fails raises ``BoxError``; a tree that cannot be held, ``Refused``. Returns the names it changed."""
    done = docker("exec", "-u", "root", container, "python3", "-I", "-c", PULL, repo, user, timeout=timeout)
    if done.returncode != 0:
        raise BoxError(f"the pull failed ({done.returncode}): {_text(done.stderr).strip()[-2000:]}")
    if state is not None:
        write_state(state, {"call": call_no, "phase": "pulling"})
    return bring_in(workspace, done.stdout or b"", repo, before)


def set_back(workspace: Path, docker: Callable[..., Any], **where: Any) -> str:
    """The container set to the workspace again (``mirror``) after a pull that could not be made, so the two hold one tree: "" when
    it was, else why not."""
    try:
        mirror(workspace, docker, **where)
    except (Refused, BoxError, OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"[:2000]
    return ""


def read_state(path: Path) -> dict[str, Any] | None:
    """The box's state file (``write_state``), or None when there is none. One that cannot be read as the box writes it refuses
    (``Refused``): what a call left in the container is then not known, and nothing may guess it."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Refused(f"the box's state {path} could not be read: {exc}") from exc
    try:
        got = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refused(f"the box's state {path} cannot be read: {exc}") from exc
    calls = got.get("calls") if isinstance(got, dict) else None
    if not isinstance(calls, list) or not all(isinstance(x, dict) and isinstance(x.get("call"), int) and isinstance(x.get("phase"), str)
                                              for x in calls):
        raise Refused(f"the box's state {path} is not a list of calls with their phases")
    return got


def write_state(path: Path, entry: Mapping[str, Any]) -> None:
    """One call's phase kept in the box's state file, a file outside the workspace: merged into that call's entry, or a new entry
    for a new call. Written whole to a file of its own beside it, flushed to disk, then put in its place in one step
    (``os.replace``), so a reader finds the state before or after the write, never part of it. A settled call keeps no map."""
    state = read_state(path) or {"calls": []}
    calls = state["calls"]
    if calls and calls[-1]["call"] == entry["call"]:
        calls[-1] = {**calls[-1], **entry}
    else:
        calls.append(dict(entry))
    if calls[-1]["phase"] not in ("mirrored", "pulling"):
        calls[-1].pop("before", None)
    temp = Path(path).with_name(Path(path).name + f".{os.getpid()}.tmp")
    with open(temp, "wb") as f:
        f.write(json.dumps(state, indent=1, sort_keys=True).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def _changes(changed: list[str]) -> dict[str, Any]:
    return {"changed": changed[:200], "count": len(changed)}


def settle(workspace: Path, docker: Callable[..., Any], *, state: Path, container: str, repo: str, user: str,
           timeout: float) -> dict[str, Any] | None:
    """A call the box did not finish, finished by the runner once the session has ended (``run._run_task``'s ``turn``), before the
    workspace is mirrored again. None when the last call in the box's state is settled, set back or not settled, or there is no
    state. A call left "mirrored" or "pulling" (its command may have changed the container, and its pull may have written part of
    the workspace) is pulled now, three-way from the tree the call mirrored, so what the command changed is kept and what the
    workspace changed after it is not written over; a tree that cannot be held is "not settled", with why, and the mirror after
    this sets the container to the workspace. A state that cannot be read refuses (``read_state``)."""
    calls = (read_state(state) or {"calls": []})["calls"]
    last = calls[-1] if calls else None
    if last is None or last["phase"] not in ("mirrored", "pulling"):
        return None
    n, before = last["call"], last.get("before")
    if not isinstance(before, dict):
        raise Refused(f"the box's state {state} gives call {n} no tree it was mirrored from")
    try:
        changed = pull(workspace, docker, container=container, repo=repo, user=user, timeout=timeout, state=state, call_no=n,
                       before=before)
    except Refused as exc:
        entry = {"call": n, "phase": "not settled", "by": "runner", "error": str(exc)[:2000]}
    else:
        entry = {"call": n, "phase": "settled", "by": "runner", **_changes(changed)}
    write_state(state, entry)
    return {k: v for k, v in entry.items()}


BACK = ("what the command changed in files could not be brought back into your working folder ({why}), so none of it is kept: "
        "{after}. Make the change with your own editing tools.")
#: A pull that failed after it began to write (``Partial``): part of the command's change is in the working folder.
PART = ("what the command changed in files was brought back into your working folder only in part ({why}): {after}, as your "
        "working folder now is. Read the files the command was to change before you go on.")


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
    """One ``tools/call``: the mirror, the command, then the pull, so the call ends with one tree in the workspace and the
    container. A docker failure is a tool error: a mirror that failed, and a reply without the call's start line, whatever its exit
    code (``run_command``); so is a docker client ended by a signal (a return code below 0, or none), which gives no exit of the
    command's. Once the mirror is made, the pull is made whatever the command returned: a command that ran in part changed files
    too. A pull that cannot be made is a tool error after the command's output, which says so, and the container is set back to
    the workspace (``set_back``), so no change is dropped without the agent being told. With ``opts.state`` each phase is kept
    (``write_state``): "mirrored" with the tree the container was given, "pulling", then "settled", "set back" or "not settled"."""
    arguments = params.get("arguments") or {}
    if params.get("name") != "run":
        return _result(f"there is no tool {params.get('name')!r}; the one tool is run", error=True)
    command = arguments.get("command") if isinstance(arguments, dict) else None
    if not isinstance(command, str) or not command.strip():
        return _result("run needs a command: a string that is not empty", error=True)
    where = {"container": opts.container, "repo": opts.repo, "user": opts.user, "timeout": float(opts.timeout)}
    workspace, state = Path(opts.workspace), getattr(opts, "state", None)
    state = Path(state) if state else None
    try:
        mirror(workspace, docker, **where)
    except Refused as exc:
        return _result(f"nothing was mirrored or run: {exc}", error=True)
    except (BoxError, OSError, subprocess.SubprocessError) as exc:
        return _result(f"{type(exc).__name__}: {exc}", error=True)
    try:
        before = tree_map(workspace, opts.repo)
        calls = (read_state(state) or {"calls": []})["calls"] if state is not None else []
        n = (calls[-1]["call"] + 1) if calls else 1
        if state is not None:
            write_state(state, {"call": n, "phase": "mirrored", "by": "box", "before": before})
    except (Refused, OSError) as exc:
        return _result(f"the workspace was mirrored and no command was run: the box could not keep its state: {exc}", error=True)
    try:
        code, text = run_command(command, docker, **where)
        failed = code is None or code < 0
    except (BoxError, OSError, subprocess.SubprocessError) as exc:
        text, failed = f"{type(exc).__name__}: {exc}", True
    try:
        changed = pull(workspace, docker, **where, state=state, call_no=n, before=before)
    except (Refused, BoxError, OSError, subprocess.SubprocessError) as exc:
        why = str(exc) if isinstance(exc, Refused) else f"{type(exc).__name__}: {exc}"
        left = set_back(workspace, docker, **where)
        after = ("the container was set back to your working folder" if not left else
                 f"the container could not be set back to your working folder ({left}), and is set back before your next command")
        partial = isinstance(exc, Partial)
        entry: dict[str, Any] = {"call": n, "phase": "set back" if not left else "not settled", "error": why[:2000],
                                 "in_part": partial}
        try:
            if state is not None:
                write_state(state, entry)
        except (Refused, OSError) as exc2:
            after += f"; the box could not keep its state ({exc2})"
        return _result(f"{text}\n\n{(PART if partial else BACK).format(why=why, after=after)}", error=True)
    try:
        if state is not None:
            write_state(state, {"call": n, "phase": "settled", **_changes(changed)})
    except (Refused, OSError) as exc:
        return _result(f"{text}\n\nwhat the command changed in files is in your working folder, but the box could not keep its "
                       f"state: {exc}", error=True)
    return _result(text, error=failed)


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


def options(argv: list[str] | None = None) -> argparse.Namespace:
    """The box's options, from its command line as the run's MCP configuration gives it (``run.host_files``)."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for flag in ("--container", "--repo", "--workspace", "--user", "--docker"):
        ap.add_argument(flag, required=True)
    ap.add_argument("--timeout", type=float, required=True)
    ap.add_argument("--state", default=None)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    opts = options(argv)
    serve(sys.stdin.buffer, sys.stdout.buffer, opts, docker_runner(opts.docker))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
