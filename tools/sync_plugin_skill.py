# SPDX-License-Identifier: Apache-2.0
"""Keep the Claude Code plugin's guide and LangExtract bundle equal to G6's own.

``plugins/g6/skills/using-g6/`` ships ``SKILL.md`` and ``references/`` byte for byte as ``src/g6/skills/using-g6/`` has
them (the plugin needs only the skill and its references, not ``routing.json``). ``--check`` lists each difference and
exits 1 if there is one; ``--write`` makes the copy equal. ``tests/skills/test_plugin_skill.py`` runs the check.
The complete LangExtract bundle, including its licence and provenance, also ships under ``plugins/g6/vendor/``.

usage: python tools/sync_plugin_skill.py --check | --write
"""
from __future__ import annotations

import sys
from pathlib import Path

from g6.skills.install import KEPT, MARKER, MISSING, install_files, recover_installs, source_files, with_records

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "g6" / "skills" / "using-g6"
SHIPPED = ROOT / "plugins" / "g6" / "skills" / "using-g6"
BUNDLE_SOURCE = ROOT / "src/g6/skills/vendor/langextract"
BUNDLE_SHIPPED = ROOT / "plugins/g6/vendor/langextract"


def _files(folder: Path, *, source: bool, whole: bool = False) -> dict[str, Path]:
    if source:
        if not folder.exists():
            return {}
        files = source_files(folder)
        return {name: path for name, path in files.items()
                if name != MARKER and (whole or name == "SKILL.md" or name.startswith("references/"))}
    found = list(folder.rglob("*"))
    return {p.relative_to(folder).as_posix(): p for p in found if p.is_file() and "__pycache__" not in p.parts
            and p.relative_to(folder).as_posix() != MARKER}


def problems(source: Path = SOURCE, shipped: Path = SHIPPED, *, whole: bool = False) -> list[str]:
    """Each way the shipped copy differs from the source, sorted; ``[]`` when they are equal."""
    want = _files(source, source=True, whole=whole)
    if whole and not want:
        return [f"{MISSING}: source bundle is missing or empty"]
    if whole:
        try:
            want = with_records(want, source)
        except OSError as exc:
            return [str(exc)]
    have = _files(shipped, source=False) if shipped.is_dir() else {}
    out = [f"{name} is missing" for name in want if name not in have]
    out += [f"{name} differs" for name in want if name in have and have[name].read_bytes() != want[name].read_bytes()]
    out += [f"{name} is not in the source" for name in have if name not in want]
    return sorted(out)


def write(source: Path = SOURCE, shipped: Path = SHIPPED, *, whole: bool = False) -> str:
    """Copy the source, replacing only an unchanged earlier G6 installation."""
    recover_installs(shipped)
    files = _files(source, source=True, whole=whole)
    if whole and not files:
        raise FileNotFoundError(f"{MISSING}: source bundle is missing or empty")
    if whole:
        files = with_records(files, source)
    identity = f"plugin/{'bundle' if whole else 'guide'}/{source.name}"
    code = install_files(files, shipped, skill_id=identity)
    detail = "left as is; personal or edited skill" if code == KEPT else "installed"
    print(f"{shipped.name}: {code}: {detail}")
    return code


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--write"]:
        failed = False
        for source, shipped, whole in ((SOURCE, SHIPPED, False), (BUNDLE_SOURCE, BUNDLE_SHIPPED, True)):
            try:
                write(source, shipped, whole=whole)
            except OSError as exc:
                print(f"G6_SKILL_INSTALL_FAILED: {exc}", file=sys.stderr)
                failed = True
        return 1 if failed else 0
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
