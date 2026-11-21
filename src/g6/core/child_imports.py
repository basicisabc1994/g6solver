# SPDX-License-Identifier: Apache-2.0
"""Explicit imports for trusted G6 children, including uninstalled source checkouts.

This is an import bootstrap, not an operating-system boundary. Standalone untrusted-code sandboxes keep their own
bootstraps. The child runs this file itself, by its resolved path, under ``-I`` or ``-P``: Python then puts neither
the current directory nor this file's folder on the import path, and the file puts the G6 root it lives in just ahead
of the installed packages. An isolated worker (``-I``) also ignores PYTHONPATH; a registered client's child (``-P``)
keeps the person's own PYTHONPATH and user site-packages, which the person sets, not the project.

Nothing from G6 is imported here before the root is in place, so an installed copy of G6 cannot answer first.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def python_args(target: str, *, mode: str = "-m", isolated: bool = True) -> list[str]:
    """What follows the interpreter to run a trusted module (or fixed ``-c`` program) from the parent's resolved G6
    package, for a caller that names its interpreter itself.

    Isolated workers keep ``-I``; registered clients keep ``-P``. The file is one argument, so separators and spaces
    in its path cannot become extra import roots, and the command holds no program text of its own.
    """
    if mode not in ("-m", "-c"):
        raise ValueError("a G6 child runs a module or fixed Python code")
    return ["-I" if isolated else "-P", str(Path(__file__).resolve()), mode, target]


def python_command(target: str, *, mode: str = "-m", python: str | None = None,
                   isolated: bool = True) -> list[str]:
    """``python_args`` after ``python``, or this process's own interpreter."""
    return [python or sys.executable, *python_args(target, mode=mode, isolated=isolated)]


def _start() -> None:
    """In the child: put this file's G6 root just ahead of the installed packages (after the standard library and any
    PYTHONPATH), then run the module (``-m``) or fixed code (``-c``) that follows, as Python itself would."""
    import site

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sites = {*site.getsitepackages(), site.getusersitepackages()}
    sys.path.insert(next((i for i, entry in enumerate(sys.path) if entry in sites), len(sys.path)), root)
    mode, target = sys.argv[1:3]
    del sys.argv[1:3]
    if mode == "-m":
        import runpy

        runpy.run_module(target, run_name="__main__", alter_sys=True)
    else:
        sys.argv[0] = "-c"
        exec(compile(target, "<string>", "exec"), vars(sys.modules["__main__"]))


if __name__ == "__main__":
    _start()
