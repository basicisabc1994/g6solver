"""Review 3: the retained content handle's sharing mode against the archive's own writer.

Synthetic folders only: no container, no model, no real archive, no confined run.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from worked_examples import replay_rc as rc  # noqa: E402

ROW = {"event": "started", "kind": "run", "task": "black-5634", "run": "run/black-5634/001"}


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes")
def test_index_read_does_not_refuse_the_harness_writer(tmp_path):
    # The replay's --source is the worked examples' archive, the one run.py and loop.py append to.
    # While the replay holds runs.jsonl, the harness's own index append (wecommon.Archive.append,
    # used by every start and finish) must still succeed, as it does beside a plain read.
    archive = rc.c.Archive(tmp_path)
    archive.append(ROW)
    source = rc.Source(tmp_path)
    with rc._open_handle(source.path("runs.jsonl"), root=source.root, readable=True):
        archive.append({"event": "ended", "run": ROW["run"], "status": "finished"})
    assert len(archive.rows()) == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes")
def test_index_read_survives_a_harness_append_in_progress(tmp_path):
    # The other direction: a writer that has the index open for append (as Archive.append does)
    # must not turn the replay's read into a sharing violation.
    index = tmp_path / "runs.jsonl"
    index.write_text(json.dumps(ROW) + "\n", encoding="utf-8")
    with open(index, "a", encoding="utf-8", newline="\n"):
        assert rc.Source(tmp_path).rows() == [ROW]
