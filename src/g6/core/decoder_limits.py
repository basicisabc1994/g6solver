# SPDX-License-Identifier: Apache-2.0
"""Compatibility exports for the standard-library-only guarded decoder."""
from g6._decoder_limits import ListOf, UndecodableRecord, decode, json_value, shaped

__all__ = ["ListOf", "UndecodableRecord", "decode", "json_value", "shaped"]
