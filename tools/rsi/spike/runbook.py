# SPDX-License-Identifier: Apache-2.0
"""Offline S0 entry point. No execute operation exists."""
import argparse
import json
from pathlib import Path

from g6.recursion.spike.family_bank import dry_run
from g6.recursion.spike.runbook import STEPS, check_file, check_record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    describe = commands.add_parser("describe")
    describe.add_argument("step", choices=STEPS)
    check = commands.add_parser("check")
    check.add_argument("step", choices=STEPS)
    check.add_argument("record", type=Path)
    bank = commands.add_parser("family-bank")
    bank.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "describe":
        print(json.dumps(STEPS[args.step].describe(), indent=2))
        return 0
    if args.action == "check":
        result = check_file(args.step, args.record)
    else:
        value = dry_run()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        result = check_record("P0-9", value)
    print(json.dumps(result.as_dict(), indent=2))
    return {"pass": 0, "fail": 1, "unknown": 2}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
