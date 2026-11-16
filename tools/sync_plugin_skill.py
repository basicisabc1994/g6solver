# SPDX-License-Identifier: Apache-2.0
"""Keep the Claude Code plugin's copy of the ``using-g6`` skill equal to G6's own.

``plugins/g6/skills/using-g6/`` ships ``SKILL.md`` and ``references/`` byte for byte as ``src/g6/skills/using-g6/`` has
them (the plugin needs only the skill and its references, not ``routing.json``). ``--check`` lists each difference and
exits 1 if there is one; ``--write`` makes the copy equal. ``tests/skills/test_plugin_skill.py`` runs the check.

usage: python tools/sync_plugin_skill.py --check | --write
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "g6" / "skills" / "using-g6"
SHIPPED = ROOT / "plugins" / "g6" / "skills" / "using-g6"


def _files(folder: Path, *, source: bool) -> dict[str, Path]:
    found = [folder / "SKILL.md", *(folder / "references").rglob("*")] if source else list(folder.rglob("*"))
    return {p.relative_to(folder).as_posix(): p for p in found if p.is_file()}


def problems(source: Path = SOURCE, shipped: Path = SHIPPED) -> list[str]:
    """Each way the shipped copy differs from the source, sorted; ``[]`` when they are equal."""
    want, have = _files(source, source=True), _files(shipped, source=False) if shipped.is_dir() else {}
    out = [f"{name} is missing" for name in want if name not in have]
    out += [f"{name} differs" for name in want if name in have and have[name].read_bytes() != want[name].read_bytes()]
    out += [f"{name} is not in the source" for name in have if name not in want]
    return sorted(out)


def write(source: Path = SOURCE, shipped: Path = SHIPPED) -> None:
    """Make the shipped copy equal to the source."""
    if shipped.is_dir():
        shutil.rmtree(shipped)
    for name, path in _files(source, source=True).items():
        target = shipped / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--write"]:
        write()
        return 0
    if args != ["--check"]:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    found = problems()
    for line in found:
        print(f"plugins/g6/skills/using-g6: {line}")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
