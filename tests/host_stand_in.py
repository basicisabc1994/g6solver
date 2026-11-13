# SPDX-License-Identifier: Apache-2.0
"""Claude Code and Codex stood in for at the one place G6 runs either program (``host_models._run``).

Once ``g6.self_maintenance.usage`` is imported, its construction hook makes every host caller a watched one, whose
completion never resolves the caller's own ``complete`` and whose session refuses a replaced ``_reply``. A test that stands a
host in there holds only while no earlier test imported usage; after one, its stand-in is passed by, and the person's real
program would be looked for and started. Standing in at the launcher holds on the native path and the owned path alike."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from g6.core import host_models


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def stand_in_hosts(monkeypatch, answer: Callable[[str, str], str]) -> None:
    """Every host program started from here on is a stand-in that replies ``answer(system, user)`` as its program would:
    Claude Code's JSON result, or Codex's events and its last-message file. Nothing real is looked for or started."""

    def run(argv, stdin, cwd, files, *rest):
        if "exec" in argv:                       # Codex: one prompt between G6's delimiters
            text = answer(_between(stdin, "<<<G6_INSTRUCTIONS\n", "\nG6_INSTRUCTIONS>>>"),
                          _between(stdin, "<<<G6_REQUEST\n", "\nG6_REQUEST>>>"))
            Path(argv[argv.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
            item = {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
            return 0, json.dumps(item) + "\n" + json.dumps({"type": "turn.completed"}) + "\n", ""
        given = argv[argv.index("--system-prompt-file") + 1] if "--system-prompt-file" in argv else ""
        system = Path(given).read_text(encoding="utf-8") if given else ""
        return 0, json.dumps({"type": "result", "is_error": False, "result": answer(system, stdin)}), ""

    monkeypatch.setattr(host_models, "_run", run)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    for kind in (host_models.ClaudeCodeCaller, host_models.CodexCaller):
        monkeypatch.setattr(kind, "locate", lambda self: "fake-host")
