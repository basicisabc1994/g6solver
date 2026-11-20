"""Capture a host interpreter without adding candidate paths or importing candidates.

Run ``python -m validation.distill.pilot.m1b.environment --python <interpreter>``
and save stdout as the environment JSON supplied to A6. The subprocess uses -I;
the loaded snapshot follows the runner's imports before any inventory tooling.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from .fit import Environment

FIELDS = ("resolvable_modules", "loaded_packages", "pytest_dependencies")
PROBE = r'''
import json, os, socket, sys
import pytest
loaded = sorted({name.split('.')[0] for name in sys.modules})

import importlib.metadata
import importlib.util
import pkgutil

names = set(sys.builtin_module_names)
names.update(module.name for module in pkgutil.iter_modules())
names.update(importlib.metadata.packages_distributions())
resolvable = []
for name in sorted(names):
    if not name.isidentifier():
        continue
    try:
        if importlib.util.find_spec(name) is not None:
            resolvable.append(name)
    except (ImportError, AttributeError, ValueError):
        pass
print(json.dumps({'resolvable_modules': resolvable, 'loaded_packages': loaded,
                  'pytest_dependencies': loaded}, sort_keys=True))
'''


def from_json(body: dict) -> Environment:
    """Read only the three top-level name sets consumed by the frozen filter."""
    if not isinstance(body, dict):
        raise ValueError("invalid_environment")
    values = []
    for key in FIELDS:
        names = body.get(key)
        if not isinstance(names, list) or any(not isinstance(n, str) or not n.isidentifier() for n in names):
            raise ValueError("invalid_environment")
        values.append(frozenset(names))
    return Environment(*values)


def public(environment: Environment) -> dict:
    return {key: sorted(getattr(environment, key)) for key in FIELDS}


def capture(python: str = sys.executable, *, runner=subprocess.run) -> Environment:
    """Inventory only the selected host; neither cwd nor PYTHONPATH enters -I."""
    try:
        result = runner([str(python), "-I", "-c", PROBE], capture_output=True, text=True,
                        encoding="utf-8", timeout=120, check=False)
        if result.returncode:
            raise ValueError("child_failed")
        return from_json(json.loads(result.stdout))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        raise ValueError("environment_capture_failed") from None


def main(argv=None, *, runner=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(public(capture(args.python, runner=runner)), indent=2, sort_keys=True))
    except ValueError:
        parser.exit(2, "environment_capture_failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
