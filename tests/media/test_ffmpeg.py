# SPDX-License-Identifier: Apache-2.0
"""adapt_ffmpeg: every file ffmpeg writes is probed from disk against what the request and the sources say it must be,
and one that is missing, empty, silent, blank or the wrong length is refused and removed, whatever ffmpeg's exit code
said (D1, ADAPT_FFMPEG-01, -02); every input of every op is confined (-03); nothing is replaced unasked (-04); a
loudness measurement is never made up (-05). Stand-in ffmpeg and ffprobe programs (G6-owned scripts run through the
same child-process path) cover what must hold without the real ffmpeg; the tests that need the real one skip without it.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from media_kit import outside_link, put, stand_in, tone_wav

import g6.media.adapt_ffmpeg as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.media._av import CONFINE, FFMPEG, FFPROBE
from g6.media._types import (
    BACKEND_FAILED,
    BLANK,
    DURATION_MISMATCH,
    EXISTS,
    INPUT_INVALID,
    LOUDNESS_MISSED,
    NO_OUTPUT,
    OUTSIDE_MISSING,
    SILENT,
    TIME_LIMIT,
    MediaError,
    media_root,
)
from g6.media.adapt_ffmpeg import block, commands

SRC = Path(package.__file__).parent
REAL_FFMPEG = shutil.which("ffmpeg")
REAL = pytest.mark.skipif(not (REAL_FFMPEG and shutil.which("ffprobe")), reason="ffmpeg and ffprobe are not installed here")

FFMPEG_STAND_IN = r'''
import json, sys, time, wave
from pathlib import Path
here = Path(__file__).parent
args = sys.argv[1:]
if args == ["-version"]:
    print("ffmpeg version 0.0-stand-in"); sys.exit(0)
mode = json.loads((here / "mode.json").read_text()) if (here / "mode.json").exists() else {}
with open(here / "calls.jsonl", "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
time.sleep(mode.get("sleep", 0))
if mode.get("fail"):
    sys.stderr.write("Invalid data found when processing input\n"); sys.exit(1)
out = args[-1]
inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
seek = float(args[args.index("-ss") + 1]) if "-ss" in args else 0.0
length = float(args[args.index("-t") + 1]) if "-t" in args else None
what = mode.get("write", "good")
if what == "nothing":
    sys.exit(0)
if what == "empty":
    open(out, "wb").close(); sys.exit(0)
with wave.open(inputs[0]) as w:
    rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())
align = channels * width
start = int(round(seek * rate)) * align
data = frames[start:] if length is None else frames[start:start + int(round(length * rate)) * align]
if what == "short":
    data = data[: len(data) * 2 // 5 // align * align]
if what == "silent":
    data = bytes(len(data))
with wave.open(out, "wb") as w:
    w.setnchannels(channels); w.setsampwidth(width); w.setframerate(rate); w.writeframes(data)
'''

FFPROBE_STAND_IN = r'''
import json, sys, wave
args = sys.argv[1:]
if args == ["-version"]:
    print("ffprobe version 0.0-stand-in"); sys.exit(0)
name = args[-1]
try:
    with wave.open(name) as w:
        rate, channels, frames = w.getframerate(), w.getnchannels(), w.getnframes()
except Exception:
    sys.stderr.write(name + ": Invalid data found when processing input\n"); sys.exit(1)
length = f"{frames / rate:.6f}"
print(json.dumps({"format": {"format_name": "wav", "duration": length}, "streams": [{
    "codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": str(rate), "channels": channels,
    "duration": length}]}))
'''


class StandIn:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def behave(self, **mode: object) -> None:
        (self.folder / "mode.json").write_text(json.dumps(mode), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        log = self.folder / "calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


@pytest.fixture
def ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    monkeypatch.setenv("G6_FFMPEG", str(stand_in(tmp_path, "ffmpeg", FFMPEG_STAND_IN)))
    monkeypatch.setenv("G6_FFPROBE", str(stand_in(tmp_path, "ffprobe", FFPROBE_STAND_IN)))
    found = StandIn(tmp_path)
    found.behave()
    return found


def _invoke(op: str, **params) -> dict:
    result = get_registry().invoke("adapt_ffmpeg", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_ffmpeg", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


def _clip(name: str = "clip", seconds: float = 2.0) -> str:
    put(f"{name}.wav", tone_wav(seconds=seconds))
    return f"{name}.wav"


# -- D1: an output that is silent, blank or short is refused, whatever ffmpeg said ------------------------------------

def test_d1_an_output_ffmpeg_made_and_called_done_but_silent_is_refused_and_removed(ffmpeg: StandIn) -> None:
    source = _clip()
    ffmpeg.behave(write="silent")
    before = _media_files()
    out = _invoke("trim", source=source, start=0.25, end=1.25, name="d1-silent")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == SILENT, out.get("reason")
    assert out["artifact"] is None and check_envelope(out) == [] and _media_files() == before
    assert [c["name"] for c in out["evidence"][0]["checks"] if not c["passed"]] == ["not_silent"]


@REAL
def test_d1_real_a_filter_that_turns_every_picture_black_is_refused() -> None:
    source = _real("d1src.mp4", *TESTSRC)
    out = _invoke("filter", source=source, filter_expr="eq=brightness=-1.0:contrast=0", name="d1-black")
    assert out["code"] == BLANK and "nearly one grey" in out["reason"]
    assert "d1-black.mp4" not in _media_files()


def _account(video: bool = True, audio: bool = True):
    streams = ([{"codec_type": "video", "codec_name": "h264", "width": 160, "height": 120, "avg_frame_rate": "10/1",
                 "duration": "2.0"}] if video else []) + (
        [{"codec_type": "audio", "codec_name": "aac", "sample_rate": "8000", "channels": 1, "duration": "2.0"}]
        if audio else [])
    return lambda path, seconds=60.0: {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.0"},
                                       "streams": streams}


def test_a_silent_black_clip_is_reported_silent_and_blank_and_a_check_not_run_is_not_assessed(
        ffmpeg: StandIn, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.media import _av

    put("dark.mp4", b"read by the test's own ffprobe account, not by ffmpeg")
    monkeypatch.setattr(_av, "probe", _account())
    monkeypatch.setattr(_av, "_decodes", lambda *a: _av.Check(name="decodes", passed=True, detail="decoded"))
    monkeypatch.setattr(_av, "_sound", lambda *a: (_av.Check(name="not_silent", passed=False, code=SILENT,
                                                             detail="silent"), {}))
    monkeypatch.setattr(_av, "_frames", lambda *a: (_av.Check(name="not_blank", passed=False, code=BLANK,
                                                              detail="black"), {}))
    found = _av.inspect_av(media_root() / "dark.mp4")
    assert [(c.name, c.code) for c in found.checks if not c.passed] == [("not_silent", SILENT), ("not_blank", BLANK)]
    out = _invoke("probe", source="dark.mp4")
    assert out["measured"]["silent"] is True and out["measured"]["blank"] is True, out["measured"]
    monkeypatch.setattr(_av, "probe", _account(audio=False))
    monkeypatch.setattr(_av, "_frames", lambda *a: (_av.Check(name="not_blank", passed=True, detail="busy"), {}))
    mute = _invoke("probe", source="dark.mp4")
    assert mute["measured"]["silent"] is None and mute["measured"]["blank"] is False, mute["measured"]


# -- ADAPT_FFMPEG-01, -02: an exit code of 0 is not a check; the length is the one measured -----------------------------

@pytest.mark.parametrize("mode", ["nothing", "empty"])
def test_p0_an_exit_code_of_zero_with_nothing_written_is_refused(ffmpeg: StandIn, mode: str) -> None:
    ffmpeg.behave(write=mode)
    out = _invoke("convert", source=_clip(), format="wav", name=f"p0-{mode}")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == NO_OUTPUT
    assert not [n for n in _media_files() if n.startswith((".partial-", f"p0-{mode}"))]


def test_p1_a_trim_says_the_length_it_measured_and_a_short_one_is_refused(ffmpeg: StandIn) -> None:
    source = _clip()
    ffmpeg.behave(write="short")
    out = _invoke("trim", source=source, start=0.5, end=1.5, name="p1-short")
    assert out["code"] == DURATION_MISMATCH and "0.400 s, not the 1.000 s asked for" in out["reason"]
    ffmpeg.behave(write="good")
    good = _invoke("trim", source=source, start=0.5, end=1.5, name="p1-good")
    assert good["completion_state"] == "qualified-draft" and good["warning_card"]["code"] == "G6_E_MEDIA_PROBED"
    assert good["artifact"]["measured"]["duration_s"] == 1.0 and good["artifact"]["requested"]["duration"] == 1.0
    assert good["artifact"]["kind"] == "audio" and good["artifact"]["mime"] == "audio/wav"


@REAL
def test_p1_real_a_stream_copy_trim_that_lands_on_key_frames_is_refused_and_an_accurate_one_passes() -> None:
    source = _real("gop.mp4", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=4", "-g", "50",
                   "-pix_fmt", "yuv420p")
    copied = _invoke("trim", source=source, start=0.5, end=1.5, accurate=False, name="copy-cut")
    assert copied["code"] == DURATION_MISMATCH, copied.get("reason")
    exact = _invoke("trim", source=source, start=0.5, end=1.5, name="exact-cut")
    assert exact["completion_state"] == "qualified-draft", exact.get("reason")
    assert abs(exact["artifact"]["measured"]["duration_s"] - 1.0) <= 0.1


# -- ADAPT_FFMPEG-03: every input of every op is confined ---------------------------------------------------------------

def _found(video: bool = True, audio: bool = True) -> dict:
    return {"format": "mov,mp4", "duration_s": 4.0, "video_streams": int(video), "audio_streams": int(audio),
            **({"video": {"codec": "h264", "width": 320, "height": 240, "fps": 25.0}} if video else {}),
            **({"audio": {"codec": "aac", "rate": 48000, "channels": 2}} if audio else {})}


PLANS = {
    "convert": ({"format": "mkv"}, lambda d, f: commands.convert(d, f)),
    "trim": ({"end": 2.0}, lambda d, f: commands.trim(d, f)),
    "concat": ({"sources": ["a.mp4", "b.mp4"], "source": ""}, lambda d, f: commands.concat(d, [f, f])),
    "extract_audio": ({"format": "wav"}, lambda d, f: commands.extract_audio(d, f)),
    "add_audio": ({"audio": "music.wav"}, lambda d, f: commands.add_audio(d, f, _found(video=False))),
    "resize": ({"width": 160}, lambda d, f: commands.resize(d, f)),
    "framerate": ({"fps": 12}, lambda d, f: commands.framerate(d, f)),
    "filter": ({"filter_expr": "hflip,eq=contrast=1.2"}, lambda d, f: commands.video_filter(d, f)),
    "thumbnail": ({"format": "png"}, lambda d, f: commands.thumbnail(d, f)),
    "gif": ({"format": "gif"}, lambda d, f: commands.gif(d, f)),
    "overlay": ({"overlay": "logo.png"}, lambda d, f: commands.overlay(d, f, _found(audio=False))),
    "speed": ({"factor": 3.0}, lambda d, f: commands.speed(d, f)),
    "normalize_audio": ({}, lambda d, f: commands.normalize_audio(d, f, dict.fromkeys(
        ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"), "-20"))),
    "subtitles": ({"subtitles": "words.srt"}, lambda d, f: commands.subtitles(d, f)),
}


@pytest.mark.parametrize("op", sorted(PLANS))
def test_p1_every_input_of_every_op_is_opened_only_as_a_local_file_of_a_plain_media_format(op: str) -> None:
    fields, build = PLANS[op]
    data = block.FFmpegInput.model_validate({"op": op, "source": "clip.mp4", **fields})
    data = data.model_copy(update={"format": block._format(data)})
    args = build(data, _found()).args
    starts = [i for i, a in enumerate(args) if a == "-i"]
    assert starts and all(args[i - len(CONFINE):i] == list(CONFINE) for i in starts), args
    assert "-y" not in args and args[-1] == "{out}"
    assert "lavfi" not in CONFINE[3] and "hls" not in CONFINE[3].split(",") and "concat" not in CONFINE[3].split(",")


def test_p1_the_stand_in_saw_the_confinement_on_every_input_it_was_given(ffmpeg: StandIn) -> None:
    _invoke("trim", source=_clip(), start=0.0, end=1.0, name="confined")
    (call,) = ffmpeg.calls()
    at = call.index("-i")
    assert call[at - len(CONFINE):at] == list(CONFINE) and "-n" in call and call[at + 1] == "clip.wav"


@REAL
def test_p1_real_a_playlist_dressed_as_a_video_is_never_followed() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    (media_root() / "dressed.mp4").write_text(f"#EXTM3U\n#EXTINF:2,\nhttp://127.0.0.1:{port}/a.ts\n#EXT-X-ENDLIST\n",
                                              encoding="utf-8")
    out = _invoke("convert", source="dressed.mp4", format="mkv", name="followed")
    with pytest.raises(OSError):
        listener.accept()
    listener.close()
    assert out["completion_state"] == "blocked-escalated" and "followed.mkv" not in _media_files()


# -- what reaches ffmpeg, and what is never replaced ---------------------------------------------------------------------

@pytest.mark.parametrize("expr", ["movie=../secret.mp4", "drawtext=textfile=x.txt", "curves=psfile=x.acv",
                                  "hflip;vflip", "scale=10:10'", "zmq", "hflip,unknown_filter"])
def test_a_filter_that_could_open_a_file_or_is_not_allowed_is_refused_before_ffmpeg_runs(ffmpeg: StandIn,
                                                                                         expr: str) -> None:
    put("v.wav", tone_wav())
    out = _invoke("filter", source="v.wav", filter_expr=expr, format="mp4")
    assert out["code"] == INPUT_INVALID and ffmpeg.calls() == []


@pytest.mark.parametrize("expr", ["curves=plot=x.mp4", "curves=none::::::x.acv"])
def test_curves_which_writes_its_plot_to_a_file_or_reads_a_preset_file_by_position_is_refused(expr: str) -> None:
    # curves=plot=<name> writes a file and its seventh positional option is psfile, which reads one
    with pytest.raises(MediaError) as caught:
        commands.check_filter(expr)
    assert caught.value.code == INPUT_INVALID


@pytest.mark.parametrize("source", ["../x.wav", "C:/x.wav", "/etc/passwd", "a/b.wav", "http:x.wav", "-i"])
def test_a_source_outside_the_media_folder_is_refused(source: str) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("probe", source=source)


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_link_to_a_file_outside_the_media_folder_is_refused_before_ffmpeg_runs(ffmpeg: StandIn, tmp_path: Path,
                                                                                 kind: str) -> None:
    outside_link(tmp_path, "linked.wav", tone_wav(seconds=2.0), kind)
    out = _invoke("trim", source="linked.wav", start=0.0, end=1.0, name="from-a-link")
    assert out["code"] == INPUT_INVALID and "link" in out["reason"] and ffmpeg.calls() == []
    put("plain.wav", tone_wav(seconds=2.0))
    joined = _invoke("concat", sources=["plain.wav", "linked.wav"], format="wav", name="joined-link")
    assert joined["code"] == INPUT_INVALID and ffmpeg.calls() == []


def test_a_different_file_of_that_name_is_never_replaced_unasked(ffmpeg: StandIn) -> None:
    source = _clip()
    put("taken.wav", tone_wav(seconds=0.3, freq=220))
    out = _invoke("trim", source=source, start=0.0, end=1.0, name="taken")
    assert out["code"] == EXISTS and not [n for n in _media_files() if n.startswith(".partial-")]
    assert _invoke("trim", source=source, start=0.0, end=1.0, name="taken", overwrite=True)["written"] == "replaced"


def test_a_subtitle_file_that_is_a_playlist_is_refused(ffmpeg: StandIn) -> None:
    put("v.wav", tone_wav())
    (media_root() / "words.srt").write_text("#EXTM3U\nhttp://127.0.0.1/x\n", encoding="utf-8")
    assert _invoke("subtitles", source="v.wav", subtitles="words.srt")["code"] == INPUT_INVALID
    assert ffmpeg.calls() == []


# -- G1, G3, G4: no label from an exit code, claims refused, errors tighten -------------------------------------------

def test_g1_nothing_is_verified_and_the_package_never_assigns_it(ffmpeg: StandIn) -> None:
    out = _invoke("trim", source=_clip(), start=0.0, end=1.0, name="ff-g1", overwrite=True)
    assert out["completion_state"] == "qualified-draft" and out["artifact"]["completion_state"] == "qualified-draft"
    for path in SRC.rglob("*.py"):
        assert "\"verified\"" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize("field", [{"approved": True}, {"reviewer": "Dr Who"}, {"completion_state": "verified"},
                                   {"output_path": "D:/clips/x.mp4"}, {"input_base64": "AAAA"}, {"ffmpeg": "x"},
                                   {"protocol_whitelist": "http"}])
def test_g3_a_callers_claims_paths_and_switches_are_refused_not_honoured(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("trim", source="clip.wav", end=1.0, **field)


def test_g4_a_missing_ffmpeg_is_the_honest_refusal_with_install_steps(monkeypatch: pytest.MonkeyPatch,
                                                                      tmp_path: Path) -> None:
    monkeypatch.setenv("G6_FFMPEG", str(tmp_path / "absent-ffmpeg.exe"))
    out = _invoke("probe", source=_clip())
    assert out["code"] == OUTSIDE_MISSING and "G6 never installs it" in out["reason"] and "ffmpeg.org" in out["reason"]


def test_g4_a_failing_or_slow_ffmpeg_is_a_refusal_with_its_reason(ffmpeg: StandIn) -> None:
    source = _clip()
    ffmpeg.behave(fail=True)
    out = _invoke("convert", source=source, format="wav", name="g4-fail")
    assert out["code"] == BACKEND_FAILED and "Invalid data" in out["reason"]
    ffmpeg.behave(sleep=30)
    slow = _invoke("convert", source=source, format="wav", name="g4-slow", timeout=2)
    assert slow["code"] == TIME_LIMIT and not [n for n in _media_files() if n.startswith((".partial-", "g4-"))]


# -- G5, G6, G7, G8 --------------------------------------------------------------------------------------------------------

def test_g5_every_op_runs_a_program_so_every_op_is_sensitive_and_refused_over_mcp_unless_allowed(
        ffmpeg: StandIn) -> None:
    assert set(package.__g6_sensitive_ops__) == set(_invoke("ops")["ops"]) and len(package.__g6_sensitive_ops__) == 15
    source = _clip()
    for op in package.__g6_sensitive_ops__:
        denied = invoke_reply("adapt_ffmpeg", op, json.dumps({"source": source}), allow_sensitive=frozenset())
        assert not denied["ok"] and denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", op
    assert ffmpeg.calls() == []
    allowed = invoke_reply("adapt_ffmpeg", "trim", json.dumps({"source": source, "end": 1.0}),
                           allow_sensitive=frozenset({"adapt_ffmpeg.trim"}))
    assert allowed["ok"] and allowed["result"]["completion_state"] == "qualified-draft"
    assert check_envelope(allowed["result"]) == []


def test_g6_g7_g8_no_key_no_model_no_stand_in_and_nothing_to_install_but_ffmpeg() -> None:
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "litellm", "openrouter", "anthropic", "openai", "mock", "fake", "simulat",
                     "experimental", "all rights reserved", "shell=true", "os.system", "httpx", "socket"):
            assert word not in text, (path.name, word)
        if path.suffix == ".py":
            assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
    assert package.__g6_extras__ == {} and package.__g6_outside__ == (FFMPEG, FFPROBE)
    assert FFMPEG.env == "G6_FFMPEG" and "command line" in FFMPEG.licence and not FFMPEG.paid and not FFMPEG.online


# -- D2, D4, D6 ----------------------------------------------------------------------------------------------------------

def test_d2_it_declares_the_failure_modes_it_catches_and_left_planned() -> None:
    assert package.__g6_mitigates__ == ("visual_check_failed", "audio_check_failed") and package.__g6_introduces__ == ()
    assert "g6.media.adapt_ffmpeg" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_imports_no_other_member() -> None:
    assert get_registry().get_class("adapt_ffmpeg") is block.AdaptFFmpegBlock
    assert block.FFmpegInput.model_config["extra"] == "forbid"
    assert len(_invoke("help")["help"]) == len(_invoke("ops")["ops"]) == 15
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("g6.media."):
                assert node.module.split(".")[2].startswith("_") or node.module.split(".")[2] == "adapt_ffmpeg"


def test_d6_an_agent_finds_it_describes_it_and_invokes_it_over_mcp(ffmpeg: StandIn) -> None:
    assert "adapt_ffmpeg" in [r["component"] for r in discovery.find("convert or trim a video with ffmpeg")]
    info = describe_reply("adapt_ffmpeg")
    assert info["family"] == "media" and info["skill"] and {p["name"] for p in info["outside"]} == {"ffmpeg", "ffprobe"}
    reply = invoke_reply("adapt_ffmpeg", "extract_audio", json.dumps({"source": _clip(), "request_id": "q1",
                                                                      "run_id": "u1"}),
                         allow_sensitive=frozenset({"adapt_ffmpeg"}))
    assert reply["ok"] and reply["result"]["request_id"] == "q1" and check_envelope(reply["result"]) == []


# -- with the real ffmpeg: each op's result measured -----------------------------------------------------------------------

TESTSRC = ("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=2", "-shortest", "-pix_fmt", "yuv420p")


def _real(name: str, *args: str) -> str:
    path = media_root() / name
    media_root().mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    subprocess.run([REAL_FFMPEG, "-nostdin", "-hide_banner", "-v", "error", *args, str(path)], check=True, timeout=120,
                   env={**os.environ})
    return name


@REAL
def test_real_each_op_writes_what_it_was_asked_for_and_says_what_it_measured() -> None:
    video = _real("real.mp4", *TESTSRC)
    _real("logo.png", "-f", "lavfi", "-i", "color=c=red:size=40x30", "-frames:v", "1")
    (media_root() / "words.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\nHello\n", encoding="utf-8")
    probed = _invoke("probe", source=video)
    assert probed["warning_card"]["code"] == "G6_E_MEDIA_MEASURED" and probed["measured"]["video"]["width"] == 320
    checks = {
        "convert": ({"format": "mkv"}, {"width": 320, "height": 240}),
        "resize": ({"width": 160}, {"width": 160, "height": 120}),
        "framerate": ({"fps": 10}, {"fps": 10.0}),
        "thumbnail": ({"time": 1.0}, {"width": 320, "height": 240}),
        "gif": ({"fps": 10, "width": 120}, {"width": 120, "height": 90}),
        "extract_audio": ({}, {"channels": 1}),
        "speed": ({"factor": 2.0}, {"duration_s": 1.0}),
        "overlay": ({"overlay": "logo.png", "x": 10, "y": 10}, {"width": 320}),
        "subtitles": ({"subtitles": "words.srt"}, {"width": 320}),
        "add_audio": ({"audio": "real.mp4"}, {"width": 320}),
        "concat": ({"sources": [video, video], "source": ""}, {"duration_s": 4.0}),
    }
    for op, (params, want) in checks.items():
        out = _invoke(op, **{"source": video, **params, "name": f"real-{op}"})
        assert out["completion_state"] == "qualified-draft", (op, out.get("reason"))
        found = out["artifact"]["measured"]
        flat = {**found, **(found.get("video") or {}), **(found.get("audio") or {})}
        for key, value in want.items():
            assert abs(flat[key] - value) <= (0.15 if key == "duration_s" else 0.01 * value), (op, key, flat[key])


@REAL
def test_real_normalize_audio_measures_the_result_again_and_refuses_a_silent_source(
        monkeypatch: pytest.MonkeyPatch) -> None:
    quiet = _real("quiet.wav", "-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-af", "volume=-30dB")
    out = _invoke("normalize_audio", source=quiet, target_lufs=-20, true_peak=-2, name="ff-levelled", overwrite=True)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    loud = out["artifact"]["measured"]["loudness"]
    assert abs(loud["integrated_lufs"] + 20) <= 1.0 and out["artifact"]["measured"]["rate"] == 44100
    hush = _real("hush.wav", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1")
    assert _invoke("normalize_audio", source=hush)["code"] == SILENT
    monkeypatch.setattr(block, "_loudness_check", lambda *a: (block.Check(
        name="loudness_matches_request", passed=False, code=LOUDNESS_MISSED, detail="-9.0 LUFS, not -20"), {}))
    missed = _invoke("normalize_audio", source=quiet, target_lufs=-20, name="missed")
    assert missed["code"] == LOUDNESS_MISSED and "missed.wav" not in _media_files()
