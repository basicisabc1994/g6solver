# SPDX-License-Identifier: Apache-2.0
"""g6.integrity.tree — G6's own re-check of an integrity result, run at the door (plan A1, DECISIONS #40).

An :class:`~g6.integrity.IntegrityResult` that says matched and pinned is a claim anyone can construct. ``verified``
stands only when this re-check reproduces it against a pin the caller gave: it re-hashes the files under the root
now, compares them with the manifest, checks that the manifest's digest is the caller's pin, and plants two controls
the same code must catch (a perturbed digest in the manifest, and one file's bytes with one byte flipped, which must
hash differently through the same hash the manifest was built with). A check that misses a control vouches for
nothing, and a re-check that cannot read the files blocks rather than raising.

It is G6's re-checker for results produced by code in ``g6.integrity`` (``g6.core.reliability_envelope.
_G6_RECHECKERS``): ``claim`` takes the result's own record and the root and pin from the caller's input, and declines
a result that carries anything else; ``replay`` re-runs it. The execution identity it reports is the digest of the
manifest it rebuilt from the files, never one taken from the witness.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

import g6.integrity as integrity
from g6.core.promotion_evaluator import ReplayOutcome

ORACLE = "g6.integrity.tree"
DEFAULT_PATTERNS = ("**/*.py",)
#: The keys an integrity result has: anything else in a result is a claim this re-check does not examine.
RECORD = frozenset({f.name for f in fields(integrity.IntegrityResult)} | {"completion_state", "reliability_label"})


def _differences(actual: Mapping[str, str], listed: Mapping[str, str]) -> tuple[tuple[str, ...], ...]:
    """(missing, modified, unexpected): listed but absent, present with other bytes, present but not listed."""
    missing = tuple(sorted(set(listed) - set(actual)))
    modified = tuple(sorted(p for p in set(listed) & set(actual) if listed[p] != actual[p]))
    unexpected = tuple(sorted(set(actual) - set(listed)))
    return missing, modified, unexpected


def _perturbed(manifest: Mapping[str, str]) -> tuple[str, dict[str, str]]:
    """The manifest with one digest changed in its last hex digit, and the path it changed."""
    path = min(manifest)
    digest = manifest[path]
    changed = dict(manifest)
    changed[path] = digest[:-1] + ("0" if digest[-1:] != "0" else "1")
    return path, changed


def _hash_sees_content(root: str, listed: Mapping[str, str]) -> bool:
    """One listed file's bytes, and the same bytes with one byte flipped, hash differently through the manifest's own
    hash: a hash (or a manifest builder) that ignores content fails this."""
    path = min(listed)
    data = (Path(root) / path).read_bytes()
    flipped = data[:-1] + bytes([data[-1] ^ 1]) if data else b"\x00"
    return integrity._sha(data) != integrity._sha(flipped) and integrity._sha(data) == listed[path]


def recheck(root: str, manifest: Mapping[str, str], pin: str,
            patterns: Sequence[str] = DEFAULT_PATTERNS) -> tuple[str, str, str]:
    """Re-run the integrity check now: ``(label, why, identity)``.

    ``verified`` when the files match the manifest, the manifest's digest is the pin, and both planted controls are
    caught; ``blocked-escalated`` when the re-check sees files or a digest that do not match, or cannot read the
    files; ``qualified-draft`` when there is nothing to re-check (no root, manifest or pin) or a control was missed.
    ``identity`` is the digest of the manifest rebuilt from the files ("" when nothing was rebuilt). Never raises.
    """
    if not root or not manifest or not pin:
        return "qualified-draft", "nothing to re-check: no root, manifest and pin", ""
    listed = {str(k): str(v) for k, v in manifest.items()}
    try:
        if integrity.manifest_sha256(listed) != pin:
            return "blocked-escalated", "the manifest's digest is not the pinned one", ""
        actual = integrity.build_manifest(root, patterns=tuple(patterns))
        identity = integrity.manifest_sha256(actual)
        gaps = [f"{len(x)} file(s) {what}" for x, what in zip(_differences(actual, listed),
                                                             ("missing", "modified", "not in the manifest")) if x]
        if gaps:
            return "blocked-escalated", "the files no longer match the manifest: " + ", ".join(gaps), identity
        path, control = _perturbed(listed)
        if path not in _differences(actual, control)[1] or integrity.manifest_sha256(control) == pin:
            return ("qualified-draft", "the planted control (a perturbed digest) was not caught, so this check cannot "
                    "vouch for the files", identity)
        if not _hash_sees_content(root, listed):
            return ("qualified-draft", "the planted control (one file with one byte flipped) hashed the same, so this "
                    "check cannot vouch for the files", identity)
    except Exception as exc:  # noqa: BLE001 - a re-check that cannot read the files fails closed, never raises
        return "blocked-escalated", f"the re-check could not read the files: {type(exc).__name__}: {exc}", ""
    return "verified", "", identity


def _manifest(value: Any) -> dict[str, str] | None:
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        try:
            return {str(k): str(v) for k, v in value}
        except (TypeError, ValueError):
            return None
    return None


def _resolved(root: Any) -> str:
    try:
        return str(Path(str(root)).resolve()) if root else ""
    except (OSError, RuntimeError, ValueError):
        return ""


def claim(value: Mapping[str, Any], caller: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """What to re-check: ``(execution_id, details)``, or None.

    The manifest is the result's own record; the root, the pin (``expected_manifest_sha256``) and any ``patterns``
    come from ``caller``, the caller's own input, never from the result being judged. None when the caller gave no
    root and pin, when the result is about another root, or when the result carries a key an integrity result does
    not have (an answer, rows, evidence, a witness): the label would cover a claim this re-check does not examine.
    The execution identity the re-check must reproduce is the caller's pin.
    """
    if set(value) - RECORD:
        return None
    root, pin = _resolved(caller.get("root")), caller.get("expected_manifest_sha256")
    manifest = _manifest(value.get("manifest"))
    patterns = caller.get("patterns") or DEFAULT_PATTERNS
    if not (root and isinstance(pin, str) and pin and manifest):
        return None
    if value.get("root") and _resolved(value.get("root")) != root:
        return None
    if isinstance(patterns, str) or not all(isinstance(p, str) for p in patterns):
        return None
    return pin, {"subject": root, "root": root, "manifest": manifest, "expected_manifest_sha256": pin,
                 "patterns": list(patterns)}


def replay(witness: Mapping[str, Any]) -> ReplayOutcome:
    """The re-check, from a witness a door built with :func:`claim`."""
    details = witness.get("details")
    if not isinstance(details, Mapping):
        return ReplayOutcome(False, detail="the witness names nothing to re-check")
    manifest = _manifest(details.get("manifest")) or {}
    patterns = details.get("patterns") or DEFAULT_PATTERNS
    label, why, identity = recheck(str(details.get("root") or ""), manifest,
                                   str(details.get("expected_manifest_sha256") or ""), tuple(patterns))
    return ReplayOutcome(label == "verified", execution_id=identity, detail=why,
                         refuted=label == "blocked-escalated")


__all__ = ["ORACLE", "RECORD", "claim", "recheck", "replay"]
