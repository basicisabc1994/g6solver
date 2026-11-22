# SPDX-License-Identifier: Apache-2.0
"""Keep the Claude Code plugin's guide and LangExtract bundle equal to G6's own.

``plugins/g6/skills/using-g6/`` ships ``SKILL.md`` and ``references/`` byte for byte as ``src/g6/skills/using-g6/`` has
them (the plugin needs only the skill and its references, not ``routing.json``). ``--check`` lists each difference and
exits 1 if there is one; ``--write`` makes the copy equal. ``tests/skills/test_plugin_skill.py`` runs the check.
The complete LangExtract bundle, including its licence and provenance, also ships under ``plugins/g6/vendor/``.

usage: python tools/sync_plugin_skill.py --check | --write
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "g6" / "skills" / "using-g6"
SHIPPED = ROOT / "plugins" / "g6" / "skills" / "using-g6"
BUNDLE_SOURCE = ROOT / "src/g6/skills/vendor/langextract"
BUNDLE_SHIPPED = ROOT / "plugins/g6/vendor/langextract"


def _files(folder: Path, *, source: bool, whole: bool = False) -> dict[str, Path]:
    found = ([folder / "SKILL.md", *(folder / "references").rglob("*")]
             if source and not whole else list(folder.rglob("*")))
    return {p.relative_to(folder).as_posix(): p for p in found if p.is_file() and "__pycache__" not in p.parts}


def problems(source: Path = SOURCE, shipped: Path = SHIPPED, *, whole: bool = False) -> list[str]:
    """Each way the shipped copy differs from the source, sorted; ``[]`` when they are equal."""
    want = _files(source, source=True, whole=whole)
    if whole and not want:
        return ["source bundle is missing or empty"]
    have = _files(shipped, source=False) if shipped.is_dir() else {}
    out = [f"{name} is missing" for name in want if name not in have]
    out += [f"{name} differs" for name in want if name in have and have[name].read_bytes() != want[name].read_bytes()]
    out += [f"{name} is not in the source" for name in have if name not in want]
    return sorted(out)


def write(source: Path = SOURCE, shipped: Path = SHIPPED, *, whole: bool = False) -> None:
    """Make the shipped copy equal to the source."""
    files = _files(source, source=True, whole=whole)
    if whole and not files:
        raise FileNotFoundError("source bundle is missing or empty")
    if shipped.is_dir():
        shutil.rmtree(shipped)
    for name, path in files.items():
        target = shipped / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--write"]:
        write()
        write(BUNDLE_SOURCE, BUNDLE_SHIPPED, whole=True)
        return 0
    if args != ["--check"]:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    found = problems()
    bundles = problems(BUNDLE_SOURCE, BUNDLE_SHIPPED, whole=True)
    for shipped, lines in ((SHIPPED, found), (BUNDLE_SHIPPED, bundles)):
        for line in lines:
            print(f"{shipped.relative_to(ROOT).as_posix()}: {line}")
    return 1 if found or bundles else 0


if __name__ == "__main__":
    raise SystemExit(main())
