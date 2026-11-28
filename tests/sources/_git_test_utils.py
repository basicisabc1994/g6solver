# SPDX-License-Identifier: Apache-2.0
"""Decode the git process boundary without assuming a fixed option prefix."""
from collections.abc import Sequence


def git_command(argv: Sequence[str]) -> tuple[str, ...]:
    """Subcommand and arguments after the working directory selected by -C."""
    return tuple(argv[argv.index("-C") + 2:])
