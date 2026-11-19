# SPDX-License-Identifier: Apache-2.0
"""One exact reader for reliability states; no spelling or prefix inference."""

STATES = ("verified", "qualified-draft", "blocked-escalated")
UNKNOWN = "unknown"


def state_of(value: object) -> str:
    """Read an exact canonical value, otherwise return unknown."""
    return value if isinstance(value, str) and value in STATES else UNKNOWN
