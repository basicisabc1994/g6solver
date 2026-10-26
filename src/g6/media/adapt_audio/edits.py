# SPDX-License-Identifier: Apache-2.0
"""What each adapt_audio op does to the samples, and the checks of its own it adds to the inspection.

Each edit takes the op's input, the samples (``(frames, channels)``, full scale 1.0) and the rate, and returns an
:class:`Edit`: the new samples, the rate and bit depth to write, the parameters used, and a function that reads the
written file's measurements and returns the op's own checks (a level change measured again against its target). Every
analysis returns its measurements. All of it is :mod:`g6.media._dsp`, NumPy only.
"""
from __future__ import annotations

import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from g6.media import _dsp, _wav
from g6.media._types import CLIPPED, INPUT_INVALID, LOUDNESS_MISSED, Check, MediaError


@dataclass
class Edit:
    """An edit's result. ``frames`` and ``channels`` are what the result must hold, worked out from the request and
    the source, never from ``samples`` (``None``: the source's own)."""

    samples: Any
    rate: int
    params: dict[str, Any] = field(default_factory=dict)
    checks: Callable[[dict[str, Any]], list[Check]] | None = None
    dither_seed: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    frames: int | None = None
    channels: int | None = None


def _db(value: float) -> float:
    return 10 ** (value / 20)


def peak_within(ceiling: float) -> Callable[[dict[str, Any]], list[Check]]:
    def check(after: dict[str, Any]) -> list[Check]:
        found = after["true_peak_dbtp"]
        ok = found <= ceiling + 0.1
        return [Check(name="true_peak_within_ceiling", passed=ok, code="" if ok else LOUDNESS_MISSED,
                      detail=f"true peak {found} dBTP, measured again" + ("" if ok else f"; the ceiling is {ceiling:g}"
                                                                                       " dBTP"))]
    return check


# -- analysis --------------------------------------------------------------------------------------------------------

def analyse(op: str, d: Any, x: Any, rate: int) -> dict[str, Any]:
    _dsp.numpy("measure the sound")
    if op == "loudness":
        found = _dsp.measure(x, rate)
        return {k: found[k] for k in ("lufs", "true_peak_dbtp", "peak_dbfs", "rms_dbfs", "duration_s")}
    if op == "crest_factor":
        found = _dsp.measure(x, rate)
        return {k: found[k] for k in ("crest_factor_db", "peak_dbfs", "true_peak_dbtp", "rms_dbfs")}
    if op == "phase_correlation":
        if x.shape[1] != 2:
            raise MediaError(INPUT_INVALID, f"phase correlation needs two channels, not {x.shape[1]}")
        return {"correlation": _dsp.correlation(x)}
    if op == "pitch":
        frequency, periodicity = _dsp.pitch(x, rate)
        if frequency is None:
            return {"frequency_hz": None, "note": None, "cents": None, "periodicity": periodicity}
        note, cents = _dsp.note_name(frequency)
        return {"frequency_hz": frequency, "note": note, "cents": cents, "periodicity": periodicity}
    if op == "bpm":
        bpm, strength = _dsp.tempo(x, rate)
        return {"bpm": bpm, "strength": strength}
    if op == "silence":
        found = _dsp.silences(x, rate, d.threshold_dbfs, d.min_silence)
        return {"silences": found, "silent_s": round(sum(s["end_s"] - s["start_s"] for s in found), 3),
                "threshold_dbfs": d.threshold_dbfs}
    if op == "spectrum":
        return _dsp.spectrum(x, rate)
    raise MediaError(INPUT_INVALID, f"no analysis named {op!r}")  # pragma: no cover - the input model lists them


# -- edits -----------------------------------------------------------------------------------------------------------

def trim(d: Any, x: Any, rate: int) -> Edit:
    start = int(round(d.start * rate))
    end = x.shape[0] if d.end is None else min(x.shape[0], int(round(d.end * rate)))
    if start >= end:
        raise MediaError(INPUT_INVALID, f"start {d.start:g} s is not before the end ({end / rate:.3f} s)")
    return Edit(x[start:end], rate, {"start_frame": start, "end_frame": end}, frames=end - start)


def fade(d: Any, x: Any, rate: int) -> Edit:
    np = _dsp.numpy()
    y = x.copy()
    for seconds, rising in ((d.fade_in, True), (d.fade_out, False)):
        n = min(x.shape[0], int(round(seconds * rate)))
        if n <= 0:
            continue
        t = np.linspace(0.0, 1.0, n)
        curve = np.sin(t * math.pi / 2) if d.curve == "equal_power" else t
        if rising:
            y[:n] *= curve[:, None]
        else:
            y[-n:] *= curve[::-1][:, None]
    return Edit(y, rate, {"fade_in": d.fade_in, "fade_out": d.fade_out, "curve": d.curve})


def gain(d: Any, x: Any, rate: int) -> Edit:
    return Edit(x * _db(d.gain_db), rate, {"gain_db": d.gain_db})


def resample(d: Any, x: Any, rate: int) -> Edit:
    if d.rate == rate:
        return Edit(x, rate, {"rate": rate, "unchanged": True})
    return Edit(_dsp.resample(x, rate, d.rate), d.rate, {"from": rate, "to": d.rate},
                frames=int(round(x.shape[0] * d.rate / rate)))


def convert(d: Any, x: Any, rate: int) -> Edit:
    np = _dsp.numpy()
    y = x
    if d.channels is not None and d.channels != x.shape[1]:
        if d.channels == 1:
            y = x.mean(axis=1, keepdims=True)
        elif x.shape[1] == 1:
            _dsp.fits(x.shape[0] * d.channels, f"the sound in {d.channels} channels")
            y = np.repeat(x, d.channels, axis=1)
        else:
            raise MediaError(INPUT_INVALID, f"{x.shape[1]} channels are made one (mono) or kept; not {d.channels}")
    return Edit(y, rate, {"channels": y.shape[1], "bits": d.bits}, channels=d.channels)


def dither(d: Any, x: Any, rate: int) -> Edit:
    seed = d.seed if d.seed is not None else secrets.randbits(32)
    return Edit(x, rate, {"bits": d.bits, "dither": "TPDF, one step", "seed": seed}, dither_seed=seed,
                fields={"seed": seed})


def eq(d: Any, x: Any, rate: int) -> Edit:
    sections = [_dsp.biquad(b.type, b.frequency_hz, rate, b.q, b.gain_db) for b in d.bands]
    return Edit(_dsp.filtered(x, rate, sections), rate, {"bands": [b.model_dump() for b in d.bands]})


def compress(d: Any, x: Any, rate: int) -> Edit:
    threshold = -20.0 if d.threshold_db is None else d.threshold_db
    y = _dsp.compress(x, rate, threshold, d.ratio, d.attack_ms, d.release_ms, d.knee_db, d.makeup_db)
    return Edit(y, rate, {"threshold_db": threshold, "ratio": d.ratio, "attack_ms": d.attack_ms,
                          "release_ms": d.release_ms, "knee_db": d.knee_db, "makeup_db": d.makeup_db})


def limit(d: Any, x: Any, rate: int) -> Edit:
    return Edit(_dsp.limit(x, rate, d.ceiling_dbtp, d.release_ms), rate,
                {"ceiling_dbtp": d.ceiling_dbtp, "release_ms": d.release_ms}, peak_within(d.ceiling_dbtp))


def gate(d: Any, x: Any, rate: int) -> Edit:
    threshold = -50.0 if d.threshold_db is None else d.threshold_db
    y = _dsp.gate(x, rate, threshold, d.floor_db, d.attack_ms, d.hold_ms, d.release_ms)
    return Edit(y, rate, {"threshold_db": threshold, "floor_db": d.floor_db, "attack_ms": d.attack_ms,
                          "hold_ms": d.hold_ms, "release_ms": d.release_ms})


def de_ess(d: Any, x: Any, rate: int) -> Edit:
    threshold = -30.0 if d.threshold_db is None else d.threshold_db
    y = _dsp.de_ess(x, rate, d.frequency_hz, threshold, d.reduction_db)
    return Edit(y, rate, {"frequency_hz": d.frequency_hz, "threshold_db": threshold, "reduction_db": d.reduction_db})


def noise_reduce(d: Any, x: Any, rate: int) -> Edit:
    np = _dsp.numpy()
    span = None
    if d.noise_end is not None:
        span = (int(round(d.noise_start * rate)), min(x.shape[0], int(round(d.noise_end * rate))))
    y, used = _dsp.reduce_noise(x, rate, span, d.reduction_db)

    def floor(z: Any) -> float:
        part = z[used[0]:used[1]]
        return _wav.dbfs(float(np.sqrt(np.mean(part ** 2)))) if part.size else _wav.FLOOR_DBFS

    noise = {"span_s": [round(used[0] / rate, 3), round(used[1] / rate, 3)], "before_dbfs": floor(x),
             "after_dbfs": floor(y)}
    return Edit(y, rate, {"reduction_db": d.reduction_db, "noise_span_s": noise["span_s"],
                          "noise_from": "the span given" if span else "the quietest tenth of the frames"},
                fields={"noise_floor": noise})


def normalise_loudness(d: Any, x: Any, rate: int) -> Edit:
    """Gain to ``target_lufs``; refused when that gain would put the true peak over ``ceiling_dbtp``, unless ``limit``,
    which brings the peaks down and raises the gain again (at most four times) until the target is met."""
    level = _dsp.loudness(x, rate)
    if level is None:
        raise MediaError(INPUT_INVALID, "the sound is shorter than 400 ms or below the -70 LUFS gate, so it has no "
                                        "integrated loudness to set")
    peak = _dsp.true_peak(x)
    wanted = d.target_lufs - level
    after_peak = _wav.dbfs(peak * _db(wanted))
    params = {"measured_lufs": level, "target_lufs": d.target_lufs, "ceiling_dbtp": d.ceiling_dbtp,
              "gain_db": round(wanted, 3), "limit": d.limit}
    if after_peak > d.ceiling_dbtp and not d.limit:
        raise MediaError(CLIPPED, f"at {d.target_lufs:g} LUFS ({wanted:+.1f} dB) its true peak would be "
                                  f"{after_peak:+.1f} dBTP, over the {d.ceiling_dbtp:g} dBTP ceiling: lower the target, "
                                  "raise the ceiling, or ask for limit=true")
    y = x * _db(wanted)
    if d.limit and after_peak > d.ceiling_dbtp:
        applied = wanted
        for _ in range(4):
            y = _dsp.limit(x * _db(applied), rate, d.ceiling_dbtp)
            reached = _dsp.loudness(y, rate)
            if reached is None or abs(reached - d.target_lufs) <= 0.3:
                break
            applied += d.target_lufs - reached
        params |= {"gain_db": round(applied, 3), "limited": True}

    def checks(after: dict[str, Any]) -> list[Check]:
        found = after["lufs"]
        ok = found is not None and abs(found - d.target_lufs) <= 0.5
        return [Check(name="loudness_matches_request", passed=ok, code="" if ok else LOUDNESS_MISSED,
                      detail=f"{found} LUFS, measured again" + ("" if ok else f"; asked for {d.target_lufs:g} (within "
                                                                              "0.5 LU)")),
                *peak_within(d.ceiling_dbtp)(after)]

    return Edit(y, rate, params, checks)


def join(d: Any, sounds: list[tuple[Any, int]], *, mix: bool) -> Edit:
    np = _dsp.numpy()
    rates = {r for _, r in sounds}
    widths = {s.shape[1] for s, _ in sounds}
    if len(rates) > 1 or len(widths) > 1:
        raise MediaError(INPUT_INVALID, f"the sources differ (rates {sorted(rates)}, channels {sorted(widths)}); "
                                        "resample or convert them to match first")
    rate, width = rates.pop(), widths.pop()
    if not mix:
        return Edit(np.concatenate([s for s, _ in sounds]), rate, {"sources": len(sounds)},
                    frames=sum(s.shape[0] for s, _ in sounds), channels=width)
    gains = list(d.gains_db) or [0.0] * len(sounds)
    if len(gains) != len(sounds):
        raise MediaError(INPUT_INVALID, f"gains_db gives {len(gains)} gains for {len(sounds)} sources")
    longest = max(s.shape[0] for s, _ in sounds)
    out = np.zeros((longest, sounds[0][0].shape[1]))
    for (s, _), g in zip(sounds, gains):
        out[:s.shape[0]] += s * _db(g)
    return Edit(out, rate, {"sources": len(sounds), "gains_db": gains}, frames=longest, channels=width)


EDITS: dict[str, Callable[[Any, Any, int], Edit]] = {
    "trim": trim, "fade": fade, "gain": gain, "resample": resample, "convert": convert, "dither": dither, "eq": eq,
    "compress": compress, "limit": limit, "gate": gate, "de_ess": de_ess, "noise_reduce": noise_reduce,
    "normalise_loudness": normalise_loudness,
}

__all__ = ["EDITS", "Edit", "analyse", "join"]
