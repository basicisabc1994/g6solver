# SPDX-License-Identifier: Apache-2.0
"""What each adapt_ffmpeg op asks ffmpeg to do, and what the file it writes must then measure.

Pure functions, no program run: each takes the op's input and ffprobe's account of its sources and returns a
:class:`Plan`, the ffmpeg arguments (every input preceded by :data:`g6.media._av.CONFINE`, every name a bare file name
in the media folder) and the expectations the inspector checks the result against, computed from the request and the
sources, never read from the result. The validators are here too: the video filter allow-list, the plain names an
option's value may be, and the subtitle file's own text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from g6.media._av import CONFINE
from g6.media._types import INPUT_INVALID, MediaError

#: Each output format: its suffix, the ffprobe format names that may read it back, and whether it holds video and
#: sound.
FORMATS: dict[str, tuple[str, tuple[str, ...], bool, bool]] = {
    "mp4": (".mp4", ("mp4",), True, True), "mov": (".mov", ("mov",), True, True),
    "mkv": (".mkv", ("matroska",), True, True), "webm": (".webm", ("webm",), True, True),
    "avi": (".avi", ("avi",), True, True), "gif": (".gif", ("gif",), True, False),
    "mp3": (".mp3", ("mp3",), False, True), "wav": (".wav", ("wav",), False, True),
    "flac": (".flac", ("flac",), False, True), "ogg": (".ogg", ("ogg",), False, True),
    "opus": (".opus", ("ogg",), False, True), "m4a": (".m4a", ("mp4", "m4a"), False, True),
    "png": (".png", ("png_pipe", "image2"), True, False), "jpg": (".jpg", ("jpeg_pipe", "image2"), True, False),
}
VIDEO_FORMATS = ("mp4", "mov", "mkv", "webm", "avi")
SOUND_FORMATS = ("mp3", "wav", "flac", "ogg", "opus", "m4a")
PICTURE_FORMATS = ("png", "jpg")
#: The video filters ``filter`` may chain: none opens a file, a device or an address, or runs a command (each one's
#: options checked against ffmpeg's filter documentation). ``curves`` is not among them: its ``plot`` option writes a
#: file and its ``psfile`` option, also its seventh positional one, reads one.
SAFE_FILTERS = frozenset({
    "bilateral", "boxblur", "chromakey", "colorbalance", "colorchannelmixer", "colorcontrast", "colorcorrect",
    "colorhold", "colorize", "colorkey", "colorlevels", "colorspace", "crop", "eq", "fade", "fps",
    "framestep", "gblur", "gradfun", "grayscale", "hflip", "hue", "lut", "lutrgb", "negate", "noise", "pad", "rotate",
    "scale", "setsar", "thumbnail", "transpose", "unsharp", "vflip",
})
_UNSAFE = ("file", "movie", "sendcmd", "zmq", "url", "http", "device")
_TOKENS = re.compile(r"^[A-Za-z0-9_.,:=+\-*()[\] ]+$")
_FILTER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: A plain value for an option (a codec, a bit rate): it cannot be read as an option, a path or an address.
VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_SUBTITLE_HEAD = re.compile(r"\A\ufeff?\s*(?:\d+\s*\r?\n\s*-?\d+:\d+:\d+[,.]\d+ --> |\[Script Info\]|WEBVTT)")
_PLAYLIST = ("#extm3u", "<mpd", "<compositionplaylist", "ffconcat", "<?xml")


@dataclass
class Plan:
    """The ffmpeg arguments for one op (the output name goes last, as ``{out}``), what the result must measure, the
    kind of file it is, and the recipe kept in its provenance."""

    args: list[str]
    kind: str
    expect: dict[str, Any]
    recipe: dict[str, Any] = field(default_factory=dict)


def inputs(*names: str, before: tuple[str, ...] = ()) -> list[str]:
    """``-i name`` for each source, each preceded by the confinement (and by ``before``, input options such as a
    seek)."""
    out: list[str] = []
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9.][A-Za-z0-9._-]{0,99}", name) or ".." in name:
            raise MediaError(INPUT_INVALID, f"{name!r} is not a plain file name in the media folder")
        out += [*before, *CONFINE, "-i", name]
    return out


def check_filter(expr: str) -> str:
    """A chain of :data:`SAFE_FILTERS` with plain arguments, or :class:`MediaError` (``G6_E_MEDIA_INPUT_INVALID``)."""
    text = expr.strip()
    lowered = text.lower()
    if not text:
        raise MediaError(INPUT_INVALID, "filter needs filter_expr")
    hit = next((word for word in _UNSAFE if word in lowered), None)
    if hit:
        raise MediaError(INPUT_INVALID, f"filter_expr names {hit!r}, which no allowed filter needs")
    if not _TOKENS.fullmatch(text):
        raise MediaError(INPUT_INVALID, "filter_expr may hold only letters, digits, spaces and . , : = + - * ( ) [ ]")
    for segment in text.split(","):
        name = segment.strip().split("=", 1)[0].split(":", 1)[0].strip()
        if not segment.strip() or not _FILTER_NAME.fullmatch(name):
            raise MediaError(INPUT_INVALID, f"filter_expr has an empty or malformed filter: {segment.strip()!r}")
        if name not in SAFE_FILTERS:
            raise MediaError(INPUT_INVALID, f"filter {name!r} is not one filter may use (allowed: "
                                            f"{', '.join(sorted(SAFE_FILTERS))})")
    return text


def check_subtitles(text: str, name: str) -> None:
    """The subtitle file must be SRT, ASS or WebVTT text with no playlist or manifest in it: ffmpeg's subtitles filter
    opens it itself, outside the input confinement."""
    if not _SUBTITLE_HEAD.match(text) or any(marker in text.lower() for marker in _PLAYLIST):
        raise MediaError(INPUT_INVALID, f"{name} is not SRT, ASS or WebVTT subtitle text")


def even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def video_of(found: dict[str, Any]) -> dict[str, Any]:
    return found.get("video") or {}


def _need(found: dict[str, Any], what: str, name: str) -> None:
    if not found.get(f"{what}_streams"):
        raise MediaError(INPUT_INVALID, f"{name} has no {what} stream")


def _length(found: dict[str, Any], name: str) -> float:
    length = found.get("duration_s")
    if not length:
        raise MediaError(INPUT_INVALID, f"ffprobe found no length for {name}")
    return float(length)


def _encode(fmt: str, *, video: bool, codec: dict[str, Any]) -> list[str]:
    """The encoder options for an output in ``fmt``: the caller's codecs (plain names), quality, and the pixel
    format every player reads for an MP4 or a QuickTime file."""
    out: list[str] = []
    if codec.get("video_codec"):
        out += ["-c:v", codec["video_codec"]]
    if codec.get("crf") is not None:
        out += ["-crf", str(codec["crf"])]
    if codec.get("audio_codec"):
        out += ["-c:a", codec["audio_codec"]]
    if codec.get("audio_bitrate"):
        out += ["-b:a", codec["audio_bitrate"]]
    if fmt == "wav":
        out += ["-c:a", "pcm_s16le"]
    if video and fmt in ("mp4", "mov"):
        out += ["-pix_fmt", "yuv420p"]
    return out


def _same_streams(fmt: str, found: dict[str, Any], name: str) -> dict[str, Any]:
    """What a result in ``fmt`` keeps of a source: its video when ``fmt`` holds video, its sound when it holds sound."""
    _, _, holds_video, holds_sound = FORMATS[fmt]
    video, sound = holds_video and bool(found["video_streams"]), holds_sound and bool(found["audio_streams"])
    if not (video or sound):
        raise MediaError(INPUT_INVALID, f"a {fmt} file would keep nothing of {name}")
    expect: dict[str, Any] = {"video": video, "audio": sound, "formats": FORMATS[fmt][1]}
    if video:
        shown = video_of(found)
        expect.update(width=shown.get("width"), height=shown.get("height"))
    return expect


def convert(d: Any, found: dict[str, Any]) -> Plan:
    expect = _same_streams(d.format, found, d.source)
    maps = (["-map", "0:v:0"] if expect["video"] else ["-vn"]) + (["-map", "0:a:0"] if expect["audio"] else ["-an"])
    args = [*inputs(d.source), *maps, *_encode(d.format, video=expect["video"], codec=d.codec()), "{out}"]
    return Plan(args, "video" if expect["video"] else "audio",
                {**expect, "duration": _length(found, d.source), "tolerance": d.tolerance or 0.15})


def trim(d: Any, found: dict[str, Any]) -> Plan:
    total = _length(found, d.source)
    if d.start >= total:
        raise MediaError(INPUT_INVALID, f"start {d.start:g} s is past the end of {d.source} ({total:.3f} s)")
    length = round(min(d.end, total) - d.start, 6)
    expect = _same_streams(d.format, found, d.source)
    if d.accurate:
        codec = _encode(d.format, video=expect["video"], codec=d.codec())
    else:
        if d.format != d.source_format:
            raise MediaError(INPUT_INVALID, "a stream copy keeps the source's format; give the same format, or "
                                            "accurate=true to re-encode")
        codec = ["-c", "copy"]
    args = [*inputs(d.source, before=("-ss", f"{d.start:.6f}")), "-t", f"{length:.6f}",
            *(["-map", "0:v:0"] if expect["video"] else ["-vn"]), *(["-map", "0:a:0"] if expect["audio"] else ["-an"]),
            *codec, "{out}"]
    return Plan(args, "video" if expect["video"] else "audio",
                {**expect, "duration": length, "tolerance": d.tolerance or 0.1},
                {"start": d.start, "end": d.end, "accurate": d.accurate})


def concat(d: Any, found: list[dict[str, Any]]) -> Plan:
    names = d.sources
    video = all(f["video_streams"] for f in found)
    sound = all(f["audio_streams"] for f in found)
    if not (video or sound) or (any(f["video_streams"] for f in found) and not video):
        raise MediaError(INPUT_INVALID, "concat needs sources that all have video, or all have sound")
    _, _, holds_video, holds_sound = FORMATS[d.format]
    video, sound = video and holds_video, sound and holds_sound
    if not (video or sound):
        raise MediaError(INPUT_INVALID, f"a {d.format} file would keep nothing of these sources")
    graph, labels = [], []
    first = video_of(found[0])
    for i, _ in enumerate(found):
        if video:
            graph.append(f"[{i}:v:0]scale={first.get('width')}:{first.get('height')},setsar=1,"
                         f"fps={first.get('fps') or 25}[v{i}]")
            labels.append(f"[v{i}]")
        if sound:
            graph.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
            labels.append(f"[a{i}]")
    graph.append("".join(labels) + f"concat=n={len(found)}:v={int(video)}:a={int(sound)}"
                 + ("[v]" if video else "") + ("[a]" if sound else ""))
    maps = (["-map", "[v]"] if video else []) + (["-map", "[a]"] if sound else [])
    total = sum(_length(f, n) for f, n in zip(found, names))
    expect: dict[str, Any] = {"video": video, "audio": sound, "formats": FORMATS[d.format][1], "duration": total,
                              "tolerance": d.tolerance or 0.1 + 0.05 * len(found)}
    if video:
        expect.update(width=first.get("width"), height=first.get("height"))
    args = [*inputs(*names), "-filter_complex", ";".join(graph), *maps,
            *_encode(d.format, video=video, codec=d.codec()), "{out}"]
    return Plan(args, "video" if video else "audio", expect, {"sources": list(names)})


def extract_audio(d: Any, found: dict[str, Any]) -> Plan:
    _need(found, "audio", d.source)
    length = (found.get("audio") or {}).get("duration_s") or _length(found, d.source)
    args = [*inputs(d.source), "-map", "0:a:0", "-vn", *_encode(d.format, video=False, codec=d.codec()), "{out}"]
    return Plan(args, "audio", {"video": False, "audio": True, "formats": FORMATS[d.format][1], "duration": length,
                                "tolerance": d.tolerance or 0.15})


def add_audio(d: Any, found: dict[str, Any], sound: dict[str, Any]) -> Plan:
    _need(found, "video", d.source)
    _need(sound, "audio", d.audio)
    length = min(_length(found, d.source), _length(sound, d.audio))
    shown = video_of(found)
    args = [*inputs(d.source, d.audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            *_encode(d.format, video=False, codec={k: v for k, v in d.codec().items() if k.startswith("audio")}),
            "-shortest", "{out}"]
    return Plan(args, "video", {"video": True, "audio": True, "formats": FORMATS[d.format][1], "duration": length,
                                "tolerance": d.tolerance or 0.25, "width": shown.get("width"),
                                "height": shown.get("height")}, {"audio": d.audio})


def _video_plan(d: Any, found: dict[str, Any], vf: str, *, width: int | None, height: int | None,
                fps: float | None = None, duration: float | None = None, recipe: dict | None = None) -> Plan:
    _need(found, "video", d.source)
    sound = bool(found["audio_streams"]) and FORMATS[d.format][3]
    args = [*inputs(d.source), "-map", "0:v:0", *(["-map", "0:a:0", "-c:a", "copy"] if sound else ["-an"]),
            "-vf", vf, *_encode(d.format, video=True, codec={k: v for k, v in d.codec().items()
                                                              if not k.startswith("audio")}), "{out}"]
    expect: dict[str, Any] = {"video": True, "audio": sound, "formats": FORMATS[d.format][1],
                              "duration": duration if duration is not None else _length(found, d.source),
                              "tolerance": d.tolerance or 0.15}
    if width is not None:
        expect.update(width=width, height=height)
    if fps is not None:
        expect["fps"] = fps
    return Plan(args, "video", expect, recipe or {})


def resize(d: Any, found: dict[str, Any]) -> Plan:
    shown = video_of(found)
    w0, h0 = shown.get("width"), shown.get("height")
    if not (w0 and h0):
        raise MediaError(INPUT_INVALID, f"ffprobe found no frame size for {d.source}")
    width = d.width if d.width is not None else even(d.height * w0 / h0)
    height = d.height if d.height is not None else even(d.width * h0 / w0)
    if d.format in VIDEO_FORMATS and (width % 2 or height % 2):
        raise MediaError(INPUT_INVALID, f"{width}x{height}: a {d.format} video needs an even width and height")
    return _video_plan(d, found, f"scale={width}:{height}:flags=lanczos,setsar=1", width=width, height=height,
                       recipe={"width": width, "height": height})


def framerate(d: Any, found: dict[str, Any]) -> Plan:
    shown = video_of(found)
    return _video_plan(d, found, f"fps={d.fps:g}", width=shown.get("width"), height=shown.get("height"), fps=d.fps,
                       recipe={"fps": d.fps})


def video_filter(d: Any, found: dict[str, Any]) -> Plan:
    return _video_plan(d, found, check_filter(d.filter_expr), width=None, height=None,
                       recipe={"filter_expr": d.filter_expr})


def speed(d: Any, found: dict[str, Any]) -> Plan:
    length = _length(found, d.source) / d.factor
    video, sound = bool(found["video_streams"]) and FORMATS[d.format][2], bool(found["audio_streams"]) and FORMATS[
        d.format][3]
    if not (video or sound):
        raise MediaError(INPUT_INVALID, f"a {d.format} file would keep nothing of {d.source}")
    tempo, left = [], d.factor
    while left > 2.0 or left < 0.5:
        step = 2.0 if left > 2.0 else 0.5
        tempo.append(f"atempo={step:g}")
        left /= step
    tempo.append(f"atempo={left:.6f}")
    parts = ([f"[0:v:0]setpts=PTS/{d.factor:.6f}[v]"] if video else []) + (
        [f"[0:a:0]{','.join(tempo)}[a]"] if sound else [])
    maps = (["-map", "[v]"] if video else []) + (["-map", "[a]"] if sound else [])
    expect: dict[str, Any] = {"video": video, "audio": sound, "formats": FORMATS[d.format][1], "duration": length,
                              "tolerance": d.tolerance or 0.15}
    if video:
        expect.update(width=video_of(found).get("width"), height=video_of(found).get("height"))
    args = [*inputs(d.source), "-filter_complex", ";".join(parts), *maps,
            *_encode(d.format, video=video, codec=d.codec()), "{out}"]
    return Plan(args, "video" if video else "audio", expect, {"factor": d.factor})


def thumbnail(d: Any, found: dict[str, Any]) -> Plan:
    _need(found, "video", d.source)
    shown = video_of(found)
    at = d.time if d.time is not None else round(_length(found, d.source) / 2, 3)
    w0, h0 = shown.get("width"), shown.get("height")
    if not (w0 and h0):
        raise MediaError(INPUT_INVALID, f"ffprobe found no frame size for {d.source}")
    width = d.width or (even(d.height * w0 / h0) if d.height else w0)
    height = d.height or (even(d.width * h0 / w0) if d.width else h0)
    vf = ["-vf", f"scale={width}:{height}:flags=lanczos"] if d.width or d.height else []
    args = [*inputs(d.source, before=("-ss", f"{at:.6f}")), "-map", "0:v:0", "-frames:v", "1", *vf, "{out}"]
    return Plan(args, "image", {"video": True, "audio": False, "formats": FORMATS[d.format][1], "width": width,
                                "height": height}, {"time": at})


def gif(d: Any, found: dict[str, Any]) -> Plan:
    _need(found, "video", d.source)
    shown = video_of(found)
    width = d.width or min(480, shown.get("width") or 480)
    height = even(width * shown["height"] / shown["width"]) if shown.get("width") else d.height
    fps = d.fps or 10.0
    total = _length(found, d.source)
    start = d.start or 0.0
    length = min(total - start, d.end - start) if d.end else total - start
    if length <= 0:
        raise MediaError(INPUT_INVALID, f"start {start:g} s is past the end of {d.source}")
    graph = (f"fps={fps:g},scale={width}:{height}:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse")
    args = [*inputs(d.source, before=("-ss", f"{start:.6f}")), "-t", f"{length:.6f}", "-map", "0:v:0", "-an", "-vf",
            graph, "-loop", "0", "{out}"]
    expect: dict[str, Any] = {"video": True, "audio": False, "formats": ("gif",), "width": width, "height": height,
                              "duration": length, "tolerance": d.tolerance or max(0.2, 2 / fps)}
    if (100 / fps).is_integer():
        expect["fps"] = fps
    return Plan(args, "animation", expect, {"fps": fps, "start": start})


def overlay(d: Any, found: dict[str, Any], laid: dict[str, Any]) -> Plan:
    _need(found, "video", d.source)
    _need(laid, "video", d.overlay)
    shown = video_of(found)
    sound = bool(found["audio_streams"]) and FORMATS[d.format][3]
    args = [*inputs(d.source, d.overlay), "-filter_complex", f"[0:v:0][1:v:0]overlay=x={d.x}:y={d.y}[v]",
            "-map", "[v]", *(["-map", "0:a:0", "-c:a", "copy"] if sound else ["-an"]),
            *_encode(d.format, video=True, codec={k: v for k, v in d.codec().items() if not k.startswith("audio")}),
            "{out}"]
    return Plan(args, "video", {"video": True, "audio": sound, "formats": FORMATS[d.format][1],
                                "width": shown.get("width"), "height": shown.get("height"),
                                "duration": _length(found, d.source), "tolerance": d.tolerance or 0.15},
                {"overlay": d.overlay, "x": d.x, "y": d.y})


def subtitles(d: Any, found: dict[str, Any]) -> Plan:
    shown = video_of(found)
    return _video_plan(d, found, f"subtitles=filename={d.subtitles}", width=shown.get("width"),
                       height=shown.get("height"), recipe={"subtitles": d.subtitles})


def loudness_pass(d: Any, measured: dict[str, str] | None = None) -> str:
    """The loudnorm filter for a measuring pass (``measured`` is ``None``) or for the linear pass that applies it."""
    base = f"loudnorm=I={d.target_lufs:g}:TP={d.true_peak:g}:LRA={d.lra:g}"
    if measured is None:
        return base + ":print_format=json"
    return (base + f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true:print_format=json")


def normalize_audio(d: Any, found: dict[str, Any], measured: dict[str, str]) -> Plan:
    _need(found, "audio", d.source)
    video = bool(found["video_streams"]) and FORMATS[d.format][2]
    rate = (found.get("audio") or {}).get("rate") or 48000
    args = [*inputs(d.source), *(["-map", "0:v:0", "-c:v", "copy"] if video else ["-vn"]), "-map", "0:a:0",
            "-af", loudness_pass(d, measured), "-ar", str(rate),
            *_encode(d.format, video=False, codec={k: v for k, v in d.codec().items() if k.startswith("audio")}),
            "{out}"]
    expect: dict[str, Any] = {"video": video, "audio": True, "formats": FORMATS[d.format][1],
                              "duration": _length(found, d.source), "tolerance": d.tolerance or 0.15}
    return Plan(args, "video" if video else "audio", expect,
                {"target_lufs": d.target_lufs, "true_peak": d.true_peak, "lra": d.lra, "measured": measured})


__all__ = ["FORMATS", "PICTURE_FORMATS", "SAFE_FILTERS", "SOUND_FORMATS", "VALUE", "VIDEO_FORMATS", "Plan",
           "check_filter", "check_subtitles", "inputs", "loudness_pass"]
