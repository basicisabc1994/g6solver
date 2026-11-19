# SPDX-License-Identifier: Apache-2.0
"""Operation effects are declarations, independent of operation names."""
from collections.abc import Mapping


def effects_of(module: object, op: str) -> tuple[str, ...]:
    """Read per-operation __g6_effects__; unknown is never assumed internal."""
    declared = getattr(module, "__g6_effects__", None)
    effects = declared.get(op) if isinstance(declared, Mapping) else None
    if not isinstance(effects, (tuple, list)) or not effects:
        return ("unknown",)
    if not all(isinstance(effect, str) and effect in ("internal", "outward") for effect in effects):
        return ("unknown",)
    return tuple(dict.fromkeys(effects))
