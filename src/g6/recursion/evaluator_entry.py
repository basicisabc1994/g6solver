# SPDX-License-Identifier: Apache-2.0
"""Protected host entry point; never expose this module on learner invoke routes.

The host supplies an already-open handle to its sealed 32-byte evaluator secret.
It keeps that handle, secret and resulting capability outside learner mounts and
processes. A role name cannot supply this authority. No file permissions are set here.
"""
from __future__ import annotations

import hmac
from pathlib import Path
from typing import BinaryIO


class EvaluatorCapability:
    """Opaque host authority; store bindings contain no signing key."""

    __slots__ = ("__key",)

    def __init__(self):
        raise TypeError("use the protected evaluator entry point")

    def binding(self, root: str | Path) -> str:
        message = b"g6-rsi-evaluator-v1\0" + str(Path(root).resolve()).encode("utf-8")
        return hmac.digest(self.__key, message, "sha256").hex()


def evaluator_capability(sealed_secret: BinaryIO) -> EvaluatorCapability:
    """Mint authority only from the host's sealed secret, never a caller role."""
    key = sealed_secret.read(33)
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("G6_E_RSI_EVALUATOR_SECRET_INVALID")
    capability = object.__new__(EvaluatorCapability)
    capability._EvaluatorCapability__key = key
    return capability
