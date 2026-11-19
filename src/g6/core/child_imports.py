# SPDX-License-Identifier: Apache-2.0
"""Explicit imports for trusted G6 children, including uninstalled source checkouts.

This is an import bootstrap, not an operating-system boundary. Standalone untrusted-code sandboxes keep their own
bootstraps. Neither the current directory nor PYTHONPATH supplies this child's G6 package.
"""
from __future__ import annotations

import sys
from pathlib import Path

import g6

_BOOTSTRAP = (
    "import sys, runpy, site; "
    "root = sys.argv.pop(1); "
    "sites = [*site.getsitepackages(), site.getusersitepackages()]; "
    "at = next((i for i, p in enumerate(sys.path) if p in sites), len(sys.path)); "
    "sys.path.insert(at, root); "
    "mode = sys.argv.pop(1); target = sys.argv.pop(1); sys.argv[0] = target; "
    "runpy.run_module(target, run_name='__main__', alter_sys=True) if mode == '-m' "
    "else exec(compile(target, 'g6_child', 'exec'))"
)


def python_command(target: str, *, mode: str = "-m", python: str | None = None,
                   isolated: bool = True) -> list[str]:
    """Run a trusted module (or fixed ``-c`` program) from the parent's resolved G6 package.

    Isolated workers keep ``-I``. Registered clients keep ``-P`` and ignore Python startup environment variables with
    ``-E``; the source root is one argument, so separators and spaces in it cannot become extra import roots.
    """
    if mode not in ("-m", "-c"):
        raise ValueError("a G6 child runs a module or fixed Python code")
    root = str(Path(g6.__file__).resolve().parent.parent)
    return [python or sys.executable, *(["-I"] if isolated else ["-P", "-E"]),
            "-c", _BOOTSTRAP, root, mode, target]
