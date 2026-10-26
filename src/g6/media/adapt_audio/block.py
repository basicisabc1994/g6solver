# SPDX-License-Identifier: Apache-2.0
"""AdaptAudioBlock, the registry and MCP door (``g6_invoke``) to adapt_audio.

Every op reads a WAV from the media folder (or inline base64, at most 25 MiB). The analyses write nothing and answer
``G6_E_MEDIA_MEASURED``. Every edit measures its source first, refuses a result that would go past full scale (never
clipping it silently), writes the result as a fresh WAV in the media folder, inspects it from disk, measures it again
(peak, true peak, loudness, length, rate, channels) with the op's own checks (a loudness or true-peak target met), and
only then gives it the name asked for. ``transcode`` alone runs a program (ffmpeg) and is the one sensitive op.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media import _dsp, _programs, _sound, _wav
from g6.media._av import CONFINE, FFMPEG, FFPROBE, inspect_media, probe, streams
from g6.media._types import (
    BACKEND_FAILED,
    FORMAT_MISMATCH,
    INPUT_INVALID,
    MEASURED,
    PROBED,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    check_name,
    discard,
    media_artifact,
    media_file,
    media_root,
    media_source,
    place,
    refuse,
)
from g6.media.adapt_audio import edits

MAX_BASE64_CHARS = 4 * ((25 * 1024 * 1024 + 2) // 3)
ANALYSES = ("metadata", "loudness", "spectrum", "pitch", "bpm", "silence", "phase_correlation", "crest_factor")
_OPS = {
    "metadata": "metadata(source): format, rate, channels, bits, frames, length, peak, RMS and how much of it is "
                "sound, read with the standard library alone",
    "loudness": "loudness(source): integrated loudness by ITU-R BS.1770-4 (LUFS), true peak, peak and RMS",
    "spectrum": "spectrum(source): the strongest frequencies, centroid, roll-off and low, mid and high band energy",
    "pitch": "pitch(source): the fundamental frequency of its loudest half second (YIN) and the nearest note",
    "bpm": "bpm(source): the tempo from the repetition of its onsets",
    "silence": "silence(source, threshold_dbfs, min_silence): the stretches quieter than the threshold",
    "phase_correlation": "phase_correlation(source): how alike a stereo pair is, from -1 to +1",
    "crest_factor": "crest_factor(source): peak over RMS in dB, with the true peak",
    "trim": "trim(source, start, end): the part from start to end, cut on exact samples",
    "concat": "concat(sources): two to sixteen WAVs of one rate and channel count, one after another",
    "mix": "mix(sources, gains_db): WAVs of one rate and channel count summed, each at its gain",
    "fade": "fade(source, fade_in, fade_out, curve=linear|equal_power)",
    "gain": "gain(source, gain_db)",
    "resample": "resample(source, rate): band-limited windowed-sinc resampling",
    "convert": "convert(source, channels, bits): mono or stereo, 16, 24 or 32 bits",
    "eq": "eq(source, bands): peak, low_shelf, high_shelf, low_pass, high_pass and band_pass filters in a row",
    "compress": "compress(source, threshold_db, ratio, attack_ms, release_ms, knee_db, makeup_db)",
    "limit": "limit(source, ceiling_dbtp, release_ms): a true-peak limiter; the true peak is measured again",
    "gate": "gate(source, threshold_db, floor_db, attack_ms, hold_ms, release_ms)",
    "de_ess": "de_ess(source, frequency_hz, threshold_db, reduction_db): the band above frequency_hz lowered while it "
              "is loud",
    "noise_reduce": "noise_reduce(source, noise_start, noise_end, reduction_db): spectral gating against the noise of "
                    "that span (or of the quietest frames)",
    "normalise_loudness": "normalise_loudness(source, target_lufs, ceiling_dbtp, limit): to a loudness target, refused "
                          "when the true peak would pass the ceiling unless limit=true; measured again",
    "dither": "dither(source, bits, seed): to 16 or 24 bits with TPDF dither",
    "transcode": "transcode(source, format): any sound file ffmpeg reads to WAV, or WAV to flac, mp3, ogg, opus or m4a "
                 "(sensitive: it runs ffmpeg on your machine; from Python, or over MCP when the server's "
                 "G6_MCP_SENSITIVE lists it)",
}
SENSITIVE_OPS: tuple[str, ...] = ("transcode",)
_FORMATS = {"wav": (".wav", ("wav",)), "flac": (".flac", ("flac",)), "mp3": (".mp3", ("mp3",)),
            "ogg": (".ogg", ("ogg",)), "opus": (".opus", ("ogg",)), "m4a": (".m4a", ("mp4", "m4a"))}


def _plain(value: str) -> str:
    if value:
        try:
            check_name(value)
        except MediaError as exc:
            raise ValueError(exc.reason) from None
    return value


class Band(BaseModel):
    """One filter of an ``eq``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["peak", "low_shelf", "high_shelf", "low_pass", "high_pass", "band_pass"]
    frequency_hz: float = Field(gt=10, le=96000, allow_inf_nan=False)
    gain_db: float = Field(0.0, ge=-30, le=30, allow_inf_nan=False)
    q: float = Field(0.7071, ge=0.1, le=20, allow_inf_nan=False)


class AudioInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["metadata", "loudness", "spectrum", "pitch", "bpm", "silence", "phase_correlation", "crest_factor",
                "trim", "concat", "mix", "fade", "gain", "resample", "convert", "eq", "compress", "limit", "gate",
                "de_ess", "noise_reduce", "normalise_loudness", "dither", "transcode", "ops", "help"] = "help"
    source: str = Field("", max_length=100)
    audio_base64: str = Field("", max_length=MAX_BASE64_CHARS)
    sources: list[str] = Field(default_factory=list, max_length=16)
    gains_db: list[float] = Field(default_factory=list, max_length=16)
    start: float = Field(0.0, ge=0, le=86400, allow_inf_nan=False)
    end: float | None = Field(None, gt=0, le=86400, allow_inf_nan=False)
    fade_in: float = Field(0.0, ge=0, le=600, allow_inf_nan=False)
    fade_out: float = Field(0.0, ge=0, le=600, allow_inf_nan=False)
    curve: Literal["linear", "equal_power"] = "linear"
    gain_db: float = Field(0.0, ge=-96, le=48, allow_inf_nan=False)
    rate: int | None = Field(None, ge=8000, le=192000)
    channels: Literal[1, 2] | None = None
    bits: Literal[16, 24, 32] | None = None
    bands: list[Band] = Field(default_factory=list, max_length=16)
    threshold_db: float | None = Field(None, ge=-90, le=0, allow_inf_nan=False)
    ratio: float = Field(4.0, ge=1, le=100, allow_inf_nan=False)
    attack_ms: float = Field(10.0, ge=0.1, le=1000, allow_inf_nan=False)
    release_ms: float = Field(100.0, ge=1, le=5000, allow_inf_nan=False)
    hold_ms: float = Field(50.0, ge=0, le=2000, allow_inf_nan=False)
    knee_db: float = Field(0.0, ge=0, le=24, allow_inf_nan=False)
    makeup_db: float = Field(0.0, ge=-24, le=24, allow_inf_nan=False)
    floor_db: float = Field(-40.0, ge=-120, le=0, allow_inf_nan=False)
    ceiling_dbtp: float = Field(-1.0, ge=-24, le=0, allow_inf_nan=False)
    target_lufs: float = Field(-16.0, ge=-70, le=-5, allow_inf_nan=False)
    limit: bool = False
    frequency_hz: float = Field(6000.0, gt=1000, le=20000, allow_inf_nan=False)
    reduction_db: float = Field(12.0, ge=0, le=60, allow_inf_nan=False)
    noise_start: float = Field(0.0, ge=0, le=86400, allow_inf_nan=False)
    noise_end: float | None = Field(None, gt=0, le=86400, allow_inf_nan=False)
    threshold_dbfs: float = Field(-50.0, ge=-120, le=0, allow_inf_nan=False)
    min_silence: float = Field(0.25, ge=0.01, le=600, allow_inf_nan=False)
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    format: Literal["wav", "flac", "mp3", "ogg", "opus", "m4a"] = "wav"
    timeout: float = Field(_programs.DEFAULT_SECONDS, ge=1, le=_programs.MAX_SECONDS, allow_inf_nan=False)
    name: str = Field("", max_length=90)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("source", "name")
    @classmethod
    def _plain_names(cls, value: str) -> str:
        return _plain(value)

    @field_validator("sources")
    @classmethod
    def _plain_sources(cls, value: list[str]) -> list[str]:
        return [_plain(v) for v in value]

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> AudioInput:
        if self.op in ("ops", "help"):
            return self
        if self.op in ("concat", "mix"):
            if len(self.sources) < 2:
                raise ValueError(f"{self.op} needs sources: two to sixteen WAV file names in the media folder")
        elif self.op == "transcode":
            if not self.source:
                raise ValueError("transcode needs source: a file name in the media folder")
        elif bool(self.source) == bool(self.audio_base64):
            raise ValueError(f"{self.op} needs a sound: source (a WAV in the media folder) or audio_base64, one of them")
        needs = {"resample": "rate", "eq": "bands", "convert": ("channels", "bits"), "dither": "bits"}
        wanted = needs.get(self.op)
        if wanted and not any(getattr(self, w) for w in (wanted if isinstance(wanted, tuple) else (wanted,))):
            raise ValueError(f"{self.op} needs {' or '.join(wanted) if isinstance(wanted, tuple) else wanted}")
        if self.op == "trim" and self.end is not None and self.end <= self.start:
            raise ValueError("trim's end must come after its start")
        if self.op == "noise_reduce" and self.noise_end is not None and self.noise_end <= self.noise_start:
            raise ValueError("noise_end must come after noise_start")
        return self


def _ids(d: AudioInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _named(name: str) -> Path:
    return media_source(name)


def _not_wav(path: Path, exc: MediaError) -> MediaError:
    if exc.code == FORMAT_MISMATCH:
        return MediaError(FORMAT_MISMATCH, f"{exc.reason}; adapt_audio edits WAV: turn it into one with transcode "
                                           "(it needs ffmpeg)")
    return exc


def _layout(name: str) -> _wav.WavInfo:
    """The header of the WAV ``name`` in the media folder; its samples are not read."""
    path = _named(name)
    try:
        return _wav.read_info(path)
    except MediaError as exc:
        raise _not_wav(path, exc) from None


def _read(d: AudioInput, name: str = "") -> tuple[Any, int, _wav.WavInfo, str]:
    """The samples of ``name`` (or of the op's source or inline sound), its rate, its layout and its SHA-256."""
    if d.audio_base64 and not name:
        try:
            data = base64.b64decode(d.audio_base64.split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError):
            raise MediaError(INPUT_INVALID, "audio_base64 is not valid base64") from None
        with tempfile.TemporaryDirectory(prefix="g6-audio-", ignore_cleanup_errors=True) as scratch:
            path = Path(scratch) / "inline.wav"
            path.write_bytes(data)
            try:
                x, rate, info = _dsp.load(path)
            except MediaError as exc:
                raise _not_wav(path, exc) from None
        return x, rate, info, hashlib.sha256(data).hexdigest()
    path = _named(name or d.source)
    try:
        x, rate, info = _dsp.load(path)
    except MediaError as exc:
        raise _not_wav(path, exc) from None
    return x, rate, info, hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata(d: AudioInput) -> MediaResult:
    """The standard library's account of the WAV: it needs no extra."""
    with tempfile.TemporaryDirectory(prefix="g6-audio-", ignore_cleanup_errors=True) as scratch:
        if d.audio_base64:
            try:
                path = Path(scratch) / "inline.wav"
                path.write_bytes(base64.b64decode(d.audio_base64.split(",", 1)[-1], validate=True))
            except (binascii.Error, ValueError):
                return refuse(d.op, INPUT_INVALID, "audio_base64 is not valid base64", **_ids(d))
        else:
            path = _named(d.source)
        try:
            info = _wav.read_info(path)
            levels = _wav.levels(path, info)
        except MediaError as exc:
            exc = _not_wav(path, exc)
            return refuse(d.op, exc.code, exc.reason, **_ids(d))
    found = {"format": "WAV", "encoding": info.encoding, "rate": info.rate, "channels": info.channels,
             "bits": info.bits, "frames": info.frames, "duration_s": round(info.duration, 6), **levels.measured(),
             "silent": levels.sound_fraction < _wav.MIN_SOUND_FRACTION}
    return answer(d.op, f"a WAV of {info.duration:.3f} s at {info.rate} Hz, {info.channels} channels, measured",
                  card=MEASURED, evidence=[{"kind": "sound_measurement", "measured": found}], measured=found,
                  **_ids(d))


def _analyse(d: AudioInput) -> MediaResult:
    x, rate, _, digest = _read(d)
    found = edits.analyse(d.op, d, x, rate)
    return answer(d.op, f"{d.op} measured from the samples", card=MEASURED,
                  evidence=[{"kind": "sound_measurement", "op": d.op, "source_sha256": digest, "measured": found}],
                  measured=found, **_ids(d))


def _keep(d: AudioInput, edit: edits.Edit, before: dict[str, Any], bits: int, sources: dict[str, str],
          source: _wav.WavInfo) -> MediaResult:
    """Write, inspect and measure the edit's samples against the length and channels the request and the source's
    header say it has (never the samples' own), run the op's own checks on the written file, then place it."""
    own = (lambda _written, after: edit.checks(after)) if edit.checks else None
    frames = edit.frames if edit.frames is not None else source.frames
    channels = edit.channels if edit.channels is not None else source.channels
    return _sound.keep(d.op, edit.samples, edit.rate, bits, frames=frames, channels=channels, name=d.name,
                       overwrite=d.overwrite, ids=_ids(d), params=edit.params, checks=own, before=before,
                       dither_seed=edit.dither_seed, recipe={"sources": sources}, fields=edit.fields)

def _edit(d: AudioInput) -> MediaResult:
    if d.op in ("concat", "mix"):
        if d.op == "concat":  # the joined sound's size from the headers, before a sample is read
            _dsp.fits(sum(info.frames * info.channels for info in map(_layout, d.sources)), "the joined sound")
        read = [_read(d, name) for name in d.sources]
        edit = edits.join(d, [(x, rate) for x, rate, _, _ in read], mix=d.op == "mix")
        bits = d.bits or max(info.bits if info.bits in (16, 24, 32) and not info.floating else 24
                             for _, _, info, _ in read)
        before = {"sources": [_sound.probe(x, rate) for x, rate, _, _ in read]}
        return _keep(d, edit, before, bits, {n: r[3] for n, r in zip(d.sources, read)}, read[0][2])
    x, rate, info, digest = _read(d)
    edit = edits.EDITS[d.op](d, x, rate)
    bits = d.bits or (info.bits if info.bits in (16, 24, 32) and not info.floating else 24)
    return _keep(d, edit, _sound.probe(x, rate), bits, {d.source or "audio_base64": digest}, info)


def _transcode(d: AudioInput) -> MediaResult:
    """Any sound file ffmpeg reads into WAV, or a WAV into another format, through the family's confined ffmpeg."""
    ids = _ids(d)
    temp: Path | None = None
    try:
        ffmpeg = _programs.find(FFMPEG)
        _programs.find(FFPROBE)
        source = _named(d.source)
        found = streams(probe(source))
        if not found["audio_streams"]:
            raise MediaError(INPUT_INVALID, f"{d.source} has no sound")
        suffix, formats = _FORMATS[d.format]
        final = media_file(d.name or f"transcode-{secrets.token_hex(6)}", suffix)
        temp = media_root() / f".partial-{secrets.token_hex(8)}{suffix}"
        codec = ["-c:a", "pcm_s16le" if (d.bits or 16) == 16 else f"pcm_s{d.bits}le"] if d.format == "wav" else []
        args = ["-nostdin", "-hide_banner", "-nostats", "-v", "error", "-n", *CONFINE, "-i", source.name, "-map",
                "0:a:0", "-vn", *codec, temp.name]
        ran = _programs.run(ffmpeg.where, args, seconds=d.timeout, cwd=media_root(), name="ffmpeg")
        record = {"kind": "program_run", "program": "ffmpeg", "version": ffmpeg.version, "exit_code": ran.code,
                  "argv": [FFMPEG.command, *args[:-1], "{out}"]}
        if ran.code != 0:
            discard(temp)
            return refuse(d.op, BACKEND_FAILED, f"ffmpeg failed (exit {ran.code}): {ran.text(400)}"[:500],
                          evidence=[record], **ids)
        inspection = inspect_media(temp, kind="audio", formats=formats, audio=True, video=False,
                                   duration=found["duration_s"], tolerance=0.15, seconds=d.timeout)
        if not inspection.passed:
            discard(temp)
            return refuse(d.op, inspection.failure.code, inspection.failure.detail,
                          evidence=[inspection.evidence(), record], **ids)
        written = place(temp, final, overwrite=d.overwrite)
        temp = None
    except MediaError as exc:
        if temp is not None:
            discard(temp)
        return refuse(d.op, exc.code, exc.reason, **ids)
    recipe = {"op": "transcode", "format": d.format, "argv": record["argv"]}
    provenance = Provenance(backend="ffmpeg", backend_version=ffmpeg.version[:200], technique="transcode",
                            recipe=recipe, recipe_sha256=hashlib.sha256(json.dumps(recipe).encode()).hexdigest())
    artifact = media_artifact(final, inspection, provenance, {"format": d.format, "duration": found["duration_s"]})
    return answer(d.op, f"{d.source} as {d.format}, probed: it decodes, lasts as long as its source and is not "
                        "silent", artifact=artifact, card=PROBED, evidence=[record], written=written, **ids)


@block_contract(
    purpose="edit, level, clean and measure sound in WAV files, each result inspected and measured again before it is "
            "returned, and never clipped silently",
    mitigates_failure_modes=("audio_check_failed",),
    verification_method="g6.media sound inspector on every WAV written (decodes, rate, channels, length, not silent), "
                        "a full-scale check before writing, and the result measured again: integrated loudness "
                        "(BS.1770-4) and true peak against the op's target",
)
@component_maturity(
    "beta",
    reason="NumPy DSP with BS.1770-4 loudness and oversampled true peak; every edit measured before and after and "
           "refused when it would clip or misses its target; qualified-draft at best: nothing judges how it sounds",
)
class AdaptAudioBlock(AIBlock[AudioInput, dict, dict]):
    """Edit or measure a sound and say what was measured. Never ``verified``."""

    def __init__(self, name: str = "adapt_audio", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AudioInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            if data.op == "metadata":
                result = _metadata(data)
            elif data.op == "transcode":
                result = _transcode(data)
            elif data.op in ANALYSES:
                result = _analyse(data)
            else:
                result = _edit(data)
        except MediaError as exc:
            result = refuse(data.op, exc.code, exc.reason, **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["ANALYSES", "MAX_BASE64_CHARS", "SENSITIVE_OPS", "AdaptAudioBlock", "AudioInput", "Band"]
