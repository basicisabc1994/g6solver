# SPDX-License-Identifier: Apache-2.0
"""Study entry point for the shared installed implementation."""
import sys

from g6.distill.held_out import registration_gate as _implementation

# Both study import spellings share the same types, custody checks and function globals.
sys.modules[__name__] = _implementation
