# SPDX-License-Identifier: Apache-2.0
"""WAV files with the standard library alone: read a file's header and samples, write PCM, and inspect one.

``_``-prefixed, so it never registers as a component. Every member of the family that makes sound writes WAV and runs
:func:`inspect_wav` on the file it wrote, from disk, before it returns anything; a file that fails is a refusal with
the failed check's code, never a result.

* :func:`read_info` parses the RIFF header itself (PCM of 8, 16, 24 or 32 bits, 32- or 64-bit float, and the
  extensible header carrying either): rate, channels, sample width, frames, where the samples are. A header that lies
  (a data chunk longer than the file, a partial frame, a block size that does not match) is refused, never read past.
* :func:`blocks` yields the samples a block at a time as ``array`` values with their full scale, so nothing holds a
  whole long file; :func:`levels` measures peak, RMS and the share of samples above the silence floor from them.
* :func:`inspect_wav`: the file exists and is not empty (``G6_E_MEDIA_NO_OUTPUT``); it is a WAV that decodes in full,
  every sample finite (``_UNDECODABLE``, ``_FORMAT_MISMATCH``); its rate and channels are the ones asked for
  (``_STREAM_MISMATCH``); its length is the one asked for within the tolerance given (``_DURATION_MISMATCH``); and it
  is not silent (``_SILENT``): at least :data:`MIN_SOUND_FRACTION` of its samples are louder than
  :data:`SILENCE_DBFS`, so an empty, all-zero or nearly silent file fails, and so does one of a few stray clicks.
* :func:`pcm_wav` wraps raw little-endian PCM (what a speech service sends) in a WAV header.

What it cannot judge: whether the sound is the one wanted. It measures; it does not listen.
"""
from __future__ import annotations

import io
import math
import operator
import struct
import sys
import wave
from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.media._types import (
    DURATION_MISMATCH,
    FORMAT_MISMATCH,
    NO_OUTPUT,
    SILENT,
    STREAM_MISMATCH,
    UNDECODABLE,
    Check,
    Inspection,
    MediaError,
)

#: A sample counts as sound when it is louder than this (decibels below full scale).
SILENCE_DBFS = -60.0
#: The share of samples that must be sound for a file not to be silent (0.1 %).
MIN_SOUND_FRACTION = 0.001
#: The largest WAV the inspector reads (bytes), and how many frames it reads at a time.
MAX_BYTES = 1024 * 1024 * 1024
BLOCK_FRAMES = 1 << 16
#: The quietest level reported, in place of minus infinity (JSON has no infinity).
FLOOR_DBFS = -200.0
PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE
INSPECTOR = "g6.media sound inspector (WAV, standard library)"
_U8 = bytes((b - 128) & 0xFF for b in range(256))


@dataclass(frozen=True)
class WavInfo:
    """A WAV file's layout, read from its header and checked against its size."""

    rate: int
    channels: int
    bits: int
    floating: bool
    frames: int
    data_offset: int
    data_bytes: int

    @property
    def duration(self) -> float:
        return self.frames / self.rate

    @property
    def encoding(self) -> str:
        return f"{'f' if self.floating else 'u' if self.bits == 8 else 's'}{self.bits}le"


def _chunks(f: Any, size: int) -> Iterator[tuple[bytes, int, int]]:
    """Each RIFF chunk after the ``WAVE`` tag: its id, the offset of its body and its declared size."""
    at = 12
    while at + 8 <= size:
        f.seek(at)
        head = f.read(8)
        ident, length = head[:4], struct.unpack("<I", head[4:])[0]
        yield ident, at + 8, length
        at += 8 + length + (length & 1)


def read_info(path: Path | str) -> WavInfo:
    """The layout of the WAV at ``path``; :class:`MediaError` (``G6_E_MEDIA_FORMAT_MISMATCH`` when it is not a WAV,
    ``_UNDECODABLE`` when its header does not hold together)."""
    path = Path(path)
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            kind = "an RF64 file" if head[:4] == b"RF64" else "not a RIFF WAVE file"
            raise MediaError(FORMAT_MISMATCH, f"{path.name} is {kind}")
        fmt: tuple[int, ...] | None = None
        data: tuple[int, int] | None = None
        for ident, offset, length in _chunks(f, size):
            if ident == b"fmt ":
                f.seek(offset)
                body = f.read(min(length, 64))
                if len(body) < 16:
                    raise MediaError(UNDECODABLE, f"{path.name}: the fmt chunk is too short")
                tag, channels, rate, _, align, bits = struct.unpack("<HHIIHH", body[:16])
                if tag == EXTENSIBLE:
                    if len(body) < 26:
                        raise MediaError(UNDECODABLE, f"{path.name}: the extensible fmt chunk is too short")
                    tag = struct.unpack("<H", body[24:26])[0]
                fmt = (tag, channels, rate, align, bits)
            elif ident == b"data":
                if offset + length > size:
                    raise MediaError(UNDECODABLE, f"{path.name}: the data chunk says {length} bytes but only "
                                                  f"{size - offset} follow (a truncated or streamed file)")
                data = (offset, length)
            if fmt and data:
                break
    if fmt is None or data is None:
        raise MediaError(UNDECODABLE, f"{path.name}: no {'fmt' if fmt is None else 'data'} chunk")
    tag, channels, rate, align, bits = fmt
    floating = tag == FLOAT
    if tag not in (PCM, FLOAT) or (floating and bits not in (32, 64)) or (not floating and bits not in (8, 16, 24, 32)):
        raise MediaError(UNDECODABLE, f"{path.name}: format {tag} with {bits}-bit samples is not one G6 reads (PCM of "
                                      "8, 16, 24 or 32 bits, or 32- or 64-bit float)")
    if not (1 <= channels <= 32 and 1000 <= rate <= 768_000) or align != channels * bits // 8:
        raise MediaError(UNDECODABLE, f"{path.name}: {channels} channels at {rate} Hz with blocks of {align} bytes "
                                      "do not hold together")
    if data[1] % align:
        raise MediaError(UNDECODABLE, f"{path.name}: the data is not a whole number of frames")
    return WavInfo(rate, channels, bits, floating, data[1] // align, data[0], data[1])


def _decode(raw: bytes, info: WavInfo) -> tuple[array, float]:
    """One block of interleaved samples as an ``array`` and its full scale."""
    if info.floating:
        values = array("f" if info.bits == 32 else "d", raw)
        scale = 1.0
    elif info.bits == 8:
        values, scale = array("b", raw.translate(_U8)), 128.0
    elif info.bits == 16:
        values, scale = array("h", raw), 32768.0
    elif info.bits == 24:
        wide = bytearray(len(raw) // 3 * 4)
        wide[1::4], wide[2::4], wide[3::4] = raw[0::3], raw[1::3], raw[2::3]
        values, scale = array("i", bytes(wide)), 2147483648.0
    else:
        values, scale = array("i", raw), 2147483648.0
    if sys.byteorder == "big" and values.itemsize > 1:
        values.byteswap()
    return values, scale


def blocks(path: Path | str, info: WavInfo | None = None,
           frames: int = BLOCK_FRAMES) -> Iterator[tuple[array, float]]:
    """The samples of the WAV at ``path`` a block of ``frames`` at a time (interleaved), each with its full scale."""
    info = info or read_info(path)
    step = frames * info.channels * info.bits // 8
    with open(path, "rb") as f:
        f.seek(info.data_offset)
        left = info.data_bytes
        while left > 0:
            raw = f.read(min(step, left))
            if not raw:
                raise MediaError(UNDECODABLE, f"{Path(path).name}: the samples end before the header says")
            left -= len(raw)
            yield _decode(raw, info)


def dbfs(level: float) -> float:
    """A level (full scale 1.0) in decibels below full scale, never below :data:`FLOOR_DBFS`."""
    return round(max(FLOOR_DBFS, 20 * math.log10(level)) if level > 0 else FLOOR_DBFS, 2)


@dataclass(frozen=True)
class Levels:
    peak: float
    rms: float
    sound_fraction: float
    samples: int

    def measured(self) -> dict[str, Any]:
        return {"peak_dbfs": dbfs(self.peak), "rms_dbfs": dbfs(self.rms),
                "sound_fraction": round(self.sound_fraction, 6)}


def levels(path: Path | str, info: WavInfo | None = None) -> Levels:
    """Peak, RMS and the share of samples louder than :data:`SILENCE_DBFS`, measured over every sample; a sample that
    is not a finite number is :class:`MediaError` (``G6_E_MEDIA_UNDECODABLE``)."""
    info = info or read_info(path)
    floor = 10 ** (SILENCE_DBFS / 20)
    peak = squares = 0.0
    loud = count = 0
    for values, scale in blocks(path, info):
        if not values:
            continue
        high, low = max(values), min(values)
        if info.floating and not (math.isfinite(high) and math.isfinite(low)):
            raise MediaError(UNDECODABLE, f"{Path(path).name}: a sample is not a finite number")
        peak = max(peak, max(high, -low) / scale)
        squares += math.fsum(map(operator.mul, values, values)) / (scale * scale)
        limit = floor * scale
        loud += len(values) - sum(map(limit.__ge__, map(abs, values)))
        count += len(values)
    if info.floating and not math.isfinite(squares):
        raise MediaError(UNDECODABLE, f"{Path(path).name}: a sample is not a finite number")
    return Levels(min(peak, 1e6), math.sqrt(squares / count) if count else 0.0, loud / count if count else 0.0, count)


def exists_check(path: Path) -> Check:
    if not path.is_file():
        return Check(name="exists", passed=False, detail=f"no file at {path}", code=NO_OUTPUT)
    size = path.stat().st_size
    if size == 0:
        return Check(name="exists", passed=False, detail="the file is empty", code=NO_OUTPUT)
    if size > MAX_BYTES:
        return Check(name="exists", passed=False, detail=f"{size} bytes is more than the inspector reads ({MAX_BYTES})",
                     code=UNDECODABLE)
    return Check(name="exists", passed=True, detail=f"{size} bytes")


def duration_check(found: float, want: float | None, tolerance: float | None,
                   minimum: float | None = None) -> Check | None:
    """Whether a length measured in seconds is the one asked for (within ``tolerance``), and at least ``minimum``."""
    if want is not None:
        allowed = tolerance if tolerance is not None else 0.005
        ok = abs(found - want) <= allowed
        return Check(name="duration_matches_request", passed=ok, code="" if ok else DURATION_MISMATCH,
                     detail=f"{found:.3f} s" + (f", as asked ({want:.3f} s, within {allowed:g} s)" if ok else
                                                f", not the {want:.3f} s asked for (within {allowed:g} s)"))
    if minimum is not None:
        ok = found >= minimum
        return Check(name="duration_matches_request", passed=ok, code="" if ok else DURATION_MISMATCH,
                     detail=f"{found:.3f} s" + ("" if ok else f", shorter than the {minimum:g} s it must last"))
    return None


def silence_check(found: Levels) -> Check:
    ok = found.sound_fraction >= MIN_SOUND_FRACTION
    detail = (f"{found.sound_fraction:.2%} of the samples are louder than {SILENCE_DBFS:g} dBFS (peak "
              f"{dbfs(found.peak)} dBFS)")
    if not ok:
        detail += f"; the floor is {MIN_SOUND_FRACTION:.1%}"
    return Check(name="not_silent", passed=ok, code="" if ok else SILENT, detail=detail)


def inspect_wav(path: Path | str, *, rate: int | None = None, channels: int | None = None,
                duration: float | None = None, tolerance: float | None = None,
                min_duration: float | None = None) -> Inspection:
    """Measure the WAV at ``path`` against the ``rate``, ``channels`` and ``duration`` (seconds, within
    ``tolerance``; default 5 ms) asked for, or at least ``min_duration``; see the module docstring."""
    path = Path(path)
    checks = [exists_check(path)]
    if not checks[0].passed:
        return Inspection(inspector=INSPECTOR, kind="audio", checks=tuple(checks))
    try:
        info = read_info(path)
        found = levels(path, info)
    except MediaError as exc:
        checks.append(Check(name="decodes", passed=False, code=exc.code, detail=exc.reason[:500]))
        return Inspection(inspector=INSPECTOR, kind="audio", checks=tuple(checks))
    except (OSError, struct.error, ValueError, OverflowError) as exc:
        checks.append(Check(name="decodes", passed=False, code=UNDECODABLE, detail=f"{type(exc).__name__}: {exc}"[:500]))
        return Inspection(inspector=INSPECTOR, kind="audio", checks=tuple(checks))
    measured: dict[str, Any] = {"format": "WAV", "encoding": info.encoding, "rate": info.rate,
                                "channels": info.channels, "bits": info.bits, "frames": info.frames,
                                "duration_s": round(info.duration, 6), **found.measured()}
    checks.append(Check(name="decodes", passed=True, detail=f"a WAV of {info.frames} frames, {info.encoding}"))
    for name, want, got in (("rate_matches_request", rate, info.rate), ("channels_match_request", channels,
                                                                         info.channels)):
        if want is not None:
            checks.append(Check(name=name, passed=got == want, code="" if got == want else STREAM_MISMATCH,
                                detail=f"{got}" + (", as asked" if got == want else f", not the {want} asked for")))
    timed = duration_check(info.duration, duration, tolerance, min_duration)
    if timed is not None:
        checks.append(timed)
    checks.append(silence_check(found))
    return Inspection(inspector=INSPECTOR, kind="audio", checks=tuple(checks), measured=measured, mime="audio/wav")


def pcm_wav(pcm: bytes, *, rate: int, channels: int = 1, width: int = 2) -> bytes:
    """Raw little-endian PCM wrapped in a WAV header; a partial last frame is dropped."""
    align = channels * width
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm[: len(pcm) - len(pcm) % align])
    return out.getvalue()


__all__ = ["BLOCK_FRAMES", "FLOOR_DBFS", "INSPECTOR", "MAX_BYTES", "MIN_SOUND_FRACTION", "SILENCE_DBFS", "Levels",
           "WavInfo", "blocks", "dbfs", "duration_check", "exists_check", "inspect_wav", "levels", "pcm_wav", "read_info",
           "silence_check"]
