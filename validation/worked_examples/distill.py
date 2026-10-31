# SPDX-License-Identifier: Apache-2.0
"""Round 3's harness distillation (PLAN.md, 2026-10-30): failed runs in, generic instructions out.

G6's ``FailureTraceStrategy`` reads the failures of a generation's training runs and proposes new instructions for the
coding agent. What it may read comes from training bugs only. The text is refused if it names a project, a bug, a test or
a file from those runs, so that it can carry only a way of working. The agent never sees the maintainers' tests.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from g6.core import llm
from g6.metaharness.failure_strategy import FailureTraceStrategy
from g6.metaharness.propose import DevFailure, ProposerView

MODEL = "claude-opus-5-5"
OUT_CHARS, PATCH_CHARS = 3000, 4000
TASK = ("Improve the instructions given to a coding agent that fixes a bug in an open-source Python project, with G6 "
        "reviewing its patch. Each failure below is a run whose patch the project's own tests rejected; the agent never "
        "sees those tests. Return prompt: the complete new instructions, at most 400 words. They must work on any "
        "project. Never name a project, a file, a function, a test or a value from these failures. Say what to do "
        "differently: how to read the issue, what to check, and when to stop.")
#: One word per project, compared without regard to case. "click" and "black" are ordinary words too; the instructions
#: can do without them.
PROJECTS = ("attrs", "black", "click", "fastapi", "pydantic", "starlette")


def named(text: str, tests: Iterable[str], files: Iterable[str], projects: Sequence[str] = PROJECTS) -> list[str]:
    """What ``text`` names that it must not: a project, an issue number, a test's own name, or a file from the runs."""
    found = [word for word in projects if re.search(rf"\b{word}\b", text, re.I)]
    found += re.findall(r"#?\b\d{4,5}\b", text)
    found += re.findall(r"(?<![A-Za-z])[A-Za-z]:\\|/(?:home|Users)/", text)      # the text is pushed to a public branch
    names = {test.rpartition("::")[2].partition("[")[0] for test in tests} | {Path(f).name for f in files}
    found += [name for name in sorted(names) if len(name) > 3 and name.lower() in text.lower()]
    return found


def failures(rows: Sequence[Mapping[str, Any]], archive: Path) -> list[DevFailure]:
    """One ``DevFailure`` for each failed run in ``rows`` (each with ``task``, ``run``, ``failing``, ``tests``). A run that ended
    well has ``caught``: what the runner's own checks found on its first patch."""
    found = []
    for row in rows:
        folder = archive / row["run"]
        report, patch = [(folder / name).read_text("utf-8", errors="replace")[:size] if (folder / name).is_file() else ""
                         for name, size in (("final_report.txt", OUT_CHARS), ("final.patch", PATCH_CHARS))]
        caught = f"\n--- what the runner's checks caught on the first patch ---\n{row['caught'][:OUT_CHARS]}" if row.get("caught") else ""
        found.append(DevFailure(case_id=row["task"], input="", output=f"{report}\n--- the patch ---\n{patch}{caught}",
                                expected="the project's own tests for this bug pass", oracle="the project's own tests",
                                failed_checks=tuple(row.get("tests") or ()), failure_codes=()))
    return found


class Metered:
    """The model, and what its calls cost at most: tokens estimated generously from characters (2.5 to a token)."""

    def __init__(self, inner: llm.LLMCaller) -> None:
        self.inner, self.cost_usd = inner, 0.0

    def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
        self.cost_usd += len(system + user) * 4.00 / 2.5 / 1_000_000      # counted first: a failed call can still cost
        said = self.inner.complete(system, user, max_tokens=max_tokens)
        self.cost_usd += len(said) * 20.00 / 2.5 / 1_000_000
        return said


def distill(base: str, rows: Sequence[Mapping[str, Any]], archive: Path, caller: llm.LLMCaller,
            notes: Sequence[str] = (), projects: Sequence[str] = PROJECTS) -> tuple[str, list[str]]:
    """The new instructions from ``base`` and the failures in ``rows``, and what they wrongly name ([] when clean)."""
    found = failures(rows, archive)
    view = ProposerView(workflow="round3", task="\n".join([TASK, *notes]), checks=(), steps=(), models=(MODEL,),
                        optional_checks=(), base={"prompt": base}, dev=(), failures=tuple(found),
                        dev_success=len(rows) - len(found), notes=tuple(notes))
    proposals = FailureTraceStrategy(caller).propose(view, None)
    text = str((proposals[0] if proposals else {}).get("prompt") or "").strip()
    if "\n" not in text:      # the model sent the text with its line breaks escaped
        text = text.replace("\\n", "\n")
    tests = [t for row in rows for t in row.get("tests") or ()]
    files = re.findall(r"^diff --git a/(\S+)", "\n".join(f.output for f in found), re.M)
    return text, named(text, tests, files, projects)


def main_row(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
