"""Refuse a release tag that does not match the stable version in the source tree."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


def check(tag: str, version: str) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or tag != f"v{version}":
        raise ValueError(f"Release tag {tag!r} must match a stable source version; found {version!r}")


def main(tag: str) -> None:
    source = Path(__file__).resolve().parents[1] / "src/g6/__init__.py"
    versions = [ast.literal_eval(node.value) for node in ast.parse(source.read_text(encoding="utf-8")).body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
    if len(versions) != 1 or not isinstance(versions[0], str):
        raise ValueError("Expected exactly one literal source version")
    check(tag, versions[0])
    print(f"Release tag matches source version: {tag}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python tools/check_release.py vX.Y.Z")
    try:
        main(sys.argv[1])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
