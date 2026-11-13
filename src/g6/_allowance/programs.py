# SPDX-License-Identifier: Apache-2.0
"""Where a program this feature starts is: an absolute path, never the working directory's.

Windows looks for a bare program name in the working directory before anywhere
else, and so does shutil.which there. A status line renders in whatever folder
the person has open, so a file of that folder must never be started as a
program. Here a bare name is looked for in PATH's folders one at a time, and an
entry that is empty, "." or relative is passed over: it names the working
directory too. A path is started as given only when it names its own root.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Mapping


def absolute(path: str) -> bool:
    """A path that names its own root; on Windows, its drive or share as well."""
    return os.path.isabs(path) and (os.name != "nt" or bool(os.path.splitdrive(path)[0]))


def program(command: str, *, environ: Mapping[str, str] | None = None) -> str:
    """The absolute path to start for ``command``; OSError when no absolute place holds it."""
    if not command or "\x00" in command:
        raise FileNotFoundError("no program is named")
    if os.path.dirname(command):
        if absolute(command):
            return command  # the person's own setting, or this interpreter: started as given
        raise FileNotFoundError(f"{command} is a relative path, so it is not started")
    for folder in (os.environ if environ is None else environ).get("PATH", "").split(os.pathsep):
        # A name joined to an absolute folder is looked for in that folder only.
        if absolute(folder) and (found := shutil.which(os.path.join(folder, command))):
            return found
    raise FileNotFoundError(f"no {command} in an absolute folder of PATH, and the working directory is never searched")
