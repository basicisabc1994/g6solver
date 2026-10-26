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
    return 0


__all__ = ["PROMPT", "QUESTION", "STEPS", "add_arguments", "isolation", "main"]
