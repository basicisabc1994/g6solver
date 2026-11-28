# SPDX-License-Identifier: Apache-2.0
"""A deliberately weak classifier for the synthetic repository."""


def classify(message: str) -> str:
    text = message.lower()
    if "refund" in text:
        return "refund"
    if "deliver" in text:
        return "delivery"
    return "other"