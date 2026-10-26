# SPDX-License-Identifier: Apache-2.0
"""G6's own files name one copyright holder, The G6 authors, and none reserves the rights the Apache-2.0 licence grants.

The owner chose the holder text on 2026-10-26 (DECISIONS #23). Before that, the files ported from the archive carried a
line above their SPDX line that named a company and said "All rights reserved", which contradicts the licence. Files
from elsewhere keep their own notices: the vendored skills and the graph viewer's bundled libraries."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

OSS = Path(__file__).resolve().parents[2]
HOLDER = "Copyright (c) 2026 The G6 authors"
#: A copyright line in a file's comment header.
COPYRIGHT = re.compile(r"^\s*(?:#|//|/?\*|<!--)\s*(Copyright\b.*?)\s*(?:-->|\*/)?\s*$", re.IGNORECASE)
THIRD_PARTY = ("src/g6/skills/vendor/", "web/static/graph/app/")
COMMENT = ("#", "//", "/*", "*", "<!--")
HEAD_LINES = 5


def _own_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=OSS, capture_output=True, check=True).stdout
    return [p for p in listed.decode("utf-8").split("\0") if p and not p.startswith(THIRD_PARTY)]


def _head(rel: str) -> list[str]:
    try:
        with open(OSS / rel, encoding="utf-8") as handle:
            return [handle.readline() for _ in range(HEAD_LINES)]
    except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
        return []


def test_every_own_copyright_line_names_the_g6_authors() -> None:
    wrong = [f"{rel}:{n}: {line.strip()}" for rel in _own_files() for n, line in enumerate(_head(rel), 1)
             if (found := COPYRIGHT.match(line)) and found.group(1) != HOLDER]
    assert wrong == [], f"{len(wrong)} header lines name another holder or reserve rights:\n" + "\n".join(wrong[:20])


def test_no_own_file_header_reserves_all_rights() -> None:
    reserved = [rel for rel in _own_files() if any(line.lstrip().startswith(COMMENT) and "all rights reserved" in
                                                   line.lower() for line in _head(rel))]
    assert reserved == [], f"{len(reserved)} files: " + ", ".join(reserved[:20])


def test_notice_names_the_g6_authors() -> None:
    lines = (OSS / "NOTICE").read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["G6", HOLDER]
