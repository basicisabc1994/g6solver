# SPDX-License-Identifier: Apache-2.0
"""Demo 1 — persistent state across sessions.

Three separate Python processes share one state folder ($G6_HOME):

1. A task runs through ``g6.harness``. The answer misses one of the two acceptance checks, so the run is
   ``blocked-escalated`` and the harness stores the failed check against this kind of task.
2. A new process runs the same kind of task with a standard that forgot that check. The harness remembers the
   earlier failure and will not call the answer usable until the check is back (or a person waives it).
3. The check is restored and the answer fixed: the run is a ``qualified-draft``.

A fourth run with an empty state folder repeats step 2 and passes, which shows the memory caused the difference.

    python examples/demo_1_persistent_state.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

SESSION = textwrap.dedent("""
    import sys
    from g6.harness import Check, Standard, run

    GOAL = "Reply to a customer asking how refunds work"
    credit = Check("mentions store credit", lambda out: "store credit" in out.lower())
    window = Check("states the refund window", lambda out: "14 days" in out)
    step = sys.argv[1]
    checks = (credit,) if step in ("2", "control") else (credit, window)
    answer = ("Refunds are issued within 14 days, or you can take store credit." if step == "3"
              else "You can take store credit.")
    reply = run(Standard(GOAL, checks), lambda contract: answer)
    print(f"  checks:      {[c.predicate for c in checks]}")
    print(f"  answer:      {answer!r}")
    print(f"  label:       {reply['completion_state']}")
    print(f"  code:        {(reply.get('warning_card') or {}).get('code', '-')}")
    print(f"  next action: {reply['next_action']}")
""")

STEPS = (
    ("1", "Session 1: the answer misses the refund window"),
    ("2", "Session 2, a new process: the standard forgot the refund-window check"),
    ("3", "Session 3: the check is back and the answer fixed"),
)


def _session(step: str, home: str) -> str:
    env = dict(os.environ, G6_HOME=home)
    return subprocess.run([sys.executable, "-c", SESSION, step], env=env, capture_output=True, text=True,
                          check=True).stdout


def main() -> int:
    home, empty = tempfile.mkdtemp(prefix="g6_demo1_"), tempfile.mkdtemp(prefix="g6_demo1_empty_")
    try:
        for step, title in STEPS:
            print(title)
            print(_session(step, home))
        print("Control: session 2 again, with an empty state folder")
        print(_session("control", empty))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(empty, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
