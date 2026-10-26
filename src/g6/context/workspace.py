# SPDX-License-Identifier: Apache-2.0
"""``Workspace(root)``: the one directory that holds this package's state, with safe writers (audit 032 §11).

Layout under ``root``: ``memory/`` (notes, read back into packets), ``memory/exemplars/``, ``compactions/``,
``retrieval_logs/`` and ``scorecards/``. ``root`` is resolved once, at construction, so memory is written and read
under the same directory whatever the working directory is (CTX-010). File names are built only from a validated
``run_id`` / ``phase`` and every target must resolve inside ``root`` before it is written (CTX-003). JSON goes
through ``json.dumps``, never string formatting, and every string written is masked (CTX-007). The envelope state
written into an artifact is recomputed by ``score_packet``, never copied from the packet (CTX-004).

Notes come back as untrusted ``memory_note`` items whose ``freshness_class`` comes from the file's mtime (CTX-001).
The compaction lists test failures and open bugs from structured verification items (``outcome == "failed"`` /
``"unresolved"``), not from words in the text (CTX-012).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from g6.context.envelope import score_packet
from g6.context.packet import ContextItem, ContextPacket, estimate_tokens, packet_tokens, validate_name
from g6.context.retrieve import query_terms
from g6.context.redact import scrub

_NOTE_CHARS = 2000
_BULLET_CHARS = 240


def _masked(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {str(k): _masked(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_masked(v) for v in value]
    return value


def _line(text: object) -> str:
    """One masked line: newlines collapsed, so a value cannot open a new markdown section."""
    return scrub(" ".join(str(text).split()))


def _bullets(items: list[ContextItem]) -> list[str]:
    return [f"- {_line(item.key)}: {_line(item.value)[:_BULLET_CHARS]}" for item in items] or ["- none recorded"]


def budget_ratio(packet: ContextPacket) -> float:
    """Recounted tokens over ``budget_tokens`` (0.0 when there is no budget)."""
    return packet_tokens(packet) / packet.budget_tokens if packet.budget_tokens > 0 else 0.0


class Workspace:
    """State for packets under one ``root``; see the module docstring for the layout and the guarantees."""

    def __init__(self, root: str | os.PathLike[str], *, aging_after_days: float = 30.0,
                 stale_after_days: float = 90.0) -> None:
        self.root = Path(root).resolve()
        self.aging_after_days = float(aging_after_days)
        self.stale_after_days = float(stale_after_days)

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    @property
    def exemplar_dir(self) -> Path:
        return self.memory_dir / "exemplars"

    def _target(self, subdir: str, filename: str) -> Path:
        target = self.root / subdir / filename
        if not target.resolve().is_relative_to(self.root):  # audit 032, CTX-003 (also catches planted symlinks)
            raise ValueError(f"G6_E_CONTEXT_UNSAFE_PATH: {subdir}/{filename} resolves outside the workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def _phase(value: str) -> str:
        return validate_name(value, "phase") if value else ""

    def _write_json(self, subdir: str, filename: str, body: dict[str, Any]) -> Path:
        target = self._target(subdir, filename)
        target.write_text(json.dumps(_masked(body), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target

    def write_retrieval_log(self, packet: ContextPacket) -> Path:
        """``retrieval_logs/<run_id>.retrieval.json``: where each item came from and why it was included."""
        run_id = validate_name(packet.run_id)
        fields = ("key", "section", "authority", "kind", "trust", "source", "freshness_class", "retrieved_by",
                  "retrieved_at", "included_because", "outcome")
        body = {"run_id": run_id, "phase": self._phase(packet.phase),
                "completion_state": score_packet(packet).completion_state,
                "items": [{name: getattr(item, name) for name in fields} for item in packet.all_items],
                "repository_handles": [{"kind": h.kind, "file_path": h.file_path, "line": h.line}
                                       for h in packet.repository_handles]}
        return self._write_json("retrieval_logs", f"{run_id}.retrieval.json", body)

    def write_scorecard(self, packet: ContextPacket) -> Path:
        """``scorecards/<run_id>.scorecard.json``: the scorecard recomputed from the packet's content."""
        run_id = validate_name(packet.run_id)
        return self._write_json("scorecards", f"{run_id}.scorecard.json", score_packet(packet).model_dump(mode="json"))

    def compact(self, packet: ContextPacket, *, trigger: str, phase: str = "") -> Path:
        """``compactions/<run_id>.<phase>.summary.md``: decisions, user constraints, open bugs and test failures."""
        run_id = validate_name(packet.run_id)
        label = self._phase(phase or packet.phase) or "context"
        target = self._target("compactions", f"{run_id}.{label}.summary.md")
        checks = packet.verification
        lines = [
            f"# Context Compaction: {run_id}", "",
            f"- trigger: {_line(trigger)}",
            f"- role: {_line(packet.role)}",
            f"- used_tokens: {packet_tokens(packet)}",
            f"- budget_tokens: {packet.budget_tokens}",
            f"- completion_state: {score_packet(packet).completion_state}", "",
            "## Architectural Decisions", *_bullets(packet.authority + packet.component), "",
            "## User Constraints", *_bullets(packet.task + packet.output_contract), "",
            # audit 032, CTX-012: from structured outcomes, not from "fail"/"test" appearing in the text
            "## Unresolved Bugs", *_bullets([item for item in checks if item.outcome == "unresolved"]), "",
            "## Test Failures", *_bullets([item for item in checks if item.outcome == "failed"]), "",
            "## Rollback State", "- none recorded (a context packet carries no rollback state)",
        ]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def compact_if_over_budget(self, packet: ContextPacket, *, ratio: float = 0.70, phase: str = "") -> Path | None:
        """Write a compaction when the packet uses more than ``ratio`` of its budget; otherwise ``None``."""
        if budget_ratio(packet) <= ratio:
            return None
        return self.compact(packet, trigger=f"token-budget>{ratio:.2f}", phase=phase)

    def write_memory(self, run_id: str, summary: str) -> Path:
        """``memory/<run_id>.summary.md``, masked; read back by ``memory_items``."""
        target = self._target("memory", f"{validate_name(run_id)}.summary.md")
        target.write_text(scrub(summary).rstrip() + "\n", encoding="utf-8")
        return target

    def memory_items(self, query: str, *, run_id: str = "", limit: int = 5) -> list[ContextItem]:
        """Notes in ``memory/`` that mention a word of ``query`` (all notes when it has none)."""
        return self._notes(self.memory_dir, "memory", "operational", query, run_id, limit)

    def exemplar_items(self, query: str, *, run_id: str = "", limit: int = 3) -> list[ContextItem]:
        """Notes in ``memory/exemplars/`` that mention a word of ``query``."""
        return self._notes(self.exemplar_dir, "exemplar", "exemplar", query, run_id, limit)

    def freshness(self, mtime: float, *, now: float | None = None) -> tuple[str, str]:
        """``(freshness_class, stale_after)`` for a file last modified at ``mtime`` (epoch seconds)."""
        age_days = ((time.time() if now is None else now) - mtime) / 86400
        stale_after = datetime.fromtimestamp(mtime + self.stale_after_days * 86400, timezone.utc)
        cls = ("stale" if age_days >= self.stale_after_days else
               "aging" if age_days >= self.aging_after_days else "fresh")
        return cls, stale_after.replace(microsecond=0).isoformat()

    def _notes(self, directory: Path, section: str, authority: str, query: str, run_id: str,
               limit: int) -> list[ContextItem]:
        if not directory.is_dir():
            return []
        terms = query_terms(query)
        items: list[ContextItem] = []
        for note in sorted(directory.glob("*.md")):
            if not note.is_file() or not note.resolve().is_relative_to(self.root):
                continue
            try:
                text = note.read_text(encoding="utf-8-sig", errors="replace")
                mtime = note.stat().st_mtime
            except OSError:
                continue
            if terms and not any(t in text.lower() or t in note.name.lower() for t in terms):
                continue
            value = scrub(text)[:_NOTE_CHARS]
            freshness, stale_after = self.freshness(mtime)  # audit 032, CTX-001: freshness from mtime
            items.append(ContextItem(
                key=f"{section}:{note.stem}", value=value, section=section, authority=authority,  # type: ignore[arg-type]
                kind="memory_note", trust="untrusted", source=note.relative_to(self.root).as_posix(),
                freshness_class=freshness, stale_after=stale_after,  # type: ignore[arg-type]
                retrieved_by="g6.context.Workspace", included_because="note matched the task",
                token_estimate=estimate_tokens(value), metadata={"run_id": run_id}))
            if len(items) >= limit:
                break
        return items


__all__ = ["Workspace", "budget_ratio"]
