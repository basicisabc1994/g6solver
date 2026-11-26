# SPDX-License-Identifier: Apache-2.0
"""Keep guarded decoders inside their consumers' import boundaries equal to one source.

Only ``src/g6/_decoder_limits.py`` is edited by hand. Each registered copy is its
exact bytes with one fixed source header prepended. The check also discovers
unregistered copies, including renamed or headerless implementations. Writing
repairs registered copies only; unknown copies remain an error for review.

usage: python tools/sync_decoder_limits.py --check | --write
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "src/g6/_decoder_limits.py"
GENERATED_HEADER = b"# Generated from src/g6/_decoder_limits.py; run tools/sync_decoder_limits.py --write.\n"
HEADER = b"# SPDX-License-Identifier: Apache-2.0\n" + GENERATED_HEADER
COPIES = (
    "src/g6/core/decoder_limits.py",
    "src/g6/research/mission_bridge/_decoder_limits.py",
    "src/g6/self_training/_decoder_limits.py",
)


def discover(root: Path = ROOT) -> list[str]:
    """Find copies by their source header, decoder filename, or implementation names."""
    found = []
    for path in sorted((root / "src/g6").rglob("*.py")):
        name = path.relative_to(root).as_posix()
        if name == SOURCE:
            continue
        body = path.read_bytes()
        if path.name in {"decoder_limits.py", "_decoder_limits.py"} or body.startswith((HEADER, GENERATED_HEADER)):
            found.append(name)
            continue
        tree = ast.parse(body, filename=name)
        definitions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        if {"decode", "json_value", "UndecodableRecord"} <= definitions:
            found.append(name)
    return found


def problems(root: Path = ROOT) -> list[str]:
    """List missing, drifted, and unregistered copies, without changing any file."""
    expected = HEADER + (root / SOURCE).read_bytes()
    out = []
    for name in COPIES:
        path = root / name
        if not path.is_file():
            out.append(f"{name} is missing")
        elif path.read_bytes() != expected:
            out.append(f"{name} differs")
    out += [f"{name} is not a registered copy" for name in discover(root) if name not in COPIES]
    return sorted(out)


def write(root: Path = ROOT) -> None:
    """Generate registered copies; never remove or accept an unknown copy."""
    expected = HEADER + (root / SOURCE).read_bytes()
    for name in COPIES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_bytes() != expected:
            path.write_bytes(expected)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (["--check"], ["--write"]):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    if args == ["--write"]:
        write(ROOT)
    found = problems(ROOT)
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
