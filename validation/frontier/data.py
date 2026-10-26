# SPDX-License-Identifier: Apache-2.0
"""The frontier programme's datasets: where they live, where they come from, and their licences.

Data lives outside the repository in ``data_root()`` (``$G6_FRONTIER_DATA``, default ``~/g6_frontier_data``), laid out
like V6b's data folder, so V6b's case readers work on it unchanged. ``fetch(key)`` copies each file from a folder that
already holds it (V6b's data folder) or downloads it, unpacks only the named archive members, and records every file's
SHA-256 with the licence and the sources in ``MANIFEST.json``. Files are read as data and never executed. Datasets are
never committed: rounds commit ids and these hashes only.

    python validation/frontier/data.py [--source <folder>] [key ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tarfile
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

MANIFEST = "MANIFEST.json"
ROWS_API = "https://datasets-server.huggingface.co/rows"
PAGE = 100                                   # the rows API's largest page


@dataclass(frozen=True)
class Dataset:
    licence: str
    citation: str
    files: Mapping[str, str] = field(default_factory=dict)       # path under data_root() -> the URL it comes from
    unpack: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # archive -> members, into <its folder>/extracted
    rows: tuple[str, str, tuple[str, ...]] | None = None          # datasets-server: (dataset, config, splits)


_POLYAI = "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master"
DATASETS: dict[str, Dataset] = {
    "gsm8k": Dataset(
        "MIT", "Cobbe et al. 2021, Training Verifiers to Solve Math Word Problems (openai/grade-school-math)",
        {"gsm8k/test.jsonl": "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/"
                             "test.jsonl",
         "gsm8k/LICENSE": "https://raw.githubusercontent.com/openai/grade-school-math/master/LICENSE"}),
    "massive": Dataset(
        "CC BY 4.0", "FitzGerald et al. 2022, MASSIVE: A 1M-Example Multilingual NLU Dataset (version 1.1)",
        {"massive/amazon-massive-dataset-1.1.tar.gz":
         "https://amazon-massive-nlu-dataset.s3.amazonaws.com/amazon-massive-dataset-1.1.tar.gz"},
        {"massive/amazon-massive-dataset-1.1.tar.gz": ("1.1/data/en-US.jsonl", "1.1/LICENSE", "1.1/NOTICE.md")}),
    "scifact": Dataset(
        "claims CC BY 4.0; corpus ODC-By 1.0", "Wadden et al. 2020, Fact or Fiction: Verifying Scientific Claims",
        {"scifact/data.tar.gz": "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"},
        {"scifact/data.tar.gz": ("data/claims_train.jsonl", "data/claims_dev.jsonl", "data/corpus.jsonl")}),
    "mbpp": Dataset(
        "CC BY 4.0", "Austin et al. 2021, Program Synthesis with Large Language Models (google-research/mbpp)",
        {"mbpp/mbpp.jsonl": "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl",
         "mbpp/README.md": "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/README.md"}),
    "arc": Dataset(
        "CC BY-SA 4.0", "Clark et al. 2018, Think you have Solved Question Answering? Try ARC (allenai/ai2_arc, "
                        "ARC-Challenge)",
        rows=("allenai/ai2_arc", "ARC-Challenge", ("train", "validation", "test"))),
    "banking77": Dataset(
        "CC BY 4.0", "Casanueva et al. 2020, Efficient Intent Detection with Dual Sentence Encoders "
                     "(PolyAI-LDN/task-specific-datasets)",
        {"banking77/train.csv": f"{_POLYAI}/banking_data/train.csv",
         "banking77/test.csv": f"{_POLYAI}/banking_data/test.csv",
         "banking77/categories.json": f"{_POLYAI}/banking_data/categories.json",
         "banking77/LICENSE": f"{_POLYAI}/LICENSE"}),
}


def data_root() -> Path:
    return Path(os.environ.get("G6_FRONTIER_DATA") or Path.home() / "g6_frontier_data")


def _get(url: str) -> bytes:
    """One GET, retried three times with a growing pause (the rows API rate-limits)."""
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "g6-frontier-data/0.1"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _unpack(archive: Path, members: tuple[str, ...]) -> list[str]:
    """Only the named members, through tarfile's ``data`` filter (no links, no paths outside the folder)."""
    dest = archive.parent / "extracted"
    with tarfile.open(archive) as tar:
        found = {m.name: m for m in tar.getmembers() if m.name in members}
        missing = [m for m in members if m not in found]
        if missing:
            raise ValueError(f"{archive.name}: no member {', '.join(missing)}")
        tar.extractall(dest, members=[found[m] for m in members], filter="data")
    return [f"{archive.parent.name}/extracted/{m}" for m in members]


def _rows(root: Path, key: str, rows: tuple[str, str, tuple[str, ...]], get: Callable[[str], bytes]) -> list[str]:
    """Every row of each split, in row order, one JSON object per line; a split that comes back short is refused."""
    dataset, config, splits = rows
    written = []
    for split in splits:
        out, total = [], None
        while total is None or len(out) < total:
            query = urlencode({"dataset": dataset, "config": config, "split": split, "offset": len(out),
                               "length": PAGE})
            page = json.loads(get(f"{ROWS_API}?{query}"))
            total = page["num_rows_total"]
            got = [r["row"] for r in page["rows"]]
            if not got and len(out) < total:
                raise ValueError(f"{key} {split}: {len(out)} rows of {total}")
            out += got
        rel = f"{key}/{config}-{split}.jsonl"
        _write(root / rel, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out).encode("utf-8"))
        written.append(rel)
    return written


def fetch(key: str, *, source: Path | None = None, get: Callable[[str], bytes] | None = None) -> dict:
    """Put dataset ``key`` in ``data_root()``: each file copied from ``source`` when it holds it, else downloaded; then
    the named archive members unpacked and the rows API paged through. Returns the manifest entry it records."""
    spec, root, get = DATASETS[key], data_root(), get or _get
    hashed = []
    for rel, url in spec.files.items():
        have = source / rel if source else None
        _write(root / rel, have.read_bytes() if have and have.is_file() else get(url))
        hashed.append(rel)
    for archive, members in spec.unpack.items():
        hashed += _unpack(root / archive, members)
    if spec.rows:
        hashed += _rows(root, key, spec.rows, get)
    entry = {"licence": spec.licence, "citation": spec.citation,
             "sources": list(spec.files.values()) or [f"{ROWS_API}?dataset={spec.rows[0]}&config={spec.rows[1]}"],
             "files": {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in sorted(hashed)},
             "fetched": dt.date.today().isoformat()}
    path = root / MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    manifest[key] = entry
    _write(path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("keys", nargs="*", default=list(DATASETS))
    ap.add_argument("--source", type=Path, help="a folder already holding some files (V6b's data folder)")
    args = ap.parse_args(argv)
    for key in args.keys:
        entry = fetch(key, source=args.source)
        print(f"{key}: {len(entry['files'])} files, {entry['licence']}")
    print(data_root())
    return 0


if __name__ == "__main__":
    sys.exit(main())
