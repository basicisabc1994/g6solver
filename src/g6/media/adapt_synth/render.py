# SPDX-License-Identifier: Apache-2.0
"""Making sound from numbers (NumPy), and measuring what was made against what was asked.

The oscillators are band-limited: a square, saw or triangle wave is the sum of its harmonics below the Nyquist
frequency (at most 200), so it does not alias, and a fundamental (or an FM sound's upper sideband) at or above
:data:`ALIAS_SHARE` of the sample rate is refused before it is made. Notes take an ADSR envelope; drums are synthesised
(a kick's falling sine, a snare's noise and body, a hat's high noise, a clap's bursts). Each render is a pure function
of its parameters and its seed.

The checks read the samples of the file that was written, never the renderer's own idea of them:
:func:`pitch_checks` (each note's fundamental, measured by YIN on its own stretch, within 25 cents of the note asked
for; a note outside the detector's :data:`DETECTED` range is not measured, and then no pitch check is passed),
:func:`chord_check` (a spectral peak within 30 cents of every chord tone), :func:`sideband_check` (FM's strongest
peaks at the carrier plus or minus whole multiples of the modulator), :func:`colour_check` (a noise's spectral slope,
fitted from 100 Hz to 10 kHz, within 1.5 dB per octave of white 0, pink -3 or brown -6), :func:`sustain_check` and
:func:`hits_check` (an onset within 15 ms of every drum step that has a hit).
"""
from __future__ import annotations

import math
from typing import Any

from g6.media import _dsp
from g6.media._types import INPUT_INVALID, PITCH_MISMATCH, Check, MediaError
from g6.media.adapt_synth import theory

MAX_HARMONICS = 200
CENTS = 25.0
#: The fundamentals the pitch detector reads (Hz); a note outside them is not measured.
DETECTED = (30.0, 4000.0)
#: A frequency at or above this share of the sample rate is refused: it would alias, or nearly.
ALIAS_SHARE = 0.45


def _np() -> Any:
    return _dsp.numpy("make the sound")


def oscillator(wave: str, freq: float, seconds: float, rate: int, phase: float = 0.0) -> Any:
    """One band-limited wave of ``freq`` Hz, ``seconds`` long, peak about 1."""
    np = _np()
    t = np.arange(int(round(seconds * rate))) / rate
    if wave == "sine":
        return np.sin(2 * np.pi * freq * t + phase)
    top = max(1, min(MAX_HARMONICS, int((rate / 2 - 1) // freq)))
    out = np.zeros_like(t)
    for k in range(1, top + 1):
        if wave == "square" and k % 2 == 0 or wave == "triangle" and k % 2 == 0:
            continue
        if wave == "square":
            amp = 1 / k
        elif wave == "saw":
            amp = (-1) ** (k + 1) / k
        else:  # triangle
            amp = (-1) ** ((k - 1) // 2) / (k * k)
        out += amp * np.sin(2 * np.pi * k * freq * t + k * phase)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    return out / peak if peak > 0 else out


def adsr(count: int, rate: int, attack: float, decay: float, sustain: float, release: float) -> Any:
    """An ADSR envelope of ``count`` samples: up to 1 in ``attack``, down to ``sustain`` in ``decay``, held, and down to
    0 in the last ``release`` seconds."""
    np = _np()
    env = np.full(count, sustain, dtype=float)
    a, d, r = (int(round(s * rate)) for s in (attack, decay, release))
    r = min(r, count)
    a = min(a, count - r)
    d = min(d, count - r - a)
    if a:
        env[:a] = np.linspace(0.0, 1.0, a, endpoint=False)
    if d:
        env[a:a + d] = np.linspace(1.0, sustain, d, endpoint=False)
    if r:
        start = env[count - r - 1] if count - r - 1 >= 0 else sustain
        env[count - r:] = np.linspace(start, 0.0, r)
    return env


def fm(freq: float, ratio: float, index: float, seconds: float, rate: int) -> Any:
    np = _np()
    t = np.arange(int(round(seconds * rate))) / rate
    return np.sin(2 * np.pi * freq * t + index * np.sin(2 * np.pi * freq * ratio * t))


def noise(colour: str, seconds: float, rate: int, seed: int) -> Any:
    """White, pink (-3 dB per octave) or brown (-6 dB per octave) noise, shaped in the frequency domain."""
    np = _np()
    n = int(round(seconds * rate))
    white = np.random.default_rng(seed).standard_normal(n)
    if colour == "white":
        out = white
    else:
        spec = np.fft.rfft(white)
        f = np.fft.rfftfreq(n, 1 / rate)
        f[0] = f[1] if n > 1 else 1.0
        spec *= f ** (-0.5 if colour == "pink" else -1.0)
        out = np.fft.irfft(spec, n)
    peak = float(np.max(np.abs(out))) if n else 0.0
    return out / peak if peak > 0 else out


def drum(kind: str, rate: int, rng: Any) -> Any:
    np = _np()
    if kind == "kick":
        n = int(0.35 * rate)
        t = np.arange(n) / rate
        sweep = 50 + 100 * np.exp(-t * 30)
        return np.sin(2 * np.pi * np.cumsum(sweep) / rate) * np.exp(-t * 9)
    if kind == "snare":
        n = int(0.18 * rate)
        t = np.arange(n) / rate
        return (0.6 * rng.standard_normal(n) + 0.5 * np.sin(2 * np.pi * 185 * t)) * np.exp(-t * 22)
    if kind == "hat":
        n = int(0.06 * rate)
        t = np.arange(n) / rate
        hiss = _dsp.high_band(rng.standard_normal(n)[:, None], rate, min(7000.0, rate / 2 * 0.8))[:, 0]
        return hiss * np.exp(-t * 70) * 2.0
    if kind == "clap":
        n = int(0.2 * rate)
        t = np.arange(n) / rate
        bursts = sum(np.exp(-np.clip(t - d, 0, None) * 120) * (t >= d) for d in (0.0, 0.012, 0.024))
        return rng.standard_normal(n) * bursts * 0.5
    raise MediaError(INPUT_INVALID, f"no drum {kind!r} (kick, snare, hat, clap)")


def peak_to(x: Any, volume: float) -> Any:
    """``x`` scaled so its peak is exactly ``volume`` (0 to 1)."""
    np = _np()
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    return x * (volume / peak) if peak > 0 else x


# -- checks on the written sound ---------------------------------------------------------------------------------------

def _cents(found: float, want: float) -> float:
    return 1200 * math.log2(found / want)


def unmeasured(notes: list[tuple[str, float, float, float]]) -> list[str]:
    """The notes of ``notes`` outside :data:`DETECTED`, which no pitch check measures."""
    return [f"{note} ({hz:.2f} Hz)" for note, hz, *_ in notes if not DETECTED[0] <= hz <= DETECTED[1]]


def pitch_checks(z: Any, rate: int, notes: list[tuple[str, float, float, float]]) -> list[Check]:
    """Each ``(label, hz, start_s, length_s)``: its fundamental, measured on the middle of its own stretch of the
    written sound, within :data:`CENTS` of the frequency asked for. A note outside :data:`DETECTED` is not measured;
    then no passed check is returned (it would vouch for a note nothing measured), while a measured note that is off
    still fails."""
    found, skipped = [], False
    for number, (note, want, start, length) in enumerate(notes, 1):
        if not DETECTED[0] <= want <= DETECTED[1]:
            skipped = True
            continue
        span = max(0.05, min(0.4, length * 0.6))
        middle = start + (length - span) / 2
        got, periodicity = _dsp.pitch(z, rate, start=int(middle * rate), length=span)
        if got is None:
            return [Check(name="pitch_matches_request", passed=False, code=PITCH_MISMATCH,
                          detail=f"note {number} ({note}, {want:.2f} Hz): no pitch could be measured "
                                 f"(periodicity {periodicity})")]
        off = _cents(got, want)
        if abs(off) > CENTS:
            heard, _ = _dsp.note_name(got)
            return [Check(name="pitch_matches_request", passed=False, code=PITCH_MISMATCH,
                          detail=f"note {number}: {note} ({want:.2f} Hz) was asked for, {got:.2f} Hz ({heard}, "
                                 f"{off:+.0f} cents) was measured")]
        found.append(f"{note} {got:.2f} Hz ({off:+.1f} cents)")
    if skipped:
        return []
    return [Check(name="pitch_matches_request", passed=True, detail="; ".join(found)[:500])]


def chord_check(z: Any, rate: int, notes: list[str]) -> Check:
    peaks = [p["frequency_hz"] for p in _dsp.spectrum(z, rate)["peaks"]]
    missing = [n for n in notes if not any(abs(_cents(p, theory.frequency(n))) <= 30 for p in peaks if p > 0)]
    if missing:
        return Check(name="chord_tones_present", passed=False, code=PITCH_MISMATCH,
                     detail=f"no spectral peak near {', '.join(missing)} (peaks at "
                            f"{', '.join(f'{p:.1f}' for p in peaks[:8])} Hz)")
    return Check(name="chord_tones_present", passed=True, detail=f"a spectral peak near each of {', '.join(notes)}")


def sideband_check(z: Any, rate: int, freq: float, ratio: float) -> Check:
    mod = freq * ratio
    peaks = [p["frequency_hz"] for p in _dsp.spectrum(z, rate)["peaks"][:3]]
    off = [p for p in peaks if min(abs(p - abs(freq + k * mod)) for k in range(-20, 21)) > max(3.0, 0.01 * p)]
    if off or not peaks:
        return Check(name="sidebands_where_expected", passed=False, code=PITCH_MISMATCH,
                     detail=f"strongest peaks {peaks} Hz are not all at {freq:g} Hz plus or minus multiples of "
                            f"{mod:g} Hz")
    return Check(name="sidebands_where_expected", passed=True,
                 detail=f"strongest peaks {', '.join(f'{p:.1f}' for p in peaks)} Hz, each at {freq:g} Hz plus or minus "
                        f"a multiple of {mod:g} Hz")


def colour_check(z: Any, rate: int, colour: str) -> Check:
    np = _np()
    signal = _dsp.mono(z)
    size = 4096
    frames = np.lib.stride_tricks.sliding_window_view(signal, size)[::size // 2]
    power = np.mean(np.abs(np.fft.rfft(frames * np.hanning(size), axis=1)) ** 2, axis=0)
    f = np.fft.rfftfreq(size, 1 / rate)
    keep = (f >= 100) & (f <= min(10000, rate / 2 * 0.9))
    slope = float(np.polyfit(np.log2(f[keep]), 10 * np.log10(power[keep] + 1e-30), 1)[0])
    want = {"white": 0.0, "pink": -3.0, "brown": -6.0}[colour]
    ok = abs(slope - want) <= 1.5
    return Check(name="colour_matches_request", passed=ok, code="" if ok else PITCH_MISMATCH,
                 detail=f"spectral slope {slope:+.2f} dB per octave" + ("" if ok else f", not {want:+.0f} ({colour})"))


def sustain_check(z: Any, rate: int, attack: float, decay: float, sustain: float, release: float,
                  seconds: float) -> Check | None:
    """The level held between the decay and the release, against the peak: ``sustain`` within 2 dB."""
    np = _np()
    held = seconds - attack - decay - release
    if sustain <= 0 or held < 0.1:
        return None
    signal = _dsp.mono(z)
    start = int((attack + decay + held * 0.25) * rate)
    end = int((attack + decay + held * 0.75) * rate)
    level = float(np.max(np.abs(signal[start:end])))
    top = float(np.max(np.abs(signal)))
    off = 20 * math.log10(level / top / sustain) if level > 0 and top > 0 else -math.inf
    ok = abs(off) <= 2.0
    return Check(name="sustain_level_matches_request", passed=ok, code="" if ok else PITCH_MISMATCH,
                 detail=f"held at {level / top:.3f} of the peak, {off:+.2f} dB from the {sustain:g} asked for")


def hits_check(z: Any, rate: int, hits: list[float]) -> Check:
    """An onset (a jump in the 2 ms energy) within 15 ms of every step that has a hit."""
    np = _np()
    step = max(1, int(0.002 * rate))
    signal = _dsp.mono(z)
    count = len(signal) // step
    energy = (signal[:count * step] ** 2).reshape(count, step).mean(axis=1)
    rise = np.diff(np.log10(energy + 1e-10), prepend=-10.0)
    missed = []
    for at in hits:
        i = int(at * rate / step)
        window = rise[max(0, i - 8):i + 8]
        if not window.size or float(np.max(window)) < 1.0:
            missed.append(round(at, 3))
    if missed:
        return Check(name="hits_on_their_steps", passed=False, code=PITCH_MISMATCH,
                     detail=f"no onset within 15 ms of the hits at {missed[:8]} s")
    return Check(name="hits_on_their_steps", passed=True, detail=f"an onset at each of the {len(hits)} hits")


__all__ = ["ALIAS_SHARE", "CENTS", "DETECTED", "MAX_HARMONICS", "adsr", "chord_check", "colour_check", "drum", "fm",
           "hits_check", "noise", "oscillator", "peak_to", "pitch_checks", "sideband_check", "sustain_check",
           "unmeasured"]
