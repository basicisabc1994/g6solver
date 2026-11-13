# SPDX-License-Identifier: Apache-2.0
"""One context line when cached allowance advice changes; no provider refresh."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from g6.allowance import report
from g6._allowance.store import Store

_RECOMMENDATIONS = frozenset({"unknown", "usual workload", "conserve optional fan-out",
                             "no additional optional subagents", "use more for useful independent work"})


def context_note(root: str | Path, reading: dict) -> str:
    recommendations = []
    for provider in ("codex", "claude"):
        value = reading.get("providers", {}).get(provider, {}).get("recommendation", "unknown")
        recommendations.append(value if value in _RECOMMENDATIONS else "unknown")
    note = (f"G6 allowances: Codex {recommendations[0]}; Claude {recommendations[1]}. "
            "Read g6_allowances before optional fan-out; advice only, with no provider switch.")
    digest = hashlib.sha256(note.encode()).hexdigest()
    return note if Store(root).changed_note("allowance-advice", digest) else ""


def main() -> int:
    try:
        root = Path(os.environ.get("G6_HOME", str(Path.home() / ".g6"))) / "allowances"
        note = context_note(root, report(refresh=False))
        if note:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": note}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
