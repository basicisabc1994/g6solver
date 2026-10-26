# SPDX-License-Identifier: Apache-2.0
"""AdaptFFmpegBlock, the registry and MCP door (``g6_invoke``) to adapt_ffmpeg.

Every op runs ffmpeg or ffprobe, programs on the person's machine, so every one is in ``__g6_sensitive_ops__``: it runs
from Python, and over MCP only when the person who runs the server lists it (or ``adapt_ffmpeg``) in
``G6_MCP_SENSITIVE``. Sources are file names in the media folder; ffmpeg writes a fresh file there, which is probed from
disk against what the request and the sources say it must be, and only then given the name asked for. ffmpeg's exit
code is not a check: a result is ``qualified-draft`` at best, and a file that fails is removed and refused.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media import _programs
from g6.media._av import FFMPEG, FFPROBE, inspect_av, inspect_media, probe, streams
from g6.media._types import (
    BACKEND_FAILED,
    INPUT_INVALID,
    LOUDNESS_MISSED,
    MEASURED,
    PROBED,
    SILENT,
    UNDECODABLE,
    Check,
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
    sha256_file,
)
from g6.media.adapt_ffmpeg import commands
from g6.media.adapt_ffmpeg.commands import FORMATS, PICTURE_FORMATS, SOUND_FORMATS, VALUE, VIDEO_FORMATS

_SAYS = " (sensitive: it runs ffmpeg on your machine; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_OPS = {
    "probe": "probe(source): ffprobe's account of a file in the media folder, and whether ffmpeg decodes it, its sound "
             "is silent and its pictures blank",
    "convert": "convert(source, format, video_codec, audio_codec, crf, audio_bitrate): the same media in another format",
    "trim": "trim(source, start, end, accurate): the part from start to end; accurate=false is a fast stream copy, "
            "whose cut lands on key frames, so its measured length is compared like any other",
    "concat": "concat(sources, format): two to twenty files one after another (the first's frame size and rate)",
    "extract_audio": "extract_audio(source, format): the sound alone",
    "add_audio": "add_audio(source, audio, format): a video with the sound of another file, as long as the shorter",
    "resize": "resize(source, width, height): a video at a new frame size; one side alone keeps the shape",
    "framerate": "framerate(source, fps): a video at a new frame rate",
    "filter": "filter(source, filter_expr): a chain of allowed video filters (eq, crop, scale, hflip and others)",
    "thumbnail": "thumbnail(source, time, width, height, format=png|jpg): one frame as a picture",
    "gif": "gif(source, fps, width, start, end): an animated GIF with its own palette",
    "overlay": "overlay(source, overlay, x, y): a picture or video laid on a video at x, y",
    "speed": "speed(source, factor): faster (above 1) or slower, the sound kept in pitch",
    "normalize_audio": "normalize_audio(source, target_lufs, true_peak, lra): EBU R128 loudness in two passes, the "
                       "result measured again against the target",
    "subtitles": "subtitles(source, subtitles): SRT, ASS or WebVTT subtitles drawn onto a video",
}
SENSITIVE_OPS: tuple[str, ...] = tuple(_OPS)
_NAMED = ("source", "audio", "overlay", "subtitles")


def _plain(value: str) -> str:
    if value:
        try:
            check_name(value)
        except MediaError as exc:
            raise ValueError(exc.reason) from None
    return value


class FFmpegInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["probe", "convert", "trim", "concat", "extract_audio", "add_audio", "resize", "framerate", "filter",
                "thumbnail", "gif", "overlay", "speed", "normalize_audio", "subtitles", "ops", "help"] = "help"
    source: str = Field("", max_length=100)
    sources: list[str] = Field(default_factory=list, max_length=20)
    audio: str = Field("", max_length=100)
    overlay: str = Field("", max_length=100)
    subtitles: str = Field("", max_length=100)
    format: Literal["mp4", "mov", "mkv", "webm", "avi", "gif", "mp3", "wav", "flac", "ogg", "opus", "m4a", "png",
                    "jpg"] | None = None
    start: float = Field(0.0, ge=0, le=86400, allow_inf_nan=False)
    end: float | None = Field(None, gt=0, le=86400, allow_inf_nan=False)
    accurate: bool = True
    tolerance: float | None = Field(None, gt=0, le=60, allow_inf_nan=False)
    width: int | None = Field(None, ge=2, le=7680)
    height: int | None = Field(None, ge=2, le=4320)
    fps: float | None = Field(None, gt=0, le=240, allow_inf_nan=False)
    factor: float = Field(1.0, ge=0.25, le=4.0, allow_inf_nan=False)
    time: float | None = Field(None, ge=0, le=86400, allow_inf_nan=False)
    x: int = Field(0, ge=-7680, le=7680)
    y: int = Field(0, ge=-4320, le=4320)
    filter_expr: str = Field("", max_length=1000)
    target_lufs: float = Field(-16.0, ge=-70, le=-5, allow_inf_nan=False)
    true_peak: float = Field(-1.5, ge=-9, le=0, allow_inf_nan=False)
    lra: float = Field(11.0, ge=1, le=50, allow_inf_nan=False)
    video_codec: str = Field("", max_length=64)
    audio_codec: str = Field("", max_length=64)
    crf: int | None = Field(None, ge=0, le=63)
    audio_bitrate: str = Field("", pattern=r"^(\d{1,4}k)?$")
    name: str = Field("", max_length=90)
    overwrite: bool = False
    timeout: float = Field(_programs.DEFAULT_SECONDS, ge=1, le=_programs.MAX_SECONDS, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator(*_NAMED, "name")
    @classmethod
    def _plain_names(cls, value: str) -> str:
        return _plain(value)

    @field_validator("sources")
    @classmethod
    def _plain_sources(cls, value: list[str]) -> list[str]:
        return [_plain(v) for v in value]

    @field_validator("video_codec", "audio_codec")
    @classmethod
    def _plain_values(cls, value: str) -> str:
        if value and not VALUE.fullmatch(value):
            raise ValueError(f"{value!r} is not a plain codec name")
        return value

    @property
    def source_format(self) -> str:
        found = Path(self.source).suffix.lower().lstrip(".")
        return {"jpeg": "jpg", "m4v": "mp4"}.get(found, found)

    def codec(self) -> dict[str, Any]:
        return {"video_codec": self.video_codec, "audio_codec": self.audio_codec, "crf": self.crf,
                "audio_bitrate": self.audio_bitrate}

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> FFmpegInput:
        if self.op in ("ops", "help"):
            return self
        if self.op == "concat":
            if len(self.sources) < 2:
                raise ValueError("concat needs sources: two to twenty file names in the media folder")
        elif not self.source:
            raise ValueError(f"{self.op} needs source: a file name in the media folder")
        for op, field in (("add_audio", "audio"), ("overlay", "overlay"), ("subtitles", "subtitles"),
                          ("filter", "filter_expr")):
            if self.op == op and not getattr(self, field):
                raise ValueError(f"{op} needs {field}")
        if self.op == "trim" and (self.end is None or self.end <= self.start):
            raise ValueError("trim needs end, after start")
        if self.op == "resize" and self.width is None and self.height is None:
            raise ValueError("resize needs width, height or both")
        if self.op == "framerate" and self.fps is None:
            raise ValueError("framerate needs fps")
        if self.op == "convert" and self.format is None:
            raise ValueError("convert needs format")
        return self


def _format(d: FFmpegInput) -> str:
    """The output format: the one asked for, else the op's own default; refused when the op cannot write it."""
    first = Path(d.sources[0] if d.op == "concat" else d.source).suffix.lower().lstrip(".")
    first = {"jpeg": "jpg", "m4v": "mp4"}.get(first, first)
    default = {"thumbnail": "png", "gif": "gif", "extract_audio": "wav"}.get(d.op) or (
        first if first in FORMATS and first not in PICTURE_FORMATS + ("gif",) else "mp4")
    fmt = d.format or default
    allowed = {"thumbnail": PICTURE_FORMATS, "gif": ("gif",), "extract_audio": SOUND_FORMATS}.get(d.op) or (
        VIDEO_FORMATS if d.op in ("add_audio", "resize", "framerate", "filter", "overlay", "subtitles")
        else VIDEO_FORMATS + SOUND_FORMATS)
    if fmt not in allowed:
        raise MediaError(INPUT_INVALID, f"{d.op} writes {', '.join(allowed)}, not {fmt}")
    return fmt


def _ids(d: FFmpegInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _source(name: str, field: str) -> Path:
    return media_source(name, field)


def _probed(path: Path) -> dict[str, Any]:
    return streams(probe(path))


def _ffmpeg(where: str, args: list[str], d: FFmpegInput, *, info: bool = False) -> _programs.Ran:
    head = ["-nostdin", "-hide_banner", "-nostats", "-v", "info" if info else "error", "-n"]
    return _programs.run(where, [*head, *args], seconds=d.timeout, cwd=media_root(), name="ffmpeg")


_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def _loudness(where: str, name: str, d: FFmpegInput) -> dict[str, str]:
    """loudnorm's measurement of ``name`` (its integrated loudness, true peak, range, threshold and offset), or
    :class:`MediaError` when ffmpeg measured nothing: no measurement is ever made up."""
    ran = _ffmpeg(where, [*commands.inputs(name), "-map", "0:a:0", "-af", commands.loudness_pass(d), "-f", "null",
                          "-"], d, info=True)
    found = _JSON.findall(ran.err.decode("utf-8", "replace"))
    if ran.code != 0 or not found:
        raise MediaError(BACKEND_FAILED, f"ffmpeg measured no loudness for {name}: {ran.text(300) or f'exit {ran.code}'}")
    try:
        measured = {k: str(v) for k, v in json.loads(found[-1]).items()}
    except ValueError:
        raise MediaError(BACKEND_FAILED, f"ffmpeg's loudness measurement of {name} is not JSON") from None
    if measured.get("input_i", "").lstrip("-") in ("inf", "") or measured.get("input_tp", "").lstrip("-") == "inf":
        raise MediaError(SILENT, f"{name} is silent: there is no loudness to measure or set")
    return measured


def _loudness_check(where: str, name: str, d: FFmpegInput) -> tuple[Check, dict[str, Any]]:
    """The result measured again: its integrated loudness within 1 LU of the target and its true peak at most 0.5 dB
    over the ceiling asked for."""
    got = _loudness(where, name, d)
    level, peak = float(got["input_i"]), float(got["input_tp"])
    ok = abs(level - d.target_lufs) <= 1.0 and peak <= d.true_peak + 0.5
    detail = (f"{level:.1f} LUFS and a true peak of {peak:.1f} dBTP, measured again"
              + ("" if ok else f"; asked for {d.target_lufs:g} LUFS (within 1 LU) and at most {d.true_peak:g} dBTP"))
    return (Check(name="loudness_matches_request", passed=ok, code="" if ok else LOUDNESS_MISSED, detail=detail),
            {"integrated_lufs": level, "true_peak_dbtp": peak, "lra": float(got.get("input_lra", "nan"))})


def _probe(d: FFmpegInput, path: Path) -> MediaResult:
    found = inspect_av(path, kind="video", seconds=d.timeout)
    decoded = next((c for c in found.checks if c.name == "decodes"), None)
    if decoded is None or not decoded.passed:
        failure = found.failure
        return refuse(d.op, failure.code if failure else UNDECODABLE, failure.detail if failure else "not decoded",
                      evidence=[found.evidence()], **_ids(d))
    said = {c.name: c.passed for c in found.checks}
    # a check that did not run (no sound, no picture) is not assessed: None, never a pass
    measured = {**found.measured, **{key: None if check not in said else not said[check]
                                     for key, check in (("silent", "not_silent"), ("blank", "not_blank"))}}
    return answer(d.op, f"{path.name} measured: {found.measured.get('format')}, "
                        f"{found.measured.get('duration_s')} s, decoded in full", card=MEASURED,
                  evidence=[found.evidence()], measured=measured, **_ids(d))


def _plan(d: FFmpegInput, where: str, paths: dict[str, Path]) -> commands.Plan:
    found = {field: _probed(path) for field, path in paths.items() if field != "subtitles"}
    if d.op == "concat":
        return commands.concat(d, [found[f"sources[{i}]"] for i in range(len(d.sources))])
    builders = {"convert": commands.convert, "trim": commands.trim, "extract_audio": commands.extract_audio,
                "resize": commands.resize, "framerate": commands.framerate, "filter": commands.video_filter,
                "thumbnail": commands.thumbnail, "gif": commands.gif, "speed": commands.speed,
                "subtitles": commands.subtitles}
    if d.op in builders:
        return builders[d.op](d, found["source"])
    if d.op == "add_audio":
        return commands.add_audio(d, found["source"], found["audio"])
    if d.op == "overlay":
        return commands.overlay(d, found["source"], found["overlay"])
    return commands.normalize_audio(d, found["source"], _loudness(where, paths["source"].name, d))


def _make(d: FFmpegInput) -> MediaResult:
    ids = _ids(d)
    temp: Path | None = None
    try:
        ffmpeg = _programs.find(FFMPEG)
        _programs.find(FFPROBE)
        paths = {field: _source(getattr(d, field), field) for field in _NAMED if getattr(d, field)}
        paths |= {f"sources[{i}]": _source(n, "sources") for i, n in enumerate(d.sources)} if d.op == "concat" else {}
        if d.op == "probe":
            return _probe(d, paths["source"])
        if d.op == "subtitles":
            commands.check_subtitles(paths["subtitles"].read_text(encoding="utf-8-sig", errors="replace")[:2_000_000],
                                     d.subtitles)
        fmt = _format(d)
        d = d.model_copy(update={"format": fmt})
        plan = _plan(d, ffmpeg.where, paths)
        suffix = FORMATS[fmt][0]
        final = media_file(d.name or f"{d.op}-{secrets.token_hex(6)}", suffix)
        temp = media_root() / f".partial-{secrets.token_hex(8)}{suffix}"
        args = [temp.name if a == "{out}" else a for a in plan.args]
        ran = _ffmpeg(ffmpeg.where, args, d)
        record = {"kind": "program_run", "program": "ffmpeg", "version": ffmpeg.version, "exit_code": ran.code,
                  "seconds": ran.seconds, "argv": [FFMPEG.command, *[("{out}" if a == temp.name else a) for a in args]]}
        if ran.code != 0:
            discard(temp)
            return refuse(d.op, BACKEND_FAILED, f"ffmpeg failed (exit {ran.code}): {ran.text(400)}"[:500],
                          evidence=[record], **ids)
        found = inspect_media(temp, kind=plan.kind, seconds=d.timeout, **plan.expect)
        loud: dict[str, Any] = {}
        if found.passed and d.op == "normalize_audio":
            check, loud = _loudness_check(ffmpeg.where, temp.name, d)
            found = found.model_copy(update={"checks": (*found.checks, check),
                                             "measured": {**found.measured, "loudness": loud}})
        if not found.passed:
            discard(temp)
            return refuse(d.op, found.failure.code, found.failure.detail,
                          evidence=[found.evidence(), record, {"kind": "file", "removed": True}], **ids)
        written = place(temp, final, overwrite=d.overwrite)
        temp = None
        sources = {p.name: sha256_file(p) for p in paths.values()}
        recipe = {"op": d.op, "format": fmt, "argv": record["argv"], "sources": sources, **plan.recipe}
        digest = hashlib.sha256(json.dumps(recipe, sort_keys=True, default=str).encode()).hexdigest()
        provenance = Provenance(backend="ffmpeg", backend_version=ffmpeg.version[:200], technique=d.op, recipe=recipe,
                                recipe_sha256=digest)
        requested = {k: v for k, v in plan.expect.items() if k in ("duration", "width", "height", "fps", "video",
                                                                   "audio") and v is not None}
        artifact = media_artifact(final, found, provenance, {**requested, "format": fmt})
        length = found.measured.get("duration_s")
        said = (f"{d.op}: ffmpeg's {fmt} was probed from disk: it decodes"
                + (f", lasts {length:.3f} s" if isinstance(length, (int, float)) else "")
                + " and has the streams and size asked for, and is not silent or blank"
                + (f"; measured again at {loud['integrated_lufs']:.1f} LUFS" if loud else ""))
        return answer(d.op, said, artifact=artifact, card=PROBED, evidence=[record], written=written, **ids)
    except MediaError as exc:
        if temp is not None:
            discard(temp)
        return refuse(d.op, exc.code, exc.reason, **ids)


@block_contract(
    purpose="convert, cut, join, resize, retime, filter, subtitle and level video and sound with ffmpeg, each result "
            "probed from disk against what was asked before it is returned",
    mitigates_failure_modes=("visual_check_failed", "audio_check_failed"),
    verification_method="g6.media sound and video inspector on every file ffmpeg writes: ffprobe's streams, a full "
                        "decode, the length, frame size and rate, sample rate and channels asked for, not silent, not "
                        "blank; normalize_audio's loudness measured again",
)
@component_maturity(
    "beta",
    reason="ffmpeg over its command line through g6.media's runner (argument lists, confined inputs, a deadline, a "
           "clean environment); every output probed and a failed one refused; qualified-draft at best",
)
class AdaptFFmpegBlock(AIBlock[FFmpegInput, dict, dict]):
    """Run one ffmpeg op and say what was measured about its result. Never ``verified``."""

    def __init__(self, name: str = "adapt_ffmpeg", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: FFmpegInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": [v + _SAYS for v in _OPS.values()]})
        result = _make(data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "AdaptFFmpegBlock", "FFmpegInput"]
