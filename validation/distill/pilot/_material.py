"""Read the worked-example image layout through an injected Docker runner."""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from validation.worked_examples.selection import is_test

from . import preflight

# Support mode includes ordinary fixed Python files for the case's import context.
EXTRACT = r'''
import json
import sys
from pathlib import Path, PurePosixPath

root = Path.cwd()
fixed_root = Path('/oracle/fixed')
status = Path('/oracle/fix.files').read_text(encoding='utf-8')
base, fixed = {}, {}
for row in status.splitlines():
    kind, name = row.split('\t')
    path = PurePosixPath(name)
    if (kind not in ('A', 'M', 'D') or path.is_absolute() or '..' in path.parts
            or path.as_posix() != name or '\\' in name):
        raise ValueError('unsupported fix path')
    if path.suffix != '.py':
        continue
    for folder, values, absent in ((root, base, 'A'), (fixed_root, fixed, 'D')):
        if kind == absent:
            continue
        source = folder / name
        if source.is_symlink() or not source.resolve().is_relative_to(folder.resolve()):
            raise ValueError('linked fix path')
        if not source.is_file() or source.stat().st_size > 131072:
            raise ValueError('missing or large source')
        values[name] = source.read_text(encoding='utf-8')
if sys.argv[1:] == ['support']:
    for source in sorted(fixed_root.rglob('*.py')):
        name = source.relative_to(fixed_root).as_posix()
        if any(part.startswith('.') for part in PurePosixPath(name).parts):
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(fixed_root.resolve()):
            raise ValueError('linked support path')
        if not source.is_file() or source.stat().st_size > 131072:
            raise ValueError('missing or large support source')
        fixed[name] = source.read_text(encoding='utf-8')
        if sum(len(s.encode('utf-8')) for s in fixed.values()) > 1048576:
            raise ValueError('support source too large')
print(json.dumps({'name_status': status, 'patch': Path('/oracle/fix.patch').read_text(encoding='utf-8'),
                  'base': base, 'fixed': fixed}))
'''


class MaterialError(ValueError):
    pass


def extract(bug: dict, runner, *, include_support: bool = False) -> dict:
    rows = preflight.image_checks(bug, runner)
    if any(row["reason_code"] != "ok" for row in rows):
        raise MaterialError(next(row["reason_code"] for row in rows if row["reason_code"] != "ok"))
    args = ("support",) if include_support else ()
    result = preflight.invoke(runner, preflight.run_command(bug["oracle_image_id"], "python3", "-I", "-c", EXTRACT, *args))
    if result.returncode:
        raise MaterialError("extraction_failed")
    try:
        body = json.loads(result.stdout)
        if not isinstance(body["patch"], str) or not isinstance(body["name_status"], str):
            raise ValueError("invalid descriptors")
        for key in ("base", "fixed"):
            if not isinstance(body[key], dict) or any(not isinstance(p, str) or not isinstance(s, str)
                                                     for p, s in body[key].items()):
                raise ValueError("invalid sources")
        changed_files(body)
        return body
    except (ValueError, TypeError, KeyError):
        raise MaterialError("extraction_failed") from None


def changed_files(material: dict) -> list[str]:
    paths = []
    for row in material["name_status"].splitlines():
        kind, name = row.split("\t")
        path = PurePosixPath(name)
        if (kind not in {"A", "M", "D"} or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or "\\" in name or not name):
            raise MaterialError("extraction_failed")
        if name in paths:
            raise MaterialError("extraction_failed")
        paths.append(name)
    return paths


def targets(material: dict) -> list[str]:
    return [p for p in changed_files(material) if p.endswith(".py") and not is_test(p)]


def changed_lines(material: dict, target: str) -> tuple[int, ...]:
    """Added or replaced fixed-side lines, excluding unchanged hunk context and deletions."""
    active = False
    left = right = new_line = 0
    found = set()
    for line in material["patch"].splitlines():
        if left or right:
            if line.startswith("\\"):
                continue
            if line.startswith("+"):
                if active:
                    found.add(new_line)
                new_line += 1
                right -= 1
            elif line.startswith("-"):
                left -= 1
            elif line.startswith(" "):
                new_line += 1
                left -= 1
                right -= 1
            else:
                raise MaterialError("extraction_failed")
            if left < 0 or right < 0:
                raise MaterialError("extraction_failed")
        elif line.startswith("+++ "):
            active = line[4:] == f"b/{target}"
        elif line.startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not match:
                raise MaterialError("extraction_failed")
            left = int(match[2]) if match[2] is not None else 1
            right = int(match[4]) if match[4] is not None else 1
            new_line = int(match[3])
    if left or right:
        raise MaterialError("extraction_failed")
    if target in material["fixed"] and any(n > len(material["fixed"][target].splitlines()) for n in found):
        raise MaterialError("extraction_failed")
    return tuple(sorted(found))
