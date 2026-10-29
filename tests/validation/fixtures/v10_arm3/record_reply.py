# SPDX-License-Identifier: Apache-2.0
"""Records one arm-3 reply (V9's ``arms.g6_review``) on a development fixture, for V10's runner tests: a tiny
repository whose patch breaks the function its stated check exercises. Never task data.

Run in the Linux container the confined tests use, with G6's source at the verification search commit first on the
import path and bubblewrap available:

    PYTHONPATH=<g6 source>/src python record_reply.py <validation folder> <reply.json>

The fixture is the one ``tests/validation/test_v10_runner.py`` rebuilds (``FILES``, ``PATCH``, ``CHECK``).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

FILES = {"calc.py": "def add(a, b):\n    return a + b\n",
         "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}
PATCH = {"calc.py": "def add(a, b):\n    return a - b\n"}
CHECK = "tests/test_calc.py::test_add"
STATEMENT = "Addition returns the sum of its two arguments."


def main(argv: list[str]) -> int:
    sys.path.insert(0, argv[1])
    from patch_review_utility import arms, contract

    with tempfile.TemporaryDirectory(prefix="v10-arm3-") as scratch:
        root = Path(scratch) / "fixture"
        for rel, text in FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")

        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-qm", "development fixture")
        for rel, text in PATCH.items():
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        found = arms.g6_review(root, contract.split(STATEMENT), [CHECK], timeout_s=120)
    Path(argv[2]).write_text(json.dumps(found, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
