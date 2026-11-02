"""One tick of the loop on stage 3's folder, once a person has enrolled it (``enrol``'s reply names ``loop.tick()`` as what follows). It starts no runner and
nothing that outlives it. A watcher reads Codex's weekly figure, which costs no model call, and asks the folder to stop at ``CODEX_STOP`` percent, so the loop
can never take the K arms' reviewer away from them. It prints what the cycle's row says of itself and no case.
Usage: stage3_tick.py <folder>"""
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from g6.metaharness.runner import _local_now, _pinned, open_loop
from g6.self_maintenance.envelope import ask_to_stop
from g6.self_maintenance.pacing import read_settings
from g6.self_maintenance.usage import codex_limits

CODEX_STOP, EVERY_S = 60, 600


def used_percent(limits: dict) -> float | None:
    window = ((limits.get("rateLimits") or {}).get("primary") or {})
    return window.get("usedPercent")


def watch(root: Path, done: threading.Event, read=codex_limits) -> None:
    while not done.wait(EVERY_S):
        try:
            used = used_percent(read())
        except Exception as why:  # noqa: BLE001 - an unread figure stops nothing: the tick's own account reads still stand
            print(f"=== stage3 watch could not read: {type(why).__name__}", flush=True)
            continue
        print(f"=== stage3 watch {datetime.now():%H:%M} codex week {used}%", flush=True)
        if used is not None and used >= CODEX_STOP:
            ask_to_stop(root, f"Codex's week reached {used}%, the study's limit for the loop is {CODEX_STOP}%")
            return


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    settings = read_settings(root)
    loop, reader = open_loop(root, pinned=_pinned(root), settings=settings)
    print(f"=== stage3 tick start {datetime.now():%H:%M:%S} worst case {loop.worst_case()} calls", flush=True)
    done = threading.Event()
    threading.Thread(target=watch, args=(root, done), daemon=True).start()
    if reader is not None:
        reader.refresh(_local_now())
    try:
        reply = loop.tick()
    finally:
        done.set()
    keep = ("completion_state", "message", "outcome", "dev_success", "dev_cases", "constraint", "failure_records", "tuning", "decision", "champion", "head_version")
    print("=== stage3 tick end", datetime.now().strftime("%H:%M:%S"), json.dumps({k: reply.get(k) for k in keep if k in reply}, default=str)[:3000], flush=True)
    return 0 if reply["completion_state"] != "blocked-escalated" else 1


if __name__ == "__main__":
    sys.exit(main())
