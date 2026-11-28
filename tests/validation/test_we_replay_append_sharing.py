# SPDX-License-Identifier: Apache-2.0
"""All source open sites coexist with harness appends; partial tails are deferred."""
from __future__ import annotations

import json
import os
import builtins
from contextlib import contextmanager

import pytest

from worked_examples import replay_rc as rc

ROW = {"event": "started", "kind": "run", "task": "black-5634", "run": "run/black-5634/001"}


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes")
@pytest.mark.parametrize("site", ["index", "content", "score"])
@pytest.mark.parametrize("writer_first", [False, True])
def test_every_source_open_site_coexists_with_archive_append(tmp_path, monkeypatch, capsys, site, writer_first):
    archive = rc.c.Archive(tmp_path)
    archive.append(ROW)
    content = tmp_path / "content.txt"
    content.write_bytes(b"payload")
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    score = tmp_path / "score.json"
    score.write_text(json.dumps({"records": [], "plan": {"cases": [], "modes": ["hook"],
                          "registered_commit": "a" * 40}, "image_ids": {}, "s0": "before",
                          "registration_sha256": registration.sha256}), encoding="utf-8")
    target = archive.index if site == "index" else content if site == "content" else score
    # Point the real writer at each synthetic input to exercise both directions
    # of the same-file sharing contract. Append after the reader takes its bytes.
    monkeypatch.setattr(type(archive), "index", property(lambda self: target))
    original = rc._open_handle
    held = []

    def read():
        if site == "index":
            assert rc.Source(tmp_path).rows() == [ROW]
        elif site == "content":
            assert rc.Source(tmp_path).read_bytes(content.name) == b"payload"
        else:
            assert rc.main(["--score", str(score)]) == 2
            capsys.readouterr()

    @contextmanager
    def opened(path, **kwargs):
        with original(path, **kwargs) as handle:
            if kwargs.get("readable") and path == target:
                held.append(path)
            yield handle
            if kwargs.get("readable") and path == target and not writer_first:
                archive.append({"event": "other"})

    monkeypatch.setattr(rc, "_open_handle", opened)
    if writer_first:
        original_open = builtins.open

        @contextmanager
        def writer_open(path, mode="r", *args, **kwargs):
            with original_open(path, mode, *args, **kwargs) as stream:
                if path == target and mode == "a":
                    read()  # inside the actual Archive.append open, before its write
                yield stream

        monkeypatch.setattr(builtins, "open", writer_open)
        archive.append({"event": "other"})
    else:
        read()
    assert held == [target]


@pytest.mark.parametrize("tail", [b'{"event":', b'{"text":"\xe2\x82'])
def test_index_defers_unfinished_json_and_utf8_tails(tmp_path, tail):
    archive = rc.c.Archive(tmp_path)
    archive.append(ROW)
    with open(archive.index, "ab") as writer:
        writer.write(tail)
        writer.flush()
        assert rc.Source(tmp_path).rows() == [ROW]


def test_complete_final_json_record_without_newline_is_kept(tmp_path):
    (tmp_path / "runs.jsonl").write_bytes(json.dumps(ROW).encode())
    assert rc.Source(tmp_path).rows() == [ROW]


def test_malformed_terminated_line_still_refuses(tmp_path):
    (tmp_path / "runs.jsonl").write_bytes(b'{"event":\n')
    with pytest.raises(ValueError):
        rc.Source(tmp_path).rows()
