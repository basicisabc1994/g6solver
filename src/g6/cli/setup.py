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
        code = register_clients(args, clients, say)
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


def register_clients(args, clients: list[str], say: Say) -> int:
    """Explicit registration via the clients' CLIs; never parses or overwrites their config files."""
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
        say(f"Registered G6 for {client} with this installation's Python, started with -P so that the packages in "
            "the project's folder never replace G6's own. Restart the client to load it.")
    say("Local settings assume a trusted host. An agent with your account's unrestricted shell can change them.")
    if "patch_review.review" in {item.strip() for item in allowed.split(",")}:
        say(FIRST_REVIEW)
    else:
        say(NOT_ALLOWED.format(remove=" or ".join(REMOVE[client] for client in clients)))
    return 0


__all__ = ["PROMPT", "QUESTION", "STEPS", "add_arguments", "isolation", "main"]
