# SPDX-License-Identifier: Apache-2.0
"""g6.integrity.tree — G6's own re-check of an integrity result, run at the door (plan A1, DECISIONS #40).

An :class:`~g6.integrity.IntegrityResult` that says matched and pinned is a claim anyone can construct. ``verified``
stands only when this re-check reproduces it: it re-hashes the files under the root now, compares them with the
manifest, checks that the manifest's digest is the pin, and plants one control (a perturbed digest in the manifest)
that the same comparison must catch. A comparison that misses the control vouches for nothing.

It is G6's re-checker for the ``integrity`` component (``g6.core.reliability_envelope._G6_RECHECKERS``): ``claim``
names what to re-check from a result's own content, and ``replay`` re-runs it. The execution identity it reports is
the digest of the manifest it rebuilt from the files, never one taken from the witness.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from g6.core.promotion_evaluator import ReplayOutcome
from g6.integrity import build_manifest, manifest_sha256

ORACLE = "g6.integrity.tree"
DEFAULT_PATTERNS = ("**/*.py",)


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


def recheck(root: str, manifest: Mapping[str, str], pin: str,
            patterns: Sequence[str] = DEFAULT_PATTERNS) -> tuple[str, str, str]:
    """Re-run the integrity check now: ``(label, why, identity)``.

    ``verified`` when the files match the manifest, the manifest's digest is the pin, and the planted control is
    caught; ``blocked-escalated`` when the re-check sees files or a digest that do not match; ``qualified-draft`` when
    there is nothing to re-check (no root, manifest or pin) or the comparison missed the control. ``identity`` is the
    digest of the manifest rebuilt from the files ("" when nothing was rebuilt).
    """
    if not root or not manifest or not pin:
        return "qualified-draft", "nothing to re-check: the result names no root, manifest and pin", ""
    listed = {str(k): str(v) for k, v in manifest.items()}
    if manifest_sha256(listed) != pin:
        return "blocked-escalated", "the manifest's digest is not the pinned one", ""
    actual = build_manifest(root, patterns=tuple(patterns))
    identity = manifest_sha256(actual)
    gaps = [f"{len(x)} file(s) {what}" for x, what in zip(_differences(actual, listed),
                                                         ("missing", "modified", "not in the manifest")) if x]
    if gaps:
        return "blocked-escalated", "the files no longer match the manifest: " + ", ".join(gaps), identity
    path, control = _perturbed(listed)
    if path not in _differences(actual, control)[1] or manifest_sha256(control) == pin:
        return ("qualified-draft", "the planted control (a perturbed digest) was not caught, so this check cannot "
                "vouch for the files", identity)
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


def claim(value: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """What to re-check, from an integrity result's own content: ``(execution_id, details)``, or None when the result
    names no root, manifest and pin. The execution identity it must reproduce is the pinned manifest digest."""
    root, pin = value.get("root"), value.get("expected_manifest_sha256")
    manifest = _manifest(value.get("manifest"))
    patterns = value.get("patterns") or DEFAULT_PATTERNS
    if not (isinstance(root, str) and root and isinstance(pin, str) and pin and manifest):
        return None
    if isinstance(patterns, str) or not all(isinstance(p, str) for p in patterns):
        return None
    return pin, {"root": root, "manifest": manifest, "expected_manifest_sha256": pin, "patterns": list(patterns)}


def replay(witness: Mapping[str, Any]) -> ReplayOutcome:
    """The re-check, from a witness the door built with :func:`claim`."""
    details = witness.get("details")
    if not isinstance(details, Mapping):
        return ReplayOutcome(False, detail="the witness names nothing to re-check")
    manifest = _manifest(details.get("manifest")) or {}
    patterns = details.get("patterns") or DEFAULT_PATTERNS
    label, why, identity = recheck(str(details.get("root") or ""), manifest,
                                   str(details.get("expected_manifest_sha256") or ""), tuple(patterns))
    return ReplayOutcome(label == "verified", execution_id=identity, detail=why)


__all__ = ["ORACLE", "claim", "recheck", "replay"]
