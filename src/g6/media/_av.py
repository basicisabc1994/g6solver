# SPDX-License-Identifier: Apache-2.0
"""Sound and moving pictures through ffmpeg and ffprobe: the two programs declared once for the family, the
confinement every input is opened with, and the inspector behind every sound or video file the family writes.
``_``-prefixed, so it never registers as a component.

* :data:`FFMPEG` and :data:`FFPROBE` are outside programs the person installs (``G6_FFMPEG`` and ``G6_FFPROBE`` may
  name them); G6 runs them over their command line only (their builds may be GPL) and never bundles them.
* :data:`CONFINE` goes before every input: ``-protocol_whitelist file`` and ``-format_whitelist`` :data:`DEMUXERS`,
  plain media, picture and subtitle formats that open no other file or address. ffmpeg picks a demuxer by probing the
  bytes, so without it a playlist or manifest named ``clip.wav`` would be read as one and could name other files or
  addresses from inside itself. Every program runs in the media folder on bare file names (no path, no colon, never a
  leading ``-``), so nothing a caller gives can be read as an option, a protocol or a place.
* :func:`inspect_av` measures a file from disk: ffprobe reads its format and streams; it is the format asked for
  (``G6_E_MEDIA_FORMAT_MISMATCH``); it has the streams asked for, with the frame size, frame rate, sample rate and
  channels asked for (``_STREAM_MISMATCH``, ``_SIZE_MISMATCH``); ffmpeg decodes every frame of it without an error
  (``_UNDECODABLE``); it lasts as long as asked within the tolerance (``_DURATION_MISMATCH``); its sound, decoded to
  WAV, is not silent by the WAV inspector's own rule (``_SILENT``); and its pictures are not blank (``_BLANK``): of up
  to :data:`SAMPLED_FRAMES` frames taken across it, scaled to 160 x 90 in grey, at least one has
  :data:`MIN_CONTENT_FRACTION` of its pixels more than :data:`GREY_TOLERANCE` from its most common grey. Once it
  decodes, its length, its sound and its pictures are each measured whatever the others found, so a silent black clip
  is found both silent and blank.
* :func:`inspect_media` sends a WAV to :func:`g6.media._wav.inspect_wav` (no program needed) and anything else here.

What none of it can judge: whether the file sounds or looks as wanted. It measures; it does not listen or watch.
"""
from __future__ import annotations

import json
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from g6.core import outside
from g6.media import _programs, _wav
from g6.media._types import (
    BLANK,
    FORMAT_MISMATCH,
    NO_OUTPUT,
    SIZE_MISMATCH,
    STREAM_MISMATCH,
    UNDECODABLE,
    Check,
    Inspection,
    MediaError,
    MediaKind,
)

FFMPEG = outside.OutsideProgram(
    name="ffmpeg", purpose="the media family converts, cuts, joins and measures sound and video with it",
    install="Install ffmpeg from ffmpeg.org (winget install Gyan.FFmpeg, brew install ffmpeg or apt install ffmpeg) "
            "and put it on PATH, or set G6_FFMPEG to the program",
    licence="LGPL-2.1-or-later, or GPL-2.0-or-later for builds with GPL parts (G6 runs it over its command line and "
            "never bundles it)", command="ffmpeg", env="G6_FFMPEG", version_args=("-version",))
FFPROBE = outside.OutsideProgram(
    name="ffprobe", purpose="the media family reads the format and streams of sound and video files with it",
    install="ffprobe comes with ffmpeg (ffmpeg.org); put it on PATH, or set G6_FFPROBE to the program",
    licence="LGPL-2.1-or-later, or GPL-2.0-or-later for builds with GPL parts (G6 runs it over its command line and "
            "never bundles it)", command="ffprobe", env="G6_FFPROBE", version_args=("-version",))

#: The demuxers an input may be read with: plain media, picture and subtitle formats that open no other file or
#: address (not hls, dash, concat, sdp, lavfi or any other playlist, manifest or generator).
DEMUXERS: tuple[str, ...] = (
    "wav", "w64", "aiff", "caf", "au", "mp3", "flac", "ogg", "aac", "ac3", "eac3", "amr", "wv",
    "mov", "matroska", "avi", "mpegts", "flv", "mpeg", "m4v", "h264", "hevc", "mjpeg",
    "gif", "apng", "image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe", "gif_pipe",
    "srt", "ass", "webvtt",
)
CONFINE: tuple[str, ...] = ("-protocol_whitelist", "file", "-format_whitelist", ",".join(DEMUXERS))
#: How many frames the blank check takes across a video, the grey a pixel must differ by, and the share that must.
SAMPLED_FRAMES = 12
GREY_TOLERANCE = 8
MIN_CONTENT_FRACTION = 0.001
_FRAME = (160, 90)
INSPECTOR = "g6.media sound and video inspector (ffprobe and ffmpeg)"
#: How long one inspection's programs may take, together.
INSPECT_SECONDS = 120.0


def _rate(text: Any) -> float | None:
    try:
        value = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return float(value) if value > 0 else None


def _number(value: Any) -> float | None:
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return found if found >= 0 else None


def probe(path: Path, *, seconds: float = 60.0) -> dict[str, Any]:
    """ffprobe's account of ``path`` (``format`` and ``streams``), read with the confinement; :class:`MediaError`
    (``G6_E_MEDIA_UNDECODABLE``) when ffprobe cannot read it."""
    ran = _programs.run(_programs.find(FFPROBE).where, ["-v", "error", *CONFINE, "-print_format", "json", "-show_format",
                                                  "-show_streams", path.name], seconds=seconds, cwd=path.parent,
                        name="ffprobe")
    if ran.code != 0:
        raise MediaError(UNDECODABLE, f"ffprobe could not read {path.name}: {ran.text(400) or f'exit {ran.code}'}")
    try:
        found = json.loads(ran.out.decode("utf-8", "replace"))
    except ValueError:
        raise MediaError(UNDECODABLE, f"ffprobe's account of {path.name} is not JSON") from None
    if not isinstance(found, dict) or not isinstance(found.get("format"), dict):
        raise MediaError(UNDECODABLE, f"ffprobe found no format in {path.name}")
    return found


def streams(found: dict[str, Any]) -> dict[str, Any]:
    """What ffprobe found, measured: the format names, the length, and the first video and audio streams (a cover
    picture attached to a sound file is not a video)."""
    fmt = found["format"]
    listed = [s for s in found.get("streams") or [] if isinstance(s, dict)]
    video = [s for s in listed if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in listed if s.get("codec_type") == "audio"]
    durations = [d for d in (_number(fmt.get("duration")), *(_number(s.get("duration")) for s in video + audio))
                 if d is not None]
    out: dict[str, Any] = {"format": str(fmt.get("format_name") or ""), "duration_s": round(max(durations), 6)
                           if durations else None, "video_streams": len(video), "audio_streams": len(audio)}
    if video:
        v = video[0]
        out["video"] = {"codec": str(v.get("codec_name") or ""), "width": v.get("width"), "height": v.get("height"),
                        "fps": round(r, 4) if (r := _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")))
                        else None, "frames": int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else None}
    if audio:
        a = audio[0]
        out["audio"] = {"codec": str(a.get("codec_name") or ""), "rate": int(a["sample_rate"])
                        if str(a.get("sample_rate", "")).isdigit() else None, "channels": a.get("channels")}
    return out


def _decodes(ffmpeg: str, path: Path, seconds: float) -> Check:
    ran = _programs.run(ffmpeg, ["-nostdin", "-hide_banner", "-v", "error", "-xerror", *CONFINE, "-i", path.name,
                                 "-map", "0:v?", "-map", "0:a?", "-f", "null", "-"], seconds=seconds, cwd=path.parent,
                        name="ffmpeg")
    said = ran.text(400)
    if ran.code != 0 or said:
        return Check(name="decodes", passed=False, code=UNDECODABLE,
                     detail=f"ffmpeg could not decode every frame: {said or f'exit {ran.code}'}"[:500])
    return Check(name="decodes", passed=True, detail="ffmpeg decoded every frame without an error")


def _sound(ffmpeg: str, path: Path, duration: float | None, seconds: float) -> tuple[Check, dict[str, Any]]:
    """The first sound stream decoded to WAV in a scratch folder and measured by the WAV inspector's rule (a stream
    longer than half an hour is decoded at 16 kHz mono for this)."""
    long = duration is not None and duration > 1800
    with tempfile.TemporaryDirectory(prefix="g6-media-sound-", ignore_cleanup_errors=True) as scratch:
        wav = Path(scratch) / "sound.wav"
        ran = _programs.run(ffmpeg, ["-nostdin", "-hide_banner", "-v", "error", "-n", *CONFINE, "-i", path.name,
                                     "-map", "0:a:0", *(["-ac", "1", "-ar", "16000"] if long else []),
                                     "-c:a", "pcm_s16le", str(wav)], seconds=seconds, cwd=path.parent, name="ffmpeg")
        if ran.code != 0 or not wav.is_file():
            return Check(name="not_silent", passed=False, code=UNDECODABLE,
                         detail=f"its sound could not be decoded: {ran.text(300) or f'exit {ran.code}'}"[:500]), {}
        try:
            found = _wav.levels(wav)
        except MediaError as exc:
            return Check(name="not_silent", passed=False, code=exc.code, detail=exc.reason[:500]), {}
    return _wav.silence_check(found), found.measured()


def _frames(ffmpeg: str, path: Path, fps: float | None, duration: float | None,
            seconds: float) -> tuple[Check, dict[str, Any]]:
    total = (fps or 1.0) * (duration or 0.0)
    step = max(1, int(total // SAMPLED_FRAMES)) if total > SAMPLED_FRAMES else 1
    width, height = _FRAME
    ran = _programs.run(ffmpeg, ["-nostdin", "-hide_banner", "-v", "error", *CONFINE, "-i", path.name, "-map", "0:v:0",
                                 "-vf", f"framestep={step},scale={width}:{height},format=gray", "-frames:v",
                                 str(SAMPLED_FRAMES), "-f", "rawvideo", "pipe:1"], seconds=seconds, cwd=path.parent,
                        name="ffmpeg")
    size = width * height
    pictures = [ran.out[i:i + size] for i in range(0, len(ran.out) - size + 1, size)]
    if ran.code != 0 or not pictures:
        return Check(name="not_blank", passed=False, code=UNDECODABLE,
                     detail=f"no frame could be taken from it: {ran.text(300) or f'exit {ran.code}'}"[:500]), {}
    shares = []
    for picture in pictures:
        background = max(range(256), key=picture.count)
        table = bytes(1 if abs(v - background) > GREY_TOLERANCE else 0 for v in range(256))
        shares.append(sum(picture.translate(table)) / size)
    best = max(shares)
    measured = {"frames_sampled": len(pictures), "content_fraction": round(best, 6)}
    if best < MIN_CONTENT_FRACTION:
        return Check(name="not_blank", passed=False, code=BLANK,
                     detail=f"every one of the {len(pictures)} frames taken is nearly one grey (at most "
                            f"{best:.4%} of the pixels differ by more than {GREY_TOLERANCE}; the floor is "
                            f"{MIN_CONTENT_FRACTION:.1%})"), measured
    return Check(name="not_blank", passed=True, detail=f"{best:.2%} of the pixels of the busiest of "
                                                       f"{len(pictures)} frames taken differ from its grey"), measured


def _matches(name: str, want: Any, got: Any, code: str, *, close: float = 0.0) -> Check:
    if close and isinstance(want, (int, float)) and isinstance(got, (int, float)):
        ok = abs(got - want) <= close * max(abs(want), 1e-9)
    else:
        ok = got == want
    return Check(name=name, passed=ok, code="" if ok else code,
                 detail=f"{got}" + (", as asked" if ok else f", not the {want} asked for"))


def inspect_av(path: Path | str, *, kind: MediaKind = "video", formats: tuple[str, ...] = (),
               video: bool | None = None, audio: bool | None = None, width: int | None = None,
               height: int | None = None, fps: float | None = None, rate: int | None = None,
               channels: int | None = None, duration: float | None = None, tolerance: float | None = None,
               min_duration: float | None = None, seconds: float = INSPECT_SECONDS) -> Inspection:
    """Measure the file at ``path`` (in the media folder) against what was asked for: ``formats`` (ffprobe's format
    names, any one), whether it has ``video`` and ``audio`` (``None``: not compared), the frame size, ``fps``, sample
    ``rate``, ``channels``, and ``duration`` within ``tolerance`` (default 0.1 s) or at least ``min_duration``. A
    missing ffmpeg or ffprobe, or a program past its time, is :class:`MediaError`: nothing passes uninspected."""
    path = Path(path)
    exists = _wav.exists_check(path)
    if not exists.passed:
        return Inspection(inspector=INSPECTOR, kind=kind, checks=(exists,))
    ffmpeg = _programs.find(FFMPEG).where
    try:
        found = streams(probe(path, seconds=seconds))
    except MediaError as exc:
        if exc.code != UNDECODABLE:
            raise
        return Inspection(inspector=INSPECTOR, kind=kind,
                          checks=(exists, Check(name="decodes", passed=False, code=UNDECODABLE, detail=exc.reason)))
    checks = [exists]
    names = found["format"].split(",")
    if formats:
        ok = any(f in names for f in formats)
        checks.append(Check(name="format_matches_request", passed=ok, code="" if ok else FORMAT_MISMATCH,
                            detail=f"{found['format']}" + (", as asked" if ok else f", not {' or '.join(formats)}")))
    for wanted, has, what in ((video, found["video_streams"], "video"), (audio, found["audio_streams"], "audio")):
        if wanted is not None and bool(has) != wanted:
            checks.append(Check(name=f"{what}_present" if wanted else f"no_{what}", passed=False, code=STREAM_MISMATCH,
                                detail=f"{has} {what} streams, " + (f"but {what} was asked for" if wanted else
                                                                    f"but no {what} was asked for")))
    shown = found.get("video") or {}
    heard = found.get("audio") or {}
    if width is not None or height is not None:
        want = (width if width is not None else shown.get("width"), height if height is not None else shown.get("height"))
        checks.append(_matches("size_matches_request", f"{want[0]}x{want[1]}",
                               f"{shown.get('width')}x{shown.get('height')}", SIZE_MISMATCH))
    if fps is not None:
        checks.append(_matches("fps_matches_request", fps, shown.get("fps"), STREAM_MISMATCH, close=0.01))
    if rate is not None:
        checks.append(_matches("rate_matches_request", rate, heard.get("rate"), STREAM_MISMATCH))
    if channels is not None:
        checks.append(_matches("channels_match_request", channels, heard.get("channels"), STREAM_MISMATCH))
    if not all(c.passed for c in checks):
        return Inspection(inspector=INSPECTOR, kind=kind, checks=tuple(checks), measured=found)
    checks.append(_decodes(ffmpeg, path, seconds))
    decoded, length = checks[-1].passed, found.get("duration_s")
    if decoded and (duration is not None or min_duration is not None) and kind != "image":
        if length is None:
            checks.append(Check(name="duration_matches_request", passed=False, code=NO_OUTPUT,
                                detail="ffprobe found no length"))
        else:
            timed = _wav.duration_check(length, duration, 0.1 if tolerance is None else tolerance, min_duration)
            if timed is not None:
                checks.append(timed)
    if decoded and found["audio_streams"] and audio is not False:
        heard_check, levels = _sound(ffmpeg, path, length, seconds)
        checks.append(heard_check)
        found = {**found, "audio": {**heard, **levels}}
    if decoded and found["video_streams"] and video is not False:
        seen_check, frames = _frames(ffmpeg, path, shown.get("fps"), length, seconds)
        checks.append(seen_check)
        found = {**found, "video": {**shown, **frames}}
    return Inspection(inspector=INSPECTOR, kind=kind, checks=tuple(checks), measured=found, mime=_mime(path, found))


#: The MIME type of each kind of file the family writes, by the format ffprobe found (checked against the request).
_MIMES = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
          ".webm": "video/webm", ".avi": "video/x-msvideo", ".ts": "video/mp2t", ".flv": "video/x-flv",
          ".gif": "image/gif", ".png": "image/png", ".jpg": "image/jpeg", ".mp3": "audio/mpeg", ".flac": "audio/flac",
          ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".aiff": "audio/aiff"}


def _mime(path: Path, found: dict[str, Any]) -> str:
    mime = _MIMES.get(path.suffix.lower(), "application/octet-stream")
    return mime.replace("video/", "audio/") if not found["video_streams"] and mime.startswith("video/") else mime


def inspect_media(path: Path | str, **wanted: Any) -> Inspection:
    """A WAV through the WAV inspector (no program); anything else through :func:`inspect_av`."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        keep = {k: wanted[k] for k in ("rate", "channels", "duration", "tolerance", "min_duration") if k in wanted}
        return _wav.inspect_wav(path, **keep)
    return inspect_av(path, **wanted)


__all__ = ["CONFINE", "DEMUXERS", "FFMPEG", "FFPROBE", "GREY_TOLERANCE", "INSPECTOR", "MIN_CONTENT_FRACTION",
           "SAMPLED_FRAMES", "inspect_av", "inspect_media", "probe", "streams"]
