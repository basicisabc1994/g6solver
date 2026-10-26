# SPDX-License-Identifier: Apache-2.0
"""Sound as numbers, with NumPy (the ``audio`` extra): WAV samples in and out, filters, dynamics, and the measurements
the family's sound checks rest on. ``_``-prefixed, so it never registers as a component; adapt_audio and adapt_synth
share it, so neither imports the other.

* :func:`load` reads a WAV the stdlib reader (:mod:`g6.media._wav`) parsed into floats, full scale 1.0, ``(frames,
  channels)``; :func:`wav_bytes` writes PCM, refusing (never clipping) a sample past full scale, with TPDF dither when
  asked.
* Loudness by ITU-R BS.1770-4 (:func:`loudness`): K-weighting (its two filters at any sample rate), 400 ms blocks with
  75 % overlap, the absolute gate at -70 LUFS and the relative gate 10 LU below; a 997 Hz sine at 0 dBFS is -3.01 LUFS.
  True peak (:func:`true_peak`): four times oversampled by a windowed-sinc interpolator (not the standard's own 48-tap
  filter, so it may differ from it by a few hundredths of a dB).
* Filters (:func:`biquad`, Robert Bristow-Johnson's cookbook) run as their impulse response, computed exactly from
  their poles, convolved by FFT; :func:`resample` is band-limited windowed-sinc interpolation.
* :func:`pitch` (YIN), :func:`tempo` (onset strength and its autocorrelation), :func:`spectrum`, :func:`silences`,
  :func:`measure`; and the dynamics: :func:`compress`, :func:`gate`, :func:`limit` (true-peak, with look-ahead),
  :func:`de_ess` and :func:`reduce_noise` (spectral gating).

Without NumPy each raises :class:`~g6.media._types.MediaError` with ``G6_E_MEDIA_EXTRA_MISSING`` and the line to run.

The work is bounded as the inputs are: no op reads or makes more than :data:`MAX_SAMPLES` samples (:func:`fits` refuses
a result past it before it is made), :func:`resample` gathers a bounded number of values at a time whatever the ratio
and the channels, and :func:`tempo` transforms a block of frames at a time with a window no longer than four hops.
"""
from __future__ import annotations

import io
import math
import wave
from pathlib import Path
from typing import Any

from g6.media import _wav
from g6.media._types import CLIPPED, EXTRA_MISSING, INPUT_INVALID, UNDECODABLE, Check, MediaError

#: The most samples (frames times channels) an op reads, and the most a result it makes may hold: about five minutes of
#: 48 kHz stereo.
MAX_SAMPLES = 30_000_000
#: How many source values one step of :func:`resample` gathers (output frames x taps x channels): 16 MiB of float64.
RESAMPLE_STEP = 1 << 21
#: How many frames :func:`tempo` transforms at a time.
ONSET_BLOCK = 1024
_FULL = {8: 127.0, 16: 32767.0, 24: 8388607.0, 32: 2147483647.0}
_NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def numpy(doing: str = "process the sound") -> Any:
    """NumPy, or :class:`MediaError` (``G6_E_MEDIA_EXTRA_MISSING``) saying how to install it."""
    try:
        import numpy as np
    except ImportError:
        raise MediaError(EXTRA_MISSING, f"NumPy is not installed, so G6 cannot {doing}: pip install 'g6[audio]'") from None
    return np


# -- WAV in and out --------------------------------------------------------------------------------------------------

def load(path: Path | str) -> tuple[Any, int, _wav.WavInfo]:
    """The samples of the WAV at ``path`` as float64 ``(frames, channels)``, its rate and its layout."""
    np = numpy("read the sound")
    info = _wav.read_info(path)
    if info.frames * info.channels > MAX_SAMPLES:
        raise MediaError(INPUT_INVALID, f"{Path(path).name} holds {info.frames * info.channels} samples, more than an "
                                        f"op reads ({MAX_SAMPLES}, about five minutes of 48 kHz stereo)")
    with open(path, "rb") as f:
        f.seek(info.data_offset)
        raw = f.read(info.data_bytes)
    if len(raw) != info.data_bytes:
        raise MediaError(UNDECODABLE, f"{Path(path).name}: the samples end before the header says")
    if info.floating:
        x = np.frombuffer(raw, "<f4" if info.bits == 32 else "<f8").astype(np.float64)
    elif info.bits == 8:
        x = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128.0) / 128.0
    elif info.bits == 16:
        x = np.frombuffer(raw, "<i2") / 32768.0
    elif info.bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v) / float(1 << 23)
    else:
        x = np.frombuffer(raw, "<i4") / 2147483648.0
    if not np.all(np.isfinite(x)):
        raise MediaError(UNDECODABLE, f"{Path(path).name}: a sample is not a finite number")
    return x.reshape(info.frames, info.channels), info.rate, info


def fits(samples: int, what: str) -> None:
    """Refuse (:class:`MediaError`, ``G6_E_MEDIA_INPUT_INVALID``) a result of ``samples`` values (frames times
    channels) past :data:`MAX_SAMPLES`, before anything that size is made."""
    if samples > MAX_SAMPLES:
        raise MediaError(INPUT_INVALID, f"{what} would hold {samples} samples, more than an op makes ({MAX_SAMPLES}, "
                                        "about five minutes of 48 kHz stereo)")


def full_scale(x: Any) -> Check:
    """Whether every sample of ``x`` is within full scale; a result past it would distort when written."""
    np = numpy()
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    over = int(np.count_nonzero(np.abs(x) > 1.0))
    if over:
        return Check(name="not_clipped", passed=False, code=CLIPPED,
                     detail=f"{over} samples would go past full scale (peak {_wav.dbfs(peak):+.2f} dBFS); nothing is "
                            "clipped silently")
    return Check(name="not_clipped", passed=True, detail=f"peak {_wav.dbfs(peak)} dBFS, within full scale")


def wav_bytes(x: Any, rate: int, bits: int = 16, *, dither_seed: int | None = None) -> bytes:
    """``x`` as PCM WAV of ``bits`` (16, 24 or 32); TPDF dither of one step when ``dither_seed`` is given. A sample past
    full scale is :class:`MediaError` (``G6_E_MEDIA_CLIPPED``): check :func:`full_scale` first."""
    np = numpy("write the sound")
    if bits not in (16, 24, 32):
        raise MediaError(INPUT_INVALID, f"{bits}-bit samples are not written; use 16, 24 or 32")
    x = np.asarray(x, dtype=np.float64)
    x = x[:, None] if x.ndim == 1 else x
    check = full_scale(x)
    if not check.passed:
        raise MediaError(CLIPPED, check.detail)
    full = _FULL[bits]
    scaled = x * full
    if dither_seed is not None:
        rng = np.random.default_rng(dither_seed)
        scaled = scaled + rng.uniform(-0.5, 0.5, scaled.shape) + rng.uniform(-0.5, 0.5, scaled.shape)
    ints = np.clip(np.round(scaled), -full, full).astype("<i4")  # the dither's last step stays within full scale
    if bits == 16:
        raw = ints.astype("<i2").tobytes()
    elif bits == 24:
        raw = ints.reshape(-1, 1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    else:
        raw = ints.tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(raw)
    return out.getvalue()


# -- filters ---------------------------------------------------------------------------------------------------------

def biquad(kind: str, frequency: float, rate: int, q: float = 0.7071, gain_db: float = 0.0) -> tuple[list, list]:
    """One second-order section from Robert Bristow-Johnson's cookbook: ``peak``, ``low_shelf``, ``high_shelf``,
    ``low_pass``, ``high_pass`` or ``band_pass``; ``(b, a)`` with ``a[0] == 1``."""
    if not 0 < frequency < rate / 2:
        raise MediaError(INPUT_INVALID, f"{frequency:g} Hz is not between 0 and half the sample rate ({rate / 2:g} Hz)")
    a_gain = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * frequency / rate
    cos, alpha = math.cos(w0), math.sin(w0) / (2 * q)
    root = 2 * math.sqrt(a_gain) * alpha
    if kind == "peak":
        b = [1 + alpha * a_gain, -2 * cos, 1 - alpha * a_gain]
        a = [1 + alpha / a_gain, -2 * cos, 1 - alpha / a_gain]
    elif kind == "low_shelf":
        b = [a_gain * ((a_gain + 1) - (a_gain - 1) * cos + root), 2 * a_gain * ((a_gain - 1) - (a_gain + 1) * cos),
             a_gain * ((a_gain + 1) - (a_gain - 1) * cos - root)]
        a = [(a_gain + 1) + (a_gain - 1) * cos + root, -2 * ((a_gain - 1) + (a_gain + 1) * cos),
             (a_gain + 1) + (a_gain - 1) * cos - root]
    elif kind == "high_shelf":
        b = [a_gain * ((a_gain + 1) + (a_gain - 1) * cos + root), -2 * a_gain * ((a_gain - 1) + (a_gain + 1) * cos),
             a_gain * ((a_gain + 1) + (a_gain - 1) * cos - root)]
        a = [(a_gain + 1) - (a_gain - 1) * cos + root, 2 * ((a_gain - 1) - (a_gain + 1) * cos),
             (a_gain + 1) - (a_gain - 1) * cos - root]
    elif kind == "low_pass":
        b = [(1 - cos) / 2, 1 - cos, (1 - cos) / 2]
        a = [1 + alpha, -2 * cos, 1 - alpha]
    elif kind == "high_pass":
        b = [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2]
        a = [1 + alpha, -2 * cos, 1 - alpha]
    elif kind == "band_pass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cos, 1 - alpha]
    else:
        raise MediaError(INPUT_INVALID, f"no filter of kind {kind!r}")
    return [v / a[0] for v in b], [1.0, a[1] / a[0], a[2] / a[0]]


def impulse_response(sections: list[tuple[list, list]], rate: int, seconds: float = 2.0) -> Any:
    """The impulse response of the sections in cascade, from each one's poles (exact, no recursion), long enough for
    the slowest to decay below one part in a billion and at most ``seconds``."""
    np = numpy()
    longest = 64
    for _, a in sections:
        poles = np.roots([1.0, a[1], a[2]])
        radius = float(np.max(np.abs(poles)))
        if radius >= 1.0:
            raise MediaError(INPUT_INVALID, "a filter asked for is unstable")
        if radius > 0:
            longest = max(longest, int(math.log(1e-9) / math.log(radius)) + 3)
    n = min(longest, int(seconds * rate))
    out = np.zeros(n)
    out[0] = 1.0
    for b, a in sections:
        p1, p2 = np.roots([1.0, a[1], a[2]]) if (a[1] or a[2]) else (0.0, 0.0)
        k = np.arange(n)
        if abs(p1 - p2) > 1e-12:
            h = ((p1 ** (k + 1) - p2 ** (k + 1)) / (p1 - p2)).real
        else:
            h = ((k + 1) * p1 ** k).real
        full = b[0] * h
        full[1:] += b[1] * h[:-1]
        full[2:] += b[2] * h[:-2]
        out = convolve(out[:, None], full)[:, 0][:n]
    return out


def convolve(x: Any, h: Any) -> Any:
    """``x`` ``(frames, channels)`` convolved with ``h`` by FFT, the first ``len(x)`` samples (a causal filter)."""
    np = numpy()
    frames, size = x.shape[0], len(h)
    block = 1 << max(14, int(math.ceil(math.log2(max(size, 1)))) + 1)
    nfft = 1 << int(math.ceil(math.log2(block + size - 1)))
    spectrum_h = np.fft.rfft(h, nfft)
    out = np.zeros((frames + size, x.shape[1]))
    for start in range(0, frames, block):
        piece = x[start:start + block]
        done = np.fft.irfft(np.fft.rfft(piece, nfft, axis=0) * spectrum_h[:, None], nfft, axis=0)
        out[start:start + len(piece) + size - 1] += done[:len(piece) + size - 1]
    return out[:frames]


def filtered(x: Any, rate: int, sections: list[tuple[list, list]]) -> Any:
    return convolve(x, impulse_response(sections, rate))


def high_band(x: Any, rate: int, frequency: float) -> Any:
    """The part of ``x`` above ``frequency``, with no phase shift (a linear-phase windowed-sinc high-pass, its delay
    taken out), so ``x - high_band(x)`` is exactly the rest."""
    np = numpy()
    half = int(4 * rate / frequency) | 1
    n = np.arange(-half, half + 1)
    cutoff = 2 * frequency / rate
    low = cutoff * np.sinc(cutoff * n) * np.kaiser(len(n), 8.0)
    low /= low.sum()
    h = -low
    h[half] += 1.0
    padded = np.pad(x, ((0, half), (0, 0)))
    return convolve(padded, h)[half:half + x.shape[0]]


def _sinc_kernel(distance: Any, cutoff: float, half: float) -> Any:
    np = numpy()
    inside = np.abs(distance) < half
    window = np.where(inside, np.i0(8.0 * np.sqrt(np.clip(1 - (distance / half) ** 2, 0, 1))) / np.i0(8.0), 0.0)
    return cutoff * np.sinc(cutoff * distance) * window


def resample(x: Any, rate: int, to: int, zeros: int = 16) -> Any:
    """``x`` at ``to`` samples a second: band-limited windowed-sinc interpolation (Kaiser, beta 8), cut off just below
    the lower Nyquist frequency. A result past :data:`MAX_SAMPLES` is refused before it is made; each step gathers at
    most :data:`RESAMPLE_STEP` values (the taps grow as the ratio falls)."""
    np = numpy()
    frames = x.shape[0]
    count = int(round(frames * to / rate))
    fits(count * x.shape[1], f"the sound at {to} Hz")
    cutoff = min(1.0, to / rate) * 0.97
    half = zeros / cutoff
    reach = int(math.ceil(half))
    padded = np.pad(x, ((reach, reach + 1), (0, 0)))
    out = np.empty((count, x.shape[1]))
    offsets = np.arange(-reach + 1, reach + 1)
    step = max(1, min(4096, RESAMPLE_STEP // (len(offsets) * x.shape[1])))
    for start in range(0, count, step):
        t = np.arange(start, min(start + step, count)) * (rate / to)
        base = np.floor(t).astype(np.int64)
        idx = base[:, None] + offsets[None, :]
        weights = _sinc_kernel(t[:, None] - idx, cutoff, half)
        out[start:start + len(t)] = np.einsum("nk,nkc->nc", weights, padded[idx + reach])
    return out


# -- loudness and peaks ----------------------------------------------------------------------------------------------

def kweighting(rate: int) -> list[tuple[list, list]]:
    """BS.1770's K-weighting at ``rate``: its high shelf (about +4 dB above 1.7 kHz) and its high-pass (38 Hz), from
    the analogue design the standard's 48 kHz coefficients come from (as libebur128 computes them at any rate)."""
    k = math.tan(math.pi * 1681.974450955533 / rate)
    q = 0.7071752369554196
    vh = 10 ** (3.999843853973347 / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = ([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0],
             [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    k = math.tan(math.pi * 38.13547087602444 / rate)
    q = 0.5003270373238773
    a0 = 1 + k / q + k * k
    high_pass = ([1.0, -2.0, 1.0], [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    return [shelf, high_pass]


def _weights(channels: int) -> list[float]:
    if channels == 6:  # L, R, C, LFE (not counted), Ls, Rs
        return [1.0, 1.0, 1.0, 0.0, 1.41, 1.41]
    return [1.0] * min(channels, 3) + [1.41] * max(0, channels - 3)


def loudness(x: Any, rate: int) -> float | None:
    """Integrated loudness in LUFS (BS.1770-4); ``None`` for less than 400 ms or nothing above the absolute gate."""
    np = numpy("measure loudness")
    y = filtered(x, rate, kweighting(rate))
    step = int(round(0.1 * rate))
    count = y.shape[0] // step
    if count < 4:
        return None
    energy = (y[:count * step] ** 2).reshape(count, step, y.shape[1]).mean(axis=1)
    blocks = (energy[:-3] + energy[1:-2] + energy[2:-1] + energy[3:]) / 4
    z = blocks @ np.asarray(_weights(y.shape[1]))
    with np.errstate(divide="ignore"):
        levels = -0.691 + 10 * np.log10(z)
    kept = levels > -70.0
    if not np.any(kept):
        return None
    relative = -0.691 + 10 * math.log10(float(np.mean(z[kept]))) - 10.0
    kept &= levels > relative
    return round(-0.691 + 10 * math.log10(float(np.mean(z[kept]))), 3)


def _phases(taps: int = 12) -> list[Any]:
    np = numpy()
    offsets = np.arange(-taps + 1, taps + 1)
    out = []
    for p in (0.25, 0.5, 0.75):
        kernel = _sinc_kernel(p - offsets, 1.0, float(taps))
        out.append(kernel / kernel.sum())
    return out


def peak_envelope(x: Any, taps: int = 12) -> Any:
    """Each sample's true peak (four times oversampled) across channels: ``max`` of the sample and the three points
    interpolated after it."""
    np = numpy()
    env = np.max(np.abs(x), axis=1)
    for kernel in _phases(taps):
        for c in range(x.shape[1]):
            full = np.convolve(x[:, c], kernel[::-1])
            env = np.maximum(env, np.abs(full[taps:taps + x.shape[0]]))
    return env


def true_peak(x: Any) -> float:
    """The largest true peak of ``x`` (full scale 1.0)."""
    np = numpy("measure the true peak")
    return float(np.max(peak_envelope(x))) if x.size else 0.0


def measure(x: Any, rate: int) -> dict[str, Any]:
    """The levels every write op reports before and after: length, rate, channels, sample peak, true peak, RMS,
    integrated loudness, crest factor, DC offset, and for stereo the phase correlation."""
    np = numpy("measure the sound")
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0
    tp = true_peak(x)
    out: dict[str, Any] = {"duration_s": round(x.shape[0] / rate, 6), "rate": rate, "channels": x.shape[1],
                           "frames": x.shape[0], "peak_dbfs": _wav.dbfs(peak), "true_peak_dbtp": _wav.dbfs(tp),
                           "rms_dbfs": _wav.dbfs(rms), "lufs": loudness(x, rate),
                           "crest_factor_db": round(20 * math.log10(peak / rms), 3) if rms > 0 and peak > 0 else None,
                           "dc_offset": [round(float(v), 6) for v in np.mean(x, axis=0)]}
    if x.shape[1] == 2:
        out["correlation"] = correlation(x)
    return out


def correlation(x: Any) -> float | None:
    """The phase correlation of a stereo pair, from -1 (opposite) to +1 (the same)."""
    np = numpy()
    left, right = x[:, 0], x[:, 1]
    den = math.sqrt(float(np.sum(left ** 2)) * float(np.sum(right ** 2)))
    return round(float(np.sum(left * right)) / den, 4) if den > 0 else None


# -- analysis --------------------------------------------------------------------------------------------------------

def mono(x: Any) -> Any:
    return x.mean(axis=1)


def note_name(frequency: float) -> tuple[str, float]:
    """The nearest equal-tempered note (A4 = 440 Hz) and how far off it is, in cents."""
    midi = 69 + 12 * math.log2(frequency / 440.0)
    nearest = int(round(midi))
    return f"{_NOTES[nearest % 12]}{nearest // 12 - 1}", round((midi - nearest) * 100, 1)


def pitch(x: Any, rate: int, *, low: float = 30.0, high: float = 4000.0, start: int | None = None,
          length: float = 0.5) -> tuple[float | None, float]:
    """The fundamental frequency (YIN, threshold 0.1) of the loudest stretch of ``length`` seconds, or of the
    stretch from ``start``; ``(None, periodicity)`` when nothing is periodic enough. Periodicity is ``1 - d'``."""
    np = numpy("measure pitch")
    signal = mono(x)
    span = min(len(signal), int(length * rate))
    if start is None:
        hop = max(1, span // 4)
        energies = [float(np.sum(signal[i:i + span] ** 2)) for i in range(0, max(1, len(signal) - span + 1), hop)]
        start = int(np.argmax(energies)) * hop
    seg = signal[start:start + span]
    lag_max = min(int(rate / low), len(seg) // 2)
    lag_min = max(2, int(rate / high))
    window = len(seg) - lag_max
    if window <= lag_min or not np.any(seg):
        return None, 0.0
    nfft = 1 << int(math.ceil(math.log2(len(seg) + window)))
    fx = np.fft.rfft(seg, nfft)
    fw = np.fft.rfft(seg[:window][::-1], nfft)
    r = np.fft.irfft(fx * fw, nfft)[window - 1:window - 1 + lag_max + 1]
    squares = np.concatenate([[0.0], np.cumsum(seg ** 2)])
    e0 = squares[window]
    et = squares[np.arange(lag_max + 1) + window] - squares[np.arange(lag_max + 1)]
    d = e0 + et - 2 * r
    d[0] = 0.0
    running = np.cumsum(d[1:])
    cmnd = np.ones_like(d)
    cmnd[1:] = d[1:] * np.arange(1, lag_max + 1) / np.where(running > 0, running, 1.0)
    found = None
    for tau in range(lag_min, lag_max):
        if cmnd[tau] < 0.1:
            while tau + 1 < lag_max and cmnd[tau + 1] < cmnd[tau]:
                tau += 1
            found = tau
            break
    if found is None:
        tau = lag_min + int(np.argmin(cmnd[lag_min:lag_max]))
        if cmnd[tau] >= 0.3:
            return None, round(float(1 - cmnd[tau]), 4)
        found = tau
    a, b, c = cmnd[found - 1], cmnd[found], cmnd[found + 1] if found + 1 <= lag_max else cmnd[found]
    shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    return round(rate / (found + shift), 3), round(float(1 - b), 4)


def stft(signal: Any, size: int = 2048, hop: int = 512) -> Any:
    np = numpy()
    padded = np.pad(signal, (size // 2, size // 2 + size))
    count = 1 + (len(padded) - size) // hop
    frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::hop][:count]
    return np.fft.rfft(frames * np.hanning(size), axis=1)


def _onsets(signal: Any, size: int, hop: int) -> Any:
    """Each frame's positive spectral flux against the frame before (the frames :func:`stft` makes), computed
    :data:`ONSET_BLOCK` frames at a time so the memory is bounded whatever the length."""
    np = numpy()
    padded = np.pad(signal, (size // 2, size // 2 + size))
    count = 1 + (len(padded) - size) // hop
    frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::hop][:count]
    window = np.hanning(size)
    out, last = [], None
    for first in range(0, count, ONSET_BLOCK):
        level = np.log1p(1000 * np.abs(np.fft.rfft(frames[first:first + ONSET_BLOCK] * window, axis=1)))
        if last is not None:
            level = np.concatenate([last, level])
        out.append(np.maximum(0.0, np.diff(level, axis=0)).sum(axis=1))
        last = level[-1:]
    return np.concatenate(out)


def tempo(x: Any, rate: int, *, low: float = 60.0, high: float = 200.0) -> tuple[float | None, float]:
    """Beats per minute from the autocorrelation of an onset-strength envelope (positive spectral flux every 10 ms);
    ``(None, 0)`` when there is no repeating onset. Strength is the autocorrelation peak over its zero-lag value. The
    window is 1024 samples, or four hops below 25.6 kHz, so the work grows with the samples and no faster; only the
    lags the tempo range needs are correlated."""
    np = numpy("measure tempo")
    hop = max(1, rate // 100)
    flux = _onsets(mono(x), min(1024, 4 * hop), hop)
    flux = flux - flux.mean()
    if not np.any(flux):
        return None, 0.0
    lo, hi = int(60 * rate / (high * hop)), int(math.ceil(60 * rate / (low * hop)))
    if hi + 1 >= len(flux):
        return None, 0.0
    ac = np.array([float(np.dot(flux[:len(flux) - k], flux[k:])) for k in range(hi + 2)])
    if ac[0] <= 0:
        return None, 0.0
    lag = lo + int(np.argmax(ac[lo:hi + 1]))
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    return round(60 * rate / ((lag + shift) * hop), 2), round(float(b / ac[0]), 4)


def silences(x: Any, rate: int, threshold_dbfs: float = -50.0, minimum: float = 0.25) -> list[dict[str, float]]:
    """The stretches at least ``minimum`` seconds long whose 10 ms RMS stays below ``threshold_dbfs``."""
    np = numpy("find silences")
    step = max(1, rate // 100)
    count = x.shape[0] // step
    if not count:
        return []
    rms = np.sqrt((x[:count * step] ** 2).reshape(count, step, x.shape[1]).mean(axis=(1, 2)))
    quiet = rms < 10 ** (threshold_dbfs / 20)
    out, start = [], None
    for i, q in enumerate([*quiet, False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * step / rate >= minimum:
                out.append({"start_s": round(start * step / rate, 3), "end_s": round(i * step / rate, 3)})
            start = None
    return out


def spectrum(x: Any, rate: int, size: int = 8192) -> dict[str, Any]:
    """The averaged spectrum (Welch, Hann): its strongest peaks (frequency and the level of a sine that peaks there),
    centroid, 85 % roll-off and the energy below 250 Hz, from 250 Hz to 4 kHz, and above."""
    np = numpy("measure the spectrum")
    signal = mono(x)
    size = min(size, 1 << int(math.log2(max(len(signal), 64))))
    frames = np.lib.stride_tricks.sliding_window_view(signal, size)[::size // 2]
    window = np.hanning(size)
    power = np.mean(np.abs(np.fft.rfft(frames * window, axis=1)) ** 2, axis=0)
    freqs = np.fft.rfftfreq(size, 1 / rate)
    amp = np.sqrt(power) * 2 / window.sum()
    floor = float(np.max(amp)) * 10 ** (-60 / 20)
    peaks = []
    for k in range(1, len(amp) - 1):
        if amp[k] > floor and amp[k] >= amp[k - 1] and amp[k] > amp[k + 1]:
            a, b, c = (math.log(max(v, 1e-30)) for v in amp[k - 1:k + 2])
            shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
            peaks.append({"frequency_hz": round(float(freqs[k] + shift * rate / size), 2),
                          "level_db": _wav.dbfs(float(math.exp(b - 0.25 * (a - c) * shift)))})
    peaks.sort(key=lambda p: -p["level_db"])
    near, kept = 4 * rate / size, []
    for found in peaks:  # a window's side lobes beside a stronger peak are not peaks of their own
        if all(abs(found["frequency_hz"] - k["frequency_hz"]) > near for k in kept):
            kept.append(found)
    peaks = kept
    total = float(power.sum())
    cumulative = np.cumsum(power)

    def band(lo: float, hi: float) -> float:
        share = float(power[(freqs >= lo) & (freqs < hi)].sum())
        return round(10 * math.log10(share / total), 2) if share > 0 and total > 0 else _wav.FLOOR_DBFS

    return {"peaks": peaks[:8], "centroid_hz": round(float((freqs * power).sum() / total), 2) if total else None,
            "rolloff_hz": round(float(freqs[int(np.searchsorted(cumulative, 0.85 * total))]), 2) if total else None,
            "bands_db": {"low": band(0, 250), "mid": band(250, 4000), "high": band(4000, rate)}}


# -- dynamics --------------------------------------------------------------------------------------------------------

def _levels_db(x: Any, rate: int, block: int, window_s: float) -> Any:
    """The RMS level of ``x`` (the loudest channel) in dB, one value per ``block`` samples, over ``window_s``."""
    np = numpy()
    count = int(math.ceil(x.shape[0] / block))
    padded = np.pad(x, ((0, count * block - x.shape[0]), (0, 0)))
    power = (padded ** 2).reshape(count, block, x.shape[1]).mean(axis=1).max(axis=1)
    span = max(1, int(round(window_s * rate / block)))
    smooth = np.convolve(power, np.ones(span) / span)[span // 2:span // 2 + count]
    return 10 * np.log10(np.maximum(smooth, 1e-20))


def _smooth(target: Any, block_s: float, attack_s: float, release_s: float) -> Any:
    """Gain (dB) following ``target``: falling within ``attack_s``, rising back within ``release_s``."""
    np = numpy()
    attack = 1 - math.exp(-block_s / max(attack_s, 1e-6))
    release = 1 - math.exp(-block_s / max(release_s, 1e-6))
    out = np.empty_like(target)
    state = 0.0
    for i, want in enumerate(target.tolist()):
        state += (want - state) * (attack if want < state else release)
        out[i] = state
    return out


def _apply(x: Any, gain_db: Any, block: int) -> Any:
    np = numpy()
    centres = (np.arange(len(gain_db)) + 0.5) * block
    per_sample = np.interp(np.arange(x.shape[0]), centres, gain_db)
    return x * (10 ** (per_sample / 20))[:, None]


def compress(x: Any, rate: int, threshold_db: float, ratio: float, attack_ms: float, release_ms: float,
             knee_db: float = 0.0, makeup_db: float = 0.0) -> Any:
    """A feed-forward compressor on the 10 ms RMS of the loudest channel (the channels move together)."""
    np = numpy("compress the sound")
    block = max(16, rate // 1000)
    level = _levels_db(x, rate, block, 0.01)
    over = level - threshold_db
    if knee_db > 0:
        reduce = np.where(over <= -knee_db / 2, 0.0, np.where(
            over >= knee_db / 2, over * (1 - 1 / ratio), (1 - 1 / ratio) * (over + knee_db / 2) ** 2 / (2 * knee_db)))
    else:
        reduce = np.maximum(over, 0.0) * (1 - 1 / ratio)
    gain = _smooth(-reduce, block / rate, attack_ms / 1000, release_ms / 1000)
    return _apply(x, gain + makeup_db, block)


def gate(x: Any, rate: int, threshold_db: float, floor_db: float, attack_ms: float, hold_ms: float,
         release_ms: float) -> Any:
    """A noise gate: open (no change) while the 10 ms RMS is above ``threshold_db`` and for ``hold_ms`` after, closed
    to ``floor_db`` otherwise."""
    np = numpy("gate the sound")
    block = max(16, rate // 1000)
    level = _levels_db(x, rate, block, 0.01)
    hold = int(round(hold_ms / 1000 * rate / block))
    open_ = level > threshold_db
    if hold:
        open_ = np.convolve(open_.astype(float), np.ones(hold + 1))[:len(open_)] > 0
    target = np.where(open_, 0.0, floor_db)
    gain = _smooth(target, block / rate, release_ms / 1000, attack_ms / 1000)
    return _apply(x, gain, block)


def limit(x: Any, rate: int, ceiling_db: float, release_ms: float = 50.0, passes: int = 4) -> Any:
    """A true-peak limiter: each 1 ms block's oversampled peak brought to ``ceiling_db`` with one block of look-ahead
    and a ``release_ms`` recovery; the gain at every sample is at most the lowest any block near it needs. Run again
    on its own result (up to ``passes`` times) while the true peak is still over the ceiling."""
    np = numpy("limit the sound")
    ceiling = 10 ** (ceiling_db / 20)
    block = max(16, rate // 1000)
    y = x
    for _ in range(passes):
        env = peak_envelope(y)
        if float(np.max(env)) <= ceiling:
            break
        count = int(math.ceil(len(env) / block))
        peaks = np.pad(env, (0, count * block - len(env))).reshape(count, block).max(axis=1)
        need = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-20)) * 0.999
        need = np.minimum(need, np.concatenate([need[1:], [1.0]]))
        recover = 1 - math.exp(-(block / rate) / (release_ms / 1000))
        gain = np.empty_like(need)
        state = 1.0
        for i, want in enumerate(need.tolist()):
            state = want if want < state else min(want, state + (1 - state) * recover)
            gain[i] = state
        per = np.repeat(gain, block)[:len(env)]
        # the mean, over one block, of the lowest gain within a block either side: never more than any block near it
        # needs, and without steps
        lowest = np.lib.stride_tricks.sliding_window_view(np.pad(per, block, mode="edge"), 2 * block + 1).min(axis=1)
        smooth = np.convolve(np.pad(lowest, block, mode="edge"), np.ones(block) / block, mode="same")[block:-block]
        y = y * smooth[:, None]
    return y


def de_ess(x: Any, rate: int, frequency: float, threshold_db: float, reduction_db: float,
           attack_ms: float = 1.0, release_ms: float = 40.0) -> Any:
    """Lower the band above ``frequency`` (:func:`high_band`) by up to ``reduction_db`` while its 5 ms RMS is above
    ``threshold_db``; the rest of the sound is untouched, and with no reduction the result is the input."""
    np = numpy("de-ess the sound")
    band = high_band(x, rate, frequency)
    block = max(16, rate // 1000)
    level = _levels_db(band, rate, block, 0.005)
    target = -np.minimum(reduction_db, np.maximum(level - threshold_db, 0.0))
    gain = _smooth(target, block / rate, attack_ms / 1000, release_ms / 1000)
    centres = (np.arange(len(gain)) + 0.5) * block
    g = 10 ** (np.interp(np.arange(x.shape[0]), centres, gain) / 20)
    return x + (g[:, None] - 1.0) * band


def _gated(signal: Any, profile: Any, floor: float, size: int, hop: int) -> Any:
    """``signal`` with each STFT bin below ``profile`` lowered towards ``floor`` (a 3 x 3 smoothed gain), rebuilt by
    overlap-add."""
    np = numpy()
    window = np.hanning(size)
    spec = stft(signal, size, hop)
    mag = np.abs(spec)
    gain = floor + (1 - floor) * np.clip((mag - profile) / np.maximum(profile, 1e-12), 0.0, 1.0)
    padded = np.pad(gain, 1, mode="edge")
    gain = sum(padded[i:i + gain.shape[0], j:j + gain.shape[1]] for i in range(3) for j in range(3)) / 9.0
    frames = np.fft.irfft(spec * gain, size, axis=1) * window
    count = frames.shape[0]
    total = np.zeros((count - 1) * hop + size)
    norm = np.zeros_like(total)
    for i in range(count):
        total[i * hop:i * hop + size] += frames[i]
        norm[i * hop:i * hop + size] += window ** 2
    return (total / np.where(norm > 1e-8, norm, 1.0))[size // 2:size // 2 + len(signal)]


def reduce_noise(x: Any, rate: int, noise: tuple[int, int] | None, reduction_db: float,
                 size: int = 2048, hop: int = 512) -> tuple[Any, tuple[int, int]]:
    """Spectral gating: the noise's average magnitude in each frequency (from ``noise``, a span of samples, or from
    the quietest tenth of the frames), doubled, is a threshold; each frame's bins below it are lowered by up to
    ``reduction_db``, smoothly, and the sound is rebuilt by overlap-add. Long sound is done in pieces whose edges
    overlap by whole frames, so the result is the same as in one piece. Returns it and the span the noise came from."""
    np = numpy("reduce noise")
    window = np.hanning(size)
    signal = mono(x)
    starts = np.arange(0, max(1, len(signal) - size + 1), hop)
    if noise is not None:
        picked = starts[(starts + size // 2 >= noise[0]) & (starts + size // 2 < noise[1])]
        if not len(picked):
            raise MediaError(INPUT_INVALID, "the noise span is shorter than one analysis frame")
        span = noise
    else:
        energy = np.array([float(np.sum(signal[s:s + size] ** 2)) for s in starts])
        picked = starts[energy <= np.quantile(energy, 0.1)]
        span = (int(picked.min()), int(picked.max() + size))
    floor = 10 ** (-reduction_db / 20)
    out = np.zeros_like(x)
    piece, pad = hop * 512, size * 2
    whole = np.pad(x, ((0, size), (0, 0)))  # a sound shorter than one frame is one frame, padded
    for c in range(x.shape[1]):
        frames = np.stack([whole[s:s + size, c] for s in picked]) * window
        profile = np.abs(np.fft.rfft(frames, axis=1)).mean(axis=0) * 2.0
        for start in range(0, x.shape[0], piece):
            lo = max(0, start - pad)
            chunk = x[lo:min(x.shape[0], start + piece + pad), c]
            done = _gated(chunk, profile, floor, size, hop)
            out[start:start + piece, c] = done[start - lo:start - lo + piece][:len(out[start:start + piece, c])]
    return out, span

__all__ = ["MAX_SAMPLES", "ONSET_BLOCK", "RESAMPLE_STEP", "biquad", "compress", "convolve", "correlation", "de_ess",
           "filtered", "fits", "full_scale", "gate",
           "high_band",
           "impulse_response", "kweighting", "limit", "load", "loudness", "measure", "mono", "note_name", "numpy",
           "peak_envelope", "pitch", "reduce_noise", "resample", "silences", "spectrum", "stft", "tempo", "true_peak",
           "wav_bytes"]
