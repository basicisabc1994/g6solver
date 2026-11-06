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
found and settled (``settle``), by the next call before it mirrors anything and by the runner after the session. Only the
standard library, so ``-I`` holds.

It also holds the one reader of a container call's result and of a test report (``seen``, ``tests_seen``: passed, failed or
unknown, and unknown is never either), which the runner and the cohort controls import (replay cohort, repair 1).
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
from typing import Any, BinaryIO, NamedTuple

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
#: holding ``.git`` or ``.venv`` at any depth may be removed, all before anything changes. No path operation reaches through a
#: link at any depth: each name's path is used only once every folder above it is read, by lstat from the root down, as a folder
#: of its own (``inside``, ``path_of``), and a name below a link or a file is not in the repository's tree; the tree is walked
#: by lstat (``below``), a link or anything but a folder of its own an entry never walked into; a folder the mirror made holds
#: only what it writes there, and nothing in it is read (``made``, ``holding``). Then what is new or changed is
#: written: never through a name already there, which is removed (a folder, a link, another name of a file) and the file or link
#: made fresh, exclusively, keeping a replaced file's owner, and its mode when the executable bit is the tar's; a file's mode is
#: its tar member's, git's 0755 or 0644, so the executable bit is carried; a file with the same bytes whose executable bit
#: differs has its mode set on itself; a link already there with the same target is left as it is; what the tar does not hold
#: removed (a link by itself, never what it points to), ``.git`` and ``.venv`` left as they are, and what it made given to USER.
#: A link's target is its text: never read, followed or checked. Prints what it wrote and removed.
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
made = set()   # the folders this mirror made: empty when made, so all they hold is what it writes there, never read again


def at(name):
    return os.path.join(repo, *name.split("/")) if name else repo


def inside(name):
    """Whether each folder above ``name`` is a folder of its own, read by lstat from the repository's root down (``kind``) and
    stopping at the first that is not, so nothing below a link, a file or anything else is ever read: such a name is not in the
    repository's tree. A folder this mirror made (``made``) is one, and so is every folder the tar holds below it."""
    parts = name.split("/")
    for i in range(1, len(parts)):
        above = "/".join(parts[:i])
        if above in made:
            return True
        if kind(at(above))[0] != "dir":
            return False
    return True


def holding(name):
    """What holds ``name`` (``kind``), read only when its folder was there before this mirror: in a folder it made, nothing."""
    return (None, None) if "/" in name and name.rsplit("/", 1)[0] in made else kind(at(name))


def path_of(name):
    """``name``'s path in the container, once each folder above it is a folder of its own (``inside``); else the mirror refuses,
    so no path operation reaches through a link at any depth."""
    if not inside(name):
        sys.exit("refused: " + repr(name) + " is below a link or a file in the repository")
    return at(name)


def below(name):
    """Every entry below the folder ``name`` ("" for the repository's root), deepest first, each with its kind by lstat
    (``kind``): a link, or anything but a folder of its own, is an entry never walked into, as are ``.git`` and ``.venv``, and a
    folder this mirror made, which holds only what the tar holds."""
    found = []
    for n in sorted(os.listdir(path_of(name))):
        child = name + "/" + n if name else n
        what = kind(at(child))[0]
        if what == "dir" and n not in kept and child not in made:
            found.extend(below(child))
        found.append((child, what))
    return found


for name in [*files, *links]:
    if inside(name) and kind(at(name))[0] == "dir" and any(kept & set(rel.split("/")) for rel, _ in below(name)):
        sys.exit("refused: " + repr(name) + " would replace a folder holding .git or .venv")


def clear(path):
    if kind(path)[0] == "dir":
        shutil.rmtree(path)
    else:
        os.remove(path)


def own(path):
    if ids:
        os.chown(path, *ids)


def setmode(fd, mode):
    if hasattr(os, "fchmod"):  # not on the host's own tests on Windows, whose files hold no executable bit
        os.fchmod(fd, mode)


def git_mode(member):
    return 0o755 if member.mode & stat.S_IXUSR else 0o644


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
            setmode(f.fileno(), mode)
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


for name in sorted(dirs):     # a folder before what is in it: each folder above it is already one of its own
    path = path_of(name)
    if holding(name)[0] not in (None, "dir"):
        clear(path)
    if holding(name)[0] is None:
        os.mkdir(path, 0o755)
        own(path)
        made.add(name)
for name, m in sorted(files.items()):
    path, data, run = path_of(name), tar.extractfile(m).read(), bool(m.mode & stat.S_IXUSR)
    held, st = holding(name)
    keeps = held == "file" and bool(st.st_mode & stat.S_IXUSR) == run      # the old file's mode, its executable bit the tar's
    if held == "file":
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)), "rb") as f:
            if f.read() == data:
                if not keeps:          # the same bytes, the executable bit changed: set on the file itself
                    setmode(f.fileno(), git_mode(m))
                    written += 1
                continue
    elif held is not None:
        clear(path)
    fresh(path, data, stat.S_IMODE(st.st_mode) if keeps else git_mode(m), (st.st_uid, st.st_gid) if held == "file" and ids else ids)
    written += 1
for name, target in sorted(links.items()):
    path = path_of(name)
    held, st = holding(name)
    if held == "other" and stat.S_ISLNK(st.st_mode) and os.readlink(path) == target:
        continue               # the same link: left as it is
    if held is not None:
        clear(path)
    link(path, target)
    written += 1
for rel, held in below(""):    # deepest first: what is in a folder before the folder
    if kept & set(rel.split("/")):
        continue
    if held != "dir":
        if rel not in files and rel not in links:
            os.remove(path_of(rel))
            removed += 1
    elif rel not in dirs and not os.listdir(path_of(rel)):
        os.rmdir(path_of(rel))
        removed += 1
print(json.dumps({"written": written, "removed": removed}))
'''

#: The container's tree, as the one export to the host makes it: run in the repository as the container's user, every plain file
#: and folder but ``.git`` and ``.venv`` at any depth (GNU tar's exclusions match any name part). ``run.workspace_from`` takes the
#: parent's tree out with it, and ``PULL`` a command's tree after the command.
EXPORT = ("tar", "-c", "--exclude=.git", "--exclude=.venv", ".")

#: Run in the container as root, ``python3 -I -c PULL REPO USER``, once a command has ended: every process of USER is stopped
#: (``STOP``), so nothing the command left running changes a file after the export reads it; the repository's root must be a
#: folder of its own, not a link, reached through no link (its real path, resolved once, the path it was given), as the mirror
#: checks it; then the export (``EXPORT``) runs as USER in the repository and writes the tar on standard output. GNU tar stores a
#: link as a link and walks into no link, at any depth.
PULL = r'''import os, signal, stat, sys, time
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
st = os.lstat(repo)
if (stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or not stat.S_ISDIR(st.st_mode)
        or os.path.normcase(os.path.realpath(repo)) != os.path.normcase(repo)):
    sys.exit("refused: the repository's root " + repr(repo) + " is not a folder of its own reached through no link")
if ids:
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


# --- the one reader of a container call's result, and of a test report (replay cohort, repair 1) --------------------------------
#: The three states a container call's result, or a test report, is read as: it ran and passed, it ran and failed, or what it did
#: is not known. Only what was observed is either of the first two; everything else is UNKNOWN, never a pass and never a failure.
PASSED, FAILED, UNKNOWN = "passed", "failed", "unknown"


class Seen(NamedTuple):
    """What one container call was seen to do (``seen``): its state, why when it is not PASSED, the exit code that came back (None
    when none did), its output, and the answer ``read`` took from that output (None when there was none)."""
    state: str
    why: str
    code: int | None
    output: bytes
    answer: Any = None

    @property
    def text(self) -> str:
        return _text(self.output).strip()


def seen(done: Any, read: Callable[[bytes], Any] | None = None) -> Seen:
    """The one reader of a container call's result, which every consumer imports: the box's own steps, the runner's captures and
    the patch-size probe (``run.patch_bytes``), the cohort controls (``run._oracle_run``, ``cohorts``) and ``run.checked``.
    ``done`` is what docker returned (a return code and an output); ``read``, for a call whose answer is a line its command
    prints, takes that answer from the output, or gives None when it is not there.

    - No return code, or one below 0 (the client ended by a signal): UNKNOWN.
    - ``read`` given and its answer not in the output: UNKNOWN, whatever the code: the command was not seen to reach its answer.
    - Code 0: PASSED.
    - Any other code: FAILED when ``read`` found the answer, so the command itself ran and gave that code; else UNKNOWN, since a
      non-zero return from the call itself may be docker's, its daemon's or the container's, not the command's."""
    code = getattr(done, "returncode", None)
    output = getattr(done, "stdout", None) or b""
    output = output if isinstance(output, bytes) else str(output).encode("utf-8", "surrogateescape")
    if not isinstance(code, int) or isinstance(code, bool):
        return Seen(UNKNOWN, "no exit status came back", None, output)
    if code < 0:
        return Seen(UNKNOWN, f"the call was ended by signal {-code}", code, output)
    answer = read(output) if read is not None else None
    if read is not None and answer is None:
        return Seen(UNKNOWN, f"its answer's own line did not come back (exit {code})", code, output)
    if code == 0:
        return Seen(PASSED, "", code, output, answer)
    if read is not None:
        return Seen(FAILED, f"it ran and exited {code}", code, output, answer)
    said = (_text(output) + " " + _text(getattr(done, "stderr", None))).strip()[-600:]
    return Seen(UNKNOWN, f"the call itself returned {code}: {said}", code, output)


def _run_seen(body: Any) -> bool:
    """Whether a test run was observed: its call seen to pass, its exit and its report came back (``tests_seen``'s first rules)."""
    return (isinstance(body, Mapping) and body.get("patch_applied") is not False and body.get("call") == PASSED
            and body.get("pytest_exit") is not None and isinstance(body.get("results"), Mapping)
            and bool(body.get("report", bool(body.get("results")))))


def _collect_failed(test: str, body: Mapping[str, Any], gold: Mapping[str, Any] | None) -> bool:
    """Whether pytest's own report shows ``test`` failing at collection in ``body``'s run (coordinator's ruling, 2026-11-06): the
    run's report names a failed collection (``collect_errors``, node ids as pytest gives them) whose node id, with "::", begins
    the test's own node id, which only ``gold`` (the gold patch's run, on the same image ids, ``cohorts.control_why``) can give,
    as pytest's report of it (``nodes``), and that run, observed, saw the test pass. Anything else is no failure: a collection
    error in a module that holds no such test, a gold run that errors or holds no result for it, or a test whose module pytest's
    own report does not tie to the error."""
    errors = body.get("collect_errors")
    nodes = gold.get("nodes") if isinstance(gold, Mapping) else None
    if not (isinstance(errors, list) and isinstance(nodes, Mapping) and _run_seen(gold) and gold["results"].get(test) == "passed"):
        return False
    node = nodes.get(test)
    return isinstance(node, str) and any(isinstance(e, str) and e and node.startswith(e + "::") for e in errors)


def tests_seen(body: Mapping[str, Any] | None, tests: Iterable[str], *, seen_passing_in: Mapping[str, Any] | None = None
               ) -> dict[str, Any]:
    """The one reader of a test report (a ``run._oracle_run`` body: its pytest call's state, its exit, whether its report came
    back, its results), over ``tests``: PASSED when the run was observed and every one of them was seen to pass; FAILED when the
    run was observed and at least one was seen to fail (failed or error); else UNKNOWN, with why. The run is not observed when
    its patch did not apply, its call was not seen to pass (``seen``) or its call's own result was never recorded, no exit
    status came back, or no report came back (a timeout with no result among them); a test the report does not hold, or holds
    as skipped, is a missing result: never a pass and never a failure. ``seen_passing_in``: the base's reading only, the gold
    patch's run on the same image ids; a test the base's report does not hold is seen to fail when pytest's own report shows its
    module failing to collect there and that run saw it pass (``_collect_failed``). Returns the state, why, and how many tests
    were seen to pass, seen to fail, and not seen."""
    names = sorted(set(tests))
    body = body if isinstance(body, Mapping) else {}
    results = body.get("results")
    exit_ = body.get("pytest_exit")
    counts = {"tests": len(names), "passed": 0, "failed": 0, "unknown": len(names)}

    def unknown(why: str) -> dict[str, Any]:
        return {"state": UNKNOWN, "why": why, **counts}

    if not names:
        return unknown("there is no test to read")
    if body.get("patch_applied") is False:
        return unknown("the patch did not apply, so no test ran on it")
    if body.get("call") != PASSED:
        return unknown(f"the test call was not seen to run: {body.get('call_why') or body.get('call')}" if "call" in body else
                       "the test call's own result was never recorded")
    if exit_ is None:
        return unknown("no exit status came back: the tests were not seen to end")
    if not isinstance(results, Mapping) or not body.get("report", bool(results)):
        return unknown("the tests ran out of time (exit 124) with no report" if str(exit_).strip() == "124" else
                       f"no test report came back (exit {exit_})")
    passed = sum(results.get(t) == "passed" for t in names)
    failed = sum(results.get(t) in ("failed", "error") or (t not in results and seen_passing_in is not None
                                                            and _collect_failed(t, body, seen_passing_in)) for t in names)
    counts = {"tests": len(names), "passed": passed, "failed": failed, "unknown": len(names) - passed - failed}
    if passed == len(names):
        return {"state": PASSED, "why": "", **counts}
    if failed:
        return {"state": FAILED, "why": f"{failed} of {len(names)} seen to fail", **counts}
    return unknown(f"none of {len(names)} was seen to fail and {counts['unknown']} have no result in the report "
                   f"(exit {exit_})")


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
EXEC = "x"            # before an executable file's mark in a tree's map: git's mode 100755
#: git's three modes for what a tree holds but a folder: a file, an executable file and a symbolic link. Every crossing carries
#: each entry's mode (replay cohort, repair 1): the tar's member mode, the tree map's mark, the workspace's record.
FILE_MODE, EXEC_MODE, LINK_MODE = "100644", "100755", "120000"


def _mark(data: bytes | None, link: bool = False, executable: bool = False) -> str:
    """The one rule for what an entry is in a tree's map, on either side: ``FOLDER`` for a folder, a file's SHA-256 in hex, with
    ``EXEC`` before it for an executable file, and for a symbolic link ``LINK`` and its target's SHA-256, so a link, an executable
    and a file holding the same bytes differ, a change of mode alone is a change, and a link is compared by its target and kind,
    never by what it points to."""
    if data is None:
        return FOLDER
    return (LINK if link else EXEC if executable else "") + hashlib.sha256(data).hexdigest()


def _marked(data: bytes | None, mode: str | None) -> str:
    """``_mark`` of an entry by its git mode (None for a folder)."""
    return _mark(data, mode == LINK_MODE, mode == EXEC_MODE)


def _mode_of(mark: str | None) -> str | None:
    """The git mode a tree map's mark stands for: None for a folder or no entry."""
    if mark is None or mark == FOLDER:
        return None
    return LINK_MODE if mark.startswith(LINK) else EXEC_MODE if mark.startswith(EXEC) else FILE_MODE


def _member_mode(member: tarfile.TarInfo) -> str | None:
    """A tar member's git mode, as git reads a file's: a link 120000, a file whose owner may run it 100755, any other file
    100644; None for a folder."""
    if member.isdir():
        return None
    if member.issym():
        return LINK_MODE
    return EXEC_MODE if member.mode & stat.S_IXUSR else FILE_MODE


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
    changes the target, as it does in git. It lists too which are executable, as git's index keeps mode 100755 where the file
    system holds no executable bit (``core.fileMode=false``): writing such a file keeps its mode, as it does in git."""
    base = Path(workspace).resolve()
    return base.parent / f"{base.name}.box.links.json"


def _is_mark(mark: Any) -> bool:
    """A mark as ``_mark`` writes one, or None for a name that is not there."""
    return mark is None or mark == FOLDER or (isinstance(mark, str) and re.fullmatch(
        f"(?:{re.escape(LINK)}|{re.escape(EXEC)})?[0-9a-f]{{64}}", mark) is not None)


def _names(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def read_links(workspace: Path) -> dict[str, Any]:
    """The workspace's link record (``links_of``): ``links``, the names of its plain files that are links, ``executable``, the
    names of those whose mode is 100755, and ``pending``, by name, what a pull that may not have ended was changing a name's
    mode or link to (``{"from": mark, "to": mark}``). None there: no link and no executable. One that cannot be read as the box
    writes it refuses (``Refused``): which files are links or executables is then not known, and nothing may guess, since a link
    read as a file would reach the container as a file, and an executable as a file that cannot be run."""
    path = links_of(workspace)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"links": set(), "executable": set(), "pending": {}}
    except OSError as exc:
        raise Refused(f"the workspace's link record {path.name} could not be read: {exc}") from exc
    try:
        got = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refused(f"the workspace's link record {path.name} cannot be read: {exc}") from exc
    links, execs, pending = ((got.get("links"), got.get("executable"), got.get("pending")) if isinstance(got, dict)
                             else (None, None, None))
    if not (_names(links) and _names(execs) and isinstance(pending, dict) and not set(links) & set(execs)
            and all(isinstance(v, dict) and set(v) == {"from", "to"} and _is_mark(v["from"]) and _is_mark(v["to"])
                    for v in pending.values())):
        raise Refused(f"the workspace's link record {path.name} is not a list of links and of executables with the changes a "
                      "pull was making")
    return {"links": set(links), "executable": set(execs), "pending": pending}


def write_links(workspace: Path, links: Iterable[str], pending: Mapping[str, Mapping[str, str | None]] | None = None, *,
                executable: Iterable[str] = ()) -> None:
    """The workspace's link record, its links and its executables, written whole to a file of its own beside it, flushed to
    disk, then put in its place in one step (``os.replace``), so a reader finds it before or after the write, never part of it."""
    path = links_of(workspace)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with open(temp, "wb") as f:
        f.write(json.dumps({"links": sorted(links), "executable": sorted(executable), "pending": dict(pending or {})}, indent=1,
                           sort_keys=True).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def _mode(rel: str, data: bytes, record: Mapping[str, Any]) -> str:
    """The git mode of the workspace's plain file ``rel``, holding ``data``: by the record (``read_links``). A name a pull was
    changing (``pending``) is what the side its bytes are is: the side it was changing to, or else the side it was changing
    from, so a pull ended after it wrote a name and before it kept the record is read as it left the name. Otherwise, as git
    reads its index, a name listed as a link is 120000 and one listed as executable 100755, whatever its bytes now are; any
    other is 100644."""
    change = record["pending"].get(rel)
    if change is not None:
        for side in (change["to"], change["from"]):
            for mode in (FILE_MODE, EXEC_MODE, LINK_MODE):
                if side == _marked(data, mode):
                    return mode
    return LINK_MODE if rel in record["links"] else EXEC_MODE if rel in record["executable"] else FILE_MODE


def _tree(root: Path, repo: str) -> Iterator[tuple[Path, str, os.stat_result, bytes | None, str | None]]:
    """The workspace's entries in the order they are packed. First the root itself is checked (``_root``), so a walk, the re-walk
    among them, reads nothing through a root that became a link. Then each plain file and folder (``_plain``) but ``.git`` and
    ``.venv`` (``KEPT``), its name relative to the workspace with ``/``, checked before it is used to read anything as one the
    host holds as named and the container can hold (``_holdable``), a file's bytes, read as the file that was checked
    (``_read``), a folder's None, and the file's git mode by the workspace's link record (``_mode``): a symbolic link's target
    the container must be able to make (``_link_target``). A folder or a record that cannot be read refuses the workspace."""
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
                yield path, rel, st, None, None
                continue
            data = _read(path, st, rel)
            mode = _mode(rel, data, record)
            if mode == LINK_MODE:
                _link_target(rel, data)
            yield path, rel, st, data, mode


def pack(workspace: Path, repo: str = "", *, marks: dict[str, str] | None = None) -> bytes:
    """The workspace as a tar, in memory: its plain files and folders, without ``.git`` or ``.venv`` (``KEPT``), each named relative
    to it with ``/``, every name one the container can hold under ``repo`` (``_holdable``), each file with its git mode by its
    link record (``_mode``): one the record lists as a link as a symbolic link whose target is the file's bytes, never as a file,
    one it lists as executable with mode 0755, any other 0644. Anything else refuses the whole workspace (``Refused``), so
    nothing is sent; so does a workspace that is itself a link, a junction or another reparse point, and a folder that cannot be
    read, which would otherwise look empty and have its files removed in the container. Each file is read as the file that was
    checked (``_read``); once the walk is done every folder it went through, the workspace's own included, is checked again
    (``_same_folder``), and then the whole tree is walked again, its root checked first (``_tree``): its names and each one's
    mark (``_mark``: what it is, its mode, and a file's or a link's SHA-256) must be what was packed, else the workspace changed
    while it was packed and is refused, so the next call sends it as it is then. Any failure to pack it (a name the tar cannot
    hold, a read that fails, the tar's own error) refuses it too: nothing is sent or run. ``marks``: filled with the tree that
    was packed, as a map (``tree_map``'s form), from the one read that built the tar."""
    given = Path(workspace)
    top = _root(given)
    try:
        root = given.resolve()
        out = io.BytesIO()
        walked = [(given, top, "the workspace")]
        packed: list[tuple[str, str]] = []
        with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path, rel, st, data, mode in _tree(root, repo):
                info = tarfile.TarInfo(rel)
                if data is None:
                    walked.append((path, st, rel))
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                elif mode == LINK_MODE:
                    info.type, info.linkname, info.mode = tarfile.SYMTYPE, data.decode("utf-8"), 0o777
                    tar.addfile(info)
                else:
                    info.size, info.mode = len(data), 0o755 if mode == EXEC_MODE else 0o644
                    tar.addfile(info, io.BytesIO(data))
                packed.append((rel, _marked(data, mode)))
        for path, st, rel in walked:
            _same_folder(path, st, rel)
        now = [(rel, _marked(data, mode)) for _, rel, _, data, mode in _tree(root, repo)]
        if now != packed:
            at = next((i for i, (was, is_) in enumerate(zip(packed, now)) if was != is_), min(len(packed), len(now)))
            raise Refused(f"{(packed if at < len(packed) else now)[at][0]} changed while the workspace was packed: walked again, "
                          "the tree is not what was packed, so nothing is sent")
    except Refused:
        raise
    except (OSError, ValueError, tarfile.TarError) as exc:       # UnicodeError is a ValueError
        raise Refused(f"the workspace could not be packed: {type(exc).__name__}: {exc}") from exc
    if marks is not None:
        marks.clear()
        marks.update(packed)
    return out.getvalue()


def mirror(workspace: Path, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float) -> dict[str, str]:
    """The workspace into the container's repository (``MIRROR``, as root). ``docker`` is called as ``run.Docker`` is. A refused
    workspace, a name the container cannot hold under ``repo`` among them, sends nothing and runs nothing (``Refused``); a docker
    step not seen to pass (``seen``) raises ``BoxError``. Returns the tree it sent, as a map (``tree_map``'s form), from the one
    read of the workspace that built the tar (``pack``): the baseline of a call's three-way pull, never a second read."""
    sent: dict[str, str] = {}
    data = pack(workspace, repo, marks=sent)
    got = seen(docker("exec", "-i", "-u", "root", container, "python3", "-I", "-c", MIRROR, repo, user, input=data,
                      timeout=timeout))
    if got.state != PASSED:
        raise BoxError(f"the mirror failed ({got.why})")
    return sent


def tree_map(workspace: Path, repo: str = "") -> dict[str, str]:
    """The workspace's tree as a map: each entry's name relative to it with ``/``, to its mark (``_mark``): a file's SHA-256 in hex,
    with ``EXEC`` before it for an executable, ``LINK`` and a link's target's, or ``FOLDER``. It is read as ``pack`` reads it
    (``_tree``): the root and every entry checked, and anything that could not be packed refuses it."""
    given = Path(workspace)
    _root(given)
    try:
        return {rel: _marked(data, mode) for _, rel, _, data, mode in _tree(given.resolve(), repo)}
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


def _put_new(temp: Path, path: Path) -> None:
    """``temp``, a whole file, given the name ``path``, which must not be there, in one step that never replaces what holds it: on
    Windows a rename, which refuses a name that is there (another file's short-name alias or a name in another case among them);
    elsewhere a second name made for the file, refused the same way, then the first removed."""
    if os.name == "nt":
        os.rename(temp, path)
    else:
        os.link(temp, path)
        os.remove(temp)


def _write_new(path: Path, data: bytes, *, folder: Path, over: bool = False) -> None:
    """A file put in place with ``data``, whole, never written through a name that is there: it is made whole, exclusively, in
    ``folder``, the box's own beside the workspace (``spare``), then put at its name in one step, so a write that fails part way,
    or a box that ends, leaves at that name what was there before: the old file, or nothing. ``over``: it replaces the file at
    that name, and is moved over it (``os.replace``); else the name must not be there (``_put_new``), so a name that is another
    file's short-name alias meets that file and fails."""
    folder.mkdir(exist_ok=True)
    temp = folder / f"{secrets.token_hex(8)}.part"
    try:
        with open(temp, "xb") as f:
            f.write(data)
        if over:
            os.replace(temp, path)
        else:
            _put_new(temp, path)
    finally:
        if os.path.lexists(temp):
            os.remove(temp)


def bring_in(workspace: Path, data: bytes, repo: str = "", before: Mapping[str, str] | None = None) -> list[str]:
    """The container's tree (``data``, an ``EXPORT`` tar) made the workspace's, and the names it changed there, sorted. Every member
    is checked first (``export_members``) and the workspace read as ``pack`` reads it, so a tree that cannot be held writes
    nothing. ``before``: the tree the container was given before the command (``tree_map``, as the call's state keeps it). Then
    each name takes the container's side where the workspace still holds what the container was given, and keeps the workspace's
    where the workspace changed since, so nothing written in the workspace after the mirror is lost; with no ``before`` (an empty
    workspace, ``run.workspace_from``) the container's tree is taken whole. Each entry is compared by its mark (``_mark``), its
    git mode with it, so a change of mode alone is a change. First what a box that ended part way through an earlier pull left
    in its folder beside the workspace (``spare``) is removed. Removals go first, deepest first, then folders, shallowest first,
    then files in the export's own order, each made whole beside the workspace and put at its name in one step (``_write_new``):
    a new file at a name that must not be there, so a name that is another file's short-name alias meets that file and refuses
    the tree, and a changed file moved over the old one, so a write that fails leaves it as it was. A symbolic link is written
    as git writes one where links cannot be made, a plain file holding its target, and an executable as git keeps one where the
    file system holds no executable bit, a plain file; each is listed in the workspace's link record (``write_links``): before
    anything is written, every name whose mode the pull changes is kept there as pending, so a pull ended part way is read as
    it left each name (``_mode``); once the tree is checked the record is set to the tree's links and executables. Last, the
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
    marks = {rel: _marked(body, _member_mode(members[rel])) for rel, body in theirs.items()}
    room = spare(base)
    try:
        for left in (room.iterdir() if room.is_dir() else ()):     # an earlier pull's file never put at its name: the box's own
            os.remove(left)
    except OSError as exc:
        raise Refused(f"the box's own folder beside the workspace could not be emptied: {exc}") from exc
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

    def listed(tree: Mapping[str, str], mode: str) -> set[str]:
        return {rel for rel, mark in tree.items() if _mode_of(mark) == mode}

    def plain(mark: str | None) -> bool:
        return _mode_of(mark) in (None, FILE_MODE)

    pending = {rel: {"from": now.get(rel), "to": target.get(rel)} for rel in sorted({*now, *target})
               if now.get(rel) != target.get(rel) and not (plain(now.get(rel)) and plain(target.get(rel)))}
    changed: list[str] = []                     # each name is listed before it is touched, so a failure after it says "in part"

    def failed(why: str) -> Refused:
        if pending:                             # the record set to what the workspace holds now; else its pending stays, read as is
            try:
                held = tree_map(root, repo)
                write_links(base, listed(held, LINK_MODE), executable=listed(held, EXEC_MODE))
            except (Refused, OSError, ValueError):
                pass
        return (Partial if changed else Refused)(why)

    try:
        if pending:                             # before anything is written: which names the pull changes a mode of, and to what
            write_links(base, listed(now, LINK_MODE), pending, executable=listed(now, EXEC_MODE))
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
            elif now.get(rel) not in (None, FOLDER) and target[rel].lstrip(LINK + EXEC) == now[rel].lstrip(LINK + EXEC):
                continue                        # the same bytes in another mode: the record carries the mode
            else:                               # made whole beside the workspace, then put at its name
                _write_new(path, theirs[rel], folder=room, over=now.get(rel) not in (None, FOLDER))
        written(base, [m for rel, m in members.items() if target.get(rel) == marks[rel]])
        after = tree_map(root, repo)
    except Refused as exc:
        raise failed(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise failed(f"the container's tree could not be written into the workspace: {type(exc).__name__}: {exc}") from exc
    if after != target:
        wrong = sorted(set(after.items()) ^ set(target.items()))[:5]
        raise failed(f"the workspace is not the tree decided after the container's tree was brought in: {wrong}")
    linked, execs = listed(target, LINK_MODE), listed(target, EXEC_MODE)
    if pending or record["pending"] or linked != record["links"] or execs != record["executable"]:
        try:
            write_links(base, linked, executable=execs)
        except OSError as exc:
            raise failed(f"the workspace's link record could not be kept: {type(exc).__name__}: {exc}") from exc
    return sorted(set(changed))


def pull(workspace: Path, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float,
         state: Path | None = None, call_no: int = 0, before: Mapping[str, str] | None = None) -> list[str]:
    """What a command changed in files, brought from the container into the workspace: ``PULL`` (as root: the user's processes
    stopped, then the export), then ``bring_in``, three-way from ``before``. With ``state``, the call's phase "pulling" is kept
    there (``write_state``) before anything is written, so a box ended in the middle of it is found and settled (``settle``). A
    docker step not seen to pass (``seen``) raises ``BoxError``; a tree that cannot be held, ``Refused``. Returns the names it
    changed."""
    got = seen(docker("exec", "-u", "root", container, "python3", "-I", "-c", PULL, repo, user, timeout=timeout))
    if got.state != PASSED:
        raise BoxError(f"the pull failed ({got.why[:2000]})")
    if state is not None:
        write_state(state, {"call": call_no, "phase": "pulling"})
    return bring_in(workspace, got.output, repo, before)


def set_back(workspace: Path, docker: Callable[..., Any], **where: Any) -> str:
    """The container set to the workspace again (``mirror``) after a pull that could not be made, so the two hold one tree: "" when
    it was, else why not."""
    try:
        mirror(workspace, docker, **where)
    except (Refused, BoxError, OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"[:2000]
    return ""


#: Every phase a call of the box is kept in (``write_state``), a closed set: "mirrored" (the container given the workspace, the
#: tree it was given kept) and "pulling" (the command's tree being brought back) are unfinished, and a box that ended in either is
#: settled before anything is mirrored again (``settle``); "settled", "set back" and "not settled" are finished. Any other phase
#: refuses (``read_state``, ``write_state``): what that call left in the container is not known.
UNFINISHED = ("mirrored", "pulling")
PHASES = (*UNFINISHED, "settled", "set back", "not settled")


def read_state(path: Path) -> dict[str, Any] | None:
    """The box's state file (``write_state``), or None when there is none. One that cannot be read as the box writes it refuses
    (``Refused``), a phase outside ``PHASES`` and an unfinished call with no tree it was mirrored from among them: what a call
    left in the container is then not known, and nothing may guess it."""
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
    for x in calls:
        if x["phase"] not in PHASES:
            raise Refused(f"the box's state {path} gives call {x['call']} the phase {x['phase']!r}, which is none of the box's "
                          f"{', '.join(PHASES)}: what it left in the container is not known")
        before = x.get("before")
        if x["phase"] in UNFINISHED and not (isinstance(before, dict) and all(isinstance(k, str) and _is_mark(v) and v is not None
                                                                              for k, v in before.items())):
            raise Refused(f"the box's state {path} gives call {x['call']} no tree it was mirrored from")
    return got


def write_state(path: Path, entry: Mapping[str, Any]) -> None:
    """One call's phase kept in the box's state file, a file outside the workspace: merged into that call's entry, or a new entry
    for a new call. Written whole to a file of its own beside it, flushed to disk, then put in its place in one step
    (``os.replace``), so a reader finds the state before or after the write, never part of it. A finished call keeps no map. A
    phase outside ``PHASES`` is never written (``ValueError``)."""
    if entry.get("phase") not in PHASES:
        raise ValueError(f"no phase {entry.get('phase')!r} of the box's: {', '.join(PHASES)}")
    state = read_state(path) or {"calls": []}
    calls = state["calls"]
    if calls and calls[-1]["call"] == entry["call"]:
        calls[-1] = {**calls[-1], **entry}
    else:
        calls.append(dict(entry))
    if calls[-1]["phase"] not in UNFINISHED:
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
    """A call the box did not finish, finished before the workspace is mirrored again: by the next call of the box before it
    mirrors anything (``call``), and by the runner once the session has ended (``run._run_task``'s ``turn``). None when the last
    call in the box's state is finished, or there is no state. A call left unfinished (``UNFINISHED``: its command may have
    changed the container, and its pull may have written part of the workspace) is pulled now, three-way from the tree the call
    mirrored, so what the command changed is kept and what the workspace changed after it is not written over; a tree that cannot
    be held is "not settled", with why, and the mirror after this sets the container to the workspace. A state that cannot be
    read, a phase outside ``PHASES`` among it, refuses (``read_state``); so does a docker step not seen to pass (``BoxError``):
    the call stays unfinished, and nothing may be mirrored over it."""
    calls = (read_state(state) or {"calls": []})["calls"]
    last = calls[-1] if calls else None
    if last is None or last["phase"] not in UNFINISHED:
        return None
    n, before = last["call"], last["before"]
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
#: Said before a command's output when the box first finished its last call, which had ended before its pull (``settle``).
SETTLED = ("the box's last call (call {n}) had ended before what its command changed in files came back; it came back now, "
           "before this command: {count} names changed in your working folder.")
UNSETTLED = ("the box's last call (call {n}) had ended before what its command changed in files came back, and it could not be "
             "brought back now ({why}), so none of it is kept. Make that change with your own editing tools.")
#: A pull that failed after it began to write (``Partial``): part of the command's change is in the working folder.
PART = ("what the command changed in files was brought back into your working folder only in part ({why}): {after}, as your "
        "working folder now is. Read the files the command was to change before you go on.")


def run_command(command: str, docker: Callable[..., Any], *, container: str, repo: str, user: str, timeout: float) -> tuple[int, str]:
    """The command in the container, as ``user`` in ``repo``, stopped there (with its children) at ``timeout`` seconds, behind a
    start line (``START``) with a nonce chosen for this call, which the container's shell prints before the command runs: its exit
    code, and the last ``MAX_OUTPUT`` characters of its combined output, the start line taken out, after a line giving the code.
    The reply is read by the one reader (``seen``), the start line, as a line of its own, being the command's answer: a reply
    without it, or with no exit code, raises ``BoxError`` whatever its exit code: the command never ran, or what it did is not
    known, and what came back is docker's, its daemon's or the container's."""
    seconds = str(max(1, int(timeout)))
    start = f"{START} {secrets.token_hex(16)}"

    def ran(output: bytes) -> str | None:        # the output without the start line, when the command's own shell printed it
        before, line, after = _text(output).partition(start + "\n")
        return None if not line or (before and not before.endswith("\n")) else before + after

    got = seen(docker("exec", "-u", user, "-w", repo, container, "timeout", "-k", "10", seconds, "sh", "-c", SHELL, start, command,
                      timeout=timeout + 60, merged=True), ran)
    if got.state == UNKNOWN and (got.code is None or got.code < 0):
        raise BoxError(f"the command's exit is not known: {got.why}: {got.text[-2000:]}")
    if got.state == UNKNOWN:
        raise BoxError(f"the command never ran: its start line did not come back (exit {got.code}): {got.text[-2000:]}")
    note = f" (124 is what the box gives a command it stopped at {seconds} seconds)" if got.code == 124 else ""
    return got.code, f"exit code {got.code}{note}\n{got.answer[-MAX_OUTPUT:]}"


def _result(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def call(opts: Any, params: dict[str, Any], docker: Callable[..., Any]) -> dict[str, Any]:
    """One ``tools/call``: the mirror, the command, then the pull, so the call ends with one tree in the workspace and the
    container. A docker failure is a tool error: a mirror that failed, and a reply without the call's start line, whatever its exit
    code (``run_command``); so is a docker client ended by a signal (a return code below 0, or none), which gives no exit of the
    command's. Once the mirror is made, the pull is made whatever the command returned: a command that ran in part changed files
    too. A pull that cannot be made is a tool error after the command's output, which says so, and the container is set back to
    the workspace (``set_back``), so no change is dropped without the agent being told. With ``opts.state`` each phase is kept
    (``write_state``): "mirrored" with the tree the container was given, "pulling", then "settled", "set back" or "not settled".
    One snapshot of a call, settled first: before anything is mirrored, the previous call's state is read and a call it left
    unfinished is settled (``settle``), so a box ended after a command and started again never mirrors over what the command
    changed; and the tree the three-way pull starts from is the one the mirror sent, from the one read of the workspace that
    built it (``mirror``), never a second read, which an edit made after the mirror would be in."""
    arguments = params.get("arguments") or {}
    if params.get("name") != "run":
        return _result(f"there is no tool {params.get('name')!r}; the one tool is run", error=True)
    command = arguments.get("command") if isinstance(arguments, dict) else None
    if not isinstance(command, str) or not command.strip():
        return _result("run needs a command: a string that is not empty", error=True)
    where = {"container": opts.container, "repo": opts.repo, "user": opts.user, "timeout": float(opts.timeout)}
    workspace, state = Path(opts.workspace), getattr(opts, "state", None)
    state = Path(state) if state else None
    note = ""
    if state is not None:
        try:
            last = settle(workspace, docker, state=state, **where)
        except (Refused, BoxError, OSError, subprocess.SubprocessError) as exc:
            return _result(f"nothing was mirrored or run: the box's last call ended before it was finished, and it could not be "
                           f"finished now: {type(exc).__name__}: {exc}", error=True)
        if last is not None:
            note = (SETTLED if last["phase"] == "settled" else UNSETTLED).format(
                n=last["call"], count=last.get("count", 0), why=last.get("error", "")) + "\n\n"
    try:
        before = mirror(workspace, docker, **where)
    except Refused as exc:
        return _result(f"{note}nothing was mirrored or run: {exc}", error=True)
    except (BoxError, OSError, subprocess.SubprocessError) as exc:
        return _result(f"{note}{type(exc).__name__}: {exc}", error=True)
    try:
        calls = (read_state(state) or {"calls": []})["calls"] if state is not None else []
        n = (calls[-1]["call"] + 1) if calls else 1
        if state is not None:
            write_state(state, {"call": n, "phase": "mirrored", "by": "box", "before": before})
    except (Refused, OSError, ValueError) as exc:
        return _result(f"{note}the workspace was mirrored and no command was run: the box could not keep its state: {exc}",
                       error=True)
    try:
        code, text = run_command(command, docker, **where)
        text, failed = note + text, False
    except (BoxError, OSError, subprocess.SubprocessError) as exc:
        text, failed = f"{note}{type(exc).__name__}: {exc}", True
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
