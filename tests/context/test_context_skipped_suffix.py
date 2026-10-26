# SPDX-License-Identifier: Apache-2.0
"""An empty retrieval says how many files the retriever skipped for their suffix, which suffixes it reads, and how to
read others.

The retriever reads only ``DEFAULT_SUFFIXES`` unless it is given ``suffixes``. Before, a folder of ``.log`` files gave
an empty retrieval whose message said only that nothing matched the query, as if the logs had been searched."""
from __future__ import annotations

from pathlib import Path

from g6.context import Retriever, build_packet
from g6.context.retrieve import DEFAULT_SUFFIXES


def _empty_message(packet) -> str:
    (message,) = [e["message"] for e in packet.evidence if e["code"] == "G6_E_CONTEXT_EMPTY_RETRIEVAL"]
    return message


def test_files_skipped_for_their_suffix_are_counted_and_named_in_the_empty_retrieval(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_text("parse_timeout happened at 12:00\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("parse_timeout again\n", encoding="utf-8")
    retriever = Retriever(tmp_path)
    retrieval = retriever.retrieve("parse_timeout")
    assert retrieval.candidates == () and retrieval.skipped_suffix == 2
    assert retrieval.summary()["skipped_suffix"] == 2
    message = _empty_message(build_packet("parse_timeout", retriever, 4000))
    assert "2 file(s)" in message and "suffix" in message and "suffixes" in message
    assert all(suffix in message for suffix in DEFAULT_SUFFIXES)


def test_survival_with_the_suffix_added_the_same_files_are_read(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_text("parse_timeout happened at 12:00\n", encoding="utf-8")
    retriever = Retriever(tmp_path, suffixes={*DEFAULT_SUFFIXES, ".log"})
    packet = build_packet("parse_timeout", retriever, 4000)
    assert [h.file_path for h in packet.repository_handles] == ["app.log"]
    assert retriever.retrieve("parse_timeout").skipped_suffix == 0
    assert "G6_E_CONTEXT_EMPTY_RETRIEVAL" not in {e["code"] for e in packet.evidence}


def test_an_empty_retrieval_with_nothing_skipped_says_so(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def unrelated():\n    pass\n", encoding="utf-8")
    message = _empty_message(build_packet("parse_timeout", Retriever(tmp_path), 4000))
    assert "nothing matched the query" in message and "no file was skipped for its suffix" in message
