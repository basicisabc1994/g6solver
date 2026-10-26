# SPDX-License-Identifier: Apache-2.0
"""The family's shared code for sound and moving pictures: the WAV inspector (standard library) refuses an empty, silent,
truncated, wrong-rate or wrong-length file; the program runner gives a clean environment, a deadline and an output cap;
a missing ffmpeg is the honest refusal with install steps; and, with the real ffmpeg when it is installed, the sound and
video inspector refuses a black picture, a silent sound track, a truncated file and a playlist dressed as a video."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from media_kit import float_wav, put, stand_in, tone_wav

from g6.media import _av, _programs, _wav
from g6.media._types import (
    BACKEND_FAILED,
    BLANK,
    DURATION_MISMATCH,
    EXISTS,
    FORMAT_MISMATCH,
    NO_OUTPUT,
    OUTSIDE_MISSING,
    SILENT,
    SIZE_MISMATCH,
    STREAM_MISMATCH,
    TIME_LIMIT,
    UNDECODABLE,
    MediaError,
    answer,
    media_file,
    media_root,
    place,
)

FFMPEG = shutil.which("ffmpeg")
REAL = pytest.mark.skipif(not (FFMPEG and shutil.which("ffprobe")), reason="ffmpeg and ffprobe are not installed here")


def failed(inspection) -> list[tuple[str, str]]:
    return [(c.name, c.code) for c in inspection.checks if not c.passed]


# -- the WAV inspector ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_a_tone_passes_at_every_pcm_width_and_its_level_is_measured(width: int) -> None:
    found = _wav.inspect_wav(put(f"tone{width}.wav", tone_wav(width=width)), rate=8000, channels=1, duration=0.5)
    assert found.passed, failed(found)
    assert found.measured["bits"] == 8 * width and found.measured["frames"] == 4000
    assert abs(found.measured["peak_dbfs"] - 20 * math.log10(0.5)) < 0.2 and found.measured["rms_dbfs"] < -8.5


def test_float_samples_are_read_and_one_that_is_not_a_number_is_refused() -> None:
    good = [0.25 * math.sin(i / 3) for i in range(800)]
    found = _wav.inspect_wav(put("float.wav", float_wav(good)))
    assert found.passed and found.measured["encoding"] == "f32le", failed(found)
    assert failed(_wav.inspect_wav(put("nan.wav", float_wav([*good, float("nan")])))) == [("decodes", UNDECODABLE)]


def test_an_all_zero_file_and_one_of_a_few_clicks_are_silent() -> None:
    assert failed(_wav.inspect_wav(put("zero.wav", tone_wav(silent=True)))) == [("not_silent", SILENT)]
    clicks = bytearray(tone_wav(seconds=2.0, silent=True))
    for at in (1000, 9000, 20000):  # three loud samples in 16000
        clicks[44 + 2 * at: 46 + 2 * at] = (30000).to_bytes(2, "little", signed=True)
    found = _wav.inspect_wav(put("clicks.wav", bytes(clicks)))
    assert failed(found) == [("not_silent", SILENT)] and found.measured["peak_dbfs"] > -1.0


@pytest.mark.parametrize(("data", "code"), [
    (b"", NO_OUTPUT),
    (b"ID3\x04" + bytes(200), FORMAT_MISMATCH),
    (tone_wav()[:-500], UNDECODABLE),  # the data chunk says more than follows
])
def test_an_empty_foreign_or_truncated_file_is_refused(data: bytes, code: str) -> None:
    path = media_root() / "broken.wav"
    media_root().mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    assert failed(_wav.inspect_wav(path))[0][1] == code


def test_the_rate_channels_and_length_asked_for_are_compared() -> None:
    path = put("stereo.wav", tone_wav(seconds=0.5, channels=2))
    assert failed(_wav.inspect_wav(path, rate=16000)) == [("rate_matches_request", STREAM_MISMATCH)]
    assert failed(_wav.inspect_wav(path, channels=1)) == [("channels_match_request", STREAM_MISMATCH)]
    assert failed(_wav.inspect_wav(path, duration=1.0)) == [("duration_matches_request", DURATION_MISMATCH)]
    assert _wav.inspect_wav(path, duration=0.52, tolerance=0.05).passed
    assert failed(_wav.inspect_wav(path, min_duration=0.6)) == [("duration_matches_request", DURATION_MISMATCH)]


def test_pcm_is_wrapped_and_a_partial_frame_dropped() -> None:
    pcm = tone_wav(seconds=0.25)[44:] + b"\x01"
    found = _wav.read_info(put("wrapped.wav", _wav.pcm_wav(pcm, rate=8000)))
    assert (found.rate, found.channels, found.frames) == (8000, 1, 2000)


def test_a_probed_result_carries_its_own_card() -> None:
    out = answer("x", "measured", card="G6_E_MEDIA_PROBED")
    assert out.completion_state == "qualified-draft" and "Listen to it or watch it" in out.warning_card["suggested_action"]


def test_a_programs_output_is_placed_by_the_overwrite_rule() -> None:
    final = media_file("placed", ".wav")
    final.unlink(missing_ok=True)

    def temp(data: bytes) -> Path:
        path = media_root() / f".partial-{len(data)}-{os.getpid()}.wav"
        path.write_bytes(data)
        return path

    assert place(temp(b"one"), final) == "written"
    assert place(temp(b"one"), final) == "unchanged"
    with pytest.raises(MediaError) as caught:
        place(temp(b"two!"), final)
    assert caught.value.code == EXISTS and final.read_bytes() == b"one"
    assert place(temp(b"two!"), final, overwrite=True) == "replaced" and final.read_bytes() == b"two!"
    assert not [p for p in media_root().iterdir() if p.name.startswith(".partial-")]


# -- the program runner, through a stand-in program --------------------------------------------------------------------

STAND_IN = r'''
import json, os, sys, time
args = sys.argv[1:]
if args == ["-version"]:
    print("stand-in 1.0"); sys.exit(0)
if args[:1] == ["sleep"]:
    time.sleep(float(args[1])); sys.exit(0)
if args[:1] == ["flood"]:
    sys.stdout.write("x" * int(args[1])); sys.exit(0)
if args[:1] == ["env"]:
    print(json.dumps(dict(os.environ))); sys.exit(0)
if args[:1] == ["echo"]:
    print(json.dumps(args[1:])); sys.exit(0)
sys.exit(3)
'''


@pytest.fixture
def program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    launcher = stand_in(tmp_path, "ffmpeg", STAND_IN)
    monkeypatch.setenv("G6_FFMPEG", str(launcher))
    return _programs.find(_av.FFMPEG).where


def test_a_program_gets_a_clean_environment_and_its_own_home(program: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "not-for-the-program")
    ran = _programs.run(program, ["env"], seconds=60)
    seen = json.loads(ran.out.decode())
    assert ran.code == 0 and "SOME_SERVICE_TOKEN" not in seen and "G6_FFMPEG" not in seen
    assert seen["HOME"] == seen["TEMP"] and Path(seen["HOME"]).name.startswith("g6-media-run-")


def test_arguments_reach_the_program_as_a_list(program: str) -> None:
    ran = _programs.run(program, ["echo", "a b", "c.wav"], seconds=60)
    assert json.loads(ran.out.decode()) == ["a b", "c.wav"]


def test_a_program_past_its_deadline_is_stopped(program: str) -> None:
    with pytest.raises(MediaError) as caught:
        _programs.run(program, ["sleep", "30"], seconds=1.5)
    assert caught.value.code == TIME_LIMIT and "did not finish within 1.5 s" in caught.value.reason


def test_a_program_past_its_output_cap_is_stopped(program: str) -> None:
    with pytest.raises(MediaError) as caught:
        _programs.run(program, ["flood", "200000"], seconds=60, output_cap=1000)
    assert caught.value.code == BACKEND_FAILED and "more than 1000 bytes" in caught.value.reason


def test_a_missing_program_is_the_honest_refusal_with_install_steps(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_FFMPEG", str(tmp_path / "no-such-ffmpeg.exe"))
    with pytest.raises(MediaError) as caught:
        _programs.find(_av.FFMPEG)
    assert caught.value.code == OUTSIDE_MISSING and "G6 never installs it" in caught.value.reason
    assert "ffmpeg.org" in caught.value.reason


# -- the sound and video inspector, with the real ffmpeg ---------------------------------------------------------------

def make(name: str, *args: str) -> Path:
    path = media_root() / name
    media_root().mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    subprocess.run([FFMPEG, "-nostdin", "-hide_banner", "-v", "error", *args, str(path)], check=True, timeout=120,
                   env={**os.environ})
    return path


TESTSRC = ("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=2", "-shortest", "-pix_fmt", "yuv420p")


@REAL
def test_real_a_good_video_passes_with_its_size_rate_length_sound_and_pictures() -> None:
    path = make("good.mp4", *TESTSRC)
    found = _av.inspect_av(path, formats=("mp4",), video=True, audio=True, width=320, height=240, fps=25,
                           duration=2.0)
    assert found.passed, failed(found)
    assert found.mime == "video/mp4" and found.measured["video"]["content_fraction"] > 0.1
    assert found.measured["audio"]["sound_fraction"] > 0.5
    assert failed(_av.inspect_av(path, width=640, height=480)) == [("size_matches_request", SIZE_MISMATCH)]
    assert failed(_av.inspect_av(path, duration=1.0))[-1] == ("duration_matches_request", DURATION_MISMATCH)


@REAL
def test_real_a_black_video_is_blank_and_a_silent_track_is_silent() -> None:
    black = make("black.mp4", "-f", "lavfi", "-i", "color=c=black:size=160x120:rate=10:duration=1", "-pix_fmt",
                 "yuv420p")
    assert failed(_av.inspect_av(black)) == [("not_blank", BLANK)]
    hush = make("hush.m4a", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1")
    assert failed(_av.inspect_av(hush, audio=True, video=False)) == [("not_silent", SILENT)]


@REAL
def test_real_a_truncated_file_and_a_playlist_named_as_a_video_are_refused() -> None:
    good = make("whole.mkv", *TESTSRC)
    cut = media_root() / "cut.mkv"
    cut.write_bytes(good.read_bytes()[: good.stat().st_size // 3])
    assert failed(_av.inspect_av(cut))[0] in (("decodes", UNDECODABLE), ("duration_matches_request", NO_OUTPUT))
    playlist = media_root() / "list.mp4"
    playlist.write_text("#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/secret.ts\n", encoding="utf-8")
    assert failed(_av.inspect_av(playlist)) == [("decodes", UNDECODABLE)]
