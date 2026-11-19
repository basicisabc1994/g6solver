# SPDX-License-Identifier: Apache-2.0
"""Protected host entry point; never expose this module on learner invoke routes.

The host supplies an already-open handle to its sealed 32-byte evaluator secret.
It keeps that handle, secret and resulting capability outside learner mounts and
processes. ACCESS CONTROL is the binding barrier against held-out leakage; the
learner and proposer never hold this capability. Canonical screens and canaries
only detect accidental copies in their fixed views, without a claim about
deliberate adversarial obfuscation by a party controlling the encoding.
A role name cannot supply this authority. No file permissions are set here.
"""
from __future__ import annotations

import hmac
import json
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

    def commitment(self, domain: str, *values: object) -> str:
        """Domain-separated HMAC for public content commitments under the sealed secret."""
        message = json.dumps([domain, *values], sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hmac.digest(self.__key, b"g6-rsi-content-v2\0" + message.encode("utf-8"), "sha256").hex()


def evaluator_capability(sealed_secret: BinaryIO) -> EvaluatorCapability:
    """Mint authority only from the host's sealed secret, never a caller role."""
    key = sealed_secret.read(33)
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("G6_E_RSI_EVALUATOR_SECRET_INVALID")
    capability = object.__new__(EvaluatorCapability)
    capability._EvaluatorCapability__key = key
    return capability
