# SPDX-License-Identifier: Apache-2.0
"""The frontier programme's data: one durable folder outside the repo, each file hashed and its licence recorded."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from urllib.parse import parse_qs, urlparse

import pytest

from frontier import data


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_FRONTIER_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def _no_download(url):
    raise AssertionError(f"downloaded {url}")


def _manifest(root):
    return json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))


def test_data_root_reads_the_environment(root):
    assert data.data_root() == root


def test_data_root_defaults_to_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("G6_FRONTIER_DATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert data.data_root() == tmp_path / "g6_frontier_data"


def test_fetch_copies_from_a_source_folder_and_records_hashes_and_licence(root, tmp_path):
    source = tmp_path / "v6b"
    (source / "gsm8k").mkdir(parents=True)
    (source / "gsm8k" / "test.jsonl").write_bytes(b'{"question": "1+1?", "answer": "#### 2"}\n')
    (source / "gsm8k" / "LICENSE").write_bytes(b"MIT License\n")

    entry = data.fetch("gsm8k", source=source, get=_no_download)

    body = (root / "gsm8k" / "test.jsonl").read_bytes()
    assert entry["files"]["gsm8k/test.jsonl"] == hashlib.sha256(body).hexdigest()
    assert entry["licence"] == "MIT"
    assert _manifest(root)["gsm8k"] == entry


def test_fetch_downloads_what_the_source_folder_lacks(root, tmp_path):
    got = []

    def get(url):
        got.append(url)
        return b"downloaded\n"

    data.fetch("gsm8k", source=tmp_path / "empty", get=get)
    assert sorted(got) == sorted(data.DATASETS["gsm8k"].files.values())


def _tar(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_fetch_unpacks_only_the_named_members(root):
    archive = _tar({"1.1/data/en-US.jsonl": b'{"id": "1"}\n', "1.1/data/de-DE.jsonl": b"{}\n",
                    "1.1/LICENSE": b"CC BY 4.0\n", "1.1/NOTICE.md": b"notice\n", "../outside.txt": b"x"})

    def get(url):
        return archive if url.endswith(".tar.gz") else b"readme\n"

    entry = data.fetch("massive", get=get)

    extracted = root / "massive" / "extracted" / "1.1"
    assert (extracted / "data" / "en-US.jsonl").read_bytes() == b'{"id": "1"}\n'
    assert not (extracted / "data" / "de-DE.jsonl").exists()
    assert not (root / "massive" / "outside.txt").exists() and not (root / "outside.txt").exists()
    assert "massive/extracted/1.1/data/en-US.jsonl" in entry["files"]


def test_fetch_refuses_an_archive_missing_a_named_member(root):
    archive = _tar({"1.1/LICENSE": b"CC BY 4.0\n"})
    with pytest.raises(ValueError, match="en-US.jsonl"):
        data.fetch("massive", get=lambda url: archive if url.endswith(".tar.gz") else b"readme\n")


def _rows_api(totals, *, short=None):
    """A stand-in for the datasets-server rows API: ``totals`` rows per split, pages of at most 100."""
    def get(url):
        q = parse_qs(urlparse(url).query)
        split, offset, length = q["split"][0], int(q["offset"][0]), int(q["length"][0])
        total = totals[split]
        end = min(offset + length, total)
        if split == short:
            end -= 1
        rows = [{"row_idx": i, "row": {"id": f"{split}-{i}", "question": "q", "answerKey": "A",
                                       "choices": {"text": ["x", "y"], "label": ["A", "B"]}}}
                for i in range(offset, end)]
        return json.dumps({"rows": rows, "num_rows_total": total}).encode()
    return get


def test_fetch_pages_through_the_arc_rows(root):
    entry = data.fetch("arc", get=_rows_api({"train": 150, "validation": 7, "test": 201}))

    lines = (root / "arc" / "ARC-Challenge-test.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == [f"test-{i}" for i in range(201)]
    assert set(entry["files"]) == {f"arc/ARC-Challenge-{s}.jsonl" for s in ("train", "validation", "test")}
    assert entry["licence"] == "CC BY-SA 4.0"


def test_fetch_refuses_a_short_arc_split(root):
    with pytest.raises(ValueError, match="validation"):
        data.fetch("arc", get=_rows_api({"train": 3, "validation": 7, "test": 2}, short="validation"))


def test_every_dataset_names_a_licence_and_a_citation():
    assert set(data.DATASETS) == {"gsm8k", "massive", "scifact", "mbpp", "arc", "banking77"}
    for spec in data.DATASETS.values():
        assert spec.licence and spec.citation
