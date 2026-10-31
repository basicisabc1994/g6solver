# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The owner's decisions, at their own terminal (workforce OS plan, V4c: the deferrals package's D3 default).

``python -P -m g6.workforce.decide list`` shows what waits for a person (a gate, or verifiers that disagree);
``approve | reject | uphold | set-aside <mission> <unit> --reason "..."`` answers one; ``confirm <mission>`` confirms
what counts as done; ``allow <repository>`` allows G6 to run a repository's code at its present revision before a
coding mission is created on it (audit 010 WF-007). An answer is taken only when the unit's key (the mission id, the
repository name) is typed back at an interactive terminal; piped input is refused and nothing is written. That is a
convenience check, NOT proof that a person typed it: a process with the owner's rights can open a console and type
into it (measured in the review of V4). So the answer is recorded in the person's place (the actor ``human``) with its
principal (``decisions.principal``, audit 010 WF-013): channel ``terminal``, not authenticated, not independent of the
caller. Who may decide, and how that is proven, is the founder's decision D3. The owner and the venture
come from the same environment the workforce service reads (``--db-dir``, ``--venture``, ``--owner`` override it).
``correct <mission> <unit> --file <json> --reason "..."`` writes the unit G6 is waiting for the chat to write, as the
person's own output (a draft until its check judges it). ``--minutes N`` on an answer keeps how long the person says
it took (R5: 0 to 480, their word, never a measurement). The archive's formal-requirement challenges are not ported.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from g6.workforce import acceptance, service
from g6.workforce.bench import ENV_DB_DIR
from g6.workforce.decisions import declared_minutes, principal
from g6.workforce.missions import RUNNING

VERBS = {"approve": "approve", "reject": "reject", "uphold": "uphold", "set-aside": "set_aside"}
# COPY: This measurement does not include preparation or review away from the terminal.
TIMING_NOTE = "Human time measures time at the terminal only."


def _now() -> datetime:
    return datetime.now(UTC)


def _number(text: str | None) -> object:
    """``--minutes`` as a number (a whole number kept whole); text that is not one is passed on to be refused."""
    if text is None:
        return None
    try:
        found = float(text)
    except ValueError:
        return text
    return int(found) if found.is_integer() else found


def _interactive() -> bool:
    """Whether a person is at this process's terminal (both ends are a TTY)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _pending(wf: service.Workforce) -> list[dict]:
    out = []
    for mission in wf.missions.list(wf.venture_id):
        if mission["status"] in (*RUNNING, "awaiting_plan"):
            out += [{"mission_id": mission["mission_id"], **q}
                    for q in wf.runtime.decisions.pending(mission["mission_id"])
                    + wf.runtime.confirmation_pending(mission["mission_id"])]
    return out


def command_line() -> argparse.ArgumentParser:
    """The command line ``main`` reads; the desktop exe reads its command with it too (lead ruling F8)."""
    parser = argparse.ArgumentParser(prog="python -P -m g6.workforce.decide",
                                     description="Decide, as the owner, what a mission waits for a person to decide.")
    parser.add_argument("command", choices=["list", *VERBS, "correct", "confirm", "allow"])
    parser.add_argument("mission", nargs="?", help="the mission id (allow: the repository's name)")
    parser.add_argument("unit", nargs="?", help="the unit key (a gate, or the verifier whose fail is in dispute)")
    parser.add_argument("--reason", default="", help="why (kept with the decision)")
    parser.add_argument("--file", help="correct: a JSON file holding the unit's new output")
    # COPY: The --minutes option's help.
    parser.add_argument("--minutes", help="approve, reject, uphold, set-aside: how many minutes this decision took you, "
                                          "if you want it counted (0 to 480); kept with the decision as your own figure")
    parser.add_argument("--db-dir", help=f"the workforce store (default: ${ENV_DB_DIR})")
    parser.add_argument("--venture", help=f"the venture id (default: ${service.ENV_VENTURE})")
    parser.add_argument("--owner", help=f"the owner (default: ${service.ENV_OWNER}, else the local operator)")
    return parser


def main(argv: list[str] | None = None, *, ask: Callable[[str], str] | None = None) -> int:
    parser = command_line()
    args = parser.parse_args(argv)
    for name, value in ((ENV_DB_DIR, args.db_dir), (service.ENV_VENTURE, args.venture),
                        (service.ENV_OWNER, args.owner)):
        if value:
            os.environ[name] = value
    wf = service.bind()
    if args.command == "list":
        print(json.dumps(_pending(wf), indent=2))
        return 0
    if args.command == "correct" and not args.file:
        parser.error("correct needs --file: a JSON file holding the unit's new output")
    if args.minutes is not None and args.command not in VERBS:
        # COPY: The refusal of --minutes on a command that records no decision.
        parser.error("--minutes is kept with a decision: approve, reject, uphold or set-aside")
    if args.command == "confirm":
        if not args.mission:
            parser.error("confirm needs the mission id")
        if not _interactive():
            print("refused: a confirmation is typed at an interactive terminal (run this yourself); nothing was "
                  "recorded", file=sys.stderr)
            return 2
        started = _now()
        print(TIMING_NOTE)
        print("\n".join(acceptance.describe(wf.missions.acceptance(args.mission))))
        typed = (ask or input)(f"Type the mission id ({args.mission}) to confirm what counts as done: ")
        ended = _now()
        if str(typed).strip() != args.mission:
            print("refused: not confirmed; nothing was recorded", file=sys.stderr)
            return 2
        identity = f"terminal:{wf.missions.owner}"
        try:
            found = wf.runtime.confirm_acceptance(args.mission, reason=args.reason, actor="human",
                                                  decided_by=identity, timing=(started, ended),
                                                  principal=principal(identity, channel="terminal"))
            from g6.workforce import coding

            if coding.frozen(wf, args.mission):
                coding.advance(wf, args.mission)
        except (ValueError, LookupError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"confirmed": args.mission, "intervention": found.id}))
        return 0
    if args.command == "allow":
        return _allow(wf, parser, args, ask)
    if not (args.mission and args.unit):
        parser.error(f"{args.command} needs the mission id and the unit key")
    if not _interactive():
        print("refused: a decision is typed at an interactive terminal (run this yourself); nothing was recorded",
              file=sys.stderr)
        return 2
    try:  # R5: refused before the person is asked to type anything
        minutes = declared_minutes(_number(args.minutes))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    started = _now()
    print(TIMING_NOTE)
    typed = (ask or input)(f"Type the unit key ({args.unit}) to {args.command} it in mission {args.mission}: ")
    ended = _now()
    if str(typed).strip() != args.unit:
        print("refused: not confirmed; nothing was recorded", file=sys.stderr)
        return 2
    try:
        if args.command == "correct":
            content = json.loads(Path(args.file).read_text(encoding="utf-8"))
            found = wf.runtime.correct(args.mission, args.unit, content, reason=args.reason, actor="human",
                                       decided_by=f"terminal:{wf.missions.owner}")
            print(json.dumps(found))
            return 0
        identity = f"terminal:{wf.missions.owner}"
        found = wf.runtime.decide(args.mission, args.unit, VERBS[args.command], reason=args.reason, actor="human",
                                  decided_by=identity, timing=(started, ended), minutes=minutes,
                                  principal=principal(identity, channel="terminal"))
        from g6.workforce import coding

        if args.command == "approve" and coding.frozen(wf, args.mission):
            coding.advance(wf, args.mission)
    except (ValueError, LookupError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"decision_id": found.get("decision_id"), "unit": found.get("unit_key") or args.unit,
                      "decision": found.get("decision"),
                      **({"minutes_declared": minutes} if minutes is not None else {})}))
    return 0


def _allow(wf: service.Workforce, parser: argparse.ArgumentParser, args: argparse.Namespace,
           ask: Callable[[str], str] | None) -> int:
    """``allow <repository>`` (audit 010 WF-007): the person reads what running the repository's code means at its present
    revision and types its name back; G6 records the permission that ``mission_create`` needs before it runs anything."""
    from g6.workforce import coding

    if not args.mission:
        parser.error("allow needs the repository's name")
    if not _interactive():
        print("refused: a permission is typed at an interactive terminal (run this yourself); nothing was recorded",
              file=sys.stderr)
        return 2
    try:
        terms = coding.execution_terms(wf, args.mission)
    except (ValueError, LookupError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    started = _now()
    print(TIMING_NOTE)
    print("\n".join(terms["lines"]))
    typed = (ask or input)(f"Type the repository's name ({args.mission}) to allow G6 to run its code: ")
    ended = _now()
    if str(typed).strip() != args.mission:
        print("refused: not allowed; nothing was recorded", file=sys.stderr)
        return 2
    identity = f"terminal:{wf.missions.owner}"
    try:
        found = coding.allow_execution(wf, terms, reason=args.reason, decided_by=identity,
                                       principal=principal(identity, channel="terminal"), timing=(started, ended))
    except (ValueError, LookupError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"allowed": args.mission, "revision": terms["revision"], "evidence": found}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
