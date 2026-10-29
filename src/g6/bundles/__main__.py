# SPDX-License-Identifier: Apache-2.0
"""``python -P -m g6.bundles [<id>]`` — start a bundle's local page; with no id, list the bundles."""
from __future__ import annotations

import sys

from g6.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["bundle", *sys.argv[1:]]))
