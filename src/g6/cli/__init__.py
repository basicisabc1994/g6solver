# SPDX-License-Identifier: Apache-2.0
"""The ``g6`` command line: ``init``, ``components``, ``find``, ``describe``, ``invoke``, ``home``, ``bundle``, ``doctor``,
``skills``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, default=str))


def _bundle(args: argparse.Namespace) -> int:
    from g6.bundles import AWAITING_CERTIFICATE, awaiting_certificate, catalog
    from g6.bundles.server import serve

    bundles = catalog()
    if not args.bundle_id:
        for b in bundles.values():
            held = f" ({AWAITING_CERTIFICATE})" if awaiting_certificate(b) else ""
            print(f"{b.id:16} {b.title}{held} — {b.summary}")
        return 0
    if args.bundle_id not in bundles:
        print(f"unknown bundle {args.bundle_id!r}; run 'g6 bundle' to list them", file=sys.stderr)
        return 2
    serve(bundles[args.bundle_id], port=args.port, open_browser=not args.no_browser)
    return 0


def _state(args: argparse.Namespace) -> int:
    from g6.core import snapshots

    try:
        if args.state_cmd == "ls":
            for s in snapshots.list_snapshots():
                print(f"{s.name}  {s.files} files  {s.size} bytes")
        elif args.state_cmd == "snapshot":
            s = snapshots.snapshot(args.label)
            print(f"saved snapshot {s.name} ({s.files} files)")
        elif args.state_cmd == "rollback":
            restored, safety = snapshots.rollback(args.name)
            print(f"restored {restored.name}; the previous state is saved as {safety.name}")
        else:
            print(f"exported to {snapshots.export(args.path)} (API keys left out)")
    except (FileNotFoundError, FileExistsError, KeyError, ValueError) as exc:
        print(exc.args[0] if exc.args else exc, file=sys.stderr)
        return 1
    return 0


def _superpowers_plugin(home: Path) -> bool:
    """Whether Claude Code already has the superpowers plugin, so its skills need no second copy."""
    plugins = home / ".claude" / "plugins"
    if any(p.is_dir() for p in plugins.glob("cache/*/superpowers")):
        return True
    try:
        return "superpowers@" in (plugins / "installed_plugins.json").read_text(encoding="utf-8")
    except OSError:
        return False


def _skills(args: argparse.Namespace) -> int:
    import shutil

    from g6.core import skills

    components = {c: skills.skill_name(c) for c in skills.components_with_skills()}
    vendored = skills.vendored()
    if args.skills_cmd == "list":
        print(f"{skills.GUIDE:36} which G6 step fits each point of an agent's work (installed by default)")
        for component, name in components.items():
            print(f"{name:36} the {component} component's own skill")
        for name in vendored:
            print(f"{name:36} superpowers, for a coding agent's lifecycle (--with-superpowers)")
        return 0

    sources = {skills.GUIDE: skills.guide_dir(), **{name: skills.skill_dir(c) for c, name in components.items()},
               **vendored}
    if args.all:
        wanted = [skills.GUIDE, *components.values()]
    elif args.names:
        unknown = [n for n in args.names if n not in components and n not in sources]
        if unknown:
            print(f"no skill called {', '.join(unknown)}; 'g6 skills list' shows them all", file=sys.stderr)
            return 2
        wanted = [components.get(n, n) for n in args.names]
    else:
        wanted = [skills.GUIDE]
    if args.with_superpowers:
        if _superpowers_plugin(Path.home()):
            print("The superpowers plugin is installed, so its skills are not copied a second time.")
        else:
            wanted += [n for n in vendored if n not in wanted]

    dest = (Path.home() if args.user else Path.cwd()) / ".claude" / "skills"
    for name in wanted:
        target = dest / name
        if target.exists() and not args.force:
            print(f"{name}: already in {dest}, left as is (--force replaces it)")
            continue
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(sources[name], target, ignore=shutil.ignore_patterns("__pycache__"))
        print(f"{name}: installed in {target}")
    return 0


def _doctor_report(args: argparse.Namespace) -> int:
    from g6.cli.doctor import E_WRITE_FAILED, diagnostic_report, write_report

    if not args.report:
        print("--keep-caller-text and --write go with --report <traceback file>", file=sys.stderr)
        return 2
    try:
        text = Path(args.report).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"cannot read {args.report}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    built = diagnostic_report(text, keep_caller_text=args.keep_caller_text)
    written = write_report(built, dry_run=not args.write)
    _print({"report": built, "write": written})
    return 1 if built["completion_state"] == "blocked-escalated" or written["code"] == E_WRITE_FAILED else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="g6", description="Open-source harness engineering for AI agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the local state folder ($G6_HOME, default ~/.g6)")
    sub.add_parser("home", help="print the state folder")
    sub.add_parser("components", help="list installed components")
    f = sub.add_parser("find", help="rank components for a task description")
    f.add_argument("query")
    f.add_argument("--limit", type=int, default=5)
    d = sub.add_parser("describe", help="maturity, ops and dependencies of a component")
    d.add_argument("component")
    i = sub.add_parser("invoke", help="run one op of a component")
    i.add_argument("component")
    i.add_argument("op")
    i.add_argument("params", nargs="?", default="{}", help="JSON object of op parameters")
    st = sub.add_parser("state", help="snapshot, list, roll back or export the state folder")
    st_sub = st.add_subparsers(dest="state_cmd", required=True)
    st_sub.add_parser("ls", help="list snapshots")
    sn = st_sub.add_parser("snapshot", help="save the current state")
    sn.add_argument("--label", default="")
    rb = st_sub.add_parser("rollback", help="restore a snapshot (the current state is snapshotted first)")
    rb.add_argument("name")
    ex = st_sub.add_parser("export", help="write the state to a zip for sharing (API keys left out)")
    ex.add_argument("path")
    doc = sub.add_parser("doctor", help="check this installation (read-only, no network)")
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--report", metavar="TRACEBACK_FILE",
                     help="build a local error report from a saved Python traceback (caller text stripped; sent "
                          "nowhere)")
    doc.add_argument("--keep-caller-text", action="store_true",
                     help="keep messages, names and paths in the report, with secrets, e-mail addresses and home "
                          "folders masked")
    doc.add_argument("--write", action="store_true",
                     help="write the report under $G6_HOME/diagnostics (without it nothing is written)")
    b = sub.add_parser("bundle", help="open a ready-made bundle in your browser (no id: list them)")
    b.add_argument("bundle_id", nargs="?")
    b.add_argument("--port", type=int, default=0)
    b.add_argument("--no-browser", action="store_true")
    sk = sub.add_parser("skills", help="list G6's skills, or copy them into an agent's skills folder")
    sk_sub = sk.add_subparsers(dest="skills_cmd", required=True)
    sk_sub.add_parser("list", help="list the skills G6 ships")
    si = sk_sub.add_parser("install", help="copy skills into ./.claude/skills (with no names: the using-g6 skill)")
    si.add_argument("names", nargs="*", help="using-g6, a component (e.g. coding_agent_gate) or a skill name")
    si.add_argument("--all", action="store_true", help="the using-g6 skill and every component's skill")
    si.add_argument("--with-superpowers", action="store_true",
                    help="also the superpowers skills for a coding agent (skipped if the superpowers plugin is there)")
    si.add_argument("--user", action="store_true", help="into ~/.claude/skills, for every project")
    si.add_argument("--force", action="store_true", help="replace a skill folder that is already there")
    args = ap.parse_args(argv)

    if args.cmd == "bundle":
        return _bundle(args)
    if args.cmd == "skills":
        return _skills(args)
    if args.cmd == "state":
        return _state(args)
    if args.cmd == "doctor" and (args.report or args.keep_caller_text or args.write):
        return _doctor_report(args)
    if args.cmd == "doctor":
        from g6.cli.doctor import render, report, run_checks

        checks = run_checks()
        print(json.dumps(report(checks), indent=2) if args.json else render(checks))
        return 1 if report(checks)["status"] == "fail" else 0

    from g6.core import discovery
    from g6.core.registry import get_registry
    from g6.core.storage import g6_home

    if args.cmd == "init":
        home = g6_home()
        home.mkdir(parents=True, exist_ok=True)
        print(f"G6 state folder ready: {home}")
        return 0
    if args.cmd == "home":
        print(g6_home())
        return 0
    if args.cmd == "components":
        _print(get_registry().names())
        return 0
    if args.cmd == "find":
        _print(discovery.find(args.query, args.limit))
        return 0
    if args.cmd == "describe":
        info = discovery.describe(args.component)
        _print(info)
        return 0 if info.get("available") else 1
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as exc:
        print(f"params is not JSON: {exc}", file=sys.stderr)
        return 2
    result = get_registry().invoke(args.component, args.op, params)
    if result.__class__.__name__ == "Success":
        from g6.core.door import door_reply

        # The same door as g6_invoke: a verified stands only when G6's own re-check reproduces it.
        _print(door_reply(args.component, args.op, result)["result"])
        return 0
    print(result.failure(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
