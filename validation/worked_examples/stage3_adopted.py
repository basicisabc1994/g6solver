"""What the loop adopted, as arm K2's data (PLAN.md, 2026-11-02 evening). The head comes out through ``leave_shadow``, the loop's own way out, and its reviewer
text goes into ``harness/K2.json`` word for word: no change here is written by a person or an agent. A change that is no reviewer text has nothing to carry to
K1's reviewer, so it is reported and K2 is not made. This is a test of a recommendation, never a promotion.
Usage: stage3_adopted.py <folder>"""
import json
import sys
from pathlib import Path
from typing import Any

import run as we

STEP = "Read the patch this way as well:\n"


def text_of(profile: dict[str, Any], parent: str) -> str | None:
    """The reviewer text ``profile`` adds to ``parent``, or ``None`` when its prompt and skill are the parent's."""
    prompt, skill = profile.get("prompt") or "", profile.get("skill_text") or ""
    if prompt == parent and not skill:
        return None
    return STEP + "\n\n".join(part for part in (prompt, skill) if part)


def main() -> int:
    from g6.metaharness.runner import _pinned, open_loop
    from stage3_domain import K1_READING

    root = Path(sys.argv[1]).resolve()
    loop, _ = open_loop(root, pinned=_pinned(root))
    reply = loop.leave_shadow()
    profile = reply.get("candidate")
    if not profile:
        print("=== stage3 nothing to carry:", reply.get("message"))
        return 1
    text = text_of(profile, K1_READING)
    if text is None:
        print("=== stage3 the head is no reviewer text", json.dumps({k: profile.get(k) for k in ("retries", "routing")}))
        return 1
    (we.HARNESS / "K2.json").write_text(json.dumps({"critic": text}, indent=1) + "\n", encoding="utf-8")
    print("=== stage3 K2 written", len(text), "chars from version", reply.get("message", "")[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
