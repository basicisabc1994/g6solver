# SPDX-License-Identifier: Apache-2.0
"""The study's production environment: the pinned G6 toolchain (``toolchain.py``, ``toolchain.json``) that the arms
running G6 mount read-only into each task's own benchmark image, and the development check on V7's images
(``dev_check.py``). The task images are never modified.
"""
