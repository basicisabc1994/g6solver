# SPDX-License-Identifier: Apache-2.0
"""Keeping a sound G6 computed: the one step every sound edit and every rendered sound ends with (adapt_audio,
adapt_synth). ``_``-prefixed, so it never registers as a component.

:func:`keep` refuses samples past full scale before anything is written (nothing is clipped silently), writes them as a
fresh WAV in the media folder, inspects it from disk (:func:`g6.media._wav.inspect_wav`: it decodes, has the rate,
channels and number of frames it must have, which the caller works out from the request and its sources, never from
the samples it made, and is not silent), reads it back and measures it (:func:`probe`: peak,
true peak, RMS, integrated loudness, length), runs the op's own checks on the file that was written (a loudness
target, a pitch), and only then gives it the name asked for, by :func:`g6.media._types.place`'s rule. Any failure
removes the file and is a refusal with the failed check's code.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from typing import Any

from g6.media import _dsp, _wav
from g6.media._types import (
    PROBED,
    Check,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    discard,
    media_artifact,
    media_file,
    media_root,
    place,
    refuse,
)


def probe(x: Any, rate: int) -> dict[str, Any]:
    """The levels reported before and after an edit."""
    found = _dsp.measure(x, rate)
    return {k: found[k] for k in ("duration_s", "rate", "channels", "frames", "peak_dbfs", "true_peak_dbtp",
                                  "rms_dbfs", "lufs")}


def keep(op: str, samples: Any, rate: int, bits: int, *, frames: int, channels: int, name: str, overwrite: bool,
         ids: dict[str, str], params: dict[str, Any], checks: Callable[[Any, dict[str, Any]], list[Check]] | None = None,
         before: dict[str, Any] | None = None, dither_seed: int | None = None, recipe: dict[str, Any] | None = None,
         seed: int | None = None, fields: dict[str, Any] | None = None, made: str = "") -> MediaResult:
    """``samples`` kept as the WAV ``name`` (``bits`` 16, 24 or 32), inspected against ``frames`` and ``channels``,
    what the request and its sources say the result holds; ``checks(written_samples, after)`` are the op's own; see
    the module docstring."""
    fields = dict(fields or {})
    clipped = _dsp.full_scale(samples)
    record = {"kind": "edit", "op": op, "params": params}
    first = [{"kind": "probe_before", **before}] if before else []
    if not clipped.passed:
        return refuse(op, clipped.code, clipped.detail, evidence=[*first, record], **fields, **ids)
    temp = media_root() / f".partial-{secrets.token_hex(8)}.wav"
    try:
        data = _dsp.wav_bytes(samples, rate, bits, dither_seed=dither_seed)
        final = media_file(name or f"{op}-{hashlib.sha256(data).hexdigest()[:12]}", ".wav")
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(data)
        found = _wav.inspect_wav(temp, rate=rate, channels=channels, duration=frames / rate, tolerance=0.5 / rate)
        after: dict[str, Any] = {}
        if found.passed:
            written, _, _ = _dsp.load(temp)
            after = probe(written, rate)
            own = [clipped, *(checks(written, after) if checks else [])]
            found = found.model_copy(update={"checks": (*found.checks, *own),
                                             "measured": {**found.measured, "lufs": after["lufs"]}})
        evidence = [*first, *([{"kind": "probe_after", **after}] if after else []), record]
        if not found.passed:
            discard(temp)
            return refuse(op, found.failure.code, found.failure.detail, evidence=[found.evidence(), *evidence],
                          **fields, **ids)
        placed = place(temp, final, overwrite=overwrite)
    except MediaError as exc:
        discard(temp)
        return refuse(op, exc.code, exc.reason, evidence=[*first, record], **fields, **ids)
    full = {"op": op, "params": params, "bits": bits, **(recipe or {})}
    provenance = Provenance(backend="numpy", backend_version=f"NumPy {_dsp.numpy().__version__}", technique=op,
                            seed=seed if seed is not None else dither_seed, recipe=full,
                            recipe_sha256=hashlib.sha256(json.dumps(full, sort_keys=True, default=str).encode())
                            .hexdigest())
    requested = {"rate": rate, "channels": channels, "frames": frames, "bits": bits}
    artifact = media_artifact(final, found, provenance, requested)
    said = (f"{made or op} written and inspected: a {bits}-bit WAV of {samples.shape[0] / rate:.3f} s, within full "
            f"scale, not silent; measured at {after['lufs']} LUFS and {after['true_peak_dbtp']} dBTP")
    return answer(op, said, artifact=artifact, card=PROBED, evidence=evidence, written=placed, **fields, **ids)


__all__ = ["keep", "probe"]
