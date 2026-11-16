# SPDX-License-Identifier: Apache-2.0
"""``g6 setup``: the person's choices for this computer, asked once and kept in ``$G6_HOME/choices.json``
(``g6.core.choices``; DECISIONS #40, ruling 10). The person runs it in a terminal; an agent never should, and G6
cannot tell who typed it, so the guide tells agents to leave it to the person.

Its steps run in order (:data:`STEPS`), each asking one thing. For now there is one: where no operating-system
boundary can confine the code G6 runs (a patch, a generated script, a repository's tests), whether such code may run
without one, with the person's own rights. With a boundary there is nothing to ask. ``--allow-unconfined`` and
``--refuse-unconfined`` answer it without asking, for scripts and CI. Later steps will ask for the model, the groups of
sensitive operations and the agents to register G6 with.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

QUESTION = """G6 found no operating-system boundary on this computer to keep the code it runs for you (a patch, a
generated script, your repository's tests) away from your files:
  {why}

Without a boundary, G6 does not run such code. If you allow it, the code runs with your own rights: it can read your
files, your key file ~/.g6/.env among them, and the programs it starts can reach the network. g6 doctor says what this
computer needs for a boundary.
"""
PROMPT = "Allow G6 to run such code without a boundary on this computer? [y/N] "
#: How a client starts G6's server, after this installation's Python: with -P. The client starts it in the project's
#: folder, and a plain ``-c`` puts that folder first on the import path, so a project's own ``pydantic`` or
#: ``starlette`` would replace G6's and run in G6's process (worked examples, W1). -P keeps the folder off; -I would
#: also drop PYTHONPATH and the user's site-packages, which the person sets, not the project.
SERVER_ARGS = ("-P", "-c", "from g6.mcp import main; main()")
#: How each client removes its existing g6 entry, so setup can register this installation in its place.
REMOVE = {"claude": "claude mcp remove --scope user g6", "codex": "codex mcp remove g6"}
#: Setup's closing lines when patch review is allowed: the first review call.
FIRST_REVIEW = """
The first review. In a Python repository, ask your coding agent for a change, then ask it to review the change with
G6 before it says the task is done. Its call looks like this:
  g6_review_patch(repo="<your repository's folder>", task="<what you asked for>",
                  requirements=[{"id": "R1", "text": "<one thing the change must do>",
                                 "checks": ["tests/test_example.py::test_it"]}])
It returns a job: the agent polls metaharness_job until the review ends, then shows you assessment.report as it is.
Merging stays your decision."""
#: Setup's closing lines when patch review was left out of --allow.
NOT_ALLOWED = """
Patch review is not allowed in this registration, so g6_review_patch will refuse. To allow it, remove the entry
('{remove}') and run g6 setup again with --allow patch_review.review."""

Ask = Callable[[str], str]
Say = Callable[[str], None]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """``g6 setup``'s options."""
    answer = parser.add_mutually_exclusive_group()
    answer.add_argument("--allow-unconfined", action="store_true",
                        help="allow untrusted code to run where no operating-system boundary can confine it, with "
                             "your own rights (no question asked)")
    answer.add_argument("--refuse-unconfined", action="store_true",
                        help="refuse untrusted code where no operating-system boundary can confine it (no question "
                             "asked)")
    parser.add_argument("--client", choices=("claude", "codex"), action="append", default=[],
                        help="register G6 and install its skill for this client (repeat for both)")
    parser.add_argument("--allow", default="", help="comma-separated sensitive components or operations for this registration")
    parser.add_argument("--model", choices=("claude_code", "codex"),
                        help="host model provider for registered servers; uses the client's existing sign-in")


def isolation(args: argparse.Namespace, ask: Ask, say: Say) -> int:
    """Whether untrusted code may run without an operating-system boundary here: asked only where none is ready."""
    from g6.core import choices, confine

    given = choices.ALLOW if args.allow_unconfined else choices.REFUSE if args.refuse_unconfined else None
    pythons = list(dict.fromkeys([sys.executable, *([os.environ["G6_PYTHON"]] if os.environ.get("G6_PYTHON") else [])]))
    missing = [(python, why) for python, (name, why) in ((p, confine.backend(p)) for p in pythons) if name is None]
    if given is None and not missing:
        say("Isolation: an operating-system boundary confines the code G6 runs here, so there is nothing to choose.")
        return 0
    if given is None:
        say(QUESTION.format(why=missing[0][1]))
        try:
            reply = ask(PROMPT)
        except EOFError:
            say("No answer was given, so nothing was recorded. Run g6 setup in a terminal, or answer with "
                "g6 setup --allow-unconfined or g6 setup --refuse-unconfined.")
            return 2
        given = choices.ALLOW if reply.strip().lower() in ("y", "yes") else choices.REFUSE
    entry = choices.record(choices.UNCONFINED, given, choices.SETUP)
    allowed = given == choices.ALLOW
    say(f"Recorded in {choices.path()} ({entry['recorded_at']}): code G6 runs for you "
        + ("may run without an operating-system boundary, with your own rights, where none can confine it."
           if allowed else "runs only inside an operating-system boundary; where none can confine it, G6 refuses it."))
    if allowed and missing:
        say(confine.UNCONFINED_WARNING)
    if os.environ.get(confine.REQUIRE, "").strip().lower() in {"1", "true", "yes", "on"}:
        say(f"{confine.REQUIRE} is set where you ran this, so G6 refuses such code there whatever is recorded.")
    return 0


#: The questions ``g6 setup`` asks, in order; each returns 0 to go on, or the command's exit code.
STEPS: tuple[Callable[[argparse.Namespace, Ask, Say], int], ...] = (isolation,)


def main(args: argparse.Namespace, *, ask: Ask = input, say: Say = print) -> int:
    for step in STEPS:
        code = step(args, ask, say)
        if code:
            return code
    from g6.core.storage import g6_home
    g6_home().mkdir(parents=True, exist_ok=True)
    if getattr(args, "model", None):
        try:
            configure_model(args.model)
        except (OSError, ValueError):
            say("Model preference could not be saved; no model call was made.")
            return 2
    clients = list(dict.fromkeys(getattr(args, "client", [])))
    if clients:
        code = register_clients(args, clients, say, ask)
        if code:
            return code
    return 0


def configure_model(provider: str) -> None:
    """Save an explicitly requested host provider, preserving other settings without displaying them."""
    import re
    import tempfile
    from pathlib import Path
    from g6.core import keys

    if provider not in ("claude_code", "codex"):
        raise ValueError("unsupported host provider")
    target = keys.path()
    if target.is_symlink():
        raise ValueError("refusing a symlinked settings file")
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = target.read_text(encoding="utf-8") if target.exists() else ""
    lines = [line for line in previous.splitlines() if not re.match(r"\s*(?:export\s+)?G6_LLM_PROVIDER\s*=", line)]
    content = "\n".join([*lines, f"G6_LLM_PROVIDER={provider}", ""])
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=".g6-settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        Path(name).chmod(0o600)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


class _LeftAlone(Exception):
    """Why setup leaves the person's ``AGENTS.md`` as it is."""


def _read(target) -> tuple[int, int, bytes] | None:
    """The file as G6 may write over it: its identity (device and file number) and bytes; ``None`` when there is no
    file. ``_LeftAlone`` for a file G6 does not change: a link, junction or other reparse point, not a plain file,
    hard-linked to another name, read-only (no write access, no owner write bit, or Windows' read-only attribute),
    unreadable, or not UTF-8 text without NUL. Setup runs this same check at its first read and again before it
    writes, so the two cannot drift: the file it replaces is one it would have agreed to change at the start."""
    import stat

    try:
        status = os.stat(target, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _LeftAlone(f"it could not be read ({type(exc).__name__})") from None
    attributes = getattr(status, "st_file_attributes", 0)
    if stat.S_ISLNK(status.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise _LeftAlone("it is a link to another file")
    if not stat.S_ISREG(status.st_mode):
        raise _LeftAlone("it is not a plain file")
    if status.st_nlink > 1:
        raise _LeftAlone("it is hard-linked to another name")
    if (not os.access(target, os.W_OK) or not status.st_mode & stat.S_IWUSR
            or attributes & stat.FILE_ATTRIBUTE_READONLY):
        raise _LeftAlone("it is read-only")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise _LeftAlone(f"it could not be read ({type(exc).__name__})") from None
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = data[len(bom):].decode("utf-8")
    except UnicodeDecodeError:
        raise _LeftAlone("it is not UTF-8 text") from None
    if "\x00" in text:
        raise _LeftAlone("it is not UTF-8 text")
    return status.st_dev, status.st_ino, data


def _agents_update(data: bytes | None, block: str) -> bytes:
    """The file's bytes (``None`` when there is no file) with G6's block in place: the block written in the file's own
    newline style, as exactly one ``AGENTS_BEGIN ... AGENTS_END`` pair replaced, or appended where there is none;
    every other byte (newlines, a byte-order mark, trailing blank lines) as it was. ``_LeftAlone`` for a file G6
    cannot change that way. A marker counts only as a whole line of its own."""
    import re
    from g6.mcp.instructions import AGENTS_BEGIN, AGENTS_END

    if data is None:
        return block.encode("utf-8") + b"\n"
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    first = data.find(b"\n")
    newline = b"\r\n" if first > 0 and data[first - 1:first] == b"\r" else b"\n"
    written = block.encode("utf-8").replace(b"\n", newline)
    # a marker counts only as a whole line of its own (spaces or tabs around it allowed): one quoted inside a line is
    # the person's text, not G6's block
    begins, ends = ([*re.finditer(rb"(?m)^(?:\xef\xbb\xbf)?[ \t]*" + re.escape(marker.encode()) + rb"[ \t]*(?=\r?$)",
                                  data)] for marker in (AGENTS_BEGIN, AGENTS_END))
    if not begins and not ends:
        body = data[len(bom):]
        gap = b"" if not body or body.endswith(newline * 2) else newline if body.endswith(newline) else newline * 2
        return data + gap + written + newline
    if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].start():
        raise _LeftAlone(f"its g6:begin and g6:end lines do not make exactly one block ({len(begins)} begin, "
                         f"{len(ends)} end)")
    start = begins[0].start() + (len(bom) if begins[0].start() == 0 else 0)
    return data[:start] + written + data[ends[0].end():]


def _remove(name: str, say: Say) -> None:
    """Remove G6's temporary file, never raising: made writable first (a copied read-only mode would block the removal
    on Windows), one attempt, and named to the person when it stays."""
    import stat

    try:
        os.chmod(name, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass
    try:
        os.unlink(name)
    except FileNotFoundError:
        pass
    except OSError as exc:
        say(f"G6 could not remove its temporary file {name} ({type(exc).__name__}); you can delete it.")


def _terminal() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def codex_agents(ask: Ask, say: Say) -> None:
    """Offer G6's instructions to Codex as a delimited block in the person's own ``AGENTS.md`` (in ``CODEX_HOME``,
    else ``~/.codex``): shown first, written only on a yes, and only the block's bytes change (``_agents_update``).
    A file G6 cannot change that way (a lone begin or end line, two blocks, not UTF-8, not a plain file, a link,
    read-only or unreadable), setup's own question with no terminal to answer it, anything but a yes, or a write that
    fails: the file stays byte for byte as it was, no temporary file is left, and the person is told why and shown
    the block to add by hand. Setup goes on either way.

    The file may change while setup waits at its question (the person saves it in an editor, makes it read-only or
    links it, or another program does). So after the yes, and once more just before the replace, G6 runs the whole
    check of its first read again (``_read``: kind, links, link count, write access, text) and compares identity
    (device and file number) and bytes with what it read before asking; any difference leaves the file as it now is
    and the person is shown the block to add by hand. The temporary file takes the file's mode only after the first of
    those checks passes, and is always removed (made writable first) or named to the person. What remains: a change
    that lands between that last check and the rename is lost or overwritten, a window of one rename, since no lock is
    taken on the person's file. The file's mode is kept; Windows attributes (hidden, say) and ACLs are not, as the new
    file replaces the old one."""
    import tempfile
    from pathlib import Path
    from g6.mcp.instructions import agents_block

    target = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "AGENTS.md"
    block = agents_block()

    def by_hand(why: str) -> None:
        say(f"G6 left {target} as it was: {why}. To give Codex G6's instructions, add this block to it yourself:"
            f"\n\n{block}\n")

    changed = "it changed while setup waited, and G6 writes only over the file it showed you the change for"
    try:
        seen = _read(target)
        updated = _agents_update(seen[2] if seen else None, block)
    except _LeftAlone as why:
        by_hand(str(why))
        return
    if seen is not None and updated == seen[2]:
        return
    if ask is input and not _terminal():
        by_hand("no terminal is attached to answer whether to write it, so nothing was asked or written")
        return
    say(f"G6 can tell Codex when to call it with this block in {target}; the rest of the file stays as it is:\n\n"
        f"{block}\n")
    try:
        reply = ask(f"Write this block to {target}? [y/N] ")
    except (EOFError, OSError):
        reply = ""
    if reply.strip().lower() not in ("y", "yes"):
        say(f"Nothing was written to {target}.")
        return
    name = None
    try:
        if _read(target) != seen:
            by_hand(changed)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=".g6-agents-")
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
        if seen is not None:
            import shutil
            shutil.copymode(target, name)
        if _read(target) != seen:           # the last check, just before the replace
            by_hand(changed)
            return
        os.replace(name, target)
        name = None
    except _LeftAlone as why:
        by_hand(f"{why} now, which it was not when setup read it")
        return
    except OSError as exc:
        by_hand(f"writing it failed ({type(exc).__name__})")
        return
    finally:
        if name is not None:
            _remove(name, say)
    say(f"Wrote G6's block to {target}.")


def register_clients(args, clients: list[str], say: Say, ask: Ask = input) -> int:
    """Explicit registration via the clients' CLIs; never parses or overwrites their config files. For Codex it also
    offers G6's block for the person's ``AGENTS.md`` (``codex_agents``), written only on their yes."""
    import re
    import subprocess
    from g6.cli import main as cli
    from g6.core.outside import find_program

    allowed = args.allow.strip()
    if allowed and any(not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?", item.strip())
                       for item in allowed.split(",")):
        say("--allow requires explicit component or component.operation names; wildcards are not supported.")
        return 2
    from pathlib import Path
    from g6.core import skills

    for client in clients:
        binary = find_program(client)
        if binary is None:
            say(f"{client} is not on PATH; install its CLI, then run g6 setup --client {client}.")
            return 2
        command = [binary, "mcp", "add", "g6"]
        if client == "claude":
            command += ["--scope", "user"]
        flag = "-e" if client == "claude" else "--env"
        if allowed:
            command += [flag, f"G6_MCP_SENSITIVE={allowed}"]
        if args.model:
            command += [flag, f"G6_LLM_PROVIDER={args.model}"]
        command += ["--", sys.executable, *SERVER_ARGS]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            say(f"{client} registration did not finish: {type(exc).__name__}")
            return 2
        if result.returncode:
            say(f"{client} refused registration. Inspect its existing g6 entry with '{client} mcp get g6'; to "
                f"replace it, remove it with '{REMOVE[client]}' and run g6 setup again.")
            return 2
        # setup is the person's install step, so it brings this version's skill: an earlier install's copy left in
        # place keeps its old instructions (the owner's pilot, 2026-10-30)
        earlier = (Path.home() / f".{client}" / "skills" / skills.GUIDE).exists()
        cli(["skills", "install", "--user", "--client", client, "--force"])
        if earlier:
            say(f"Replaced the earlier copy of the {skills.GUIDE} skill for {client} with this version's.")
        if client == "codex":
            codex_agents(ask, say)
        say(f"Registered G6 for {client} with this installation's Python, started with -P so that the packages in "
            "the project's folder never replace G6's own. Restart the client to load it.")
    say("Local settings assume a trusted host. An agent with your account's unrestricted shell can change them.")
    if "patch_review.review" in {item.strip() for item in allowed.split(",")}:
        say(FIRST_REVIEW)
    else:
        say(NOT_ALLOWED.format(remove=" or ".join(REMOVE[client] for client in clients)))
    return 0


__all__ = ["PROMPT", "QUESTION", "STEPS", "add_arguments", "isolation", "main"]
